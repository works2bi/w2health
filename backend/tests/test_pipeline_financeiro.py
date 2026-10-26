"""Consolidação do canônico V1 — cadastro de empresa e receita pelo pipeline REAL.

    CSV → RAW → mapping → DQ → Silver → Gold → reconciliação financeira → API

* empresa × contrato, faturamento direto e consolidado, receita reconhecida por nível;
* receita sem cadastro PRESERVADA como não atribuída (WARNING, não rejeição);
* sobreposição de vigência CONTRA O JÁ CARREGADO bloqueia a segunda carga;
* documento consolidado E detalhado (dupla contagem) bloqueia;
* receita reconhecida divergente da legada reprova a reconciliação — nada é publicado;
* gating de feature e isolamento entre tenants.
"""

from __future__ import annotations

import csv
import io

import pytest
from sqlalchemy import text

from app.models import TenantFeature
from tests.conftest import SUPERADMIN_EMAIL, create_tenant, create_user, iso_client, upload_and_wait
from tests.test_pipeline_e2e import _pacote

pytestmark = pytest.mark.scenarios

CSV_F = "csv-f"
MESES = ["2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06"]


def _csv(cab: list[str], linhas: list[list]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\n")
    w.writerow(cab)
    w.writerows(linhas)
    return buf.getvalue().encode()


def _ler(files: dict[str, bytes], nome: str) -> list[dict]:
    return list(csv.DictReader(io.StringIO(files[nome].decode()), delimiter=";"))


def _financeiro(pacote: dict[str, bytes], *, orfa: bool = True) -> dict[str, bytes]:
    """CT-001 faturado direto e detalhado por plano; EMP-2 (CT-003) consolidado SEM detalhe."""
    planos_ct1 = sorted(
        {
            r["cod_plano"]
            for r in _ler(pacote, "plano_contratos.csv")
            if r["cod_contrato"] == "CT-001"
        }
    )
    files = {
        "empresas.csv": _csv(
            ["cod_empresa", "nome_empresa", "tipo_empresa", "situacao"],
            [
                ["EMP-1", "Empresa Um (fictícia)", "empresarial", "ativa"],
                ["EMP-2", "Empresa Dois (fictícia)", "adesao", "ativa"],
            ],
        ),
        "empresa_contratos.csv": _csv(
            ["cod_empresa", "cod_contrato", "papel", "dt_inicio", "dt_fim"],
            [
                ["EMP-1", "CT-001", "", "01/01/2025", ""],
                ["EMP-1", "CT-002", "contratante", "01/01/2025", ""],
                ["EMP-2", "CT-003", "contratante", "01/01/2025", ""],
            ],
        ),
    }
    fat, itens, rec, rr = [], [], [], []
    for m in MESES:
        mm = m.replace("-", "")
        doc1, doc2 = f"F-{mm}-CT001", f"F-{mm}-EMP2"
        valores = {p: 1000 + 100 * i for i, p in enumerate(planos_ct1)}
        tot1 = sum(valores.values())
        fat += [
            [
                doc1,
                "contrato",
                "CT-001",
                "",
                m,
                "",
                "",
                f"{tot1},00",
                "0",
                "0",
                f"{tot1},00",
                "emitida",
            ],
            [doc2, "empresa", "", "EMP-2", m, "", "", "5000,00", "0", "0", "5000,00", "emitida"],
        ]
        for i, (p, v) in enumerate(valores.items(), 1):
            itens.append([doc1, str(i), "mensalidade", "CT-001", p, m, "", f"{v},00"])
            rr.append([f"R-{mm}-{p}", m, "CT-001", p, "", "", doc1, "", f"{v},00"])
        rr.append([f"R-{mm}-EMP2", m, "", "", "EMP-2", "", doc2, "", "5000,00"])
        if orfa:
            rr.append([f"R-{mm}-ORFA", m, "CT-999", "", "", "", "", "", "300,00"])
        rec.append([f"B-{doc1}", doc1, f"10/{m[5:]}/{m[:4]}", f"{tot1},00"])
    files["faturas.csv"] = _csv(
        [
            "nr_documento",
            "unidade",
            "cod_contrato",
            "cod_empresa",
            "competencia",
            "dt_emissao",
            "dt_vencimento",
            "vl_bruto",
            "vl_desconto",
            "vl_acrescimo",
            "vl_total",
            "situacao",
        ],
        fat,
    )
    files["fatura_itens.csv"] = _csv(
        [
            "nr_documento",
            "nr_item",
            "natureza",
            "cod_contrato",
            "cod_plano",
            "competencia_ref",
            "qtd_vidas",
            "vl_item",
        ],
        itens,
    )
    files["recebimentos.csv"] = _csv(["nr_baixa", "nr_documento", "dt_pagamento", "vl_pago"], rec)
    files["receitas_reconhecidas.csv"] = _csv(
        [
            "id_lancamento",
            "competencia",
            "cod_contrato",
            "cod_plano",
            "cod_empresa",
            "natureza",
            "nr_documento",
            "qtd_vidas",
            "vl_reconhecido",
        ],
        rr,
    )
    return files


@pytest.fixture(scope="module")
def env_f(iso_env, tmp_path_factory):
    create_tenant(iso_env, CSV_F)
    create_user(iso_env, "csv.f@iso.example", tenant=CSV_F, role="MANAGER")
    sa = iso_client(iso_env, SUPERADMIN_EMAIL, None)
    r = sa.post(
        f"/api/admin/tenants/{CSV_F}/sources",
        json={
            "name": "Pacote CSV",
            "source_type": "FILE",
            "source_system": "generic_csv",
            "configuration": {"mapping_id": "generic_operator", "mapping_version": 1},
        },
    )
    assert r.status_code == 201, r.text
    pacote = _pacote(tmp_path_factory.mktemp("pf"), seed=55, benef=120)
    base = {k: v for k, v in pacote.items() if k != "receitas.csv"}  # receita só reconhecida
    benef = _ler(pacote, "beneficiarios.csv")[0]
    vinc = _csv(
        ["matricula", "cod_contrato", "cod_plano", "dt_inicio", "dt_fim"],
        [[benef["matricula"], benef["cod_contrato"], benef["cod_plano"], "01/01/2024", ""]],
    )
    run = upload_and_wait(
        sa, CSV_F, r.json()["id"], {**base, **_financeiro(pacote), "vinculos.csv": vinc}
    )
    return {
        "sa": sa,
        "fonte": r.json()["id"],
        "pacote": pacote,
        "base": base,
        "run": run,
        "benef": benef,
        "iso": iso_env,
    }


def _id(env, tabela: str, codigo: str) -> int:
    with env["iso"].owner() as s:
        return s.execute(
            text(f"SELECT id FROM {tabela} WHERE tenant_id = :t AND codigo = :c"),
            {"t": CSV_F, "c": codigo},
        ).scalar_one()


def _dq(env, run: dict) -> set[tuple]:
    det = (
        env["sa"].get(f"/api/admin/tenants/{CSV_F}/ingestion-runs/{run['ingestion_run_id']}").json()
    )
    return {(d["rule_id"], d["severity"]) for d in det["data_quality"]}


def test_carga_financeira_publicada_e_reconciliada(env_f):
    r = env_f["run"]
    assert r["status"] == "SUCCESS" and r["stage"] == "AVAILABLE", r
    assert r["reconciliation"] in ("PASS", "WARNING")
    assert ("receita_sem_cadastro", "WARNING") in _dq(env_f, r)  # órfã preservada, não rejeitada
    with env_f["iso"].owner() as s:
        niveis = dict(
            s.execute(
                text(
                    "SELECT nivel_atribuicao, count(*) FROM receitas_reconhecidas WHERE tenant_id = :t GROUP BY 1"
                ),
                {"t": CSV_F},
            ).all()
        )
        papel = (
            s.execute(
                text("SELECT DISTINCT papel FROM empresas_contratos WHERE tenant_id = :t"),
                {"t": CSV_F},
            )
            .scalars()
            .all()
        )
    assert niveis["empresa"] == 6 and niveis["nao_atribuida"] == 6 and niveis["contrato_plano"] >= 6
    assert papel == ["contratante"]  # papel vazio na origem → contratante


def test_sinistralidade_contratual_so_com_receita_atribuida(env_f):
    c = iso_client(env_f["iso"], "csv.f@iso.example", CSV_F)
    ct1 = c.get(
        f"/api/analytics/contratos/{_id(env_f, 'contratos', 'CT-001')}",
        params={"competencia": "2026-03"},
    ).json()
    assert (
        ct1["receita_disponivel"] is True and ct1["sinistralidade_contratual"]["disponivel"] is True
    )
    ct3 = c.get(
        f"/api/analytics/contratos/{_id(env_f, 'contratos', 'CT-003')}",
        params={"competencia": "2026-03"},
    ).json()
    assert (
        ct3["sinistralidade_contratual"]["disponivel"] is False
        and ct3["sinistralidade_contratual"]["valor"] is None
    )
    emp2 = c.get(
        f"/api/analytics/empresas/{_id(env_f, 'empresas', 'EMP-2')}",
        params={"competencia": "2026-03"},
    ).json()
    assert (
        emp2["receita_consolidada_empresa"] == 5000 and emp2["sinistralidade_liquida"] is not None
    )
    comp = c.get("/api/analytics/receitas/composicao", params={"competencia": "2026-03"}).json()
    assert comp["receita_operadora"]["fonte"] == "reconhecida"
    rec = c.get("/api/analytics/receitas/reconciliacao", params={"competencia": "2026-03"}).json()
    assert rec["status"] in ("PASS", "WARNING")


def test_vigencia_sobreposta_com_carga_anterior_bloqueia(env_f):
    b = env_f["benef"]
    nova = _csv(
        ["matricula", "cod_contrato", "cod_plano", "dt_inicio", "dt_fim"],
        [[b["matricula"], b["cod_contrato"], b["cod_plano"], "01/06/2025", ""]],
    )  # dentro da vigente
    r = upload_and_wait(env_f["sa"], CSV_F, env_f["fonte"], {**env_f["base"], "vinculos.csv": nova})
    assert r["status"] == "FAILED" and r["stage"] == "RECEIVED"
    assert ("vigencia_sobreposta", "ERROR") in _dq(env_f, r)


def test_documento_consolidado_e_detalhado_bloqueia(env_f):
    rr = _csv(
        [
            "id_lancamento",
            "competencia",
            "cod_contrato",
            "cod_plano",
            "cod_empresa",
            "natureza",
            "nr_documento",
            "qtd_vidas",
            "vl_reconhecido",
        ],
        [
            ["X-1", "2026-03", "", "", "EMP-2", "", "F-DUPLO", "", "100,00"],
            ["X-2", "2026-03", "CT-003", "", "", "", "F-DUPLO", "", "100,00"],
        ],
    )
    r = upload_and_wait(
        env_f["sa"], CSV_F, env_f["fonte"], {**env_f["base"], "receitas_reconhecidas.csv": rr}
    )
    assert r["status"] == "FAILED"
    assert ("receita_documento_duplicado", "ERROR") in _dq(env_f, r)


def test_total_da_fatura_inconsistente_bloqueia(env_f):
    fat = _csv(
        [
            "nr_documento",
            "unidade",
            "cod_contrato",
            "cod_empresa",
            "competencia",
            "dt_emissao",
            "dt_vencimento",
            "vl_bruto",
            "vl_desconto",
            "vl_acrescimo",
            "vl_total",
            "situacao",
        ],
        [
            [
                "F-RUIM",
                "contrato",
                "CT-001",
                "",
                "2026-03",
                "",
                "",
                "100,00",
                "10,00",
                "0",
                "100,00",
                "",
            ]
        ],
    )
    r = upload_and_wait(env_f["sa"], CSV_F, env_f["fonte"], {**env_f["base"], "faturas.csv": fat})
    assert r["status"] == "FAILED"
    assert ("fatura_total_inconsistente", "ERROR") in _dq(env_f, r)


def test_receita_divergente_da_legada_reprova_e_nao_publica(env_f):
    with env_f["iso"].owner() as s:
        antes = s.execute(
            text("SELECT count(*) FROM receitas WHERE tenant_id = :t"), {"t": CSV_F}
        ).scalar_one()
    # receita legada do pacote (outro total) + reconhecida da primeira carga → divergência
    r = upload_and_wait(
        env_f["sa"],
        CSV_F,
        env_f["fonte"],
        {**env_f["base"], "receitas.csv": env_f["pacote"]["receitas.csv"]},
    )
    assert r["status"] == "FAILED" and r["stage"] == "PROCESSED"
    det = (
        env_f["sa"].get(f"/api/admin/tenants/{CSV_F}/ingestion-runs/{r['ingestion_run_id']}").json()
    )
    falhas = {c["check_id"] for c in det["reconciliation"] if c["status"] == "FAIL"}
    assert "receita_reconhecida_vs_legada" in falhas
    with env_f["iso"].owner() as s:
        depois = s.execute(
            text("SELECT count(*) FROM receitas WHERE tenant_id = :t"), {"t": CSV_F}
        ).scalar_one()
    assert depois == antes  # nada publicado


def test_gating_e_isolamento(env_f, iso_env):
    c = iso_client(iso_env, "csv.f@iso.example", CSV_F)
    with iso_env.owner() as s:
        s.add(
            TenantFeature(
                tenant_id=CSV_F, feature_key="contract_intelligence", enabled=False, reason="teste"
            )
        )
        s.commit()
    try:
        assert (
            c.get("/api/analytics/empresas", params={"competencia": "2026-03"}).status_code == 403
        )
        assert (
            c.get(
                "/api/analytics/investigacao", params={"competencia": "2026-03", "empresa_id": 1}
            ).status_code
            == 403
        )
    finally:
        with iso_env.owner() as s:
            s.execute(text("DELETE FROM tenant_features WHERE tenant_id = :t"), {"t": CSV_F})
            s.commit()
    # empresa de outro tenant é indistinguível de inexistente
    ca = iso_client(iso_env, "a.manager@iso.example", "tenant-a")
    assert (
        ca.get(
            f"/api/analytics/empresas/{_id(env_f, 'empresas', 'EMP-1')}",
            params={"competencia": "2026-03"},
        ).status_code
        == 404
    )
