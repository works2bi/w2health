"""Investigação inteligente de custos — POR QUE a despesa assistencial mudou (Evolução V1).

Orquestra, para um escopo (operadora, contrato, fator ou contrato + fator) e duas janelas
de competências:

1. a variação observada (bruta → glosa → coparticipação → líquida);
2. a DECOMPOSIÇÃO ESTRUTURAL (`decomposicao_estrutural.decompor`) — população, perfil
   etário, frequência, mix de procedimentos, mix de prestadores, intensidade, preço
   contratual, custo unitário não explicado, não comparável, glosa, coparticipação —
   que reconcilia exatamente com a variação;
3. o BASELINE histórico (`baseline.comparar`);
4. a REDE: participação, transferências de volume entre prestadores do mesmo
   procedimento e custo vs pares compatíveis (mesmo procedimento e mesmo tipo);
5. COORTES COMPORTAMENTAIS de utilização (recorrente, alto custo histórico, esporádico,
   sem uso prévio) — partição exata, sem nenhuma inferência clínica;
6. a lente de DEFASAGEM (atendimentos de meses anteriores apresentados na competência);
7. a evidência de PREÇO (tabela contratada) — única base para afirmar reajuste;
8. as CONCLUSÕES rotuladas FATO / HIPÓTESE / INVESTIGAR, cada uma com indicador, período,
   valores, contribuição, evidências, limitações e próximo passo.

Regras de redação: "contribuição" (aritmética, FATO) nunca vira "causa"; HIPÓTESE sempre
traz o que falta para confirmá-la; nada é gerado por IA — todo texto é template preenchido
com números calculados aqui.
"""

from __future__ import annotations

import statistics
from datetime import date

from sqlalchemy.orm import Session

from app.analytics import baseline as bl
from app.analytics import decomposicao_estrutural as de
from app.analytics import formulas as f
from app.repositories import analytics_repo as arepo
from app.repositories import investigacao_repo as repo
from app.repositories.investigacao_repo import NIVEIS_REDE, Escopo

FATO, HIPOTESE, INVESTIGAR = "FATO", "HIPOTESE", "INVESTIGAR"

COMPARACOES = ("mes_anterior", "periodo_anterior", "ano_anterior")
JANELAS = (1, 3, 6, 12)
MIN_EVENTOS = 30            # por período, para levantar hipóteses
LIMIAR_RELEVANCIA = 0.10    # fração de Σ|efeitos| para um efeito virar conclusão
MESES_HISTORICO_COORTE = 6
LIMIAR_RECORRENTE = 4       # competências com uso, dos 6 anteriores
TOP_ALTO_CUSTO = 0.05       # 5% maiores despesas do histórico
MIN_BENEF_ALTO_CUSTO = 20
LIMIAR_PARES = 1.25         # custo médio ≥ 25% acima da mediana dos pares
LIMIAR_CONCENTRACAO_COORTE = 0.40  # fração da variação vinda de recorrentes/alto custo
SOBREPESO_COORTE = 1.5      # … e ≥ 1,5× o peso desse grupo na despesa da base
CANCELAMENTO_MAX = 1.5      # … sem coortes se compensando (Σ|Δ coorte| ≤ 1,5 × |Δ|)

ROTULO_TIPO = {
    "consulta": "consultas", "exame": "exames", "terapia": "terapias (sessões em quantidade)",
    "pronto_socorro": "atendimentos de pronto-socorro", "internacao": "internações (diárias em quantidade)",
    "cirurgia": "cirurgias", "opme": "OPME (itens em quantidade)",
}

COORTES = {
    "alto_custo_historico": "Alto custo no histórico",
    "uso_recorrente": "Utilização recorrente",
    "uso_esporadico": "Utilização esporádica",
    "sem_uso_previo": "Sem utilização no histórico",
}


# ----------------------------------------------------------------------------- formatação
def _brl(v: float) -> str:
    s = "−" if v < 0 else ""
    a = abs(v)
    if a >= 1e6:
        txt = f"{a / 1e6:.2f} mi"
    elif a >= 1e3:
        txt = f"{a / 1e3:.1f} mil"
    else:
        txt = f"{a:.0f}"
    return f"{s}R$ {txt.replace('.', ',')}"


def _pct(v: float | None, sinal: bool = True) -> str:
    if v is None:
        return "n/d"
    return (f"{v:+.1f}%" if sinal else f"{v:.1f}%").replace(".", ",").replace("-", "−")


def _var_pct(a: float, b: float) -> float | None:
    return (a - b) / b * 100.0 if abs(b) > 1e-9 else None


def _mes(d: date) -> str:
    return f"{d.month:02d}/{d.year}"


def _periodo_txt(meses: list[date]) -> str:
    return _mes(meses[0]) if len(meses) == 1 else f"{_mes(meses[0])}–{_mes(meses[-1])}"


# ----------------------------------------------------------------------------- períodos
def periodos(competencia: date, comparacao: str, meses: int) -> tuple[list[date], list[date]]:
    if comparacao not in COMPARACOES:
        raise ValueError(f"comparação inválida: {comparacao}")
    if meses not in JANELAS:
        raise ValueError(f"janela inválida: {meses} (use {', '.join(map(str, JANELAS))})")
    atual = bl.janela(competencia, meses)
    if comparacao == "ano_anterior":
        base = bl.janela(bl._menos_meses(competencia, 12), meses)
    else:
        base = bl.janela(bl._menos_meses(atual[0], 1), meses)
    return base, atual


# ----------------------------------------------------------------------------- conclusões
class _Achados:
    def __init__(self, contexto: dict, permitir_hipoteses: bool):
        self.itens: list[dict] = []
        self.ctx = contexto
        self.permitir_hipoteses = permitir_hipoteses

    def add(self, tipo: str, codigo: str, titulo: str, texto: str, *, indicador: str,
            referencia=None, comparacao=None, contribuicao: dict | None = None,
            evidencias: list[str] | None = None, limitacoes: list[str] | None = None,
            proximo_passo: str | None = None, metodologia: str = "",
            deep_link: dict | None = None) -> None:
        if tipo == HIPOTESE and not self.permitir_hipoteses:
            return
        self.itens.append({
            "tipo": tipo, "codigo": codigo, "titulo": titulo, "texto": texto,
            "indicador": indicador, "periodo": self.ctx["periodo"], "escopo": self.ctx["escopo"],
            "valor_referencia": referencia, "valor_comparacao": comparacao,
            "contribuicao": contribuicao, "evidencias": evidencias or [],
            "limitacoes": limitacoes or [], "proximo_passo": proximo_passo,
            "metodologia": metodologia, "deep_link": deep_link,
        })


def _contrib(valor: float, denom: float) -> dict:
    return {"valor": round(valor, 2),
            "participacao_pct": round(valor / denom * 100.0, 1) if abs(denom) > 1e-9 else None}


