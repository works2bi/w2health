"""Testes matemáticos da decomposição estrutural (Evolução V1) — sem banco.

Cada cenário tem resultado conhecido calculado à mão. Invariantes verificados em todos:
reconciliação exata (Σ efeitos = Δ despesa líquida) e ausência de dupla contagem (cada
real de variação aparece em um único efeito).
"""

from __future__ import annotations

import random

import pytest

from app.analytics import decomposicao_estrutural as de
from app.analytics import formulas as f
from app.analytics.decomposicao_estrutural import Celula, Periodo


def _ef(res, codigo):
    return res.efeitos[codigo].valor


def _assert_reconcilia(res):
    assert res.reconciliacao["ok"], res.reconciliacao
    soma = sum(e.valor for e in res.efeitos.values() if e.valor is not None)
    assert soma == pytest.approx(res.reconciliacao["delta_observado"], abs=0.01)


def _so(res, *codigos, tol=0.01):
    """Todos os efeitos fora de `codigos` são (numericamente) zero ou indisponíveis."""
    for c, e in res.efeitos.items():
        if c not in codigos and e.valor is not None:
            assert abs(e.valor) <= tol, (c, e.valor)


def _cel(proc, prest, n, valor_unit, faixa="30-39", q_por_evento=1.0):
    return Celula(proc, prest, n, n * q_por_evento, n * valor_unit * q_por_evento, faixa)


EXPO = {"30-39": 1000.0}


# ------------------------------------------------------------------ Shapley
def test_shapley_com_dois_fatores_e_o_bennet():
    n0, p0, n1, p1 = 120.0, 85.0, 150.0, 92.0
    phi_n, phi_p = de.shapley_produto([n0, p0], [n1, p1])
    b = f.bennet_bridge(n0, p0, n1, p1)
    assert phi_n == pytest.approx(b.efeito_frequencia)
    assert phi_p == pytest.approx(b.efeito_custo_medio)


@pytest.mark.parametrize("n", [3, 4, 5])
def test_shapley_eficiencia_soma_exata(n):
    rnd = random.Random(n)
    x0 = [rnd.uniform(0.5, 3) for _ in range(n)]
    x1 = [rnd.uniform(0.5, 3) for _ in range(n)]
    prod0 = prod1 = 1.0
    for a, b in zip(x0, x1, strict=True):
        prod0 *= a
        prod1 *= b
    assert sum(de.shapley_produto(x0, x1)) == pytest.approx(prod1 - prod0)


def test_shapley_simetria_fatores_iguais_recebem_o_mesmo():
    phi = de.shapley_produto([1.0, 1.0, 5.0], [2.0, 2.0, 5.0])
    assert phi[0] == pytest.approx(phi[1])
    assert phi[2] == pytest.approx(0.0)


# ------------------------------------------------------------------ cenários A–D do especialista
def test_cenario_a_mais_atendimentos_custo_estavel_e_frequencia():
    base = Periodo([_cel("CONS", "H1", 100, 100), _cel("EXA", "H2", 50, 300)], EXPO)
    atual = Periodo([_cel("CONS", "H1", 130, 100), _cel("EXA", "H2", 65, 300)], EXPO)
    res = de.decompor(base, atual)
    _assert_reconcilia(res)
    assert _ef(res, de.FREQUENCIA) == pytest.approx(30 * 100 + 15 * 300)
    _so(res, de.FREQUENCIA)


def test_cenario_b_frequencia_estavel_custo_unitario_sobe_sem_tabela():
    base = Periodo([_cel("CONS", "H1", 100, 100), _cel("EXA", "H2", 50, 300)], EXPO)
    atual = Periodo([_cel("CONS", "H1", 100, 115), _cel("EXA", "H2", 50, 345)], EXPO)
    res = de.decompor(base, atual)
    _assert_reconcilia(res)
    assert _ef(res, de.CUSTO_UNITARIO) == pytest.approx(100 * 15 + 50 * 45)
    # sem tabela de preço: NÃO há efeito de preço contratual (indisponível, não zero)
    assert res.efeitos[de.PRECO_CONTRATUAL].valor is None
    assert not res.efeitos[de.PRECO_CONTRATUAL].disponivel
    _so(res, de.CUSTO_UNITARIO)


