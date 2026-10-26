"""Orquestrador do pipeline de arquivo (fonte FILE) — um tenant por execução.

Fase 3: a carga é ASSÍNCRONA, dividida em duas metades com a fila no meio.

    [requisição HTTP]  receive_package
        validar arquivos → RAW (imutável) → ingestão QUEUED + job na MESMA transação
    [fila]             pipeline_jobs (só ids técnicos)
    [worker]           process_ingestion
        reconstruir PipelineContext → validar job × ingestão (RLS) → ler RAW (sha256)
        → mapping → canônico → Data Quality (gate)
        → [transação] Silver → Gold → readiness → reconciliação → publicação (commit)

Garantias (mantidas da Fase 2 e reforçadas):
* tenant explícito (`PipelineContext`) e sessões do papel `w2health_pipeline` amarradas a
  ele (RLS) — sem fallback para o papel dono; o worker reconstrói o contexto do zero a cada
  job (nada é herdado de um job anterior);
* nada é promovido antes do gate de Data Quality;
* Silver + Gold + reconciliação na MESMA transação: carga reprovada não é publicada;
* metadados (ingestão, DQ, reconciliação, passos) gravados em transações próprias —
  sobrevivem à falha e explicam "por que a carga do tenant X falhou";
* estágio honesto: RECEIVED → VALIDATED → PROCESSED → RECONCILED → AVAILABLE;
* idempotência: reenviar o MESMO pacote (checksum) não reprocessa; reexecutar o MESMO job
  (retry, worker reiniciado) não duplica — ingestão já publicada é no-op, e Silver usa
  UPSERT/snapshot por competência.
"""

from __future__ import annotations

import csv
import hashlib
import io
import logging
import re
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.logging import log_context
from app.data_platform import onboarding, quality, readiness, reconciliation
from app.data_platform.canonical import ENTITIES, LOAD_ORDER
from app.data_platform.connections import load_connection_mapping
from app.data_platform.context import PipelineContext, PipelineError, require_active_tenant
from app.data_platform.mapping import EntityResult, Mapping, apply_entity
from app.data_platform.silver import SilverLoader, derive_canonical, existing_codes
from app.data_platform.storage import RawStorageError, get_raw_storage, raw_key, sha256_hex
from app.db.pipeline import PipelineSession
from app.db.tenant_scope import bind_tenant
from app.models import (
    DataQualityResult,
    IngestionRun,
    PipelineJob,
    PipelineRun,
    RawObject,
    ReconciliationResult,
    SourceConnection,
)
from app.saas import audit
from app.seed.aggregate import rebuild_aggregations

log = logging.getLogger("app.pipeline")

_FILE = re.compile(r"^([a-z][a-z0-9_]{1,60})\.csv$")
#: assinaturas de formatos binários comuns — "planilha.csv" que na verdade é zip/pdf/imagem
_MAGIC = (b"PK\x03\x04", b"%PDF", b"\x89PNG", b"\xff\xd8\xff", b"GIF8", b"MZ", b"\x1f\x8b",
          b"\xd0\xcf\x11\xe0", b"7z\xbc\xaf", b"Rar!")


@dataclass
class RunSummary:
    ingestion_run_id: int
    pipeline_run_id: int | None
    status: str
    stage: str | None
    received: int = 0
    valid: int = 0
    rejected: int = 0
    warnings: int = 0
    errors: int = 0
    reconciliation: str | None = None
    duplicate_of: int | None = None
    message: str = ""
    entities: dict = field(default_factory=dict)
    job_id: int | None = None
    failure_reason: str | None = None

    def as_dict(self) -> dict:
        return self.__dict__.copy()


class FileValidationError(PipelineError):
    pass


class JobIntegrityError(PipelineError):
    """O job não corresponde ao que existe no banco do tenant (adulterado ou órfão)."""


class RawIntegrityError(PipelineError):
    """O conteúdo lido do RAW não confere com o sha256 registrado na recepção."""


# ======================================================================== validação de arquivos
@dataclass(frozen=True)
class FileLimits:
    max_bytes: int
    max_files: int
    max_columns: int = 200
    max_line_bytes: int = 65536
    max_rows: int = 5_000_000

    @classmethod
    def from_settings(cls) -> FileLimits:
        from app.core.config import get_settings

        s = get_settings()
        return cls(max_bytes=s.upload_max_file_mb * 1024 * 1024, max_files=s.upload_max_files,
                   max_columns=s.upload_max_columns, max_line_bytes=s.upload_max_line_bytes,
                   max_rows=s.upload_max_rows)


