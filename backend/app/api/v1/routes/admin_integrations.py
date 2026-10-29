"""Admin Works2Data — Integrações / Data Sources (somente SUPER_ADMIN).

O tenant-alvo vem do caminho (área de plataforma) e cada leitura/escrita do data plane é
feita numa sessão amarrada a ESSE tenant (RLS). A execução de carga usa o papel de
pipeline (`app/data_platform/runner.py`). Segredos nunca são exibidos.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, File, Form, Query, Request, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.data_platform import connections as conn_svc
from app.data_platform import lineage, onboarding, padroes_ans, readiness
from app.data_platform.context import PipelineError
from app.data_platform.mapping import MappingError
from app.data_platform.runner import FileLimits, FileValidationError, receive_package
from app.data_platform.storage import StorageUnavailable
from app.db.pipeline import PipelineDatabaseNotConfigured
from app.db.session import get_db
from app.db.tenant_scope import bind_tenant
from app.models import (
    DataQualityResult,
    IngestionRun,
    PipelineJob,
    PipelineRun,
    ReconciliationResult,
    SourceConnection,
    Tenant,
    WorkerHeartbeat,
)
from app.saas import audit
from app.saas.features import resolve_all
from app.security.deps import Principal, require_platform
from app.security.errors import ApiError
from app.security.rbac import Perm
from app.worker.admin import admin_cancel, admin_requeue

router = APIRouter(prefix="/admin", tags=["Administração Works2Data — Integrações"])
_INTEG = require_platform(Perm.PLATFORM_TENANTS_MANAGE)


class SourceIn(BaseModel):
    name: str = Field(min_length=3, max_length=120)
    source_type: str
    source_system: str = Field(min_length=2, max_length=60)
    configuration: dict = Field(default_factory=dict)
    secret_reference: str | None = Field(default=None, max_length=120)


class SourcePatchIn(BaseModel):
    status: str


class OnboardingIn(BaseModel):
    state: str
    note: str = Field(min_length=3, max_length=200)


@contextmanager
def tenant_session(db: Session, tenant_id: str):
    if db.get(Tenant, tenant_id) is None:
        raise ApiError("not_found", "Tenant não encontrado.")
    s = Session(bind=db.get_bind(), autoflush=False, expire_on_commit=False)
    try:
        bind_tenant(s, tenant_id)
        yield s
    finally:
        s.close()


def _iso(d):
    return d.isoformat() if d else None


def run_dict(r: IngestionRun) -> dict:
    return {
        "id": r.id, "source_connection_id": r.source_connection_id, "status": r.status, "stage": r.stage,
        "started_at": _iso(r.started_at), "finished_at": _iso(r.finished_at),
        "records_received": r.records_received, "records_valid": r.records_valid,
        "records_rejected": r.records_rejected, "warnings": r.warnings_count, "errors": r.errors_count,
        "competencia_inicio": _iso(r.competencia_inicio), "competencia_fim": _iso(r.competencia_fim),
        "checksum": r.checksum, "mapping_ref": r.mapping_ref, "triggered_by": r.triggered_by,
        "duplicate_of": r.duplicate_of, "error_summary": r.error_summary or {},
        "correlation_id": r.correlation_id,
    }


def _recon_status(s: Session, run_id: int) -> str | None:
    sts = set(s.execute(select(ReconciliationResult.status).where(
        ReconciliationResult.ingestion_run_id == run_id)).scalars())
    if not sts:
        return None
    return "FAIL" if "FAIL" in sts else ("WARNING" if "WARNING" in sts else "PASS")


# ============================================================================ visão geral
@router.get("/integrations", summary="Fontes de dados de todos os tenants (visão operacional)")
def overview(_p: Principal = Depends(_INTEG), db: Session = Depends(get_db)) -> dict:
    itens = []
    for t in db.execute(select(Tenant).order_by(Tenant.name)).scalars():
        with tenant_session(db, t.id) as s:
            for c in conn_svc.list_for_tenant(s, t.id):
                ultimo = s.execute(select(IngestionRun).where(IngestionRun.source_connection_id == c.id)
                                   .order_by(desc(IngestionRun.id))).scalars().first()
                # última carga EFETIVA publicada (reenvio idêntico não conta como carga)
                publicada = s.execute(select(IngestionRun).where(
                    IngestionRun.source_connection_id == c.id, IngestionRun.stage == "AVAILABLE",
                    IngestionRun.duplicate_of.is_(None)).order_by(desc(IngestionRun.id))).scalars().first()
                itens.append({"tenant_id": t.id, "tenant_name": t.name, **conn_svc.as_dict(c),
                              "last_run": run_dict(ultimo) if ultimo else None,
                              "last_published": run_dict(publicada) if publicada else None,
                              "last_published_reconciliation": _recon_status(s, publicada.id) if publicada else None})
            ob = onboarding.get(s, t.id)
            s.commit()
        for i in itens:
            if i["tenant_id"] == t.id:
                i["onboarding_state"] = ob.state
    return {"itens": itens}


# ============================================================================ fontes
@router.get("/tenants/{tenant_id}/sources")
def list_sources(tenant_id: str, _p: Principal = Depends(_INTEG), db: Session = Depends(get_db)) -> dict:
    with tenant_session(db, tenant_id) as s:
        return {"itens": [conn_svc.as_dict(c) for c in conn_svc.list_for_tenant(s, tenant_id)]}


@router.post("/tenants/{tenant_id}/sources", status_code=201)
def create_source(tenant_id: str, payload: SourceIn, p: Principal = Depends(_INTEG),
                  db: Session = Depends(get_db)) -> dict:
    try:
        cfg = conn_svc.validate_payload(payload.source_type, payload.name, payload.source_system,
                                        payload.configuration, payload.secret_reference)
    except (conn_svc.ConnectionError_, MappingError, ValueError) as e:
        raise ApiError("invalid_request", str(e)) from e
    with tenant_session(db, tenant_id) as s:
        if s.execute(select(SourceConnection).where(SourceConnection.tenant_id == tenant_id,
                                                    SourceConnection.name == payload.name)).first():
            raise ApiError("conflict", "Já existe uma fonte com este nome no tenant.")
        c = SourceConnection(tenant_id=tenant_id, name=payload.name, source_type=payload.source_type,
                             source_system=payload.source_system, configuration=cfg,
                             secret_reference=payload.secret_reference)
        s.add(c)
        s.flush()
        onboarding.advance(s, tenant_id, "SOURCE_REGISTERED", by=p.email)
        audit.add(s, "integration.source_created", actor=p.actor(), tenant_id=tenant_id,
                  entity_type="source_connection", entity_id=c.id,
                  details={"tipo": c.source_type, "source_system": c.source_system,
                           "mapping": cfg.get("mapping_id"), "com_segredo": c.secret_reference is not None})
        s.commit()
        return conn_svc.as_dict(c)


@router.patch("/tenants/{tenant_id}/sources/{source_id}")
def patch_source(tenant_id: str, source_id: int, payload: SourcePatchIn, p: Principal = Depends(_INTEG),
                 db: Session = Depends(get_db)) -> dict:
    if payload.status not in ("ACTIVE", "DISABLED"):
        raise ApiError("invalid_request", "status deve ser ACTIVE ou DISABLED")
    with tenant_session(db, tenant_id) as s:
        c = s.get(SourceConnection, source_id)
        if c is None:
            raise ApiError("not_found", "Fonte não encontrada.")
        de = c.status
        c.status = payload.status
        audit.add(s, "integration.source_status", actor=p.actor(), tenant_id=tenant_id,
                  entity_type="source_connection", entity_id=c.id, details={"de": de, "para": c.status})
        s.commit()
        return conn_svc.as_dict(c)


@router.post("/tenants/{tenant_id}/sources/{source_id}/validate", summary="Valida conectividade da fonte")
def validate_source(tenant_id: str, source_id: int, p: Principal = Depends(_INTEG),
                    db: Session = Depends(get_db)) -> dict:
    with tenant_session(db, tenant_id) as s:
        c = s.get(SourceConnection, source_id)
        if c is None:
            raise ApiError("not_found", "Fonte não encontrada.")
        try:
            r = conn_svc.validate_connectivity(c)
        except (MappingError, ValueError) as e:
            r = {"ok": False, "detail": str(e)[:200]}
        if r["ok"]:
            onboarding.advance(s, tenant_id, "CONNECTION_VALIDATED", by=p.email)
        audit.add(s, "integration.source_validated", actor=p.actor(), tenant_id=tenant_id,
                  entity_type="source_connection", entity_id=c.id, outcome="success" if r["ok"] else "failure")
        s.commit()
        return r


# ============================================================================ upload controlado
#: tipos MIME aceitos para "<entidade>.csv" (navegadores/SOs variam; o conteúdo é
#: revalidado byte a byte — o MIME é só uma barreira a mais contra arquivo trocado)
_CSV_MIME = {"text/csv", "application/csv", "text/plain", "application/vnd.ms-excel",
             "application/octet-stream", "text/x-csv", ""}
_upload_limiter = None


def _limitador_upload():
    global _upload_limiter
    if _upload_limiter is None:
        from app.security.rate_limit import build_limiter

        cfg = get_settings()
        _upload_limiter = build_limiter(cfg.rate_limit_backend, cfg.upload_rate_limit_per_hour, 3600)
    return _upload_limiter


@router.post("/tenants/{tenant_id}/sources/{source_id}/uploads", status_code=202,
             summary="Importação controlada (fonte FILE): recebe, grava o RAW e ENFILEIRA — "
                     "mapping → DQ → Silver → Gold → reconciliação rodam no worker")
async def upload(tenant_id: str, source_id: int, request: Request, response: Response,
                 files: list[UploadFile] = File(...),
                 p: Principal = Depends(_INTEG), db: Session = Depends(get_db)) -> dict:
    cfg = get_settings()
    limite = cfg.upload_max_file_mb * 1024 * 1024
    if not _limitador_upload().hit(f"upload:user:{p.user_id}"):
        raise ApiError("rate_limited", "Limite de cargas por hora atingido. Tente mais tarde.")
    if len(files) > cfg.upload_max_files:
        raise ApiError("invalid_request", f"no máximo {cfg.upload_max_files} arquivos por carga")
    recebidos: dict[str, bytes] = {}
    for f in files:
        if (f.content_type or "").split(";")[0].strip().lower() not in _CSV_MIME:
            raise ApiError("invalid_request", "tipo de arquivo não aceito (envie CSV)")
        data = await f.read(limite + 1)
        if len(data) > limite:
            raise ApiError("invalid_request", f"arquivo maior que {cfg.upload_max_file_mb} MB")
        recebidos[(f.filename or "")[:200]] = data
    if db.get(Tenant, tenant_id) is None:
        raise ApiError("not_found", "Tenant não encontrado.")
    try:
        r = await run_in_threadpool(receive_package, tenant_id=tenant_id, source_connection_id=source_id,
                                    files=recebidos, triggered_by=p.email, max_bytes=limite,
                                    max_files=cfg.upload_max_files, limits=FileLimits.from_settings())
    except PipelineDatabaseNotConfigured as e:
        raise ApiError("service_unavailable", "Execução de pipeline não configurada neste ambiente.") from e
    except StorageUnavailable as e:
        raise ApiError("service_unavailable", "Armazenamento indisponível — tente novamente.") from e
    except (PipelineError, MappingError, FileValidationError) as e:
        raise ApiError("invalid_request", str(e)[:300]) from e
    # 202 = aceito e enfileirado; 200 = desfecho imediato (reenvio idêntico ou arquivo recusado)
    if r.status != "QUEUED":
        response.status_code = 200
    return r.as_dict()


_XML_MIME = {"text/xml", "application/xml", "application/octet-stream", ""}


async def _ler_xmls(arquivos: list[UploadFile], limite: int) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    for f in arquivos:
        if (f.content_type or "").split(";")[0].strip().lower() not in _XML_MIME:
            raise ApiError("invalid_request", f"{f.filename}: envie XML")
        data = await f.read(limite + 1)
        if len(data) > limite:
            raise ApiError("invalid_request", f"{f.filename}: arquivo maior que o limite")
        out[(f.filename or f"arquivo{len(out)}.xml")[:200]] = data
    return out


@router.post("/tenants/{tenant_id}/sources/{source_id}/padroes-ans",
             summary="Converte TISS (guias) + SIB (beneficiários) no pacote canônico; com "
                     "processar=true, enfileira a carga pela fonte (mapping ans_padroes)")
async def padroes_ans_converter(
    tenant_id: str, source_id: int, response: Response,
    tiss: list[UploadFile] = File(...), sib: list[UploadFile] = File(...),
    processar: bool = Form(False),
    uf_padrao: str | None = Form(None, min_length=2, max_length=2),
    p: Principal = Depends(_INTEG), db: Session = Depends(get_db),
) -> dict:
    cfg = get_settings()
    limite = cfg.upload_max_file_mb * 1024 * 1024
    with tenant_session(db, tenant_id) as s:  # fonte é data plane (RLS do tenant do caminho)
        fonte = s.get(SourceConnection, source_id)
        config = dict(fonte.configuration or {}) if fonte is not None else None
    if config is None:
        raise ApiError("not_found", "Fonte não encontrada.")
    if config.get("mapping_id") != padroes_ans.MAPPING_ID:
        raise ApiError("invalid_request", "A fonte precisa usar o mapping 'ans_padroes' (versão 1).")
    try:
        arquivos, relatorio = await run_in_threadpool(
            padroes_ans.converter, await _ler_xmls(tiss, limite), await _ler_xmls(sib, limite), uf_padrao)
    except padroes_ans.PadraoANSError as e:
        raise ApiError("invalid_request", str(e)[:300]) from e
    audit.add(db, "integration.ans_converted", actor=p.actor(), tenant_id=tenant_id,
              entity_type="source_connection", entity_id=source_id,
              details={"contagens": relatorio["contagens"], "processar": processar})
    db.commit()
    if not processar:
        return {"relatorio": relatorio, "carga": None}
    if relatorio["prestadores_sem_uf"]:
        raise ApiError("invalid_request", f"{relatorio['prestadores_sem_uf']} prestador(es) sem UF — "
                                          "informe a UF padrão da rede (uf_padrao).")
    if not _limitador_upload().hit(f"upload:user:{p.user_id}"):
        raise ApiError("rate_limited", "Limite de cargas por hora atingido. Tente mais tarde.")
    try:
        r = await run_in_threadpool(receive_package, tenant_id=tenant_id, source_connection_id=source_id,
                                    files=arquivos, triggered_by=p.email, max_bytes=limite,
                                    max_files=cfg.upload_max_files, limits=FileLimits.from_settings())
    except PipelineDatabaseNotConfigured as e:
        raise ApiError("service_unavailable", "Execução de pipeline não configurada neste ambiente.") from e
    except StorageUnavailable as e:
        raise ApiError("service_unavailable", "Armazenamento indisponível — tente novamente.") from e
    except (PipelineError, MappingError, FileValidationError) as e:
        raise ApiError("invalid_request", str(e)[:300]) from e
    response.status_code = 202 if r.status == "QUEUED" else 200
    return {"relatorio": relatorio, "carga": r.as_dict()}


@router.post("/tenants/{tenant_id}/diops-conferencia",
             summary="Confere o balancete DIOPS do trimestre (contas 311 e 411) com a camada analítica")
async def diops_conferencia(
    tenant_id: str, arquivo: UploadFile = File(...),
    ano: int = Form(..., ge=2000, le=2100), trimestre: int = Form(..., ge=1, le=4),
    tolerancia_pct: float = Form(1.0, ge=0, le=100),
    _p: Principal = Depends(_INTEG), db: Session = Depends(get_db),
) -> dict:
    dados = await arquivo.read(5 * 1024 * 1024 + 1)
    if len(dados) > 5 * 1024 * 1024:
        raise ApiError("invalid_request", "balancete maior que 5 MB")
    with tenant_session(db, tenant_id) as s:
        try:
            return padroes_ans.conferir_diops(s, dados, ano, trimestre, tolerancia_pct)
        except padroes_ans.PadraoANSError as e:
            raise ApiError("invalid_request", str(e)[:300]) from e


# ============================================================================ jobs (operação)
def _job_dict(j: PipelineJob, agora: datetime) -> dict:
    dur = None
    if j.started_at:
        dur = int(((j.finished_at or agora) - j.started_at).total_seconds() * 1000)
    travado = j.status == "RUNNING" and j.lease_expires_at is not None and j.lease_expires_at < agora
    return {
        "id": j.id, "tenant_id": j.tenant_id, "source_connection_id": j.source_connection_id,
        "ingestion_run_id": j.ingestion_run_id, "pipeline_run_id": j.pipeline_run_id,
        "status": j.status, "attempts": j.attempts, "max_attempts": j.max_attempts,
        "created_at": _iso(j.created_at), "started_at": _iso(j.started_at), "finished_at": _iso(j.finished_at),
        "next_attempt_at": _iso(j.next_attempt_at), "heartbeat_at": _iso(j.heartbeat_at),
        "duration_ms": dur, "last_error": j.last_error, "error_class": j.error_class,
        "stuck": travado, "created_by": j.created_by,
        # retry só faz sentido para falha de infraestrutura (DQ/mapping exigem novo envio)
        "can_retry": (j.status == "FAILED" and j.error_class == "retryable") or travado,
        "can_cancel": j.status == "QUEUED",
    }


@router.get("/jobs", summary="Fila do pipeline — status, tentativas, travados")
def list_jobs(status: str | None = Query(None), tenant_id: str | None = Query(None),
              stuck: bool = Query(False), limit: int = Query(50, ge=1, le=200),
              _p: Principal = Depends(_INTEG), db: Session = Depends(get_db)) -> dict:
    agora = db.execute(select(func.now())).scalar_one()
    stmt = select(PipelineJob).order_by(desc(PipelineJob.id)).limit(limit)
    if status:
        stmt = stmt.where(PipelineJob.status == status)
    if tenant_id:
        stmt = stmt.where(PipelineJob.tenant_id == tenant_id)
    if stuck:
        stmt = stmt.where(PipelineJob.status == "RUNNING", PipelineJob.lease_expires_at < agora)
    itens = [_job_dict(j, agora) for j in db.execute(stmt).scalars()]
    resumo = {st: n for st, n in db.execute(select(PipelineJob.status, func.count())
                                            .group_by(PipelineJob.status)).all()}
    mais_antigo = db.execute(select(func.min(PipelineJob.created_at))
                             .where(PipelineJob.status == "QUEUED")).scalar()
    vivos = db.execute(select(func.count()).select_from(WorkerHeartbeat).where(
        WorkerHeartbeat.last_seen_at > agora - timedelta(seconds=120))).scalar_one()
    return {"itens": itens, "resumo": resumo, "workers_ativos": vivos,
            "fila_mais_antiga_segundos": int((agora - mais_antigo).total_seconds()) if mais_antigo else None,
            "travados": sum(1 for i in itens if i["stuck"])}


def _job_ou_404(db: Session, job_id: int) -> PipelineJob:
    j = db.get(PipelineJob, job_id)
    if j is None:
        raise ApiError("not_found", "Job não encontrado.")
    return j


@router.post("/jobs/{job_id}/retry", summary="Reenfileira um job (falha de infraestrutura ou travado) — auditado")
def retry_job(job_id: int, p: Principal = Depends(_INTEG), db: Session = Depends(get_db)) -> dict:
    agora = db.execute(select(func.now())).scalar_one()
    j = _job_ou_404(db, job_id)
    info = _job_dict(j, agora)
    if not info["can_retry"]:
        raise ApiError("conflict", "Este job não pode ser reexecutado: falhas de Data Quality, mapping ou "
                                   "validação exigem corrigir a origem e reenviar o pacote.")
    ok = admin_requeue(job_id, by=p.email)
    if not ok:
        raise ApiError("conflict", "O job mudou de estado — atualize a lista.")
    audit.add(db, "pipeline.job_retry", actor=p.actor(), tenant_id=j.tenant_id, entity_type="pipeline_job",
              entity_id=job_id, details={"status_anterior": info["status"], "tentativas": info["attempts"],
                                         "travado": info["stuck"]})
    db.commit()
    db.expire_all()
    return _job_dict(_job_ou_404(db, job_id), agora)


@router.post("/jobs/{job_id}/cancel", summary="Cancela um job ainda na fila — auditado")
def cancel_job(job_id: int, p: Principal = Depends(_INTEG), db: Session = Depends(get_db)) -> dict:
    agora = db.execute(select(func.now())).scalar_one()
    j = _job_ou_404(db, job_id)
    if j.status != "QUEUED":
        raise ApiError("conflict", "Só é possível cancelar jobs ainda na fila.")
    if not admin_cancel(job_id, by=p.email):
        raise ApiError("conflict", "O job mudou de estado — atualize a lista.")
    audit.add(db, "pipeline.job_cancel", actor=p.actor(), tenant_id=j.tenant_id, entity_type="pipeline_job",
              entity_id=job_id, details={"tentativas": j.attempts})
    db.commit()
    db.expire_all()
    return _job_dict(_job_ou_404(db, job_id), agora)


# ============================================================================ execuções
@router.get("/tenants/{tenant_id}/ingestion-runs")
def list_runs(tenant_id: str, source_id: int | None = Query(None), limit: int = Query(50, ge=1, le=200),
              _p: Principal = Depends(_INTEG), db: Session = Depends(get_db)) -> dict:
    with tenant_session(db, tenant_id) as s:
        stmt = select(IngestionRun).order_by(desc(IngestionRun.id)).limit(limit)
        if source_id:
            stmt = stmt.where(IngestionRun.source_connection_id == source_id)
        itens = []
        runs = s.execute(stmt).scalars().all()
        jobs = {j.ingestion_run_id: j for j in db.execute(select(PipelineJob).where(
            PipelineJob.tenant_id == tenant_id,
            PipelineJob.ingestion_run_id.in_([r.id for r in runs]))).scalars()} if runs else {}
        agora = db.execute(select(func.now())).scalar_one()
        for r in runs:
            j = jobs.get(r.id)
            itens.append({**run_dict(r), "reconciliation": _recon_status(s, r.id),
                          "job": _job_dict(j, agora) if j else None})
        return {"itens": itens}


@router.get("/tenants/{tenant_id}/ingestion-runs/{run_id}")
def get_run(tenant_id: str, run_id: int, _p: Principal = Depends(_INTEG), db: Session = Depends(get_db)) -> dict:
    with tenant_session(db, tenant_id) as s:
        r = s.get(IngestionRun, run_id)
        if r is None:
            raise ApiError("not_found", "Execução não encontrada.")
        prun = s.execute(select(PipelineRun).where(PipelineRun.ingestion_run_id == r.id)
                         .order_by(desc(PipelineRun.id))).scalars().first()
        dq = s.execute(select(DataQualityResult).where(DataQualityResult.ingestion_run_id == r.id)
                       .order_by(DataQualityResult.id)).scalars().all()
        rec = s.execute(select(ReconciliationResult).where(ReconciliationResult.ingestion_run_id == r.id)
                        .order_by(ReconciliationResult.id)).scalars().all()
        return {
            "run": run_dict(r),
            "lineage": lineage.ingestion_detail(s, r),
            "steps": prun.steps if prun else [],
            "pipeline_status": prun.status if prun else None,
            "data_quality": [{"rule_id": d.rule_id, "description": d.rule_description, "entity": d.entity,
                              "severity": d.severity, "blocking": d.blocking, "checked": d.records_checked,
                              "failed": d.records_failed, "sample": (d.sample or {}).get("referencias", [])}
                             for d in dq],
            "reconciliation": [{"check_id": c.check_id, "entity": c.entity, "scope": c.scope,
                                "expected": str(c.expected), "actual": str(c.actual),
                                "difference": str(c.difference), "tolerance": str(c.tolerance),
                                "status": c.status} for c in rec],
            "reconciliation_status": _recon_status(s, r.id),
            "job": (lambda j: _job_dict(j, db.execute(select(func.now())).scalar_one()) if j else None)(
                db.execute(select(PipelineJob).where(PipelineJob.tenant_id == tenant_id,
                                                     PipelineJob.ingestion_run_id == r.id)).scalars().first()),
        }


@router.get("/tenants/{tenant_id}/lineage", summary="De onde veio a métrica da competência?")
def get_lineage(tenant_id: str, competencia: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
                _p: Principal = Depends(_INTEG), db: Session = Depends(get_db)) -> dict:
    y, m = competencia.split("-")
    with tenant_session(db, tenant_id) as s:
        return lineage.metric_lineage(s, tenant_id, date(int(y), int(m), 1))


# ============================================================================ readiness / onboarding
def capabilities(s: Session, t: Tenant) -> list[dict]:
    prontidao = readiness.current(s, t.id)
    out = []
    for f in resolve_all(s, t):
        r = prontidao.get(f.key)
        out.append({"key": f.key, "name": f.name, "entitled": f.enabled, "entitlement_source": f.source,
                    "data_status": r.status if r else "NOT_READY", "data_reason": r.reason if r else "",
                    "available": f.enabled and bool(r and r.ready)})
    return out


@router.get("/tenants/{tenant_id}/readiness", summary="Feature contratada × dados prontos × disponível")
def get_readiness(tenant_id: str, _p: Principal = Depends(_INTEG), db: Session = Depends(get_db)) -> dict:
    with tenant_session(db, tenant_id) as s:
        out = {"itens": capabilities(s, s.get(Tenant, tenant_id))}
        s.commit()
        return out


@router.post("/tenants/{tenant_id}/readiness/refresh")
def refresh_readiness(tenant_id: str, p: Principal = Depends(_INTEG), db: Session = Depends(get_db)) -> dict:
    with tenant_session(db, tenant_id) as s:
        readiness.refresh(s, tenant_id)
        audit.add(s, "integration.readiness_refreshed", actor=p.actor(), tenant_id=tenant_id,
                  entity_type="tenant", entity_id=tenant_id)
        s.commit()
        return {"itens": capabilities(s, s.get(Tenant, tenant_id))}


@router.get("/tenants/{tenant_id}/onboarding")
def get_onboarding(tenant_id: str, _p: Principal = Depends(_INTEG), db: Session = Depends(get_db)) -> dict:
    with tenant_session(db, tenant_id) as s:
        ob = onboarding.get(s, tenant_id)
        s.commit()
        return onboarding.as_dict(ob)


@router.post("/tenants/{tenant_id}/onboarding", summary="Decisão manual: HOMOLOGATED ou ACTIVE")
def decide_onboarding(tenant_id: str, payload: OnboardingIn, p: Principal = Depends(_INTEG),
                      db: Session = Depends(get_db)) -> dict:
    with tenant_session(db, tenant_id) as s:
        try:
            ob = onboarding.decide(s, tenant_id, payload.state, by=p.email, note=payload.note)
        except onboarding.OnboardingError as e:
            raise ApiError("invalid_request", str(e)) from e
        audit.add(s, "integration.onboarding_decision", actor=p.actor(), tenant_id=tenant_id,
                  entity_type="tenant", entity_id=tenant_id,
                  details={"estado": payload.state, "nota": payload.note})
        s.commit()
        return onboarding.as_dict(ob)
