"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";

import { Breadcrumbs } from "@/components/shell/Breadcrumbs";
import { StatusBadge } from "@/components/ui";
import { fmtCompetencia, LABEL_COMPARACAO } from "@/lib/format";
import { useFilters } from "@/lib/filters";
import { routeRequirement } from "@/lib/nav";
import { ROLE_LABEL, useSession } from "@/lib/session";

function currentTitle(pathname: string): string {
  return routeRequirement(pathname)?.label ?? "W2Health Intelligence";
}

function TenantSwitcher() {
  const s = useSession();
  const me = s.me!;
  const [erro, setErro] = useState<string | null>(null);
  const opcoes = me.memberships.filter((m) => m.tenant_status === "ACTIVE");
  if (!s.isSuperAdmin && opcoes.length <= 1) {
    return me.tenant ? <span className="text-xs font-medium text-slate-600">{me.tenant.name}</span> : null;
  }
  return (
    <label className="flex items-center gap-1.5 text-xs text-slate-500">
      Ambiente
      <select
        className="max-w-[14rem] rounded-md border border-slate-300 bg-white px-2 py-1 text-xs text-slate-700"
        value={me.tenant?.id ?? ""}
        onChange={async (e) => {
          setErro(null);
          try {
            await s.switchTenant(e.target.value || null);
          } catch (err) {
            setErro((err as Error).message);
          }
        }}
      >
        {s.isSuperAdmin && <option value="">— Plataforma (sem tenant) —</option>}
        {me.tenant && !opcoes.some((o) => o.tenant_id === me.tenant!.id) && (
          <option value={me.tenant.id}>{me.tenant.name} (acesso de plataforma)</option>
        )}
        {opcoes.map((o) => (
          <option key={o.tenant_id} value={o.tenant_id}>
            {o.tenant_name}
          </option>
        ))}
      </select>
      {erro && <span className="text-rose-600">{erro}</span>}
    </label>
  );
}

function UserMenu() {
  const s = useSession();
  const [open, setOpen] = useState(false);
  const me = s.me!;
  const papel = s.isSuperAdmin ? "SUPER_ADMIN" : me.role;
  return (
    <div className="relative">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-2 rounded-md border border-slate-200 px-2 py-1 text-xs text-slate-700 hover:bg-slate-50"
        aria-haspopup="menu"
        aria-expanded={open}
      >
        <span className="grid h-6 w-6 place-items-center rounded-full bg-brand-800 text-[10px] font-semibold text-white">
          {me.user.name.slice(0, 2).toUpperCase()}
        </span>
        <span className="hidden sm:inline">{me.user.name}</span>
        {papel && <StatusBadge tone={s.isSuperAdmin ? "gold" : "info"}>{ROLE_LABEL[papel] ?? papel}</StatusBadge>}
      </button>
      {open && (
        <div role="menu" className="absolute right-0 z-30 mt-1 w-56 rounded-lg border border-slate-200 bg-white py-1 text-sm shadow-lg" onMouseLeave={() => setOpen(false)}>
          <div className="border-b border-slate-100 px-3 py-2 text-xs text-slate-500">{me.user.email}</div>
          <Link href="/conta" role="menuitem" className="block px-3 py-2 hover:bg-slate-50" onClick={() => setOpen(false)}>
            Minha conta e segurança
          </Link>
          {!me.user.mfa_enabled && (
            <Link href="/conta" className="block px-3 py-1 text-xs text-amber-700 hover:bg-amber-50" onClick={() => setOpen(false)}>
              ⚠ Ative a autenticação em dois fatores
            </Link>
          )}
          <button role="menuitem" onClick={() => s.logout()} className="block w-full px-3 py-2 text-left text-rose-700 hover:bg-rose-50">
            Sair
          </button>
        </div>
      )}
    </div>
  );
}

export function Header() {
  const pathname = usePathname();
  const f = useFilters();
  const s = useSession();
  const req = routeRequirement(pathname);
  const analitica = Boolean(req?.tenant && req.permission === "analytics:read") && s.me?.tenant?.status === "ACTIVE";

  return (
    <header className="no-print border-b border-slate-200 bg-white">
      <div className="flex flex-wrap items-center justify-between gap-3 px-6 py-3">
        <div>
          <Breadcrumbs />
          <h1 className="text-lg font-semibold text-slate-900">{currentTitle(pathname)}</h1>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          {analitica && (
            <>
              <label className="flex items-center gap-1.5 text-xs text-slate-500">
                Competência
                <select
                  className="rounded-md border border-slate-300 bg-white px-2 py-1 text-xs text-slate-700"
                  value={f.competencia ?? ""}
                  onChange={(e) => f.setCompetencia(e.target.value)}
                >
                  {f.competencias.map((c) => (
                    <option key={c} value={c}>
                      {fmtCompetencia(`${c}-01`)}
                    </option>
                  ))}
                </select>
              </label>
              <label className="flex items-center gap-1.5 text-xs text-slate-500">
                Comparar com
                <select
                  className="rounded-md border border-slate-300 bg-white px-2 py-1 text-xs text-slate-700"
                  value={f.comparacao}
                  onChange={(e) => f.setComparacao(e.target.value)}
                >
                  {Object.entries(LABEL_COMPARACAO).map(([k, v]) => (
                    <option key={k} value={k}>
                      {v}
                    </option>
                  ))}
                </select>
              </label>
            </>
          )}
          {s.me && <TenantSwitcher />}
          {s.me?.tenant?.is_synthetic && (
            <span className="rounded-full bg-gold-50 px-3 py-1 text-xs font-medium text-gold-700 ring-1 ring-gold-400" title="Dados sintéticos — nenhuma pessoa real">
              dados sintéticos
            </span>
          )}
          {s.me && <UserMenu />}
        </div>
      </div>
    </header>
  );
}