def validate_files(mapping: Mapping, files: dict[str, bytes], *, max_bytes: int,
                   max_files: int, limits: FileLimits | None = None) -> dict[str, tuple[list[str], list[dict], bytes]]:
    """Valida nome, extensão, tamanho, encoding, estrutura e dimensões do CSV. Nunca executa
    nem interpreta o conteúdo (fórmulas ficam como texto). Retorna
    {source_entity: (cabeçalho, linhas, bytes)}."""
    lim = limits or FileLimits(max_bytes=max_bytes, max_files=max_files)
    if not files:
        raise FileValidationError("nenhum arquivo recebido")
    if len(files) > lim.max_files:
        raise FileValidationError(f"no máximo {lim.max_files} arquivos por carga")
    out = {}
    for nome, data in files.items():
        base = nome.replace("\\", "/").rsplit("/", 1)[-1].strip().lower()
        m = _FILE.match(base)
        if not m:
            raise FileValidationError(f"nome de arquivo inválido: use <entidade>.csv ({base[:60]!r})")
        entidade = m.group(1)
        if mapping.by_source(entidade) is None:
            raise FileValidationError(f"arquivo '{base}' não pertence ao mapping {mapping.ref}")
        if entidade in out:
            raise FileValidationError(f"arquivo duplicado: {base}")
        if len(data) == 0:
            raise FileValidationError(f"arquivo vazio: {base}")
        if len(data) > lim.max_bytes:
            raise FileValidationError(f"arquivo maior que o limite ({lim.max_bytes // (1024 * 1024)} MB): {base}")
        if data.startswith(_MAGIC) or b"\x00" in data[:65536]:
            raise FileValidationError(f"arquivo binário não é aceito (extensão .csv não confere): {base}")
        try:
            texto = data.decode(mapping.encoding).lstrip("﻿")
        except UnicodeDecodeError as e:
            raise FileValidationError(f"encoding inválido em {base} (esperado {mapping.encoding})") from e
        maior_linha = max((len(x.encode(mapping.encoding, errors="replace"))
                           for x in texto.splitlines()), default=0)
        if maior_linha > lim.max_line_bytes:
            raise FileValidationError(f"linha maior que o limite ({lim.max_line_bytes} bytes) em {base}")
        try:
            leitor = csv.reader(io.StringIO(texto, newline=""), delimiter=mapping.delimiter, strict=True)
            linhas = list(leitor)
        except csv.Error as e:
            raise FileValidationError(f"CSV malformado em {base}: {str(e)[:80]}") from e
        if not linhas or not any(c.strip() for c in linhas[0]):
            raise FileValidationError(f"cabeçalho ausente em {base}")
        cab = [c.strip() for c in linhas[0]]
        if len(cab) > lim.max_columns:
            raise FileValidationError(f"{base}: {len(cab)} colunas — acima do limite ({lim.max_columns})")
        if len(set(cab)) != len(cab):
            raise FileValidationError(f"colunas repetidas no cabeçalho de {base}")
        if len(linhas) - 1 > lim.max_rows:
            raise FileValidationError(f"{base}: acima do limite de {lim.max_rows} linhas")
        registros = []
        for n, lin in enumerate(linhas[1:], start=2):
            if not lin or all(not c.strip() for c in lin):
                continue
            if len(lin) != len(cab):
                raise FileValidationError(f"{base} linha {n}: {len(lin)} colunas, esperado {len(cab)}")
            registros.append(dict(zip(cab, lin, strict=True)))
        out[entidade] = (cab, registros, data)
    return out


def package_checksum(files: dict[str, bytes]) -> str:
    h = hashlib.sha256()
    for nome in sorted(files):
        h.update(nome.encode())
        h.update(hashlib.sha256(files[nome]).digest())
    return h.hexdigest()


