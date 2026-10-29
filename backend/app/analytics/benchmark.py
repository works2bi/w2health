"""Benchmark de mercado com dados abertos da ANS (Evolução, item 7).

Pergunta: "a minha sinistralidade / custo por vida está alta ou baixa para o meu segmento?"

* Referência: distribuição (p25 / mediana / p75) do indicador entre as operadoras da mesma
  modalidade, calculada dos arquivos públicos da ANS — demonstrações contábeis (contas 311 =
  contraprestações efetivas, 411 = eventos indenizáveis líquidos) + cadastro de operadoras
  (CADOP, modalidade) + beneficiários por operadora (opcional, para despesa por vida/mês).
* Operadora: últimos 12 meses da camada analítica até a competência escolhida.
* Sem carga de dados abertos, usa a referência ILUSTRATIVA — e diz isso em todo lugar
  (`ilustrativo: true`). Nunca apresentar como dado de mercado real.

Rótulos: o indicador da operadora é FATO; a posição frente ao mercado é FATO sobre a
referência carregada; diferenças de critério contábil (regime de competência, PEONA) entre
a operadora e o DIOPS das demais tornam a comparação APROXIMADA — dito na resposta.
"""

from __future__ import annotations

import csv
import io
from collections import defaultdict
from datetime import date

import numpy as np
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.benchmark import BenchmarkMercado
from app.repositories import analytics_repo as repo

MODALIDADES = {
    "medicina_de_grupo": "Medicina de grupo",
    "cooperativa_medica": "Cooperativa médica",
    "autogestao": "Autogestão",
    "seguradora": "Seguradora especializada em saúde",
    "filantropia": "Filantropia",
    "mercado": "Mercado (todas as modalidades médico-hospitalares)",
}
INDICADORES = {"sinistralidade_pct": "Sinistralidade (%)", "despesa_pmpm": "Despesa por vida/mês (R$)"}

# Valores de DEMONSTRAÇÃO (ordem de grandeza plausível, NÃO são dados da ANS).
_ILUSTRATIVO = {
    "sinistralidade_pct": {"medicina_de_grupo": (76, 82, 88), "cooperativa_medica": (78, 84, 90),
                           "autogestao": (85, 91, 97), "seguradora": (79, 85, 89),
                           "filantropia": (74, 81, 89), "mercado": (77, 84, 90)},
    "despesa_pmpm": {"medicina_de_grupo": (280, 360, 450), "cooperativa_medica": (300, 390, 480),
                     "autogestao": (420, 520, 640), "seguradora": (450, 560, 700),
                     "filantropia": (250, 330, 420), "mercado": (300, 400, 520)},
}
_ALIAS_MODALIDADE = {
    "medicina de grupo": "medicina_de_grupo", "cooperativa médica": "cooperativa_medica",
    "cooperativa medica": "cooperativa_medica", "autogestão": "autogestao", "autogestao": "autogestao",
    "seguradora especializada em saúde": "seguradora", "seguradora especializada em saude": "seguradora",
    "filantropia": "filantropia",
}


def garantir_ilustrativo(session: Session, periodo: str = "2025") -> int:
    """Insere a referência ilustrativa se ainda não houver nenhuma (idempotente)."""
    if session.execute(select(BenchmarkMercado.id).limit(1)).first():
        return 0
    n = 0
    for ind, por_seg in _ILUSTRATIVO.items():
        for seg, (p25, med, p75) in por_seg.items():
            session.add(BenchmarkMercado(fonte="ilustrativo", periodo=periodo, segmento=seg, indicador=ind,
                                         p25=p25, mediana=med, p75=p75, media_ponderada=med, n_operadoras=0))
            n += 1
    session.flush()
    return n


# ------------------------------------------------------------------- dados abertos da ANS
def _num(s: str) -> float:
    s = (s or "").strip()
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    return float(s) if s else 0.0


