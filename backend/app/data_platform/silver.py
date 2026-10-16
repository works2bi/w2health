"""Promoção para a SILVER — as tabelas canônicas do W2Health (as mesmas que o gerador
sintético popula). Recebe linhas canônicas já validadas pelo gate de Data Quality.

Regras de carga (docs/INGESTION_FRAMEWORK.md §Idempotência):
* dimensões (especialidade, plano, contrato, prestador, procedimento, beneficiário):
  UPSERT pela chave de negócio `(tenant_id, codigo)` — atualiza atributos e linhagem;
  ausência de um registro no arquivo NÃO apaga nada (não se infere exclusão);
* plano_contrato (N:N): UPSERT pelo par `(tenant_id, contrato, plano)` — mesma regra:
  par ausente no arquivo não é removido (beneficiários podem apontar para ele);
* fatos por competência (receita, evento): SNAPSHOT — as competências presentes no
  arquivo são substituídas (eventos: só os da MESMA fonte); registro removido da origem
  some no reprocessamento daquela competência; reenviar o mesmo arquivo não duplica.
* região: derivada de cidade/UF (macrorregião pela divisão oficial do IBGE).

Tudo roda na sessão do pipeline, amarrada ao tenant (RLS) e numa única transação —
a publicação só acontece se Gold e reconciliação também passarem.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import delete, func, literal_column, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.faixas import faixa_etaria, idade_em
from app.data_platform.canonical import MACRORREGIAO_UF
from app.data_platform.context import PipelineContext
from app.models import (
    Beneficiario,
    Competencia,
    Contrato,
    Especialidade,
    EventoAssistencial,
    Plano,
    PlanoContrato,
    Prestador,
    Procedimento,
    Receita,
    Regiao,
)

MESES_PT = {1: "Janeiro", 2: "Fevereiro", 3: "Março", 4: "Abril", 5: "Maio", 6: "Junho",
            7: "Julho", 8: "Agosto", 9: "Setembro", 10: "Outubro", 11: "Novembro", 12: "Dezembro"}
#: rótulo neutro quando o grupo do procedimento não é informado (dimensão de análise
#: exige valor; não é regra de negócio — aparece literalmente como "nao_informado")
GRUPO_NAO_INFORMADO = "nao_informado"
_CHUNK = 1000


@dataclass
class SilverStats:
    inserted: dict[str, int] = field(default_factory=dict)
    updated: dict[str, int] = field(default_factory=dict)
    deleted: dict[str, int] = field(default_factory=dict)

    def add(self, entity: str, ins: int, upd: int = 0, dele: int = 0) -> None:
        self.inserted[entity] = self.inserted.get(entity, 0) + ins
        self.updated[entity] = self.updated.get(entity, 0) + upd
        self.deleted[entity] = self.deleted.get(entity, 0) + dele


def existing_codes(session: Session, tenant_id: str) -> dict[str, set]:
    """Códigos de negócio já presentes na Silver do tenant (para validar FKs de cargas
    incrementais — ex.: só eventos, referenciando beneficiários já carregados)."""
    out = {}
    for ent, model in (("especialidade", Especialidade), ("plano", Plano), ("contrato", Contrato),
                       ("prestador", Prestador), ("procedimento", Procedimento),
                       ("beneficiario", Beneficiario)):
        out[ent] = set(session.execute(select(model.codigo).where(model.tenant_id == tenant_id)).scalars())
    out["plano_contrato"] = set(session.execute(
        select(Contrato.codigo, Plano.codigo)
        .join(PlanoContrato, PlanoContrato.id_contrato == Contrato.id)
        .join(Plano, Plano.id == PlanoContrato.id_plano)
        .where(PlanoContrato.tenant_id == tenant_id)).tuples())
    return out


class SilverLoader:
    def __init__(self, session: Session, ctx: PipelineContext, source_system: str) -> None:
        self.s = session
        self.ctx = ctx
        self.lineage = {"source_system": source_system,
                        "source_connection_id": ctx.source_connection_id,
                        "ingestion_run_id": ctx.ingestion_run_id}
        self.stats = SilverStats()
        self._ids: dict[str, dict] = {}

    # ------------------------------------------------------------------ utilitários
    def _map(self, model, *cols) -> dict:
        rows = self.s.execute(select(model.codigo, model.id, *cols)
                              .where(model.tenant_id == self.ctx.tenant_id)).all()
        return {r[0]: (r[1] if not cols else tuple(r[1:])) for r in rows}

    def ids(self, entity: str) -> dict:
        if entity not in self._ids:
            m = {"especialidade": (Especialidade,), "plano": (Plano,), "contrato": (Contrato,),
                 "prestador": (Prestador,),
                 "procedimento": (Procedimento, Procedimento.id_especialidade),
                 "beneficiario": (Beneficiario, Beneficiario.id_regiao)}[entity]
            self._ids[entity] = self._map(*m)
        return self._ids[entity]

    def _upsert(self, model, entity: str, rows: list[dict], constraint: str, update_cols: list[str]):
        if not rows:
            return
        ins = upd = 0
        tabela = model.__table__
        for i in range(0, len(rows), _CHUNK):
            stmt = pg_insert(tabela).values(rows[i:i + _CHUNK])
            stmt = stmt.on_conflict_do_update(
                constraint=constraint,
                set_={c: stmt.excluded[c] for c in update_cols + list(self.lineage) + ["source_record_id"]},
            ).returning(tabela.c.id, literal_column("(xmax = 0)").label("inserido"))
            for _id, inserido in self.s.execute(stmt).all():
                ins += 1 if inserido else 0
                upd += 0 if inserido else 1
        self.stats.add(entity, ins, upd)
        self._ids.pop(entity, None)

    def _base(self, codigo: str) -> dict:
        return {"tenant_id": self.ctx.tenant_id, **self.lineage, "source_record_id": codigo}

    # ------------------------------------------------------------------ competências
    def ensure_competencias(self, meses: set[date]) -> None:
        if not meses:
            return
        existentes = set(self.s.execute(select(Competencia.competencia)
                                        .where(Competencia.competencia.in_(meses))).scalars())
        novas = [{"competencia": m, "ano": m.year, "mes": m.month, "mes_nome": MESES_PT[m.month],
                  "trimestre": (m.month - 1) // 3 + 1, "is_inverno": m.month in (6, 7, 8)}
                 for m in sorted(meses - existentes)]
        if novas:
            self.s.execute(pg_insert(Competencia.__table__).values(novas).on_conflict_do_nothing())

    # ------------------------------------------------------------------ regiões
    def regiao_ids(self, pares: set[tuple[str, str]]) -> dict[tuple[str, str], int]:
        atuais = {(r.cidade, r.uf): r.id for r in self.s.execute(
            select(Regiao).where(Regiao.tenant_id == self.ctx.tenant_id)).scalars()}
        faltando = [p for p in pares if p not in atuais]
        for cidade, uf in sorted(faltando):
            r = Regiao(tenant_id=self.ctx.tenant_id, cidade=cidade, uf=uf,
                       macrorregiao=MACRORREGIAO_UF[uf], **self.lineage,
                       source_record_id=f"{cidade}/{uf}")
            self.s.add(r)
            self.s.flush()
            atuais[(cidade, uf)] = r.id
        if faltando:
            self.stats.add("regiao", len(faltando))
        return atuais

    # ------------------------------------------------------------------ dimensões
    def especialidades(self, rows: list[dict]) -> None:
        self._upsert(Especialidade, "especialidade", [
            {**self._base(r["codigo"]), "codigo": r["codigo"], "nome": r["nome"], "grupo": r.get("grupo")}
            for r in rows], "uq_especialidades_tenant_codigo", ["nome", "grupo"])

    def planos(self, rows: list[dict]) -> None:
        self._upsert(Plano, "plano", [
            {**self._base(r["codigo"]), "codigo": r["codigo"], "nome": r["nome"],
             "segmentacao": r.get("segmentacao"),
             "tem_coparticipacao": bool(r.get("tem_coparticipacao") or False),
             "percentual_coparticipacao": r.get("percentual_coparticipacao") or 0}
            for r in rows], "uq_planos_tenant_codigo",
            ["nome", "segmentacao", "tem_coparticipacao", "percentual_coparticipacao"])

    def contratos(self, rows: list[dict]) -> None:
        self._upsert(Contrato, "contrato", [
            {**self._base(r["codigo"]), "codigo": r["codigo"],
             # nome é obrigatório no serving; sem nome na origem exibe-se o próprio código
             "nome": r.get("nome") or r["codigo"], "tipo": r.get("tipo"), "vidas_alvo": None}
            for r in rows], "uq_contratos_tenant_codigo", ["nome", "tipo"])

    def planos_contratos(self, rows: list[dict]) -> None:
        planos, contratos = self.ids("plano"), self.ids("contrato")
        dados = [{**self._base(f"{r['contrato_codigo']}/{r['plano_codigo']}"),
                  "id_contrato": contratos[r["contrato_codigo"]], "id_plano": planos[r["plano_codigo"]]}
                 for r in rows]
        tabela = PlanoContrato.__table__
        ins = upd = 0
        for i in range(0, len(dados), _CHUNK):
            stmt = pg_insert(tabela).values(dados[i:i + _CHUNK])
            stmt = stmt.on_conflict_do_update(
                constraint="pk_planos_contratos",
                set_={c: stmt.excluded[c] for c in list(self.lineage) + ["source_record_id"]},
            ).returning(literal_column("(xmax = 0)").label("inserido"))
            for (inserido,) in self.s.execute(stmt).all():
                ins += 1 if inserido else 0
                upd += 0 if inserido else 1
        self.stats.add("plano_contrato", ins, upd)

    def prestadores(self, rows: list[dict]) -> None:
        esp = self.ids("especialidade")
        reg = self.regiao_ids({(r["cidade"], r["uf"]) for r in rows})
        self._upsert(Prestador, "prestador", [
            {**self._base(r["codigo"]), "codigo": r["codigo"], "nome_ficticio": r["nome"],
             "tipo_prestador": r.get("tipo_prestador"), "id_regiao": reg[(r["cidade"], r["uf"])],
             "id_especialidade_principal": esp[r["especialidade_codigo"]], "nivel_preco": None}
            for r in rows], "uq_prestadores_tenant_codigo",
            ["nome_ficticio", "tipo_prestador", "id_regiao", "id_especialidade_principal"])

    def procedimentos(self, rows: list[dict]) -> None:
        esp = self.ids("especialidade")
        self._upsert(Procedimento, "procedimento", [
            {**self._base(r["codigo"]), "codigo": r["codigo"], "descricao": r["descricao"],
             "id_especialidade": esp[r["especialidade_codigo"]],
             "grupo_procedimento": r.get("grupo_procedimento") or GRUPO_NAO_INFORMADO,
             "perfil_utilizacao": r.get("perfil_utilizacao"),
             "complexidade": None, "custo_base": None, "tipo_atendimento_tipico": None,
             "idade_min": 0, "idade_max": 120}
            for r in rows], "uq_procedimentos_tenant_codigo",
            ["descricao", "id_especialidade", "grupo_procedimento", "perfil_utilizacao"])

    def beneficiarios(self, rows: list[dict], ref_inicio: date, ref_fim: date) -> None:
        planos, contratos = self.ids("plano"), self.ids("contrato")
        reg = self.regiao_ids({(r["cidade"], r["uf"]) for r in rows})
        out = []
        for r in rows:
            saida = r.get("data_saida")
            out.append({
                **self._base(r["codigo"]), "codigo": r["codigo"], "sexo": r.get("sexo"),
                "data_nascimento": r["data_nascimento"],
                # mesma aproximação do gerador: faixa fixada na 1ª competência carregada
                "faixa_etaria": faixa_etaria(idade_em(r["data_nascimento"], ref_inicio)),
                "id_regiao": reg[(r["cidade"], r["uf"])], "id_plano": planos[r["plano_codigo"]],
                "id_contrato": contratos[r["contrato_codigo"]], "data_adesao": r["data_adesao"],
                "data_saida": saida,
                "status": "inativo" if saida is not None and saida <= ref_fim else "ativo",
            })
        self._upsert(Beneficiario, "beneficiario", out, "uq_beneficiarios_tenant_codigo",
                     ["sexo", "data_nascimento", "faixa_etaria", "id_regiao", "id_plano",
                      "id_contrato", "data_adesao", "data_saida", "status"])

    # ------------------------------------------------------------------ fatos
    def receitas(self, rows: list[dict]) -> None:
        planos = self.ids("plano")
        meses = {r["competencia"] for r in rows}
        removidas = self.s.execute(delete(Receita).where(
            Receita.tenant_id == self.ctx.tenant_id, Receita.competencia.in_(meses))).rowcount or 0
        dados = [{**self._base(f"{r['competencia']:%Y-%m}/{r['plano_codigo']}"),
                  "competencia": r["competencia"], "id_plano": planos[r["plano_codigo"]],
                  "quantidade_beneficiarios": r["quantidade_beneficiarios"],
                  "receita_contraprestacao": r["receita_contraprestacao"]} for r in rows]
        for i in range(0, len(dados), _CHUNK):
            self.s.execute(pg_insert(Receita.__table__).values(dados[i:i + _CHUNK]))
        self.stats.add("receita", len(dados), 0, removidas)

    def eventos(self, rows: list[dict]) -> None:
        benef, proc = self.ids("beneficiario"), self.ids("procedimento")
        prest, esp = self.ids("prestador"), self.ids("especialidade")
        meses = {r["competencia"] for r in rows}
        removidos = self.s.execute(delete(EventoAssistencial).where(
            EventoAssistencial.tenant_id == self.ctx.tenant_id,
            EventoAssistencial.source_connection_id == self.ctx.source_connection_id,
            EventoAssistencial.competencia.in_(meses))).rowcount or 0
        dados = []
        for r in rows:
            id_benef, id_regiao = benef[r["beneficiario_codigo"]]
            id_proc, id_esp_proc = proc[r["procedimento_codigo"]]
            dados.append({
                "tenant_id": self.ctx.tenant_id, **self.lineage,
                "source_record_id": r["source_record_id"],
                "id_beneficiario": id_benef, "id_prestador": prest[r["prestador_codigo"]],
                "id_procedimento": id_proc,
                "id_especialidade": esp[r["especialidade_codigo"]] if r.get("especialidade_codigo") else id_esp_proc,
                "id_diagnostico": None, "id_regiao": id_regiao,
                "data_evento": r["data_evento"], "competencia": r["competencia"],
                "tipo_atendimento": r["tipo_atendimento"], "quantidade": r["quantidade"],
                "valor_apresentado": r["valor_apresentado"], "valor_glosado": r["valor_glosado"],
                "valor_pago": r["valor_pago"], "valor_coparticipacao": r["valor_coparticipacao"],
                "cenario_tag": None,
            })
        for i in range(0, len(dados), _CHUNK):
            self.s.execute(pg_insert(EventoAssistencial.__table__).values(dados[i:i + _CHUNK]))
        self.stats.add("evento_assistencial", len(dados), 0, removidos)


def derive_canonical(entity: str, row: dict) -> dict:
    """Derivações DECLARADAS nos contratos canônicos (nada além disso)."""
    if entity == "evento_assistencial":
        r = dict(row)
        if r.get("competencia") is None:
            r["competencia"] = r["data_evento"].replace(day=1)
        r["valor_glosado"] = r.get("valor_glosado") or 0
        r["valor_coparticipacao"] = r.get("valor_coparticipacao") or 0
        if r.get("valor_pago") is None:
            r["valor_pago"] = r["valor_apresentado"] - r["valor_glosado"]
        r["quantidade"] = r.get("quantidade") or 1
        return r
    if entity in ("beneficiario", "prestador"):
        r = dict(row)
        r["uf"] = str(r["uf"]).strip().upper()
        r["cidade"] = str(r["cidade"]).strip()
        return r
    return row


def count_for_run(session: Session, model, ingestion_run_id: int) -> int:
    return session.execute(select(func.count()).select_from(model)
                           .where(model.ingestion_run_id == ingestion_run_id)).scalar_one()
