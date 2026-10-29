"use client";

/**
 * Admin Works2Data — Integrações / Data Sources (Fase 2).
 *
 * Transparência da carga: nunca "upload realizado com sucesso" só porque o arquivo chegou.
 * Estágios: RECEBIDO → VALIDADO → PROCESSADO → RECONCILIADO → DISPONÍVEL.
 * Segredos nunca são exibidos.
 */

import { FormEvent, useState } from "react";

import { PadroesAns } from "@/components/admin/PadroesAns";
import { TenantPicker } from "@/components/admin/TenantPicker";
import {
  Button,
  Card,
  DataState,
  Dialog,
  Field,
  Input,
  PageHeader,
  StatusBadge,
  Table,
  Td,
  Th,
} from "@/components/ui";
import { apiSend, ApiError, apiUploadFiles, qs } from "@/lib/api";
import { useApi } from "@/lib/useApi";

interface Run {
  id: number;
  source_connection_id: number | null;
  status: string;
  stage: string | null;
  started_at: string | null;
  finished_at: string | null;
  records_received: number;
  records_valid: number;
  records_rejected: number;
  warnings: number;
  errors: number;
  competencia_inicio: string | null;
  competencia_fim: string | null;
  checksum: string | null;
  mapping_ref: string | null;
  triggered_by: string | null;
  duplicate_of: number | null;
  error_summary: Record<string, unknown>;
  correlation_id: string | null;
  reconciliation?: string | null;
  job?: Job | null;
}

interface Job {
  id: number;
  tenant_id: string;
  ingestion_run_id: number;
  status: string;
  attempts: number;
  max_attempts: number;
  created_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  next_attempt_at: string | null;
  duration_ms: number | null;
  last_error: string | null;
  error_class: string | null;
  stuck: boolean;
  can_retry: boolean;
  can_cancel: boolean;
}

const EM_ANDAMENTO = new Set(["QUEUED", "RUNNING"]);
const fmtDur = (ms: number | null | undefined) =>
  ms == null ? "—" : ms < 1000 ? `${ms} ms` : ms < 60000 ? `${(ms / 1000).toFixed(1)} s` : `${Math.round(ms / 60000)} min`;

interface Source {
  id: number;
  tenant_id: string;
  tenant_name?: string;
  name: string;
  source_type: string;
  source_system: string;
  status: string;
  configuration: Record<string, unknown>;
  has_secret: boolean;
  last_run_at: string | null;
  last_success_at: string | null;
  last_error_at: string | null;
  last_error_summary: string | null;
  last_run?: Run | null;
  last_published?: Run | null;
  last_published_reconciliation?: string | null;
  onboarding_state?: string;
}

const STAGES = ["RECEIVED", "VALIDATED", "PROCESSED", "RECONCILED", "AVAILABLE"];
const STAGE_LABEL: Record<string, string> = {
  RECEIVED: "Recebido", VALIDATED: "Validado", PROCESSED: "Processado",
  RECONCILED: "Reconciliado", AVAILABLE: "Disponível",
};
const STATUS_TONE: Record<string, "success" | "warning" | "danger" | "info" | "neutral"> = {
  SUCCESS: "success", PARTIAL: "warning", FAILED: "danger", RUNNING: "info", PENDING: "neutral",
  QUEUED: "neutral", CANCELLED: "neutral",
  PASS: "success", WARNING: "warning", FAIL: "danger",
};
const fmt = (d: string | null) => (d ? new Date(d).toLocaleString("pt-BR") : "—");

export default function IntegracoesPage() {
  const [tenant, setTenant] = useState<string | null>(null);
  return (
    <div className="space-y-4">
      <PageHeader
        title="Integrações / Data Sources"
        description="Fontes de dados por tenant, cargas e sua jornada até o consumo analítico. Toda execução tem tenant explícito e roda com o papel de pipeline (sob RLS)."
        actions={<TenantPicker value={tenant} onChange={setTenant} allowAll />}
      />
      {tenant ? <TenantIntegracoes tenant={tenant} /> : <VisaoGeral />}
    </div>
  );
}

