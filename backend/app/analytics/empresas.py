"""Empresas (Consolidação do canônico V1) — agrupamento de contratos SEM dupla contagem.

Um contrato pertence a no máximo uma empresa CONTRATANTE em cada competência (regra da
carga). A empresa soma os contratos associados NAQUELA competência (associação histórica)
e a sua própria receita consolidada sem detalhamento — que nunca é distribuída entre os
contratos. Sinistralidade da empresa só quando toda a receita dela está identificada:
receita dos contratos (atribuída) + receita consolidada da empresa, e a competência não
tem divergência de receita.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.analytics.contratos import sinistralidade_contratual
from app.db.tenant_scope import tenant_of as _t

_CONTRATOS_DA_EMPRESA_NA_COMPETENCIA = """
    SELECT ec.id_empresa, ac.*, ct.nome AS contrato_nome, ct.tipo AS contrato_tipo
    FROM empresas_contratos ec
    JOIN agg_contrato_competencia ac
      ON ac.tenant_id = ec.tenant_id AND ac.id_contrato = ec.id_contrato AND ac.competencia = :c
    JOIN contratos ct ON ct.id = ec.id_contrato
    WHERE ec.tenant_id = :t AND ec.papel = 'contratante'
      AND ec.vigencia_inicio <= :c AND (ec.vigencia_fim IS NULL OR ec.vigencia_fim > :c)
"""


def _f(x) -> float:
    return float(x) if x is not None else 0.0


def _receita_empresa(session: Session, competencia: date) -> dict[int, float]:
    rows = session.execute(
        text(
            "SELECT id_empresa, SUM(receita_contraprestacao) AS v FROM receitas_reconhecidas "
            "WHERE tenant_id = :t AND competencia = :c AND NOT estimada AND nivel_atribuicao = 'empresa' "
            "GROUP BY id_empresa"
        ),
        {"t": _t(session), "c": competencia},
    ).all()
    return {int(r[0]): float(r[1]) for r in rows}


def _bloco(
    emp: dict, contratos: list[dict], receita_emp: float, status_receita: str | None
) -> dict:
    vidas = sum(int(c["vidas"] or 0) for c in contratos)
    desp = sum(_f(c["despesa_liquida"]) for c in contratos)
    rec_ctr = sum(_f(c["receita_reconhecida"]) for c in contratos)
    receita = rec_ctr + receita_emp
    sem_receita = [c["contrato_nome"] for c in contratos if not c["receita_reconhecida"]]
    motivo = None
    if receita <= 0:
        motivo = "sem receita reconhecida da empresa nem dos contratos"
    elif status_receita == "divergente":
        motivo = "receita da competência divergente entre fontes"
    elif sem_receita and receita_emp <= 0:
        motivo = "há contratos da empresa sem receita identificada"
    return {
        "id": emp["id"],
        "codigo": emp["codigo"],
        "nome": emp["nome"],
        "tipo": emp["tipo"],
        "n_contratos": len(contratos),
        "vidas": vidas,
        "despesa_liquida": round(desp, 2),
        "receita_contratos": round(rec_ctr, 2),
        "receita_consolidada_empresa": round(receita_emp, 2),
        "receita_total": round(receita, 2),
        "sinistralidade_liquida": round(desp / receita * 100, 2) if motivo is None else None,
        "motivo_sem_sinistralidade": motivo,
    }


def listar(session: Session, competencia: date) -> dict:
    t = _t(session)
    emps = {
        r["id"]: dict(r)
        for r in session.execute(
            text("SELECT id, codigo, nome, tipo FROM empresas WHERE tenant_id = :t ORDER BY nome"),
            {"t": t},
        )
        .mappings()
        .all()
    }
    rows = [
        dict(r)
        for r in session.execute(
            text(_CONTRATOS_DA_EMPRESA_NA_COMPETENCIA), {"t": t, "c": competencia}
        )
        .mappings()
        .all()
    ]
    status = session.execute(
        text(
            "SELECT receita_status FROM agg_sinistralidade_competencia WHERE tenant_id = :t AND competencia = :c"
        ),
        {"t": t, "c": competencia},
    ).scalar()
    rec_emp = _receita_empresa(session, competencia)
    itens = [
        _bloco(e, [r for r in rows if r["id_empresa"] == eid], rec_emp.get(eid, 0.0), status)
        for eid, e in emps.items()
    ]
    return {
        "competencia": competencia.isoformat(),
        "total": len(itens),
        "itens": sorted(itens, key=lambda x: -x["despesa_liquida"]),
        "metodologia": (
            "Contratos da empresa = associação 'contratante' vigente na competência "
            "(um contrato, uma empresa). Receita da empresa = receita atribuída aos "
            "contratos + cobrança consolidada da empresa sem detalhamento (nunca "
            "rateada entre contratos)."
        ),
    }


def detalhe(session: Session, id_empresa: int, competencia: date) -> dict:
    t = _t(session)
    emp = (
        session.execute(
            text(
                "SELECT id, codigo, nome, tipo, status FROM empresas "
                "WHERE tenant_id = :t AND id = :e"
            ),
            {"t": t, "e": id_empresa},
        )
        .mappings()
        .first()
    )
    if emp is None:
        raise ValueError("empresa não encontrada")
    rows = [
        dict(r)
        for r in session.execute(
            text(_CONTRATOS_DA_EMPRESA_NA_COMPETENCIA + " AND ec.id_empresa = :e"),
            {"t": t, "c": competencia, "e": id_empresa},
        )
        .mappings()
        .all()
    ]
    status = session.execute(
        text(
            "SELECT receita_status FROM agg_sinistralidade_competencia WHERE tenant_id = :t AND competencia = :c"
        ),
        {"t": t, "c": competencia},
    ).scalar()
    bloco = _bloco(
        dict(emp), rows, _receita_empresa(session, competencia).get(id_empresa, 0.0), status
    )
    historico = [
        dict(r)
        for r in session.execute(
            text(
                "SELECT ct.id, ct.nome, ec.papel, ec.vigencia_inicio, ec.vigencia_fim FROM empresas_contratos ec "
                "JOIN contratos ct ON ct.id = ec.id_contrato WHERE ec.tenant_id = :t AND ec.id_empresa = :e "
                "ORDER BY ec.vigencia_inicio, ct.nome"
            ),
            {"t": t, "e": id_empresa},
        )
        .mappings()
        .all()
    ]
    return {
        "empresa": {**dict(emp)},
        "competencia": competencia.isoformat(),
        **bloco,
        "contratos": sorted(
            [
                {
                    "id_contrato": r["id_contrato"],
                    "nome": r["contrato_nome"],
                    "tipo": r["contrato_tipo"],
                    "vidas": r["vidas"],
                    "despesa_liquida": round(_f(r["despesa_liquida"]), 2),
                    "custo_pmpm": round(_f(r["custo_pmpm"]), 2),
                    "participacao_despesa": round(
                        _f(r["despesa_liquida"]) / bloco["despesa_liquida"], 4
                    )
                    if bloco["despesa_liquida"]
                    else None,
                    "sinistralidade_contratual": sinistralidade_contratual(r),
                }
                for r in rows
            ],
            key=lambda x: -x["despesa_liquida"],
        ),
        "associacoes": [
            {
                "id_contrato": h["id"],
                "contrato": h["nome"],
                "papel": h["papel"],
                "vigencia_inicio": h["vigencia_inicio"].isoformat(),
                "vigencia_fim": h["vigencia_fim"].isoformat() if h["vigencia_fim"] else None,
            }
            for h in historico
        ],
    }
