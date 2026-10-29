"""Planos de ação + medição de efeito (Evolução, item 3)."""

from __future__ import annotations

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.scenarios


def _chave_top(db) -> str:
    return db.execute(text(
        "SELECT chave FROM agg_competencia_dimensao WHERE tenant_id = 'w2h-demo' "
        "AND dimensao = 'especialidade' GROUP BY chave ORDER BY SUM(despesa_liquida) DESC LIMIT 1"
    )).scalar_one()


def test_ciclo_completo_com_efeito_medido(gestor, db):
    chave = _chave_top(db)
    r = gestor.post("/api/planos-acao", json={
        "titulo": "Auditoria de contas da especialidade", "origem_tipo": "insight",
        "origem_ref": "concentracao_especialidade", "dimensao": "especialidade", "chave": chave,
        "responsavel": "Coordenação de Regulação", "competencia_inicio": "2026-04-15",
        "meta_reducao_pct": 5,
    })
    assert r.status_code == 201, r.text
    p = r.json()
    assert p["competencia_inicio"] == "2026-04-01"
    ef = p["efeito"]
    assert ef["situacao"] == "medido"
    assert ef["meses_antes"] == ["2026-01-01", "2026-02-01", "2026-03-01"]
    assert ef["meses_depois"] == ["2026-04-01", "2026-05-01", "2026-06-01"]
    assert ef["rotulos"]["efeito_relativo_pp"] == "HIPÓTESE"
    assert ef["efeito_relativo_pp"] == pytest.approx(
        ef["variacao_alvo_pct"] - ef["variacao_restante_pct"], abs=0.02)
    assert isinstance(ef["atingiu_meta"], bool)

    r = gestor.patch(f"/api/planos-acao/{p['id']}", json={"status": "em_andamento"})
    assert r.status_code == 200 and r.json()["status"] == "em_andamento"
    assert any(i["id"] == p["id"] for i in gestor.get("/api/planos-acao").json()["itens"])
    assert gestor.delete(f"/api/planos-acao/{p['id']}").status_code == 204
    assert gestor.get(f"/api/planos-acao/{p['id']}").status_code == 404


def test_acao_recente_aguarda_competencia_fechada(gestor):
    r = gestor.post("/api/planos-acao", json={
        "titulo": "Ação na carteira", "responsavel": "Diretoria", "competencia_inicio": "2026-12-01"})
    assert r.status_code == 201, r.text
    ef = r.json()["efeito"]
    assert ef["situacao"] == "aguardando_dados"
    assert "2026-12-01" in ef["meses_preliminares_ignorados"]
    gestor.delete(f"/api/planos-acao/{r.json()['id']}")


def test_viewer_le_mas_nao_escreve(leitor):
    assert leitor.get("/api/planos-acao").status_code == 200
    r = leitor.post("/api/planos-acao", json={
        "titulo": "x" * 5, "responsavel": "Fulano", "competencia_inicio": "2026-01-01"})
    assert r.status_code == 403


def test_dimensao_sem_chave_rejeitada(gestor):
    r = gestor.post("/api/planos-acao", json={
        "titulo": "Sem chave", "responsavel": "Fulano", "competencia_inicio": "2026-01-01",
        "dimensao": "especialidade"})
    assert r.status_code == 422
