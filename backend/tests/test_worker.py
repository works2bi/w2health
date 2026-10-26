"""Fase 3 — fila, worker, isolamento de contexto, idempotência, retry e resiliência.

Todos os cenários usam a fila e o worker REAIS (PostgreSQL + papel w2health_pipeline sob
RLS); falhas de infraestrutura são simuladas por monkeypatch nos pontos de I/O.
"""

from __future__ import annotations

import importlib.util
import logging
import os
import threading
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, OperationalError

from app.core.logging import current_log_context
from app.data_platform import runner
from app.data_platform.paths import data_platform_dir
from app.data_platform.storage import StorageUnavailable, get_raw_storage, set_raw_storage
from app.db.pipeline import PipelineSession
from app.worker.queue import PostgresJobQueue
from app.worker.service import Worker
from tests.conftest import SUPERADMIN_EMAIL, create_tenant, create_user, iso_client

pytestmark = pytest.mark.scenarios

WA, WB = "wk-a", "wk-b"


def _pacote(
    tmp: Path, seed: int, benef: int = 60, meses: int = 3, receitas: bool = True
) -> dict[str, bytes]:
    p = Path(data_platform_dir()) / "examples" / "generic_operator" / "generate_package.py"
    spec = importlib.util.spec_from_file_location("w2h_pkg_wk", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.gerar(tmp, benef, seed, date(2026, 1, 1), meses, receitas)
    return {f.name: f.read_bytes() for f in sorted(tmp.glob("*.csv"))}


@pytest.fixture(scope="module")
def wk(iso_env, tmp_path_factory):
    for t in (WA, WB):
        create_tenant(iso_env, t)
    sa = iso_client(iso_env, SUPERADMIN_EMAIL, None)
    fontes = {}
    for t in (WA, WB):
        r = sa.post(
            f"/api/admin/tenants/{t}/sources",
            json={
                "name": "Pacote CSV",
                "source_type": "FILE",
                "source_system": "generic_csv",
                "configuration": {"mapping_id": "generic_operator", "mapping_version": 1},
            },
        )
        assert r.status_code == 201, r.text
        fontes[t] = r.json()["id"]
    pk = {
        s: _pacote(tmp_path_factory.mktemp(f"p{s}"), seed=s)
        for s in (101, 202, 303, 404, 505, 606, 707)
    }
    return {"sa": sa, "fontes": fontes, "pk": pk, "iso": iso_env}


def _post(wk, tenant: str, files: dict[str, bytes]):
    return wk["sa"].post(
        f"/api/admin/tenants/{tenant}/sources/{wk['fontes'][tenant]}/uploads",
        files=[("files", (n, d, "text/csv")) for n, d in files.items()],
    )


def _enqueue(wk, tenant: str, files: dict[str, bytes]) -> dict:
    r = _post(wk, tenant, files)
    assert r.status_code == 202, r.text
    return r.json()


def _job(wk, job_id: int):
    with wk["iso"].owner() as s:
        return (
            s.execute(text("SELECT * FROM pipeline_jobs WHERE id = :i"), {"i": job_id})
            .mappings()
            .one()
        )


def _run(wk, tenant: str, run_id: int):
    with wk["iso"].owner() as s:
        return (
            s.execute(
                text("SELECT * FROM ingestion_runs WHERE id = :i AND tenant_id = :t"),
                {"i": run_id, "t": tenant},
            )
            .mappings()
            .one()
        )


def _eventos(wk, tenant: str) -> int:
    with wk["iso"].owner() as s:
        return s.execute(
            text("SELECT count(*) FROM eventos_assistenciais WHERE tenant_id = :t"), {"t": tenant}
        ).scalar_one()


@pytest.fixture
def worker(monkeypatch):
    w = Worker(worker_id="pytest-wk")
    monkeypatch.setattr(w, "backoff", lambda attempt: 0)  # retry imediato nos testes
    return w


def _bloqueios(wk) -> list[dict]:
    """Diagnóstico: quem está esperando quem no banco de isolamento (pg_blocking_pids)."""
    with wk["iso"].owner() as s:
        return [dict(r) for r in s.execute(text(
            "SELECT pid, usename, application_name, state, wait_event_type, wait_event, "
            "now() - xact_start AS em_transacao, pg_blocking_pids(pid) AS bloqueado_por, "
            r"left(regexp_replace(query, '\s+', ' ', 'g'), 160) AS consulta "
            "FROM pg_stat_activity WHERE datname = current_database() AND pid <> pg_backend_pid() "
            "")).mappings()]


def _pilhas(threads) -> dict:
    """Diagnóstico: pilha Python de cada thread ainda viva + estado do pool do pipeline."""
    import sys
    import traceback

    from app.db.pipeline import get_pipeline_engine

    frames = sys._current_frames()
    marca = f"{os.sep}app{os.sep}"  # só os quadros do código da aplicação
    out = {t.name: "".join(f for f in traceback.format_stack(frames[t.ident]) if marca in f)[-3000:]
           for t in threads if t.ident in frames}
    out["pool"] = get_pipeline_engine().pool.status()
    out["outras_threads"] = [t.name for t in threading.enumerate()]
    return out


def _limpa_fila(wk):
    """Cada teste começa com a fila sem pendências de testes anteriores."""
    Worker(worker_id="pytest-cleanup").drain()


# ===================================================================== fluxo assíncrono
def test_upload_responde_rapido_com_job_e_nao_processa_na_requisicao(wk):
    _limpa_fila(wk)
    r = _enqueue(wk, WA, wk["pk"][101])
    assert r["status"] == "QUEUED" and r["stage"] == "RECEIVED"
    assert r["job_id"] and r["pipeline_run_id"] and r["ingestion_run_id"]
    assert r["received"] > 0
    j = _job(wk, r["job_id"])
    assert j["status"] == "QUEUED" and j["attempts"] == 0 and j["tenant_id"] == WA
    with wk["iso"].owner() as s:  # nada promovido antes do worker
        n = s.execute(
            text("SELECT count(*) FROM eventos_assistenciais WHERE ingestion_run_id = :i"),
            {"i": r["ingestion_run_id"]},
        ).scalar_one()
    assert n == 0
    # o job só carrega ids técnicos — nenhuma coluna de payload
    assert set(j) >= {
        "tenant_id",
        "source_connection_id",
        "ingestion_run_id",
        "pipeline_run_id",
    }
    assert not any(k in j for k in ("payload", "data", "content", "files"))
    Worker(worker_id="pytest").drain()
    assert _run(wk, WA, r["ingestion_run_id"])["status"] == "SUCCESS"
    assert _job(wk, r["job_id"])["status"] == "SUCCESS"


def test_worker_nao_vaza_contexto_entre_tenants_a_b_a(wk, worker):
    _limpa_fila(wk)
    ra = _enqueue(wk, WA, wk["pk"][202])
    rb = _enqueue(wk, WB, wk["pk"][202])  # MESMOS códigos de negócio (mesmo seed) em B
    ra2 = _enqueue(wk, WA, wk["pk"][303])

    vistos: list[tuple[int | None, str | None]] = []

    class Captura(logging.Handler):
        def emit(self, record):
            ctx = current_log_context()
            if ctx.get("job_id"):
                vistos.append((ctx.get("job_id"), ctx.get("tenant_id")))

    h = Captura()
    lg = logging.getLogger("app")
    nivel = lg.level
    lg.setLevel(logging.INFO)
    lg.addHandler(h)
    try:
        assert worker.drain() == 3
    finally:
        lg.removeHandler(h)
        lg.setLevel(nivel)
    esperado = {ra["job_id"]: WA, rb["job_id"]: WB, ra2["job_id"]: WA}
    assert vistos and all(esperado[j] == t for j, t in vistos), "log de um job com tenant de outro"
    assert not current_log_context().get("tenant_id")  # nada sobrou após os jobs
    with wk["iso"].owner() as s:
        for r, t in ((ra, WA), (rb, WB), (ra2, WA)):
            tenants = set(
                s.execute(
                    text(
                        "SELECT DISTINCT tenant_id FROM eventos_assistenciais WHERE ingestion_run_id = :i"
                    ),
                    {"i": r["ingestion_run_id"]},
                ).scalars()
            )
            assert tenants in ({t}, set()), (r, tenants)
            assert _run(wk, t, r["ingestion_run_id"])["status"] == "SUCCESS"
    # conexão do pool após os jobs: sem tenant algum para o RLS
    with PipelineSession() as s:
        assert (
            s.execute(text("SELECT current_setting('app.tenant_id', true)")).scalar() or ""
        ) == ""
        assert s.execute(text("SELECT count(*) FROM eventos_assistenciais")).scalar_one() == 0


def test_job_adulterado_com_tenant_diferente_falha_sem_tocar_dados(wk, worker):
    _limpa_fila(wk)
    ra = _enqueue(wk, WA, wk["pk"][404])
    antes_b = _eventos(wk, WB)
    with wk[
        "iso"
    ].owner() as s:  # alguém grava na fila um job apontando a ingestão de A como se fosse de B
        forjado = s.execute(
            text(
                "INSERT INTO pipeline_jobs (job_type, tenant_id, source_connection_id, ingestion_run_id, "
                "pipeline_run_id, dedup_key, status) VALUES ('file_ingestion', :b, :src, :ing, :pr, :k, 'QUEUED') "
                "RETURNING id"
            ),
            {
                "b": WB,
                "src": wk["fontes"][WB],
                "ing": ra["ingestion_run_id"],
                "pr": ra["pipeline_run_id"],
                "k": "forjado-1",
            },
        ).scalar_one()
        s.commit()
    assert worker.run_job(forjado)
    j = _job(wk, forjado)
    assert (
        j["status"] == "FAILED"
        and j["error_class"] == "definitive"
        and j["failure_reason"] == "integrity"
    )
    assert "não corresponde" in j["last_error"]
    assert _eventos(wk, WB) == antes_b
    assert _run(wk, WA, ra["ingestion_run_id"])["status"] == "QUEUED"  # A intacta
    worker.drain()
    assert _run(wk, WA, ra["ingestion_run_id"])["status"] == "SUCCESS"


def test_job_duplicado_e_reexecucao_nao_duplicam_dados(wk, worker):
    _limpa_fila(wk)
    r = _enqueue(wk, WB, wk["pk"][505])
    with wk["iso"].owner() as s, pytest.raises(IntegrityError):  # mesmo dedup_key → recusado
        s.execute(
            text(
                "INSERT INTO pipeline_jobs (job_type, tenant_id, source_connection_id, ingestion_run_id, "
                "pipeline_run_id, dedup_key) SELECT job_type, tenant_id, source_connection_id, ingestion_run_id, "
                "pipeline_run_id, dedup_key FROM pipeline_jobs WHERE id = :i"
            ),
            {"i": r["job_id"]},
        )
    worker.drain()
    depois = _eventos(wk, WB)
    with wk["iso"].owner() as s:  # reentrega do mesmo trabalho (ex.: fila reenvia após restart)
        s.execute(
            text(
                "UPDATE pipeline_jobs SET status = 'QUEUED', next_attempt_at = now() WHERE id = :i"
            ),
            {"i": r["job_id"]},
        )
        s.commit()
    worker.drain()
    assert _eventos(wk, WB) == depois
    assert _job(wk, r["job_id"])["status"] == "SUCCESS"


# ===================================================================== retry / falhas
class _StorageFalho:
    """Embrulha o storage real; falha `vezes` leituras com indisponibilidade."""

    def __init__(self, real, vezes: int):
        self.real, self.vezes, self.backend = real, vezes, real.backend

    def get(self, key):
        if self.vezes > 0:
            self.vezes -= 1
            raise StorageUnavailable("simulado")
        return self.real.get(key)

    def __getattr__(self, nome):
        return getattr(self.real, nome)


def test_falha_transitoria_entra_em_retry_e_conclui_sem_duplicar(wk, worker):
    _limpa_fila(wk)
    r = _enqueue(wk, WA, wk["pk"][606])
    real = get_raw_storage()
    set_raw_storage(_StorageFalho(real, vezes=1))
    try:
        assert worker.run_once()
        j = _job(wk, r["job_id"])
        assert j["status"] == "QUEUED" and j["attempts"] == 1 and j["error_class"] == "retryable"
        assert "armazenamento" in j["last_error"]
        run = _run(wk, WA, r["ingestion_run_id"])
        assert (
            run["status"] == "QUEUED" and run["error_summary"]["ultima_tentativa"]["tentativa"] == 1
        )
        worker.drain()
    finally:
        set_raw_storage(real)
    j = _job(wk, r["job_id"])
    assert j["status"] == "SUCCESS" and j["attempts"] == 2
    with wk["iso"].owner() as s:
        n_dq = s.execute(
            text("SELECT count(*) FROM data_quality_results WHERE ingestion_run_id = :i"),
            {"i": r["ingestion_run_id"]},
        ).scalar_one()
        n_rec = s.execute(
            text(
                "SELECT count(*), count(DISTINCT (check_id, scope, entity)) "
                "FROM reconciliation_results WHERE ingestion_run_id = :i"
            ),
            {"i": r["ingestion_run_id"]},
        ).one()
    assert n_dq > 0 and n_rec[0] == n_rec[1]  # tentativa anterior não deixou resultados duplicados


def test_tentativas_esgotadas_terminam_em_failed(wk, worker):
    _limpa_fila(wk)
    r = _enqueue(wk, WB, wk["pk"][707])
    real = get_raw_storage()
    set_raw_storage(_StorageFalho(real, vezes=99))
    try:
        worker.drain()
    finally:
        set_raw_storage(real)
    j = _job(wk, r["job_id"])
    assert j["status"] == "FAILED" and j["attempts"] == j["max_attempts"] == 3
    assert "tentativas esgotadas" in j["last_error"] and j["failure_reason"] == "infrastructure"
    run = _run(wk, WB, r["ingestion_run_id"])
    assert run["status"] == "FAILED" and "Traceback" not in str(run["error_summary"])


def test_banco_indisponivel_no_meio_e_retentavel(wk, worker, monkeypatch, tmp_path):
    _limpa_fila(wk)
    r = _enqueue(wk, WA, _pacote(tmp_path, seed=808))
    original = runner.existing_codes
    chamadas = {"n": 0}

    def cai_uma_vez(*a, **k):
        chamadas["n"] += 1
        if chamadas["n"] == 1:
            raise OperationalError("SELECT", {}, Exception("conexão perdida (simulado)"))
        return original(*a, **k)

    monkeypatch.setattr(runner, "existing_codes", cai_uma_vez)
    worker.drain()
    j = _job(wk, r["job_id"])
    assert j["status"] == "SUCCESS" and j["attempts"] == 2


def test_dq_fail_e_definitivo_sem_retry(wk, worker, tmp_path):
    _limpa_fila(wk)
    files = _pacote(tmp_path, seed=909)
    files["eventos.csv"] = files["eventos.csv"].replace(
        b"\n", b"\nX;", 1
    )  # quebra a 1ª linha de dados
    r = _post(wk, WB, files)
    if r.status_code == 200:  # recusado já na validação do arquivo — também definitivo
        assert r.json()["status"] == "FAILED"
        return
    jid = r.json()["job_id"]
    worker.drain()
    j = _job(wk, jid)
    assert j["status"] == "FAILED" and j["attempts"] == 1 and j["error_class"] == "definitive"


def test_dq_bloqueio_registra_motivo(wk, worker, tmp_path):
    _limpa_fila(wk)
    files = _pacote(tmp_path, seed=919)
    linhas = files["beneficiarios.csv"].decode().splitlines()
    cab = linhas[0].split(";")
    i_uf = cab.index("uf")
    campos = linhas[1].split(";")
    campos[i_uf] = "XX"  # UF inválida → ERROR de DQ → gate bloqueia
    linhas[1] = ";".join(campos)
    files["beneficiarios.csv"] = ("\n".join(linhas) + "\n").encode()
    r = _enqueue(wk, WB, files)
    worker.drain()
    j = _job(wk, r["job_id"])
    assert j["status"] == "FAILED" and j["failure_reason"] == "dq_blocked" and j["attempts"] == 1


def test_reconciliacao_fail_nao_publica(wk, worker, monkeypatch, tmp_path):
    _limpa_fila(wk)
    r = _enqueue(wk, WA, _pacote(tmp_path, seed=929))
    antes = _eventos(wk, WA)
    monkeypatch.setattr(runner.reconciliation, "overall", lambda checks: runner.reconciliation.FAIL)
    worker.drain()
    j = _job(wk, r["job_id"])
    assert j["status"] == "FAILED" and j["failure_reason"] == "reconciliation_failed"
    assert _eventos(wk, WA) == antes
    assert _run(wk, WA, r["ingestion_run_id"])["stage"] == "PROCESSED"


def test_mapping_invalido_apos_enfileirar_e_definitivo(wk, worker, tmp_path):
    _limpa_fila(wk)
    r = _enqueue(wk, WA, _pacote(tmp_path, seed=939))
    with wk["iso"].owner() as s:
        s.execute(
            text("UPDATE source_connections SET configuration = CAST(:c AS json) WHERE id = :i"),
            {
                "c": '{"mapping_id": "generic_operator", "mapping_version": 99}',
                "i": wk["fontes"][WA],
            },
        )
        s.commit()
    try:
        worker.drain()
    finally:
        with wk["iso"].owner() as s:
            s.execute(
                text(
                    "UPDATE source_connections SET configuration = CAST(:c AS json) WHERE id = :i"
                ),
                {
                    "c": '{"mapping_id": "generic_operator", "mapping_version": 1}',
                    "i": wk["fontes"][WA],
                },
            )
            s.commit()
    j = _job(wk, r["job_id"])
    assert j["status"] == "FAILED" and j["error_class"] == "definitive" and j["attempts"] == 1


# ===================================================================== queda do worker / reaper
def test_worker_cai_no_meio_da_carga_e_e_recuperado(wk, worker, monkeypatch, tmp_path):
    _limpa_fila(wk)
    r = _enqueue(wk, WB, _pacote(tmp_path, seed=949))
    antes = _eventos(wk, WB)

    def morre(*a, **k):
        raise SystemExit("processo do worker morto no meio da publicação (simulado)")

    monkeypatch.setattr(runner, "rebuild_aggregations", morre)
    with pytest.raises(SystemExit):  # BaseException: nada trata — como um kill
        worker.run_once()
    monkeypatch.undo()
    j = _job(wk, r["job_id"])
    assert j["status"] == "RUNNING" and j["attempts"] == 1  # ficou "órfão"
    assert _eventos(wk, WB) == antes  # transação de publicação não foi confirmada
    # sem recuperação, ficaria RUNNING para sempre: o lease vence e o reaper devolve à fila
    with wk["iso"].owner() as s:
        s.execute(
            text(
                "UPDATE pipeline_jobs SET lease_expires_at = now() - interval '1 minute' WHERE id = :i"
            ),
            {"i": r["job_id"]},
        )
        s.commit()
    novo = Worker(worker_id="pytest-wk-2")
    novo.backoff = lambda attempt: 0
    assert novo.reap_if_due(force=True) == 1
    j = _job(wk, r["job_id"])
    assert j["status"] == "QUEUED" and "lease expirado" in j["last_error"]
    assert _run(wk, WB, r["ingestion_run_id"])["status"] == "QUEUED"
    novo.drain()
    j = _job(wk, r["job_id"])
    assert j["status"] == "SUCCESS" and j["attempts"] == 2
    with wk["iso"].owner() as s:  # publicado UMA vez, com a linhagem desta ingestão
        n = s.execute(
            text(
                "SELECT count(*), count(DISTINCT source_record_id) FROM eventos_assistenciais "
                "WHERE ingestion_run_id = :i"
            ),
            {"i": r["ingestion_run_id"]},
        ).one()
    run = _run(wk, WB, r["ingestion_run_id"])
    assert n[0] == n[1] > 0 and run["status"] == "SUCCESS"


def test_job_travado_sem_tentativas_vira_failed(wk, monkeypatch, tmp_path):
    _limpa_fila(wk)
    r = _enqueue(wk, WA, _pacote(tmp_path, seed=959))
    q = PostgresJobQueue()
    assert q.claim("worker-que-vai-morrer", 600, job_id=r["job_id"])
    with wk["iso"].owner() as s:
        s.execute(
            text(
                "UPDATE pipeline_jobs SET lease_expires_at = now() - interval '1 minute', "
                "max_attempts = 1 WHERE id = :i"
            ),
            {"i": r["job_id"]},
        )
        s.commit()
    assert Worker(worker_id="pytest-reaper").reap_if_due(force=True) == 1
    j = _job(wk, r["job_id"])
    assert j["status"] == "FAILED" and j["failure_reason"] == "lease_expired"
    assert _run(wk, WA, r["ingestion_run_id"])["status"] == "FAILED"


# ===================================================================== concorrência
def test_cargas_concorrentes_a_b_isoladas_e_processadas_uma_vez(wk, tmp_path_factory):
    _limpa_fila(wk)
    pa = _pacote(tmp_path_factory.mktemp("ca"), seed=4242, benef=80)
    pb = _pacote(tmp_path_factory.mktemp("cb"), seed=4242, benef=80)  # MESMOS ids de negócio
    ra, rb = _enqueue(wk, WA, pa), _enqueue(wk, WB, pb)
    erros: list[BaseException] = []

    def roda(nome):
        try:
            Worker(worker_id=nome).drain()
        except BaseException as e:  # noqa: BLE001
            erros.append(e)

    ts = [threading.Thread(target=roda, args=(f"conc-{i}",)) for i in range(3)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(120)
    # Consolidação V1 — causas da intermitência, medidas e corrigidas:
    # 1) as duas publicações se serializavam no calendário GLOBAL `competencias` (produto —
    #    ver test_calendario_global_nao_serializa_publicacoes);
    # 2) sob a carga da suíte completa, commits esperavam o fsync do WAL do disco virtual
    #    (wait_event = WALSync) — bancos de teste efêmeros agora sem synchronous_commit
    #    (conftest.recreate_database). Se um worker não terminar, a falha traz as pilhas e
    #    as sessões do banco em vez de aparecer como "job ainda RUNNING".
    vivos = [t for t in ts if t.is_alive()]
    assert not vivos, (f"worker não terminou dentro do prazo — pilhas: {_pilhas(vivos)} — "
                       f"sessões no banco: {_bloqueios(wk)}")
    assert not erros
    for r, t in ((ra, WA), (rb, WB)):
        j = _job(wk, r["job_id"])
        assert j["status"] == "SUCCESS" and j["attempts"] == 1  # SKIP LOCKED: um worker por job
        assert _run(wk, t, r["ingestion_run_id"])["status"] == "SUCCESS"
    with wk["iso"].owner() as s:
        for r, t in ((ra, WA), (rb, WB)):
            tenants = set(
                s.execute(
                    text(
                        "SELECT DISTINCT tenant_id FROM eventos_assistenciais "
                        "WHERE ingestion_run_id = :i"
                    ),
                    {"i": r["ingestion_run_id"]},
                ).scalars()
            )
            assert tenants == {t}
        # mesmo beneficiário BEN-... nos dois tenants, cada um com a sua linha
        cod = s.execute(
            text("SELECT codigo FROM beneficiarios WHERE tenant_id = :t ORDER BY codigo LIMIT 1"),
            {"t": WA},
        ).scalar_one()
        assert (
            s.execute(
                text(
                    "SELECT count(DISTINCT tenant_id) FROM beneficiarios WHERE codigo = :c "
                    "AND tenant_id IN (:a, :b)"
                ),
                {"c": cod, "a": WA, "b": WB},
            ).scalar_one()
            == 2
        )


# ===================================================================== operação (Admin)
def test_admin_lista_retry_e_cancel_auditados(wk, tmp_path):
    _limpa_fila(wk)
    sa = wk["sa"]
    r = _enqueue(wk, WA, _pacote(tmp_path, seed=969))
    lista = sa.get("/api/admin/jobs", params={"tenant_id": WA}).json()
    item = next(i for i in lista["itens"] if i["id"] == r["job_id"])
    assert item["status"] == "QUEUED" and item["can_cancel"] and not item["can_retry"]
    assert sa.post(f"/api/admin/jobs/{r['job_id']}/retry").status_code == 409
    c = sa.post(f"/api/admin/jobs/{r['job_id']}/cancel")
    assert c.status_code == 200 and c.json()["status"] == "CANCELLED"
    assert _run(wk, WA, r["ingestion_run_id"])["status"] == "CANCELLED"
    assert sa.post(f"/api/admin/jobs/{r['job_id']}/cancel").status_code == 409
    # travado → retry permitido
    r2 = _enqueue(wk, WA, _pacote(tmp_path / "b", seed=979))
    PostgresJobQueue().claim("worker-morto", 600, job_id=r2["job_id"])
    with wk["iso"].owner() as s:
        s.execute(
            text(
                "UPDATE pipeline_jobs SET lease_expires_at = now() - interval '5 minutes' WHERE id = :i"
            ),
            {"i": r2["job_id"]},
        )
        s.commit()
    travados = sa.get("/api/admin/jobs", params={"stuck": "true"}).json()
    assert any(i["id"] == r2["job_id"] and i["stuck"] and i["can_retry"] for i in travados["itens"])
    rr = sa.post(f"/api/admin/jobs/{r2['job_id']}/retry")
    assert rr.status_code == 200 and rr.json()["status"] == "QUEUED"
    Worker(worker_id="pytest-admin").drain()
    assert _job(wk, r2["job_id"])["status"] == "SUCCESS"
    with wk["iso"].owner() as s:
        acoes = set(
            s.execute(
                text(
                    "SELECT action FROM audit_logs WHERE entity_type = 'pipeline_job' "
                    "AND entity_id IN (:a, :b)"
                ),
                {"a": str(r["job_id"]), "b": str(r2["job_id"])},
            ).scalars()
        )
    assert {"pipeline.job_cancel", "pipeline.job_retry"} <= acoes


def test_operacao_de_jobs_exige_super_admin(wk, iso_env):
    create_user(iso_env, "wk.admin@iso.example", tenant=WA, role="TENANT_ADMIN")
    create_user(iso_env, "wk.viewer@iso.example", tenant=WA, role="VIEWER")
    for email in ("wk.admin@iso.example", "wk.viewer@iso.example"):
        c = iso_client(iso_env, email, WA)
        assert c.get("/api/admin/jobs").status_code == 403
        assert c.post("/api/admin/jobs/1/retry").status_code == 403
        r = c.post(
            f"/api/admin/tenants/{WA}/sources/{wk['fontes'][WA]}/uploads",
            files=[("files", ("eventos.csv", b"a;b\n1;2\n", "text/csv"))],
        )
        assert r.status_code == 403  # viewer/tenant admin não disparam carga


def test_storage_indisponivel_no_upload_responde_503_sem_job(wk, tmp_path):
    _limpa_fila(wk)

    class Fora:
        backend = "local"

        def put(self, *a, **k):
            raise StorageUnavailable("simulado")

    real = get_raw_storage()
    set_raw_storage(Fora())
    try:
        r = _post(wk, WA, _pacote(tmp_path, seed=989))
    finally:
        set_raw_storage(real)
    assert r.status_code == 503 and r.json()["code"] == "service_unavailable"
    with wk["iso"].owner() as s:
        ultimo = s.execute(
            text(
                "SELECT id, status FROM ingestion_runs WHERE tenant_id = :t ORDER BY id DESC "
                "LIMIT 1"
            ),
            {"t": WA},
        ).one()
        jobs = s.execute(
            text("SELECT count(*) FROM pipeline_jobs WHERE ingestion_run_id = :i"), {"i": ultimo.id}
        ).scalar_one()
    assert ultimo.status == "FAILED" and jobs == 0


def test_lease_e_renovado_durante_passo_longo(wk, monkeypatch, tmp_path):
    """Um passo mais longo que o lease não pode parecer worker morto."""
    import time as _t

    _limpa_fila(wk)
    r = _enqueue(wk, WA, _pacote(tmp_path, seed=999))
    w = Worker(worker_id="pytest-lease")
    monkeypatch.setattr(w.settings, "job_lease_seconds", 2)
    original = runner.rebuild_aggregations
    vistos = []

    def gold_lento(*a, **k):
        for _ in range(3):  # ~4,5 s > lease de 2 s
            _t.sleep(1.5)
            with wk["iso"].owner() as s:
                vistos.append(
                    s.execute(
                        text("SELECT lease_expires_at > now() FROM pipeline_jobs WHERE id = :i"),
                        {"i": r["job_id"]},
                    ).scalar_one()
                )
        return original(*a, **k)

    monkeypatch.setattr(runner, "rebuild_aggregations", gold_lento)
    w.drain()
    assert vistos and all(vistos), vistos  # o lease nunca venceu durante o passo
    j = _job(wk, r["job_id"])
    assert j["status"] == "SUCCESS" and j["attempts"] == 1


def test_calendario_global_nao_serializa_publicacoes(iso_env):
    """Causa raiz do teste intermitente acima: o Silver gravava o calendário GLOBAL
    (`competencias`) DENTRO da transação de publicação — uma segunda carga com os mesmos
    meses esperava a primeira commitar. Agora o calendário é gravado numa transação própria:
    com uma publicação ABERTA que já registrou o mês, outra conexão grava o mesmo mês sem
    esperar (lock_timeout curto prova a ausência de bloqueio)."""
    from datetime import date

    from app.data_platform.context import PipelineContext
    from app.data_platform.silver import SilverLoader
    from app.db.pipeline import PipelineSession
    from app.db.tenant_scope import bind_tenant

    mes = date(2031, 7, 1)  # mês ainda inexistente no calendário
    with iso_env.owner() as s:
        s.execute(text("DELETE FROM competencias WHERE competencia = :m"), {"m": mes})
        s.commit()
    pub = PipelineSession()
    try:
        bind_tenant(pub, WA)
        pub.execute(text("SELECT 1"))  # transação de publicação ABERTA
        SilverLoader(pub, PipelineContext(WA, None, "pytest"), "teste").ensure_competencias({mes})
        with iso_env.owner() as outra:
            outra.execute(text("SET LOCAL lock_timeout = '2s'"))
            outra.execute(text(
                "INSERT INTO competencias (competencia, ano, mes, mes_nome, trimestre, is_inverno) "
                "VALUES (:m, 2031, 7, 'Julho', 3, true) ON CONFLICT DO NOTHING"), {"m": mes})
            outra.commit()
        with iso_env.owner() as s:  # visível fora da publicação ainda aberta
            assert s.execute(text("SELECT count(*) FROM competencias WHERE competencia = :m"),
                             {"m": mes}).scalar_one() == 1
    finally:
        pub.rollback()
        pub.close()
        with iso_env.owner() as s:
            s.execute(text("DELETE FROM competencias WHERE competencia = :m"), {"m": mes})
            s.commit()
