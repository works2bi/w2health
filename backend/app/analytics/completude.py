"""Completude da competência e estimativa de contas a chegar (IBNR) — Evolução, item 1.

Problema: a competência mais recente parece "boa" porque parte das contas ainda não foi
apresentada. Comparar um mês incompleto com um mês fechado leva a conclusões erradas.

Método (chain-ladder sobre o triângulo de desenvolvimento):

1. Triângulo: despesa líquida (e eventos) por competência ANALÍTICA × defasagem em meses
   até a competência de APRESENTAÇÃO (`competencia_apresentacao`).
2. Data de corte = maior competência de apresentação carregada. A idade de cada
   competência é a distância (em meses) até o corte.
3. Fator de desenvolvimento f_k = Σ C(k+1) ÷ Σ C(k) sobre as competências que já têm
   k+1 meses de desenvolvimento (no máximo as `JANELA_FATORES` mais recentes).
4. Estimativa final = observado × Π f_k da idade atual até `MAX_DEFASAGEM`.
   Completude = observado ÷ estimativa final; IBNR = estimativa final − observado.

Rótulos: o observado é FATO; a estimativa final e a sinistralidade projetada são
ESTIMATIVA (dependem do padrão histórico de apresentação se repetir). Sem data de
apresentação suficiente, nada é estimado — devolve `disponivel: false` e o motivo.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.tenant_scope import tenant_of as _t
from app.repositories import analytics_repo as repo

MAX_DEFASAGEM = 6          # defasagens ≥ 6 meses entram na última coluna
JANELA_FATORES = 12        # competências maduras usadas em cada fator
MIN_COBERTURA = 0.90       # fração mínima de eventos com data de apresentação
LIMIAR_FECHADA = 99.5      # completude (%) a partir da qual a competência é "fechada"


def _idx(d: date) -> int:
    return d.year * 12 + d.month - 1


def _triangulo(session: Session) -> tuple[list[dict], dict]:
    rows = session.execute(
        text(
            f"""
            SELECT competencia,
                   LEAST(GREATEST(
                       (EXTRACT(YEAR FROM competencia_apresentacao)::int * 12
                        + EXTRACT(MONTH FROM competencia_apresentacao)::int)
                     - (EXTRACT(YEAR FROM competencia)::int * 12
                        + EXTRACT(MONTH FROM competencia)::int), 0), {MAX_DEFASAGEM}) AS lag,
                   SUM(valor_apresentado - valor_glosado - valor_coparticipacao) AS liquida,
                   COUNT(*) AS eventos
              FROM eventos_assistenciais
             WHERE tenant_id = :t AND competencia_apresentacao IS NOT NULL
             GROUP BY 1, 2
            """
        ),
        {"t": _t(session)},
    ).mappings().all()
    cob = session.execute(
        text(
            "SELECT COUNT(*) AS total, COUNT(competencia_apresentacao) AS com_data, "
            "MAX(competencia_apresentacao) AS corte "
            "FROM eventos_assistenciais WHERE tenant_id = :t"
        ),
        {"t": _t(session)},
    ).mappings().first()
    return [dict(r) for r in rows], dict(cob)


def _fatores(acum: dict[date, list[float]], idade: dict[date, int]) -> list[dict]:
    """f_k a partir das competências mais recentes com k+1 meses de desenvolvimento."""
    fatores = []
    for k in range(MAX_DEFASAGEM):
        maduras = sorted((m for m in acum if idade[m] >= k + 1), reverse=True)[:JANELA_FATORES]
        num = sum(acum[m][k + 1] for m in maduras)
        den = sum(acum[m][k] for m in maduras)
        ok = len(maduras) >= 3 and den > 0
        fatores.append({
            "de": k, "para": k + 1,
            "fator": round(num / den, 6) if ok else 1.0,
            "competencias_usadas": len(maduras),
            "estimado": ok,
        })
    return fatores


def _desenvolver(celulas: dict[date, dict[int, float]], corte: date) -> tuple[dict, dict, list]:
    idade = {m: min(max(_idx(corte) - _idx(m), 0), MAX_DEFASAGEM) for m in celulas}
    acum: dict[date, list[float]] = {}
    for m, por_lag in celulas.items():
        s, linha = 0.0, []
        for k in range(MAX_DEFASAGEM + 1):
            s += por_lag.get(k, 0.0)
            linha.append(s)
        acum[m] = linha
    return acum, idade, _fatores(acum, idade)


def _projecao(acum: list[float], idade: int, fatores: list[dict]) -> tuple[float, float]:
    observado = acum[idade]
    mult = 1.0
    for f in fatores[idade:]:
        mult *= f["fator"]
    return observado, observado * mult


def calcular(session: Session) -> dict:
    rows, cob = _triangulo(session)
    total, com_data, corte = cob["total"] or 0, cob["com_data"] or 0, cob["corte"]
    cobertura = (com_data / total) if total else 0.0
    base = {
        "metodo": "chain-ladder (triângulo competência de atendimento × defasagem de apresentação)",
        "cobertura_data_apresentacao_pct": round(cobertura * 100, 2),
    }
    if not total or corte is None or cobertura < MIN_COBERTURA:
        return {
            **base, "disponivel": False, "corte": None, "meses": [], "fatores": [],
            "motivo": ("A carga não traz a competência de apresentação das contas em pelo "
                       f"menos {int(MIN_COBERTURA * 100)}% dos eventos — sem ela não dá para "
                       "estimar quanto ainda falta chegar. Nenhuma estimativa é exibida."),
        }

    liq: dict[date, dict[int, float]] = {}
    ev: dict[date, dict[int, float]] = {}
    for r in rows:
        liq.setdefault(r["competencia"], {})[r["lag"]] = float(r["liquida"] or 0)
        ev.setdefault(r["competencia"], {})[r["lag"]] = float(r["eventos"] or 0)
    acum_l, idade, fat_l = _desenvolver(liq, corte)
    acum_e, _, fat_e = _desenvolver(ev, corte)

    receita = {r["competencia"]: float(r["receita"] or 0) for r in repo.serie_sinistralidade(session)}
    meses = []
    for m in sorted(liq):
        obs, fim = _projecao(acum_l[m], idade[m], fat_l)
        ev_obs, ev_fim = _projecao(acum_e[m], idade[m], fat_e)
        compl = (obs / fim * 100) if fim > 0 else 100.0
        rec = receita.get(m, 0.0)
        meses.append({
            "competencia": m.isoformat(),
            "idade_meses": idade[m],
            "status": "fechada" if compl >= LIMIAR_FECHADA else "preliminar",
            "completude_pct": round(min(compl, 100.0), 1),
            "despesa_liquida_observada": round(obs, 2),
            "despesa_liquida_estimada_final": round(max(fim, obs), 2),
            "ibnr_estimado": round(max(fim - obs, 0.0), 2),
            "eventos_observados": int(ev_obs),
            "eventos_estimados_final": int(round(max(ev_fim, ev_obs))),
            "sinistralidade_observada": round(obs / rec * 100, 2) if rec > 0 else None,
            "sinistralidade_projetada": round(max(fim, obs) / rec * 100, 2) if rec > 0 else None,
            "desenvolvimento_pct": [
                round(acum_l[m][k] / acum_l[m][idade[m]] * 100, 1) if acum_l[m][idade[m]] else None
                for k in range(idade[m] + 1)
            ],
        })
    return {
        **base,
        "disponivel": True,
        "corte": corte.isoformat(),
        "max_defasagem_meses": MAX_DEFASAGEM,
        "fatores": fat_l,
        "fatores_eventos": fat_e,
        "meses": meses,
        "rotulos": {"observado": "FATO", "estimativa_final": "ESTIMATIVA",
                    "sinistralidade_projetada": "ESTIMATIVA"},
        "limitacoes": [
            "A estimativa supõe que o padrão de apresentação das últimas competências se repete.",
            "Mudança de faturamento de um prestador grande (atraso, lote represado) distorce "
            "os fatores até as competências afetadas amadurecerem.",
            "Glosas futuras sobre contas já apresentadas não entram na estimativa.",
        ],
    }


def da_competencia(session: Session, competencia: date) -> dict:
    """Recorte de uma competência (badge das telas) + a série completa."""
    res = calcular(session)
    sel = next((m for m in res["meses"] if m["competencia"] == competencia.isoformat()), None)
    return {**res, "competencia": competencia.isoformat(), "selecionada": sel}
