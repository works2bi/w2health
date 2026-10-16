"""Data Quality do pipeline — executada sobre o CANÔNICO, ANTES de qualquer promoção.

Reaproveita as regras independentes de fornecedor de `data_platform/quality/rules.py`
(valores negativos, glosa > apresentado, competência inválida, saída antes da adesão,
duplicidades, coparticipação acima do pago) e acrescenta as regras do contrato
operacional: chave estrangeira por código de negócio, UF válida, arquivo obrigatório
ausente, rejeições do mapping, coberturas (INFO).

Gate (docs/DATA_QUALITY.md §Gate):
* linha com qualquer ERROR é rejeitada (nunca "corrigida");
* se a fração rejeitada de uma entidade ultrapassar `quality.max_rejected_ratio` do
  mapping (padrão 0 — sem tolerância silenciosa), a carga inteira é BLOQUEADA;
* WARNING e INFO não bloqueiam, mas ficam registrados em `data_quality_results`.
"""

from __future__ import annotations

import importlib.util
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.data_platform.canonical import MACRORREGIAO_UF
from app.data_platform.mapping import EntityResult, Mapping
from app.data_platform.paths import data_platform_dir

ERROR, WARNING, INFO = "ERROR", "WARNING", "INFO"
_SAMPLE = 5

#: campos RECOMENDADOS cuja cobertura é reportada (INFO) — docs/DATA_CONTRACT.md
COBERTURA = {
    "evento_assistencial": ("valor_glosado", "valor_coparticipacao", "especialidade_codigo"),
    "beneficiario": ("data_saida", "sexo"),
    "procedimento": ("grupo_procedimento", "perfil_utilizacao"),
    "prestador": ("tipo_prestador",),
}

#: FKs por código de negócio: campo → entidade referenciada
FKS = {
    "plano_contrato": {"contrato_codigo": "contrato", "plano_codigo": "plano"},
    "prestador": {"especialidade_codigo": "especialidade"},
    "procedimento": {"especialidade_codigo": "especialidade"},
    "beneficiario": {"plano_codigo": "plano", "contrato_codigo": "contrato"},
    "receita": {"plano_codigo": "plano"},
    "evento_assistencial": {"beneficiario_codigo": "beneficiario",
                            "prestador_codigo": "prestador",
                            "procedimento_codigo": "procedimento",
                            "especialidade_codigo": "especialidade"},
}


