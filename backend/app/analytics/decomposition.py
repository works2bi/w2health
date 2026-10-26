"""Explicação automática da variação da sinistralidade / despesa.

`explicar` decompõe a variação por dimensão (contribuição em R$ e em % da variação) e,
para cada fator, roda o bridge frequência × custo médio. Para fatores coesos
(especialidade, grupo de despesa, procedimento) o bridge é a soma dos efeitos por
procedimento — o que separa corretamente "mais procedimentos" de "procedimento mais
caro". `drill` aprofunda um fator. Nada é estático — tudo deriva dos agregados.

**v1.2**:
  * `explicar` devolve `concentracao_variacao_beneficiarios` — quantos beneficiários
    concentram o aumento da despesa líquida (traz o beneficiário para o TOPO da
    explicação, sem exigir drill);
  * `explicar`/`drill` aceitam `contrato_id` — restringem toda a análise a um contrato
    (calculado sob demanda a partir de `eventos_assistenciais`). Sem receita por
    contrato, `sinistralidade`/`efeito_receita` ficam `None` e um `aviso` é retornado.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.analytics import formulas as f
from app.analytics.periodo import competencia_comparacao
from app.repositories import analytics_repo as repo

EPS = 1e-9

DIMENSOES_VALIDAS = (
    "grupo_despesa", "tipo_atendimento", "especialidade", "procedimento",
    "prestador", "regiao", "faixa_etaria", "sexo", "plano", "contrato",
)
# Dimensões cujo bridge é montado a partir dos procedimentos que as compõem.
DIMENSOES_COESAS = {"especialidade", "grupo_despesa", "procedimento"}

_AVISO_SEM_RECEITA_CONTRATO = (
    "Receita por contrato ainda não disponível — a análise deste contrato fica limitada "
    "à despesa (sinistralidade e efeito-receita não são calculados)."
)


def _f(x) -> float:
    return float(x) if x is not None else 0.0


def _subitens_por_chave(proc_ant: list[dict], proc_atu: list[dict], campo: str) -> dict:
    """{chave -> [(n0, p0, n1, p1), ...]} a partir do detalhe por procedimento."""
    ant: dict[str, dict[str, dict]] = {}
    atu: dict[str, dict[str, dict]] = {}
    for r in proc_ant:
        ant.setdefault(str(r[campo]), {})[r["id"]] = r
    for r in proc_atu:
        atu.setdefault(str(r[campo]), {})[r["id"]] = r
    out: dict[str, list[tuple[float, float, float, float]]] = {}
    for chave in set(ant) | set(atu):
        pares = []
        a_map, b_map = ant.get(chave, {}), atu.get(chave, {})
        for pid in set(a_map) | set(b_map):
            a, b = a_map.get(pid), b_map.get(pid)
            n0 = _f(a["eventos"]) if a else 0.0
            n1 = _f(b["eventos"]) if b else 0.0
            p0 = (_f(a["despesa"]) / n0) if n0 else 0.0
            p1 = (_f(b["despesa"]) / n1) if n1 else 0.0
            pares.append((n0, p0, n1, p1))
        out[chave] = pares
    return out


def _fator(
    chave: str, dim_ant: dict, dim_atu: dict, receita_ant: float, metodo: str,
    subitens: dict | None = None,
) -> dict:
    a = dim_ant.get(chave)
    b = dim_atu.get(chave)
    if a is None and b is None:
        raise KeyError(chave)
    rot = (b or a)["rotulo"]
    d0 = _f(a["despesa"]) if a else 0.0
    d1 = _f(b["despesa"]) if b else 0.0
    n0 = _f(a["eventos"]) if a else 0.0
    n1 = _f(b["eventos"]) if b else 0.0

    if subitens is not None and chave in subitens:
        br = f.bridge_composto(subitens[chave], n0, n1, metodo=metodo)
    else:
        p0 = (d0 / n0) if n0 else 0.0
        p1 = (d1 / n1) if n1 else 0.0
        br = f.bridge(n0, p0, n1, p1, metodo=metodo)

    # `receita_ant is None` -> escopo de contrato (sem receita): impacto em p.p. não se aplica.
    if receita_ant is None:
        impacto_pp = None
    else:
        impacto_pp = (d1 - d0) / receita_ant * 100.0 if receita_ant > 0 else 0.0
    return {
        "chave": chave,
        "categoria": rot,
        "despesa_anterior": round(d0, 2),
        "despesa_atual": round(d1, 2),
        "impacto_financeiro": round(d1 - d0, 2),
        "impacto_pp": round(impacto_pp, 3) if impacto_pp is not None else None,
        "efeito_principal": br.efeito_principal,
        "bridge": br.as_dict(),
    }


def concentracao_variacao_beneficiarios(
    session: Session, competencia: date, comparacao: str = "mes_anterior",
    contrato_id: int | None = None, top_n: int = 12,
) -> dict | None:
    """Quantos beneficiários concentram o AUMENTO da despesa líquida do período (C1).

    Traz o beneficiário para o topo da explicação — sem drill. Opera sobre
    `agg_beneficiario_competencia.despesa_liquida`.
    """
    comp_ant = competencia_comparacao(competencia, comparacao)
    deltas = repo.beneficiarios_delta_mes(session, competencia, comp_ant, contrato_id)
    if not deltas:
        return None

    total_delta = sum(v["delta"] for v in deltas.values())
    positivos = sorted(
        ((bid, v) for bid, v in deltas.items() if v["delta"] > 0),
        key=lambda kv: kv[1]["delta"], reverse=True,
    )
    if not positivos:
        return {
            "competencia": competencia.isoformat(),
            "comparacao": comparacao,
            "delta_total_liquido": round(total_delta, 2),
            "delta_positivo_total": 0.0,
            "n_beneficiarios_com_aumento": 0,
            "n_para_credito_50pct": 0,
            "top5_share_do_aumento": 0.0,
            "gini_do_aumento": 0.0,
            "top": [],
            "deep_link": {"rota": "/beneficiarios",
                          "params": {"competencia": competencia.isoformat()}},
            "metodologia": _METODOLOGIA_CONC,
        }

    soma_pos = sum(v["delta"] for _bid, v in positivos)
    acc, n_50 = 0.0, 0
    for _bid, v in positivos:
        acc += v["delta"]
        n_50 += 1
        if acc >= 0.5 * soma_pos:
            break

    conc = f.concentracao([v["delta"] for _bid, v in positivos], ks=(3, 5, 10))
    denom = soma_pos if soma_pos > EPS else 1.0
    return {
        "competencia": competencia.isoformat(),
        "comparacao": comparacao,
        "delta_total_liquido": round(total_delta, 2),
        "delta_positivo_total": round(soma_pos, 2),
        "n_beneficiarios_com_aumento": len(positivos),
        "n_para_credito_50pct": n_50,
        "top5_share_do_aumento": round(conc.top_k_share.get(5, 0.0), 4),
        "gini_do_aumento": round(conc.gini, 4),
        "top": [
            {
                "id": bid,
                "codigo": v["codigo"],
                "id_contrato": v["id_contrato"],
                "delta": round(v["delta"], 2),
                "participacao_pct": round(v["delta"] / denom * 100.0, 2),
            }
            for bid, v in positivos[:top_n]
        ],
        "deep_link": {"rota": "/beneficiarios",
                      "params": {"competencia": competencia.isoformat()}},
        "metodologia": _METODOLOGIA_CONC,
    }


_METODOLOGIA_CONC = (
    "Δdespesa líquida por beneficiário (mês − comparação). Sobre os beneficiários com "
    "aumento: top-k share e Gini; 'crédito para 50%' = menor nº de beneficiários cujo "
    "aumento somado atinge metade do aumento total."
)


def _reconciliacao_base(contribs: list, s_ant: dict, s_atu: dict) -> dict:
    soma_fatores = sum(c.delta for c in contribs)
    var_liq = _f(s_atu["despesa_liquida"]) - _f(s_ant["despesa_liquida"])
    d_copart = _f(s_atu["coparticipacao"]) - _f(s_ant["coparticipacao"])
    return {
        "base_fatores": "valor_pago (bruta − glosa)",
        "base_variacao_despesa": "despesa líquida (bruta − glosa − coparticipação)",
        "soma_fatores": round(soma_fatores, 2),
        "variacao_despesa_liquida": round(var_liq, 2),
        "delta_coparticipacao": round(d_copart, 2),
        "ok": abs(soma_fatores - d_copart - var_liq) <= 0.05,
        "nota": "Σ fatores − Δ coparticipação = Δ despesa líquida.",
    }


def explicar(
    session: Session,
    competencia: date,
    comparacao: str = "mes_anterior",
    dimensao: str = "especialidade",
    metodo: str = "bennet",
    top: int = 12,
    contrato_id: int | None = None,
) -> dict:
    if dimensao not in DIMENSOES_VALIDAS:
        raise ValueError(f"dimensão inválida: {dimensao}")
    if contrato_id is not None and dimensao == "contrato":
        raise ValueError("dimensão 'contrato' não se aplica quando já há escopo de contrato")

    comp_ant = competencia_comparacao(competencia, comparacao)

    if contrato_id is not None:
        s_atu = repo.sinistralidade_por_contrato_mes(session, competencia, contrato_id)
        s_ant = repo.sinistralidade_por_contrato_mes(session, comp_ant, contrato_id)
        if s_atu is None or s_ant is None:
            raise ValueError("contrato sem dados na competência ou na comparação")
        dim_atu = repo.dimensao_mes_por_contrato(session, competencia, dimensao, contrato_id)
        dim_ant = repo.dimensao_mes_por_contrato(session, comp_ant, dimensao, contrato_id)
        proc_ant = lambda: repo.procedimentos_mes_detalhe_por_contrato(session, comp_ant, contrato_id)  # noqa: E731
        proc_atu = lambda: repo.procedimentos_mes_detalhe_por_contrato(session, competencia, contrato_id)  # noqa: E731
        dec = None
        receita_ant = None
    else:
        s_atu = repo.sinistralidade_mes(session, competencia)
        s_ant = repo.sinistralidade_mes(session, comp_ant)
        if s_atu is None or s_ant is None:
            raise ValueError("competência ou comparação sem dados")
        dec = f.decomposicao_sinistralidade(
            _f(s_ant["despesa"]), _f(s_ant["receita"]),
            _f(s_atu["despesa"]), _f(s_atu["receita"]),
        )
        dim_atu = repo.dimensao_mes(session, competencia, dimensao)
        dim_ant = repo.dimensao_mes(session, comp_ant, dimensao)
        proc_ant = lambda: repo.procedimentos_mes_detalhe(session, comp_ant)  # noqa: E731
        proc_atu = lambda: repo.procedimentos_mes_detalhe(session, competencia)  # noqa: E731
        receita_ant = _f(s_ant["receita"])

    subitens = None
    if dimensao in DIMENSOES_COESAS:
        campo = {"especialidade": "especialidade", "grupo_despesa": "grupo_despesa",
                 "procedimento": "id"}[dimensao]
        subitens = _subitens_por_chave(proc_ant(), proc_atu(), campo)

    contribs = f.contribuicoes(
        {k: (v["rotulo"], _f(v["despesa"])) for k, v in dim_ant.items()},
        {k: (v["rotulo"], _f(v["despesa"])) for k, v in dim_atu.items()},
    )

    fatores_altos = [
        {**_fator(c.chave, dim_ant, dim_atu, receita_ant, metodo, subitens),
         "participacao_variacao": c.participacao_pct}
        for c in contribs if c.delta > 0
    ][:top]
    fatores_baixos = [
        {**_fator(c.chave, dim_ant, dim_atu, receita_ant, metodo, subitens),
         "participacao_variacao": c.participacao_pct}
        for c in contribs if c.delta < 0
    ][:top]

    out = {
        "competencia": competencia.isoformat(),
        "comparacao": comparacao,
        "competencia_comparacao": comp_ant.isoformat(),
        "dimensao": dimensao,
        "metodo_bridge": metodo,
        "sinistralidade_atual": round(_f(s_atu["sinistralidade"]), 2) if s_atu["sinistralidade"] is not None else None,
        "sinistralidade_anterior": round(_f(s_ant["sinistralidade"]), 2) if s_ant["sinistralidade"] is not None else None,
        "variacao_pp": round(dec.variacao_pp, 2) if dec else None,
        "efeito_despesa_pp": round(dec.efeito_despesa_pp, 3) if dec else None,
        "efeito_receita_pp": round(dec.efeito_receita_pp, 3) if dec else None,
        "despesa_atual": round(_f(s_atu["despesa"]), 2),
        "despesa_anterior": round(_f(s_ant["despesa"]), 2),
        "variacao_despesa": round(_f(s_atu["despesa"]) - _f(s_ant["despesa"]), 2),
        "principais_fatores": fatores_altos,
        "fatores_reducao": fatores_baixos,
        "concentracao_variacao_beneficiarios": concentracao_variacao_beneficiarios(
            session, competencia, comparacao, contrato_id
        ),
        # Evolução V1 (aditivo): deixa explícito que os FATORES somam Δ Σvalor_pago (bruta −
        # glosa) e o TOPO usa a despesa líquida — a diferença é a Δ coparticipação.
        "reconciliacao_base": _reconciliacao_base(contribs, s_ant, s_atu),
        "metodologia": (
            "Contribuição_i = D_i,1 − D_i,0; participação_i = contribuição_i / ΔD_total "
            "(ou / Σ|Δ| quando há muito cancelamento). "
            f"Bridge ({metodo}): para especialidade/grupo/procedimento, soma dos efeitos "
            "por procedimento (separa 'mais procedimentos' de 'procedimento mais caro')."
        ),
    }
    if contrato_id is not None:
        out["escopo"] = {
            "tipo": "contrato", "id_contrato": contrato_id, "receita_disponivel": False,
        }
        out["aviso"] = _AVISO_SEM_RECEITA_CONTRATO
    return out


def drill(
    session: Session,
    competencia: date,
    dimensao: str,
    chave: str,
    comparacao: str = "mes_anterior",
    metodo: str = "bennet",
    contrato_id: int | None = None,
) -> dict:
    comp_ant = competencia_comparacao(competencia, comparacao)

    if contrato_id is not None:
        s_ant = repo.sinistralidade_por_contrato_mes(session, comp_ant, contrato_id)
        dim_atu = repo.dimensao_mes_por_contrato(session, competencia, dimensao, contrato_id)
        dim_ant = repo.dimensao_mes_por_contrato(session, comp_ant, dimensao, contrato_id)
        proc_ant = lambda: repo.procedimentos_mes_detalhe_por_contrato(session, comp_ant, contrato_id)  # noqa: E731
        proc_atu = lambda: repo.procedimentos_mes_detalhe_por_contrato(session, competencia, contrato_id)  # noqa: E731
        receita_ant = None
    else:
        s_ant = repo.sinistralidade_mes(session, comp_ant)
        dim_atu = repo.dimensao_mes(session, competencia, dimensao)
        dim_ant = repo.dimensao_mes(session, comp_ant, dimensao)
        proc_ant = lambda: repo.procedimentos_mes_detalhe(session, comp_ant)  # noqa: E731
        proc_atu = lambda: repo.procedimentos_mes_detalhe(session, competencia)  # noqa: E731
        receita_ant = _f(s_ant["receita"]) if s_ant else 0.0

    subitens = None
    if dimensao in DIMENSOES_COESAS:
        campo = {"especialidade": "especialidade", "grupo_despesa": "grupo_despesa",
                 "procedimento": "id"}[dimensao]
        subitens = _subitens_por_chave(proc_ant(), proc_atu(), campo)

    fator = _fator(chave, dim_ant, dim_atu, receita_ant, metodo, subitens)
    serie = repo.dimensao_serie(session, dimensao, chave)

    onde_prestadores = repo.eventos_da_categoria(
        session, competencia, dimensao, chave, agrupar_por="prestador", limit=8
    )
    onde_beneficiarios = repo.eventos_da_categoria(
        session, competencia, dimensao, chave, agrupar_por="beneficiario", limit=8
    )
    prest_ant = {
        r["id"]: _f(r["despesa"])
        for r in repo.eventos_da_categoria(
            session, comp_ant, dimensao, chave, agrupar_por="prestador", limit=200
        )
    }
    prest_contrib = sorted(
        (
            {
                "id": r["id"], "rotulo": r["rotulo"],
                "despesa_atual": round(_f(r["despesa"]), 2),
                "despesa_anterior": round(prest_ant.get(r["id"], 0.0), 2),
                "delta": round(_f(r["despesa"]) - prest_ant.get(r["id"], 0.0), 2),
            }
            for r in repo.eventos_da_categoria(
                session, competencia, dimensao, chave, agrupar_por="prestador", limit=200
            )
        ),
        key=lambda x: abs(x["delta"]), reverse=True,
    )[:8]

    out = {
        "competencia": competencia.isoformat(),
        "comparacao": comparacao,
        "dimensao": dimensao,
        "chave": chave,
        "fator": fator,
        "serie": [
            {
                "competencia": r["competencia"].isoformat(),
                "despesa": round(_f(r["despesa"]), 2),
                "eventos": r["eventos"],
                "custo_medio": round(_f(r["custo_medio"]), 2),
                "freq_por_mil": round(_f(r["freq_por_mil"]), 3),
            }
            for r in serie
        ],
        "onde_investigar": {
            "prestadores_maior_despesa": [
                {"id": r["id"], "rotulo": r["rotulo"],
                 "despesa": round(_f(r["despesa"]), 2), "eventos": r["eventos"],
                 "custo_medio": round(_f(r["custo_medio"]), 2)}
                for r in onde_prestadores
            ],
            "beneficiarios_maior_despesa": [
                {"id": r["id"], "rotulo": r["rotulo"],
                 "despesa": round(_f(r["despesa"]), 2), "eventos": r["eventos"]}
                for r in onde_beneficiarios
            ],
            "prestadores_maior_contribuicao_variacao": prest_contrib,
        },
        "metodologia": fator["bridge"],
    }
    if contrato_id is not None:
        out["escopo"] = {"tipo": "contrato", "id_contrato": contrato_id}
        out["aviso"] = _AVISO_SEM_RECEITA_CONTRATO
    return out
