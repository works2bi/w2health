/**
 * Next.js — configuração de produção e headers de segurança (Fase 3).
 *
 * CSP: o App Router injeta scripts inline de bootstrap/hidratação, por isso `script-src`
 * mantém 'unsafe-inline' (nonce exigiria middleware e renderização dinâmica de todas as
 * páginas — avaliado e documentado em docs/PENTEST_READINESS.md como risco residual).
 * Mitigações: nenhum HTML/JS vem do cliente (React escapa texto), `connect-src` restrito
 * à própria origem + API, `frame-ancestors 'none'`, `object-src 'none'`, `base-uri 'self'`.
 * HSTS: ligado com ENABLE_HSTS=true no build (só atrás de TLS; normalmente o proxy envia).
 *
 * NEXT_OUTPUT=export: build estático (`out/`) servido por um proxy (nginx), sem Node em
 * runtime. `headers()` não se aplica a export — o proxy precisa enviar os mesmos headers
 * de segurança (ver docs/DEPLOY_VPS.md). Sem a variável, o build segue `standalone`.
 */
const api = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000/api";
let apiOrigin = "";
try {
  apiOrigin = new URL(api).origin;
} catch {
  apiOrigin = "";
}
const dev = process.env.NODE_ENV !== "production";
const exportEstatico = process.env.NEXT_OUTPUT === "export";

const csp = [
  "default-src 'self'",
  `script-src 'self' 'unsafe-inline'${dev ? " 'unsafe-eval'" : ""}`,
  "style-src 'self' 'unsafe-inline'",
  `img-src 'self' data: blob: ${apiOrigin}`.trim(),
  "font-src 'self' data:",
  `connect-src 'self' ${apiOrigin}${dev ? " ws: http://localhost:*" : ""}`.trim(),
  "frame-ancestors 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "object-src 'none'",
].join("; ");

const securityHeaders = [
  { key: "Content-Security-Policy", value: csp },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=(), payment=(), usb=()" },
  { key: "Cross-Origin-Opener-Policy", value: "same-origin" },
  ...(process.env.ENABLE_HSTS === "true"
    ? [{ key: "Strict-Transport-Security", value: "max-age=31536000; includeSubDomains" }]
    : []),
];

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  output: exportEstatico ? "export" : "standalone",
  // export: gera `rota/index.html`, servido pelo `try_files $uri/` do proxy
  trailingSlash: exportEstatico,
  poweredByHeader: false,
  // sem source maps públicos no navegador em produção
  productionBrowserSourceMaps: false,
  ...(exportEstatico
    ? {}
    : {
        async headers() {
          return [{ source: "/:path*", headers: securityHeaders }];
        },
      }),
};

export default nextConfig;
