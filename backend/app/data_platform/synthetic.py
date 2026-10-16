"""CAMINHO A — gerador sintético registrado como uma FONTE como outra qualquer.

    Synthetic Generator → Canônico/Silver → Gold → Serving → API

O gerador escreve direto no modelo canônico (ele é um produtor canônico, não precisa de
mapping), mas deixa a MESMA linhagem de uma fonte externa: `source_connections`
(tipo SYNTHETIC), `ingestion_runs`, `pipeline_runs`, linhagem nas linhas da Silver,
readiness e onboarding. O motor analítico não distingue os dois caminhos.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.data_platform import onboarding, readiness
from app.models import (
    Contrato,
    Diagnostico,
    Especialidade,
    IngestionRun,
    PipelineRun,
    Plano,
    PlanoContrato,
    Prestador,
    Procedimento,
    Regiao,
    SourceConnection,
)

SOURCE_SYSTEM = "synthetic_generator"
SOURCE_NAME = "Gerador sintético W2Health"


def start(session: Session, tenant_id: str, *, triggered_by: str = "synthetic_generator") -> dict:
    conn = session.execute(select(SourceConnection).where(
        SourceConnection.tenant_id == tenant_id, SourceConnection.name == SOURCE_NAME)).scalars().first()
    if conn is None:
        conn = SourceConnection(tenant_id=tenant_id, name=SOURCE_NAME, source_type="SYNTHETIC",
                                source_system=SOURCE_SYSTEM,
                                configuration={"description": "massa sintética gerada internamente"})
        session.add(conn)
        session.flush()
    run = IngestionRun(tenant_id=tenant_id, source_connection_id=conn.id, source_system=SOURCE_SYSTEM,
                       status="RUNNING", triggered_by=triggered_by, mapping_ref="canonico-direto",
                       error_summary={})
    session.add(run)
    session.flush()
    conn.last_run_at = datetime.now(UTC)
    lineage = {"source_system": SOURCE_SYSTEM, "source_connection_id": conn.id, "ingestion_run_id": run.id}
    session.info["w2h_lineage"] = lineage
    return lineage


def finish(session: Session, tenant_id: str, lineage: dict, *, counts: dict, eventos: int,
           competencia_inicio, competencia_fim) -> None:
    session.info.pop("w2h_lineage", None)
    # catálogos inseridos via ORM recebem a linhagem aqui (os fatos já vieram no COPY)
    for model in (Regiao, Plano, Contrato, PlanoContrato, Especialidade, Procedimento, Prestador,
                  Diagnostico):
        session.execute(update(model).where(model.tenant_id == tenant_id, model.source_system.is_(None))
                        .values(**lineage))
    agora = datetime.now(UTC)
    run = session.get(IngestionRun, lineage["ingestion_run_id"])
    run.status, run.stage, run.finished_at = "SUCCESS", "AVAILABLE", agora
    run.records_received = run.records_valid = run.records_inserted = eventos
    run.competencia_inicio, run.competencia_fim = competencia_inicio, competencia_fim
    session.add(PipelineRun(tenant_id=tenant_id, ingestion_run_id=run.id,
                            source_connection_id=lineage["source_connection_id"], layer="serving",
                            status="SUCCESS", triggered_by=run.triggered_by, finished_at=agora,
                            steps=[{"stage": "canonical_direct", "status": "OK"},
                                   {"stage": "gold", "status": "OK", "linhas": counts}]))
    conn = session.get(SourceConnection, lineage["source_connection_id"])
    conn.last_success_at = agora
    prontidao = readiness.refresh(session, tenant_id)
    for estado in ("SOURCE_REGISTERED", "CONNECTION_VALIDATED", "RAW_LOADED", "MAPPING_VALIDATED",
                   "DATA_QUALITY_VALIDATED", "SILVER_READY", "GOLD_READY", "RECONCILED"):
        onboarding.advance(session, tenant_id, estado, by=SOURCE_SYSTEM)
    if any(r.ready for r in prontidao.values()):
        onboarding.advance(session, tenant_id, "CAPABILITIES_READY", by=SOURCE_SYSTEM)
    session.flush()
