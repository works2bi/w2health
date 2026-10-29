# Matriz RBAC — W2Health V1

> Fonte de verdade: `backend/app/security/rbac.py` (travada por `tests/test_rbac.py`).
> O código pergunta **permissões** (`require_permission`) — nunca o nome do papel.

## Papéis

| Papel | Onde vive | Quem | Escopo |
|---|---|---|---|
| `SUPER_ADMIN` | `users.platform_role` | equipe Works2Data | plataforma; dados de um tenant só após seleção explícita e auditada (exige MFA) |
| `TENANT_ADMIN` | `user_tenants.role` | administrador da operadora | o próprio tenant |
| `MANAGER` | `user_tenants.role` | gestor/analista | análises + regras de alerta |
| `VIEWER` | `user_tenants.role` | leitor | somente leitura |

Um usuário pode ter papéis diferentes em tenants diferentes (um vínculo por tenant).

## Permissões de tenant

| Permissão | VIEWER | MANAGER | TENANT_ADMIN | SUPER_ADMIN (no tenant) |
|---|:-:|:-:|:-:|:-:|
| `analytics:read` — módulos analíticos (sujeito a features) | ✓ | ✓ | ✓ | ✓ |
| `alert_rules:read` — ver regras de alerta | ✓ | ✓ | ✓ | ✓ |
| `alert_rules:write` — criar/editar/excluir regras | | ✓ | ✓ | ✓ |
| `action_plans:write` — criar/editar planos de ação | | ✓ | ✓ | ✓ |
| `tenant_users:read` — listar usuários do tenant | | | ✓ | ✓ |
| `tenant_users:manage` — adicionar, papel, status, reset (contas exclusivas) | | | ✓ | ✓ |
| `tenant_branding:manage` — identidade visual (exige feature `custom_branding`) | | | ✓ | ✓ |
| `tenant_settings:read` / `:manage` — configurações funcionais | | | ✓ | ✓ |
| `tenant_audit:read` — auditoria do próprio tenant | | | ✓ | ✓ |

## Permissões de plataforma (somente SUPER_ADMIN, sem tenant)

`platform_tenants:manage` · `platform_plans:manage` · `platform_features:manage` ·
`platform_users:manage` · `platform_secrets:manage` · `platform_audit:read` ·
`platform_tenant:access`.

## Restrições explícitas

| Regra | Onde |
|---|---|
| TENANT_ADMIN não troca plano nem habilita feature (não há rota em `/api/tenant`) | `test_features::tenant_admin_nao_habilita_feature_nem_troca_plano` |
| TENANT_ADMIN não atribui `SUPER_ADMIN` | `app/saas/users.py::_check_role` |
| TENANT_ADMIN não altera limites contratuais (`limits.*` = `platform`) | `settings_catalog` |
| TENANT_ADMIN só reseta senha/MFA de conta exclusiva do próprio tenant | `users.is_exclusive_to` |
| Ninguém altera o próprio papel/status pela administração | `users.update_member`, `admin.patch_user` |
| Último TENANT_ADMIN ativo não pode ser rebaixado pela área do tenant | `users.update_member` |
| SUPER_ADMIN só é criado pela CLI com acesso ao banco | `app/saas/cli.py create-superadmin` |
| Mudança de papel/status de vínculo revoga as sessões do usuário naquele tenant | `sessions.revoke_all_for_user_in_tenant` |

## Mapa de rotas (resumo)

| Área | Guard |
|---|---|
| `/api/executive`, `/api/analytics/*`, `/api/meta/*`, `/api/catalogos/*` | `analytics:read` + feature do módulo |
| `/api/config/regras-alerta` (GET / escrita) | `alert_rules:read` / `alert_rules:write` + `alerts` |
| `/api/tenant/*` | `tenant_*` conforme a rota |
| `/api/admin/*` | `require_platform(...)` |
| `/api/auth/*`, `/api/public/*`, `/api/health` | autenticação própria / público |

Negações de permissão e de plataforma são auditadas (`access.denied`).