# ======================================================================== metadados
class _Meta:
    """Gravações de metadados em transações próprias (sobrevivem a falhas)."""

    def __init__(self, ctx: PipelineContext, on_step: Callable[[], None] | None = None) -> None:
        self.ctx = ctx
        self.on_step = on_step

    def session(self) -> Session:
        s = PipelineSession()
        bind_tenant(s, self.ctx.tenant_id)
        return s

    def update_run(self, run_id: int, **campos) -> None:
        with self.session() as s:
            r = s.get(IngestionRun, run_id)
            for k, v in campos.items():
                setattr(r, k, v)
            s.commit()

    def step(self, prun_id: int, stage: str, status: str, t0: float, **info) -> None:
        ms = int((time.perf_counter() - t0) * 1000)
        with self.session() as s:
            p = s.get(PipelineRun, prun_id)
            p.steps = [*(p.steps or []), {"stage": stage, "status": status, "ms": ms, **info}]
            p.layer = stage
            s.commit()
        log.info("pipeline.step", extra={"event": "pipeline.step", "stage": stage, "status": status,
                                         "duration_ms": ms, **self.ctx.log_fields()})
        if self.on_step is not None:
            self.on_step()  # heartbeat do worker (renova o lease do job)


# ======================================================================== 1ª metade: recepção
def receive_package(*, tenant_id: str, source_connection_id: int, files: dict[str, bytes],
                    triggered_by: str, max_bytes: int, max_files: int,
                    limits: FileLimits | None = None, max_attempts: int | None = None) -> RunSummary:
    """Executa na requisição: valida, grava o RAW e enfileira. Responde rápido — o
    processamento pesado fica para o worker. Retorna o resumo com `job_id`."""
    from app.core.config import get_settings

    ctx = PipelineContext(tenant_id=tenant_id, source_connection_id=source_connection_id,
                          triggered_by=triggered_by)
    meta = _Meta(ctx)
    with meta.session() as s:
        require_active_tenant(s, tenant_id)
        conn = s.get(SourceConnection, source_connection_id)
        if conn is None:  # RLS: fonte de outro tenant é invisível
            raise PipelineError("fonte inexistente neste tenant")
        if conn.source_type != "FILE" or conn.status != "ACTIVE":
            raise PipelineError("fonte não é um arquivo ativo")
        mapping = load_connection_mapping(conn.configuration or {})
        checksum = package_checksum(files)
        anterior = s.execute(select(IngestionRun).where(
            IngestionRun.source_connection_id == conn.id, IngestionRun.checksum == checksum,
            IngestionRun.stage == "AVAILABLE").order_by(IngestionRun.id.desc())).scalars().first()
        run = IngestionRun(tenant_id=tenant_id, source_connection_id=conn.id,
                           source_system=conn.source_system, status="RUNNING", checksum=checksum,
                           triggered_by=triggered_by, mapping_ref=mapping.ref,
                           correlation_id=ctx.correlation_id, error_summary={})
        s.add(run)
        s.flush()
        prun = PipelineRun(tenant_id=tenant_id, ingestion_run_id=run.id, source_connection_id=conn.id,
                           layer="ingestion", status="RUNNING", triggered_by=triggered_by,
                           correlation_id=ctx.correlation_id, steps=[])
        s.add(prun)
        conn.last_run_at = datetime.now(UTC)
        s.commit()
    ctx = ctx.with_runs(pipeline_run_id=prun.id, ingestion_run_id=run.id)
    meta.ctx = ctx
    summary = RunSummary(run.id, prun.id, "RUNNING", None)

    with log_context(**ctx.log_fields()):
        log.info("pipeline.received", extra={"event": "pipeline.received", "files": sorted(files)})
        if anterior is not None:
            return _finish_duplicate(meta, ctx, summary, anterior.id)
        try:
            t0 = time.perf_counter()
            parsed = validate_files(mapping, files, max_bytes=max_bytes, max_files=max_files,
                                    limits=limits or FileLimits(max_bytes=max_bytes, max_files=max_files))
            meta.step(ctx.pipeline_run_id, "file_validation", "OK", t0, arquivos=len(parsed))
        except FileValidationError as e:
            _fail(meta, ctx, summary, str(e)[:300], stage=None)
            return summary

        # RAW (imutável) + ingestão QUEUED + job — o job só existe se o RAW existe
        t0 = time.perf_counter()
        storage = get_raw_storage()
        refs = []
        try:
            for entidade, (_cab, linhas, data) in parsed.items():
                ref = storage.put(raw_key(ctx.tenant_id, ctx.source_connection_id, entidade,
                                          ctx.ingestion_run_id, f"{entidade}.csv"), data,
                                  content_type="text/csv")
                refs.append((entidade, ref, len(linhas)))
        except RawStorageError as e:
            _fail(meta, ctx, summary, "armazenamento RAW indisponível — reenvie o pacote", stage=None)
            raise e
        recebidos = sum(n for _e, _r, n in refs)
        with meta.session() as s:
            for entidade, ref, n in refs:
                s.add(RawObject(tenant_id=ctx.tenant_id, source_connection_id=ctx.source_connection_id,
                                ingestion_run_id=ctx.ingestion_run_id, source_entity=entidade,
                                storage_key=ref.key, file_name=f"{entidade}.csv", size_bytes=ref.size,
                                sha256=ref.sha256, records=n))
            r = s.get(IngestionRun, ctx.ingestion_run_id)
            r.status, r.stage, r.records_received = "QUEUED", "RECEIVED", recebidos
            p = s.get(PipelineRun, ctx.pipeline_run_id)
            p.status = "QUEUED"
            job = PipelineJob(job_type="file_ingestion", tenant_id=ctx.tenant_id,
                              source_connection_id=ctx.source_connection_id,
                              ingestion_run_id=ctx.ingestion_run_id, pipeline_run_id=ctx.pipeline_run_id,
                              dedup_key=f"file_ingestion:{ctx.tenant_id}:{ctx.ingestion_run_id}",
                              status="QUEUED", max_attempts=max_attempts or get_settings().job_max_attempts,
                              correlation_id=ctx.correlation_id, created_by=triggered_by[:254])
            s.add(job)
            s.commit()  # outbox: RAW registrado, ingestão QUEUED e job — juntos ou nada
            onboarding.advance(s, ctx.tenant_id, "RAW_LOADED", by=ctx.triggered_by)
            s.commit()
            job_id = job.id
        meta.step(ctx.pipeline_run_id, "raw", "OK", t0, registros=recebidos)
        _audit(meta, ctx.with_runs(job_id=job_id), "pipeline.ingestion_queued", "success",
               {"job_id": job_id, "recebidas": recebidos, "arquivos": len(refs)})
        summary.status, summary.stage, summary.received, summary.job_id = "QUEUED", "RECEIVED", recebidos, job_id
        summary.message = "pacote recebido e enfileirado — o processamento continua no worker"
        log.info("pipeline.queued", extra={"event": "pipeline.queued", "job_id": job_id})
        return summary