def _ler_csv(dados: bytes) -> list[dict]:
    try:
        texto = dados.decode("utf-8-sig")
    except UnicodeDecodeError:
        texto = dados.decode("latin-1")
    return [{(k or "").strip().strip('"').lower(): (v or "").strip().strip('"') for k, v in r.items()}
            for r in csv.DictReader(io.StringIO(texto), delimiter=";")]


def calcular_de_dados_abertos(demonstracoes: bytes, cadop: bytes, periodo: str,
                              beneficiarios: bytes | None = None, meses_periodo: int = 12) -> list[dict]:
    """Distribuição por modalidade a partir dos CSVs públicos da ANS.

    `demonstracoes`: DATA;REG_ANS;CD_CONTA_CONTABIL;DESCRICAO;VL_SALDO_INICIAL;VL_SALDO_FINAL
    (saldo final acumulado no ano — use o arquivo do 4º trimestre para o ano fechado);
    `cadop`: Registro_ANS;...;Modalidade;...; `beneficiarios` (opcional): REG_ANS;QT_BENEFICIARIOS
    (média do período)."""
    modalidade = {}
    for r in _ler_csv(cadop):
        reg = r.get("registro_ans") or r.get("reg_ans") or ""
        modalidade[reg.lstrip("0")] = _ALIAS_MODALIDADE.get(r.get("modalidade", "").lower())
    contas: dict[str, dict[str, float]] = defaultdict(dict)
    for r in _ler_csv(demonstracoes):
        conta = (r.get("cd_conta_contabil") or "").replace(".", "")
        if conta in ("311", "411"):
            contas[(r.get("reg_ans") or "").lstrip("0")][conta] = abs(_num(r.get("vl_saldo_final", "")))
    vidas = {}
    if beneficiarios:
        for r in _ler_csv(beneficiarios):
            vidas[(r.get("reg_ans") or r.get("registro_ans") or "").lstrip("0")] = _num(
                r.get("qt_beneficiarios") or r.get("beneficiarios") or "")

    por_seg: dict[str, dict[str, list[tuple[float, float, float]]]] = defaultdict(lambda: defaultdict(list))
    for reg, c in contas.items():
        rec, desp = c.get("311", 0.0), c.get("411", 0.0)
        seg = modalidade.get(reg)
        if seg is None or rec <= 0:
            continue
        sin = desp / rec * 100
        if not 10 <= sin <= 300:  # descarta operadora em situação atípica (início/encerramento)
            continue
        for s in (seg, "mercado"):
            por_seg[s]["sinistralidade_pct"].append((sin, desp, rec))
            if vidas.get(reg, 0) > 0:
                por_seg[s]["despesa_pmpm"].append((desp / vidas[reg] / meses_periodo, desp, vidas[reg] * meses_periodo))

    linhas = []
    for seg, inds in por_seg.items():
        for ind, vals in inds.items():
            if len(vals) < 5:  # poucas operadoras: distribuição não representativa
                continue
            xs = np.array([v[0] for v in vals])
            pond = sum(v[1] for v in vals) / sum(v[2] for v in vals) * (100 if ind == "sinistralidade_pct" else 1)
            linhas.append({"fonte": "ans_dados_abertos", "periodo": periodo, "segmento": seg, "indicador": ind,
                           "p25": round(float(np.percentile(xs, 25)), 2),
                           "mediana": round(float(np.percentile(xs, 50)), 2),
                           "p75": round(float(np.percentile(xs, 75)), 2),
                           "media_ponderada": round(pond, 2), "n_operadoras": len(vals)})
    return linhas


def gravar(session: Session, linhas: list[dict]) -> int:
    for ln in linhas:
        session.execute(delete(BenchmarkMercado).where(
            BenchmarkMercado.fonte == ln["fonte"], BenchmarkMercado.periodo == ln["periodo"],
            BenchmarkMercado.segmento == ln["segmento"], BenchmarkMercado.indicador == ln["indicador"]))
        session.add(BenchmarkMercado(**ln))
    session.flush()
    return len(linhas)


