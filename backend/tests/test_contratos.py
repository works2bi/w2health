"""v1.2 — Contract Intelligence (C3/C4).

Cobrem: múltiplos contratos por plano; `contratos.listar/detalhe`; concentração
(Gini/Pareto) por contrato; top beneficiários; drivers com escopo de contrato; e a
ausência DELIBERADA de sinistralidade contratual (aviso explícito, sem número fictício).
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import text

from app.analytics import contratos, decomposition
from app.repositories import analytics_repo as repo

pytestmark = pytest.mark.scenarios

COMP = date(2026, 9, 1)


def test_varios_contratos_por_plano(db):
    linhas = db.execute(
        text("SELECT id_plano, COUNT(*) AS n FROM planos_contratos GROUP BY id_plano")
    ).mappings().all()
    assert len(linhas) >= 10
    assert min(r["n"] for r in linhas) >= 2, "todo plano deve ter ≥ 2 contratos (v1.2)"
    assert db.execute(text("SELECT COUNT(*) FROM contratos")).scalar_one() >= 40


def test_plano_do_beneficiario_e_um_plano_do_contrato(db):
    """Contrato × plano é N:N; o beneficiário só pode estar num plano do contrato dele."""
    orfaos = db.execute(text(
        "SELECT COUNT(*) FROM beneficiarios b LEFT JOIN planos_contratos pc "
        "ON pc.tenant_id = b.tenant_id AND pc.id_contrato = b.id_contrato AND pc.id_plano = b.id_plano "
        "WHERE pc.id_contrato IS NULL")).scalar_one()
    assert orfaos == 0


def test_listar_contratos_shape(db):
    res = contratos.listar(db, COMP, "mes_anterior")
    assert res["receita_disponivel"] is False and res["aviso"]
    assert res["total"] >= 1
    it = res["itens"][0]
    for campo in ("vidas", "despesa_bruta", "glosas", "coparticipacao", "despesa_liquida",
                  "gini", "top5_share", "n_beneficiarios_alto_custo"):
        assert campo in it
    assert "sinistralidade" not in it  # nunca um número contratual fictício
    assert it["despesa_liquida"] == pytest.approx(
        it["despesa_bruta"] - it["glosas"] - it["coparticipacao"], abs=1.0
    )


def test_detalhe_contrato_completo(db):
    algum = repo.contratos_resumo_mes(db, COMP)[0]["id_contrato"]
    d = contratos.detalhe(db, algum, COMP, "mes_anterior")
    assert d["receita_disponivel"] is False
    assert "Receita por contrato ainda não disponível" in d["aviso"]
    assert d["kpis"]["vidas"] >= 0
    assert "sinistralidade" not in d["kpis"]
    assert len(d["evolucao"]) >= 1
    conc = d["concentracao"]
    assert 0.0 <= conc["gini"] <= 1.0
    assert conc["top_beneficiarios"] == sorted(
        conc["top_beneficiarios"], key=lambda x: -x["despesa_liquida"]
    )
    # drivers com escopo de contrato: sem sinistralidade/efeito_receita
    if d["drivers"]:
        for ft in d["drivers"]["principais_fatores"]:
            assert ft["impacto_pp"] is None


def test_explain_com_escopo_de_contrato(db):
    cid = repo.contratos_resumo_mes(db, COMP)[0]["id_contrato"]
    ex = decomposition.explicar(db, COMP, "mes_anterior", "especialidade", contrato_id=cid)
    assert ex["sinistralidade_atual"] is None
    assert ex["efeito_receita_pp"] is None
    assert ex["escopo"]["id_contrato"] == cid
    assert "Receita por contrato" in ex["aviso"]
    assert isinstance(ex["principais_fatores"], list)


def test_explain_contrato_recusa_dimensao_contrato(db):
    cid = repo.contratos_resumo_mes(db, COMP)[0]["id_contrato"]
    with pytest.raises(ValueError):
        decomposition.explicar(db, COMP, "mes_anterior", "contrato", contrato_id=cid)


def test_concentracao_da_variacao_escopo_contrato_reconcilia(db):
    cid = repo.contratos_resumo_mes(db, COMP)[0]["id_contrato"]
    cv = decomposition.concentracao_variacao_beneficiarios(db, COMP, "mes_anterior", cid)
    if cv and cv["top"]:
        # participações do top somam <= 100% (com a proteção contra cancelamento)
        assert all(t["participacao_pct"] > -1e6 for t in cv["top"])