def _finish_duplicate(meta: _Meta, ctx: PipelineContext, summary: RunSummary, anterior: int) -> RunSummary:
    msg = f"pacote idêntico à ingestão #{anterior} — nada a reprocessar (idempotência por checksum)"
    meta.update_run(ctx.ingestion_run_id, status="SUCCESS", stage="AVAILABLE", duplicate_of=anterior,
                    finished_at=datetime.now(UTC), error_summary={"nota": msg})
    meta.step(ctx.pipeline_run_id, "duplicate_check", "SKIPPED", time.perf_counter(), duplicate_of=anterior)
    _close_pipeline(meta, ctx, "SUCCESS")
    summary.status, summary.stage, summary.duplicate_of, summary.message = "SUCCESS", "AVAILABLE", anterior, msg
    _audit(meta, ctx, "pipeline.ingestion_duplicate", "success", {"duplicate_of": anterior})
    return summary


# ======================================================================== 2ª metade: worker
@dataclass(frozen=True)
class JobRef:
    """O que o worker sabe de um job: só identificadores técnicos."""

    job_id: int
    tenant_id: str
    source_connection_id: int
    ingestion_run_id: int
    pipeline_run_id: int
    attempt: int
    created_by: str | None = None
    correlation_id: str | None = None


def job_context(job: JobRef) -> PipelineContext:
    """Contexto NOVO a cada job — nada herdado do job anterior do mesmo processo."""
    return PipelineContext(tenant_id=job.tenant_id, source_connection_id=job.source_connection_id,
                           triggered_by=job.created_by or "worker",
                           correlation_id=job.correlation_id or uuid.uuid4().hex,
                           ).with_runs(pipeline_run_id=job.pipeline_run_id,
                                       ingestion_run_id=job.ingestion_run_id, job_id=job.job_id)


