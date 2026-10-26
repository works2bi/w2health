"""Promoção para a SILVER — as tabelas canônicas do W2Health (as mesmas que o gerador
sintético popula). Recebe linhas canônicas já validadas pelo gate de Data Quality.

Regras de carga (docs/INGESTION_FRAMEWORK.md §Idempotência):
* dimensões (especialidade, plano, contrato, prestador, procedimento, beneficiário):
  UPSERT pela chave de negócio `(tenant_id, codigo)` — atualiza atributos e linhagem;
  ausência de um registro no arquivo NÃO apaga nada (não se infere exclusão);
* plano_contrato (N:N): UPSERT pelo par `(tenant_id, contrato, plano)` — mesma regra:
  par ausente no arquivo não é removido (beneficiários podem apontar para ele);
* vinculo_beneficiario / preco_contratado (Evolução V1, opcionais): UPSERT pela chave
  `(beneficiário, início)` / `(prestador, procedimento, início)`;
* evento: contrato/plano são CONGELADOS no evento — pelo vínculo histórico vigente na
  `data_evento`, se o beneficiário tem histórico; senão pelo cadastro, SOMENTE se
  `data_adesao ≤ data_evento < data_saida`. Fora disso o evento fica NÃO ATRIBUÍDO
  (`id_contrato` nulo) — nunca o contrato atual (Consolidação V1);
* empresa / empresa_contrato (Consolidação V1): UPSERT por código / (contrato, papel, início);
* fatura: UPSERT por número; itens: SNAPSHOT por documento (itens do documento substituídos);
  recebimento: UPSERT por número; receita reconhecida: SNAPSHOT por competência e fonte,
  com nível de atribuição resolvido aqui (contrato × plano · contrato · empresa · não
  atribuída) — código sem cadastro vira `nao_atribuida` com `referencia_origem`;
* calendário global (`competencias`): gravado numa transação PRÓPRIA e curta, antes da
  publicação — duas cargas de tenants diferentes não se serializam nele;
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
    BeneficiarioVinculo,
    Competencia,
    Contrato,
    Empresa,
    EmpresaContrato,
    Especialidade,
    EventoAssistencial,
    Fatura,
    FaturaItem,
    Plano,
    PlanoContrato,
    PrecoContratado,
    Prestador,
    Procedimento,
    Recebimento,
    Receita,
    ReceitaReconhecida,
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
    # Consolidação V1
    out["empresa"] = set(session.execute(select(Empresa.codigo).where(Empresa.tenant_id == tenant_id)).scalars())
    out["fatura"] = set(session.execute(
        select(Fatura.numero_documento).where(Fatura.tenant_id == tenant_id)).scalars())
    out["_vigencias"] = _vigencias_persistidas(session, tenant_id)
    niveis: dict[str, set] = {}
    for doc, nivel in session.execute(
            select(ReceitaReconhecida.numero_documento, ReceitaReconhecida.nivel_atribuicao)
            .where(ReceitaReconhecida.tenant_id == tenant_id,
                   ReceitaReconhecida.numero_documento.is_not(None)).distinct()).all():
        niveis.setdefault(doc, set()).add("empresa" if nivel == "empresa" else "detalhe")
    out["_receita_doc_niveis"] = niveis
    return out


def _vigencias_persistidas(session: Session, tenant_id: str) -> dict[str, dict]:
    """entidade → {chave de códigos: [(início, fim)]} — base da verificação de sobreposição
    contra o JÁ carregado (não só dentro do lote)."""
    out: dict[str, dict] = {"vinculo_beneficiario": {}, "preco_contratado": {}, "empresa_contrato": {}}
    for cod, ini, fim in session.execute(
            select(Beneficiario.codigo, BeneficiarioVinculo.vigencia_inicio, BeneficiarioVinculo.vigencia_fim)
            .join(Beneficiario, Beneficiario.id == BeneficiarioVinculo.id_beneficiario)
            .where(BeneficiarioVinculo.tenant_id == tenant_id)).all():
        out["vinculo_beneficiario"].setdefault((cod,), []).append((ini, fim))
    for pc, rc, ini, fim in session.execute(
            select(Prestador.codigo, Procedimento.codigo, PrecoContratado.vigencia_inicio,
                   PrecoContratado.vigencia_fim)
            .join(Prestador, Prestador.id == PrecoContratado.id_prestador)
            .join(Procedimento, Procedimento.id == PrecoContratado.id_procedimento)
            .where(PrecoContratado.tenant_id == tenant_id)).all():
        out["preco_contratado"].setdefault((pc, rc), []).append((ini, fim))
    for cc, papel, ini, fim in session.execute(
            select(Contrato.codigo, EmpresaContrato.papel, EmpresaContrato.vigencia_inicio,
                   EmpresaContrato.vigencia_fim)
            .join(Contrato, Contrato.id == EmpresaContrato.id_contrato)
            .where(EmpresaContrato.tenant_id == tenant_id)).all():
        out["empresa_contrato"].setdefault((cc, papel), []).append((ini, fim))
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
        """Grava os meses no calendário GLOBAL numa transação própria e curta.

        O calendário é compartilhado entre tenants e idempotente. Gravá-lo dentro da
        transação de publicação fazia a carga de um tenant esperar a de outro commitar
        (conflito de chave em linha não confirmada) — cargas paralelas se serializavam.
        Um mês "órfão" (carga que depois falha) é inofensivo: o Gold só lê a janela do tenant.
        """
        if not meses:
            return
        novas = [{"competencia": m, "ano": m.year, "mes": m.month, "mes_nome": MESES_PT[m.month],
                  "trimestre": (m.month - 1) // 3 + 1, "is_inverno": m.month in (6, 7, 8)}
                 for m in sorted(meses)]
        with Session(bind=self.s.get_bind()) as cal:
            existentes = set(cal.execute(select(Competencia.competencia)
                                         .where(Competencia.competencia.in_(meses))).scalars())
            faltam = [n for n in novas if n["competencia"] not in existentes]
            if faltam:
                cal.execute(pg_insert(Competencia.__table__).values(faltam).on_conflict_do_nothing())
            cal.commit()

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

    def vinculos(self, rows: list[dict]) -> None:
        benef, planos, contratos = self.ids("beneficiario"), self.ids("plano"), self.ids("contrato")
        self._upsert(BeneficiarioVinculo, "vinculo_beneficiario", [
            {**self._base(f"{r['beneficiario_codigo']}/{r['vigencia_inicio']}"),
             "id_beneficiario": benef[r["beneficiario_codigo"]][0],
             "id_contrato": contratos[r["contrato_codigo"]], "id_plano": planos[r["plano_codigo"]],
             "vigencia_inicio": r["vigencia_inicio"], "vigencia_fim": r.get("vigencia_fim"),
             "situacao": r.get("situacao")}
            for r in rows], "uq_vinculos_tenant_benef_inicio",
            ["id_contrato", "id_plano", "vigencia_fim", "situacao"])

    def precos(self, rows: list[dict]) -> None:
        prest, proc = self.ids("prestador"), self.ids("procedimento")
        self._upsert(PrecoContratado, "preco_contratado", [
            {**self._base(f"{r['prestador_codigo']}/{r['procedimento_codigo']}/{r['vigencia_inicio']}"),
             "id_prestador": prest[r["prestador_codigo"]],
             "id_procedimento": proc[r["procedimento_codigo"]][0],
             "vigencia_inicio": r["vigencia_inicio"], "vigencia_fim": r.get("vigencia_fim"),
             "valor_unitario": r["valor_unitario"], "tipo_remuneracao": r.get("tipo_remuneracao")}
            for r in rows], "uq_precos_tenant_prest_proc_inicio",
            ["valor_unitario", "vigencia_fim", "tipo_remuneracao"])

    def _vinculo_resolver(self):
        """(id_beneficiario, data) -> (id_contrato, id_plano) ou (None, None).

        Beneficiário COM histórico: a vigência que contém a data; sem nenhuma → não atribuído.
        Beneficiário SEM histórico: o cadastro vale como um vínculo único de `data_adesao` até
        `data_saida` (exclusiva); fora desse intervalo → não atribuído. Nunca o contrato
        atual "por falta de opção" (Consolidação V1). Ambiguidade (vigências sobrepostas) é
        impedida na carga."""
        atual = {r[0]: (r[1], r[2], r[3], r[4]) for r in self.s.execute(
            select(Beneficiario.id, Beneficiario.id_contrato, Beneficiario.id_plano,
                   Beneficiario.data_adesao, Beneficiario.data_saida)
            .where(Beneficiario.tenant_id == self.ctx.tenant_id)).all()}
        hist: dict[int, list[tuple]] = {}
        for v in self.s.execute(select(
                BeneficiarioVinculo.id_beneficiario, BeneficiarioVinculo.vigencia_inicio,
                BeneficiarioVinculo.vigencia_fim, BeneficiarioVinculo.id_contrato,
                BeneficiarioVinculo.id_plano)
                .where(BeneficiarioVinculo.tenant_id == self.ctx.tenant_id)).all():
            hist.setdefault(v[0], []).append(tuple(v[1:]))

        def resolver(id_benef: int, dia: date) -> tuple[int | None, int | None]:
            if id_benef in hist:
                for ini, fim, ctr, pl in hist[id_benef]:
                    if ini <= dia and (fim is None or dia < fim):
                        return ctr, pl
                return None, None
            ctr, pl, adesao, saida = atual[id_benef]
            if adesao <= dia and (saida is None or dia < saida):
                return ctr, pl
            return None, None

        return resolver

    # ------------------------------------------------------------------ empresa (V1)
    def empresas(self, rows: list[dict]) -> None:
        self._upsert(Empresa, "empresa", [
            {**self._base(r["codigo"]), "codigo": r["codigo"], "nome": r["nome"],
             "tipo": r.get("tipo"), "status": r.get("status")}
            for r in rows], "uq_empresas_tenant_codigo", ["nome", "tipo", "status"])

    def empresas_contratos(self, rows: list[dict]) -> None:
        emp, ctr = self._map(Empresa), self.ids("contrato")
        self._upsert(EmpresaContrato, "empresa_contrato", [
            {**self._base(f"{r['contrato_codigo']}/{r['papel']}/{r['vigencia_inicio']}"),
             "id_empresa": emp[r["empresa_codigo"]], "id_contrato": ctr[r["contrato_codigo"]],
             "papel": r["papel"], "vigencia_inicio": r["vigencia_inicio"],
             "vigencia_fim": r.get("vigencia_fim")}
            for r in rows], "uq_empctr_tenant_ctr_papel_inicio", ["id_empresa", "vigencia_fim"])

    # ------------------------------------------------------------------ faturamento (V1)
    def _upsert_por(self, model, entity: str, dados: list[dict], constraint: str,
                    cols: list[str]) -> None:
        ins = upd = 0
        for i in range(0, len(dados), _CHUNK):
            stmt = pg_insert(model.__table__).values(dados[i:i + _CHUNK])
            stmt = stmt.on_conflict_do_update(constraint=constraint,
                                              set_={c: stmt.excluded[c] for c in cols}
                                              ).returning(literal_column("(xmax = 0)"))
            for (inserido,) in self.s.execute(stmt).all():
                ins += 1 if inserido else 0
                upd += 0 if inserido else 1
        self.stats.add(entity, ins, upd)

    def faturas(self, rows: list[dict]) -> None:
        emp, ctr = self._map(Empresa), self.ids("contrato")
        dados = [{**self._base(r["numero_documento"]), "numero_documento": r["numero_documento"],
                  "unidade_cobranca": r["unidade_cobranca"],
                  "id_contrato": ctr[r["contrato_codigo"]] if r["unidade_cobranca"] == "contrato" else None,
                  "id_empresa": emp[r["empresa_codigo"]] if r["unidade_cobranca"] == "empresa" else None,
                  "competencia_faturamento": r["competencia_faturamento"],
                  "data_emissao": r.get("data_emissao"), "data_vencimento": r.get("data_vencimento"),
                  "valor_bruto": r["valor_bruto"], "valor_desconto": r["valor_desconto"],
                  "valor_acrescimo": r["valor_acrescimo"], "valor_total": r["valor_total"],
                  "status": r.get("status")} for r in rows]
        cols = ["unidade_cobranca", "id_contrato", "id_empresa", "competencia_faturamento",
                "data_emissao", "data_vencimento", "valor_bruto", "valor_desconto",
                "valor_acrescimo", "valor_total", "status", *self.lineage]
        self._upsert_por(Fatura, "fatura", dados, "uq_faturas_tenant_numero", cols)

    def _faturas_ids(self) -> dict[str, int]:
        return dict(self.s.execute(select(Fatura.numero_documento, Fatura.id)
                                   .where(Fatura.tenant_id == self.ctx.tenant_id)).all())

    def fatura_itens(self, rows: list[dict]) -> None:
        fat, ctr, pl = self._faturas_ids(), self.ids("contrato"), self.ids("plano")
        docs = {fat[r["numero_documento"]] for r in rows}
        removidos = self.s.execute(delete(FaturaItem).where(
            FaturaItem.tenant_id == self.ctx.tenant_id, FaturaItem.id_fatura.in_(docs))).rowcount or 0
        dados = [{**self._base(f"{r['numero_documento']}/{r['numero_item']}"),
                  "id_fatura": fat[r["numero_documento"]], "numero_item": r["numero_item"],
                  "natureza": r["natureza"],
                  "id_contrato": ctr.get(r["contrato_codigo"]) if r.get("contrato_codigo") else None,
                  "id_plano": pl.get(r["plano_codigo"]) if r.get("plano_codigo") else None,
                  "competencia_referencia": r.get("competencia_referencia"),
                  "quantidade_beneficiarios": r.get("quantidade_beneficiarios"),
                  "valor": r["valor"]} for r in rows]
        for i in range(0, len(dados), _CHUNK):
            self.s.execute(pg_insert(FaturaItem.__table__).values(dados[i:i + _CHUNK]))
        self.stats.add("fatura_item", len(dados), 0, removidos)

    def recebimentos(self, rows: list[dict]) -> None:
        fat = self._faturas_ids()
        dados = [{**self._base(r["numero_recebimento"]), "numero_recebimento": r["numero_recebimento"],
                  "id_fatura": fat.get(r["numero_documento"]) if r.get("numero_documento") else None,
                  "data_recebimento": r["data_recebimento"],
                  "competencia_recebimento": r["competencia_recebimento"],
                  "valor_recebido": r["valor_recebido"]} for r in rows]
        cols = ["id_fatura", "data_recebimento", "competencia_recebimento", "valor_recebido",
                *self.lineage]
        self._upsert_por(Recebimento, "recebimento", dados, "uq_receb_tenant_numero", cols)

    def receitas_reconhecidas(self, rows: list[dict]) -> None:
        """SNAPSHOT por competência e fonte. Nível de atribuição pelo que a origem informou E
        pelo que existe no cadastro — nada é inferido nem rateado."""
        ctr, pl, emp = self.ids("contrato"), self.ids("plano"), self._map(Empresa)
        meses = {r["competencia"] for r in rows}
        removidas = self.s.execute(delete(ReceitaReconhecida).where(
            ReceitaReconhecida.tenant_id == self.ctx.tenant_id,
            ReceitaReconhecida.source_connection_id == self.ctx.source_connection_id,
            ReceitaReconhecida.competencia.in_(meses))).rowcount or 0
        dados = [{"tenant_id": self.ctx.tenant_id, **self.lineage,
                  "source_record_id": r["source_record_id"], "competencia": r["competencia"],
                  **atribuicao_receita(r, ctr, pl, emp),
                  "natureza": r["natureza"], "numero_documento": r.get("numero_documento"),
                  "quantidade_beneficiarios": r.get("quantidade_beneficiarios") or 0,
                  "receita_contraprestacao": r["receita_contraprestacao"],
                  "estimada": False, "metodologia": None, "reajuste_aplicado_no_periodo": None}
                 for r in rows]
        for i in range(0, len(dados), _CHUNK):
            self.s.execute(pg_insert(ReceitaReconhecida.__table__).values(dados[i:i + _CHUNK]))
        self.stats.add("receita_reconhecida", len(dados), 0, removidas)

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
        vinculo = self._vinculo_resolver()
        dados = []
        for r in rows:
            id_benef, id_regiao = benef[r["beneficiario_codigo"]]
            id_proc, id_esp_proc = proc[r["procedimento_codigo"]]
            id_contrato, id_plano = vinculo(id_benef, r["data_evento"])
            estab, solic = r.get("estabelecimento_codigo"), r.get("prestador_solicitante_codigo")
            dados.append({
                "tenant_id": self.ctx.tenant_id, **self.lineage,
                "source_record_id": r["source_record_id"],
                "id_beneficiario": id_benef, "id_prestador": prest[r["prestador_codigo"]],
                "id_procedimento": id_proc,
                "id_especialidade": esp[r["especialidade_codigo"]] if r.get("especialidade_codigo") else id_esp_proc,
                "id_diagnostico": None, "id_regiao": id_regiao,
                "id_contrato": id_contrato, "id_plano": id_plano,
                "id_estabelecimento": prest[estab] if estab else None,
                "id_prestador_solicitante": prest[solic] if solic else None,
                "data_evento": r["data_evento"], "competencia": r["competencia"],
                "tipo_atendimento": r["tipo_atendimento"], "quantidade": r["quantidade"],
                "competencia_apresentacao": r.get("competencia_apresentacao"),
                "competencia_pagamento": r.get("competencia_pagamento"),
                "numero_guia": r.get("numero_guia"), "numero_conta": r.get("numero_conta"),
                "unidade_medida": r.get("unidade_medida"),
                "natureza_lancamento": r.get("natureza_lancamento"),
                "valor_pago_origem": r.get("valor_pago_origem"),
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
            r["valor_pago_origem"] = "derivado"
        else:
            r["valor_pago_origem"] = "informado"
        r["quantidade"] = r.get("quantidade") or 1
        return r
    if entity == "empresa_contrato":
        return {**row, "papel": row.get("papel") or "contratante"}
    if entity == "fatura":
        r = dict(row)
        r["valor_desconto"] = r.get("valor_desconto") or 0
        r["valor_acrescimo"] = r.get("valor_acrescimo") or 0
        if r.get("valor_total") is None and r.get("valor_bruto") is not None:
            r["valor_total"] = r["valor_bruto"] - r["valor_desconto"] + r["valor_acrescimo"]
        return r
    if entity == "fatura_item":
        return {**row, "natureza": row.get("natureza") or "mensalidade"}
    if entity == "recebimento":
        r = dict(row)
        if r.get("competencia_recebimento") is None and r.get("data_recebimento") is not None:
            r["competencia_recebimento"] = r["data_recebimento"].replace(day=1)
        return r
    if entity == "receita_reconhecida":
        return {**row, "natureza": row.get("natureza") or "contraprestacao"}
    if entity in ("beneficiario", "prestador"):
        r = dict(row)
        r["uf"] = str(r["uf"]).strip().upper()
        r["cidade"] = str(r["cidade"]).strip()
        return r
    return row


def count_for_run(session: Session, model, ingestion_run_id: int) -> int:
    return session.execute(select(func.count()).select_from(model)
                           .where(model.ingestion_run_id == ingestion_run_id)).scalar_one()


def atribuicao_receita(row: dict, contratos: dict, planos: dict, empresas: dict) -> dict:
    """Nível de atribuição da receita reconhecida (Consolidação V1) — o que a origem informou
    E existe no cadastro. Nada é inferido nem rateado:

    * contrato conhecido (+ plano conhecido)      → `contrato_plano` (ou `contrato`);
    * só empresa conhecida (cobrança consolidada) → `empresa`;
    * qualquer outra combinação                   → `nao_atribuida` + `referencia_origem`.
    """
    cod_c, cod_p, cod_e = row.get("contrato_codigo"), row.get("plano_codigo"), row.get("empresa_codigo")
    c_id = contratos.get(cod_c) if cod_c else None
    p_id = planos.get(cod_p) if cod_p else None
    e_id = empresas.get(cod_e) if cod_e else None
    if c_id is not None and (cod_p is None or p_id is not None):
        nivel = "contrato_plano" if p_id is not None else "contrato"
        return {"nivel_atribuicao": nivel, "id_contrato": c_id, "id_plano": p_id,
                "id_empresa": None, "referencia_origem": None}
    if e_id is not None and not cod_c:
        return {"nivel_atribuicao": "empresa", "id_contrato": None, "id_plano": None,
                "id_empresa": e_id, "referencia_origem": None}
    ref = "|".join(f"{k}={row[k]}" for k in ("contrato_codigo", "plano_codigo", "empresa_codigo")
                   if row.get(k))
    return {"nivel_atribuicao": "nao_atribuida", "id_contrato": None, "id_plano": None,
            "id_empresa": None, "referencia_origem": ref or None}
