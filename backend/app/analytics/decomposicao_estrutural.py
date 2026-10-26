"""Decomposição estrutural da variação da despesa assistencial (Evolução V1).

Funções PURAS (sem I/O) — testadas em `tests/test_decomposicao_estrutural.py` com cenários
de resultado conhecido. Generaliza o bridge de Bennet já aprovado (`formulas.bennet_bridge`):
com 2 fatores o valor de Shapley usado aqui É o Bennet.

Notação (período t ∈ {0 = base, 1 = atual}; despesa BRUTA = valor apresentado):

    D = E · f · Σ_p s_p · P̄_p            (procedimentos p comparáveis = presentes nos 2 períodos)
        E   exposição (beneficiários-mês)
        f   eventos por beneficiário-mês = Σ_a e_a · r_a   (a = faixa etária)
        s_p participação do procedimento p no nº de eventos
        P̄_p custo médio do procedimento p = Σ_h w_h · P_h   (h = prestador)
        P_h = k_h · u_h ;  k = quantidade/evento (intensidade) ; u = valor/unidade
        u_h = T_h · ρ_h quando há preço contratado T (ρ = pago/contratado)

**Estágio 1** (Shapley, por procedimento — a propriedade de aditividade do Shapley
permite somar os termos): efeitos POPULAÇÃO (E), FREQUÊNCIA (f), MIX DE PROCEDIMENTOS (s)
e CUSTO MÉDIO DO PROCEDIMENTO (P̄). Como o termo é linear em cada fator,
φ_x = (x₁ − x₀) · m_x, onde m_x é o multiplicador de Shapley (média ponderada do produto
dos demais fatores sobre todas as ordens de substituição).

* FREQUÊNCIA se divide, exatamente, em PERFIL ETÁRIO (Σ Δe_a · r̄_a) e TAXA (o restante
  de Δf), ambos multiplicados pelo mesmo m_f.

**Estágio 2** (linear, por procedimento, exato): φ_P̄,p = Q̄_p · ΔP̄_p, com
ΔP̄_p = MIX DE PRESTADORES + Σ_{h nos 2 períodos} ω_h · ΔP_h  (ω = média das participações).
ΔP_h = Δk · ū (INTENSIDADE) + Δu · k̄ (CUSTO POR UNIDADE); Δu = ΔT · ρ̄ (PREÇO CONTRATUAL,
só com tabela nos 2 períodos) + Δρ · T̄ (custo por unidade não explicado pela tabela).

**Não comparável**: procedimentos presentes em só um período — valor explícito, nunca
redistribuído. **Camada financeira**: −Δglosa e −Δcoparticipação levam da bruta à
líquida (linear, exato).

    Δ despesa líquida = Σ efeitos           (identidade verificada em `reconciliacao`)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import factorial

EPS = 1e-9

# códigos estáveis dos efeitos (ordem de exibição)
POPULACAO = "populacao"
PERFIL_ETARIO = "perfil_etario"
FREQUENCIA = "frequencia"
MIX_PROCEDIMENTOS = "mix_procedimentos"
MIX_PRESTADORES = "mix_prestadores"
INTENSIDADE = "intensidade"
PRECO_CONTRATUAL = "preco_contratual"
CUSTO_UNITARIO = "custo_unitario"
NAO_COMPARAVEL = "nao_comparavel"
GLOSA = "glosa"
COPARTICIPACAO = "coparticipacao"

ORDEM = (POPULACAO, PERFIL_ETARIO, FREQUENCIA, MIX_PROCEDIMENTOS, MIX_PRESTADORES,
         INTENSIDADE, PRECO_CONTRATUAL, CUSTO_UNITARIO, NAO_COMPARAVEL, GLOSA, COPARTICIPACAO)

#: natureza de cada efeito — "isolado" (identificado pelo método), "nao_isolado" (resto
#: que o dado não permite separar) ou "financeiro" (camada glosa/coparticipação)
NATUREZA = {
    POPULACAO: "isolado", PERFIL_ETARIO: "isolado", FREQUENCIA: "isolado",
    MIX_PROCEDIMENTOS: "isolado", MIX_PRESTADORES: "isolado", INTENSIDADE: "isolado",
    PRECO_CONTRATUAL: "isolado", CUSTO_UNITARIO: "nao_isolado", NAO_COMPARAVEL: "nao_isolado",
    GLOSA: "financeiro", COPARTICIPACAO: "financeiro",
}

ROTULO = {
    POPULACAO: "Tamanho da carteira exposta",
    PERFIL_ETARIO: "Perfil etário da carteira",
    FREQUENCIA: "Frequência de utilização",
    MIX_PROCEDIMENTOS: "Mix de procedimentos",
    MIX_PRESTADORES: "Mix de prestadores (rede)",
    INTENSIDADE: "Intensidade (quantidade por evento)",
    PRECO_CONTRATUAL: "Preço contratual (tabela)",
    CUSTO_UNITARIO: "Custo unitário observado (sem evidência de tabela)",
    NAO_COMPARAVEL: "Procedimentos sem base comparável",
    GLOSA: "Glosas",
    COPARTICIPACAO: "Coparticipação",
}

#: unidade/leitura de cada efeito (exibida ao gestor)
UNIDADE = {
    POPULACAO: "beneficiários-mês expostos",
    PERFIL_ETARIO: "composição da exposição por faixa etária",
    FREQUENCIA: "eventos por beneficiário-mês, dentro de cada faixa etária",
    MIX_PROCEDIMENTOS: "participação de cada procedimento no nº de eventos",
    MIX_PRESTADORES: "participação de cada prestador no volume do mesmo procedimento",
    INTENSIDADE: "quantidade (sessões, diárias, itens) por evento, mesmo procedimento e prestador",
    PRECO_CONTRATUAL: "valor unitário da tabela contratada vigente",
    CUSTO_UNITARIO: "valor apresentado por unidade, mesmo procedimento e prestador",
    NAO_COMPARAVEL: "despesa de procedimentos que só ocorreram em um dos períodos",
    GLOSA: "valor glosado (reduz a despesa)",
    COPARTICIPACAO: "parcela cobrada do beneficiário (reduz a despesa)",
}


# ----------------------------------------------------------------------------- entradas
@dataclass(frozen=True)
class Celula:
    """Agregado de eventos de um período: procedimento × prestador × faixa etária."""

    procedimento: str
    prestador: str
    eventos: float
    quantidade: float
    valor: float  # despesa BRUTA (Σ valor_apresentado)
    faixa: str | None = None


@dataclass
class Periodo:
    celulas: list[Celula]
    #: beneficiários-mês expostos por faixa etária; None = exposição indisponível
    exposicao_por_faixa: dict[str, float] | None = None
    #: (procedimento, prestador) -> preço unitário contratado vigente no período
    precos: dict[tuple[str, str], float] = field(default_factory=dict)
    glosa: float = 0.0
    coparticipacao: float = 0.0

    @property
    def bruta(self) -> float:
        return sum(c.valor for c in self.celulas)

    @property
    def liquida(self) -> float:
        return self.bruta - self.glosa - self.coparticipacao


# ----------------------------------------------------------------------------- Shapley
def multiplicadores_shapley(x0: list[float], x1: list[float]) -> list[float]:
    """Multiplicadores m_i do valor de Shapley de um PRODUTO de fatores.

    Para v(S) = Π_j x_j^{(1 se j∈S senão 0)}, o Shapley do fator i é (x1_i − x0_i)·m_i, com
    m_i = Σ_{S ⊆ N∖{i}} |S|!(n−|S|−1)!/n! · Π_{j≠i} x_j^S.  Σ_i φ_i = Πx1 − Πx0 (exato).
    Com n = 2 reproduz o Bennet: m_1 = (x0_2 + x1_2)/2.
    """
    n = len(x0)
    peso = [factorial(k) * factorial(n - k - 1) / factorial(n) for k in range(n)]
    out = []
    for i in range(n):
        outros = [j for j in range(n) if j != i]
        m = 0.0
        for mask in range(1 << (n - 1)):
            prod = 1.0
            for b, j in enumerate(outros):
                prod *= x1[j] if (mask >> b) & 1 else x0[j]
            m += peso[bin(mask).count("1")] * prod
        out.append(m)
    return out


def shapley_produto(x0: list[float], x1: list[float]) -> list[float]:
    return [(b - a) * m for a, b, m in zip(x0, x1, multiplicadores_shapley(x0, x1), strict=True)]


# ----------------------------------------------------------------------------- resultado
@dataclass
class Efeito:
    codigo: str
    valor: float | None  # None = não calculável com os dados disponíveis
    disponivel: bool
    motivo: str = ""

    def as_dict(self, denom: float) -> dict:
        return {
            "codigo": self.codigo,
            "rotulo": ROTULO[self.codigo],
            "natureza": NATUREZA[self.codigo],
            "unidade": UNIDADE[self.codigo],
            "valor": round(self.valor, 2) if self.valor is not None else None,
            "participacao_pct": (round(self.valor / denom * 100.0, 2)
                                 if self.valor is not None and abs(denom) > EPS else None),
            "disponivel": self.disponivel,
            "motivo": self.motivo,
        }


@dataclass
class ResultadoDecomposicao:
    efeitos: dict[str, Efeito]
    por_procedimento: list[dict]
    por_prestador: list[dict]
    totais: dict
    cobertura: dict
    reconciliacao: dict

    def valor(self, codigo: str) -> float:
        v = self.efeitos[codigo].valor
        return v if v is not None else 0.0

    def denominador(self) -> float:
        """Base das participações: Δ observado, ou Σ|efeitos| quando há muito cancelamento
        (mesma regra de `formulas.contribuicoes`)."""
        delta = self.reconciliacao["delta_observado"]
        soma_abs = sum(abs(e.valor) for e in self.efeitos.values() if e.valor is not None)
        if soma_abs < EPS:
            return 1.0
        return delta if abs(delta) >= 0.15 * soma_abs else soma_abs

    def as_dict(self) -> dict:
        d = self.denominador()
        return {
            "efeitos": [self.efeitos[c].as_dict(d) for c in ORDEM],
            "base_participacao": "variacao_observada" if d == self.reconciliacao["delta_observado"]
                                 else "soma_absoluta_dos_efeitos",
            "por_procedimento": self.por_procedimento,
            "por_prestador": self.por_prestador,
            "totais": self.totais,
            "cobertura": self.cobertura,
            "reconciliacao": self.reconciliacao,
        }


def _div(a: float, b: float) -> float:
    return a / b if abs(b) > EPS else 0.0


def _agregar(celulas: list[Celula]):
    """{p: [n, D]}, {(p,h): [n, q, D]}, {(p, faixa): n}"""
    proc: dict[str, list[float]] = {}
    par: dict[tuple[str, str], list[float]] = {}
    faixa: dict[tuple[str, str | None], float] = {}
    for c in celulas:
        a = proc.setdefault(c.procedimento, [0.0, 0.0])
        a[0] += c.eventos
        a[1] += c.valor
        b = par.setdefault((c.procedimento, c.prestador), [0.0, 0.0, 0.0])
        b[0] += c.eventos
        b[1] += c.quantidade
        b[2] += c.valor
        faixa[(c.procedimento, c.faixa)] = faixa.get((c.procedimento, c.faixa), 0.0) + c.eventos
    return proc, par, faixa


def _intensidade_unidade(n: float, q: float, d: float) -> tuple[float, float]:
    """(k, u) com P = k·u. Sem quantidade informada (q ≤ 0) a intensidade é 1."""
    if q <= EPS:
        return 1.0, _div(d, n)
    return q / n, d / q


# ----------------------------------------------------------------------------- motor
def decompor(base: Periodo, atual: Periodo) -> ResultadoDecomposicao:
    proc0, par0, fx0 = _agregar(base.celulas)
    proc1, par1, fx1 = _agregar(atual.celulas)

    comparaveis = sorted(p for p in set(proc0) & set(proc1)
                         if proc0[p][0] > EPS and proc1[p][0] > EPS)
    nao_comp = sorted((set(proc0) | set(proc1)) - set(comparaveis))

    n0 = sum(proc0[p][0] for p in comparaveis)
    n1 = sum(proc1[p][0] for p in comparaveis)
    exp0 = base.exposicao_por_faixa
    exp1 = atual.exposicao_por_faixa
    e0 = sum(exp0.values()) if exp0 else 0.0
    e1 = sum(exp1.values()) if exp1 else 0.0
    com_exposicao = bool(comparaveis) and e0 > EPS and e1 > EPS

    ef = {c: 0.0 for c in ORDEM}
    m_f = 0.0
    por_proc: list[dict] = []
    por_prest: dict[str, dict] = {}

    def prest(h: str) -> dict:
        return por_prest.setdefault(h, {
            "chave": h, MIX_PRESTADORES: 0.0, INTENSIDADE: 0.0, PRECO_CONTRATUAL: 0.0,
            CUSTO_UNITARIO: 0.0, NAO_COMPARAVEL: 0.0,
            "despesa_anterior": 0.0, "despesa_atual": 0.0,
            "eventos_anterior": 0.0, "eventos_atual": 0.0,
        })

    desp_pares_ambos = desp_pares_tabela = desp_comparavel_estagio2 = 0.0
    pares_com_tabela = 0

    for p in comparaveis:
        np0, dp0 = proc0[p]
        np1, dp1 = proc1[p]
        pm0, pm1 = dp0 / np0, dp1 / np1
        s0, s1 = np0 / n0, np1 / n1
        if com_exposicao:
            x0, x1 = [e0, n0 / e0, s0, pm0], [e1, n1 / e1, s1, pm1]
            mult = multiplicadores_shapley(x0, x1)
            ef_pop = (x1[0] - x0[0]) * mult[0]
            ef_freq = (x1[1] - x0[1]) * mult[1]
            m_f += mult[1]
            ef_mix = (s1 - s0) * mult[2]
            q_bar = mult[3]
        else:
            x0, x1 = [n0, s0, pm0], [n1, s1, pm1]
            mult = multiplicadores_shapley(x0, x1)
            ef_pop = 0.0
            ef_freq = (n1 - n0) * mult[0]
            ef_mix = (s1 - s0) * mult[1]
            q_bar = mult[2]
        ef[POPULACAO] += ef_pop
        ef[FREQUENCIA] += ef_freq
        ef[MIX_PROCEDIMENTOS] += ef_mix

        # ---- estágio 2: dentro do procedimento
        hs = {h for (pp, h) in par0 if pp == p} | {h for (pp, h) in par1 if pp == p}
        pref_media = (pm0 + pm1) / 2.0
        loc = {MIX_PRESTADORES: 0.0, INTENSIDADE: 0.0, PRECO_CONTRATUAL: 0.0, CUSTO_UNITARIO: 0.0}
        for h in hs:
            a = par0.get((p, h), [0.0, 0.0, 0.0])
            b = par1.get((p, h), [0.0, 0.0, 0.0])
            w0, w1 = a[0] / np0, b[0] / np1
            ph = prest(h)
            ph["despesa_anterior"] += a[2]
            ph["despesa_atual"] += b[2]
            ph["eventos_anterior"] += a[0]
            ph["eventos_atual"] += b[0]
            if a[0] > EPS and b[0] > EPS:
                p0, p1 = a[2] / a[0], b[2] / b[0]
                pref = (p0 + p1) / 2.0
                omega = (w0 + w1) / 2.0
                k0, u0 = _intensidade_unidade(a[0], a[1], a[2])
                k1, u1 = _intensidade_unidade(b[0], b[1], b[2])
                kb = (k0 + k1) / 2.0
                v_int = q_bar * omega * (k1 - k0) * (u0 + u1) / 2.0
                v_uni = q_bar * omega * (u1 - u0) * kb
                t0, t1 = base.precos.get((p, h)), atual.precos.get((p, h))
                desp_pares_ambos += a[2] + b[2]
                if t0 and t1 and t0 > EPS and t1 > EPS:
                    r0, r1 = u0 / t0, u1 / t1
                    v_pre = q_bar * omega * kb * (t1 - t0) * (r0 + r1) / 2.0
                    v_res = q_bar * omega * kb * (r1 - r0) * (t0 + t1) / 2.0
                    desp_pares_tabela += a[2] + b[2]
                    pares_com_tabela += 1
                else:
                    v_pre, v_res = 0.0, v_uni
                loc[INTENSIDADE] += v_int
                loc[PRECO_CONTRATUAL] += v_pre
                loc[CUSTO_UNITARIO] += v_res
                ph[INTENSIDADE] += v_int
                ph[PRECO_CONTRATUAL] += v_pre
                ph[CUSTO_UNITARIO] += v_res
            elif b[0] > EPS:  # prestador novo no procedimento
                pref = b[2] / b[0]
            else:             # prestador saiu do procedimento
                pref = a[2] / a[0]
            v_mix = q_bar * (w1 - w0) * (pref - pref_media)
            loc[MIX_PRESTADORES] += v_mix
            ph[MIX_PRESTADORES] += v_mix
        desp_comparavel_estagio2 += dp0 + dp1
        for k, v in loc.items():
            ef[k] += v

        por_proc.append({
            "chave": p, "comparavel": True,
            "despesa_anterior": round(dp0, 2), "despesa_atual": round(dp1, 2),
            "delta": round(dp1 - dp0, 2),
            "eventos_anterior": np0, "eventos_atual": np1,
            "custo_medio_anterior": round(pm0, 2), "custo_medio_atual": round(pm1, 2),
            "participacao_eventos_anterior": round(s0, 6),
            "participacao_eventos_atual": round(s1, 6),
            "efeitos": {
                "volume": round(ef_pop + ef_freq, 2),
                MIX_PROCEDIMENTOS: round(ef_mix, 2),
                **{k: round(v, 2) for k, v in loc.items()},
            },
        })

    for p in nao_comp:
        a = proc0.get(p, [0.0, 0.0])
        b = proc1.get(p, [0.0, 0.0])
        ef[NAO_COMPARAVEL] += b[1] - a[1]
        por_proc.append({
            "chave": p, "comparavel": False,
            "despesa_anterior": round(a[1], 2), "despesa_atual": round(b[1], 2),
            "delta": round(b[1] - a[1], 2),
            "eventos_anterior": a[0], "eventos_atual": b[0],
            "custo_medio_anterior": round(_div(a[1], a[0]), 2),
            "custo_medio_atual": round(_div(b[1], b[0]), 2),
            "efeitos": {NAO_COMPARAVEL: round(b[1] - a[1], 2)},
        })
        for (pp, h), v in par0.items():
            if pp == p:
                ph = prest(h)
                ph[NAO_COMPARAVEL] -= v[2]
                ph["despesa_anterior"] += v[2]
                ph["eventos_anterior"] += v[0]
        for (pp, h), v in par1.items():
            if pp == p:
                ph = prest(h)
                ph[NAO_COMPARAVEL] += v[2]
                ph["despesa_atual"] += v[2]
                ph["eventos_atual"] += v[0]

    # ---- perfil etário × taxa (divisão exata de Δf)
    perfil_disp = False
    perfil_motivo = ""
    if com_exposicao:
        faixas = set(exp0) | set(exp1)
        nf0 = {a: sum(v for (p, fa), v in fx0.items() if fa == a and p in comparaveis) for a in faixas}
        nf1 = {a: sum(v for (p, fa), v in fx1.items() if fa == a and p in comparaveis) for a in faixas}
        com_faixa = sum(nf0.values()) + sum(nf1.values())
        if faixas - {None} and com_faixa > EPS:
            perfil = 0.0
            for a in faixas:
                ea0, ea1 = exp0.get(a, 0.0), exp1.get(a, 0.0)
                r0 = nf0[a] / ea0 if ea0 > EPS else None
                r1 = nf1[a] / ea1 if ea1 > EPS else None
                rr = [r for r in (r0, r1) if r is not None]
                if not rr:
                    continue
                perfil += (ea1 / e1 - ea0 / e0) * (sum(rr) / len(rr))
            delta_f = n1 / e1 - n0 / e0
            ef[PERFIL_ETARIO] = m_f * perfil
            ef[FREQUENCIA] = m_f * (delta_f - perfil)
            perfil_disp = True
        else:
            perfil_motivo = "faixa etária dos beneficiários não informada"
    else:
        perfil_motivo = "exposição (beneficiários-mês) indisponível no escopo"

    ef[GLOSA] = -(atual.glosa - base.glosa)
    ef[COPARTICIPACAO] = -(atual.coparticipacao - base.coparticipacao)

    efeitos: dict[str, Efeito] = {}
    for c in ORDEM:
        efeitos[c] = Efeito(c, ef[c], True)
    if not com_exposicao:
        efeitos[POPULACAO] = Efeito(POPULACAO, None, False, perfil_motivo)
    if not perfil_disp:
        efeitos[PERFIL_ETARIO] = Efeito(PERFIL_ETARIO, None, False, perfil_motivo)
    if not com_exposicao:
        efeitos[FREQUENCIA].motivo = ("sem exposição: mede a variação do nº de eventos "
                                      "(volume), não a taxa por beneficiário")
    if desp_pares_tabela <= EPS:
        efeitos[PRECO_CONTRATUAL] = Efeito(
            PRECO_CONTRATUAL, None, False,
            "sem tabela de preço contratado nos dois períodos para os pares analisados — "
            "variação de custo unitário NÃO é afirmada como reajuste")

    soma = sum(e.valor for e in efeitos.values() if e.valor is not None)
    delta = atual.liquida - base.liquida
    bruta0, bruta1 = base.bruta, atual.bruta
    ev0 = sum(c.eventos for c in base.celulas)
    ev1 = sum(c.eventos for c in atual.celulas)
    q0 = sum(c.quantidade for c in base.celulas)
    q1 = sum(c.quantidade for c in atual.celulas)
    total_desp = bruta0 + bruta1

    prestadores = []
    for h, v in por_prest.items():
        comp = v[INTENSIDADE] + v[PRECO_CONTRATUAL] + v[CUSTO_UNITARIO]
        prestadores.append({
            "chave": h,
            "despesa_anterior": round(v["despesa_anterior"], 2),
            "despesa_atual": round(v["despesa_atual"], 2),
            "delta": round(v["despesa_atual"] - v["despesa_anterior"], 2),
            "eventos_anterior": v["eventos_anterior"], "eventos_atual": v["eventos_atual"],
            "custo_medio_anterior": round(_div(v["despesa_anterior"], v["eventos_anterior"]), 2),
            "custo_medio_atual": round(_div(v["despesa_atual"], v["eventos_atual"]), 2),
            "participacao_anterior": round(_div(v["despesa_anterior"], bruta0), 6),
            "participacao_atual": round(_div(v["despesa_atual"], bruta1), 6),
            "efeitos": {
                MIX_PRESTADORES: round(v[MIX_PRESTADORES], 2),
                "custo_comparavel": round(comp, 2),
                INTENSIDADE: round(v[INTENSIDADE], 2),
                PRECO_CONTRATUAL: round(v[PRECO_CONTRATUAL], 2),
                CUSTO_UNITARIO: round(v[CUSTO_UNITARIO], 2),
                NAO_COMPARAVEL: round(v[NAO_COMPARAVEL], 2),
            },
        })
    prestadores.sort(key=lambda x: abs(x["delta"]), reverse=True)
    por_proc.sort(key=lambda x: abs(x["delta"]), reverse=True)

    return ResultadoDecomposicao(
        efeitos=efeitos,
        por_procedimento=por_proc,
        por_prestador=prestadores,
        totais={
            "despesa_bruta_anterior": round(bruta0, 2), "despesa_bruta_atual": round(bruta1, 2),
            "glosa_anterior": round(base.glosa, 2), "glosa_atual": round(atual.glosa, 2),
            "coparticipacao_anterior": round(base.coparticipacao, 2),
            "coparticipacao_atual": round(atual.coparticipacao, 2),
            "despesa_liquida_anterior": round(base.liquida, 2),
            "despesa_liquida_atual": round(atual.liquida, 2),
            "eventos_anterior": ev0, "eventos_atual": ev1,
            "quantidade_anterior": q0, "quantidade_atual": q1,
            "exposicao_anterior": round(e0, 2) if exp0 else None,
            "exposicao_atual": round(e1, 2) if exp1 else None,
            "custo_medio_anterior": round(_div(bruta0, ev0), 2),
            "custo_medio_atual": round(_div(bruta1, ev1), 2),
            "eventos_por_mil_anterior": round(_div(ev0, e0) * 1000, 3) if exp0 else None,
            "eventos_por_mil_atual": round(_div(ev1, e1) * 1000, 3) if exp1 else None,
        },
        cobertura={
            "despesa_em_procedimentos_comparaveis": round(
                _div(sum(proc0[p][1] + proc1[p][1] for p in comparaveis), total_desp), 4),
            "despesa_em_pares_comparaveis": round(_div(desp_pares_ambos, desp_comparavel_estagio2), 4),
            "despesa_com_tabela_de_preco": round(_div(desp_pares_tabela, desp_pares_ambos), 4),
            "pares_com_tabela_de_preco": pares_com_tabela,
            "exposicao_disponivel": com_exposicao,
            "perfil_etario_disponivel": perfil_disp,
            "quantidade_informada": abs((q0 + q1) - (ev0 + ev1)) > EPS,
            "procedimentos_comparaveis": len(comparaveis),
            "procedimentos_nao_comparaveis": len(nao_comp),
        },
        reconciliacao={
            "soma_efeitos": round(soma, 2),
            "delta_observado": round(delta, 2),
            "diferenca": round(soma - delta, 6),
            "ok": abs(soma - delta) <= max(0.01, 1e-9 * abs(delta)),
        },
    )
