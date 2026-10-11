"""Métricas mínimas em formato Prometheus (texto), sem dependência externa.

* HTTP (por processo da API): contagem por método/rota/classe de status e histograma de
  latência por rota. Rota = TEMPLATE (`/api/contratos/{contrato_id}`), nunca o caminho com
  ids — evita cardinalidade explosiva e não expõe identificadores.
* Fila/worker/pipeline: derivados da tabela `pipeline_jobs` (control plane, só ids e
  contagens) no momento da coleta — valem para todas as réplicas, sem estado em memória.
  Sem rótulo de tenant (cardinalidade e exposição); o detalhe por tenant fica no Admin.

Exposição: `GET /metrics` com `Authorization: Bearer <METRICS_TOKEN>`. Em produção sem
token configurado o endpoint responde 404 (desligado). Prometheus/Grafana/Datadog/
CloudWatch Agent coletam o mesmo formato — escolha de ferramenta é de deployment.
"""

from __future__ import annotations

import threading
from collections import defaultdict

from sqlalchemy import text
from sqlalchemy.orm import Session

#: métodos com rótulo próprio; qualquer outro (o cliente escolhe livremente) vira OTHER —
#: sem isso, métodos arbitrários de clientes não autenticados criariam séries sem limite
_METODOS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"})
_BUCKETS = (0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)
_lock = threading.Lock()
_req_total: dict[tuple[str, str, str], int] = defaultdict(int)
_lat_buckets: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0] * (len(_BUCKETS) + 1))
_lat_sum: dict[tuple[str, str], float] = defaultdict(float)


def observe_http(method: str, route: str, status: int, seconds: float) -> None:
    classe = f"{status // 100}xx"
    method = method if method in _METODOS else "OTHER"
    with _lock:
        _req_total[(method, route, classe)] += 1
        b = _lat_buckets[(method, route)]
        for i, lim in enumerate(_BUCKETS):
            if seconds <= lim:
                b[i] += 1
        b[-1] += 1  # +Inf
        _lat_sum[(method, route)] += seconds


def _esc(v: str) -> str:
    return str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def render_http() -> list[str]:
    out = [
        "# HELP w2h_http_requests_total Requisições HTTP por método, rota e classe de status.",
        "# TYPE w2h_http_requests_total counter",
    ]
    with _lock:
        for (m, r, c), n in sorted(_req_total.items()):
            out.append(
                f'w2h_http_requests_total{{method="{_esc(m)}",route="{_esc(r)}",status="{c}"}} {n}'
            )
        out += [
            "# HELP w2h_http_request_duration_seconds Latência HTTP por rota.",
            "# TYPE w2h_http_request_duration_seconds histogram",
        ]
        for (m, r), b in sorted(_lat_buckets.items()):
            lab = f'method="{_esc(m)}",route="{_esc(r)}"'
            for lim, n in zip(_BUCKETS, b[:-1], strict=True):
                out.append(f'w2h_http_request_duration_seconds_bucket{{{lab},le="{lim}"}} {n}')
            out.append(f'w2h_http_request_duration_seconds_bucket{{{lab},le="+Inf"}} {b[-1]}')
            out.append(f"w2h_http_request_duration_seconds_sum{{{lab}}} {_lat_sum[(m, r)]:.6f}")
            out.append(f"w2h_http_request_duration_seconds_count{{{lab}}} {b[-1]}")
    return out


def render_pipeline(s: Session) -> list[str]:
    out = [
        "# HELP w2h_jobs Jobs do pipeline por status (estado atual da fila).",
        "# TYPE w2h_jobs gauge",
    ]
    for st, n in s.execute(
        text("SELECT status, count(*) FROM pipeline_jobs GROUP BY status")
    ).all():
        out.append(f'w2h_jobs{{status="{st}"}} {n}')
    depth, oldest = s.execute(
        text(
            "SELECT count(*), coalesce(extract(epoch FROM now() - min(created_at)), 0) "
            "FROM pipeline_jobs WHERE status = 'QUEUED'"
        )
    ).one()
    out += [
        "# TYPE w2h_queue_depth gauge",
        f"w2h_queue_depth {depth}",
        "# TYPE w2h_queue_oldest_seconds gauge",
        f"w2h_queue_oldest_seconds {float(oldest):.0f}",
    ]
    retries = s.execute(
        text("SELECT coalesce(sum(greatest(attempts - 1, 0)), 0) FROM pipeline_jobs")
    ).scalar_one()
    out += [
        "# HELP w2h_job_retries_total Tentativas além da primeira (todas as execuções registradas).",
        "# TYPE w2h_job_retries_total counter",
        f"w2h_job_retries_total {retries}",
    ]
    stuck = s.execute(
        text(
            "SELECT count(*) FROM pipeline_jobs WHERE status = 'RUNNING' "
            "AND lease_expires_at < now()"
        )
    ).scalar_one()
    out += ["# TYPE w2h_jobs_stuck gauge", f"w2h_jobs_stuck {stuck}"]
    n, soma = s.execute(
        text(
            "SELECT count(*), coalesce(sum(extract(epoch FROM finished_at - started_at)), 0) FROM pipeline_jobs "
            "WHERE finished_at IS NOT NULL AND started_at IS NOT NULL"
        )
    ).one()
    out += [
        "# TYPE w2h_job_duration_seconds summary",
        f"w2h_job_duration_seconds_sum {float(soma):.3f}",
        f"w2h_job_duration_seconds_count {n}",
    ]
    rec = s.execute(
        text(
            "SELECT coalesce(sum(records_received),0), coalesce(sum(records_valid),0), "
            "coalesce(sum(records_rejected),0) FROM pipeline_jobs"
        )
    ).one()
    out += [
        "# HELP w2h_pipeline_records_total Registros processados pelo pipeline (jobs concluídos).",
        "# TYPE w2h_pipeline_records_total counter",
        f'w2h_pipeline_records_total{{kind="received"}} {rec[0]}',
        f'w2h_pipeline_records_total{{kind="valid"}} {rec[1]}',
        f'w2h_pipeline_records_total{{kind="rejected"}} {rec[2]}',
        "# HELP w2h_pipeline_failures_total Falhas por motivo (dq_blocked, reconciliation_failed, ...).",
        "# TYPE w2h_pipeline_failures_total counter",
    ]
    for motivo, qtd in s.execute(
        text(
            "SELECT failure_reason, count(*) FROM pipeline_jobs "
            "WHERE failure_reason IS NOT NULL GROUP BY failure_reason"
        )
    ).all():
        out.append(f'w2h_pipeline_failures_total{{reason="{_esc(motivo)}"}} {qtd}')
    vivos = s.execute(
        text(
            "SELECT count(*) FROM worker_heartbeats "
            "WHERE last_seen_at > now() - interval '2 minutes'"
        )
    ).scalar_one()
    out += ["# TYPE w2h_workers_alive gauge", f"w2h_workers_alive {vivos}"]
    return out


def reset_http_metrics() -> None:
    with _lock:
        _req_total.clear()
        _lat_buckets.clear()
        _lat_sum.clear()