# ------------------------------------------------------------------- comparação
def _referencias(session: Session, segmento: str) -> tuple[str | None, dict[str, BenchmarkMercado]]:
    rows = session.execute(select(BenchmarkMercado).where(BenchmarkMercado.segmento == segmento)).scalars().all()
    if not rows:
        return None, {}
    # dado real tem precedência sobre o ilustrativo; depois o período mais recente
    fonte = "ans_dados_abertos" if any(r.fonte == "ans_dados_abertos" for r in rows) else "ilustrativo"
    periodo = max(r.periodo for r in rows if r.fonte == fonte)
    return fonte, {r.indicador: r for r in rows if r.fonte == fonte and r.periodo == periodo}


def _posicao(v: float, r: BenchmarkMercado) -> str:
    if v < r.p25:
        return "abaixo_p25"
    if v <= r.mediana:
        return "p25_mediana"
    if v <= r.p75:
        return "mediana_p75"
    return "acima_p75"


ROTULO_POSICAO = {"abaixo_p25": "entre os 25% mais baixos do segmento",
                  "p25_mediana": "abaixo da mediana do segmento",
                  "mediana_p75": "acima da mediana do segmento",
                  "acima_p75": "entre os 25% mais altos do segmento"}


def comparar(session: Session, competencia: date, segmento: str = "medicina_de_grupo") -> dict:
    if segmento not in MODALIDADES:
        raise ValueError(f"segmento inválido; use um de: {', '.join(MODALIDADES)}")
    serie = [r for r in repo.serie_sinistralidade(session) if r["competencia"] <= competencia][-12:]
    rec = sum(float(r["receita"] or 0) for r in serie)
    desp = sum(float(r["despesa_liquida"] or 0) for r in serie)
    expo = sum(float(r["exposicao_beneficiario_mes"] or 0) for r in serie)
    operadora = {
        "sinistralidade_pct": round(desp / rec * 100, 2) if rec > 0 else None,
        "despesa_pmpm": round(desp / expo, 2) if expo > 0 else None,
    }
    fonte, refs = _referencias(session, segmento)
    itens = []
    for ind, rot in INDICADORES.items():
        r, v = refs.get(ind), operadora[ind]
        itens.append({
            "indicador": ind, "rotulo": rot, "operadora": v,
            "referencia": None if r is None else {"p25": r.p25, "mediana": r.mediana, "p75": r.p75,
                                                  "media_ponderada": r.media_ponderada,
                                                  "n_operadoras": r.n_operadoras, "periodo": r.periodo},
            "posicao": None if r is None or v is None else _posicao(v, r),
            "posicao_rotulo": None if r is None or v is None else ROTULO_POSICAO[_posicao(v, r)],
            "diferenca_mediana": None if r is None or v is None else round(v - r.mediana, 2),
        })
    return {
        "competencia": competencia.isoformat(),
        "janela": {"inicio": serie[0]["competencia"].isoformat() if serie else None,
                   "fim": serie[-1]["competencia"].isoformat() if serie else None, "meses": len(serie)},
        "segmento": segmento, "segmento_rotulo": MODALIDADES[segmento],
        "segmentos": [{"codigo": k, "rotulo": v} for k, v in MODALIDADES.items()],
        "fonte": fonte, "ilustrativo": fonte == "ilustrativo", "disponivel": bool(refs),
        "itens": itens,
        "aviso": ("Referência ILUSTRATIVA para demonstração — não é dado de mercado. Carregue os "
                  "dados abertos da ANS para a comparação real.") if fonte == "ilustrativo" else None,
        "limitacoes": [
            "Mercado pelo DIOPS (regime contábil, inclui PEONA); operadora pela competência de "
            "atendimento — a comparação é aproximada.",
            "Mix de carteira (idade, região, segmentação do plano) muda o custo esperado; a "
            "posição não ajusta por mix.",
        ],
    }