@lru_cache
def _rules_module():
    """Carrega `data_platform/quality/rules.py` (artefato de contrato, fora do pacote app)."""
    path = Path(data_platform_dir()) / "quality" / "rules.py"
    spec = importlib.util.spec_from_file_location("w2h_quality_rules", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["w2h_quality_rules"] = mod  # @dataclass exige o módulo registrado
    spec.loader.exec_module(mod)
    return mod


@dataclass
class DQFinding:
    rule_id: str
    severity: str
    entity: str
    description: str
    failed: int = 0
    checked: int = 0
    blocking: bool = False
    sample: list[dict] = field(default_factory=list)


@dataclass
class EntityQuality:
    entity: str
    received: int
    valid_rows: list[dict]
    rejected_lines: set[int]
    duplicates_removed: int
    blocked: bool


@dataclass
class QualityReport:
    findings: list[DQFinding] = field(default_factory=list)
    entities: dict[str, EntityQuality] = field(default_factory=dict)

    @property
    def blocked(self) -> bool:
        return any(f.blocking for f in self.findings)

    @property
    def errors(self) -> int:
        return sum(f.failed for f in self.findings if f.severity == ERROR)

    @property
    def warnings(self) -> int:
        return sum(f.failed for f in self.findings if f.severity == WARNING)


def _ref(row: dict) -> dict:
    """Amostra segura: só linha e identificador de origem — nunca o payload."""
    return {k: row[k] for k in ("_line", "source_record_id", "codigo") if row.get(k) is not None}


def _add(report: QualityReport, rule_id, sev, entity, desc, failed_rows, checked, blocking=False):
    if not failed_rows and sev != INFO:
        return
    report.findings.append(DQFinding(
        rule_id, sev, entity, desc, failed=len(failed_rows) if isinstance(failed_rows, list) else int(failed_rows),
        checked=checked, blocking=blocking,
        sample=[_ref(r) for r in failed_rows[:_SAMPLE]] if isinstance(failed_rows, list) else []))


def _dedup(rows: list[dict], key_fields: tuple[str, ...]) -> tuple[list[dict], list[dict]]:
    """Mantém a ÚLTIMA ocorrência de cada chave de negócio (regra documentada)."""
    ultimo: dict[tuple, dict] = {}
    dups: list[dict] = []
    for r in rows:
        k = tuple(r.get(f) for f in key_fields)
        if k in ultimo:
            dups.append(ultimo[k])
        ultimo[k] = r
    return list(ultimo.values()), dups


def evaluate(mapping: Mapping, results: dict[str, EntityResult], files_present: set[str],
             existing_codes: dict[str, set], business_keys: dict[str, tuple[str, ...]],
             load_order: tuple[str, ...]) -> QualityReport:
    """Avalia todas as entidades (na ordem de carga) e decide o gate. Não escreve nada."""
    rules = _rules_module()
    report = QualityReport()
    validos: dict[str, set] = {e: set(c) for e, c in existing_codes.items()}

    # arquivos obrigatórios / desconhecidos
    for em in mapping.entities:
        if em.source_entity not in files_present:
            if em.required:
                _add(report, "arquivo_obrigatorio_ausente", ERROR, em.target_entity,
                     f"arquivo '{em.source_entity}.csv' obrigatório não recebido", 1, 1, blocking=True)
            else:
                _add(report, "arquivo_opcional_ausente", INFO, em.target_entity,
                     f"arquivo '{em.source_entity}.csv' (opcional) não recebido — capacidades que "
                     "dependem dele ficam indisponíveis", 0, 0)

    for entity in load_order:
        res = results.get(entity)
        if res is None:
            continue
        rows = [{**mr.values, "_line": mr.line} for mr in res.rows]
        rejeitadas: set[int] = set()

        # 1. rejeições do mapping (cast, obrigatório, vocabulário, colunas ausentes)
        if res.rejects:
            colunas = [r for r in res.rejects if r.line == 0]
            if colunas:
                _add(report, "colunas_ausentes", ERROR, entity, colunas[0].reasons[0], 1, 1, blocking=True)
            linhas = [r for r in res.rejects if r.line > 0]
            if linhas:
                motivos = Counter(m.split(":")[0] for r in linhas for m in r.reasons)
                report.findings.append(DQFinding(
                    "mapping_invalido", ERROR, entity,
                    "linhas rejeitadas pelo mapping (tipo, obrigatório ou vocabulário): "
                    + ", ".join(f"{k}={v}" for k, v in motivos.most_common(5)),
                    failed=len(linhas), checked=res.received,
                    sample=[{"_line": r.line, "source_record_id": r.source_record_id,
                             "motivos": r.reasons[:3]} for r in linhas[:_SAMPLE]]))
                rejeitadas |= {r.line for r in linhas}

        # 2. duplicidade da chave de negócio no lote (WARNING; mantém a última)
        rows, dups = _dedup(rows, business_keys[entity])
        if dups:
            rid = "evento_duplicado" if entity == "evento_assistencial" else (
                "receita_duplicada" if entity == "receita" else "chave_duplicada")
            _add(report, rid, WARNING, entity,
                 "chave de negócio repetida no arquivo — mantida a última ocorrência", dups, res.received)

        # 3. FK por código de negócio (sempre — inclusive contra a Silver existente)
        fk_falhas: dict[str, list[dict]] = defaultdict(list)
        for r in rows:
            for campo, alvo in FKS.get(entity, {}).items():
                v = r.get(campo)
                if v is not None and v not in validos.get(alvo, set()):
                    fk_falhas[campo].append(r)
        for campo, falhas in fk_falhas.items():
            _add(report, f"fk_inexistente_{campo}", ERROR, entity,
                 f"{campo} não existe (nem no arquivo válido, nem na carga anterior do tenant)",
                 falhas, len(rows))
            rejeitadas |= {r["_line"] for r in falhas}

        # 3b. o plano do beneficiário tem de ser um plano do contrato dele (N:N)
        if entity == "beneficiario":
            pares = validos.get("plano_contrato", set())
            fora = [r for r in rows if r["_line"] not in rejeitadas
                    and (r.get("contrato_codigo"), r.get("plano_codigo")) not in pares]
            _add(report, "plano_fora_do_contrato", ERROR, entity,
                 "plano_codigo não é um plano do contrato_codigo (plano_contrato)", fora, len(rows))
            rejeitadas |= {r["_line"] for r in fora}

        # 4. UF válida (região derivada de cidade/UF)
        if entity in ("beneficiario", "prestador"):
            ruins = [r for r in rows if str(r.get("uf", "")).upper() not in MACRORREGIAO_UF]
            _add(report, "uf_invalida", ERROR, entity, "UF não reconhecida", ruins, len(rows))
            rejeitadas |= {r["_line"] for r in ruins}

        # 5. regras independentes de fornecedor (data_platform/quality/rules.py)
        canon_like = [_para_regras(entity, r) for r in rows]
        por_regra: dict[tuple, list[dict]] = defaultdict(list)
        for v in rules.avaliar(entity, canon_like, rules.QualityContext()):
            if v.rule_id in ("evento_duplicado", "receita_duplicada"):
                continue  # já tratado acima com dedup determinístico
            por_regra[(v.rule_id, v.severity, v.message.split(" ")[0])].append(v.sample)
        for (rid, sev, _), amostras in por_regra.items():
            _add(report, rid, sev, entity, _DESCR.get(rid, rid), amostras, len(rows))
            if sev == ERROR:
                rejeitadas |= {a["_line"] for a in amostras}

        # 6. coberturas (INFO)
        for campo in COBERTURA.get(entity, ()):
            preenchidos = sum(1 for r in rows if r.get(campo) not in (None, ""))
            if rows:
                report.findings.append(DQFinding(
                    f"cobertura_{campo}", INFO, entity,
                    f"{campo} preenchido em {preenchidos}/{len(rows)} linhas "
                    f"({preenchidos / len(rows) * 100:.1f}%)", failed=len(rows) - preenchidos,
                    checked=len(rows)))
        report.findings.append(DQFinding("volume", INFO, entity,
                                         f"{res.received} linhas recebidas", checked=res.received))

        validas = [r for r in rows if r["_line"] not in rejeitadas]
        ratio = (len(rejeitadas) / res.received) if res.received else 0.0
        bloqueada = ratio > mapping.max_rejected_ratio
        if rejeitadas and bloqueada:
            report.findings.append(DQFinding(
                "gate_rejeicao", ERROR, entity,
                f"{len(rejeitadas)} de {res.received} linhas rejeitadas ({ratio * 100:.2f}%) — acima "
                f"do limite configurado ({mapping.max_rejected_ratio * 100:.2f}%); carga bloqueada",
                failed=len(rejeitadas), checked=res.received, blocking=True))
        report.entities[entity] = EntityQuality(entity, res.received, validas, rejeitadas,
                                                len(dups), bloqueada and bool(rejeitadas))
        # só códigos VÁLIDOS ficam disponíveis como referência para as próximas entidades
        chave = business_keys[entity]
        if chave == ("codigo",):
            validos.setdefault(entity, set()).update(r["codigo"] for r in validas)
        elif entity == "plano_contrato":
            validos.setdefault(entity, set()).update(
                (r["contrato_codigo"], r["plano_codigo"]) for r in validas)
    return report


_DESCR = {
    "valor_negativo": "valor monetário negativo",
    "glosa_maior_que_apresentado": "glosa maior que o valor apresentado",
    "competencia_invalida": "competência inválida",
    "evento_sem_data": "evento sem data",
    "data_saida_antes_adesao": "data de saída anterior à adesão",
    "coparticipacao_acima_permitido": "coparticipação maior que o valor pago",
    "despesa_liquida_inconsistente": "despesa líquida inconsistente",
    "tenant_id_ausente": "linha sem tenant",
}


def _para_regras(entity: str, r: dict) -> dict[str, Any]:
    """Adapta a linha canônica (códigos) ao formato esperado por rules.py. O tenant é
    sempre o do PipelineContext — injetado aqui para a regra `tenant_id_ausente`."""
    out = dict(r)
    out.setdefault("tenant_id", "ctx")
    if entity == "evento_assistencial":
        out["source_system"] = "lote"
        out["id_beneficiario"] = r.get("beneficiario_codigo")
        out["id_prestador"] = r.get("prestador_codigo")
    if entity == "receita":
        out["id_plano"] = r.get("plano_codigo")
    if entity == "beneficiario":
        out["id_contrato"] = r.get("contrato_codigo")
    return out
