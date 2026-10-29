"""Simulador de reajuste do contrato (Evolução, item 2)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.main import create_app

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


def _contrato(db) -> int:
    return db.execute(text(
        "SELECT id_contrato FROM agg_contrato_competencia WHERE tenant_id = 'w2h-demo' "
        "GROUP BY 1 ORDER BY SUM(despesa_liquida) DESC LIMIT 1")).scalar_one()


def test_sem_receita_atribuida_pede_receita_informada(api, db):
    r = api.get(f"/api/analytics/contratos/{_contrato(db)}/simulacao-reajuste")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["disponivel"] is False and d["exige_receita_informada"] is True
    assert "não rateia" in d["motivo"]


def test_formula_com_receita_informada(api, db):
    cid = _contrato(db)
    url = (f"/api/analytics/contratos/{cid}/simulacao-reajuste?meta=70&vcmh=10&meses=12"
           "&ajustar_completude=false&receita_mensal=")
    d0 = api.get(url + "1").json()
    desp = d0["historico"]["despesa_liquida_considerada"]
    receita_mensal = round(desp / 12 / 0.80, 2)  # sinistralidade histórica ≈ 80%
    d = api.get(url + str(receita_mensal)).json()
    assert d["disponivel"] and d["fonte_receita"] == "informada"
    assert d["historico"]["rotulo"].startswith("HIPÓTESE")
    assert abs(d["historico"]["sinistralidade_pct"] - 80.0) < 0.1
    # 80% × 1,10 ÷ 70% − 1 = 25,71%
    assert abs(d["projecao"]["reajuste_tecnico_pct"] - 25.71) < 0.05
    no_ponto = [c for c in d["cenarios"] if abs(c["reajuste_pct"] - 25.8) < 0.01]  # arredonda p/ cima
    assert no_ponto and no_ponto[0]["atinge_meta"] and abs(no_ponto[0]["sinistralidade_projetada"] - 70.0) < 0.1
    assert [c["atinge_meta"] for c in d["cenarios"] if c["reajuste_pct"] == 30.0] == [True]
    assert [c["atinge_meta"] for c in d["cenarios"] if c["reajuste_pct"] == 20.0] == [False]


def test_ajuste_de_completude_aumenta_despesa_considerada(api, db):
    cid = _contrato(db)
    base = f"/api/analytics/contratos/{cid}/simulacao-reajuste?receita_mensal=100000"
    sem = api.get(base + "&ajustar_completude=false").json()
    com = api.get(base).json()
    assert com["historico"]["despesa_liquida_considerada"] > sem["historico"]["despesa_liquida_considerada"]
    assert com["historico"]["ajuste_completude"] > 0
    assert any(x["preliminar"] for x in com["serie"])


def test_premissas_invalidas_e_contrato_inexistente(api, db):
    cid = _contrato(db)
    assert api.get(f"/api/analytics/contratos/{cid}/simulacao-reajuste?meta=0").status_code == 422
    assert api.get("/api/analytics/contratos/999999/simulacao-reajuste").status_code == 404