def process_ingestion(job: JobRef, *, on_step: Callable[[], None] | None = None,
                      limits: FileLimits | None = None) -> RunSummary:
    """Processa um job. Erros de infraestrutura SOBEM (o worker decide retry); desfechos de
    negócio (DQ bloqueou, reconciliação reprovou) viram status FAILED no resumo."""
    ctx = job_context(job)
    meta = _Meta(ctx, on_step=on_step)
    lim = limits or FileLimits.from_settings()
    summary = RunSummary(job.ingestion_run_id, job.pipeline_run_id, "RUNNING", "RECEIVED", job_id=job.job_id)
    with log_context(**ctx.log_fields()):
        with meta.session() as s:
            require_active_tenant(s, ctx.tenant_id)
            run = s.get(IngestionRun, job.ingestion_run_id)  # sob RLS do tenant do job
            prun = s.get(PipelineRun, job.pipeline_run_id)
            if (run is None or prun is None or run.source_connection_id != job.source_connection_id
                    or prun.ingestion_run_id != run.id):
                raise JobIntegrityError("job não corresponde a uma ingestão deste tenant")
            if run.status in ("SUCCESS", "PARTIAL") and run.stage == "AVAILABLE":
                summary.status, summary.stage = run.status, run.stage
                summary.message = "ingestão já publicada — nada a refazer (idempotência)"
                return summary
            if run.status in ("FAILED", "CANCELLED"):
                summary.status, summary.message = run.status, "ingestão encerrada — nada a processar"
                return summary
            conn = s.get(SourceConnection, job.source_connection_id)
            if conn is None:
                raise JobIntegrityError("fonte do job inexistente neste tenant")
            mapping = load_connection_mapping(conn.configuration or {})
            source_system = conn.source_system
            raws = s.execute(select(RawObject).where(RawObject.ingestion_run_id == run.id)
                             .order_by(RawObject.id)).scalars().all()
            if not raws:
                raise JobIntegrityError("ingestão sem objetos RAW")
            run.status, prun.status = "RUNNING", "RUNNING"
            prun.steps = [*(prun.steps or []), {"stage": "attempt", "status": "STARTED", "ms": 0,
                                                "tentativa": job.attempt}]
            # nova tentativa começa limpa: resultados de uma tentativa interrompida saem
            s.execute(delete(DataQualityResult).where(DataQualityResult.ingestion_run_id == run.id))
            s.execute(delete(ReconciliationResult).where(ReconciliationResult.ingestion_run_id == run.id))
            s.commit()
            objetos = [(r.storage_key, r.file_name, r.sha256) for r in raws]
        log.info("pipeline.start", extra={"event": "pipeline.start", "attempt": job.attempt})

        storage = get_raw_storage()
        files: dict[str, bytes] = {}
        t0 = time.perf_counter()
        for key, nome, sha in objetos:
            data = storage.get(key)
            if sha256_hex(data) != sha:
                raise RawIntegrityError(f"conteúdo RAW não confere com o registrado ({nome})")
            files[nome] = data
        parsed = validate_files(mapping, files, max_bytes=lim.max_bytes, max_files=lim.max_files, limits=lim)
        summary.received = sum(len(v[1]) for v in parsed.values())
        meta.step(ctx.pipeline_run_id, "raw_read", "OK", t0, objetos=len(objetos))
        return _transform_and_publish(meta, ctx, mapping, parsed, summary, source_system)


