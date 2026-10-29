"""Envio agendado dos resumos de alertas (Evolução, item 4). Uso (cron / agendador):

    python -m app.ops.enviar_alertas --frequencia semanal            # todos os tenants ativos
    python -m app.ops.enviar_alertas --frequencia mensal --tenant w2h-demo

Competência = a última disponível de cada tenant. Idempotente dentro da janela da
frequência (ver `app.saas.notificacoes`). Roda com o papel dono (`DATABASE_ADMIN_URL`),
amarrando a sessão a um tenant por vez (RLS).
"""

from __future__ import annotations

import argparse
import json

from sqlalchemy import select

from app.db.session import AdminSessionLocal
from app.db.tenant_scope import bind_tenant
from app.models import Tenant
from app.repositories import analytics_repo as repo
from app.saas import features as feats
from app.saas import notificacoes as envio


def main() -> None:
    ap = argparse.ArgumentParser(prog="enviar_alertas")
    ap.add_argument("--frequencia", choices=("semanal", "mensal"), required=True)
    ap.add_argument("--tenant", help="só este tenant (padrão: todos os ativos)")
    ap.add_argument("--forcar", action="store_true")
    args = ap.parse_args()

    with AdminSessionLocal() as s:
        q = select(Tenant).where(Tenant.status == "ACTIVE")
        if args.tenant:
            q = q.where(Tenant.id == args.tenant)
        tenants = [(t.id, t.name, feats.enabled_features(s, t)) for t in s.execute(q).scalars()]

    for tid, nome, features in tenants:
        if "alerts" not in features:
            continue
        with AdminSessionLocal() as s:
            bind_tenant(s, tid)
            comps = repo.competencias(s)
            if not comps:
                continue
            res = envio.executar(s, tenant_id=tid, tenant_nome=nome, features=features,
                                 frequencia=args.frequencia, competencia=comps[-1],
                                 forcar=args.forcar)
            print(json.dumps({"tenant": tid, **res}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
