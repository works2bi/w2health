"""Benchmark de mercado — dados abertos ANS × operadora (Evolução, item 7)."""

from __future__ import annotations

import pytest

from app.analytics import benchmark

pytestmark = pytest.mark.scenarios


def test_sem_dados_abertos_usa_referencia_ilustrativa_e_avisa(gestor):
    r = gestor.get("/api/analytics/sinistralidade/benchmark?competencia=2026-12&segmento=cooperativa_medica")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["ilustrativo"] is True and "ILUSTRATIVA" in d["aviso"]
    assert d["janela"] == {"inicio": "2026-01-01", "fim": "2026-12-01", "meses": 12}
    sin = next(i for i in d["itens"] if i["indicador"] == "sinistralidade_pct")
    assert sin["operadora"] is not None and sin["referencia"]["mediana"] == 84
    assert sin["posicao"] in benchmark.ROTULO_POSICAO
    assert sin["diferenca_mediana"] == pytest.approx(sin["operadora"] - 84, abs=0.01)


def test_segmento_invalido(gestor):
    assert gestor.get("/api/analytics/sinistralidade/benchmark?segmento=xyz").status_code == 422


def _demonstracoes(ops: dict[str, tuple[float, float]]) -> bytes:
    linhas = ["DATA;REG_ANS;CD_CONTA_CONTABIL;DESCRICAO;VL_SALDO_INICIAL;VL_SALDO_FINAL"]
    for reg, (rec, desp) in ops.items():
        linhas.append(f'2025-10-01;{reg};311;"CONTRAPRESTAÇÕES EFETIVAS";0;{rec:.2f}'.replace(".", ","))
        linhas.append(f'2025-10-01;{reg};411;"EVENTOS INDENIZÁVEIS LÍQUIDOS";0;{-desp:.2f}'.replace(".", ","))
    return "\n".join(linhas).encode()


def test_calculo_a_partir_dos_dados_abertos():
    ops = {str(100000 + i): (1000.0, 700.0 + 20 * i) for i in range(6)}  # 70%..80%
    ops["999999"] = (1000.0, 5000.0)  # 500% — atípica, descartada
    ops["888888"] = (1000.0, 900.0)   # sem modalidade conhecida no CADOP
    cadop = "Registro_ANS;CNPJ;Razao_Social;Modalidade\n" + "\n".join(
        f"{r};0;X;Medicina de Grupo" for r in ops if r != "888888") + "\n888888;0;Y;Administradora"
    vidas = "REG_ANS;QT_BENEFICIARIOS\n" + "\n".join(f"{r};100" for r in ops)
    linhas = benchmark.calcular_de_dados_abertos(_demonstracoes(ops), cadop.encode(), "2025", vidas.encode())
    sin = next(x for x in linhas if x["segmento"] == "medicina_de_grupo" and x["indicador"] == "sinistralidade_pct")
    assert sin["n_operadoras"] == 6
    assert sin["mediana"] == pytest.approx(75.0)
    assert sin["media_ponderada"] == pytest.approx(75.0)
    pmpm = next(x for x in linhas if x["segmento"] == "medicina_de_grupo" and x["indicador"] == "despesa_pmpm")
    assert pmpm["mediana"] == pytest.approx(750.0 / 100 / 12, abs=0.01)
    assert {x["segmento"] for x in linhas} == {"medicina_de_grupo", "mercado"}


def test_dado_real_tem_precedencia(db):
    from sqlalchemy import delete

    from app.models.benchmark import BenchmarkMercado

    linha = {"fonte": "ans_dados_abertos", "periodo": "2025", "segmento": "autogestao",
             "indicador": "sinistralidade_pct", "p25": 80.0, "mediana": 88.0, "p75": 95.0,
             "media_ponderada": 89.0, "n_operadoras": 40}
    try:
        benchmark.gravar(db, [linha])
        from datetime import date

        d = benchmark.comparar(db, date(2026, 12, 1), "autogestao")
        assert d["fonte"] == "ans_dados_abertos" and d["ilustrativo"] is False and d["aviso"] is None
        sin = next(i for i in d["itens"] if i["indicador"] == "sinistralidade_pct")
        assert sin["referencia"]["n_operadoras"] == 40
    finally:
        db.execute(delete(BenchmarkMercado).where(BenchmarkMercado.fonte == "ans_dados_abertos"))
        db.commit()