def _transform_and_publish(meta: _Meta, ctx: PipelineContext, mapping: Mapping, parsed: dict,
                           summary: RunSummary, source_system: str) -> RunSummary:
    # 3. mapping → canônico --------------------------------------------------------
    t0 = time.perf_counter()
    results: dict[str, EntityResult] = {}
    for entidade, (cab, linhas, _d) in parsed.items():
        em = mapping.by_source(entidade)
        res = apply_entity(mapping, em, linhas, cab)
        for mr in res.rows:
            mr.values = derive_canonical(em.target_entity, mr.values)
        results[em.target_entity] = res
    meta.step(ctx.pipeline_run_id, "mapping", "OK", t0, mapping=mapping.ref)

    # 4. Data Quality (gate) — antes de qualquer promoção -------------------------
    t0 = time.perf_counter()
    with meta.session() as s:
        existentes = existing_codes(s, ctx.tenant_id)
    business_keys = {e: ENTITIES[e].business_key for e in LOAD_ORDER}
    report = quality.evaluate(mapping, results, {m.source_entity for m in mapping.entities
                                                 if m.source_entity in parsed},
                              existentes, business_keys, LOAD_ORDER)
    _persist_dq(meta, ctx, report)
    summary.errors, summary.warnings = report.errors, report.warnings
    summary.rejected = sum(len(q.rejected_lines) for q in report.entities.values())
    summary.valid = sum(len(q.valid_rows) for q in report.entities.values())
    summary.entities = {e: {"recebidas": q.received, "validas": len(q.valid_rows),
                            "rejeitadas": len(q.rejected_lines), "duplicadas": q.duplicates_removed}
                        for e, q in report.entities.items()}
    meta.update_run(ctx.ingestion_run_id, records_valid=summary.valid,
                    records_rejected=summary.rejected, warnings_count=summary.warnings,
                    errors_count=summary.errors)
    if report.blocked:
        meta.step(ctx.pipeline_run_id, "data_quality", "BLOCKED", t0, erros=summary.errors)
        bloqueios = [f.description for f in report.findings if f.blocking][:5]
        summary.failure_reason = "dq_blocked"
        _fail(meta, ctx, summary, "Data Quality bloqueou a promoção", stage="RECEIVED",
              detalhes={"bloqueios": bloqueios})
        return summary
    meta.step(ctx.pipeline_run_id, "data_quality", "OK", t0, erros=summary.errors, avisos=summary.warnings)
    with meta.session() as s:
        onboarding.advance(s, ctx.tenant_id, "MAPPING_VALIDATED", by=ctx.triggered_by)
        onboarding.advance(s, ctx.tenant_id, "DATA_QUALITY_VALIDATED", by=ctx.triggered_by)
        s.commit()
    summary.stage = "VALIDATED"
    meta.update_run(ctx.ingestion_run_id, stage="VALIDATED")

    # 5. publicação transacional: Silver → Gold → readiness → reconciliação ---------
    validas = {e: q.valid_rows for e, q in report.entities.items()}
    meses = {r["competencia"] for e in ("evento_assistencial", "receita", "receita_reconhecida")
             for r in validas.get(e, [])}
    meses |= {r["competencia_faturamento"] for r in validas.get("fatura", [])}
    meses |= {r["competencia_recebimento"] for r in validas.get("recebimento", [])}
    ref_inicio = min(meses) if meses else date.today().replace(day=1)
    ref_fim = max(meses) if meses else date.today().replace(day=1)
    t0 = time.perf_counter()
    data_s = PipelineSession()
    try:
        bind_tenant(data_s, ctx.tenant_id)
        loader = SilverLoader(data_s, ctx, source_system)
        loader.ensure_competencias(meses)
        etapas = {
            "especialidade": loader.especialidades, "plano": loader.planos,
            "contrato": loader.contratos, "plano_contrato": loader.planos_contratos,
            "prestador": loader.prestadores,
            "procedimento": loader.procedimentos,
            "beneficiario": lambda rows: loader.beneficiarios(rows, ref_inicio, ref_fim),
            "vinculo_beneficiario": loader.vinculos, "preco_contratado": loader.precos,
            "empresa": loader.empresas, "empresa_contrato": loader.empresas_contratos,
            "fatura": loader.faturas, "fatura_item": loader.fatura_itens,
            "recebimento": loader.recebimentos, "receita_reconhecida": loader.receitas_reconhecidas,
            "receita": loader.receitas, "evento_assistencial": loader.eventos,
        }
        for entidade in LOAD_ORDER:
            if validas.get(entidade):
                etapas[entidade](validas[entidade])
        data_s.flush()
        meta.step(ctx.pipeline_run_id, "silver", "OK", t0, inseridos=loader.stats.inserted,
                  atualizados=loader.stats.updated, removidos=loader.stats.deleted)

        t1 = time.perf_counter()
        gold = rebuild_aggregations(data_s, tenant_id=ctx.tenant_id)
        prontidao = readiness.refresh(data_s, ctx.tenant_id)
        meta.step(ctx.pipeline_run_id, "gold", "OK", t1, linhas=gold)
        summary.stage = "PROCESSED"

        t2 = time.perf_counter()
        checks = reconciliation.reconcile(
            data_s, ctx.tenant_id, ctx.ingestion_run_id,
            eventos=validas.get("evento_assistencial", []), receitas=validas.get("receita", []),
            beneficiarios=validas.get("beneficiario", []),
            received={e: q.received for e, q in report.entities.items()},
            rejected={e: len(q.rejected_lines) for e, q in report.entities.items()},
            duplicates={e: q.duplicates_removed for e, q in report.entities.items()},
            fin_tol=mapping.financial_tolerance_abs, count_tol=mapping.count_tolerance_abs)
        # Consolidação V1 — reconciliação financeira (receita, despesa, vidas) dos meses da carga
        checks += reconciliation.financeiro(data_s, sorted(meses))
        resultado = reconciliation.overall(checks)
        summary.reconciliation = resultado
        _persist_recon(meta, ctx, checks)
        meta.step(ctx.pipeline_run_id, "reconciliation", resultado, t2, verificacoes=len(checks))
        if resultado == reconciliation.FAIL:
            data_s.rollback()
            summary.failure_reason = "reconciliation_failed"
            _fail(meta, ctx, summary, "Reconciliação reprovou a carga — nada foi publicado",
                  stage="PROCESSED",
                  detalhes={"falhas": [c.as_dict() for c in checks if c.status == "FAIL"][:5]})
            return summary
        data_s.commit()  # publicação atômica (Silver + Gold + readiness)
    finally:
        data_s.close()

    status = "PARTIAL" if summary.rejected else "SUCCESS"
    meta.update_run(ctx.ingestion_run_id, status=status, stage="AVAILABLE", finished_at=datetime.now(UTC),
                    competencia_inicio=min(meses) if meses else None,
                    competencia_fim=max(meses) if meses else None,
                    records_inserted=sum(loader.stats.inserted.values()),
                    records_updated=sum(loader.stats.updated.values()))
    with meta.session() as s:
        for estado in ("SILVER_READY", "GOLD_READY", "RECONCILED"):
            onboarding.advance(s, ctx.tenant_id, estado, by=ctx.triggered_by)
        if any(r.ready for r in prontidao.values() if r.feature_key != "custom_branding"):
            onboarding.advance(s, ctx.tenant_id, "CAPABILITIES_READY", by=ctx.triggered_by)
        conn = s.get(SourceConnection, ctx.source_connection_id)
        conn.last_success_at = datetime.now(UTC)
        s.commit()
    _close_pipeline(meta, ctx, status)
    summary.status, summary.stage = status, "AVAILABLE"
    summary.message = ("carga publicada" if status == "SUCCESS"
                       else f"carga publicada com {summary.rejected} linha(s) rejeitada(s)")
    _audit(meta, ctx, "pipeline.ingestion_completed", "success",
           {"status": status, "recebidas": summary.received, "validas": summary.valid,
            "rejeitadas": summary.rejected, "reconciliacao": resultado})
    log.info("pipeline.fim", extra={"event": "pipeline.completed", "status": status, **ctx.log_fields()})
    return summary