def test_cenario_c_mix_de_procedimentos_mais_complexos_com_frequencia_estavel():
    # 100 eventos nos dois períodos; a parcela de cirurgia (R$ 1.000) dobra
    base = Periodo([_cel("CONS", "H1", 90, 100), _cel("CIR", "H1", 10, 1000)], EXPO)
    atual = Periodo([_cel("CONS", "H1", 80, 100), _cel("CIR", "H1", 20, 1000)], EXPO)
    res = de.decompor(base, atual)
    _assert_reconcilia(res)
    assert _ef(res, de.MIX_PROCEDIMENTOS) == pytest.approx(9000.0)
    _so(res, de.MIX_PROCEDIMENTOS)


def test_cenario_d_volume_migra_para_prestador_mais_caro():
    # mesmo procedimento, mesmo total de eventos e preços estáveis em cada hospital
    base = Periodo([_cel("INT", "HA", 80, 100), _cel("INT", "HB", 20, 200)], EXPO)
    atual = Periodo([_cel("INT", "HA", 40, 100), _cel("INT", "HB", 60, 200)], EXPO)
    res = de.decompor(base, atual)
    _assert_reconcilia(res)
    assert _ef(res, de.MIX_PRESTADORES) == pytest.approx(4000.0)
    _so(res, de.MIX_PRESTADORES)
    hb = next(p for p in res.por_prestador if p["chave"] == "HB")
    ha = next(p for p in res.por_prestador if p["chave"] == "HA")
    # HB (mais caro que a média do procedimento) ganhou volume: +0,4 × (200 − 140) × 100;
    # HA (mais barato) PERDEU volume — perder volume barato também encarece: −0,4 × (100 − 140) × 100
    assert hb["efeitos"][de.MIX_PRESTADORES] == pytest.approx(2400.0)
    assert ha["efeitos"][de.MIX_PRESTADORES] == pytest.approx(1600.0)


def test_mix_nao_e_contado_como_frequencia_nem_custo_medio():
    """O bridge antigo (bridge_composto) jogaria o cenário C em 'frequência'; o novo não."""
    base = Periodo([_cel("CONS", "H1", 90, 100), _cel("CIR", "H1", 10, 1000)], EXPO)
    atual = Periodo([_cel("CONS", "H1", 80, 100), _cel("CIR", "H1", 20, 1000)], EXPO)
    res = de.decompor(base, atual)
    antigo = f.bridge_composto([(90, 100, 80, 100), (10, 1000, 20, 1000)], 100, 100)
    assert antigo.efeito_frequencia == pytest.approx(9000.0)  # comportamento preservado
    assert abs(_ef(res, de.FREQUENCIA)) < 0.01
    assert abs(_ef(res, de.CUSTO_UNITARIO)) < 0.01


# ------------------------------------------------------------------ preço contratual
def test_reajuste_com_tabela_vira_preco_contratual():
    base = Periodo([_cel("INT", "HA", 50, 1000)], EXPO, precos={("INT", "HA"): 1000.0})
    atual = Periodo([_cel("INT", "HA", 50, 1100)], EXPO, precos={("INT", "HA"): 1100.0})
    res = de.decompor(base, atual)
    _assert_reconcilia(res)
    assert _ef(res, de.PRECO_CONTRATUAL) == pytest.approx(5000.0)
    _so(res, de.PRECO_CONTRATUAL)
    assert res.cobertura["despesa_com_tabela_de_preco"] == pytest.approx(1.0)


def test_tabela_explica_so_parte_do_aumento_o_resto_fica_nao_isolado():
    # tabela +10%, mas o valor apresentado subiu 20%
    base = Periodo([_cel("INT", "HA", 50, 1000)], EXPO, precos={("INT", "HA"): 1000.0})
    atual = Periodo([_cel("INT", "HA", 50, 1200)], EXPO, precos={("INT", "HA"): 1100.0})
    res = de.decompor(base, atual)
    _assert_reconcilia(res)
    preco, resto = _ef(res, de.PRECO_CONTRATUAL), _ef(res, de.CUSTO_UNITARIO)
    assert preco + resto == pytest.approx(10000.0)
    assert preco == pytest.approx(50 * 100 * (1.0 + 1200 / 1100) / 2)  # ΔT · ρ̄ · n
    assert resto > 0


