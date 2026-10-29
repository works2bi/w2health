"""Medição de efeito de um plano de ação — Evolução, item 3.

Compara o ALVO da ação (dimensão × chave da camada analítica, ou a carteira) antes e depois
da competência de início, e contrasta com o resto da carteira no mesmo período:

    variação do alvo     = média(depois) ÷ média(antes) − 1             → FATO
    variação do restante = mesma conta para (carteira − alvo)            → FATO
    efeito relativo      = variação do alvo − variação do restante (p.p.) → HIPÓTESE

O efeito relativo é uma diferença-em-diferenças simples: mostra se o alvo se descolou da
carteira, NÃO prova que a ação causou isso (sazonalidade, mix e outras ações também
mexem). Competências com menos de `COMPLETUDE_MINIMA` % das contas recebidas (item 1) ficam
fora do "depois" — comparar mês incompleto com mês fechado inventaria uma queda.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.analytics import completude
from app.models.gestao import PlanoAcao
from app.repositories import analytics_repo as repo

MESES_ANTES = 3
MESES_DEPOIS = 3
COMPLETUDE_MINIMA = 95.0  # % das contas esperadas já recebidas


def _media(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def _var(depois: float | None, antes: float | None) -> float | None:
    if depois is None or not antes:
        return None
    return round((depois / antes - 1) * 100, 2)


def _serie_alvo(session: Session, plano: PlanoAcao) -> dict[date, float]:
    if plano.dimensao and plano.chave:
        return {r["competencia"]: float(r["despesa_liquida"] or 0)
                for r in repo.dimensao_serie(session, plano.dimensao, plano.chave)}
    return {r["competencia"]: float(r["despesa_liquida"] or 0) for r in repo.serie_sinistralidade(session)}


def medir(session: Session, plano: PlanoAcao) -> dict:
    alvo = _serie_alvo(session, plano)
    total = {r["competencia"]: float(r["despesa_liquida"] or 0) for r in repo.serie_sinistralidade(session)}
    c = completude.calcular(session)
    preliminares = {date.fromisoformat(m["competencia"]) for m in c["meses"]
                    if m["completude_pct"] < COMPLETUDE_MINIMA}

    inicio = plano.competencia_inicio
    antes = sorted(m for m in total if m < inicio)[-MESES_ANTES:]
    depois_todos = sorted(m for m in total if m >= inicio)
    depois = [m for m in depois_todos if m not in preliminares][:MESES_DEPOIS]

    base = {
        "plano_id": plano.id,
        "alvo": plano.alvo_rotulo,
        "metrica": "despesa líquida mensal",
        "competencia_inicio": inicio.isoformat(),
        "meses_antes": [m.isoformat() for m in antes],
        "meses_depois": [m.isoformat() for m in depois],
        "meses_preliminares_ignorados": [m.isoformat() for m in depois_todos if m in preliminares],
        "serie": [{"competencia": m.isoformat(), "alvo": round(alvo.get(m, 0.0), 2),
                   "preliminar": m in preliminares}
                  for m in sorted(total) if antes and m >= antes[0]],
    }
    if not antes:
        return {**base, "situacao": "sem_linha_de_base",
                "mensagem": "Não há competências anteriores ao início da ação para comparar."}
    if not depois:
        return {**base, "situacao": "aguardando_dados",
                "mensagem": ("Ainda não há competência fechada depois do início da ação — "
                             "o efeito aparece quando o primeiro mês após o início fechar.")}

    a0, a1 = _media([alvo.get(m, 0.0) for m in antes]), _media([alvo.get(m, 0.0) for m in depois])
    r0 = _media([total[m] - alvo.get(m, 0.0) for m in antes])
    r1 = _media([total[m] - alvo.get(m, 0.0) for m in depois])
    v_alvo, v_resto = _var(a1, a0), _var(r1, r0)
    carteira = not (plano.dimensao and plano.chave)
    efeito = None if carteira or v_alvo is None or v_resto is None else round(v_alvo - v_resto, 2)
    meta = plano.meta_reducao_pct
    return {
        **base,
        "situacao": "medido",
        "media_antes": round(a0 or 0, 2),
        "media_depois": round(a1 or 0, 2),
        "variacao_alvo_pct": v_alvo,
        "variacao_restante_pct": None if carteira else v_resto,
        "efeito_relativo_pp": efeito,
        "atingiu_meta": None if meta is None or v_alvo is None else v_alvo <= -abs(meta),
        "parcial": len(depois) < MESES_DEPOIS,
        "rotulos": {"variacao_alvo_pct": "FATO", "variacao_restante_pct": "FATO",
                    "efeito_relativo_pp": "HIPÓTESE"},
        "leitura": _leitura(v_alvo, v_resto, efeito, carteira),
    }


def _leitura(v_alvo, v_resto, efeito, carteira: bool) -> str:
    if carteira:
        return (f"A despesa líquida média da carteira variou {v_alvo:+.1f}% depois do início "
                "da ação. Sem grupo de comparação, não dá para separar o efeito da ação.")
    if efeito is None:
        return "Dados insuficientes para comparar com o restante da carteira."
    sentido = "abaixo" if efeito < 0 else "acima"
    return (f"O alvo variou {v_alvo:+.1f}% e o restante da carteira {v_resto:+.1f}%: o alvo "
            f"ficou {abs(efeito):.1f} p.p. {sentido} da carteira. É um indício (HIPÓTESE) de "
            "efeito da ação, não uma prova — confira sazonalidade e outras mudanças no período.")
