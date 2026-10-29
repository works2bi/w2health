"""Relatório mensal da diretoria (Evolução, item 5).

Uma página consolidada por competência, montada só com o que já existe no produto — nada é
recalculado aqui: KPIs da Visão Executiva, completude da competência (item 1), principais
fatores (insights), alertas por severidade, planos de ação e seu efeito (item 3) e a posição
frente ao mercado (item 7). O frontend imprime em PDF (`/relatorio`).

Cada bloco respeita o plano do tenant: módulo não contratado vem `null` e entra em
`restricoes_plano`. O relatório NUNCA traz beneficiário identificado.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analytics import alerts, benchmark, completude, efeito_acao, insights, sinistralidade
from app.db.tenant_scope import tenant_of
from app.models.gestao import PlanoAcao
from app.saas.feature_filters import filter_alertas, filter_insights

MAX_FATORES = 5
MAX_PLANOS = 6


def montar(session: Session, competencia: date, features: frozenset[str], tenant_nome: str,
           segmento: str = "medicina_de_grupo") -> dict:
    restricoes: list[str] = []
    execu = sinistralidade.executivo(session, competencia, "mes_anterior")
    compl = completude.da_competencia(session, competencia)

    fatores = None
    if "insights" in features:
        fatores = [
            {k: i.get(k) for k in ("titulo", "descricao", "severidade", "deep_link")}
            for i in filter_insights(insights.gerar(session, competencia), features)
            if i["severidade"] != "positiva"
        ][:MAX_FATORES]
    else:
        restricoes.append("insights")

    alertas = None
    if "alerts" in features:
        itens = filter_alertas([a.as_dict() for a in alerts.avaliar_regras(session, competencia)], features)
        alertas = {"total": len(itens),
                   "por_severidade": {s: sum(1 for a in itens if a["severidade"] == s)
                                      for s in ("critica", "atencao", "informativo")}}
    else:
        restricoes.append("alerts")

    planos = None
    if "insights" in features:
        rows = session.execute(select(PlanoAcao).where(PlanoAcao.tenant_id == tenant_of(session))
                               .order_by(PlanoAcao.criado_em.desc())).scalars().all()
        por_status: dict[str, int] = {}
        for p in rows:
            por_status[p.status] = por_status.get(p.status, 0) + 1
        hoje = date.today()
        atrasados = sum(1 for p in rows if p.prazo and p.prazo < hoje and p.status in ("aberto", "em_andamento"))
        destaque = []
        for p in [p for p in rows if p.status != "cancelado"][:MAX_PLANOS]:
            ef = efeito_acao.medir(session, p)
            destaque.append({"titulo": p.titulo, "alvo": p.alvo_rotulo, "responsavel": p.responsavel,
                             "prazo": p.prazo.isoformat() if p.prazo else None, "status": p.status,
                             "situacao_efeito": ef["situacao"], "variacao_alvo_pct": ef.get("variacao_alvo_pct"),
                             "efeito_relativo_pp": ef.get("efeito_relativo_pp")})
        planos = {"total": len(rows), "por_status": por_status, "atrasados": atrasados, "destaque": destaque}

    mercado = None
    if "loss_ratio_intelligence" in features:
        b = benchmark.comparar(session, competencia, segmento)
        if b["disponivel"]:
            mercado = {k: b[k] for k in ("segmento_rotulo", "fonte", "ilustrativo", "aviso", "itens", "janela")}
    else:
        restricoes.append("loss_ratio_intelligence")

    sel = compl.get("selecionada")
    return {
        "tenant": tenant_nome,
        "competencia": competencia.isoformat(),
        "kpis": execu["kpis"],
        "decomposicao_receita_despesa": execu["decomposicao_receita_despesa"],
        "serie": execu["serie"][-13:],
        "completude": None if not compl["disponivel"] or sel is None else {
            "status": sel["status"], "completude_pct": sel["completude_pct"],
            "ibnr_estimado": sel["ibnr_estimado"],
            "despesa_liquida_estimada_final": sel["despesa_liquida_estimada_final"],
            "sinistralidade_projetada": sel["sinistralidade_projetada"]},
        "principais_fatores": fatores,
        "alertas": alertas,
        "planos_acao": planos,
        "mercado": mercado,
        "restricoes_plano": sorted(set(restricoes)),
        "notas": [
            "Despesa = despesa líquida (apresentado − glosa − coparticipação), por competência de atendimento.",
            "Competência preliminar: os números ainda vão subir com as contas a chegar (estimativa ao lado).",
            "Efeito de plano de ação é indício (HIPÓTESE), não prova de causalidade.",
        ],
    }
