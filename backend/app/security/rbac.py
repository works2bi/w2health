"""RBAC — papéis, permissões e matriz. Fonte única de verdade (documentada em
`docs/RBAC_MATRIX.md`; `tests/test_rbac.py` trava a matriz).

Regras:
* O código de negócio pergunta **permissões** (`require_permission(...)`), nunca o nome do
  papel. Não há `if role == ...` espalhado.
* `SUPER_ADMIN` é papel de **plataforma** (`users.platform_role`). Ao operar dentro de um
  tenant (fluxo explícito e auditado de seleção), recebe as permissões de tenant completas.
* Papéis de tenant (`user_tenants.role`): `TENANT_ADMIN` > `MANAGER` > `VIEWER`.
"""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    SUPER_ADMIN = "SUPER_ADMIN"
    TENANT_ADMIN = "TENANT_ADMIN"
    MANAGER = "MANAGER"
    VIEWER = "VIEWER"


class Perm(StrEnum):
    # ---- dados analíticos do tenant
    ANALYTICS_READ = "analytics:read"
    # ---- regras de alerta (configuração operacional)
    ALERT_RULES_READ = "alert_rules:read"
    ALERT_RULES_WRITE = "alert_rules:write"
    # ---- planos de ação (gestão da ação sobre insights/alertas)
    ACTION_PLANS_WRITE = "action_plans:write"
    # ---- administração do PRÓPRIO tenant
    TENANT_USERS_READ = "tenant_users:read"
    TENANT_USERS_MANAGE = "tenant_users:manage"
    TENANT_BRANDING_MANAGE = "tenant_branding:manage"
    TENANT_SETTINGS_READ = "tenant_settings:read"
    TENANT_SETTINGS_MANAGE = "tenant_settings:manage"
    TENANT_AUDIT_READ = "tenant_audit:read"
    # ---- plataforma (somente SUPER_ADMIN, sem tenant)
    PLATFORM_TENANTS_MANAGE = "platform_tenants:manage"
    PLATFORM_PLANS_MANAGE = "platform_plans:manage"
    PLATFORM_FEATURES_MANAGE = "platform_features:manage"
    PLATFORM_USERS_MANAGE = "platform_users:manage"
    PLATFORM_SECRETS_MANAGE = "platform_secrets:manage"
    PLATFORM_AUDIT_READ = "platform_audit:read"
    PLATFORM_TENANT_ACCESS = "platform_tenant:access"


_VIEWER = frozenset({Perm.ANALYTICS_READ, Perm.ALERT_RULES_READ})
_MANAGER = _VIEWER | {Perm.ALERT_RULES_WRITE, Perm.ACTION_PLANS_WRITE}
_TENANT_ADMIN = _MANAGER | {
    Perm.TENANT_USERS_READ,
    Perm.TENANT_USERS_MANAGE,
    Perm.TENANT_BRANDING_MANAGE,
    Perm.TENANT_SETTINGS_READ,
    Perm.TENANT_SETTINGS_MANAGE,
    Perm.TENANT_AUDIT_READ,
}
PLATFORM_PERMS = frozenset(p for p in Perm if p.value.startswith("platform"))

#: Permissões de TENANT por papel (válidas dentro do tenant do contexto).
TENANT_ROLE_PERMISSIONS: dict[Role, frozenset[Perm]] = {
    Role.VIEWER: frozenset(_VIEWER),
    Role.MANAGER: frozenset(_MANAGER),
    Role.TENANT_ADMIN: frozenset(_TENANT_ADMIN),
    # SUPER_ADMIN dentro de um tenant selecionado: administra o tenant por completo.
    Role.SUPER_ADMIN: frozenset(_TENANT_ADMIN),
}

#: Papéis que um TENANT_ADMIN pode atribuir (nunca SUPER_ADMIN — papel de plataforma).
ASSIGNABLE_BY_TENANT_ADMIN = frozenset({Role.TENANT_ADMIN, Role.MANAGER, Role.VIEWER})


def tenant_permissions(role: str | None) -> frozenset[Perm]:
    try:
        return TENANT_ROLE_PERMISSIONS[Role(role)] if role else frozenset()
    except ValueError:
        return frozenset()