def test_tabela_estavel_com_custo_subindo_nao_e_reajuste():
    base = Periodo([_cel("RM", "LAB", 40, 800)], EXPO, precos={("RM", "LAB"): 800.0})
    atual = Periodo([_cel("RM", "LAB", 40, 1040)], EXPO, precos={("RM", "LAB"): 800.0})
    res = de.decompor(base, atual)
    _assert_reconcilia(res)
    assert _ef(res, de.PRECO_CONTRATUAL) == pytest.approx(0.0)
    assert _ef(res, de.CUSTO_UNITARIO) == pytest.approx(40 * 240)


# ------------------------------------------------------------------ população e perfil
def test_perfil_etario_separado_da_taxa_de_utilizacao():
    # taxas por faixa fixas (jovem 0,1 · idoso 0,5); entram 200 idosos
    base = Periodo([_cel("CONS", "H1", 90, 100, "18-29"), _cel("CONS", "H1", 50, 100, "60+")],
                   {"18-29": 900.0, "60+": 100.0})
    atual = Periodo([_cel("CONS", "H1", 90, 100, "18-29"), _cel("CONS", "H1", 150, 100, "60+")],
                    {"18-29": 900.0, "60+": 300.0})
    res = de.decompor(base, atual)
    _assert_reconcilia(res)
    assert _ef(res, de.POPULACAO) == pytest.approx(3400.0)
    assert _ef(res, de.PERFIL_ETARIO) == pytest.approx(6600.0)
    assert abs(_ef(res, de.FREQUENCIA)) < 0.01
    _so(res, de.POPULACAO, de.PERFIL_ETARIO)


def test_sem_exposicao_populacao_e_perfil_indisponiveis_e_frequencia_vira_volume():
    base = Periodo([_cel("CONS", "H1", 100, 100)], None)
    atual = Periodo([_cel("CONS", "H1", 120, 100)], None)
    res = de.decompor(base, atual)
    _assert_reconcilia(res)
    assert res.efeitos[de.POPULACAO].valor is None
    assert res.efeitos[de.PERFIL_ETARIO].valor is None
    assert _ef(res, de.FREQUENCIA) == pytest.approx(2000.0)
    assert "sem exposição" in res.efeitos[de.FREQUENCIA].motivo


# ------------------------------------------------------------------ intensidade
def test_intensidade_mais_sessoes_por_evento():
    base = Periodo([_cel("FIS", "C1", 100, 50, q_por_evento=2)], EXPO)
    atual = Periodo([_cel("FIS", "C1", 100, 50, q_por_evento=3)], EXPO)
    res = de.decompor(base, atual)
    _assert_reconcilia(res)
    assert _ef(res, de.INTENSIDADE) == pytest.approx(100 * 50)
    _so(res, de.INTENSIDADE)


# ------------------------------------------------------------------ não comparável e rede
def test_procedimento_novo_vai_para_nao_comparavel_sem_ser_redistribuido():
    base = Periodo([_cel("CONS", "H1", 100, 100)], EXPO)
    atual = Periodo([_cel("CONS", "H1", 100, 100), _cel("NOVO", "H1", 5, 2000)], EXPO)
    res = de.decompor(base, atual)
    _assert_reconcilia(res)
    assert _ef(res, de.NAO_COMPARAVEL) == pytest.approx(10000.0)
    _so(res, de.NAO_COMPARAVEL)
    assert res.cobertura["procedimentos_nao_comparaveis"] == 1


