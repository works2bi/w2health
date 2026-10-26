"""Capability Readiness — prontidão de DADOS por capability.

    disponível = feature habilitada comercialmente (plano/override/global)  E  dados prontos

A regra mora SÓ no backend (`app/security/deps.get_tenant_context` usa este módulo).
O frontend recebe o resultado pronto em `/auth/me` (`capabilities`).

Requisitos por capability = docs/CAPABILITY_READINESS.md (fonte de verdade); aqui só os
OBRIGATÓRIOS bloqueiam. Desejáveis ausentes geram PARTIAL (disponível, com motivo).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import exists, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models import (
    AggSinistralidadeCompetencia,
    Beneficiario,
    CapabilityReadiness,
    Contrato,
    EmpresaContrato,
    EventoAssistencial,
    Prestador,
    Procedimento,
    Receita,
    ReceitaReconhecida,
)

READY, PARTIAL, NOT_READY = "READY", "PARTIAL", "NOT_READY"

#: rótulos legíveis dos sinais de dados
SIGNAL_LABEL = {
    "eventos": "eventos assistenciais",
    "receita": "receita (legada por plano ou reconhecida)",
    "receita_contratual": "receita reconhecida atribuída a contratos",
    "empresas": "empresas associadas a contratos",
    "beneficiarios": "beneficiários",
    "contratos": "contratos com beneficiários vinculados",
    "prestadores": "prestadores",
    "gold": "camada analítica (Gold) construída",
    "glosa": "valor glosado por evento",
    "coparticipacao": "coparticipação por evento",
    "saida_carteira": "datas de saída da carteira",
    "perfil_utilizacao": "perfil de utilização dos procedimentos",
}

#: capability → (obrigatórios, desejáveis)
REQUIREMENTS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "executive_overview": (("eventos", "receita", "gold"), ()),
    "loss_ratio_intelligence": (("eventos", "receita", "gold"), ()),
    "financial_composition": (("eventos", "receita", "gold"), ("glosa", "coparticipacao")),
    "advanced_explanations": (("eventos", "beneficiarios", "gold"), ("saida_carteira", "perfil_utilizacao")),
    # Consolidação V1: sem receita contratual o módulo funciona (vidas, despesa,
    # concentração) — só a sinistralidade de contrato fica indisponível → PARTIAL
    "contract_intelligence": (("eventos", "beneficiarios", "contratos", "gold"), ("receita_contratual",)),
    "provider_intelligence": (("eventos", "prestadores", "gold"), ()),
    "beneficiary_intelligence": (("eventos", "beneficiarios", "gold"), ()),
    "insights": (("eventos", "receita", "gold"), ()),
    "alerts": (("eventos", "gold"), ()),
    "custom_branding": ((), ()),  # configuração, não depende de dado
}


@dataclass(frozen=True)
class Readiness:
    feature_key: str
    status: str
    reason: str
    checks: dict

    @property
    def ready(self) -> bool:
        return self.status in (READY, PARTIAL)


def _exists(session: Session, stmt) -> bool:
    return bool(session.execute(select(exists(stmt))).scalar())


def signals(session: Session, tenant_id: str) -> dict[str, bool]:
    ev = select(EventoAssistencial.id).where(EventoAssistencial.tenant_id == tenant_id)
    return {
        "eventos": _exists(session, ev),
        "receita": _exists(session, select(Receita.id).where(
            Receita.tenant_id == tenant_id, Receita.receita_contraprestacao > 0))
        or _exists(session, select(ReceitaReconhecida.id).where(
            ReceitaReconhecida.tenant_id == tenant_id, ~ReceitaReconhecida.estimada,
            ReceitaReconhecida.receita_contraprestacao > 0)),
        "receita_contratual": _exists(session, select(ReceitaReconhecida.id).where(
            ReceitaReconhecida.tenant_id == tenant_id, ~ReceitaReconhecida.estimada,
            ReceitaReconhecida.nivel_atribuicao.in_(("contrato_plano", "contrato")))),
        "empresas": _exists(session, select(EmpresaContrato.id).where(
            EmpresaContrato.tenant_id == tenant_id)),
        "beneficiarios": _exists(session, select(Beneficiario.id).where(Beneficiario.tenant_id == tenant_id)),
        "contratos": _exists(session, select(Contrato.id).where(Contrato.tenant_id == tenant_id))
                     and _exists(session, select(Beneficiario.id).where(
                         Beneficiario.tenant_id == tenant_id, Beneficiario.id_contrato.is_not(None))),
        "prestadores": _exists(session, select(Prestador.id).where(Prestador.tenant_id == tenant_id)),
        "gold": _exists(session, select(AggSinistralidadeCompetencia.competencia).where(
            AggSinistralidadeCompetencia.tenant_id == tenant_id,
            AggSinistralidadeCompetencia.eventos > 0)),
        "glosa": _exists(session, ev.where(EventoAssistencial.valor_glosado > 0)),
        "coparticipacao": _exists(session, ev.where(EventoAssistencial.valor_coparticipacao > 0)),
        "saida_carteira": _exists(session, select(Beneficiario.id).where(
            Beneficiario.tenant_id == tenant_id, Beneficiario.data_saida.is_not(None))),
        "perfil_utilizacao": _exists(session, select(Procedimento.id).where(
            Procedimento.tenant_id == tenant_id, Procedimento.perfil_utilizacao.is_not(None))),
    }


def evaluate(sig: dict[str, bool]) -> dict[str, Readiness]:
    out = {}
    for key, (obrig, desej) in REQUIREMENTS.items():
        faltam = [s for s in obrig if not sig.get(s)]
        falt_d = [s for s in desej if not sig.get(s)]
        if faltam:
            st = NOT_READY
            motivo = "Dados necessários ainda não disponíveis: " + ", ".join(SIGNAL_LABEL[s] for s in faltam)
        elif falt_d:
            st = PARTIAL
            motivo = "Disponível com limitação — sem " + ", ".join(SIGNAL_LABEL[s] for s in falt_d)
        else:
            st, motivo = READY, ""
        out[key] = Readiness(key, st, motivo, {s: bool(sig.get(s)) for s in obrig + desej})
    return out


def refresh(session: Session, tenant_id: str) -> dict[str, Readiness]:
    """Recalcula e persiste (chamado ao final de pipeline/seed). Sessão amarrada ao tenant."""
    res = evaluate(signals(session, tenant_id))
    agora = datetime.now(UTC)
    for r in res.values():
        stmt = pg_insert(CapabilityReadiness.__table__).values(
            tenant_id=tenant_id, feature_key=r.feature_key, status=r.status, reason=r.reason,
            checks=r.checks, computed_at=agora)
        session.execute(stmt.on_conflict_do_update(
            constraint="uq_capready_tenant_feature",
            set_={"status": stmt.excluded.status, "reason": stmt.excluded.reason,
                  "checks": stmt.excluded.checks, "computed_at": stmt.excluded.computed_at}))
    session.flush()
    return res


def current(session: Session, tenant_id: str) -> dict[str, Readiness]:
    """Prontidão persistida; se o tenant ainda não tem registro (ex.: dado anterior à
    Fase 2), calcula e persiste agora."""
    rows = session.execute(select(CapabilityReadiness).where(
        CapabilityReadiness.tenant_id == tenant_id)).scalars().all()
    out = {r.feature_key: Readiness(r.feature_key, r.status, r.reason, r.checks or {}) for r in rows}
    if not rows or any(key not in out for key in REQUIREMENTS):
        out = refresh(session, tenant_id)
        session.commit()  # persiste já (requisições GET não fazem commit)
    return out