# ----------------------------------------------------------------------------- blocos
def _coortes(rows: list[dict], meses_hist: list[date], incluir_amostra: bool) -> dict:
    n_hist = len(meses_hist)
    if n_hist < 3:
        return {"disponivel": False,
                "motivo": f"histórico anterior à base com {n_hist} competência(s) (mínimo 3) — "
                          "não há como classificar utilização recorrente"}
    limiar_rec = max(2, round(LIMIAR_RECORRENTE * n_hist / MESES_HISTORICO_COORTE))
    hist_pos = sorted((float(r["liquida_hist"]) for r in rows if float(r["liquida_hist"]) > 0),
                      reverse=True)
    corte_alto = None
    if len(hist_pos) >= MIN_BENEF_ALTO_CUSTO:
        corte_alto = hist_pos[max(int(len(hist_pos) * TOP_ALTO_CUSTO) - 1, 0)]

    grupos: dict[str, dict] = {c: {"codigo": c, "rotulo": COORTES[c], "n_beneficiarios": 0,
                                   "usuarios_base": 0, "usuarios_atual": 0,
                                   "despesa_anterior": 0.0, "despesa_atual": 0.0, "_amostra": []}
                               for c in COORTES}
    for r in rows:
        b0, b1 = float(r["liquida_base"]), float(r["liquida_atual"])
        if abs(b0) < 1e-9 and abs(b1) < 1e-9:
            continue  # só histórico: não participa da variação
        if corte_alto is not None and float(r["liquida_hist"]) >= corte_alto:
            c = "alto_custo_historico"
        elif int(r["meses_uso_hist"]) >= limiar_rec:
            c = "uso_recorrente"
        elif int(r["meses_uso_hist"]) > 0:
            c = "uso_esporadico"
        else:
            c = "sem_uso_previo"
        g = grupos[c]
        g["n_beneficiarios"] += 1
        g["usuarios_base"] += 1 if int(r["eventos_base"]) > 0 else 0
        g["usuarios_atual"] += 1 if int(r["eventos_atual"]) > 0 else 0
        g["despesa_anterior"] += b0
        g["despesa_atual"] += b1
        g["_amostra"].append((b1 - b0, int(r["id"]), r["codigo"]))

    delta_total = sum(g["despesa_atual"] - g["despesa_anterior"] for g in grupos.values())
    soma_abs = sum(abs(g["despesa_atual"] - g["despesa_anterior"]) for g in grupos.values())
    if soma_abs < 1e-9:
        denom = 1.0  # nenhuma coorte mudou
    else:
        denom = delta_total if abs(delta_total) >= 0.15 * soma_abs else soma_abs
    out = []
    for g in grupos.values():
        d = g["despesa_atual"] - g["despesa_anterior"]
        amostra = sorted(g.pop("_amostra"), key=lambda x: abs(x[0]), reverse=True)[:6]
        out.append({
            **g, "despesa_anterior": round(g["despesa_anterior"], 2),
            "despesa_atual": round(g["despesa_atual"], 2), "delta": round(d, 2),
            "participacao_variacao": round(d / denom * 100.0, 1),
            "despesa_por_usuario_anterior": round(g["despesa_anterior"] / g["usuarios_base"], 2)
            if g["usuarios_base"] else None,
            "despesa_por_usuario_atual": round(g["despesa_atual"] / g["usuarios_atual"], 2)
            if g["usuarios_atual"] else None,
            "beneficiarios_amostra": [{"id": i, "codigo": cod, "delta": round(v, 2)}
                                      for v, i, cod in amostra] if incluir_amostra else [],
        })
    return {
        "disponivel": True,
        "meses_historico": n_hist,
        "historico": {"inicio": meses_hist[0].isoformat(), "fim": meses_hist[-1].isoformat()},
        "criterios": {
            "alto_custo_historico": (f"5% maiores despesas líquidas no histórico (corte "
                                     f"{_brl(corte_alto)})") if corte_alto is not None else
            f"não aplicado — menos de {MIN_BENEF_ALTO_CUSTO} beneficiários com despesa no histórico",
            "uso_recorrente": f"uso em ≥ {limiar_rec} de {n_hist} competências do histórico",
            "uso_esporadico": f"uso em 1 a {limiar_rec - 1} competências do histórico",
            "sem_uso_previo": "nenhum uso no histórico (inclui novos beneficiários)",
        },
        "coortes": out,
        "reconciliacao": {"soma_coortes": round(delta_total, 2)},
        "nota": ("Classificação COMPORTAMENTAL de utilização, definida pelo histórico ANTERIOR "
                 "à base (não pelo resultado). Não é diagnóstico nem condição crônica."),
    }


def _rede(res: de.ResultadoDecomposicao, cel0: dict, cel1: dict, rot_prest: dict,
          rot_proc: dict) -> dict:
    """Transferências de volume e comparação com pares compatíveis."""
    transf = []
    procs = sorted((p for p in res.por_procedimento if p["comparavel"]),
                   key=lambda p: abs(p["efeitos"].get(de.MIX_PRESTADORES, 0.0)), reverse=True)[:5]
    for p in procs:
        if abs(p["efeitos"].get(de.MIX_PRESTADORES, 0.0)) < 1.0:
            continue
        k = p["chave"]
        n0 = sum(v["eventos"] for (pp, _h), v in cel0.items() if pp == k) or 1.0
        n1 = sum(v["eventos"] for (pp, _h), v in cel1.items() if pp == k) or 1.0
        hs = {h for (pp, h) in cel0 if pp == k} | {h for (pp, h) in cel1 if pp == k}
        movs = []
        for h in hs:
            a, b = cel0.get((k, h)), cel1.get((k, h))
            w0 = (a["eventos"] / n0) if a else 0.0
            w1 = (b["eventos"] / n1) if b else 0.0
            if abs(w1 - w0) < 0.05:
                continue
            movs.append({
                "id_prestador": int(h), "prestador": rot_prest.get(int(h), {}).get("nome", h),
                "tipo": rot_prest.get(int(h), {}).get("tipo"),
                "participacao_anterior": round(w0, 4), "participacao_atual": round(w1, 4),
                "custo_medio_anterior": round(a["valor"] / a["eventos"], 2) if a else None,
                "custo_medio_atual": round(b["valor"] / b["eventos"], 2) if b else None,
                "situacao": "novo" if not a else ("saiu" if not b else "permanece"),
            })
        movs.sort(key=lambda m: m["participacao_atual"] - m["participacao_anterior"], reverse=True)
        transf.append({
            "id_procedimento": int(k), "procedimento": rot_proc.get(int(k), {}).get("descricao", k),
            "efeito_mix_prestadores": p["efeitos"][de.MIX_PRESTADORES],
            "custo_medio_procedimento_anterior": p["custo_medio_anterior"],
            "custo_medio_procedimento_atual": p["custo_medio_atual"],
            "movimentos": movs,
        })

    pares = []
    top = sorted((p for p in res.por_procedimento if p["comparavel"]),
                 key=lambda p: abs(p["delta"]), reverse=True)[:5]
    for p in top:
        k = p["chave"]
        linhas = []
        for (pp, h), v in cel1.items():
            if pp == k and v["eventos"] > 0:
                linhas.append((int(h), v["valor"] / v["eventos"], v["eventos"]))
        por_tipo: dict[str | None, list] = {}
        for h, cm, n in linhas:
            por_tipo.setdefault(rot_prest.get(h, {}).get("tipo"), []).append((h, cm, n))
        for tipo, grupo in por_tipo.items():
            if len(grupo) < 3:
                continue
            for h, cm, n in grupo:
                outros = [c for hh, c, _n in grupo if hh != h]
                med = statistics.median(outros)
                razao = cm / med if med > 0 else None
                if razao is not None and razao >= LIMIAR_PARES:
                    pares.append({
                        "id_procedimento": int(k),
                        "procedimento": rot_proc.get(int(k), {}).get("descricao", k),
                        "id_prestador": h, "prestador": rot_prest.get(h, {}).get("nome", h),
                        "tipo_prestador": tipo, "eventos": n, "custo_medio": round(cm, 2),
                        "mediana_pares": round(med, 2), "n_pares": len(outros),
                        "razao": round(razao, 2),
                    })
    pares.sort(key=lambda x: x["razao"], reverse=True)

    prestadores = []
    for x in res.por_prestador[:12]:
        info = rot_prest.get(int(x["chave"]), {})
        prestadores.append({**x, "id_prestador": int(x["chave"]), "prestador": info.get("nome"),
                            "tipo": info.get("tipo"),
                            "deep_link": {"rota": f"/prestadores/{int(x['chave'])}", "params": {}}})
    return {
        "prestadores": prestadores,
        "transferencias": transf,
        "acima_dos_pares": pares[:10],
        "nota": ("Pares = prestadores do MESMO procedimento e do MESMO tipo (hospital, clínica, "
                 "laboratório...), com ao menos 3 no grupo. Transferência de volume é fato "
                 "observado nos registros; o motivo da troca não é presumido."),
    }