function StageBar({ stage, status }: { stage: string | null; status: string }) {
  const idx = stage ? STAGES.indexOf(stage) : -1;
  return (
    <div className="flex flex-wrap items-center gap-1 text-[11px]">
      {STAGES.map((s, i) => {
        const feito = i <= idx;
        const falhou = status === "FAILED" && i === idx + 1;
        return (
          <span key={s} className={`rounded px-1.5 py-0.5 ${falhou ? "bg-rose-100 text-rose-700" : feito ? "bg-emerald-100 text-emerald-800" : "bg-slate-100 text-slate-400"}`}>
            {feito ? "✓ " : falhou ? "✕ " : ""}{STAGE_LABEL[s]}
          </span>
        );
      })}
    </div>
  );
}

function JobAcoes({ job, onDone }: { job: Job; onDone: () => void }) {
  const [erro, setErro] = useState<string | null>(null);
  const acao = async (tipo: "retry" | "cancel") => {
    setErro(null);
    try {
      await apiSend(`/admin/jobs/${job.id}/${tipo}`, "POST");
      onDone();
    } catch (e) {
      setErro((e as ApiError).message);
    }
  };
  if (!job.can_retry && !job.can_cancel) return null;
  return (
    <span className="whitespace-nowrap">
      {job.can_retry && <Button size="sm" variant="ghost" onClick={() => acao("retry")}>Reexecutar</Button>}
      {job.can_cancel && <Button size="sm" variant="ghost" onClick={() => acao("cancel")}>Cancelar</Button>}
      {erro && <div className="max-w-xs text-[11px] text-rose-700">{erro}</div>}
    </span>
  );
}

function JobInfo({ job }: { job: Job }) {
  return (
    <div className="text-[11px] text-slate-500">
      job #{job.id} · tentativa {job.attempts}/{job.max_attempts}
      {job.stuck && <span className="ml-1 rounded bg-rose-100 px-1 text-rose-700">travado</span>}
      {job.status === "QUEUED" && job.attempts > 0 && job.next_attempt_at && <> · nova tentativa {fmt(job.next_attempt_at)}</>}
      {job.last_error && <div className="max-w-xs text-rose-700">{job.last_error}</div>}
    </div>
  );
}

function Fila() {
  const { data, reload } = useApi<{
    itens: Job[]; resumo: Record<string, number>; workers_ativos: number;
    fila_mais_antiga_segundos: number | null; travados: number;
  }>("/admin/jobs?limit=20", { refreshInterval: 5000 });
  if (!data) return null;
  return (
    <Card title="Fila do pipeline (worker assíncrono)">
      <div className="mb-3 flex flex-wrap gap-2 text-xs">
        {["QUEUED", "RUNNING", "SUCCESS", "PARTIAL", "FAILED", "CANCELLED"].map((s) => (
          <span key={s} className="rounded bg-slate-100 px-2 py-1">{s}: <b>{data.resumo[s] ?? 0}</b></span>
        ))}
        <span className={`rounded px-2 py-1 ${data.workers_ativos ? "bg-emerald-50 text-emerald-800" : "bg-rose-50 text-rose-700"}`}>
          workers ativos: <b>{data.workers_ativos}</b>
        </span>
        {data.travados > 0 && <span className="rounded bg-rose-50 px-2 py-1 text-rose-700">travados: <b>{data.travados}</b></span>}
        {data.fila_mais_antiga_segundos != null && (
          <span className="rounded bg-slate-100 px-2 py-1">mais antigo na fila: <b>{fmtDur(data.fila_mais_antiga_segundos * 1000)}</b></span>
        )}
      </div>
      <Table>
        <thead><tr><Th>Job</Th><Th>Tenant</Th><Th>Status</Th><Th>Tentativas</Th><Th>Início</Th><Th>Duração</Th><Th>Erro</Th><Th /></tr></thead>
        <tbody>
          {data.itens.map((j) => (
            <tr key={j.id}>
              <Td className="font-mono text-xs">#{j.id}<div className="text-slate-400">ingestão #{j.ingestion_run_id}</div></Td>
              <Td className="text-xs">{j.tenant_id}</Td>
              <Td><StatusBadge tone={STATUS_TONE[j.status]}>{j.status}</StatusBadge>{j.stuck && <div className="text-[11px] text-rose-700">travado</div>}</Td>
              <Td className="text-xs">{j.attempts}/{j.max_attempts}</Td>
              <Td className="text-xs">{fmt(j.started_at ?? j.created_at)}</Td>
              <Td className="text-xs">{fmtDur(j.duration_ms)}</Td>
              <Td className="max-w-xs text-xs text-rose-700">{j.last_error ?? "—"}</Td>
              <Td><JobAcoes job={j} onDone={() => reload()} /></Td>
            </tr>
          ))}
        </tbody>
      </Table>
    </Card>
  );
}

