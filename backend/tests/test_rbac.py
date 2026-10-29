"""RBAC — matriz de permissões travada + comportamento por papel nas rotas reais.

SUPER_ADMIN · TENANT_ADMIN · MANAGER · VIEWER (docs/RBAC_MATRIX.md)."""

from __future__ import annotations

import pytest
from sqlalchemy import text

from app.security.rbac import TENANT_ROLE_PERMISSIONS, Perm, Role
from tests.conftest import SUPERADMIN_EMAIL, TENANT_A, iso_client

pytestmark = pytest.mark.scenarios

REGRA = {"nome": "Regra RBAC", "entidade": "prestador", "indicador": "crescimento_despesa",
         "operador": ">=", "limite": 77.0, "severidade": "atencao"}


def test_matriz_de_permissoes_e_a_documentada():
    v = {p.value for p in TENANT_ROLE_PERMISSIONS[Role.VIEWER]}
    m = {p.value for p in TENANT_ROLE_PERMISSIONS[Role.MANAGER]}
    a = {p.value for p in TENANT_ROLE_PERMISSIONS[Role.TENANT_ADMIN]}
    assert v == {"analytics:read", "alert_rules:read"}
    assert m == v | {"alert_rules:write", "action_plans:write"}
    assert a == m | {"tenant_users:read", "tenant_users:manage", "tenant_branding:manage",
                     "tenant_settings:read", "tenant_settings:manage", "tenant_audit:read"}
    # nenhum papel de tenant recebe permissão de plataforma
    for role, perms in TENANT_ROLE_PERMISSIONS.items():
        assert not any(p.value.startswith("platform") for p in perms), role


def test_nenhuma_rota_compara_nome_de_papel_ou_plano():
    """Regra de arquitetura: o código pergunta permissão/feature, nunca `role ==`/`plan ==`."""
    from pathlib import Path

    raiz = Path(__file__).resolve().parents[1] / "app"
    import re

    # `platform_role == "SUPER_ADMIN"` só existe nas DUAS definições centralizadas de
    # identidade de plataforma (User.is_super_admin / Principal.is_super_admin).
    proibidos = re.compile(
        r"""(?<!platform_)\brole\s*==\s*["']|\bplan(_code|\.code)?\s*==\s*["']|"""
        r"""["'](BASIC|BUSINESS|ENTERPRISE)["']\s+in\b|==\s*Role\.(?!SUPER_ADMIN\b)"""
    )
    for py in raiz.rglob("*.py"):
        # catálogo/CLI de bootstrap nomeiam planos; saas/users.py trata o papel como DADO
        # (invariante "último TENANT_ADMIN ativo", papel atribuível) — não é autorização.
        if py.name in ("cli.py", "catalog.py") or py.as_posix().endswith("saas/users.py"):
            continue
        achado = proibidos.search(py.read_text(encoding="utf-8"))
        assert achado is None, f"{py.relative_to(raiz)} contém {achado.group(0)!r}"


def test_viewer_le_mas_nao_altera(iso_env):
    c = iso_client(iso_env, "a.viewer@iso.example", TENANT_A)
    assert c.get("/api/executive/overview?competencia=2026-06").status_code == 200
    assert c.get("/api/config/regras-alerta").status_code == 200
    r = c.post("/api/config/regras-alerta", json=REGRA)
    assert r.status_code == 403 and r.json()["code"] == "forbidden"
    rid = c.get("/api/config/regras-alerta").json()[0]["id"]
    assert c.put(f"/api/config/regras-alerta/{rid}", json={"limite": 1}).status_code == 403
    assert c.delete(f"/api/config/regras-alerta/{rid}").status_code == 403
    assert c.get("/api/tenant/users").status_code == 403


def test_manager_opera_mas_nao_administra(iso_env):
    c = iso_client(iso_env, "a.manager@iso.example", TENANT_A)
    r = c.post("/api/config/regras-alerta", json=REGRA)
    assert r.status_code == 201
    assert c.delete(f"/api/config/regras-alerta/{r.json()['id']}").status_code == 204
    for rota in ("/api/tenant/users", "/api/tenant/settings", "/api/tenant/audit", "/api/admin/tenants"):
        assert c.get(rota).status_code == 403, rota
    r = c.post("/api/tenant/users", json={"email": "novo@iso.example", "name": "Novo", "role": "VIEWER"})
    assert r.status_code == 403


def test_tenant_admin_administra_o_proprio_tenant_e_nao_a_plataforma(iso_env):
    c = iso_client(iso_env, "a.admin@iso.example", TENANT_A)
    for rota in ("/api/tenant/users", "/api/tenant/settings", "/api/tenant/audit", "/api/tenant/overview"):
        assert c.get(rota).status_code == 200, rota
    for rota in ("/api/admin/tenants", "/api/admin/users", "/api/admin/plans", "/api/admin/features",
                 "/api/admin/audit", f"/api/admin/tenants/{TENANT_A}"):
        assert c.get(rota).status_code == 403, rota


def test_super_admin_sem_tenant_administra_mas_nao_ve_dados_sem_selecao_explicita(iso_env):
    c = iso_client(iso_env, SUPERADMIN_EMAIL, None)
    assert c.get("/api/admin/tenants").status_code == 200
    r = c.get("/api/executive/overview")
    assert r.status_code == 403 and r.json()["code"] == "tenant_required"
    # sem MFA configurado, a equipe de plataforma não entra em tenant algum
    r = c.post("/api/auth/switch-tenant", json={"tenant_id": TENANT_A})
    assert r.status_code == 403 and r.json()["code"] == "mfa_setup_required"
    with iso_env.owner() as s:
        s.execute(text("UPDATE users SET mfa_enabled = true WHERE email = :e"), {"e": SUPERADMIN_EMAIL})
        s.commit()
    try:
        # seleção explícita e auditada
        r = c.post("/api/auth/switch-tenant", json={"tenant_id": TENANT_A})
    finally:
        with iso_env.owner() as s:
            s.execute(text("UPDATE users SET mfa_enabled = false WHERE email = :e"), {"e": SUPERADMIN_EMAIL})
            s.commit()
    assert r.status_code == 200
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    assert c.get("/api/executive/overview?competencia=2026-06", headers=h).status_code == 200
    with iso_env.owner() as s:
        n = s.execute(text("SELECT count(*) FROM audit_logs WHERE action = 'platform.tenant_access' "
                           "AND actor_email = :e AND tenant_id = :t"),
                      {"e": SUPERADMIN_EMAIL, "t": TENANT_A}).scalar_one()
    assert n >= 1


def test_negacoes_sao_auditadas(iso_env):
    c = iso_client(iso_env, "a.viewer@iso.example", TENANT_A)
    c.get("/api/admin/tenants")
    with iso_env.owner() as s:
        n = s.execute(text("SELECT count(*) FROM audit_logs WHERE action = 'access.denied' "
                           "AND actor_email = 'a.viewer@iso.example'")).scalar_one()
    assert n >= 1


def test_permissoes_de_plataforma_sao_exclusivas():
    assert {p for p in Perm if p.value.startswith("platform")}