def _cobertura_dados(res: de.ResultadoDecomposicao, fin0: dict, fin1: dict, nivel: str) -> list[dict]:
    cob = res.cobertura
    ev = (fin0["eventos"] + fin1["eventos"]) or 1.0

    def frac(chave: str) -> float:
        return round((fin0[chave] + fin1[chave]) / ev, 4)

    return [
        {"fator": "Frequência", "dado": "eventos assistenciais", "disponivel": True,
         "cobertura": 1.0, "observacao": "unidade: evento (1 linha = 1 item de conta)"},
        {"fator": "População / perfil etário", "dado": "adesão, saída e faixa etária",
         "disponivel": cob["exposicao_disponivel"],
         "cobertura": 1.0 if cob["perfil_etario_disponivel"] else 0.0,
         "observacao": "exposição = beneficiários ativos no 1º dia de cada competência"},
        {"fator": "Mix de procedimentos", "dado": "procedimento por evento", "disponivel": True,
         "cobertura": cob["despesa_em_procedimentos_comparaveis"],
         "observacao": "fração da despesa em procedimentos presentes nos dois períodos"},
        {"fator": "Mix de prestadores", "dado": f"prestador ({nivel})", "disponivel": True,
         "cobertura": cob["despesa_em_pares_comparaveis"],
         "observacao": "fração da despesa em pares procedimento × prestador comparáveis"},
        {"fator": "Estabelecimento de atendimento", "dado": "estabelecimento por evento",
         "disponivel": frac("com_estabelecimento") > 0, "cobertura": frac("com_estabelecimento"),
         "observacao": "opcional na origem"},
        {"fator": "Prestador solicitante", "dado": "solicitante por evento",
         "disponivel": frac("com_solicitante") > 0, "cobertura": frac("com_solicitante"),
         "observacao": "opcional na origem"},
        {"fator": "Intensidade", "dado": "quantidade por evento",
         "disponivel": cob["quantidade_informada"],
         "cobertura": 1.0 if cob["quantidade_informada"] else 0.0,
         "observacao": "sem quantidade informada a intensidade é 1 por evento"},
        {"fator": "Preço contratual", "dado": "tabela de preço com vigência",
         "disponivel": cob["pares_com_tabela_de_preco"] > 0,
         "cobertura": cob["despesa_com_tabela_de_preco"],
         "observacao": "sem tabela: 'custo unitário observado', nunca 'reajuste'"},
        {"fator": "Vínculo contratual da competência", "dado": "contrato/plano no evento",
         "disponivel": frac("com_vinculo") > 0, "cobertura": frac("com_vinculo"),
         "observacao": "sem vínculo gravado vale o vínculo atual do beneficiário"},
        {"fator": "Glosa / coparticipação", "dado": "valor glosado e coparticipação por evento",
         "disponivel": (fin0["glosa"] + fin1["glosa"] + fin0["coparticipacao"]
                        + fin1["coparticipacao"]) > 0, "cobertura": None, "observacao": ""},
        {"fator": "Condição crônica", "dado": "CID + regra clínica validada",
         "disponivel": False, "cobertura": 0.0,
         "observacao": "NÃO classificada: exige lista de CIDs validada e versionada "
                       "(pendência de negócio). Só há classificação comportamental."},
    ]


