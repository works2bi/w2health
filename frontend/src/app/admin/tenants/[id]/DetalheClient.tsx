"use client";

/**
 * Visão completa de um tenant: Identificação · Plano · Status · Features · Usuários ·
 * Branding · Configurações · Segredos · Auditoria recente. Toda mudança exige motivo
 * quando relevante e é auditada pelo backend.
 */

import { FormEvent, useState } from "react";

import { AuditItem, AuditTable } from "@/components/admin/AuditTable";
import { BrandingEditor } from "@/components/admin/BrandingEditor";
import { SettingsEditor } from "@/components/admin/SettingsEditor";
import { UsersPanel } from "@/components/admin/UsersPanel";
import {
  Button,
  Card,
  DataState,
  Dialog,
  EntityStatus,
  Field,
  Input,
  PageHeader,
  Select,
  StatusBadge,
  Table,
  Td,
  Th,
} from "@/components/ui";
import { apiSend, ApiError } from "@/lib/api";
import { useSession } from "@/lib/session";
import { useApi } from "@/lib/useApi";
import { useRouteId } from "@/lib/useRouteId";

import type { TenantSummary } from "../page";

interface FeatureRes {
  key: string;
  name: string;
  enabled: boolean;
  source: string;
  override: boolean | null;
  in_plan: boolean;
  global_active: boolean;
  reason: string;
}

interface Overview {
  tenant: TenantSummary;
  features: FeatureRes[];
  secrets: { key: string; created_at: string | null; rotated_at: string | null }[];
  recent_audit: AuditItem[];
}

const SOURCE_LABEL: Record<string, string> = {
  plan: "plano",
  tenant_override: "override",
  not_in_plan: "fora do plano",
  no_plan: "sem plano",
  global_disabled: "desligada globalmente",
};

type Pendente =
  | { tipo: "status"; valor: string }
  | { tipo: "feature"; key: string; valor: boolean | null }
  | null;

export default function AdminTenantPage() {
  const id = useRouteId();
  const s = useSession();
  const base = `/admin/tenants/${id}`;
  const { data, error, isLoading, reload } = useApi<Overview>(base);
  const planos = useApi<{ plans: { code: string; name: string; is_active: boolean }[] }>("/admin/plans");
  const [pendente, setPendente] = useState<Pendente>(null);
  const [motivo, setMotivo] = useState("");
  const [erro, setErro] = useState<string | null>(null);

  async function exec(fn: () => Promise<unknown>) {
    setErro(null);
    try {
      await fn();
      await reload();
    } catch (e) {
      setErro((e as ApiError).message);
    }
  }

  async function confirmar(e: FormEvent) {
    e.preventDefault();
    if (!pendente) return;
    await exec(() =>
      pendente.tipo === "status"
        ? apiSend(`${base}/status`, "POST", { status: pendente.valor, reason: motivo })
        : apiSend(`${base}/features/${pendente.key}`, "PUT", { enabled: pendente.valor, reason: motivo }),
    );
    setPendente(null);
    setMotivo("");
  }

  const t = data?.tenant;
  return (
    <div className="space-y-4">
      <DataState isLoading={isLoading && !data} error={error}>
        {t && data && (
          <>
            <PageHeader
              title={<span className="flex items-center gap-2">{t.name} <EntityStatus status={t.status} />{t.is_synthetic && <StatusBadge tone="gold">sintético</StatusBadge>}</span>}
              description={<span className="font-mono text-xs">{t.code} · {t.uuid}</span>}
              actions={
                <>
                  {t.status === "ACTIVE" ? (
                    <Button variant="danger" onClick={() => setPendente({ tipo: "status", valor: "SUSPENDED" })}>Suspender</Button>
                  ) : (
                    <Button onClick={() => setPendente({ tipo: "status", valor: "ACTIVE" })}>Reativar</Button>
                  )}
                  <Button variant="primary" disabled={t.status !== "ACTIVE"}
                    title="Acesso de plataforma aos dados — registrado na auditoria"
                    onClick={() => exec(() => s.switchTenant(t.id))}>
                    Acessar ambiente
                  </Button>
                </>
              }
            />
            {erro && <p className="rounded-md bg-rose-50 px-3 py-2 text-sm text-rose-700">{erro}</p>}

            <div className="grid gap-4 lg:grid-cols-2">
              <Identificacao base={base} t={t} onSaved={reload} />
              <Card title="Plano">
                <div className="flex items-center gap-2">
                  <Select value={t.plan?.code ?? ""} className="w-60" onChange={(e) => exec(() => apiSend(`${base}/plan`, "PUT", { plan_code: e.target.value || null }))}>
                    <option value="">— sem plano —</option>
                    {(planos.data?.plans ?? []).map((p) => <option key={p.code} value={p.code} disabled={!p.is_active}>{p.name}</option>)}
                  </Select>
                  <span className="text-xs text-slate-400">{t.active_users} usuário(s) ativo(s)</span>
                </div>
                <p className="mt-2 text-xs text-slate-400">O plano define o conjunto padrão de features; overrides abaixo ajustam este tenant.</p>
              </Card>
            </div>

            <Card title="Features (resolução efetiva)">
              <Table>
                <thead><tr><Th>Feature</Th><Th>No plano</Th><Th>Override</Th><Th>Efetiva</Th><Th>Origem</Th></tr></thead>
                <tbody>
                  {data.features.map((f) => (
                    <tr key={f.key}>
                      <Td><div className="font-medium">{f.name}</div><div className="font-mono text-[11px] text-slate-400">{f.key}</div></Td>
                      <Td>{f.in_plan ? "✓" : "—"}</Td>
                      <Td>
                        <Select className="w-40 py-1 text-xs" value={f.override == null ? "" : f.override ? "on" : "off"}
                          onChange={(e) => setPendente({ tipo: "feature", key: f.key, valor: e.target.value === "" ? null : e.target.value === "on" })}>
                          <option value="">Seguir o plano</option>
                          <option value="on">Forçar habilitada</option>
                          <option value="off">Forçar desabilitada</option>
                        </Select>
                        {f.reason && <div className="mt-0.5 text-[11px] text-slate-400">{f.reason}</div>}
                      </Td>
                      <Td>{f.enabled ? <StatusBadge tone="success">habilitada</StatusBadge> : <StatusBadge>desabilitada</StatusBadge>}</Td>
                      <Td className="text-xs text-slate-500">{SOURCE_LABEL[f.source] ?? f.source}</Td>
                    </tr>
                  ))}
                </tbody>
              </Table>
            </Card>

            <UsersPanel base={base} resetBase="/admin/users" currentUserId={s.me?.user.id} canManage />
            <BrandingEditor base={`${base}/branding`} />
            <SettingsEditor base={`${base}/settings`} />
            <Segredos base={base} itens={data.secrets} onSaved={reload} />
            <Card title="Auditoria recente">
              {data.recent_audit.length ? <AuditTable itens={data.recent_audit} /> : <p className="text-sm text-slate-400">Sem eventos.</p>}
            </Card>
          </>
        )}
      </DataState>

      <Dialog open={pendente != null} title="Confirmar alteração" onClose={() => setPendente(null)}>
        <form onSubmit={confirmar} className="space-y-3">
          <p>
            {pendente?.tipo === "status"
              ? `Alterar status para ${pendente.valor === "SUSPENDED" ? "SUSPENSO (bloqueia todos os usuários)" : "ATIVO"}.`
              : pendente?.tipo === "feature"
                ? `Override de ${pendente.key}: ${pendente.valor == null ? "seguir o plano" : pendente.valor ? "habilitar" : "desabilitar"}.`
                : ""}
          </p>
          <Field label="Motivo (registrado na auditoria)">{(fid) => <Input id={fid} required minLength={3} value={motivo} onChange={(e) => setMotivo(e.target.value)} />}</Field>
          <div className="flex justify-end gap-2">
            <Button type="button" onClick={() => setPendente(null)}>Cancelar</Button>
            <Button type="submit" variant="primary">Confirmar</Button>
          </div>
        </form>
      </Dialog>
    </div>
  );
}

