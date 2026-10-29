/**
 * Cliente da API do W2Health Intelligence.
 *
 * Fundação SaaS V1 — segurança no transporte:
 * - o access token (JWT curto) vive SÓ em memória (nunca em localStorage/sessionStorage);
 * - o refresh token é um cookie httpOnly/SameSite=strict restrito a `/api/auth` — o JS
 *   nunca o lê; `credentials: "include"` só é usado nas rotas de autenticação;
 * - 401 dispara UMA renovação (promessa compartilhada); falhando, emite o evento
 *   `w2h:session-expired` e o shell mostra o estado "Sessão expirada";
 * - o frontend NÃO é autoridade de segurança: ele só melhora a UX. O backend revalida
 *   tenant, papel, feature e escopo em toda requisição.
 */

export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8010/api";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status?: number,
    readonly code?: string,
    readonly feature?: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export const SESSION_EXPIRED_EVENT = "w2h:session-expired";

let accessToken: string | null = null;
let refreshing: Promise<boolean> | null = null;

export function setAccessToken(token: string | null): void {
  accessToken = token;
}

export function hasAccessToken(): boolean {
  return accessToken != null;
}

/** Renova o access token pelo cookie httpOnly. Uma única chamada em voo por vez. */
export function refreshSession(): Promise<boolean> {
  if (!refreshing) {
    refreshing = (async () => {
      try {
        const r = await fetch(`${API_BASE_URL}/auth/refresh`, {
          method: "POST",
          credentials: "include",
          headers: { Accept: "application/json" },
          cache: "no-store",
        });
        if (!r.ok) {
          accessToken = null;
          return false;
        }
        const j = await r.json();
        accessToken = j.access_token ?? null;
        return accessToken != null;
      } catch {
        return false;
      } finally {
        setTimeout(() => {
          refreshing = null;
        }, 0);
      }
    })();
  }
  return refreshing;
}

async function toError(resp: Response): Promise<ApiError> {
  let detail = `HTTP ${resp.status}`;
  let code: string | undefined;
  let feature: string | undefined;
  try {
    const j = await resp.json();
    if (j?.detail) detail = typeof j.detail === "string" ? j.detail : "Requisição inválida.";
    code = j?.code;
    feature = j?.feature;
  } catch {
    /* corpo não-JSON */
  }
  return new ApiError(detail, resp.status, code, feature);
}

const AUTH_PATHS = ["/auth/login", "/auth/refresh", "/auth/logout", "/auth/mfa/verify",
  "/auth/password/required-change", "/auth/mfa/confirm"];

async function request<T>(path: string, init: RequestInit, retry = true): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  if (accessToken) headers.set("Authorization", `Bearer ${accessToken}`);
  const isAuth = AUTH_PATHS.some((p) => path.startsWith(p));
  let resp: Response;
  try {
    resp = await fetch(`${API_BASE_URL}${path}`, {
      ...init,
      headers,
      cache: "no-store",
      credentials: isAuth ? "include" : "same-origin",
    });
  } catch {
    throw new ApiError("Não foi possível conectar ao serviço. Verifique sua conexão.", 0, "network");
  }
  if (resp.status === 401 && retry && !isAuth) {
    if (await refreshSession()) return request<T>(path, init, false);
    if (typeof window !== "undefined") window.dispatchEvent(new Event(SESSION_EXPIRED_EVENT));
  }
  if (!resp.ok) throw await toError(resp);
  if (resp.status === 204) return undefined as T;
  return (await resp.json()) as T;
}

export async function apiGet<T = unknown>(path: string): Promise<T> {
  return request<T>(path, { method: "GET" });
}