# ----------------------------------------------------------------------------- motor
def investigar(
    session: Session,
    competencia: date,
    comparacao: str = "mes_anterior",
    meses: int = 1,
    dimensao: str | None = None,
    chave: str | None = None,
    contrato_id: int | None = None,
    nivel_rede: str = "executante",
    incluir_prestadores: bool = True,
    incluir_beneficiarios: bool = True,
    empresa_id: int | None = None,
) -> dict:
    if nivel_rede not in NIVEIS_REDE:
        raise ValueError(f"nivel_rede inválido: {nivel_rede}")
    if (dimensao is None) != (chave is None):
        raise ValueError("dimensao e chave devem ser informadas juntas")
    if contrato_id is not None and dimensao == "contrato":
        raise ValueError("dimensão 'contrato' não se aplica quando já há escopo de contrato")
    if contrato_id is not None and empresa_id is not None:
        raise ValueError("use contrato_id OU empresa_id")
    base_m, atual_m = periodos(competencia, comparacao, meses)
    disponiveis = set(arepo.competencias(session))
    faltam = [m for m in base_m + atual_m if m not in disponiveis]
    if faltam:
        raise ValueError("competências sem dados para a comparação: "
                         + ", ".join(m.strftime("%Y-%m") for m in faltam))

    esc = Escopo(dimensao, chave, contrato_id, nivel_rede, empresa_id)
    rows0, rows1 = repo.celulas(session, base_m, esc), repo.celulas(session, atual_m, esc)
    fin0, fin1 = repo.financeiro(session, base_m, esc), repo.financeiro(session, atual_m, esc)
    exp0, exp1 = repo.exposicao_por_faixa(session, base_m, esc), repo.exposicao_por_faixa(session, atual_m, esc)
    pr0, pr1 = repo.precos_vigentes(session, base_m, esc), repo.precos_vigentes(session, atual_m, esc)

    def periodo(rows, expo, precos, fin) -> de.Periodo:
        return de.Periodo(
            celulas=[de.Celula(str(r["procedimento"]), str(r["prestador"]), float(r["eventos"]),
                               float(r["quantidade"] or 0), float(r["valor"] or 0), r["faixa"])
                     for r in rows],
            exposicao_por_faixa=expo or None,
            precos={(str(p), str(h)): v for (p, h), v in precos.items()},
            glosa=fin["glosa"], coparticipacao=fin["coparticipacao"],
        )

    res = de.decompor(periodo(rows0, exp0, pr0, fin0), periodo(rows1, exp1, pr1, fin1))

    def por_par(rows) -> dict:
        out: dict[tuple[str, str], dict] = {}
        for r in rows:
            k = (str(r["procedimento"]), str(r["prestador"]))
            o = out.setdefault(k, {"eventos": 0.0, "quantidade": 0.0, "valor": 0.0})
            o["eventos"] += float(r["eventos"])
            o["quantidade"] += float(r["quantidade"] or 0)
            o["valor"] += float(r["valor"] or 0)
        return out

    cel0, cel1 = por_par(rows0), por_par(rows1)
    ids_proc = sorted({int(k[0]) for k in cel0} | {int(k[0]) for k in cel1})
    ids_prest = sorted({int(k[1]) for k in cel0} | {int(k[1]) for k in cel1})
    rot_proc = repo.rotulos_procedimentos(session, ids_proc)
    rot_prest = repo.rotulos_prestadores(session, ids_prest)

    # ---- rótulos na decomposição
    dec = res.as_dict()
    for p in dec["por_procedimento"]:
        info = rot_proc.get(int(p["chave"]), {})
        p["procedimento"] = info.get("descricao", p["chave"])
        p["grupo"] = info.get("grupo_procedimento")
        p["deep_link"] = {"rota": "/sinistralidade",
                          "params": {"dimensao": "procedimento", "chave": p["chave"]}}
    dec["por_procedimento"] = dec["por_procedimento"][:15]
    for x in dec["por_prestador"]:
        info = rot_prest.get(int(x["chave"]), {})
        x["prestador"], x["tipo"] = info.get("nome"), info.get("tipo")
    dec["por_prestador"] = dec["por_prestador"][:15]

    # ---- escopo legível
    rotulo_escopo = "Operadora (todos os contratos)"
    if contrato_id is not None:
        rotulo_escopo = f"Contrato {repo.rotulo_contrato(session, contrato_id) or contrato_id}"
    if empresa_id is not None:
        rotulo_escopo = f"Empresa {repo.rotulo_empresa(session, empresa_id) or empresa_id}"
    rotulo_fator = None
    if dimensao is not None:
        dm = arepo.dimensao_mes(session, competencia, dimensao).get(chave) or \
            arepo.dimensao_mes(session, base_m[-1], dimensao).get(chave)
        rotulo_fator = (dm or {}).get("rotulo") or chave
        rotulo_escopo += f" · {dimensao.replace('_', ' ')}: {rotulo_fator}"

    # ---- histórico
    serie = repo.serie_mensal(session, esc)
    s_liq = [(r["competencia"], float(r["liquida"])) for r in serie]
    s_ev = [(r["competencia"], float(r["eventos"])) for r in serie]
    s_cm = [(r["competencia"], float(r["bruta"]) / r["eventos"]) for r in serie if r["eventos"]]
    historico = {
        "despesa_liquida": bl.comparar(s_liq, competencia, meses, indicador="despesa líquida mensal"),
        "eventos": bl.comparar(s_ev, competencia, meses, indicador="eventos por mês"),
        "custo_medio": bl.comparar(s_cm, competencia, meses, indicador="custo médio por evento (bruto)"),
    }

    # ---- coortes, defasagem, unidades, preços
    hist_m = [m for m in bl.janela(bl._menos_meses(base_m[0], 1), MESES_HISTORICO_COORTE)
              if m in disponiveis]
    coortes = _coortes(repo.beneficiarios_periodos(session, base_m, atual_m, hist_m, esc),
                       hist_m, incluir_beneficiarios)

    d_def = fin1["liquida_defasada"] - fin0["liquida_defasada"]
    delta = res.reconciliacao["delta_observado"]
    defasagem = {
        "despesa_defasada_anterior": round(fin0["liquida_defasada"], 2),
        "despesa_defasada_atual": round(fin1["liquida_defasada"], 2),
        "eventos_defasados_anterior": fin0["eventos_defasados"],
        "eventos_defasados_atual": fin1["eventos_defasados"],
        "delta_defasada": round(d_def, 2),
        "delta_mesmo_mes": round(delta - d_def, 2),
        "metodo": ("Partição exata da despesa líquida: atendimentos do próprio mês da "
                   "competência × atendimentos de meses anteriores (data do evento < competência)."),
    }

    un0, un1 = repo.unidades_por_tipo(session, base_m, esc), repo.unidades_por_tipo(session, atual_m, esc)
    unidades = []
    for tipo in sorted(set(un0) | set(un1)):
        a, b = un0.get(tipo, {}), un1.get(tipo, {})
        unidades.append({
            "tipo_atendimento": tipo, "unidade": ROTULO_TIPO.get(tipo, tipo),
            "eventos_anterior": a.get("eventos", 0.0), "eventos_atual": b.get("eventos", 0.0),
            "variacao_eventos_pct": _round(_var_pct(b.get("eventos", 0.0), a.get("eventos", 0.0))),
            "quantidade_anterior": a.get("quantidade", 0.0), "quantidade_atual": b.get("quantidade", 0.0),
            "beneficiarios_anterior": a.get("beneficiarios", 0.0),
            "beneficiarios_atual": b.get("beneficiarios", 0.0),
            "despesa_bruta_anterior": round(a.get("bruta", 0.0), 2),
            "despesa_bruta_atual": round(b.get("bruta", 0.0), 2),
        })

    # só pares DO ESCOPO, com movimento nos dois períodos (a tabela é do tenant inteiro)
    no_escopo = {(int(p), int(h)) for p, h in cel0} & {(int(p), int(h)) for p, h in cel1}
    pares_tabela = sorted(set(pr0) & set(pr1) & no_escopo)
    mudancas = []
    for m in repo.mudancas_de_preco(session, pares_tabela, base_m[-1], atual_m[-1]):
        p_, h_ = int(m["p"]), int(m["h"])
        ant, novo = m["valor_anterior"], float(m["valor_novo"])
        mudancas.append({
            "id_procedimento": p_, "procedimento": rot_proc.get(p_, {}).get("descricao", p_),
            "id_prestador": h_ if incluir_prestadores else None,
            "prestador": rot_prest.get(h_, {}).get("nome", h_) if incluir_prestadores else None,
            "vigencia_inicio": m["vigencia_inicio"].isoformat(),
            "valor_anterior": float(ant) if ant is not None else None, "valor_novo": novo,
            "variacao_pct": _round(_var_pct(novo, float(ant))) if ant is not None else None,
            "tipo_remuneracao": m["tipo_remuneracao"],
        })
    precos = {"pares_com_tabela": len(pares_tabela), "mudancas_de_tabela": mudancas,
              "cobertura_despesa": res.cobertura["despesa_com_tabela_de_preco"]}

    rede = _rede(res, cel0, cel1, rot_prest, rot_proc) if incluir_prestadores else None

    # ---- conclusões
    t = res.totais
    suficiente = t["eventos_anterior"] >= MIN_EVENTOS and t["eventos_atual"] >= MIN_EVENTOS \
        and t["despesa_liquida_anterior"] > 0
    ctx = {"periodo": {"base": _periodo_txt(base_m), "atual": _periodo_txt(atual_m)},
           "escopo": rotulo_escopo}
    ach = _Achados(ctx, permitir_hipoteses=suficiente)
    denom = res.denominador()
    soma_abs = sum(abs(e.valor) for e in res.efeitos.values() if e.valor is not None) or 1.0
    liq0, liq1 = t["despesa_liquida_anterior"], t["despesa_liquida_atual"]
    var_pct = _var_pct(liq1, liq0)

    sin = sinistralidade_escopo(session, base_m, atual_m, esc, res)
    lim_head = [] if res.reconciliacao["ok"] else ["reconciliação fora da tolerância"]
    if not sin["disponivel"] and (contrato_id is not None or empresa_id is not None):
        lim_head.append(f"sinistralidade do escopo indisponível: {sin['motivo']}")
    hl = historico["despesa_liquida"]
    lim_head += hl.get("limitacoes", []) if hl.get("disponivel") else []
    ach.add(
        FATO, "variacao_total",
        f"Despesa líquida {'aumentou' if delta > 0 else 'diminuiu' if delta < 0 else 'estável'}"
        f" {_pct(var_pct)}",
        f"A despesa assistencial líquida passou de {_brl(liq0)} ({ctx['periodo']['base']}) para "
        f"{_brl(liq1)} ({ctx['periodo']['atual']}): variação de {_brl(delta)} ({_pct(var_pct)}).",
        indicador="despesa assistencial líquida", referencia=liq0, comparacao=liq1,
        contribuicao={"valor": round(delta, 2), "participacao_pct": 100.0},
        evidencias=[
            f"Bruta {_brl(t['despesa_bruta_anterior'])} → {_brl(t['despesa_bruta_atual'])}; "
            f"glosas {_brl(t['glosa_anterior'])} → {_brl(t['glosa_atual'])}; coparticipação "
            f"{_brl(t['coparticipacao_anterior'])} → {_brl(t['coparticipacao_atual'])}.",
            f"Eventos {t['eventos_anterior']:.0f} → {t['eventos_atual']:.0f}; custo médio bruto "
            f"{_brl(t['custo_medio_anterior'])} → {_brl(t['custo_medio_atual'])}.",
            f"Soma dos efeitos da decomposição = {_brl(res.reconciliacao['soma_efeitos'])} "
            f"(diferença {res.reconciliacao['diferenca']:.4f}).",
        ],
        limitacoes=lim_head, metodologia="Δ = despesa líquida(atual) − despesa líquida(base).",
    )

    if not suficiente:
        ach.add(
            INVESTIGAR, "dados_insuficientes", "Dados insuficientes para determinar a causa",
            f"O escopo tem {t['eventos_anterior']:.0f} evento(s) na base e "
            f"{t['eventos_atual']:.0f} no período atual (mínimo {MIN_EVENTOS} em cada). A "
            "decomposição continua exata, mas nenhuma hipótese é levantada com este volume.",
            indicador="volume de eventos", referencia=t["eventos_anterior"],
            comparacao=t["eventos_atual"],
            limitacoes=["volume pequeno: variações de poucos casos dominam o resultado"],
            proximo_passo="Ampliar a janela (3, 6 ou 12 meses) ou o escopo antes de concluir.",
        )

    if sin["disponivel"] and (contrato_id is not None or empresa_id is not None):
        ach.add(
            FATO, "variacao_sinistralidade_escopo",
            f"Sinistralidade {sin['s0']:.1f}% → {sin['s1']:.1f}%".replace(".", ","),
            f"A sinistralidade do escopo variou {sin['variacao_pp']:+.2f} p.p.: efeito despesa "
            f"{sin['efeito_despesa_pp']:+.2f} p.p. e efeito receita {sin['efeito_receita_pp']:+.2f} p.p. "
            "(identidade exata).",
            indicador="sinistralidade líquida do escopo", referencia=sin["s0"], comparacao=sin["s1"],
            evidencias=[f"Receita reconhecida atribuída: {_brl(sin['receita_anterior'])} → "
                        f"{_brl(sin['receita_atual'])}."],
            metodologia="ΔS = ΔD/R₀ + (D₁/R₁ − D₁/R₀); receita = reconhecida atribuída ao escopo.",
        )
    _achados_efeitos(ach, res, denom, soma_abs, t, exp0, exp1, unidades, coortes, mudancas,
                     rede, rot_proc, rot_prest, incluir_prestadores)
    _achados_contexto(ach, res, historico, defasagem, coortes, delta, soma_abs, dec)

    ordem = {FATO: 0, HIPOTESE: 1, INVESTIGAR: 2}
    head, resto = ach.itens[:1], ach.itens[1:]
    resto.sort(key=lambda a: (ordem[a["tipo"]],
                              -abs((a["contribuicao"] or {}).get("valor") or 0.0)))
    achados = head + resto

    efeitos_rel = sorted((e for e in dec["efeitos"] if e["valor"] is not None and abs(e["valor"]) > 0.005),
                         key=lambda e: abs(e["valor"]), reverse=True)
    onde_proc = [p["procedimento"] for p in dec["por_procedimento"][:2]]
    onde_prest = [x["prestador"] for x in dec["por_prestador"][:2]] if incluir_prestadores else []
    resumo = {
        "o_que_aconteceu": achados[0]["texto"],
        "quanto_mudou": {"valor": round(delta, 2), "percentual": _round(var_pct)},
        "fatores": [f"{e['rotulo']}: {_brl(e['valor'])}" for e in efeitos_rel[:3]],
        "onde": onde_proc + [p for p in onde_prest if p],
        "evidencias": {k: sum(1 for a in achados if a["tipo"] == k) for k in (FATO, HIPOTESE, INVESTIGAR)},
        "investigar": [a["proximo_passo"] for a in achados if a["proximo_passo"]][:5],
    }

    b0n, b1n = t["eventos_anterior"], t["eventos_atual"]
    bennet = f.bennet_bridge(b0n, t["custo_medio_anterior"], b1n, t["custo_medio_atual"]).as_dict()

    out = {
        "escopo": {"rotulo": rotulo_escopo, "dimensao": dimensao, "chave": chave,
                   "rotulo_fator": rotulo_fator, "contrato_id": contrato_id,
                   "nivel_rede": nivel_rede},
        "periodos": {
            "comparacao": comparacao, "meses": meses,
            "atual": {"inicio": atual_m[0].isoformat(), "fim": atual_m[-1].isoformat()},
            "base": {"inicio": base_m[0].isoformat(), "fim": base_m[-1].isoformat()},
        },
        "dados_suficientes": suficiente,
        "sinistralidade": sin,
        "resumo": resumo,
        "variacao": {**t, "delta_liquida": round(delta, 2), "delta_liquida_pct": _round(var_pct)},
        "decomposicao": dec,
        "bridge_frequencia_custo_medio": {**bennet, "nota": (
            "Visão de 2 fatores (eventos × custo médio bruto) do escopo inteiro — mistura mix e "
            "preço no 'custo médio'. A decomposição estrutural acima separa esses efeitos.")},
        "historico": historico,
        "rede": rede,
        "coortes": coortes,
        "defasagem": defasagem,
        "precos": precos,
        "unidades": unidades,
        "cobertura_dados": _cobertura_dados(res, fin0, fin1, nivel_rede),
        "achados": achados,
        "metodologia": {
            "decomposicao": de.__doc__.strip().split("\n\n")[0],
            "identidade": ("Δ líquida = população + perfil etário + frequência + mix de "
                           "procedimentos + mix de prestadores + intensidade + preço contratual "
                           "+ custo unitário não explicado + não comparável − Δglosa − "
                           "Δcoparticipação (exato)."),
            "atribuicao": ("Valores de Shapley entre população, frequência, mix de procedimentos "
                           "e custo médio (com 2 fatores é o Bennet já usado no produto); divisão "
                           "linear exata do custo médio entre rede, intensidade e preço."),
            "base_financeira": ("Fatores assistenciais sobre a despesa BRUTA (apresentada); glosa "
                                "e coparticipação somadas como camadas até a LÍQUIDA."),
        },
    }
    return out


