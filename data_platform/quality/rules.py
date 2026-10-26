"""Regras mínimas de Data Quality do W2Health (v1.2).

**Independentes de fornecedor.** Operam sobre linhas do MODELO CANÔNICO (dicts), não
sobre o esquema de nenhuma origem. Cada regra tem uma severidade:

  * ERROR   — bloqueia o processamento da entidade (a carga não avança para Silver/Gold);
  * WARNING — permite continuar, mas registra o problema (`data_quality_results`);
  * INFO    — apenas informativo.

Uso (conceitual, num pipeline futuro):

    ctx = QualityContext(beneficiarios_ids={...}, contratos_ids={...}, prestadores_ids={...})
    violacoes = avaliar("evento_assistencial", linhas_canonicas, ctx)
    if any(v.severity == "ERROR" for v in violacoes):
        abortar_entidade()

Este módulo NÃO é executado por nenhum pipeline na v1.2 — existe como contrato e é
coberto por `backend/tests/test_data_quality.py`.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date

ERROR, WARNING, INFO = "ERROR", "WARNING", "INFO"


@dataclass
class QualityContext:
    beneficiarios_ids: set = field(default_factory=set)
    contratos_ids: set = field(default_factory=set)
    prestadores_ids: set = field(default_factory=set)
    planos_ids: set = field(default_factory=set)


@dataclass
class Violation:
    rule_id: str
    severity: str
    entity: str
    message: str
    sample: dict


def _is_first_of_month(v) -> bool:
    if isinstance(v, date):
        return v.day == 1
    try:
        y, m, d = str(v).split("T")[0].split("-")
        return int(d) == 1 and 1 <= int(m) <= 12
    except Exception:
        return False


def _num(row: dict, key: str, default=0.0) -> float:
    try:
        return float(row.get(key, default) or 0.0)
    except (TypeError, ValueError):
        return float("nan")


# --------------------------------------------------------------------------- por entidade
def _check_evento(rows: Iterable[dict], ctx: QualityContext) -> list[Violation]:
    out: list[Violation] = []
    vistos: set = set()
    for r in rows:
        if not r.get("tenant_id"):
            out.append(Violation("tenant_id_ausente", ERROR, "evento_assistencial",
                                 "linha sem tenant_id", r))
        if ctx.beneficiarios_ids and r.get("id_beneficiario") not in ctx.beneficiarios_ids:
            out.append(Violation("benef_inexistente_em_evento", ERROR, "evento_assistencial",
                                 f"id_beneficiario {r.get('id_beneficiario')} não existe", r))
        if ctx.contratos_ids and r.get("id_contrato") is not None \
                and r.get("id_contrato") not in ctx.contratos_ids:
            out.append(Violation("contrato_inexistente", ERROR, "evento_assistencial",
                                 f"id_contrato {r.get('id_contrato')} não existe", r))
        if ctx.prestadores_ids and r.get("id_prestador") not in ctx.prestadores_ids:
            out.append(Violation("prestador_inexistente", ERROR, "evento_assistencial",
                                 f"id_prestador {r.get('id_prestador')} não existe", r))
        if not r.get("data_evento"):
            out.append(Violation("evento_sem_data", ERROR, "evento_assistencial",
                                 "data_evento ausente", r))
        if r.get("competencia") is not None and not _is_first_of_month(r["competencia"]):
            out.append(Violation("competencia_invalida", ERROR, "evento_assistencial",
                                 f"competência {r['competencia']} não é o 1º dia do mês", r))
        apres, glosa = _num(r, "valor_apresentado"), _num(r, "valor_glosado")
        pago, copart = _num(r, "valor_pago"), _num(r, "valor_coparticipacao")
        # estorno/ajuste/cancelamento (Consolidação V1) podem ter valor negativo
        if r.get("natureza_lancamento") in NATUREZAS_COM_NEGATIVO:
            pass
        elif apres < 0 or pago < 0:
            out.append(Violation("valor_negativo", ERROR, "evento_assistencial",
                                 "valor apresentado/pago negativo", r))
        if glosa > apres + 0.01 and apres >= 0:
            out.append(Violation("glosa_maior_que_apresentado", ERROR, "evento_assistencial",
                                 f"glosa {glosa} > apresentado {apres}", r))
        if "despesa_liquida" in r:
            liq = _num(r, "despesa_liquida")
            if abs(liq - (apres - glosa - copart)) > 0.01:
                out.append(Violation("despesa_liquida_inconsistente", WARNING, "evento_assistencial",
                                     "despesa_liquida != bruta - glosa - coparticipação", r))
        if copart > pago + 0.01 and pago >= 0:
            out.append(Violation("coparticipacao_acima_permitido", WARNING, "evento_assistencial",
                                 f"coparticipação {copart} > valor_pago {pago}", r))
        bk = (r.get("source_system"), r.get("source_record_id"))
        if bk != (None, None):
            if bk in vistos:
                out.append(Violation("evento_duplicado", WARNING, "evento_assistencial",
                                     f"chave de origem repetida {bk}", r))
            vistos.add(bk)
    return out


def _check_beneficiario(rows: Iterable[dict], ctx: QualityContext) -> list[Violation]:
    out: list[Violation] = []
    for r in rows:
        if not r.get("tenant_id"):
            out.append(Violation("tenant_id_ausente", ERROR, "beneficiario", "sem tenant_id", r))
        ad, sa = r.get("data_adesao"), r.get("data_saida")
        if ad and sa and str(sa) < str(ad):
            out.append(Violation("data_saida_antes_adesao", ERROR, "beneficiario",
                                 f"data_saida {sa} < data_adesao {ad}", r))
        if ctx.contratos_ids and r.get("id_contrato") not in ctx.contratos_ids:
            out.append(Violation("contrato_inexistente", ERROR, "beneficiario",
                                 f"id_contrato {r.get('id_contrato')} não existe", r))
    return out


def _check_receita(rows: Iterable[dict], ctx: QualityContext) -> list[Violation]:
    out: list[Violation] = []
    vistos: set = set()
    for r in rows:
        if not r.get("tenant_id"):
            out.append(Violation("tenant_id_ausente", ERROR, "receita", "sem tenant_id", r))
        if not _is_first_of_month(r.get("competencia")):
            out.append(Violation("competencia_invalida", ERROR, "receita",
                                 f"competência {r.get('competencia')} inválida", r))
        if _num(r, "receita_contraprestacao") < 0:
            out.append(Violation("valor_negativo", ERROR, "receita", "receita negativa", r))
        bk = (r.get("competencia"), r.get("id_plano"))
        if bk in vistos:
            out.append(Violation("receita_duplicada", WARNING, "receita",
                                 f"(competência, plano) repetido {bk}", r))
        vistos.add(bk)
    return out


NATUREZAS_COM_NEGATIVO = ("estorno", "ajuste", "cancelamento")


def _check_fatura(rows: Iterable[dict], ctx: QualityContext) -> list[Violation]:
    out: list[Violation] = []
    for r in rows:
        u = r.get("unidade_cobranca")
        ctr, emp = r.get("contrato_codigo"), r.get("empresa_codigo")
        if (u == "contrato" and (not ctr or emp)) or (u == "empresa" and (not emp or ctr)):
            out.append(Violation("unidade_cobranca_inconsistente", ERROR, "fatura",
                                 f"unidade '{u}' exige só o código correspondente", r))
        bruto, desc, acr = _num(r, "valor_bruto"), _num(r, "valor_desconto"), _num(r, "valor_acrescimo")
        if bruto < 0 or desc < 0 or acr < 0:
            out.append(Violation("valor_negativo", ERROR, "fatura", "valor negativo no documento", r))
        if r.get("valor_total") is not None and abs(_num(r, "valor_total") - (bruto - desc + acr)) > 0.01:
            out.append(Violation("fatura_total_inconsistente", ERROR, "fatura",
                                 "valor_total != bruto − desconto + acréscimo", r))
    return out


def _check_fatura_item(rows: Iterable[dict], ctx: QualityContext) -> list[Violation]:
    out: list[Violation] = []
    for r in rows:
        if _num(r, "valor") < 0 and r.get("natureza") not in ("desconto", "ajuste", "retroativo"):
            out.append(Violation("valor_negativo", ERROR, "fatura_item",
                                 "valor negativo só em desconto/ajuste/retroativo", r))
    return out


def _check_recebimento(rows: Iterable[dict], ctx: QualityContext) -> list[Violation]:
    return [Violation("valor_negativo", ERROR, "recebimento", "valor recebido negativo", r)
            for r in rows if _num(r, "valor_recebido") < 0]


def _check_receita_reconhecida(rows: Iterable[dict], ctx: QualityContext) -> list[Violation]:
    out: list[Violation] = []
    for r in rows:
        if not _is_first_of_month(r.get("competencia")):
            out.append(Violation("competencia_invalida", ERROR, "receita_reconhecida",
                                 f"competência {r.get('competencia')} inválida", r))
        if _num(r, "receita_contraprestacao") < 0 and r.get("natureza") != "ajuste":
            out.append(Violation("valor_negativo", ERROR, "receita_reconhecida",
                                 "receita negativa só em natureza 'ajuste'", r))
        if r.get("empresa_codigo") and r.get("contrato_codigo"):
            out.append(Violation("receita_nivel_ambiguo", ERROR, "receita_reconhecida",
                                 "informe contrato (detalhado) OU empresa (consolidado), não ambos", r))
    return out


def _vigencias(entity: str, chave: tuple[str, ...]):
    """Vigências [início, fim) por chave: fim > início e sem sobreposição (Evolução V1)."""

    def check(rows: Iterable[dict], ctx: QualityContext) -> list[Violation]:
        out: list[Violation] = []
        grupos: dict[tuple, list[dict]] = {}
        for r in rows:
            if not r.get("tenant_id"):
                out.append(Violation("tenant_id_ausente", ERROR, entity, "sem tenant_id", r))
            ini, fim = r.get("vigencia_inicio"), r.get("vigencia_fim")
            if ini and fim and str(fim) <= str(ini):
                out.append(Violation("vigencia_invertida", ERROR, entity,
                                     f"vigencia_fim {fim} <= vigencia_inicio {ini}", r))
                continue
            if entity == "preco_contratado" and _num(r, "valor_unitario") <= 0:
                out.append(Violation("preco_nao_positivo", ERROR, entity,
                                     f"valor_unitario {r.get('valor_unitario')} <= 0", r))
            if ini:
                grupos.setdefault(tuple(r.get(c) for c in chave), []).append(r)
        for linhas in grupos.values():
            linhas.sort(key=lambda x: str(x["vigencia_inicio"]))
            for ant, prox in zip(linhas, linhas[1:], strict=False):
                fim_ant = ant.get("vigencia_fim")
                if fim_ant is None or str(prox["vigencia_inicio"]) < str(fim_ant):
                    out.append(Violation("vigencia_sobreposta", ERROR, entity,
                                         f"vigência iniciada em {prox['vigencia_inicio']} "
                                         "sobrepõe a anterior", prox))
        return out

    return check


_DISPATCH = {
    "evento_assistencial": _check_evento,
    "beneficiario": _check_beneficiario,
    "receita": _check_receita,
    "preco_contratado": _vigencias("preco_contratado", ("prestador_codigo", "procedimento_codigo")),
    "vinculo_beneficiario": _vigencias("vinculo_beneficiario", ("beneficiario_codigo",)),
    # Consolidação do canônico V1
    "empresa_contrato": _vigencias("empresa_contrato", ("contrato_codigo", "papel")),
    "fatura": _check_fatura,
    "fatura_item": _check_fatura_item,
    "recebimento": _check_recebimento,
    "receita_reconhecida": _check_receita_reconhecida,
}


def avaliar(entity: str, rows: Iterable[dict], ctx: QualityContext | None = None) -> list[Violation]:
    ctx = ctx or QualityContext()
    fn = _DISPATCH.get(entity)
    if fn is None:
        return []
    return fn(list(rows), ctx)


def bloqueia_processamento(violacoes: Iterable[Violation]) -> bool:
    return any(v.severity == ERROR for v in violacoes)