function Identificacao({ base, t, onSaved }: { base: string; t: TenantSummary; onSaved: () => void }) {
  const [nome, setNome] = useState(t.name);
  const [razao, setRazao] = useState(t.legal_name ?? "");
  const [msg, setMsg] = useState<string | null>(null);
  return (
    <Card title="Identificação">
      <form className="space-y-3" onSubmit={async (e) => {
        e.preventDefault();
        setMsg(null);
        try {
          await apiSend(base, "PATCH", { name: nome, legal_name: razao || null });
          onSaved();
          setMsg("Salvo.");
        } catch (err) {
          setMsg((err as ApiError).message);
        }
      }}>
        <Field label="Nome">{(id) => <Input id={id} required minLength={2} value={nome} onChange={(e) => setNome(e.target.value)} />}</Field>
        <Field label="Razão social">{(id) => <Input id={id} value={razao} onChange={(e) => setRazao(e.target.value)} />}</Field>
        <div className="flex items-center gap-3"><Button type="submit">Salvar</Button>{msg && <span className="text-xs text-slate-500">{msg}</span>}</div>
      </form>
    </Card>
  );
}

function Segredos({ base, itens, onSaved }: {
  base: string;
  itens: { key: string; created_at: string | null; rotated_at: string | null }[];
  onSaved: () => void;
}) {
  const [chave, setChave] = useState("");
  const [valor, setValor] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  return (
    <Card title="Segredos de integração (write-only)">
      <p className="mb-3 text-xs text-slate-500">Valores são cifrados no servidor e <b>nunca</b> são exibidos de volta — apenas criados, rotacionados ou removidos.</p>
      {itens.length > 0 && (
        <Table>
          <thead><tr><Th>Chave</Th><Th>Criado</Th><Th>Rotacionado</Th><Th /></tr></thead>
          <tbody>
            {itens.map((s) => (
              <tr key={s.key}>
                <Td className="font-mono text-xs">{s.key}</Td>
                <Td className="text-xs">{s.created_at ? new Date(s.created_at).toLocaleString("pt-BR") : "—"}</Td>
                <Td className="text-xs">{s.rotated_at ? new Date(s.rotated_at).toLocaleString("pt-BR") : "—"}</Td>
                <Td className="text-right"><Button size="sm" variant="danger" onClick={async () => { await apiSend(`${base}/secrets/${s.key}`, "DELETE"); onSaved(); }}>Remover</Button></Td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
      <form className="mt-3 grid gap-2 sm:grid-cols-[14rem_1fr_auto]" onSubmit={async (e) => {
        e.preventDefault();
        setMsg(null);
        try {
          await apiSend(`${base}/secrets/${chave}`, "PUT", { value: valor });
          setChave(""); setValor("");
          onSaved();
        } catch (err) {
          setMsg((err as ApiError).message);
        }
      }}>
        <Input placeholder="chave (ex.: erp.api_token)" required value={chave} onChange={(e) => setChave(e.target.value)} />
        <Input type="password" autoComplete="off" placeholder="valor" required value={valor} onChange={(e) => setValor(e.target.value)} />
        <Button type="submit">Gravar</Button>
      </form>
      {msg && <p className="mt-2 text-xs text-rose-700">{msg}</p>}
    </Card>
  );
}
