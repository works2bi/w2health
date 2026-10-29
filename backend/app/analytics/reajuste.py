"""Simulador de reajuste do contrato — Evolução, item 2.

Pergunta do comercial: "quanto preciso reajustar este contrato para a sinistralidade
voltar à meta?". A conta é simples e fica toda à vista:

    despesa projetada  = despesa líquida dos últimos N meses × (1 + VCMH)
    receita necessária = despesa projetada ÷ meta
    reajuste técnico   = receita necessária ÷ receita reconhecida dos N meses − 1

* Base de receita: a receita reconhecida ATRIBUÍDA ao contrato (Gold). Sem ela, só com a
  receita mensal INFORMADA por quem simula (premissa explícita, rotulada HIPÓTESE);
  sem nenhuma das duas, `disponivel: false` e o motivo — nunca rateio.
* Meses ainda preliminares podem ser completados com a estimativa de contas a chegar da
  carteira (`completude`), aplicada proporcionalmente — rotulado como ESTIMATIVA.
* Histórico = FATO; projeção = HIPÓTESE (depende das premissas meta e VCMH informadas).
"""

from __future__ import annotations

import math
from datetime import date

from sqlalchemy.orm import Session

from app.analytics import completude
from app.repositories import analytics_repo as repo

CENARIOS_REAJUSTE = (0.0, 5.0, 10.0, 15.0, 20.0, 25.0, 30.0)


def _f(x) -> float:
    return float(x) if x is not None else 0.0


def simular(
    session: Session, id_contrato: int, competencia: date, *, meta: float = 75.0,
    vcmh: float = 10.0, meses: int = 12, ajustar_completude: bool = True,
    receita_informada: float | None = None,
) -> dict:
    if not 1 <= meta <= 200:
        raise ValueError("meta de sinistralidade deve estar entre 1% e 200%")
    if not -50 <= vcmh <= 100:
        raise ValueError("VCMH deve estar entre -50% e 100%")
    info = repo.contrato_info(session, id_contrato)
    if info is None:
        raise LookupError("contrato não encontrado")

    serie = [r for r in repo.contrato_serie(session, id_contrato) if r["competencia"] <= competencia]
    janela = [r for r in serie if r.get("receita_reconhecida") is not None][-meses:]
    fonte_receita = "reconhecida_atribuida"
    if not janela and receita_informada and receita_informada > 0:
        janela = [{**r, "receita_reconhecida": receita_informada} for r in serie[-meses:]]
        fonte_receita = "informada"
    premissas = {"meta_sinistralidade_pct": meta, "vcmh_pct": vcmh, "meses_base": meses,
                 "ajustar_completude": ajustar_completude, "receita_mensal_informada":
                 receita_informada if fonte_receita == "informada" else None}
    contrato = {"id": info["id"], "nome": info["nome"], "plano": info["plano"]}
    if not janela:
        return {
            "disponivel": False, "contrato": contrato, "premissas": premissas,
            "exige_receita_informada": True,
            "motivo": ("Contrato sem receita reconhecida atribuída no período. Informe a "
                       "receita mensal do contrato para simular — o sistema não rateia receita."),
        }

    fator_compl: dict[str, float] = {}
    if ajustar_completude:
        c = completude.calcular(session)
        if c["disponivel"]:
            for m in c["meses"]:
                obs = m["despesa_liquida_observada"]
                if m["status"] == "preliminar" and obs > 0:
                    fator_compl[m["competencia"]] = m["despesa_liquida_estimada_final"] / obs

    linhas, desp_obs, desp, rec = [], 0.0, 0.0, 0.0
    for r in janela:
        k = r["competencia"].isoformat()
        d_obs = _f(r["despesa_liquida"])
        d_aj = d_obs * fator_compl.get(k, 1.0)
        receita = _f(r["receita_reconhecida"])
        desp_obs, desp, rec = desp_obs + d_obs, desp + d_aj, rec + receita
        linhas.append({
            "competencia": k, "receita_reconhecida": round(receita, 2),
            "despesa_liquida": round(d_obs, 2), "despesa_liquida_ajustada": round(d_aj, 2),
            "preliminar": k in fator_compl,
            "sinistralidade": round(d_aj / receita * 100, 2) if receita > 0 else None,
        })
    if rec <= 0:
        return {"disponivel": False, "contrato": contrato, "premissas": premissas,
                "motivo": "Receita reconhecida do contrato é zero na janela."}

    sin_atual = desp / rec * 100
    desp_proj = desp * (1 + vcmh / 100)
    receita_nec = desp_proj / (meta / 100)
    reajuste = (receita_nec / rec - 1) * 100
    cenarios = [
        {"reajuste_pct": p, "sinistralidade_projetada": round(desp_proj / (rec * (1 + p / 100)) * 100, 2),
         "atinge_meta": desp_proj / (rec * (1 + p / 100)) * 100 <= meta + 1e-9}
        for p in sorted({*CENARIOS_REAJUSTE, math.ceil(max(reajuste, 0.0) * 10) / 10})
    ]
    return {
        "disponivel": True,
        "contrato": contrato,
        "competencia": competencia.isoformat(),
        "premissas": premissas,
        "fonte_receita": fonte_receita,
        "historico": {
            "meses": len(janela),
            "receita_reconhecida": round(rec, 2),
            "despesa_liquida_observada": round(desp_obs, 2),
            "despesa_liquida_considerada": round(desp, 2),
            "ajuste_completude": round(desp - desp_obs, 2),
            "sinistralidade_pct": round(sin_atual, 2),
            "rotulo": ("HIPÓTESE (receita informada)" if fonte_receita == "informada"
                       else "FATO" if desp == desp_obs else "FATO + ESTIMATIVA (contas a chegar)"),
        },
        "projecao": {
            "despesa_liquida_projetada": round(desp_proj, 2),
            "receita_necessaria": round(receita_nec, 2),
            "reajuste_tecnico_pct": round(reajuste, 2),
            "sinistralidade_sem_reajuste": round(desp_proj / rec * 100, 2),
            "rotulo": "HIPÓTESE",
        },
        "cenarios": cenarios,
        "serie": linhas,
        "formula": ("reajuste técnico = (despesa líquida N meses × (1 + VCMH) ÷ meta) ÷ "
                    "receita reconhecida N meses − 1"),
        "limitacoes": [
            "A projeção supõe carteira e utilização estáveis; entrada/saída de vidas muda o resultado.",
            "VCMH e meta são premissas informadas por quem simula, não calculadas pelo sistema.",
            "Não considera regras contratuais (pool de risco, limites regulatórios de reajuste).",
        ],
    }
