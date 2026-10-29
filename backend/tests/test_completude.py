"""Completude da competência (Evolução, item 1): chain-ladder sobre a apresentação."""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.analytics import completude
from app.db.session import get_db
from app.main import create_app
from app.repositories import analytics_repo as repo

pytestmark = pytest.mark.scenarios

@pytest.fixture
def api(seeded_sessionmaker, auth_headers) -> TestClient:
    app = create_app()

    def _get_db():
        s: Session = seeded_sessionmaker()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _get_db
    return TestClient(app, headers=auth_headers)


def test_endpoint_marca_ultima_competencia_como_preliminar(api):
    r = api.get("/api/executive/completude")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["disponivel"] is True
    assert d["cobertura_data_apresentacao_pct"] == 100.0
    sel = d["selecionada"]
    assert sel["competencia"] == d["meses"][-1]["competencia"]
    assert sel["status"] == "preliminar"
    assert 0 < sel["completude_pct"] < 100
    assert sel["ibnr_estimado"] > 0
    assert sel["despesa_liquida_estimada_final"] > sel["despesa_liquida_observada"]
    assert d["rotulos"]["estimativa_final"] == "ESTIMATIVA"


def test_competencias_antigas_fechadas_e_fatores_maiores_que_um(api):
    d = api.get("/api/executive/completude").json()
    antigas = [m for m in d["meses"] if m["idade_meses"] >= completude.MAX_DEFASAGEM]
    assert antigas and all(m["status"] == "fechada" and m["ibnr_estimado"] == 0 for m in antigas)
    assert all(f["fator"] >= 1.0 for f in d["fatores"])
    assert d["fatores"][0]["fator"] > 1.0  # parte relevante chega no mês seguinte


def test_ultima_competencia_projeta_o_que_falta_chegar(api):
    d = api.get("/api/executive/completude?competencia=2026-12").json()
    sel = d["selecionada"]
    # a carga da demo não traz as contas de dez/2026 apresentadas depois do corte
    assert sel["completude_pct"] < 80
    assert sel["sinistralidade_projetada"] > sel["sinistralidade_observada"]


def test_observado_reconcilia_com_a_camada_analitica(db):
    res = completude.calcular(db)
    gold = {r["competencia"].isoformat(): float(r["despesa_liquida"])
            for r in repo.serie_sinistralidade(db)}
    for m in res["meses"]:
        assert abs(m["despesa_liquida_observada"] - gold[m["competencia"]]) < 1.0


def test_sem_data_de_apresentacao_nao_estima(db):
    db.execute(text("UPDATE eventos_assistenciais SET competencia_apresentacao = NULL "
                    "WHERE tenant_id = 'w2h-demo'"))
    try:
        res = completude.calcular(db)
    finally:
        db.rollback()
    assert res["disponivel"] is False and "apresentação" in res["motivo"]
    assert res["meses"] == []


def test_competencia_especifica(api):
    d = api.get("/api/executive/completude?competencia=2025-06").json()
    assert d["competencia"] == date(2025, 6, 1).isoformat()


def test_relatorio_mensal_consolida_os_blocos(api):
    r = api.get("/api/executive/relatorio-mensal?competencia=2026-12")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["competencia"] == "2026-12-01" and d["kpis"]["sinistralidade"] is not None
    assert d["completude"]["status"] == "preliminar"
    assert d["principais_fatores"] is not None and d["alertas"] is not None
    assert d["mercado"]["ilustrativo"] is True
    assert d["restricoes_plano"] == []
    assert "BEN-0" not in r.text
