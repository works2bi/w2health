"""Inteligência sobre prestadores: ranking de contribuição, detalhe, comparação com
pares (z-score) e detecção de comportamento fora do padrão."""

from __future__ import annotations

import statistics
from datetime import date

from sqlalchemy.orm import Session

from app.analytics import baseline
from app.analytics import formulas as f
from app.analytics.periodo import competencia_comparacao
from app.repositories import analytics_repo as repo


def _f(x) -> float:
    return float(x) if x is not None else 0.0


def _bridge_prestador(a: dict | None, b: dict | None, metodo: str) -> dict:
    d0 = _f(a["despesa"]) if a else 0.0
    d1 = _f(b["despesa"]) if b else 0.0
    n0 = _f(a["eventos"]) if a else 0.0
    n1 = _f(b["eventos"]) if b else 0.0
    p0 = d0 / n0 if n0 else 0.0
    p1 = d1 / n1 if n1 else 0.0
    return f.bridge(n0, p0, n1, p1, metodo=metodo).as_dict()


def ranking_variacao(
    session: Session, competencia: date, comparacao: str = "mes_anterior",
    direcao: str = "alta", limit: int = 10, metodo: str = "bennet",
) -> dict:
    comp_ant = competencia_comparacao(competencia, comparacao)
    atu = {r["id_prestador"]: r for r in repo.prestadores_mes(session, competencia)}
    ant = {r["id_prestador"]: r for r in repo.prestadores_mes(session, comp_ant)}

    itens = []
    for pid in set(atu) | set(ant):
        a, b = ant.get(pid), atu.get(pid)
        d0 = _f(a["despesa"]) if a else 0.0
        d1 = _f(b["despesa"]) if b else 0.0
        base = b or a
        itens.append(
            {
                "id_prestador": pid,
                "nome": base["nome"],
                "tipo": base["tipo"],
                "regiao": base.get("regiao"),
                "especialidade_principal": base.get("especialidade_principal"),
                "despesa_anterior": round(d0, 2),
                "despesa_atual": round(d1, 2),
                "impacto": round(d1 - d0, 2),
                "eventos_atual": b["eventos"] if b else 0,
                "custo_medio_atual": round(_f(b["custo_medio"]) if b else 0.0, 2),
                "participacao": round(_f(b["participacao"]) if b else 0.0, 4),
                "bridge": _bridge_prestador(a, b, metodo),
            }
        )

    reverse = direcao == "alta"
    itens.sort(key=lambda x: x["impacto"], reverse=reverse)
    if direcao == "alta":
        itens = [x for x in itens if x["impacto"] > 0][:limit]
    else:
        itens = [x for x in itens if x["impacto"] < 0][:limit]

    total_delta = sum(
        _f(atu.get(p, {}).get("despesa", 0)) - _f(ant.get(p, {}).get("despesa", 0))
        for p in set(atu) | set(ant)
    )
    denom = total_delta if abs(total_delta) > 1e-9 else 1.0
    for x in itens:
        x["participacao_variacao"] = round(x["impacto"] / denom * 100.0, 2)

    return {
        "competencia": competencia.isoformat(),
        "comparacao": comparacao,
        "competencia_comparacao": comp_ant.isoformat(),
        "direcao": direcao,
        "itens": itens,
        "metodologia": "impacto = despesa_prestador(mês) − despesa_prestador(comparação).",
    }


def lista(
    session: Session, competencia: date, sort: str = "despesa",
    page: int = 1, page_size: int = 25,
) -> dict:
    rows = repo.prestadores_mes(session, competencia)
    keymap = {
        "despesa": lambda r: _f(r["despesa"]),
        "eventos": lambda r: r["eventos"],
        "custo_medio": lambda r: _f(r["custo_medio"]),
        "beneficiarios": lambda r: r["beneficiarios"],
        "participacao": lambda r: _f(r["participacao"]),
    }
    rows.sort(key=keymap.get(sort, keymap["despesa"]), reverse=True)
    total = len(rows)
    ini = (page - 1) * page_size
    page_rows = rows[ini: ini + page_size]
    return {
        "competencia": competencia.isoformat(),
        "total": total,
        "page": page,
        "page_size": page_size,
        "itens": [
            {
                "id_prestador": r["id_prestador"],
                "nome": r["nome"],
                "tipo": r["tipo"],
                "regiao": r["regiao"],
                "especialidade_principal": r["especialidade_principal"],
                "despesa": round(_f(r["despesa"]), 2),
                "eventos": r["eventos"],
                "beneficiarios": r["beneficiarios"],
                "custo_medio": round(_f(r["custo_medio"]), 2),
                "participacao": round(_f(r["participacao"]), 4),
            }
            for r in page_rows
        ],
    }


