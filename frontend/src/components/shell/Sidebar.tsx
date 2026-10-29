"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { apiAssetUrl } from "@/lib/api";
import { NAV_SECTIONS, NavItem } from "@/lib/nav";
import { useSession } from "@/lib/session";

function isActive(pathname: string, href: string): boolean {
  if (href === "/") return pathname === "/";
  return pathname === href || pathname.startsWith(`${href}/`);
}

export function Sidebar() {
  const pathname = usePathname();
  const s = useSession();
  const me = s.me;
  const b = me?.branding;
  const tenantAtivo = me?.tenant?.status === "ACTIVE";

  // Menu só reflete o que o usuário pode usar. O backend é quem bloqueia de fato.
  const visivel = (item: NavItem) => {
    if (item.platform) return s.isSuperAdmin;
    if (item.tenant && !tenantAtivo) return false;
    if (item.permission && !s.can(item.permission)) return false;
    if (item.feature && !s.hasFeature(item.feature)) return false;
    return true;
  };

  const logo = apiAssetUrl(b?.logo_url);
  const nome = b?.product_name ?? "W2Health Intelligence";

  return (
    <aside className="no-print sticky top-0 flex h-screen w-60 shrink-0 flex-col self-start bg-tenant-primary text-slate-100">
      <div className="flex h-16 items-center gap-3 border-b border-white/10 px-5">
        {logo ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={logo} alt="" className="h-9 w-9 rounded-lg bg-white object-contain p-0.5" />
        ) : (
          <div className="grid h-9 w-9 place-items-center rounded-lg bg-tenant-accent text-sm font-bold text-brand-900">
            W2
          </div>
        )}
        <div className="min-w-0 leading-tight">
          <div className="truncate text-sm font-semibold text-white">{nome}</div>
        </div>
      </div>

      <nav className="flex-1 space-y-4 overflow-y-auto px-3 py-4">
        {NAV_SECTIONS.map((sec) => {
          const itens = sec.items.filter(visivel);
          if (itens.length === 0) return null;
          return (
            <div key={sec.title}>
              <div className="px-3 pb-1 text-[10px] font-semibold uppercase tracking-wider text-slate-500">
                {sec.title}
              </div>
              <div className="space-y-0.5">
                {itens.map((item) => {
                  const active = isActive(pathname, item.href);
                  return (
                    <Link
                      key={item.href}
                      href={item.href}
                      className={[
                        "block rounded-md px-3 py-1.5 text-sm transition-colors",
                        active
                          ? "bg-white/15 font-semibold text-white"
                          : "text-slate-300 hover:bg-white/10 hover:text-white",
                      ].join(" ")}
                    >
                      {item.label}
                    </Link>
                  );
                })}
              </div>
            </div>
          );
        })}
      </nav>

      <div className="space-y-3 border-t border-white/10 px-5 py-3">
        <div className="text-[11px] text-slate-400">
          {me?.tenant ? (
            <>
              {me.tenant.name}
              {me.tenant.plan && <span className="text-slate-500"> · {me.tenant.plan.name}</span>}
            </>
          ) : (
            "Modo plataforma"
          )}
        </div>
        {/* assinatura da empresa (mesmo padrão do Cockpit): logo pequeno no rodapé do menu */}
        <div className="flex flex-col items-center gap-1 text-[10px] text-white/40">
          <span>Powered by</span>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src="/brand/works2data-logo-dark.png" alt="Works2Data" className="h-auto w-full max-w-[120px] opacity-90" />
        </div>
      </div>
    </aside>
  );
}
