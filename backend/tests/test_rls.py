"""Row-Level Security — a 2ª camada, testada SEM a aplicação (SQL cru no papel de runtime).

Prova que, mesmo que um repositório esquecesse o `WHERE tenant_id`, o banco não entregaria
dado de outro tenant: sem `app.tenant_id` → zero linhas; com tenant A → só A; escrita com
tenant diferente do contexto → rejeitada.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db import rls
from app.db.tenant_scope import bind_tenant
from tests.conftest import ISO_APP_ROLE, TENANT_A, TENANT_B

pytestmark = pytest.mark.scenarios

_MIGRATIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"


def _tuple_from_migration(nome_arquivo: str, constante: str) -> set[str]:
    src = (_MIGRATIONS / nome_arquivo).read_text(encoding="utf-8")
    bloco = re.search(rf"{constante} = \((.*?)\)", src, re.S).group(1)
    return set(re.findall(r'"([a-z_]+)"', bloco))


def test_lista_de_tabelas_da_migration_bate_com_metadata():
    """Toda tabela do data plane com tenant_id recebe política — se alguém criar uma tabela
    nova com tenant_id sem migration de RLS, este teste falha. (Fase 2: a política das
    tabelas novas vem da migration f4c5d6e7a8b9.)"""
    esperado = set(rls.data_plane_tables())
    fase1 = _tuple_from_migration("e8b9c0d1f2a3_v1_saas_rls_e_papel_de_runtime.py", "DATA_PLANE_TABLES")
    fase2 = _tuple_from_migration("f4c5d6e7a8b9_fase2_data_platform_operacional.py", "NEW_DATA_PLANE_TABLES")
    n_para_n = _tuple_from_migration("c9d0e1f2a3b4_plano_contrato_n_para_n.py", "NEW_DATA_PLANE_TABLES")
    investigacao = _tuple_from_migration("d0e1f2a3b4c5_investigacao_custos_modelo.py",
                                         "NEW_DATA_PLANE_TABLES")
    consolidacao = _tuple_from_migration("e1f2a3b4c5d6_consolidacao_canonico_financeiro.py",
                                         "NEW_DATA_PLANE_TABLES")
    evolucao = _tuple_from_migration("a9b8c7d6e5f4_evolucao_gestao_da_acao.py", "NEW_DATA_PLANE_TABLES")
    renomeadas = {"receitas_contrato"}  # → receitas_reconhecidas (está em `consolidacao`)
    assert (fase1 - renomeadas) | fase2 | n_para_n | investigacao | consolidacao | evolucao == esperado
    assert _tuple_from_migration("c7a1e2b3d4f5_v1_saas_control_plane.py", "DATA_PLANE_TABLES") == fase1
    controle_fase1 = _tuple_from_migration("e8b9c0d1f2a3_v1_saas_rls_e_papel_de_runtime.py",
                                           "CONTROL_PLANE_TABLES")
    novas_controle = {"tenant_onboarding", "user_recovery_codes", "auth_rate_limits",
                      # Fase 3 — fila e presença do worker (migration b8e9f0a1c2d3)
                      "pipeline_jobs", "worker_heartbeats",
                      # Evolução — referência de mercado (migration a9b8c7d6e5f4)
                      "benchmarks_mercado"}
    assert controle_fase1 | novas_controle == set(rls.control_plane_tables())


def test_todas_as_tabelas_do_data_plane_tem_rls_forcado(iso_env):
    with iso_env.owner() as s:
        rows = s.execute(text(
            "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class "
            "WHERE relname = ANY(:t) AND relkind = 'r'"), {"t": rls.data_plane_tables()}).all()
        politicas = set(s.execute(text(
            "SELECT tablename FROM pg_policies WHERE policyname = 'tenant_isolation'")).scalars())
    assert len(rows) == len(rls.data_plane_tables())
    for nome, ativo, forcado in rows:
        assert ativo and forcado, nome
    assert politicas == set(rls.data_plane_tables())


def test_papel_de_runtime_nao_e_superusuario_nem_bypassrls(iso_env):
    with iso_env.owner() as s:
        sup, byp = s.execute(text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = :r"),
                             {"r": ISO_APP_ROLE}).one()
    assert not sup and not byp


def test_sem_tenant_no_contexto_o_banco_devolve_zero_linhas(iso_env):
    with iso_env.app() as s:  # nenhum bind_tenant
        for t in ("beneficiarios", "eventos_assistenciais", "agg_sinistralidade_competencia",
                  "contratos", "prestadores", "regras_alerta"):
            assert s.execute(text(f"SELECT count(*) FROM {t}")).scalar_one() == 0, t


def test_com_tenant_a_o_banco_so_mostra_a(iso_env):
    with iso_env.app() as s:
        bind_tenant(s, TENANT_A)
        tenants = set(s.execute(text("SELECT DISTINCT tenant_id FROM eventos_assistenciais")).scalars())
        n = s.execute(text("SELECT count(*) FROM beneficiarios WHERE codigo = 'BEN-000001'")).scalar_one()
    assert tenants == {TENANT_A}
    assert n == 1  # o BEN-000001 do tenant B é invisível


def test_join_sem_filtro_tambem_e_isolado(iso_env):
    """Mesmo uma consulta "esquecida" (sem WHERE tenant) com JOIN só enxerga A."""
    with iso_env.app() as s:
        bind_tenant(s, TENANT_A)
        tenants = set(s.execute(text(
            "SELECT DISTINCT b.tenant_id FROM eventos_assistenciais e "
            "JOIN beneficiarios b ON b.id = e.id_beneficiario")).scalars())
    assert tenants == {TENANT_A}


def test_escrita_com_outro_tenant_e_rejeitada_pelo_banco(iso_env):
    with iso_env.app() as s:
        bind_tenant(s, TENANT_A)
        with pytest.raises(DBAPIError):
            s.execute(text(
                "INSERT INTO regras_alerta (tenant_id, nome, entidade, indicador, operador, limite, "
                "severidade, escopo, ativo) VALUES (:t, 'x', 'prestador', 'crescimento_despesa', "
                "'>=', 1, 'atencao', '{}', true)"), {"t": TENANT_B})
        s.rollback()


def test_update_e_delete_de_outro_tenant_afetam_zero_linhas(iso_env):
    with iso_env.owner() as s:
        id_b = s.execute(text("SELECT min(id) FROM regras_alerta WHERE tenant_id = :b"),
                         {"b": TENANT_B}).scalar_one()
    with iso_env.app() as s:
        bind_tenant(s, TENANT_A)
        assert s.execute(text("UPDATE regras_alerta SET limite = 0 WHERE id = :i"), {"i": id_b}).rowcount == 0
        assert s.execute(text("DELETE FROM regras_alerta WHERE id = :i"), {"i": id_b}).rowcount == 0
        s.rollback()


def test_runtime_nao_escreve_no_data_plane_analitico(iso_env):
    """Menor privilégio: a API só lê eventos/agregados (escrita só em regras_alerta)."""
    with iso_env.app() as s:
        bind_tenant(s, TENANT_A)
        with pytest.raises(DBAPIError):
            s.execute(text("DELETE FROM eventos_assistenciais"))
        s.rollback()


def test_auditoria_e_append_only_para_o_runtime(iso_env):
    with iso_env.app() as s:
        s.execute(text("INSERT INTO audit_logs (action, outcome, details) VALUES ('teste.rls', 'success', '{}')"))
        s.commit()
        with pytest.raises(DBAPIError):
            s.execute(text("UPDATE audit_logs SET action = 'adulterado'"))
        s.rollback()
        with pytest.raises(DBAPIError):
            s.execute(text("DELETE FROM audit_logs"))
        s.rollback()


def test_tenant_nao_vaza_entre_transacoes_do_pool(iso_env):
    """set_config é LOCAL à transação: a próxima transação na mesma conexão volta a zero."""
    with iso_env.app_engine.connect() as conn:
        with conn.begin():
            conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": TENANT_A})
            assert conn.execute(text("SELECT count(*) FROM beneficiarios")).scalar_one() > 0
        with conn.begin():
            assert conn.execute(text("SELECT count(*) FROM beneficiarios")).scalar_one() == 0
