"""Baseline histórico — o comportamento atual é excepcional em relação ao passado? (Evolução V1)

Função PURA sobre uma série mensal. Compara a janela atual (1+ competências) com:

* o período imediatamente anterior de mesmo tamanho;
* a mesma janela do ano anterior;
* a média histórica das até `janela_hist` competências ANTERIORES à janela atual
  (mínimo `MIN_HISTORICO` — abaixo disso a comparação é declarada insuficiente, nunca
  apresentada como representativa);
* sazonalidade (`seasonality.classificar`, reaproveitado) quando a janela é de 1 mês e há
  ≥ 12 meses de histórico.

O desvio padronizado de uma MÉDIA de k meses usa o erro-padrão σ/√k.
"""

from __future__ import annotations

import math
import statistics
from datetime import date

from app.analytics import seasonality

MIN_HISTORICO = 6
#: a sazonalidade só pode "explicar" um desvio com ≥ 2 ciclos anuais de histórico — com um
#: único ano o fator do mês é uma observação só (ruído, não padrão)
MIN_HISTORICO_SAZONAL = 24
LIMIAR_Z = 2.0


def _menos_meses(d: date, n: int) -> date:
    idx = d.year * 12 + (d.month - 1) - n
    return date(idx // 12, idx % 12 + 1, 1)


def janela(fim: date, meses: int) -> list[date]:
    return [_menos_meses(fim, i) for i in range(meses - 1, -1, -1)]


def _pct(a: float, b: float) -> float | None:
    return round((a - b) / b * 100.0, 2) if abs(b) > 1e-9 else None


def _comparacao(rotulo: str, meses: list[date], valores: dict[date, float], atual: float) -> dict:
    presentes = [m for m in meses if m in valores]
    if len(presentes) < len(meses):
        return {"base": rotulo, "disponivel": False, "inicio": meses[0].isoformat(),
                "fim": meses[-1].isoformat(),
                "motivo": "competências da comparação sem dados"}
    ref = sum(valores[m] for m in meses) / len(meses)
    return {"base": rotulo, "disponivel": True, "inicio": meses[0].isoformat(),
            "fim": meses[-1].isoformat(), "valor_referencia": round(ref, 2),
            "desvio_abs": round(atual - ref, 2), "desvio_pct": _pct(atual, ref)}


def comparar(serie: list[tuple[date, float]], fim: date, meses: int = 1,
             janela_hist: int = 12, indicador: str = "") -> dict:
    """`serie`: [(competência, valor)] mensal. Valores da janela são MÉDIAS mensais."""
    valores = dict(serie)
    atual_meses = janela(fim, meses)
    if any(m not in valores for m in atual_meses):
        return {"indicador": indicador, "disponivel": False,
                "motivo": "janela atual sem dados em todas as competências"}
    atual = sum(valores[m] for m in atual_meses) / meses
    inicio = atual_meses[0]

    anteriores = sorted(c for c in valores if c < inicio)[-janela_hist:]
    limitacoes: list[str] = []
    hist = {"disponivel": False, "n_competencias": len(anteriores)}
    z = None
    if len(anteriores) >= MIN_HISTORICO:
        vals = [valores[c] for c in anteriores]
        mu = statistics.fmean(vals)
        sd = statistics.pstdev(vals)
        ep = sd / math.sqrt(meses) if sd > 0 else 0.0
        z = (atual - mu) / ep if ep > 0 else None
        hist = {
            "disponivel": True, "n_competencias": len(anteriores),
            "inicio": anteriores[0].isoformat(), "fim": anteriores[-1].isoformat(),
            "media": round(mu, 2), "desvio_padrao": round(sd, 2),
            "desvio_abs": round(atual - mu, 2), "desvio_pct": _pct(atual, mu),
            "desvio_padronizado": round(z, 2) if z is not None else None,
        }
        if len(anteriores) < 12:
            limitacoes.append(f"histórico de {len(anteriores)} competências (< 12): a média "
                              "não captura um ciclo sazonal completo")
        if z is None:
            limitacoes.append("série histórica sem variação — desvio padronizado não se aplica")
    else:
        limitacoes.append(f"histórico insuficiente ({len(anteriores)} competências; mínimo "
                          f"{MIN_HISTORICO}) — sem média histórica representativa")

    sazonal = None
    n_hist_total = sum(1 for c in valores if c < inicio)
    if meses == 1:
        sazonal = seasonality.classificar(serie, fim)
        if sazonal.get("classificacao") == "indeterminado":
            limitacoes.append("sazonalidade não avaliada: menos de 12 meses de histórico")
        elif n_hist_total < MIN_HISTORICO_SAZONAL:
            limitacoes.append(f"sazonalidade indicativa: {n_hist_total} meses de histórico (um "
                              f"padrão sazonal exige ≥ {MIN_HISTORICO_SAZONAL})")
    else:
        limitacoes.append("sazonalidade avaliada só para janelas de 1 mês")

    if z is None:
        classe = "indeterminado"
    elif abs(z) >= LIMIAR_Z:
        classe = "excepcional"
        if (sazonal and sazonal.get("classificacao") == "sazonal"
                and n_hist_total >= MIN_HISTORICO_SAZONAL):
            classe = "sazonal"
    else:
        classe = "dentro_do_padrao"

    return {
        "indicador": indicador,
        "disponivel": True,
        "janela_atual": {"inicio": inicio.isoformat(), "fim": fim.isoformat(), "meses": meses},
        "valor_atual": round(atual, 2),
        "periodo_anterior": _comparacao("periodo_anterior", janela(_menos_meses(inicio, 1), meses),
                                        valores, atual),
        "ano_anterior": _comparacao("ano_anterior", janela(_menos_meses(fim, 12), meses),
                                    valores, atual),
        "media_historica": hist,
        "sazonalidade": sazonal,
        "classificacao": classe,
        "limitacoes": limitacoes,
        "metodo": (
            "Valor da janela = média mensal. Média histórica = até "
            f"{janela_hist} competências anteriores à janela (mínimo {MIN_HISTORICO}); desvio "
            f"padronizado = (atual − média) / (σ/√meses); excepcional se |z| ≥ {LIMIAR_Z:g} "
            "(salvo quando a sazonalidade explica o mês)."
        ),
    }
