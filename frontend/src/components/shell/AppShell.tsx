"use client";

/**
 * Shell da aplicação + gate de sessão/rota.
 *
 * Ordem de decisão (sempre um estado explícito, nunca tela em branco):
 *   rota pública (/login) → sem shell
 *   sessão carregando     → Loading
 *   anônimo               → redireciona ao login (preserva `next`)
 *   sessão expirada       → SessionExpired
 *   rota de plataforma    → exige SUPER_ADMIN (Forbidden)
 *   rota de tenant        → exige tenant ativo (TenantSuspended / seleção de ambiente)
 *                           + permissão (Forbidden) + feature (FeatureUnavailable)
 * O backend repete todas essas verificações — isto é UX, não segurança.
 */

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { ReactNode, Suspense, useEffect } from "react";

import { DemoBanner } from "@/components/shell/DemoBanner";
import { Header } from "@/components/shell/Header";
import { Sidebar } from "@/components/shell/Sidebar";
import {
  Button,
  DataNotReadyState,
  FeatureUnavailableState,
  ForbiddenState,
  LoadingState,
  SessionExpiredState,
  TenantSuspendedState,
} from "@/components/ui";
import { FiltersProvider } from "@/lib/filters";
import { routeRequirement } from "@/lib/nav";
import { isPublicRoute, SessionProvider, useSession } from "@/lib/session";

export function AppShell({ children }: { children: ReactNode }) {
  return (
    <Suspense fallback={<div className="p-6 text-sm text-slate-400">Carregando…</div>}>
      <SessionProvider>
        <Gate>{children}</Gate>
      </SessionProvider>
    </Suspense>
  );
}

function Gate({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const params = useSearchParams();
  const router = useRouter();
  const s = useSession();

  const publica = isPublicRoute(pathname);
  useEffect(() => {
    if (!publica && s.status === "anonymous") {
      const atual = `${pathname}${params.toString() ? `?${params}` : ""}`;
      router.replace(`/login?next=${encodeURIComponent(atual)}`);
    }
  }, [publica, s.status, pathname, params, router]);

  if (publica) return <>{children}</>;
  if (s.status === "loading" || s.status === "anonymous") {
    return (
      <div className="grid min-h-screen place-items-center bg-slate-100 p-6">
        <div className="w-64"><LoadingState label="Verificando sessão…" /></div>
      </div>
    );
  }
  if (s.status === "expired") {
    return (
      <div className="grid min-h-screen place-items-center bg-slate-100 p-6">
        <div className="w-full max-w-md">
          <SessionExpiredState
            onLogin={() => router.replace(`/login?next=${encodeURIComponent(pathname)}`)}
          />
        </div>
      </div>
    );
  }

  const tenantAtivo = s.me?.tenant?.status === "ACTIVE" && s.me?.role != null;
  return (
    <FiltersProvider
      enabled={tenantAtivo}
      defaultComparacao={(s.me?.settings["analysis.default_comparison"] as string) ?? "mes_anterior"}
    >
      <div className="flex min-h-screen flex-col">
        <div className="flex flex-1">
          <Sidebar />
          <div className="flex min-w-0 flex-1 flex-col">
            {s.me?.tenant?.is_synthetic && <div className="no-print"><DemoBanner /></div>}
            <Header />
            <main className="flex-1 p-6">
              <RouteGate>{children}</RouteGate>
            </main>
            <footer className="no-print border-t border-slate-200 px-6 py-3 text-center text-[11px] text-slate-400">
              {s.me?.branding.product_name ?? "W2Health Intelligence"}
              {s.me?.tenant?.is_synthetic && " · Ambiente demonstrativo com dados sintéticos · Nenhum dado de pessoa real é utilizado."}
            </footer>
          </div>
        </div>
      </div>
    </FiltersProvider>
  );
}

function RouteGate({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const s = useSession();
  const req = routeRequirement(pathname);
  const me = s.me!;

  if (req?.platform) {
    return s.isSuperAdmin ? <>{children}</> : <ForbiddenState />;
  }
  if (req?.tenant) {
    if (!me.tenant) {
      if (s.isSuperAdmin) {
        return (
          <div className="mx-auto max-w-md space-y-3 rounded-xl border border-slate-200 bg-white p-6 text-center text-sm text-slate-600">
            <div className="font-semibold text-slate-900">Selecione um ambiente</div>
            <p>
              Você está no modo plataforma. Para ver dados de uma operadora, selecione o tenant de
              forma explícita — o acesso é registrado na auditoria.
            </p>
            <Button variant="primary" onClick={() => router.push("/admin/tenants")}>Ir para Tenants</Button>
          </div>
        );
      }
      return <ForbiddenState />;
    }
    if (me.tenant.status !== "ACTIVE") return <TenantSuspendedState />;
    if (req.permission && !s.can(req.permission)) return <ForbiddenState />;
    if (req.feature && !s.hasFeature(req.feature)) {
      const cap = s.capability(req.feature);
      // contratada, mas sem dados prontos ≠ fora do plano (o backend já decidiu; aqui só exibimos)
      if (cap?.entitled) return <DataNotReadyState reason={cap.data_reason} />;
      return <FeatureUnavailableState feature={req.feature} />;
    }
  }
  return <>{children}</>;
}