# ======================================================================== desfechos chamados pelo worker
def fail_ingestion(job: JobRef, msg: str) -> None:
    """Falha definitiva (ou tentativas esgotadas) de um job — mensagem segura, sem stack."""
    ctx = job_context(job)
    meta = _Meta(ctx)
    summary = RunSummary(job.ingestion_run_id, job.pipeline_run_id, "FAILED", None)
    with meta.session() as s:
        run = s.get(IngestionRun, job.ingestion_run_id)
        if run is None:  # job adulterado: não existe ingestão deste tenant para marcar
            return
        stage = run.stage
    _fail(meta, ctx, summary, msg, stage=stage)


def requeue_ingestion(job: JobRef, msg: str, attempt: int, max_attempts: int) -> None:
    """Erro transitório: a ingestão volta a QUEUED com a nota da tentativa (sem dado de linha)."""
    ctx = job_context(job)
    meta = _Meta(ctx)
    with meta.session() as s:
        run = s.get(IngestionRun, job.ingestion_run_id)
        if run is None:
            return
        run.status = "QUEUED"
        run.error_summary = {**(run.error_summary or {}),
                             "ultima_tentativa": {"tentativa": attempt, "de": max_attempts, "erro": msg[:200]}}
        prun = s.get(PipelineRun, job.pipeline_run_id)
        if prun is not None:
            prun.status = "QUEUED"
            prun.steps = [*(prun.steps or []), {"stage": "attempt", "status": "RETRY", "ms": 0,
                                                "tentativa": attempt, "erro": msg[:120]}]
        s.commit()


def cancel_ingestion(job: JobRef, by: str) -> None:
    ctx = job_context(job)
    meta = _Meta(ctx)
    meta.update_run(job.ingestion_run_id, status="CANCELLED", finished_at=datetime.now(UTC),
                    error_summary={"erro": f"cancelada por {by[:120]}"})
    _close_pipeline(meta, ctx, "CANCELLED")
    _audit(meta, ctx, "pipeline.ingestion_cancelled", "success", {"por": by[:120]})