def sinistralidade_escopo(session: Session, base_m: list[date], atual_m: list[date], esc: Escopo,
                          res: de.ResultadoDecomposicao) -> dict:
    """Sinistralidade do escopo nas duas janelas — SÓ com receita identificada para ele.

    * operadora: receita da Gold (fonte única) — indisponível se alguma competência estiver
      divergente ou sem receita;
    * contrato: receita reconhecida ATRIBUÍDA, e só se a Gold publicou a sinistralidade do
      contrato em todas as competências das janelas (cobertura, sem divergência);
    * empresa: receita dos contratos (atribuída) + receita consolidada da empresa;
    * fator (dimensão): não há receita por fator — não aplicável.
    Despesa = líquida do escopo (`res.totais`). Decomposição num/den exata."""
    t = res.totais
    d0, d1 = t["despesa_liquida_anterior"], t["despesa_liquida_atual"]
    if esc.dimensao is not None:
        return {"disponivel": False, "motivo": "não há receita por fator (dimensão)"}
    if esc.contrato_id is not None:
        r0, m0 = repo.receita_contrato_janela(session, base_m, esc.contrato_id)
        r1, m1 = repo.receita_contrato_janela(session, atual_m, esc.contrato_id)
    elif esc.empresa_id is not None:
        r0, m0 = repo.receita_empresa_janela(session, base_m, esc.empresa_id)
        r1, m1 = repo.receita_empresa_janela(session, atual_m, esc.empresa_id)
    else:
        r0, m0 = repo.receita_operadora_janela(session, base_m)
        r1, m1 = repo.receita_operadora_janela(session, atual_m)
    motivo = m0 or m1
    if motivo or not r0 or not r1:
        return {"disponivel": False, "motivo": motivo or "sem receita no período"}
    dec = f.decomposicao_sinistralidade(d0, r0, d1, r1)
    return {
        "disponivel": True, "motivo": None,
        "receita_anterior": round(r0, 2), "receita_atual": round(r1, 2),
        "s0": round(d0 / r0 * 100, 2), "s1": round(d1 / r1 * 100, 2),
        "variacao_pp": round(dec.variacao_pp, 4),
        "efeito_despesa_pp": round(dec.efeito_despesa_pp, 4),
        "efeito_receita_pp": round(dec.efeito_receita_pp, 4),
        # cada efeito da decomposição estrutural em p.p. — soma = efeito despesa (exato)
        "efeitos_despesa_pp": {e.codigo: round(e.valor / r0 * 100, 4)
                               for e in res.efeitos.values() if e.valor is not None},
    }