def _zscores(alvo: dict, pares: list[dict]) -> dict:
    def z(campo: str, valor: float) -> float:
        vals = [_f(p[campo]) for p in pares if p["id_prestador"] != alvo["id_prestador"]]
        if len(vals) < 3:
            return 0.0
        mu = statistics.fmean(vals)
        sd = statistics.pstdev(vals) or 1.0
        return (valor - mu) / sd

    ev_por_benef = _f(alvo["eventos"]) / _f(alvo["beneficiarios"]) if alvo["beneficiarios"] else 0.0
    pares_ratio = [
        {**p, "ev_por_benef": _f(p["eventos"]) / _f(p["beneficiarios"]) if p["beneficiarios"] else 0.0}
        for p in pares
    ]
    return {
        "custo_medio": round(z("custo_medio", _f(alvo["custo_medio"])), 2),
        "eventos_por_beneficiario": round(
            _z_ratio(alvo, pares_ratio, ev_por_benef), 2
        ),
        "concentracao_procedimento": round(
            z("procedimento_top_share", _f(alvo["procedimento_top_share"])), 2
        ),
    }


def _z_ratio(alvo: dict, pares_ratio: list[dict], valor: float) -> float:
    vals = [p["ev_por_benef"] for p in pares_ratio if p["id_prestador"] != alvo["id_prestador"]]
    if len(vals) < 3:
        return 0.0
    mu = statistics.fmean(vals)
    sd = statistics.pstdev(vals) or 1.0
    return (valor - mu) / sd


def anomalia_prestadores(session: Session, competencia: date) -> list[dict]:
    """Varre todos os prestadores do mês e sinaliza os fora do padrão vs seus pares."""
    infos = {}
    achados = []
    prest_mes = repo.prestadores_mes(session, competencia)
    by_espec: dict[str, list[dict]] = {}
    for r in prest_mes:
        info = infos.get(r["id_prestador"]) or repo.prestador_info(session, r["id_prestador"])
        infos[r["id_prestador"]] = info
        by_espec.setdefault(info["id_especialidade_principal"], []).append(
            {**r, "_info": info}
        )

    for _espec, grupo in by_espec.items():
        if len(grupo) < 4:
            continue
        pares = grupo
        for alvo in grupo:
            z = _zscores(alvo, pares)
            flags = [k for k, v in z.items() if abs(v) >= 2.0]
            forte = any(abs(v) >= 3.0 for v in z.values())
            if forte or len(flags) >= 2:
                achados.append(
                    {
                        "id_prestador": alvo["id_prestador"],
                        "nome": alvo["nome"],
                        "tipo": alvo["tipo"],
                        "especialidade_principal": alvo["_info"]["especialidade_principal"],
                        "despesa": round(_f(alvo["despesa"]), 2),
                        "custo_medio": round(_f(alvo["custo_medio"]), 2),
                        "eventos": alvo["eventos"],
                        "zscores": z,
                        "metricas_fora_padrao": flags,
                        "severidade": "alta" if forte else "media",
                    }
                )
    achados.sort(key=lambda a: max(abs(v) for v in a["zscores"].values()), reverse=True)
    return achados


def _custo_vs_pares(session: Session, id_prestador: int, tipo: str | None, competencia: date,
                    top_proc: list[dict]) -> dict:
    """Para os principais procedimentos do prestador: custo médio dele × mediana dos demais
    prestadores do MESMO tipo que fizeram o MESMO procedimento no mês (≥ 3 pares).
    Comparar dentro do procedimento evita confrontar perfis assistenciais incompatíveis."""
    linhas = repo.custo_procedimentos_por_prestador(session, competencia, [t["id"] for t in top_proc])
    itens = []
    for t in top_proc:
        meu = next((r for r in linhas if r["id_procedimento"] == t["id"]
                    and r["id_prestador"] == id_prestador), None)
        pares = [_f(r["despesa"]) / r["eventos"] for r in linhas
                 if r["id_procedimento"] == t["id"] and r["id_prestador"] != id_prestador
                 and r["tipo"] == tipo and r["eventos"]]
        if meu is None or not meu["eventos"]:
            continue
        cm = _f(meu["despesa"]) / meu["eventos"]
        if len(pares) < 3:
            itens.append({"id_procedimento": t["id"], "procedimento": t["descricao"],
                          "custo_medio": round(cm, 2), "n_pares": len(pares), "mediana_pares": None,
                          "razao": None, "comparavel": False})
            continue
        med = statistics.median(pares)
        itens.append({"id_procedimento": t["id"], "procedimento": t["descricao"],
                      "custo_medio": round(cm, 2), "n_pares": len(pares),
                      "mediana_pares": round(med, 2),
                      "razao": round(cm / med, 3) if med > 0 else None, "comparavel": True})
    return {
        "itens": itens,
        "metodologia": ("Custo médio do prestador no procedimento ÷ mediana dos prestadores do "
                        "mesmo tipo que realizaram o mesmo procedimento no mês (mínimo 3 pares; "
                        "abaixo disso a comparação não é feita)."),
    }


