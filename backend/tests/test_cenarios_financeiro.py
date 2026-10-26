"""Consolidação do canônico V1 — laboratório cadastral/financeiro com resultado conhecido.

Banco próprio (`w2health_test_financeiro`), tenant `w2h-financeiro`
(`app/seed/cenarios_financeiro.py`). Valores esperados calculados à mão a partir dos
tickets (PL-A 300, PL-B 500), da consulta mensal (100) e dos exames de B-X (50).
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.analytics import contratos, empresas, investigacao, receitas, sinistralidade
from app.analytics import reconciliacao_financeira as rf
from app.data_platform import readiness
from app.data_platform.silver import atribuicao_receita
from app.db.tenant_scope import bind_tenant
from app.seed import cenarios_financeiro as lab
from app.seed.aggregate import rebuild_aggregations
from tests.conftest import recreate_database

pytestmark = pytest.mark.scenarios

JAN, FEV, MAR, ABR, MAI, JUN = (date(2026, m, 1) for m in range(1, 7))


@pytest.fixture(scope="session")
def fin_maker():
    import app.models  # noqa: F401
    from app.db.base import create_all

    engine, _ = recreate_database("w2health_test_financeiro")
    create_all(engine)
    Maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with Maker() as s:
        lab.run_lab(s, verbose=False)
    yield Maker
    engine.dispose()


@pytest.fixture
def fin(fin_maker):
    s = fin_maker()
    bind_tenant(s, lab.TENANT)
    try:
        yield s
    finally:
        s.rollback()
        s.close()


def _id(s, tabela: str, codigo: str) -> int:
    return s.execute(
        text(f"SELECT id FROM {tabela} WHERE tenant_id = :t AND codigo = :c"),
        {"t": lab.TENANT, "c": codigo},
    ).scalar_one()


def _ctr(s, codigo: str, mes: date) -> dict:
    return dict(
        s.execute(
            text(
                "SELECT a.* FROM agg_contrato_competencia a JOIN contratos c ON c.id = a.id_contrato "
                "WHERE a.tenant_id = :t AND c.codigo = :c AND a.competencia = :m"
            ),
            {"t": lab.TENANT, "c": codigo, "m": mes},
        )
        .mappings()
        .one()
    )


def _eventos_de(s, codigo: str) -> list[tuple]:
    return s.execute(
        text(
            "SELECT e.data_evento, ct.codigo, pl.codigo FROM eventos_assistenciais e "
            "JOIN beneficiarios b ON b.id = e.id_beneficiario "
            "LEFT JOIN contratos ct ON ct.id = e.id_contrato LEFT JOIN planos pl ON pl.id = e.id_plano "
            "WHERE e.tenant_id = :t AND b.codigo = :c ORDER BY e.data_evento"
        ),
        {"t": lab.TENANT, "c": codigo},
    ).all()


# ------------------------------------------------------------------ regra pura
def test_atribuicao_receita_nunca_inventa_nivel():
    c, p, e = {"CT": 1}, {"PL": 2}, {"EMP": 3}
    assert (
        atribuicao_receita({"contrato_codigo": "CT", "plano_codigo": "PL"}, c, p, e)[
            "nivel_atribuicao"
        ]
        == "contrato_plano"
    )
    assert atribuicao_receita({"contrato_codigo": "CT"}, c, p, e)["nivel_atribuicao"] == "contrato"
    assert atribuicao_receita({"empresa_codigo": "EMP"}, c, p, e)["nivel_atribuicao"] == "empresa"
    orfa = atribuicao_receita({"contrato_codigo": "X"}, c, p, e)
    assert (
        orfa["nivel_atribuicao"] == "nao_atribuida"
        and orfa["referencia_origem"] == "contrato_codigo=X"
    )
    # plano desconhecido não vira "contrato" silenciosamente: fica não atribuída
    assert (
        atribuicao_receita({"contrato_codigo": "CT", "plano_codigo": "?"}, c, p, e)[
            "nivel_atribuicao"
        ]
        == "nao_atribuida"
    )


# ------------------------------------------------------------------ 1–6: empresa, contrato, faturamento
def test_01_empresa_com_varios_contratos_sem_dupla_contagem(fin):
    d = empresas.detalhe(fin, _id(fin, "empresas", "EMP-ALFA"), ABR)
    assert d["n_contratos"] == 2
    assert d["receita_contratos"] == pytest.approx(3700 + 2700)
    assert d["sinistralidade_liquida"] == pytest.approx(1800 / 6400 * 100, abs=0.01)
    lista = empresas.listar(fin, ABR)["itens"]
    # cada contrato conta em uma só empresa: Σ contratos das empresas = contratos com empresa
    assert sum(i["n_contratos"] for i in lista) == 6


def test_02_contrato_com_varios_planos(fin):
    n = fin.execute(
        text("SELECT count(*) FROM planos_contratos WHERE tenant_id = :t AND id_contrato = :c"),
        {"t": lab.TENANT, "c": _id(fin, "contratos", "CT-A1")},
    ).scalar_one()
    assert n == 2
    rows = dict(
        fin.execute(
            text(
                "SELECT pl.codigo, rr.receita_contraprestacao FROM receitas_reconhecidas rr JOIN planos pl ON pl.id = rr.id_plano "
                "WHERE rr.tenant_id = :t AND rr.competencia = :m AND rr.id_contrato = :c"
            ),
            {"t": lab.TENANT, "m": JAN, "c": _id(fin, "contratos", "CT-A1")},
        ).all()
    )
    assert rows == {"PL-A": 1800, "PL-B": 2000}


def test_03_faturamento_direto_por_contrato(fin):
    f = fin.execute(
        text(
            "SELECT unidade_cobranca, id_contrato, id_empresa, competencia_faturamento, valor_total "
            "FROM faturas WHERE tenant_id = :t AND numero_documento = 'FAT-202601-CT-A1'"
        ),
        {"t": lab.TENANT},
    ).one()
    assert f[0] == "contrato" and f[1] == _id(fin, "contratos", "CT-A1") and f[2] is None
    assert f[3] == date(2025, 12, 1)  # faturado em dez, reconhecido em jan
    assert float(f[4]) == 3800


def test_04_e_05_faturamento_consolidado_com_detalhamento(fin):
    f = fin.execute(
        text(
            "SELECT f.unidade_cobranca, f.id_empresa, f.valor_total, count(it.id), sum(it.valor) "
            "FROM faturas f JOIN fatura_itens it ON it.id_fatura = f.id "
            "WHERE f.tenant_id = :t AND f.numero_documento = 'FAT-202601-EMP-BETA' GROUP BY 1, 2, 3"
        ),
        {"t": lab.TENANT},
    ).one()
    assert f[0] == "empresa" and f[1] == _id(fin, "empresas", "EMP-BETA") and f[3] == 2
    assert float(f[2]) == float(f[4]) == 1800 + 2500
    b1 = _ctr(fin, "CT-B1", JAN)
    assert b1["receita_reconhecida"] == 1800 and b1["sinistralidade_liquida"] == pytest.approx(
        600 / 1800 * 100
    )


def test_06_empresa_sem_detalhamento_nao_e_rateada(fin):
    g1 = _ctr(fin, "CT-G1", JAN)
    assert g1["receita_reconhecida"] is None and g1["sinistralidade_liquida"] is None
    assert g1["motivo_sem_sinistralidade"] == "sem_receita_atribuida"
    assert (
        fin.execute(
            text(
                "SELECT count(*) FROM receitas_reconhecidas WHERE tenant_id = :t AND id_contrato = ANY(:c)"
            ),
            {
                "t": lab.TENANT,
                "c": [_id(fin, "contratos", "CT-G1"), _id(fin, "contratos", "CT-G2")],
            },
        ).scalar_one()
        == 0
    )
    gama = empresas.detalhe(fin, _id(fin, "empresas", "EMP-GAMA"), JAN)
    assert gama["receita_consolidada_empresa"] == 4100 and gama["receita_contratos"] == 0
    assert gama["sinistralidade_liquida"] == pytest.approx(1100 / 4100 * 100, abs=0.01)
    assert all(not c["sinistralidade_contratual"]["disponivel"] for c in gama["contratos"])


# ------------------------------------------------------------------ 7–10: vínculo histórico
def test_07_e_09_mudanca_de_contrato_e_despesa_antes_e_depois(fin):
    ev = _eventos_de(fin, "CT-A1-01")
    antes = {c for d, c, _p in ev if d < lab.MUDANCA}
    depois = {c for d, c, _p in ev if d >= lab.MUDANCA}
    assert antes == {"CT-A1"} and depois == {"CT-A2"}
    assert (
        _ctr(fin, "CT-A1", FEV)["despesa_liquida"] == 1000 + 50
    )  # exame de fev fica no contrato antigo
    assert _ctr(fin, "CT-A2", MAI)["despesa_liquida"] == 900 + 50


def test_mudanca_de_contrato_nao_reclassifica_despesa_antiga(fin):
    """O cadastro ATUAL de B-X aponta CT-A2; reconstruir a Gold não move a despesa de fev."""
    rebuild_aggregations(fin, tenant_id=lab.TENANT)
    assert _ctr(fin, "CT-A1", FEV)["despesa_liquida"] == 1050
    assert _ctr(fin, "CT-A2", FEV)["despesa_liquida"] == 800


def test_08_mudanca_de_plano(fin):
    ev = _eventos_de(fin, "CT-A1-02")
    assert {p for d, _c, p in ev if d < lab.MUDANCA} == {"PL-A"}
    assert {p for d, _c, p in ev if d >= lab.MUDANCA} == {"PL-B"}


def test_10_vidas_historicas_pelo_vinculo_vigente(fin):
    assert (_ctr(fin, "CT-A1", MAR)["vidas"], _ctr(fin, "CT-A1", ABR)["vidas"]) == (10, 9)
    assert (_ctr(fin, "CT-A2", MAR)["vidas"], _ctr(fin, "CT-A2", ABR)["vidas"]) == (8, 9)
    assert (_ctr(fin, "CT-B1", MAR)["vidas"], _ctr(fin, "CT-B1", ABR)["vidas"]) == (6, 5)


def test_evento_sem_vinculo_vigente_fica_nao_atribuido(fin):
    ev = _eventos_de(fin, "CT-B1-01")  # B-Z: vínculo encerrado em abr, atendimento em mai
    assert [c for d, c, _p in ev if d >= lab.MUDANCA] == [None]
    v = {x["check_id"]: x for x in rf.resumo(rf.verificar(fin, [MAI]))["verificacoes"]}
    assert v["despesa_contratos_mais_nao_atribuida"]["status"] == "PASS"
    assert "não atribuída: R$ 100,00" in v["despesa_contratos_mais_nao_atribuida"]["explicacao"]


# ------------------------------------------------------------------ 11–12
def test_11_receita_sem_cadastro_preservada_e_fora_dos_contratos(fin):
    r = fin.execute(
        text(
            "SELECT nivel_atribuicao, referencia_origem, receita_contraprestacao "
            "FROM receitas_reconhecidas WHERE tenant_id = :t AND source_record_id = 'RR-202601-ORFA'"
        ),
        {"t": lab.TENANT},
    ).one()
    assert (
        r[0] == "nao_atribuida" and r[1] == "contrato_codigo=CT-INEXISTENTE" and float(r[2]) == 250
    )
    s = sinistralidade.indicador(fin, JAN)
    assert s["receita"] == 14850 and s["fonte_receita"] == "reconhecida"
    soma_ctr = fin.execute(
        text(
            "SELECT sum(receita_reconhecida) FROM agg_contrato_competencia "
            "WHERE tenant_id = :t AND competencia = :m"
        ),
        {"t": lab.TENANT, "m": JAN},
    ).scalar()
    assert soma_ctr == 6200 + 4300  # nem a órfã nem a GAMA entram em contrato


def test_12_contrato_sem_receita_sem_sinistralidade_ficticia(fin):
    d = contratos.detalhe(fin, _id(fin, "contratos", "CT-SEM"), ABR)
    assert d["receita_disponivel"] is False and "Receita por contrato" in d["aviso"]
    assert (
        d["sinistralidade_contratual"]["disponivel"] is False
        and d["sinistralidade_contratual"]["valor"] is None
    )
    assert d["kpis"]["despesa_liquida"] == 300 and d["kpis"]["vidas"] == 3


# ------------------------------------------------------------------ faturado × reconhecido × recebido
def test_faturado_reconhecido_e_recebido_sao_fatos_distintos(fin):
    c = receitas.composicao(fin, MAR)
    assert c["receita_reconhecida"]["total"] == 14850  # cobertura de março
    assert c["faturamento"]["total"] == 14750 - 250 + float(
        lab.ACRESCIMO
    )  # docs de abril, com juros
    assert c["recebimento"]["total"] == 14850 - 250  # recebido em março
    assert (
        len(
            {
                c["receita_reconhecida"]["total"],
                c["faturamento"]["total"],
                c["recebimento"]["total"],
            }
        )
        == 3
    )
    niveis = {n["nivel"]: n for n in c["receita_reconhecida"]["niveis"]}
    assert (
        niveis["empresa"]["valor"] == 4100
        and not niveis["empresa"]["entra_na_sinistralidade_contratual"]
    )
    assert niveis["nao_atribuida"]["valor"] == 250


# ------------------------------------------------------------------ reconciliação e divergência
def test_reconciliacao_financeira_sem_falhas(fin):
    res = rf.resumo(rf.verificar(fin))
    assert res["status"] != "FAIL", [v for v in res["verificacoes"] if v["status"] == "FAIL"]
    ids = {v["check_id"] for v in res["verificacoes"]}
    assert {
        "receita_sem_duplicidade",
        "receita_original_vs_niveis",
        "receita_reconhecida_vs_legada",
        "receita_atribuida_vs_plano",
        "receita_atribuida_vs_contratos",
        "empresa_vs_contratos_relacionados",
        "fatura_itens_vs_total",
        "faturado_vs_recebido",
        "despesa_eventos_vs_gold",
        "despesa_contratos_mais_nao_atribuida",
        "despesa_beneficiarios_vs_total",
        "vidas_historicas",
        "receita_documento_em_dois_niveis",
    } <= ids
    # diferença por plano = receita não detalhada até contrato × plano (GAMA + órfã) — explicada
    planos = [v for v in res["verificacoes"] if v["check_id"] == "receita_atribuida_vs_plano"]
    assert planos and all(v["status"] == "WARNING" for v in planos)


def test_divergencia_entre_fontes_bloqueia_sinistralidade(fin):
    fin.execute(
        text(
            "UPDATE receitas SET receita_contraprestacao = receita_contraprestacao + 100 "
            "WHERE tenant_id = :t AND competencia = :m AND id_plano = (SELECT min(id_plano) "
            "FROM receitas WHERE tenant_id = :t AND competencia = :m)"
        ),
        {"t": lab.TENANT, "m": MAR},
    )
    rebuild_aggregations(fin, tenant_id=lab.TENANT)
    s = sinistralidade.indicador(fin, MAR)
    assert s["sinistralidade_atual"] is None and s["receita_status"] == "divergente" and s["aviso"]
    assert _ctr(fin, "CT-A1", MAR)["motivo_sem_sinistralidade"] == "receita_divergente"
    v = {x["check_id"]: x for x in rf.resumo(rf.verificar(fin, [MAR]))["verificacoes"]}
    assert v["receita_reconhecida_vs_legada"]["status"] == "FAIL"
    # a fonte continua única: a receita da Gold é a reconhecida, nunca a soma
    assert s["receita"] is None and v["receita_sem_duplicidade"]["status"] == "PASS"
    serie = {r["competencia"]: r for r in sinistralidade.serie(fin)}
    assert (
        serie["2026-03-01"]["sinistralidade"] is None
        and serie["2026-02-01"]["sinistralidade"] is not None
    )
    # (o fixture faz rollback — os dados do laboratório voltam ao original)


# ------------------------------------------------------------------ investigação, readiness
def test_investigacao_por_contrato_explica_a_sinistralidade(fin):
    r = investigacao.investigar(fin, ABR, contrato_id=_id(fin, "contratos", "CT-A1"))
    assert r["decomposicao"]["reconciliacao"]["ok"]
    sin = r["sinistralidade"]
    assert sin["disponivel"] and sin["s0"] == pytest.approx(1000 / 3800 * 100, abs=0.01)
    assert sin["s1"] == pytest.approx(900 / 3700 * 100, abs=0.01)
    assert sin["efeito_despesa_pp"] + sin["efeito_receita_pp"] == pytest.approx(
        sin["variacao_pp"], abs=1e-6
    )
    assert sum(sin["efeitos_despesa_pp"].values()) == pytest.approx(
        sin["efeito_despesa_pp"], abs=1e-3
    )
    assert "variacao_sinistralidade_escopo" in {a["codigo"] for a in r["achados"]}


def test_investigacao_por_empresa_e_contrato_sem_receita(fin):
    r = investigacao.investigar(fin, ABR, empresa_id=_id(fin, "empresas", "EMP-GAMA"))
    assert r["decomposicao"]["reconciliacao"]["ok"] and r["sinistralidade"]["disponivel"]
    assert r["variacao"]["despesa_liquida_atual"] == 1100
    g = investigacao.investigar(fin, ABR, contrato_id=_id(fin, "contratos", "CT-G1"))
    assert not g["sinistralidade"]["disponivel"]
    assert any("indisponível" in lim for lim in g["achados"][0]["limitacoes"])
    with pytest.raises(ValueError):
        investigacao.investigar(fin, ABR, contrato_id=1, empresa_id=1)


def test_readiness_com_receita_contratual(fin):
    r = readiness.evaluate(readiness.signals(fin, lab.TENANT))
    assert r["contract_intelligence"].status == readiness.READY
    assert r["loss_ratio_intelligence"].status == readiness.READY  # receita só reconhecida basta


# ------------------------------------------------------------------ compatibilidade com receita legada
def test_tenant_so_com_receita_legada_continua_igual(db):
    s = sinistralidade.indicador(db, date(2026, 7, 1))
    assert s["fonte_receita"] == "legada_plano" and s["receita_status"] == "ok"
    assert s["sinistralidade_atual"] is not None
    c = receitas.composicao(db, date(2026, 7, 1))
    assert (
        c["receita_reconhecida"]["niveis"] == []
        and c["receita_legada_plano"] == c["receita_operadora"]["valor"]
    )
    r = readiness.evaluate(readiness.signals(db, "w2h-demo"))
    assert (
        r["contract_intelligence"].status == readiness.PARTIAL
    )  # funciona, sem sinistralidade contratual
    falhas = [v for v in rf.verificar(db, [date(2026, 7, 1)]) if v.status == rf.FAIL]
    assert not falhas, [f.as_dict() for f in falhas]