# ======================================================================== execução síncrona (CLI)
def run_file_ingestion(*, tenant_id: str, source_connection_id: int, files: dict[str, bytes],
                       triggered_by: str, max_bytes: int, max_files: int) -> RunSummary:
    """Conveniência para a CLI: recebe e processa AGORA, pelo mesmo caminho do worker
    (mesma fila, mesmo job, mesmas regras de retry) — não existe um segundo pipeline."""
    recebido = receive_package(tenant_id=tenant_id, source_connection_id=source_connection_id,
                               files=files, triggered_by=triggered_by, max_bytes=max_bytes,
                               max_files=max_files)
    if recebido.job_id is None:
        return recebido
    from app.worker.service import Worker

    Worker(worker_id=f"inline:{triggered_by[:60]}").run_job(recebido.job_id)
    with PipelineSession() as s:
        bind_tenant(s, tenant_id)
        run = s.get(IngestionRun, recebido.ingestion_run_id)
        recebido.status, recebido.stage = run.status, run.stage
        recebido.valid, recebido.rejected = run.records_valid, run.records_rejected
        recebido.errors, recebido.warnings = run.errors_count, run.warnings_count
        recebido.message = (run.error_summary or {}).get("erro", "") or recebido.message
    return recebido


# ======================================================================== persistência auxiliar
def _persist_dq(meta: _Meta, ctx: PipelineContext, report) -> None:
    with meta.session() as s:
        for f in report.findings:
            s.add(DataQualityResult(
                tenant_id=ctx.tenant_id, pipeline_run_id=ctx.pipeline_run_id,
                ingestion_run_id=ctx.ingestion_run_id, rule_id=f.rule_id[:60],
                rule_description=f.description[:300], entity=f.entity, severity=f.severity,
                blocking=f.blocking, records_checked=f.checked, records_failed=f.failed,
                sample={"referencias": f.sample}))
        s.commit()


def _persist_recon(meta: _Meta, ctx: PipelineContext, checks) -> None:
    with meta.session() as s:
        for c in checks:
            s.add(ReconciliationResult(
                tenant_id=ctx.tenant_id, pipeline_run_id=ctx.pipeline_run_id,
                ingestion_run_id=ctx.ingestion_run_id, check_id=c.check_id, entity=c.entity,
                scope=c.scope, expected=c.expected, actual=c.actual, difference=c.difference,
                tolerance=c.tolerance, status=c.status))
        s.commit()


def _close_pipeline(meta: _Meta, ctx: PipelineContext, status: str) -> None:
    with meta.session() as s:
        p = s.get(PipelineRun, ctx.pipeline_run_id)
        p.status = status
        p.finished_at = datetime.now(UTC)
        s.commit()


def _fail(meta: _Meta, ctx: PipelineContext, summary: RunSummary, msg: str, *,
          stage: str | None, detalhes: dict | None = None) -> None:
    resumo = {"erro": msg, "correlation_id": ctx.correlation_id, **(detalhes or {})}
    meta.update_run(ctx.ingestion_run_id, status="FAILED", stage=stage,
                    finished_at=datetime.now(UTC), error_message=msg[:500], error_summary=resumo)
    with meta.session() as s:
        conn = s.get(SourceConnection, ctx.source_connection_id)
        if conn is not None:
            conn.last_error_at = datetime.now(UTC)
            conn.last_error_summary = msg[:300]
        s.commit()
    _close_pipeline(meta, ctx, "FAILED")
    summary.status, summary.stage, summary.message = "FAILED", stage, msg
    _audit(meta, ctx, "pipeline.ingestion_failed", "failure", {"motivo": msg})
    log.warning("pipeline.falhou", extra={"event": "pipeline.failed", "motivo": msg, **ctx.log_fields()})


def _audit(meta: _Meta, ctx: PipelineContext, action: str, outcome: str, details: dict) -> None:
    with meta.session() as s:
        audit.add(s, action, actor=audit.Actor(email=ctx.triggered_by[:254], role="PIPELINE"),
                  tenant_id=ctx.tenant_id, entity_type="ingestion_run",
                  entity_id=ctx.ingestion_run_id, outcome=outcome,
                  details={**details, "correlation_id": ctx.correlation_id,
                           "source_connection_id": ctx.source_connection_id,
                           **({"job_id": ctx.job_id} if ctx.job_id else {})})
        s.commit()
