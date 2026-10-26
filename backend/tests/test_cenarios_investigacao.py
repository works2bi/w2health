"""Laboratório de cenários da investigação de custos (Evolução V1) — resultado conhecido.

Banco próprio (`w2health_test_cenarios`) com o tenant `w2h-cenarios` (um contrato por
cenário, preços fixos, contagens controladas — `app/seed/cenarios_investigacao.py`). O motor
só recebe `contrato_id`; o gabarito diz o que ele deve encontrar.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import sessionmaker

from app.analytics import decomposicao_estrutural as de
from app.analytics import investigacao
from app.db.tenant_scope import bind_tenant
from app.seed import cenarios_investigacao as lab
from tests.conftest import recreate_database

pytestmark = pytest.mark.scenarios

PROIBIDAS = ("crônic", "cronic", "deficitári", "sinistralidade individual")


@pytest.fixture(scope="session")
def lab_maker():
    import app.models  # noqa: F401
    from app.db.base import create_all

    engine, _ = recreate_database("w2health_test_cenarios")
    create_all(engine)
    Maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with Maker() as s:
        lab.run_lab(s, verbose=False)
    yield Maker
    engine.dispose()


@pytest.fixture(scope="session")
def resultados(lab_maker) -> dict[str, dict]:
    from app.repositories import analytics_repo

    out = {}
    with lab_maker() as s:
        bind_tenant(s, lab.TENANT)
        for g in analytics_repo.gabarito(s):
            out[g["codigo"]] = investigacao.investigar(
                s, lab.ALVO, "mes_anterior", 1, contrato_id=int(g["chave_alvo"]))
    return out


def _ef(r: dict) -> dict[str, float | None]:
    return {e["codigo"]: e["valor"] for e in r["decomposicao"]["efeitos"]}


def _codigos(r: dict, tipo: str | None = None) -> set[str]:
    return {a["codigo"] for a in r["achados"] if tipo is None or a["tipo"] == tipo}


def _dominante(r: dict) -> str:
    ef = {k: v for k, v in _ef(r).items() if v is not None and k not in (de.GLOSA, de.COPARTICIPACAO)}
    return max(ef, key=lambda k: abs(ef[k]))


def _d0(r: dict) -> float:
    return r["decomposicao"]["totais"]["despesa_bruta_anterior"]


# ------------------------------------------------------------------ invariantes
def test_todos_os_cenarios_estao_no_gabarito(resultados):
    assert len(resultados) == 10


@pytest.mark.parametrize("codigo", [c["codigo"] for c in lab._cenarios()])
def test_reconciliacao_exata_e_sem_linguagem_clinica(resultados, codigo):
    r = resultados[codigo]
    rec = r["decomposicao"]["reconciliacao"]
    assert rec["ok"] and abs(rec["diferenca"]) < 0.01
    assert rec["delta_observado"] == pytest.approx(r["variacao"]["delta_liquida"], abs=0.01)
    textos = " ".join(a["titulo"] + " " + a["texto"] for a in r["achados"]).lower()
    assert not any(p in textos for p in PROIBIDAS)
    for a in r["achados"]:
        if a["tipo"] == "HIPOTESE":
            assert a["proximo_passo"], a["codigo"]  # hipótese sempre diz o que verificar
        for campo in ("indicador", "periodo", "escopo", "evidencias", "limitacoes"):
            assert campo in a


# ------------------------------------------------------------------ cenários
def test_01_frequencia(resultados):
    r = resultados["inv01_frequencia"]
    ef = _ef(r)
    assert ef[de.FREQUENCIA] == pytest.approx(0.4 * _d0(r), rel=1e-6)
    for k in (de.CUSTO_UNITARIO, de.MIX_PROCEDIMENTOS, de.MIX_PRESTADORES, de.PERFIL_ETARIO):
        assert abs(ef[k]) < 1.0, k
    assert "efeito_frequencia" in _codigos(r, "FATO")
    assert not _codigos(r, "HIPOTESE") & {"hipotese_custo_unitario", "hipotese_rede"}


def test_02_custo_medio_sem_tabela_nao_e_reajuste(resultados):
    r = resultados["inv02_custo_medio"]
    ef = _ef(r)
    assert _dominante(r) == de.CUSTO_UNITARIO
    assert ef[de.CUSTO_UNITARIO] == pytest.approx(0.15 * _d0(r), rel=1e-3)
    assert abs(ef[de.FREQUENCIA]) < 1.0
    assert ef[de.PRECO_CONTRATUAL] is None  # sem tabela: não calculável, nunca "zero reajuste"
    assert "efeito_preco_contratual" not in _codigos(r)
    # a tabela do tenant (reajuste do CEN-07) não vaza como evidência para outro contrato
    assert r["precos"]["pares_com_tabela"] == 0 and r["precos"]["mudancas_de_tabela"] == []
    assert "hipotese_custo_unitario" in _codigos(r, "HIPOTESE")
    fato = next(a for a in r["achados"] if a["codigo"] == "efeito_custo_unitario")
    assert "observado" in fato["titulo"].lower()
    assert any("não é afirmado como reajuste" in lim.lower() for lim in fato["limitacoes"])


def test_03_migracao_para_hospital_mais_caro(resultados):
    r = resultados["inv03_migracao_hospital"]
    ef = _ef(r)
    assert ef[de.MIX_PRESTADORES] == pytest.approx(44100.0, abs=0.5)
    assert abs(ef[de.CUSTO_UNITARIO]) < 1.0 and abs(ef[de.FREQUENCIA]) < 1.0
    assert {"efeito_mix_prestadores"} <= _codigos(r, "FATO")
    assert "hipotese_rede" in _codigos(r, "HIPOTESE")
    transf = {t["procedimento"]: t for t in r["rede"]["transferencias"]}
    beta = [m for t in transf.values() for m in t["movimentos"] if m["prestador"] == "Hospital Beta"]
    assert beta and all(m["participacao_atual"] > m["participacao_anterior"] for m in beta)


def test_04_mix_de_procedimentos_com_frequencia_estavel(resultados):
    r = resultados["inv04_mix_procedimentos"]
    ef = _ef(r)
    assert r["variacao"]["eventos_anterior"] == r["variacao"]["eventos_atual"]
    assert ef[de.MIX_PROCEDIMENTOS] == pytest.approx(212256.0, abs=0.5)
    assert abs(ef[de.FREQUENCIA]) < 1.0 and abs(ef[de.MIX_PRESTADORES]) < 1.0
    assert "efeito_mix_procedimentos" in _codigos(r, "FATO")


def test_05_concentracao_em_recorrentes_sem_inferencia_clinica(resultados):
    r = resultados["inv05_recorrentes"]
    c = {x["codigo"]: x for x in r["coortes"]["coortes"]}
    rec = c["uso_recorrente"]["delta"] + c["alto_custo_historico"]["delta"]
    assert rec / r["variacao"]["delta_liquida"] >= 0.9
    assert r["coortes"]["reconciliacao"]["soma_coortes"] == pytest.approx(
        r["variacao"]["delta_liquida"], abs=0.01)
    assert "coorte_recorrente" in _codigos(r, "FATO")
    assert "hipotese_utilizacao_continuada" in _codigos(r, "HIPOTESE")
    assert "diagnóstico" in r["coortes"]["nota"]
    linha = next(x for x in r["cobertura_dados"] if x["fator"] == "Condição crônica")
    assert linha["disponivel"] is False


def test_06_perfil_etario(resultados):
    r = resultados["inv06_perfil"]
    ef = _ef(r)
    assert ef[de.PERFIL_ETARIO] > 0 and ef[de.POPULACAO] > 0
    assert ef[de.PERFIL_ETARIO] + ef[de.POPULACAO] == pytest.approx(r["variacao"]["despesa_bruta_atual"]
                                                                    - _d0(r), abs=1.0)
    assert abs(ef[de.FREQUENCIA]) < 1.0  # taxas por faixa constantes
    assert {"efeito_perfil_etario", "efeito_populacao"} <= _codigos(r, "FATO")


def test_07_reajuste_com_evidencia_de_tabela(resultados):
    r = resultados["inv07_reajuste_tabela"]
    ef = _ef(r)
    assert ef[de.PRECO_CONTRATUAL] == pytest.approx(18350.0, abs=0.5)
    assert abs(ef[de.CUSTO_UNITARIO]) < 1.0
    assert "efeito_preco_contratual" in _codigos(r, "FATO")
    assert "hipotese_custo_unitario" not in _codigos(r)
    mud = r["precos"]["mudancas_de_tabela"]
    assert len(mud) == 4 and all(m["variacao_pct"] == pytest.approx(10.0, abs=0.01) for m in mud)
    assert all(m["vigencia_inicio"] == lab.ALVO.isoformat() for m in mud)


def test_08_aumento_aparente_por_apresentacao_tardia(resultados):
    r = resultados["inv08_defasagem"]
    assert r["defasagem"]["delta_defasada"] == pytest.approx(r["variacao"]["delta_liquida"], abs=0.01)
    assert r["defasagem"]["delta_mesmo_mes"] == pytest.approx(0.0, abs=0.01)
    assert "defasagem" in _codigos(r, "FATO")
    assert "hipotese_defasagem" in _codigos(r, "HIPOTESE")


def test_09_reducao_de_custo(resultados):
    r = resultados["inv09_reducao"]
    ef = _ef(r)
    assert r["variacao"]["delta_liquida"] < 0
    assert ef[de.FREQUENCIA] == pytest.approx(-0.4 * _d0(r), rel=1e-6)
    assert "diminuiu" in r["achados"][0]["titulo"]


def test_10_dados_insuficientes(resultados):
    r = resultados["inv10_insuficiente"]
    assert r["dados_suficientes"] is False
    assert not _codigos(r, "HIPOTESE")  # nenhuma hipótese com 6 vidas
    assert {"dados_insuficientes", "baseline_insuficiente"} <= _codigos(r, "INVESTIGAR")
    assert r["historico"]["despesa_liquida"]["classificacao"] == "indeterminado"


def test_contrato_sem_escopo_e_operadora_inteira_tambem_reconcilia(lab_maker):
    with lab_maker() as s:
        bind_tenant(s, lab.TENANT)
        r = investigacao.investigar(s, lab.ALVO, "mes_anterior", 1)
        assert r["decomposicao"]["reconciliacao"]["ok"]
        r3 = investigacao.investigar(s, lab.ALVO, "mes_anterior", 3)
        assert r3["decomposicao"]["reconciliacao"]["ok"]
        assert r3["periodos"]["base"] == {"inicio": "2026-01-01", "fim": "2026-03-01"}
