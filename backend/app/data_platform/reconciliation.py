"""Reconciliação de carga — ORIGEM (linhas válidas do arquivo) × SILVER × GOLD.

Roda dentro da transação de publicação, depois de Silver e Gold. Resultado por verificação:

* PASS     — diferença zero;
* WARNING  — diferença diferente de zero, mas dentro da tolerância DECLARADA no mapping
             (`reconciliation.financial_tolerance_abs` / `count_tolerance_abs`);
* FAIL     — acima da tolerância → a carga NÃO é publicada (rollback).

Tolerâncias padrão = 0 (sem tolerância silenciosa). Detalhe em docs/RECONCILIATION.md.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import AggSinistralidadeCompetencia, Beneficiario, EventoAssistencial, Receita

PASS, WARNING, FAIL = "PASS", "WARNING", "FAIL"


@dataclass
class ReconCheck:
    check_id: str
    entity: str
    scope: str
    expected: Decimal
    actual: Decimal
    tolerance: Decimal

    @property
    def difference(self) -> Decimal:
        return self.actual - self.expected

    @property
    def status(self) -> str:
        d = abs(self.difference)
        if d == 0:
            return PASS
        return WARNING if d <= self.tolerance else FAIL

    def as_dict(self) -> dict:
        return {"check_id": self.check_id, "entity": self.entity, "scope": self.scope,
                "expected": str(self.expected), "actual": str(self.actual),
                "difference": str(self.difference), "tolerance": str(self.tolerance),
                "status": self.status}


def _d(v) -> Decimal:
    return Decimal(str(v or 0)).quantize(Decimal("0.01"))


def overall(checks: list[ReconCheck]) -> str:
    st = {c.status for c in checks}
    return FAIL if FAIL in st else (WARNING if WARNING in st else PASS)


def reconcile(session: Session, tenant_id: str, ingestion_run_id: int, *,
              eventos: list[dict], receitas: list[dict], beneficiarios: list[dict],
              received: dict[str, int], rejected: dict[str, int], duplicates: dict[str, int],
              fin_tol: Decimal, count_tol: int) -> list[ReconCheck]:
    ct, ft = Decimal(count_tol), Decimal(fin_tol)
    out: list[ReconCheck] = []

    # 0. contabilidade da carga: recebidas = válidas + rejeitadas + duplicadas descartadas
    for ent, n in received.items():
        validas = {"evento_assistencial": len(eventos), "receita": len(receitas),
                   "beneficiario": len(beneficiarios)}.get(ent)
        if validas is None:
            continue
        out.append(ReconCheck("contagem_recebidas", ent, "total", _d(n),
                              _d(validas + rejected.get(ent, 0) + duplicates.get(ent, 0)), ct))

    # 1. eventos: origem válida × Silver desta ingestão (registros e valores por competência)
    if eventos:
        por_mes: dict[date, list] = defaultdict(lambda: [0, Decimal(0), Decimal(0)])
        for e in eventos:
            acc = por_mes[e["competencia"]]
            acc[0] += 1
            acc[1] += Decimal(e["valor_apresentado"])
            acc[2] += Decimal(e["valor_apresentado"]) - Decimal(e["valor_glosado"]) - Decimal(e["valor_coparticipacao"])
        silver = {r[0]: r[1:] for r in session.execute(
            select(EventoAssistencial.competencia, func.count(),
                   func.sum(EventoAssistencial.valor_apresentado))
            .where(EventoAssistencial.tenant_id == tenant_id,
                   EventoAssistencial.ingestion_run_id == ingestion_run_id)
            .group_by(EventoAssistencial.competencia)).all()}
        gold = {r.competencia: r for r in session.execute(
            select(AggSinistralidadeCompetencia).where(
                AggSinistralidadeCompetencia.tenant_id == tenant_id,
                AggSinistralidadeCompetencia.competencia.in_(list(por_mes)))).scalars()}
        silver_tenant = {r[0]: r[1:] for r in session.execute(
            select(EventoAssistencial.competencia, func.count(),
                   func.sum(EventoAssistencial.valor_apresentado),
                   func.sum(EventoAssistencial.valor_apresentado - EventoAssistencial.valor_glosado
                            - EventoAssistencial.valor_coparticipacao))
            .where(EventoAssistencial.tenant_id == tenant_id,
                   EventoAssistencial.competencia.in_(list(por_mes)))
            .group_by(EventoAssistencial.competencia)).all()}
        for mes, (n, bruto, _liq) in sorted(por_mes.items()):
            esc = f"{mes:%Y-%m}"
            s_n, s_bruto = silver.get(mes, (0, 0))
            out.append(ReconCheck("eventos_registros_silver", "evento_assistencial", esc, _d(n), _d(s_n), ct))
            out.append(ReconCheck("eventos_valor_apresentado_silver", "evento_assistencial", esc,
                                  _d(bruto), _d(s_bruto), ft))
            t_n, t_bruto, t_liq = silver_tenant.get(mes, (0, 0, 0))
            g = gold.get(mes)
            out.append(ReconCheck("gold_eventos", "agg_sinistralidade_competencia", esc,
                                  _d(t_n), _d(g.eventos if g else 0), ct))
            out.append(ReconCheck("gold_despesa_bruta", "agg_sinistralidade_competencia", esc,
                                  _d(t_bruto), _d(g.despesa_bruta if g else 0), ft))
            out.append(ReconCheck("gold_despesa_liquida", "agg_sinistralidade_competencia", esc,
                                  _d(t_liq), _d(g.despesa_liquida if g else 0), ft))

    # 2. receita: origem válida × Silver × Gold por competência
    if receitas:
        por_mes_r: dict[date, Decimal] = defaultdict(Decimal)
        for r in receitas:
            por_mes_r[r["competencia"]] += Decimal(r["receita_contraprestacao"])
        silver_r = dict(session.execute(
            select(Receita.competencia, func.sum(Receita.receita_contraprestacao))
            .where(Receita.tenant_id == tenant_id, Receita.competencia.in_(list(por_mes_r)))
            .group_by(Receita.competencia)).all())
        gold_r = dict(session.execute(
            select(AggSinistralidadeCompetencia.competencia, AggSinistralidadeCompetencia.receita)
            .where(AggSinistralidadeCompetencia.tenant_id == tenant_id,
                   AggSinistralidadeCompetencia.competencia.in_(list(por_mes_r)))).all())
        for mes, total in sorted(por_mes_r.items()):
            esc = f"{mes:%Y-%m}"
            out.append(ReconCheck("receita_silver", "receita", esc, _d(total), _d(silver_r.get(mes)), ft))
            out.append(ReconCheck("gold_receita", "agg_sinistralidade_competencia", esc,
                                  _d(silver_r.get(mes)), _d(gold_r.get(mes)), ft))

    # 3. beneficiários: códigos válidos × linhas da Silver tocadas por esta ingestão
    if beneficiarios:
        n_silver = session.execute(select(func.count()).select_from(Beneficiario).where(
            Beneficiario.tenant_id == tenant_id,
            Beneficiario.ingestion_run_id == ingestion_run_id)).scalar_one()
        out.append(ReconCheck("beneficiarios_silver", "beneficiario", "total",
                              _d(len(beneficiarios)), _d(n_silver), ct))
    return out


def financeiro(session: Session, meses: list[date]) -> list[ReconCheck]:
    """Consolidação V1 — verificações de `analytics.reconciliacao_financeira` nos meses da
    carga, convertidas para o gate: FAIL reprova a publicação; WARNING (diferença explicada,
    ex.: receita não detalhada até plano) segue registrada; INFO não entra no gate."""
    from app.analytics import reconciliacao_financeira as rf

    if not meses:
        return []
    out: list[ReconCheck] = []
    for v in rf.verificar(session, meses):
        if v.status == rf.INFO:
            continue
        esperado, obtido = _d(v.esperado), _d(v.obtido)
        tol = abs(obtido - esperado) if v.status == rf.WARNING else Decimal(0)
        if v.status == rf.FAIL and esperado == obtido:  # FAIL lógico sem diferença numérica
            obtido += Decimal("0.01")
        out.append(ReconCheck(v.check_id, "financeiro", v.escopo, esperado, obtido, tol))
    return out