def _round(v: float | None, n: int = 2) -> float | None:
    return round(v, n) if v is not None else None


def _relevante(valor: float | None, soma_abs: float) -> bool:
    return valor is not None and abs(valor) >= LIMIAR_RELEVANCIA * soma_abs and abs(valor) >= 1.0


def _achados_efeitos(ach: _Achados, res, denom, soma_abs, t, exp0, exp1, unidades, coortes,
                     mudancas, rede, rot_proc, rot_prest, incluir_prestadores) -> None:
    ef = res.efeitos
    metod = "Decomposição estrutural (Shapley + divisão linear exata) — ver metodologia."

    def nome_proc(k: str) -> str:
        return rot_proc.get(int(k), {}).get("descricao", k)

    def nome_prest(k: str) -> str:
        return rot_prest.get(int(k), {}).get("nome", k)

    v = ef[de.POPULACAO].valor
    if _relevante(v, soma_abs):
        e0, e1 = t["exposicao_anterior"], t["exposicao_atual"]
        ach.add(FATO, "efeito_populacao", "Mudança no tamanho da carteira",
                f"A exposição passou de {e0:.0f} para {e1:.0f} beneficiários-mês "
                f"({_pct(_var_pct(e1, e0))}), contribuindo {_brl(v)} para a variação.",
                indicador="beneficiários-mês expostos", referencia=e0, comparacao=e1,
                contribuicao=_contrib(v, denom), metodologia=metod,
                proximo_passo="Verificar entrada/saída de vidas (novos contratos, cancelamentos).")

    v = ef[de.PERFIL_ETARIO].valor
    if _relevante(v, soma_abs):
        tot0, tot1 = sum(exp0.values()) or 1.0, sum(exp1.values()) or 1.0
        mov = sorted(((a, exp1.get(a, 0) / tot1 - exp0.get(a, 0) / tot0) for a in set(exp0) | set(exp1)),
                     key=lambda x: abs(x[1]), reverse=True)[:3]
        ach.add(FATO, "efeito_perfil_etario", "Mudança no perfil etário da carteira",
                f"A composição etária da população exposta mudou e, mantidas as taxas de "
                f"utilização de cada faixa, isso contribui {_brl(v)}.",
                indicador="composição da exposição por faixa etária",
                contribuicao=_contrib(v, denom), metodologia=metod,
                evidencias=[f"Faixa {a}: participação na exposição {_pct(d * 100)} p.p."
                            .replace("%", "") for a, d in mov],
                proximo_passo="Identificar os contratos/grupos que trouxeram a mudança de perfil.")

    v = ef[de.FREQUENCIA].valor
    if _relevante(v, soma_abs):
        com_expo = ef[de.POPULACAO].valor is not None
        evid = []
        if com_expo:
            evid.append(f"Eventos por mil beneficiários-mês: {t['eventos_por_mil_anterior']:.1f} → "
                        f"{t['eventos_por_mil_atual']:.1f}.")
        for u in sorted(unidades, key=lambda u: abs(u["eventos_atual"] - u["eventos_anterior"]),
                        reverse=True)[:3]:
            evid.append(f"{u['unidade'].capitalize()}: {u['eventos_anterior']:.0f} → "
                        f"{u['eventos_atual']:.0f} ({_pct(u['variacao_eventos_pct'])}).")
        ach.add(FATO, "efeito_frequencia",
                "Mais utilização por beneficiário" if v > 0 else "Menos utilização por beneficiário",
                (f"A taxa de utilização (eventos por beneficiário exposto, por faixa etária) "
                 f"{'subiu' if v > 0 else 'caiu'} e contribui {_brl(v)}."
                 if com_expo else
                 f"O número de eventos {'aumentou' if v > 0 else 'diminuiu'} (sem exposição para "
                 f"calcular taxa) e contribui {_brl(v)}."),
                indicador="frequência de utilização", contribuicao=_contrib(v, denom),
                evidencias=evid, metodologia=metod,
                limitacoes=[] if com_expo else [ef[de.FREQUENCIA].motivo])

    v = ef[de.MIX_PROCEDIMENTOS].valor
    if _relevante(v, soma_abs):
        top = sorted((p for p in res.por_procedimento if p["comparavel"]),
                     key=lambda p: p["efeitos"].get(de.MIX_PROCEDIMENTOS, 0.0) * (1 if v > 0 else -1),
                     reverse=True)[:3]
        ach.add(FATO, "efeito_mix_procedimentos", "Mudança no mix de procedimentos",
                f"A participação de procedimentos {'mais' if v > 0 else 'menos'} caros no total "
                f"de eventos mudou, contribuindo {_brl(v)} — sem ser contada como frequência.",
                indicador="participação dos procedimentos no nº de eventos",
                contribuicao=_contrib(v, denom), metodologia=metod,
                evidencias=[f"{nome_proc(p['chave'])}: {_pct(p['participacao_eventos_anterior'] * 100, False)}"
                            f" → {_pct(p['participacao_eventos_atual'] * 100, False)} dos eventos "
                            f"(custo médio {_brl(p['custo_medio_atual'])})" for p in top])
        ach.add(HIPOTESE, "hipotese_mix_procedimentos", "Perfil de utilização mais complexo",
                "A mudança de mix pode refletir casos mais (ou menos) complexos, mudança de "
                "conduta ou de codificação. Não comprovado pelos dados de faturamento.",
                indicador="mix de procedimentos", contribuicao=_contrib(v, denom),
                proximo_passo="Auditar a indicação dos procedimentos que ganharam participação.")

    v = ef[de.MIX_PRESTADORES].valor
    if _relevante(v, soma_abs):
        evid = []
        if incluir_prestadores and rede:
            for tr in rede["transferencias"][:3]:
                for m in tr["movimentos"][:2]:
                    evid.append(
                        f"{tr['procedimento']}: {m['prestador']} "
                        f"{_pct(m['participacao_anterior'] * 100, False)} → "
                        f"{_pct(m['participacao_atual'] * 100, False)} do volume "
                        f"(custo médio {_brl(m['custo_medio_atual'] or m['custo_medio_anterior'] or 0)}; "
                        f"média do procedimento {_brl(tr['custo_medio_procedimento_atual'])}).")
        ach.add(FATO, "efeito_mix_prestadores",
                "Volume migrou para prestadores mais caros" if v > 0 else
                "Volume migrou para prestadores mais baratos",
                f"No mesmo procedimento, a distribuição do volume entre prestadores mudou e "
                f"contribui {_brl(v)} (registros de atendimento — fato observado).",
                indicador="participação dos prestadores no volume de cada procedimento",
                contribuicao=_contrib(v, denom), evidencias=evid, metodologia=metod,
                limitacoes=[] if incluir_prestadores else
                ["detalhe por prestador indisponível no plano contratado"])
        ach.add(HIPOTESE, "hipotese_rede", "Mudança de rede ou de direcionamento",
                "A transferência de volume pode decorrer de credenciamento/descredenciamento, "
                "direcionamento, disponibilidade ou escolha do beneficiário. O motivo não é "
                "presumido.", indicador="mix de prestadores", contribuicao=_contrib(v, denom),
                proximo_passo="Verificar o motivo da transferência de volume com a gestão de rede.")

    v = ef[de.INTENSIDADE].valor
    if _relevante(v, soma_abs):
        ach.add(FATO, "efeito_intensidade", "Mais quantidade por evento" if v > 0 else
                "Menos quantidade por evento",
                f"No mesmo procedimento e prestador, a quantidade por evento (sessões, diárias, "
                f"itens) mudou e contribui {_brl(v)}.", indicador="quantidade por evento",
                contribuicao=_contrib(v, denom), metodologia=metod,
                evidencias=[f"Quantidade total {t['quantidade_anterior']:.0f} → "
                            f"{t['quantidade_atual']:.0f} para {t['eventos_anterior']:.0f} → "
                            f"{t['eventos_atual']:.0f} eventos."],
                proximo_passo="Revisar permanência/diárias e nº de sessões autorizadas por evento.")

    v = ef[de.PRECO_CONTRATUAL].valor
    if _relevante(v, soma_abs):
        ach.add(FATO, "efeito_preco_contratual", "Reajuste de tabela contratada",
                f"A tabela de preço contratada mudou para pares prestador × procedimento "
                f"analisados e explica {_brl(v)} da variação.",
                indicador="preço unitário contratado", contribuicao=_contrib(v, denom),
                metodologia=metod,
                evidencias=[f"{(m['prestador'] + ' · ') if m['prestador'] else ''}{m['procedimento']}: "
                            f"{_brl(m['valor_anterior'] or 0)} → "
                            f"{_brl(m['valor_novo'])} ({_pct(m['variacao_pct'])}), vigente desde "
                            f"{m['vigencia_inicio']}." for m in mudancas[:4]],
                limitacoes=[f"tabela cobre {res.cobertura['despesa_com_tabela_de_preco'] * 100:.0f}% "
                            "da despesa comparável"])

    v = ef[de.CUSTO_UNITARIO].valor
    if _relevante(v, soma_abs):
        sinal = 1 if v > 0 else -1
        tops_p = sorted((p for p in res.por_procedimento if p["comparavel"]),
                        key=lambda p: p["efeitos"].get(de.CUSTO_UNITARIO, 0.0) * sinal, reverse=True)[:3]
        tops_h = sorted(res.por_prestador, key=lambda h: h["efeitos"][de.CUSTO_UNITARIO] * sinal,
                        reverse=True)[:3]
        evid = [f"{nome_proc(p['chave'])}: custo médio {_brl(p['custo_medio_anterior'])} → "
                f"{_brl(p['custo_medio_atual'])}" for p in tops_p]
        if incluir_prestadores:
            evid += [f"Prestador {nome_prest(h['chave'])}: contribuição "
                     f"{_brl(h['efeitos'][de.CUSTO_UNITARIO])}" for h in tops_h]
        cob = res.cobertura["despesa_com_tabela_de_preco"]
        lim = ["sem tabela de preço para comparar: o aumento NÃO é afirmado como reajuste"]
        if cob > 0:
            lim = [f"tabela contratada cobre {cob * 100:.0f}% da despesa comparável; onde há "
                   "tabela, a parcela acima dela também está aqui"]
        ach.add(FATO, "efeito_custo_unitario",
                "Aumento de custo unitário observado" if v > 0 else "Redução de custo unitário observada",
                f"No mesmo procedimento e no mesmo prestador, o valor por unidade "
                f"{'subiu' if v > 0 else 'caiu'} e contribui {_brl(v)}. Não há evidência de "
                "tabela que explique essa parcela.",
                indicador="valor apresentado por unidade", contribuicao=_contrib(v, denom),
                evidencias=evid, limitacoes=lim, metodologia=metod)
        if v > 0:
            ach.add(HIPOTESE, "hipotese_custo_unitario", "Reajuste não registrado ou mudança de cobrança",
                    "O aumento pode refletir reajuste não carregado na tabela, mudança de pacote, "
                    "materiais/medicamentos ou de codificação. Não comprovado.",
                    indicador="custo unitário", contribuicao=_contrib(v, denom),
                    proximo_passo="Verificar a tabela de remuneração vigente dos prestadores e "
                                  "procedimentos listados (contrato, aditivos, pacotes).")

    v = ef[de.NAO_COMPARAVEL].valor
    if _relevante(v, soma_abs):
        nc = [p for p in res.por_procedimento if not p["comparavel"]][:4]
        ach.add(INVESTIGAR, "nao_comparavel", "Parte da variação não é comparável",
                f"{_brl(v)} vêm de procedimentos que só ocorreram em um dos períodos — não é "
                "possível separar volume de preço para eles.",
                indicador="despesa sem base comparável", contribuicao=_contrib(v, denom),
                evidencias=[f"{nome_proc(p['chave'])}: {_brl(p['despesa_anterior'])} → "
                            f"{_brl(p['despesa_atual'])}" for p in nc],
                limitacoes=["efeito não isolado — não redistribuído entre os demais"],
                proximo_passo="Verificar mudança de codificação/tabela TUSS ou procedimentos novos.")

    for cod, rot in ((de.GLOSA, "glosas"), (de.COPARTICIPACAO, "coparticipação")):
        v = ef[cod].valor
        if _relevante(v, soma_abs):
            ach.add(FATO, f"efeito_{cod}", f"Variação de {rot}",
                    f"A variação de {rot} {'reduziu' if v < 0 else 'aumentou'} a despesa líquida "
                    f"em {_brl(abs(v))}.", indicador=rot, contribuicao=_contrib(v, denom),
                    metodologia="Camada financeira linear: −Δ" + rot + ".")