/** POST/PUT/PATCH/DELETE com corpo JSON. */
export async function apiSend<T = unknown>(
  path: string,
  method: "POST" | "PUT" | "PATCH" | "DELETE",
  body?: unknown,
): Promise<T> {
  return request<T>(path, {
    method,
    headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
}

/** Upload multipart (logo/favicon). O tipo é validado pelo backend pelos bytes. */
export async function apiUpload<T = unknown>(path: string, file: File): Promise<T> {
  const fd = new FormData();
  fd.append("file", file);
  return request<T>(path, { method: "POST", body: fd });
}

/** Upload multipart de vários arquivos no campo `files` (pacote de carga de dados). */
export async function apiUploadFiles<T = unknown>(path: string, files: File[]): Promise<T> {
  const fd = new FormData();
  files.forEach((f) => fd.append("files", f));
  return request<T>(path, { method: "POST", body: fd });
}

/** POST multipart com um FormData montado pelo chamador (padrões ANS, DIOPS). */
export async function apiForm<T = unknown>(path: string, fd: FormData): Promise<T> {
  return request<T>(path, { method: "POST", body: fd });
}

export const qs = (params: Record<string, string | number | null | undefined>): string => {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v != null && v !== "") p.set(k, String(v));
  const s = p.toString();
  return s ? `?${s}` : "";
};

/** URL absoluta de um asset servido pela API (ex.: logo do tenant). */
export const apiAssetUrl = (path: string | null | undefined): string | null =>
  path ? `${API_BASE_URL}${path}` : null;

// ------------------------------------------------------------------- tipos parciais
export interface HealthResponse {
  status: string;
  service: string;
  version: string;
  environment: string;
  database: string;
  timestamp: string;
}

export interface Bridge {
  delta_total: number;
  efeito_frequencia: number;
  efeito_custo_medio: number;
  interacao: number;
  metodo: string;
  efeito_principal: "frequencia" | "custo_medio" | "misto";
  qtd_anterior: number;
  qtd_atual: number;
  custo_medio_anterior: number;
  custo_medio_atual: number;
  variacao_frequencia_pct: number | null;
  variacao_custo_medio_pct: number | null;
}

export interface Fator {
  chave: string;
  categoria: string;
  despesa_anterior: number;
  despesa_atual: number;
  impacto_financeiro: number;
  impacto_pp: number;
  efeito_principal: "frequencia" | "custo_medio" | "misto";
  participacao_variacao: number;
  bridge: Bridge;
}

export interface Insight {
  id: string;
  tipo: string;
  severidade: string;
  emoji: string;
  titulo: string;
  descricao: string;
  metricas: Record<string, unknown>;
  deep_link: { rota: string; params: Record<string, string> };
  score: number;
  metodologia: string;
}

export const insightHref = (i: Insight): string => {
  const p = new URLSearchParams(i.deep_link.params as Record<string, string>);
  const s = p.toString();
  return `${i.deep_link.rota}${s ? `?${s}` : ""}`;
};

// ------------------------------------------------------------- v1.2: contratos + C1
export interface ContratoResumo {
  id_contrato: number;
  nome: string;
  tipo: string;
  plano: string;
  vidas: number;
  despesa_bruta: number;
  glosas: number;
  coparticipacao: number;
  despesa_liquida: number;
  custo_pmpm: number;
  eventos: number;
  gini: number;
  top5_share: number;
  n_beneficiarios_alto_custo: number;
  variacao_despesa_liquida_pct: number | null;
  variacao_custo_pmpm_pct: number | null;
  // Consolidação do canônico V1
  empresa: { id: number; nome: string } | null;
  sinistralidade_contratual: SinistralidadeContratual;
}

/** Sinistralidade de contrato: só quando a Gold a publicou (receita reconhecida atribuída,
 *  sem divergência, sem receita consolidada não detalhada da empresa). Senão, o motivo. */
export interface SinistralidadeContratual {
  disponivel: boolean;
  valor: number | null;
  receita_reconhecida: number | null;
  despesa_liquida?: number;
  motivo: string | null;
  formula?: string;
}

export interface EmpresaResumo {
  id: number;
  codigo: string;
  nome: string;
  tipo: string | null;
  n_contratos: number;
  vidas: number;
  despesa_liquida: number;
  receita_contratos: number;
  receita_consolidada_empresa: number;
  receita_total: number;
  sinistralidade_liquida: number | null;
  motivo_sem_sinistralidade: string | null;
}

export interface ContratosLista {
  competencia: string;
  comparacao: string;
  receita_disponivel: boolean;
  aviso: string | null;
  total: number;
  itens: ContratoResumo[];
}

export interface ConcentracaoVariacao {
  competencia: string;
  comparacao: string;
  delta_total_liquido: number;
  delta_positivo_total: number;
  n_beneficiarios_com_aumento: number;
  n_para_credito_50pct: number;
  top5_share_do_aumento: number;
  gini_do_aumento: number;
  top: {
    id: number;
    codigo: string;
    id_contrato: number | null;
    delta: number;
    participacao_pct: number;
  }[];
  deep_link: { rota: string; params: Record<string, string> };
  metodologia: string;
}
