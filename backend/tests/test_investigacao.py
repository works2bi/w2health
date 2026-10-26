"""Investigação de custos (Evolução V1) sobre a massa "Vida Plena" + API, gating e isolamento.

* a decomposição reconcilia com a Gold (`agg_sinistralidade_competencia`,
  `agg_contrato_competencia`) em várias competências, janelas e comparações;
* os cenários JÁ existentes são lidos corretamente pelo motor novo (s1 frequência; s7 custo
  unitário SEM reajuste — a tabela de preço sintética não muda);
* a rota respeita features (403 sem `advanced_explanations`; sem `provider_intelligence` o
  detalhe por prestador some) e audita o acesso com amostra de beneficiário;
* isolamento entre dois tenants com os MESMOS códigos de negócio (com e sem RLS).
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select, text

from app.analytics import decomposicao_estrutural as de
from app.analytics import investigacao
from app.analytics.periodo import parse_competencia
from app.models import Plan, Tenant, TenantFeature
from tests.conftest import TENANT_A, TENANT_B, iso_client, token_for

pytestmark = pytest.mark.scenarios


def _ef(r: dict) -> dict:
    return {e["codigo"]: e["valor"] for e in r["decomposicao"]["efeitos"]}


def _liquida_gold(db, c: date) -> float:
    return float(db.execute(text(
        "SELECT despesa_liquida FROM agg_sinistralidade_competencia "
        "WHERE tenant_id = 'w2h-demo' AND competencia = :c"), {"c": c}).scalar_one())


# ------------------------------------------------------------------ reconciliação com a Gold
@pytest.mark.parametrize("comp,comparacao,meses", [
    ("2026-07", "mes_anterior", 1), ("2026-10", "mes_anterior", 1), ("2026-09", "ano_anterior", 1),
    ("2026-12", "mes_anterior", 3), ("2026-12", "ano_anterior", 12),
])
def test_operadora_reconcilia_com_a_gold(db, comp, comparacao, meses):
    c = parse_competencia(comp)
    r = investigacao.investigar(db, c, comparacao, meses)
    rec = r["decomposicao"]["reconciliacao"]
    assert rec["ok"], rec
    base, atual = investigacao.periodos(c, comparacao, meses)
    esperado = sum(_liquida_gold(db, m) for m in atual) - sum(_liquida_gold(db, m) for m in base)
    assert rec["delta_observado"] == pytest.approx(esperado, abs=0.05)


def test_contrato_reconcilia_com_agg_contrato(db):
    c, b = date(2026, 8, 1), date(2026, 7, 1)
    row = db.execute(text(
        "SELECT id_contrato, SUM(despesa_liquida) FILTER (WHERE competencia = :c) AS a, "
        "SUM(despesa_liquida) FILTER (WHERE competencia = :b) AS z FROM agg_contrato_competencia "
        "WHERE tenant_id = 'w2h-demo' AND competencia IN (:c, :b) GROUP BY id_contrato "
        "ORDER BY 2 DESC LIMIT 1"), {"c": c, "b": b}).one()
    r = investigacao.investigar(db, c, contrato_id=int(row[0]))
    assert r["decomposicao"]["reconciliacao"]["ok"]
    assert r["variacao"]["delta_liquida"] == pytest.approx(float(row[1]) - float(row[2]), abs=0.05)


def test_fator_reconcilia_com_agg_dimensao(db):
    c = date(2026, 7, 1)
    chave = db.execute(text(
        "SELECT chave FROM agg_competencia_dimensao WHERE tenant_id = 'w2h-demo' AND competencia = :c "
        "AND dimensao = 'especialidade' ORDER BY despesa_liquida DESC LIMIT 1"), {"c": c}).scalar_one()
    vals = dict(db.execute(text(
        "SELECT competencia, despesa_liquida FROM agg_competencia_dimensao WHERE tenant_id = 'w2h-demo' "
        "AND dimensao = 'especialidade' AND chave = :k AND competencia IN (:c, :b)"),
        {"k": chave, "c": c, "b": date(2026, 6, 1)}).all())
    r = investigacao.investigar(db, c, dimensao="especialidade", chave=chave)
    assert r["decomposicao"]["reconciliacao"]["ok"]
    assert r["variacao"]["delta_liquida"] == pytest.approx(vals[c] - vals[date(2026, 6, 1)], abs=0.05)


# ------------------------------------------------------------------ cenários existentes
def test_s1_catarata_e_frequencia_no_motor_novo(db, gabarito):
    g = gabarito["s1_catarata_freq"]
    r = investigacao.investigar(db, parse_competencia(g["competencia_alvo"]),
                                dimensao="procedimento", chave=g["chave_alvo"])
    ef = {k: v for k, v in _ef(r).items() if v is not None and k not in (de.GLOSA, de.COPARTICIPACAO)}
    assert max(ef, key=lambda k: ef[k]) == de.FREQUENCIA


def test_s7_custo_sobe_sem_reajuste_de_tabela(db, gabarito):
    g = gabarito["s7_custo_medio"]
    r = investigacao.investigar(db, parse_competencia(g["competencia_alvo"]),
                                dimensao="procedimento", chave=g["chave_alvo"])
    ef = _ef(r)
    bruta = r["variacao"]["despesa_bruta_atual"] - r["variacao"]["despesa_bruta_anterior"]
    assert ef[de.CUSTO_UNITARIO] >= 0.6 * bruta
    # há tabela para parte da rede (laboratórios), mas ela não mudou → nada de "reajuste"
    assert not ef[de.PRECO_CONTRATUAL]
    codigos = {a["codigo"] for a in r["achados"]}
    assert "efeito_preco_contratual" not in codigos
    assert {"efeito_custo_unitario", "hipotese_custo_unitario"} <= codigos


def test_parametros_invalidos(db):
    with pytest.raises(ValueError):
        investigacao.investigar(db, date(2026, 7, 1), meses=2)
    with pytest.raises(ValueError):
        investigacao.investigar(db, date(2026, 7, 1), dimensao="procedimento")
    with pytest.raises(ValueError, match="sem dados"):
        investigacao.investigar(db, date(2025, 1, 1))  # sem competência anterior


# ------------------------------------------------------------------ API
@pytest.fixture
def api(seeded_sessionmaker):
    """Mesmo padrão de test_api.py, com token novo (os cálculos acima podem passar do TTL)."""
    from fastapi.testclient import TestClient

    from app.db.session import get_db
    from app.main import create_app

    app = create_app()

    def _get_db():
        s = seeded_sessionmaker()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _get_db
    with seeded_sessionmaker() as s:
        tok = token_for(s, "manager@test.example", "w2h-demo")
    return TestClient(app, headers={"Authorization": f"Bearer {tok}"})


def test_api_investigacao(api):
    r = api.get("/api/analytics/investigacao", params={"competencia": "2026-07"})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["decomposicao"]["reconciliacao"]["ok"]
    assert j["achados"][0]["codigo"] == "variacao_total"
    assert {"resumo", "historico", "rede", "coortes", "defasagem", "precos", "cobertura_dados"} <= set(j)
    r = api.get("/api/analytics/investigacao",
                params={"competencia": "2026-07", "meses": 3, "comparacao": "ano_anterior",
                        "dimensao": "tipo_atendimento", "chave": "internacao"})
    assert r.status_code == 200, r.text


@pytest.mark.parametrize("params", [
    {"meses": 2}, {"comparacao": "acumulado_12m"}, {"dimensao": "procedimento"},
    {"dimensao": "xpto", "chave": "1"}, {"nivel_rede": "solicitante"},
])
def test_api_investigacao_parametros_invalidos(api, params):
    assert api.get("/api/analytics/investigacao", params={"competencia": "2026-07", **params}).status_code == 422


def test_api_audita_acesso_com_amostra_de_beneficiario(api, seeded_sessionmaker):
    assert api.get("/api/analytics/investigacao", params={"competencia": "2026-08"}).status_code == 200
    with seeded_sessionmaker() as s:
        n = s.execute(text("SELECT count(*) FROM audit_logs WHERE action = 'data.beneficiary.investigation' "
                           "AND tenant_id = 'w2h-demo'")).scalar_one()
    assert n >= 1


# ------------------------------------------------------------------ gating e isolamento
@pytest.fixture
def plano_b(iso_env):
    def _set(code: str, desligar: tuple[str, ...] = ()):
        with iso_env.owner() as s:
            s.get(Tenant, TENANT_B).plan_id = s.execute(select(Plan.id).where(Plan.code == code)).scalar_one()
            s.execute(text("DELETE FROM tenant_features WHERE tenant_id = :t"), {"t": TENANT_B})
            for f in desligar:
                s.add(TenantFeature(tenant_id=TENANT_B, feature_key=f, enabled=False, reason="teste"))
            s.commit()
    yield _set
    _set("ENTERPRISE")


def test_sem_explicacoes_avancadas_a_rota_e_bloqueada(iso_env, plano_b):
    plano_b("BASIC")
    c = iso_client(iso_env, "b.manager@iso.example", TENANT_B)
    assert c.get("/api/analytics/investigacao", params={"competencia": "2026-07"}).status_code == 403


def test_sem_inteligencia_de_prestadores_o_detalhe_some(iso_env, plano_b):
    plano_b("ENTERPRISE", desligar=("provider_intelligence", "beneficiary_intelligence"))
    c = iso_client(iso_env, "b.manager@iso.example", TENANT_B)
    r = c.get("/api/analytics/investigacao", params={"competencia": "2026-07"})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["rede"] is None
    assert j["decomposicao"]["por_prestador"] == []
    assert "provider_intelligence" in j["restricoes_plano"]
    assert all(c_["beneficiarios_amostra"] == [] for c_ in j["coortes"]["coortes"])
    assert all(m["prestador"] is None for m in j["precos"]["mudancas_de_tabela"])
    assert c.get("/api/analytics/investigacao",
                 params={"competencia": "2026-07", "dimensao": "prestador", "chave": "1"}).status_code == 403


def _gold(iso_env, tenant: str, c: date) -> float:
    with iso_env.owner() as s:
        return float(s.execute(text("SELECT despesa_liquida FROM agg_sinistralidade_competencia "
                                    "WHERE tenant_id = :t AND competencia = :c"),
                               {"t": tenant, "c": c}).scalar_one())


def test_isolamento_mesmos_codigos_de_negocio(iso_env, client_a):
    c, b = date(2026, 7, 1), date(2026, 6, 1)
    r = client_a.get("/api/analytics/investigacao", params={"competencia": "2026-07"})
    assert r.status_code == 200, r.text
    assert r.json()["variacao"]["delta_liquida"] == pytest.approx(
        _gold(iso_env, TENANT_A, c) - _gold(iso_env, TENANT_A, b), abs=0.05)
    with iso_env.owner() as s:
        ctr_b = s.execute(text("SELECT min(id) FROM contratos WHERE tenant_id = :t"), {"t": TENANT_B}).scalar_one()
        proc_b = s.execute(text("SELECT min(id) FROM procedimentos WHERE tenant_id = :t"),
                           {"t": TENANT_B}).scalar_one()
    # contrato de B é indistinguível de inexistente
    assert client_a.get("/api/analytics/investigacao",
                        params={"competencia": "2026-07", "contrato_id": ctr_b}).status_code == 404
    # procedimento de B (mesmo código de negócio que um de A): nenhum evento atravessa
    r = client_a.get("/api/analytics/investigacao",
                     params={"competencia": "2026-07", "dimensao": "procedimento", "chave": proc_b})
    assert r.status_code == 200
    v = r.json()["variacao"]
    assert v["eventos_anterior"] == 0 and v["eventos_atual"] == 0


def test_explicar_declara_a_base_dos_fatores(db):
    from app.analytics import decomposition

    for comp in (date(2026, 7, 1), date(2026, 11, 1)):
        rb = decomposition.explicar(db, comp, "mes_anterior", "especialidade")["reconciliacao_base"]
        assert rb["ok"], rb
        assert rb["soma_fatores"] - rb["delta_coparticipacao"] == pytest.approx(
            rb["variacao_despesa_liquida"], abs=0.05)