function VisaoGeral() {
  const { data, error, isLoading } = useApi<{ itens: Source[] }>("/admin/integrations");
  return (
    <div className="space-y-4">
    <Fila />
    <Card title="Fontes de todos os tenants">
      <DataState isLoading={isLoading && !data} error={error} empty={data?.itens.length === 0}>
        <Table>
          <thead><tr><Th>Tenant</Th><Th>Fonte</Th><Th>Tipo</Th><Th>Status</Th><Th>Última execução</Th><Th>Última carga publicada</Th><Th>Reconciliação</Th><Th>Onboarding</Th></tr></thead>
          <tbody>
            {data?.itens.map((s) => (
              <tr key={`${s.tenant_id}-${s.id}`}>
                <Td>{s.tenant_name}</Td>
                <Td><div className="font-medium">{s.name}</div><div className="font-mono text-[11px] text-slate-400">{s.source_system}</div></Td>
                <Td>{s.source_type}</Td>
                <Td><StatusBadge tone={s.status === "ACTIVE" ? "success" : "neutral"}>{s.status}</StatusBadge></Td>
                <Td className="text-xs">
                  {s.last_run ? (
                    <>
                      <StatusBadge tone={STATUS_TONE[s.last_run.status]}>{s.last_run.status}</StatusBadge>{" "}
                      {fmt(s.last_run.finished_at ?? s.last_run.started_at)}
                      {s.last_run.duplicate_of && <div className="text-slate-400">reenvio idêntico à #{s.last_run.duplicate_of}</div>}
                    </>
                  ) : "nunca executada"}
                </Td>
                <Td className="text-xs">
                  {s.last_published ? (
                    <>
                      #{s.last_published.id} · {fmt(s.last_published.finished_at)}
                      <div className="text-slate-400">
                        {s.last_published.records_valid}/{s.last_published.records_received} válidos · {s.last_published.errors} erros
                        {s.last_published.competencia_inicio && ` · ${s.last_published.competencia_inicio.slice(0, 7)} a ${s.last_published.competencia_fim?.slice(0, 7)}`}
                      </div>
                    </>
                  ) : "nenhuma"}
                </Td>
                <Td>{s.last_published_reconciliation ? <StatusBadge tone={STATUS_TONE[s.last_published_reconciliation]}>{s.last_published_reconciliation}</StatusBadge> : <span className="text-xs text-slate-400">{s.last_published ? "não aplicável" : "—"}</span>}</Td>
                <Td className="text-xs">{s.onboarding_state}</Td>
              </tr>
            ))}
          </tbody>
        </Table>
      </DataState>
    </Card>
    </div>
  );
}

function TenantIntegracoes({ tenant }: { tenant: string }) {
  const base = `/admin/tenants/${tenant}`;
  const fontes = useApi<{ itens: Source[] }>(`${base}/sources`);
  // enquanto houver carga na fila/em execução, a lista se atualiza sozinha
  const runs = useApi<{ itens: Run[] }>(`${base}/ingestion-runs${qs({ limit: 30 })}`, {
    refreshInterval: (d?: { itens: Run[] }) => (d?.itens.some((r) => EM_ANDAMENTO.has(r.status)) ? 3000 : 0),
  });
  const [detalhe, setDetalhe] = useState<number | null>(null);
  const recarregar = () => {
    fontes.reload();
    runs.reload();
  };
  return (
    <div className="space-y-4">
      <Onboarding base={base} />
      <Readiness base={base} />
      <Card title="Fontes" action={<NovaFonte base={base} onCreated={recarregar} />}>
        <DataState isLoading={fontes.isLoading && !fontes.data} error={fontes.error} empty={fontes.data?.itens.length === 0}>
          <Table>
            <thead><tr><Th>Fonte</Th><Th>Tipo</Th><Th>Configuração</Th><Th>Status</Th><Th>Último sucesso</Th><Th>Último erro</Th><Th /></tr></thead>
            <tbody>
              {fontes.data?.itens.map((s) => (
                <tr key={s.id}>
                  <Td><div className="font-medium">{s.name}</div><div className="font-mono text-[11px] text-slate-400">#{s.id} · {s.source_system}</div></Td>
                  <Td>{s.source_type}{s.has_secret && <div className="text-[11px] text-slate-400">credencial no cofre</div>}</Td>
                  <Td className="font-mono text-[11px]">{JSON.stringify(s.configuration)}</Td>
                  <Td><StatusBadge tone={s.status === "ACTIVE" ? "success" : "neutral"}>{s.status}</StatusBadge></Td>
                  <Td className="text-xs">{fmt(s.last_success_at)}</Td>
                  <Td className="max-w-xs text-xs text-rose-700">{s.last_error_summary ?? "—"}</Td>
                  <Td className="whitespace-nowrap text-right">
                    <Button size="sm" variant="ghost" onClick={async () => {
                      const r = await apiSend<{ ok: boolean; detail: string }>(`${base}/sources/${s.id}/validate`, "POST");
                      alert(`${r.ok ? "OK" : "Falhou"}: ${r.detail}`);
                    }}>Validar</Button>
                    {s.source_type !== "SYNTHETIC" && (
                      <Button size="sm" variant="ghost" onClick={async () => {
                        await apiSend(`${base}/sources/${s.id}`, "PATCH", { status: s.status === "ACTIVE" ? "DISABLED" : "ACTIVE" });
                        recarregar();
                      }}>{s.status === "ACTIVE" ? "Desativar" : "Ativar"}</Button>
                    )}
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
        </DataState>
      </Card>
      <Upload base={base} fontes={(fontes.data?.itens ?? []).filter((f) => f.source_type === "FILE" && f.status === "ACTIVE")}
        onDone={recarregar} />
      <PadroesAns base={base} fontes={(fontes.data?.itens ?? []).filter((f) => f.configuration.mapping_id === "ans_padroes" && f.status === "ACTIVE")}
        onDone={recarregar} />
      <Card title="Execuções (ingestões)">
        <DataState isLoading={runs.isLoading && !runs.data} error={runs.error} empty={runs.data?.itens.length === 0}>
          <Table>
            <thead><tr><Th>#</Th><Th>Status</Th><Th>Jornada da carga</Th><Th>Registros</Th><Th>Competências</Th><Th>Reconciliação</Th><Th>Início · duração</Th><Th /></tr></thead>
            <tbody>
              {runs.data?.itens.map((r) => (
                <tr key={r.id}>
                  <Td className="font-mono text-xs">{r.id}</Td>
                  <Td><StatusBadge tone={STATUS_TONE[r.status]}>{r.status}</StatusBadge>{r.job && <JobInfo job={r.job} />}</Td>
                  <Td>{r.duplicate_of
                    ? <span className="text-xs text-slate-500">reenvio idêntico à #{r.duplicate_of} — nada reprocessado</span>
                    : <StageBar stage={r.stage} status={r.status} />}</Td>
                  <Td className="text-xs">{r.records_received} recebidos · {r.records_valid} válidos · {r.records_rejected} rejeitados<div className="text-slate-400">{r.errors} erros · {r.warnings} avisos</div></Td>
                  <Td className="text-xs">{r.competencia_inicio ? `${r.competencia_inicio.slice(0, 7)} a ${r.competencia_fim?.slice(0, 7)}` : "—"}</Td>
                  <Td>{r.reconciliation ? <StatusBadge tone={STATUS_TONE[r.reconciliation]}>{r.reconciliation}</StatusBadge> : "—"}</Td>
                  <Td className="text-xs">{fmt(r.job?.started_at ?? r.started_at)} · {fmtDur(r.job?.duration_ms)}<div className="text-slate-400">{r.triggered_by}</div></Td>
                  <Td className="whitespace-nowrap">
                    <Button size="sm" variant="ghost" onClick={() => setDetalhe(r.id)}>Detalhes</Button>
                    {r.job && <JobAcoes job={r.job} onDone={recarregar} />}
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
        </DataState>
      </Card>
      <Lineage base={base} />
      {detalhe != null && <DetalheRun base={base} id={detalhe} onClose={() => setDetalhe(null)} />}
    </div>
  );
}

function Upload({ base, fontes, onDone }: { base: string; fontes: Source[]; onDone: () => void }) {
  const [fonte, setFonte] = useState<number | null>(null);
  const [arquivos, setArquivos] = useState<FileList | null>(null);
  const [res, setRes] = useState<Record<string, unknown> | null>(null);
  const [erro, setErro] = useState<string | null>(null);
  const [enviando, setEnviando] = useState(false);
  const alvo = fonte ?? fontes[0]?.id ?? null;

  async function enviar(e: FormEvent) {
    e.preventDefault();
    if (!alvo || !arquivos?.length) return;
    setErro(null);
    setRes(null);
    setEnviando(true);
    try {
      const j = await apiUploadFiles<Record<string, unknown>>(`${base}/sources/${alvo}/uploads`, Array.from(arquivos));
      setRes(j);
      onDone();
    } catch (err) {
      setErro((err as Error).message);
    } finally {
      setEnviando(false);
    }
  }

  const st = (res?.status as string) ?? "";
  return (
    <Card title="Importação controlada (fonte FILE)">
      {fontes.length === 0 ? (
        <p className="text-sm text-slate-400">Cadastre uma fonte do tipo FILE ativa para importar arquivos.</p>
      ) : (
        <form onSubmit={enviar} className="flex flex-wrap items-end gap-3">
          <Field label="Fonte">
            {(id) => (
              <select id={id} className="rounded-md border border-slate-300 px-2 py-2 text-sm" value={alvo ?? ""} onChange={(e) => setFonte(Number(e.target.value))}>
                {fontes.map((f) => <option key={f.id} value={f.id}>{f.name} ({String(f.configuration.mapping_id)} v{String(f.configuration.mapping_version)})</option>)}
              </select>
            )}
          </Field>
          <Field label="Arquivos CSV do pacote" hint="Nomes = entidades do mapping (ex.: eventos.csv). Somente .csv; limite de tamanho no servidor.">
            {(id) => <input id={id} type="file" multiple accept=".csv,text/csv" onChange={(e) => setArquivos(e.target.files)} className="text-sm" />}
          </Field>
          <Button type="submit" variant="primary" loading={enviando} disabled={!arquivos?.length}>Processar carga</Button>
        </form>
      )}
      {erro && <p className="mt-3 rounded-md bg-rose-50 px-3 py-2 text-sm text-rose-700">{erro}</p>}
      {res && (
        <div className={`mt-4 space-y-2 rounded-lg border p-4 text-sm ${st === "FAILED" ? "border-rose-200 bg-rose-50" : st === "PARTIAL" ? "border-amber-200 bg-amber-50" : st === "QUEUED" ? "border-slate-200 bg-slate-50" : "border-emerald-200 bg-emerald-50"}`}>
          <div className="flex flex-wrap items-center gap-2">
            <StatusBadge tone={STATUS_TONE[st]}>{st}</StatusBadge>
            <StageBar stage={res.stage as string | null} status={st} />
          </div>
          <div>{String(res.message)}</div>
          {st === "QUEUED" ? (
            <div className="text-xs text-slate-600">
              {String(res.received)} registros recebidos e gravados no RAW · job #{String(res.job_id)} na fila.
              Validação, Silver, Gold e reconciliação rodam no worker — acompanhe o andamento na lista de execuções (atualiza sozinha).
            </div>
          ) : (
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-6">
            {[["Recebidos", res.received], ["Válidos", res.valid], ["Rejeitados", res.rejected], ["Erros", res.errors], ["Avisos", res.warnings], ["Reconciliação", res.reconciliation ?? "—"]].map(([k, v]) => (
              <div key={String(k)} className="rounded bg-white/70 px-2 py-1"><div className="text-[11px] text-slate-500">{String(k)}</div><div className="font-semibold">{String(v)}</div></div>
            ))}
          </div>
          )}
          <div className="text-xs text-slate-500">Ingestão #{String(res.ingestion_run_id)} — veja detalhes (Data Quality, reconciliação, linhagem) na lista abaixo.</div>
        </div>
      )}
    </Card>
  );
}

function DetalheRun({ base, id, onClose }: { base: string; id: number; onClose: () => void }) {
  const { data, error } = useApi<{
    run: Run; steps: { stage: string; status: string; ms: number }[];
    data_quality: { rule_id: string; description: string; entity: string; severity: string; blocking: boolean; checked: number; failed: number }[];
    reconciliation: { check_id: string; entity: string; scope: string; expected: string; actual: string; difference: string; status: string }[];
    lineage: { raw_objects: { entity: string; file_name: string; storage_key: string; sha256: string; records: number }[]; source: { name?: string; source_type?: string } };
  }>(`${base}/ingestion-runs/${id}`);
  return (
    <Dialog open title={`Ingestão #${id}`} onClose={onClose} wide>
      {error && <p className="text-rose-700">{error.message}</p>}
      {data && (
        <div className="space-y-4 text-xs">
          <div className="flex flex-wrap items-center gap-2"><StatusBadge tone={STATUS_TONE[data.run.status]}>{data.run.status}</StatusBadge><StageBar stage={data.run.stage} status={data.run.status} /></div>
          {Object.keys(data.run.error_summary ?? {}).length > 0 && (
            <pre className="whitespace-pre-wrap rounded bg-slate-50 p-2 font-mono">{JSON.stringify(data.run.error_summary, null, 2)}</pre>
          )}
          <div><div className="mb-1 font-semibold">Passos do pipeline</div>
            {data.steps.map((s, i) => <div key={i}>{s.stage} — <b>{s.status}</b> ({s.ms} ms)</div>)}</div>
          <div><div className="mb-1 font-semibold">Data Quality</div>
            <Table><tbody>{data.data_quality.map((d, i) => (
              <tr key={i}><Td><StatusBadge tone={d.severity === "ERROR" ? "danger" : d.severity === "WARNING" ? "warning" : "neutral"}>{d.severity}</StatusBadge></Td>
                <Td className="font-mono">{d.entity}.{d.rule_id}</Td><Td>{d.description}</Td><Td>{d.failed}/{d.checked}{d.blocking && " · BLOQUEIA"}</Td></tr>
            ))}</tbody></Table></div>
          <div><div className="mb-1 font-semibold">Reconciliação</div>
            <Table><tbody>{data.reconciliation.map((c, i) => (
              <tr key={i}><Td><StatusBadge tone={STATUS_TONE[c.status]}>{c.status}</StatusBadge></Td><Td className="font-mono">{c.check_id}</Td><Td>{c.scope}</Td>
                <Td>esperado {c.expected} · obtido {c.actual} · dif {c.difference}</Td></tr>
            ))}</tbody></Table></div>
          <div><div className="mb-1 font-semibold">Linhagem — RAW ({data.lineage.source?.name})</div>
            {data.lineage.raw_objects.map((r) => <div key={r.storage_key} className="font-mono">{r.storage_key} · {r.records} linhas · sha256 {r.sha256.slice(0, 12)}…</div>)}</div>
        </div>
      )}
    </Dialog>
  );
}

function NovaFonte({ base, onCreated }: { base: string; onCreated: () => void }) {
  const [open, setOpen] = useState(false);
  const [f, setF] = useState({ name: "", source_system: "generic_csv", mapping_id: "generic_operator", mapping_version: "1" });
  const [erro, setErro] = useState<string | null>(null);
  return (
    <>
      <Button size="sm" variant="primary" onClick={() => setOpen(true)}>Nova fonte FILE</Button>
      <Dialog open={open} title="Nova fonte de arquivo" onClose={() => setOpen(false)}>
        <form className="space-y-3" onSubmit={async (e) => {
          e.preventDefault();
          setErro(null);
          try {
            await apiSend(`${base}/sources`, "POST", {
              name: f.name, source_type: "FILE", source_system: f.source_system,
              configuration: { mapping_id: f.mapping_id, mapping_version: Number(f.mapping_version) },
            });
            setOpen(false);
            onCreated();
          } catch (err) {
            setErro((err as ApiError).message);
          }
        }}>
          {erro && <p className="rounded-md bg-rose-50 px-3 py-2 text-rose-700">{erro}</p>}
          <Field label="Nome">{(id) => <Input id={id} required minLength={3} value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} />}</Field>
          <Field label="Sistema de origem (rótulo)">{(id) => <Input id={id} required value={f.source_system} onChange={(e) => setF({ ...f, source_system: e.target.value })} />}</Field>
          <Field label="Mapping" hint="arquivo data_platform/mappings/<id>_v<versão>.yaml">{(id) => <Input id={id} required value={f.mapping_id} onChange={(e) => setF({ ...f, mapping_id: e.target.value })} />}</Field>
          <Field label="Versão do mapping">{(id) => <Input id={id} type="number" min={1} value={f.mapping_version} onChange={(e) => setF({ ...f, mapping_version: e.target.value })} />}</Field>
          <p className="text-xs text-slate-500">Credenciais (fontes DATABASE/API) nunca vão na configuração — são guardadas no cofre do tenant e referenciadas.</p>
          <div className="flex justify-end gap-2"><Button type="button" onClick={() => setOpen(false)}>Cancelar</Button><Button type="submit" variant="primary">Cadastrar</Button></div>
        </form>
      </Dialog>
    </>
  );
}

function Onboarding({ base }: { base: string }) {
  const { data, reload } = useApi<{ state: string; steps: { state: string; done: boolean; manual: boolean }[] }>(`${base}/onboarding`);
  const [nota, setNota] = useState("");
  const [erro, setErro] = useState<string | null>(null);
  if (!data) return null;
  const decidir = async (state: string) => {
    setErro(null);
    try {
      await apiSend(`${base}/onboarding`, "POST", { state, note: nota });
      setNota("");
      reload();
    } catch (e) {
      setErro((e as ApiError).message);
    }
  };
  return (
    <Card title={`Onboarding técnico — ${data.state}`}>
      <div className="flex flex-wrap gap-1 text-[11px]">
        {data.steps.map((s) => (
          <span key={s.state} className={`rounded px-1.5 py-0.5 ${s.done ? "bg-emerald-100 text-emerald-800" : "bg-slate-100 text-slate-400"}`}>
            {s.done ? "✓ " : ""}{s.state}{s.manual ? " (manual)" : ""}
          </span>
        ))}
      </div>
      <div className="mt-3 flex flex-wrap items-end gap-2">
        <Field label="Nota da decisão (auditada)">{(id) => <Input id={id} value={nota} onChange={(e) => setNota(e.target.value)} className="w-72" />}</Field>
        <Button size="sm" disabled={nota.length < 3} onClick={() => decidir("HOMOLOGATED")}>Homologar</Button>
        <Button size="sm" variant="primary" disabled={nota.length < 3} onClick={() => decidir("ACTIVE")}>Ativar</Button>
      </div>
      {erro && <p className="mt-2 text-sm text-rose-700">{erro}</p>}
    </Card>
  );
}

function Readiness({ base }: { base: string }) {
  const { data, reload } = useApi<{ itens: { key: string; name: string; entitled: boolean; data_status: string; data_reason: string; available: boolean }[] }>(`${base}/readiness`);
  return (
    <Card title="Capabilities — contratada × dados prontos × disponível" action={
      <Button size="sm" variant="ghost" onClick={async () => { await apiSend(`${base}/readiness/refresh`, "POST"); reload(); }}>Recalcular</Button>}>
      <Table>
        <thead><tr><Th>Capability</Th><Th>Contratada</Th><Th>Dados</Th><Th>Disponível</Th><Th>Motivo</Th></tr></thead>
        <tbody>
          {data?.itens.map((i) => (
            <tr key={i.key}>
              <Td><div className="font-medium">{i.name}</div><div className="font-mono text-[11px] text-slate-400">{i.key}</div></Td>
              <Td>{i.entitled ? "✓" : "—"}</Td>
              <Td><StatusBadge tone={i.data_status === "READY" ? "success" : i.data_status === "PARTIAL" ? "warning" : "danger"}>{i.data_status}</StatusBadge></Td>
              <Td>{i.available ? <StatusBadge tone="success">sim</StatusBadge> : <StatusBadge>não</StatusBadge>}</Td>
              <Td className="max-w-sm text-xs text-slate-500">{i.data_reason || "—"}</Td>
            </tr>
          ))}
        </tbody>
      </Table>
    </Card>
  );
}

function Lineage({ base }: { base: string }) {
  const [comp, setComp] = useState("");
  const [busca, setBusca] = useState<string | null>(null);
  const { data, error } = useApi<Record<string, unknown>>(busca ? `${base}/lineage${qs({ competencia: busca })}` : null);
  return (
    <Card title="Lineage — de onde veio a métrica da competência?">
      <form className="flex items-end gap-2" onSubmit={(e) => { e.preventDefault(); setBusca(comp); }}>
        <Field label="Competência (AAAA-MM)">{(id) => <Input id={id} pattern="\d{4}-\d{2}" value={comp} onChange={(e) => setComp(e.target.value)} className="w-40" />}</Field>
        <Button type="submit" size="sm">Rastrear</Button>
      </form>
      {error && <p className="mt-2 text-sm text-rose-700">{error.message}</p>}
      {data && <pre className="mt-3 max-h-96 overflow-auto rounded bg-slate-50 p-3 font-mono text-[11px]">{JSON.stringify(data, null, 2)}</pre>}
    </Card>
  );
}
