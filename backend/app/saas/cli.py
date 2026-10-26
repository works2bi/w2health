"""CLI administrativa da plataforma (roda com o papel DONO do banco — `DATABASE_ADMIN_URL`).

    python -m app.saas.cli sync-catalog
    python -m app.saas.cli bootstrap-demo                 # catálogo + planos + usuários demo
    python -m app.saas.cli create-superadmin --email x@y --name "Nome"
    python -m app.saas.cli set-password --email x@y       # senha temporária (troca obrigatória)
    python -m app.saas.cli reset-mfa --email x@y --reason "perdeu o celular"

Senhas:
* NUNCA ficam no repositório. São geradas aleatoriamente e impressas UMA vez no terminal,
  ou lidas da variável `DEMO_USER_PASSWORD` (somente ambiente de desenvolvimento local).
* Promover SUPER_ADMIN só é possível aqui (exige acesso ao banco), não pela API.
Toda ação é auditada com ator `cli`.
"""

from __future__ import annotations

import argparse
import os
import secrets
import sys

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.tenant import DEFAULT_TENANT
from app.db.session import AdminSessionLocal
from app.models import Plan, Tenant, User, UserTenant
from app.saas import audit
from app.saas import users as users_svc
from app.saas.catalog import sync_catalog
from app.security.passwords import hash_password, password_policy_errors

CLI_ACTOR = audit.Actor(email="cli", role="PLATFORM_CLI")

#: segundo tenant sintético (isolamento / demonstração de gating por plano)
DEMO_TENANT_B = "w2h-demo-b"

DEMO_USERS: tuple[tuple[str, str, str | None, str | None, str | None], ...] = (
    # email, nome, tenant, papel de tenant, papel de plataforma
    ("superadmin@works2data.example", "Equipe Works2Data", None, None, "SUPER_ADMIN"),
    ("admin@vidaplena.example", "Admin Vida Plena", DEFAULT_TENANT, "TENANT_ADMIN", None),
    ("gestor@vidaplena.example", "Gestor Vida Plena", DEFAULT_TENANT, "MANAGER", None),
    ("leitor@vidaplena.example", "Leitor Vida Plena", DEFAULT_TENANT, "VIEWER", None),
    ("admin@horizonte.example", "Admin Horizonte", DEMO_TENANT_B, "TENANT_ADMIN", None),
)
DEMO_PLANS = {DEFAULT_TENANT: "ENTERPRISE", DEMO_TENANT_B: "BASIC",
              # laboratórios sintéticos (se semeados): acessíveis ao super admin da plataforma
              "w2h-cenarios": "ENTERPRISE", "w2h-financeiro": "ENTERPRISE"}


def _senha_demo() -> str:
    env = os.environ.get("DEMO_USER_PASSWORD")
    if env:
        erros = password_policy_errors(env)
        if erros:
            sys.exit("DEMO_USER_PASSWORD não atende à política: " + " ".join(erros))
        return env
    return secrets.token_urlsafe(12)


def bootstrap_demo(db: Session) -> list[tuple[str, str]]:
    """Catálogo + planos dos tenants demo + usuários demo. Idempotente.
    Retorna [(email, senha)] apenas das contas CRIADAS agora."""
    sync_catalog(db)
    for code, plan_code in DEMO_PLANS.items():
        t = db.get(Tenant, code)
        if t is not None and t.plan_id is None:
            t.plan_id = db.execute(select(Plan.id).where(Plan.code == plan_code)).scalar_one()
            audit.add(db, "tenant.plan_changed", actor=CLI_ACTOR, tenant_id=code,
                      entity_type="tenant", entity_id=code, details={"para": plan_code, "via": "bootstrap"})
    criados: list[tuple[str, str]] = []
    for email, nome, tenant, papel, plataforma in DEMO_USERS:
        if tenant is not None and db.get(Tenant, tenant) is None:
            continue  # tenant ainda não semeado
        u = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
        if u is None:
            senha = _senha_demo()
            u = User(email=email, name=nome, password_hash=hash_password(senha),
                     platform_role=plataforma, status="ACTIVE")
            db.add(u)
            db.flush()
            criados.append((email, senha))
            audit.add(db, "user.created", actor=CLI_ACTOR, tenant_id=tenant, entity_type="user",
                      entity_id=u.id, details={"via": "bootstrap", "plataforma": plataforma})
        if tenant is not None and db.get(UserTenant, (u.id, tenant)) is None:
            db.add(UserTenant(user_id=u.id, tenant_id=tenant, role=papel, status="ACTIVE"))
    db.flush()
    return criados


def _user(db: Session, email: str) -> User:
    u = db.execute(select(User).where(User.email == email.strip().lower())).scalar_one_or_none()
    if u is None:
        sys.exit("usuário não encontrado")
    return u


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m app.saas.cli")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("sync-catalog")
    sub.add_parser("bootstrap-demo")
    p = sub.add_parser("create-superadmin")
    p.add_argument("--email", required=True)
    p.add_argument("--name", required=True)
    p = sub.add_parser("set-password")
    p.add_argument("--email", required=True)
    p = sub.add_parser("reset-mfa")
    p.add_argument("--email", required=True)
    p.add_argument("--reason", required=True)
    args = ap.parse_args(argv)

    with AdminSessionLocal() as db:
        if args.cmd == "sync-catalog":
            print(sync_catalog(db))
        elif args.cmd == "bootstrap-demo":
            criados = bootstrap_demo(db)
            print("Bootstrap concluído.")
            if criados:
                print("\nContas criadas (senhas exibidas UMA única vez — guarde-as agora):")
                for email, senha in criados:
                    print(f"  {email:<34} {senha}")
                print("\nO SUPER_ADMIN será obrigado a configurar MFA no primeiro login.")
        elif args.cmd == "create-superadmin":
            email = users_svc.normalize_email(args.email)
            if db.execute(select(User).where(User.email == email)).scalar_one_or_none():
                sys.exit("já existe usuário com este e-mail")
            senha = users_svc.temporary_password()
            u = User(email=email, name=args.name.strip(), password_hash=hash_password(senha),
                     platform_role="SUPER_ADMIN", must_change_password=True)
            db.add(u)
            db.flush()
            audit.add(db, "platform.superadmin_created", actor=CLI_ACTOR, entity_type="user", entity_id=u.id)
            print(f"SUPER_ADMIN criado. Senha temporária (troca obrigatória): {senha}")
        elif args.cmd == "set-password":
            u = _user(db, args.email)
            senha = users_svc.reset_password(db, u)
            audit.add(db, "user.password_reset", actor=CLI_ACTOR, entity_type="user", entity_id=u.id,
                      details={"via": "cli"})
            print(f"Senha temporária (troca obrigatória no próximo login): {senha}")
        elif args.cmd == "reset-mfa":
            u = _user(db, args.email)
            users_svc.reset_mfa(db, u)
            audit.add(db, "mfa.admin_reset", actor=CLI_ACTOR, entity_type="user", entity_id=u.id,
                      details={"via": "cli", "motivo": args.reason})
            print("MFA resetado; sessões do usuário revogadas.")
        db.commit()


if __name__ == "__main__":
    main()
