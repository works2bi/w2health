/**
 * Navegação: itens da sidebar, requisitos por rota e rótulos para breadcrumbs.
 *
 * `feature` / `permission` / `platform` só ADAPTAM a interface (esconder menu, mostrar
 * "recurso não disponível"). Ocultar menu NÃO é segurança — o backend revalida tudo.
 */

export interface NavItem {
  href: string;
  label: string;
  feature?: string;
  permission?: string;
  /** exige papel de plataforma (SUPER_ADMIN) */
  platform?: boolean;
  /** rota que precisa de um tenant selecionado */
  tenant?: boolean;
}

export interface NavSection {
  title: string;
  items: NavItem[];
}

export const NAV_SECTIONS: NavSection[] = [
  {
    title: "Análise",
    items: [
      { href: "/", label: "Visão Executiva", feature: "executive_overview", permission: "analytics:read", tenant: true },
      { href: "/sinistralidade", label: "Sinistralidade", feature: "loss_ratio_intelligence", permission: "analytics:read", tenant: true },
      { href: "/contratos", label: "Contratos", feature: "contract_intelligence", permission: "analytics:read", tenant: true },
      { href: "/prestadores", label: "Prestadores", feature: "provider_intelligence", permission: "analytics:read", tenant: true },
      { href: "/beneficiarios", label: "Beneficiários", feature: "beneficiary_intelligence", permission: "analytics:read", tenant: true },
      { href: "/insights", label: "Insights & Alertas", feature: "insights", permission: "analytics:read", tenant: true },
      { href: "/planos-acao", label: "Planos de Ação", feature: "insights", permission: "analytics:read", tenant: true },
      { href: "/relatorio", label: "Relatório Mensal", feature: "executive_overview", permission: "analytics:read", tenant: true },
      { href: "/configuracao/insights", label: "Regras de alerta", feature: "alerts", permission: "alert_rules:read", tenant: true },
      { href: "/configuracao/alertas-email", label: "Alertas por E-mail", feature: "alerts", permission: "analytics:read", tenant: true },
    ],
  },
  {
    title: "Gestão do ambiente",
    items: [
      { href: "/gestao", label: "Ambiente", permission: "tenant_users:read", tenant: true },
      { href: "/gestao/usuarios", label: "Usuários", permission: "tenant_users:read", tenant: true },
      { href: "/gestao/identidade", label: "Identidade visual", permission: "tenant_branding:manage", feature: "custom_branding", tenant: true },
      { href: "/gestao/configuracoes", label: "Configurações", permission: "tenant_settings:read", tenant: true },
      { href: "/gestao/auditoria", label: "Auditoria", permission: "tenant_audit:read", tenant: true },
    ],
  },
  {
    title: "Plataforma Works2Data",
    items: [
      { href: "/admin/tenants", label: "Tenants", platform: true },
      { href: "/admin/planos", label: "Planos", platform: true },
      { href: "/admin/features", label: "Features", platform: true },
      { href: "/admin/integracoes", label: "Integrações", platform: true },
      { href: "/admin/usuarios", label: "Usuários", platform: true },
      { href: "/admin/identidade", label: "Branding", platform: true },
      { href: "/admin/configuracoes", label: "Configurações", platform: true },
      { href: "/admin/auditoria", label: "Auditoria", platform: true },
    ],
  },
];

export const NAV_ITEMS: NavItem[] = NAV_SECTIONS.flatMap((s) => s.items);

const EXTRA_ROUTES: NavItem[] = [
  { href: "/conta", label: "Minha conta" },
  { href: "/admin", label: "Plataforma", platform: true },
];

/** Requisito da rota atual (o item de navegação de prefixo mais longo). */
export function routeRequirement(pathname: string): NavItem | null {
  let melhor: NavItem | null = null;
  for (const item of [...NAV_ITEMS, ...EXTRA_ROUTES]) {
    const casa = item.href === "/" ? pathname === "/" : pathname === item.href || pathname.startsWith(`${item.href}/`);
    if (casa && (!melhor || item.href.length > melhor.href.length)) melhor = item;
  }
  return melhor;
}

const ROTULOS_SEGMENTO: Record<string, string> = {
  configuracao: "Configuração",
  gestao: "Gestão do ambiente",
  admin: "Plataforma",
  conta: "Minha conta",
  usuarios: "Usuários",
  identidade: "Identidade visual",
  configuracoes: "Configurações",
  auditoria: "Auditoria",
  planos: "Planos",
  features: "Features",
  tenants: "Tenants",
  integracoes: "Integrações",
  "alertas-email": "Alertas por E-mail",
  "planos-acao": "Planos de Ação",
};

/** Rótulo legível para um segmento de rota (usado nos breadcrumbs). */
export function segmentLabel(segment: string): string {
  if (ROTULOS_SEGMENTO[segment]) return ROTULOS_SEGMENTO[segment];
  const match = NAV_ITEMS.find((item) => item.href === `/${segment}`);
  if (match) return match.label;
  return segment.charAt(0).toUpperCase() + segment.slice(1);
}
