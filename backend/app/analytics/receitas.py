"""Composição da receita (Consolidação do canônico V1) — transparência das fontes.

Mostra, para uma competência, CADA fato no seu próprio grão — sem somar uns aos outros:

* receita usada pela operadora (fonte única: reconhecida OU legada) e seu status;
* receita reconhecida por nível de atribuição (contrato × plano · contrato · empresa sem
  detalhamento · não atribuída) — só a atribuída a contrato entra na sinistralidade contratual;
* receita legada por plano (quando existe);
* faturado (documentos pela competência de FATURAMENTO) e recebido (pela competência de
  RECEBIMENTO) — informativos: não são receita reconhecida.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.analytics import reconciliacao_financeira as rf
from app.db.tenant_scope import tenant_of as _t
from app.repositories import analytics_repo as repo

ROTULO_NIVEL = {
    "contrato_plano": "Atribuída a contrato × plano",
    "contrato": "Atribuída a contrato (sem plano)",
    "empresa": "Cobrança consolidada da empresa, sem detalhamento",
    "nao_atribuida": "Sem correspondência cadastral",
}


def _f(x) -> float:
    return float(x) if x is not None else 0.0


def composicao(session: Session, competencia: date) -> dict:
    s = repo.sinistralidade_mes(session, competencia)
    if s is None:
        raise ValueError("competência sem dados")
    t = _t(session)
    niveis = (
        session.execute(
            text(
                "SELECT nivel_atribuicao, COUNT(*) AS n, SUM(receita_contraprestacao) AS v "
                "FROM receitas_reconhecidas WHERE tenant_id = :t AND competencia = :c AND NOT estimada "
                "GROUP BY nivel_atribuicao"
            ),
            {"t": t, "c": competencia},
        )
        .mappings()
        .all()
    )
    estimada = _f(
        session.execute(
            text(
                "SELECT SUM(receita_contraprestacao) FROM receitas_reconhecidas "
                "WHERE tenant_id = :t AND competencia = :c AND estimada"
            ),
            {"t": t, "c": competencia},
        ).scalar()
    )
    faturado = (
        session.execute(
            text(
                "SELECT unidade_cobranca, COUNT(*) AS n, SUM(valor_bruto) AS bruto, "
                "SUM(valor_desconto) AS desconto, SUM(valor_acrescimo) AS acrescimo, SUM(valor_total) AS total "
                "FROM faturas WHERE tenant_id = :t AND competencia_faturamento = :c "
                "AND COALESCE(status, 'emitida') = 'emitida' GROUP BY unidade_cobranca"
            ),
            {"t": t, "c": competencia},
        )
        .mappings()
        .all()
    )
    recebido = _f(
        session.execute(
            text(
                "SELECT SUM(valor_recebido) FROM recebimentos WHERE tenant_id = :t AND competencia_recebimento = :c"
            ),
            {"t": t, "c": competencia},
        ).scalar()
    )
    total_rec = sum(_f(n["v"]) for n in niveis)
    return {
        "competencia": competencia.isoformat(),
        "receita_operadora": {
            "valor": round(_f(s["receita"]), 2),
            "fonte": s["fonte_receita"],
            "status": s["receita_status"],
            "nota": "fonte única da competência: reconhecida, se existir; senão a legada por plano. "
            "Nunca a soma das duas.",
        },
        "receita_reconhecida": {
            "total": round(total_rec, 2),
            "niveis": [
                {
                    "nivel": n["nivel_atribuicao"],
                    "rotulo": ROTULO_NIVEL.get(n["nivel_atribuicao"]),
                    "registros": n["n"],
                    "valor": round(_f(n["v"]), 2),
                    "participacao": round(_f(n["v"]) / total_rec, 4) if total_rec else None,
                    "entra_na_sinistralidade_contratual": n["nivel_atribuicao"]
                    in ("contrato_plano", "contrato"),
                }
                for n in sorted(
                    niveis, key=lambda x: list(ROTULO_NIVEL).index(x["nivel_atribuicao"])
                )
            ],
            "cobertura_contratual": s["cobertura_receita_contratual"],
            "estimada_ignorada": round(estimada, 2),
        },
        "receita_legada_plano": round(_f(s["receita_legada"]), 2)
        if s["receita_legada"] is not None
        else None,
        "faturamento": {
            "competencia_base": "faturamento",
            "por_unidade": [
                {
                    "unidade_cobranca": f["unidade_cobranca"],
                    "documentos": f["n"],
                    "bruto": round(_f(f["bruto"]), 2),
                    "descontos": round(_f(f["desconto"]), 2),
                    "acrescimos": round(_f(f["acrescimo"]), 2),
                    "total": round(_f(f["total"]), 2),
                }
                for f in faturado
            ],
            "total": round(sum(_f(f["total"]) for f in faturado), 2),
        },
        "recebimento": {"competencia_base": "recebimento", "total": round(recebido, 2)},
        "avisos": [
            "Faturado, recebido e reconhecido são fatos diferentes, cada um na sua competência — "
            "não são somados nem tratados como equivalentes.",
            *(
                ["Receita da competência DIVERGENTE entre fontes — sinistralidade não publicada."]
                if s["receita_status"] == "divergente"
                else []
            ),
        ],
    }


def reconciliacao(session: Session, competencia: date | None = None) -> dict:
    return {
        "competencia": competencia.isoformat() if competencia else None,
        **rf.resumo(rf.verificar(session, [competencia] if competencia else None)),
    }
