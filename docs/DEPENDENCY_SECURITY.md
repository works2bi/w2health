# Segurança de dependências — W2Health

Verificação de vulnerabilidades conhecidas **sem upgrade automático**. Upgrades major são
decididos, documentados e testados um por vez. **Nunca** rodar `npm audit fix --force`.

```powershell
./scripts/security-check.ps1     # ou: make security-check  (também roda no CI)
```

- Backend: `pip-audit` sobre `requirements.txt` (runtime) e `requirements-dev.txt`, ambos
  exportados do `uv.lock`.
- Frontend: `npm audit --omit=dev` (**bloqueante** — o que vai para o runtime) e `npm audit`
  completo (informativo — ferramentas de build/lint).

## Situação atual (2026-10-06, após a Fase 3)

| Escopo | Resultado |
|---|---|
| Backend runtime (`requirements.txt`) | **nenhuma vulnerabilidade conhecida** |
| Backend dev (`requirements-dev.txt`) | **nenhuma vulnerabilidade conhecida** |
| Frontend produção (`npm audit --omit=dev`) | **0 vulnerabilidades** |
| Frontend total (inclui build/lint) | 9 achados (7 altos, 2 moderados) — todos em ferramentas de build, ver §3 |

## 1. Antes × depois

| Pacote | Antes (Fase 2) | Depois | Achados resolvidos |
|---|---|---|---|
| next | 14.2.35 (crítico) | **15.5.28** | RCE no otimizador de imagem e em servidores Windows, SSRF (Server Actions, rewrites, WebSocket), DoS em Server Components/Actions, cache poisoning de RSC, bypass de middleware i18n, XSS com nonce/beforeInteractive, request smuggling; RCE no Image Optimization com SVG (GHSA-2464-fhr4-w9xw) e DoS em Server Actions (GHSA-p3m5-59mp-jv5r) corrigidos na 15.5.28 |
| react / react-dom | 18.3.1 | 19.2.0 | requisito do App Router no Next 15 |
| recharts / swr | 2.13.3 / 2.2.x | 2.15.4 / 2.5.1 | mesmo major; suporte a React 19 |
| postcss (interno do Next), source-map-js | 8.4.31 / 1.2.1 | 8.5.29 / 1.2.2 via `overrides` | XSS no stringify, leitura de arquivo via sourceMappingURL, DoS — **mesma linha (minor/patch)** |
| eslint-config-next | 14.2.35 | 15.5.28 | glob/plugin vulneráveis da linha 14 |
| fastapi | 0.115.14 | **0.142.2** | permite Starlette corrigido |
| starlette | 0.46.2 | **1.7.0** | multipart grande bloqueando o event loop, `request.form()` sem limite efetivo, Range quadrático, Host/path na reconstrução de URL, StaticFiles no Windows, dispatch de método |
| cryptography | 46.0.7 | **50.0.2** | PKCS7, validação de cadeia X.509, OpenSSL embutido |
| pytest | 8.4.2 | **9.1.1** | diretório temporário previsível |
| boto3 (novo) | — | 1.43.108 | adapter S3 (sem achados) |
| pyyaml | transitivo | direto (mesma versão) | — |

Totais: backend 23 achados em 3 pacotes → 0; frontend produção 1 crítico + 2 altos → 0;
frontend total 14 → 9 (restantes só em build/lint).

## 2. Decisão sobre o Next.js

* O `npm audit` sugeria `next@16.3.8` porque a **faixa agregada** do pacote vai até
  `16.3.0-preview`. Lendo cada advisory, **todos** têm correção na linha 15 (`< 15.5.24` é o
  maior limite). Decisão: **um** major (14 → 15.5.27), não dois.
* Compatibilidade verificada: React 19 (exigido pelo App Router do Next 15), TypeScript 5.6
  sem erros com `@types/react` 19, páginas dinâmicas são client components com `useParams`
  (não afetadas pela API assíncrona de `params`), sem middleware, sem Server Actions, sem
  `next/image`; build standalone e Docker OK.
* `next lint` está depreciado no 15 e removido no 16 → lint migrado para o ESLint CLI.
* Validação: `tsc`, lint, build, testes de navegador (Fase 1 smoke, Fase 2 17/17,
  recovery 6/6, Fase 3 10/10 com zero violação de CSP), `npm audit --omit=dev` = 0.
* Próximo passo (não urgente): Next 16 quando houver motivo (sem advisory pendente na 15.5).

## 3. Riscos aceitos (somente ferramentas de build)

| Pacote | Por quê aparece | Correção disponível | Exposição | Decisão |
|---|---|---|---|---|
| tailwindcss 3.4 → chokidar, micromatch, braces, fast-glob, postcss-selector-parser, postcss-nested | glob/watch do Tailwind 3 | **não existe** na linha 3 (`braces` 3.0.3 é a última versão publicada e continua sinalizada); só Tailwind 4 (migração de CSS) | build-time, processando apenas o código-fonte do próprio repositório; nada vai para a imagem de runtime | aceito; Tailwind 4 no roadmap (P2) |
| eslint-config-next 15 → @next/eslint-plugin-next → fast-glob | lint | idem (cadeia do fast-glob) | só lint local/CI | aceito |

## 4. Política

1. `security-check` no CI a cada push e antes de cada release.
2. Produção (runtime backend + `--omit=dev` frontend): **zero** vulnerabilidade conhecida para
   liberar release; exceção só com registro aqui (exposição, mitigação, prazo).
3. Patch/minor: pode atualizar após suíte verde. Major/mudança de faixa: documentar, um por
   vez, suíte completa + E2E + novo scan.
4. Lock files versionados; imagem de runtime instala só dependências de produção (as de dev
   ficam no alvo `test` da imagem).
5. Recomendado em produção: scan das imagens (Trivy/Grype) no registro — não configurado nesta fase.