def test_prestador_novo_mais_caro_e_mudanca_de_rede_nao_custo_unitario():
    base = Periodo([_cel("INT", "HA", 100, 100)], EXPO)
    atual = Periodo([_cel("INT", "HA", 50, 100), _cel("INT", "HN", 50, 300)], EXPO)
    res = de.decompor(base, atual)
    _assert_reconcilia(res)
    assert _ef(res, de.MIX_PRESTADORES) == pytest.approx(10000.0)
    _so(res, de.MIX_PRESTADORES)


# ------------------------------------------------------------------ camada financeira
def test_glosa_e_coparticipacao_levam_da_bruta_a_liquida():
    base = Periodo([_cel("CONS", "H1", 100, 100)], EXPO, glosa=300.0, coparticipacao=200.0)
    atual = Periodo([_cel("CONS", "H1", 100, 100)], EXPO, glosa=800.0, coparticipacao=500.0)
    res = de.decompor(base, atual)
    _assert_reconcilia(res)
    assert _ef(res, de.GLOSA) == pytest.approx(-500.0)
    assert _ef(res, de.COPARTICIPACAO) == pytest.approx(-300.0)
    assert res.reconciliacao["delta_observado"] == pytest.approx(-800.0)


def test_reducao_de_custo_aparece_com_sinal_negativo():
    base = Periodo([_cel("FIS", "C1", 200, 60)], EXPO)
    atual = Periodo([_cel("FIS", "C1", 150, 60)], EXPO)
    res = de.decompor(base, atual)
    _assert_reconcilia(res)
    assert _ef(res, de.FREQUENCIA) == pytest.approx(-3000.0)


# ------------------------------------------------------------------ propriedades gerais
def _aleatorio(seed: int):
    rnd = random.Random(seed)
    procs = [f"P{i}" for i in range(8)]
    prests = [f"H{i}" for i in range(5)]
    faixas = ["0-17", "18-39", "40-59", "60+"]

    def periodo(sumir: set):
        cel = []
        for p in procs:
            for h in prests:
                if (p, h) in sumir or rnd.random() < 0.3:
                    continue
                for fx in faixas:
                    n = rnd.randint(1, 30)
                    q = n * rnd.choice([1, 1, 2, 3])
                    cel.append(Celula(p, h, n, q, q * rnd.uniform(40, 900), fx))
        expo = {fx: rnd.uniform(200, 900) for fx in faixas}
        precos = {(p, h): rnd.uniform(40, 900) for p in procs for h in prests if rnd.random() < 0.4}
        return Periodo(cel, expo, precos, glosa=rnd.uniform(0, 5000), coparticipacao=rnd.uniform(0, 3000))

    return periodo({("P1", "H1")}), periodo({("P2", "H3")})


@pytest.mark.parametrize("seed", range(12))
def test_reconciliacao_exata_em_bases_aleatorias(seed):
    base, atual = _aleatorio(seed)
    res = de.decompor(base, atual)
    _assert_reconcilia(res)
    # decomposição por procedimento soma ao Δ bruto de cada procedimento
    for p in res.por_procedimento:
        assert sum(p["efeitos"].values()) == pytest.approx(p["delta"], abs=0.05)


def test_ordem_das_celulas_nao_altera_o_resultado():
    base, atual = _aleatorio(99)
    r1 = de.decompor(base, atual).as_dict()
    rnd = random.Random(1)
    b2 = Periodo(rnd.sample(base.celulas, len(base.celulas)), base.exposicao_por_faixa,
                 base.precos, base.glosa, base.coparticipacao)
    a2 = Periodo(rnd.sample(atual.celulas, len(atual.celulas)), atual.exposicao_por_faixa,
                 atual.precos, atual.glosa, atual.coparticipacao)
    r2 = de.decompor(b2, a2).as_dict()
    for e1, e2 in zip(r1["efeitos"], r2["efeitos"], strict=True):
        assert e1["valor"] == pytest.approx(e2["valor"], abs=0.02)


def test_periodo_vazio_tudo_nao_comparavel():
    res = de.decompor(Periodo([], EXPO), Periodo([_cel("CONS", "H1", 10, 100)], EXPO))
    _assert_reconcilia(res)
    assert _ef(res, de.NAO_COMPARAVEL) == pytest.approx(1000.0)