def _achados_contexto(ach: _Achados, res, historico, defasagem, coortes, delta, soma_abs, dec) -> None:
    hl = historico["despesa_liquida"]
    if hl.get("disponivel") and hl["media_historica"].get("disponivel"):
        mh = hl["media_historica"]
        z = mh.get("desvio_padronizado")
        classe = hl["classificacao"]
        if classe == "excepcional":
            titulo = "Comportamento excepcional frente ao histórico"
        elif classe == "sazonal":
            titulo = "Desvio explicado pela sazonalidade do mês"
        else:
            titulo = "Dentro do padrão histórico"
        ach.add(FATO, "baseline", titulo,
                f"A despesa líquida média mensal ({_brl(hl['valor_atual'])}) está "
                f"{_pct(mh['desvio_pct'])} da média das {mh['n_competencias']} competências "
                f"anteriores ({_brl(mh['media'])})"
                + (f", desvio padronizado {z:.1f}." if z is not None else "."),
                indicador="despesa líquida mensal", referencia=mh["media"],
                comparacao=hl["valor_atual"], limitacoes=hl["limitacoes"], metodologia=hl["metodo"])
    else:
        ach.add(INVESTIGAR, "baseline_insuficiente", "Histórico insuficiente para comparação",
                "Não há competências anteriores suficientes para dizer se a variação é "
                "excepcional.", indicador="despesa líquida mensal",
                limitacoes=hl.get("limitacoes", []) or [hl.get("motivo", "")],
                proximo_passo="Carregar ao menos 6 (idealmente 24) competências de histórico.")

    # concentração da variação entre procedimentos
    sinal = 1 if delta >= 0 else -1
    contrib = sorted((p["delta"] * sinal for p in res.por_procedimento if p["delta"] * sinal > 0),
                     reverse=True)
    if contrib:
        tot = sum(contrib)
        top3 = sum(contrib[:3]) / tot if tot > 0 else 0.0
        nomes = [p["procedimento"] for p in dec["por_procedimento"][:3]]
        concentrada = top3 >= 0.6
        ach.add(FATO, "concentracao",
                "Variação concentrada" if concentrada else "Variação distribuída",
                f"Os 3 procedimentos com maior {'aumento' if sinal > 0 else 'redução'} respondem por "
                f"{top3 * 100:.0f}% do movimento nessa direção "
                f"({len(contrib)} procedimentos se moveram).",
                indicador="concentração da variação por procedimento",
                evidencias=[", ".join(str(n) for n in nomes)],
                deep_link={"rota": "/sinistralidade", "params": {"dimensao": "procedimento"}})

    if coortes.get("disponivel"):
        cs = {c["codigo"]: c for c in coortes["coortes"]}
        rec = cs["uso_recorrente"]["delta"] + cs["alto_custo_historico"]["delta"]
        base_rec = cs["uso_recorrente"]["despesa_anterior"] + cs["alto_custo_historico"]["despesa_anterior"]
        base_tot = sum(c["despesa_anterior"] for c in coortes["coortes"])
        peso_base = base_rec / base_tot if base_tot > 0 else 0.0
        # concentração = responde por ≥ 40% da variação E por muito mais do que o seu peso
        # na despesa da base (crescimento proporcional de todos NÃO é concentração)
        share_frac = rec / delta if abs(delta) > 1e-6 else 0.0
        mov = sum(abs(c["delta"]) for c in coortes["coortes"])
        pouco_cancelamento = mov <= CANCELAMENTO_MAX * abs(delta)
        if (share_frac >= LIMIAR_CONCENTRACAO_COORTE and share_frac >= SOBREPESO_COORTE * peso_base
                and pouco_cancelamento):
            share = share_frac * 100
            ach.add(FATO, "coorte_recorrente",
                    "Beneficiários com utilização recorrente concentram a variação",
                    f"Beneficiários com uso recorrente ou alto custo no histórico anterior à base "
                    f"respondem por {share:.0f}% da variação ({_brl(rec)}).",
                    indicador="despesa líquida por coorte de utilização",
                    contribuicao={"valor": round(rec, 2), "participacao_pct": round(share, 1)},
                    evidencias=[f"{c['rotulo']}: {_brl(c['delta'])} ({c['n_beneficiarios']} benef.)"
                                for c in coortes["coortes"]]
                    + [f"Peso desse grupo na despesa da base: {peso_base * 100:.0f}% — na variação: "
                       f"{share:.0f}%."],
                    limitacoes=[coortes["nota"]])
            ach.add(HIPOTESE, "hipotese_utilizacao_continuada",
                    "Utilização continuada de um grupo identificável",
                    "A concentração em quem já utilizava pode indicar tratamentos em curso ou "
                    "acompanhamento contínuo. Classificação comportamental — sem diagnóstico.",
                    indicador="coortes de utilização", contribuicao={"valor": round(rec, 2),
                                                                     "participacao_pct": round(share, 1)},
                    proximo_passo="Avaliar o grupo com acesso auditado e programas de gestão de saúde.")

    d_def = defasagem["delta_defasada"]
    if abs(delta) > 1e-6 and d_def * delta > 0 and abs(d_def) >= 0.2 * abs(delta):
        share = d_def / delta * 100
        ach.add(FATO, "defasagem", "Parte da variação é de atendimentos de meses anteriores",
                f"{_brl(d_def)} ({share:.0f}% da variação) vêm de atendimentos realizados antes "
                "da competência e apresentados nela.", indicador="despesa por mês de atendimento",
                referencia=defasagem["despesa_defasada_anterior"],
                comparacao=defasagem["despesa_defasada_atual"],
                contribuicao={"valor": round(d_def, 2), "participacao_pct": round(share, 1)},
                metodologia=defasagem["metodo"])
        ach.add(HIPOTESE, "hipotese_defasagem", "Aumento aparente por apresentação tardia",
                "A variação pode ser efeito do calendário de faturamento, e não de mais "
                "utilização no período.", indicador="defasagem de apresentação",
                proximo_passo="Verificar o calendário de apresentação de contas dos prestadores "
                              "e comparar pela competência de atendimento.")