def detalhe(
    session: Session, id_prestador: int, competencia: date,
    comparacao: str = "mes_anterior", metodo: str = "bennet",
) -> dict:
    info = repo.prestador_info(session, id_prestador)
    if info is None:
        raise ValueError("prestador não encontrado")
    comp_ant = competencia_comparacao(competencia, comparacao)
    serie = repo.prestador_serie(session, id_prestador)
    atu = next((r for r in serie if r["competencia"] == competencia), None)
    ant = next((r for r in serie if r["competencia"] == comp_ant), None)

    top_proc = repo.prestador_top_procedimentos(session, id_prestador, competencia)
    concentr = f.concentracao([_f(t["despesa"]) for t in top_proc], ks=(1, 3, 5))

    pares = repo.prestador_peers_mes(session, competencia, info["id_especialidade_principal"])
    alvo_row = next((p for p in pares if p["id_prestador"] == id_prestador), None)
    z = _zscores(alvo_row, pares) if alvo_row else {}

    serie_desp = [(r["competencia"], _f(r["despesa"])) for r in serie]
    serie_cm = [(r["competencia"], _f(r["custo_medio"])) for r in serie if r["eventos"]]
    historico = {
        "despesa": baseline.comparar(serie_desp, competencia, indicador="despesa mensal do prestador"),
        "custo_medio": baseline.comparar(serie_cm, competencia, indicador="custo médio por evento"),
    }

    return {
        # Evolução V1 (aditivo): histórico comparável e custo vs pares do MESMO procedimento
        "historico": historico,
        "custo_vs_pares_por_procedimento": _custo_vs_pares(session, id_prestador, info["tipo"],
                                                           competencia, top_proc),
        "prestador": {
            "id": info["id"], "nome": info["nome"], "tipo": info["tipo"],
            "regiao": info["regiao"], "especialidade_principal": info["especialidade_principal"],
        },
        "competencia": competencia.isoformat(),
        "comparacao": comparacao,
        "kpis": {
            "despesa": round(_f(atu["despesa"]) if atu else 0.0, 2),
            "eventos": atu["eventos"] if atu else 0,
            "beneficiarios": atu["beneficiarios"] if atu else 0,
            "custo_medio": round(_f(atu["custo_medio"]) if atu else 0.0, 2),
            "participacao": round(_f(atu["participacao"]) if atu else 0.0, 4),
        },
        "bridge": _bridge_prestador(ant, atu, metodo),
        "serie": [
            {
                "competencia": r["competencia"].isoformat(),
                "despesa": round(_f(r["despesa"]), 2),
                "eventos": r["eventos"],
                "custo_medio": round(_f(r["custo_medio"]), 2),
                "participacao": round(_f(r["participacao"]), 4),
            }
            for r in serie
        ],
        "principais_procedimentos": [
            {
                "id": t["id"], "descricao": t["descricao"], "grupo": t["grupo_procedimento"],
                "eventos": t["eventos"], "despesa": round(_f(t["despesa"]), 2),
                "custo_medio": round(_f(t["custo_medio"]), 2),
            }
            for t in top_proc
        ],
        "concentracao": concentr.as_dict(),
        "comparacao_pares": {
            "n_pares": len(pares),
            "zscores": z,
            "fora_padrao": [k for k, v in z.items() if abs(v) >= 2.0],
        },
        "metodologia": (
            "Ranking por Δdespesa; bridge frequência × custo médio; z-score vs pares da "
            "mesma especialidade principal (fora do padrão: |z|≥3 em 1 métrica ou |z|≥2 em ≥2)."
        ),
    }
