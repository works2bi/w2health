"""Testes da fundação multi-tenant (v1.2).

Provam:
  * todo modelo persistido (menos o calendário `competencias` e o cadastro `tenants`) tem
    `tenant_id` NOT NULL;
  * a massa sintética é 100% carimbada com o tenant `w2h-demo`;
  * a chave de negócio é COMPOSTA — `(tenant_id, codigo)` —: dois tenants podem ter
    `BEN-000001` sem colisão, e uma query SEM filtro de tenant enxerga os dois (dívida de
    Fase 2, explícita);
  * as tabelas novas da v1.2 (`agg_contrato_competencia`, `receitas_contrato`, controle de
    ingestão) são tenant-aware;
  * o motor analítico consome um MODELO CANÔNICO — o nome da coluna de origem não vaza
    para nenhum SQL analítico.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text

from app.core.tenant import DEFAULT_TENANT
from app.db.base import Base

pytestmark = pytest.mark.scenarios

_SEM_TENANT = {"competencias", "tenants"}


def test_todo_modelo_persistido_tem_tenant_id():
    faltando = [
        t for t, tbl in Base.metadata.tables.items()
        if t not in _SEM_TENANT and "tenant_id" not in tbl.columns
    ]
    assert not faltando, f"tabelas sem tenant_id: {faltando}"
    for t, tbl in Base.metadata.tables.items():
        if t in _SEM_TENANT:
            continue
        assert tbl.columns["tenant_id"].nullable is False, f"{t}.tenant_id deveria ser NOT NULL"


def test_seed_carimba_tenant_demo(db):
    for tabela in ("beneficiarios", "eventos_assistenciais", "contratos", "receitas",
                   "agg_sinistralidade_competencia", "agg_contrato_competencia",
                   "cenarios_gabarito", "regras_alerta"):
        distintos = db.execute(
            text(f"SELECT DISTINCT tenant_id FROM {tabela}")
        ).scalars().all()
        assert distintos == [DEFAULT_TENANT], f"{tabela}: {distintos}"
    assert db.execute(text("SELECT id FROM tenants")).scalars().all() == [DEFAULT_TENANT]


def test_chave_de_negocio_e_composta_sem_colisao(db):
    """tenant_a + BEN-000001 != tenant_b + BEN-000001 (nem entre si, nem com w2h-demo)."""
    base = db.execute(
        text("SELECT id_regiao, id_plano, id_contrato FROM beneficiarios LIMIT 1")
    ).mappings().first()
    for tid in ("tenant_a", "tenant_b"):
        # o beneficiário referencia o par (tenant, contrato, plano) — FK composta
        db.execute(
            text("INSERT INTO planos_contratos (tenant_id, id_contrato, id_plano) VALUES (:t, :c, :p)"),
            {"t": tid, "c": base["id_contrato"], "p": base["id_plano"]},
        )
        db.execute(
            text(
                "INSERT INTO beneficiarios (tenant_id, codigo, sexo, data_nascimento, "
                "faixa_etaria, id_regiao, id_plano, id_contrato, data_adesao, status) "
                "VALUES (:t, 'BEN-000001', 'F', '1990-01-01', '30-39', :r, :p, :c, "
                "'2025-01-01', 'ativo')"
            ),
            {"t": tid, "r": base["id_regiao"], "p": base["id_plano"], "c": base["id_contrato"]},
        )
    db.flush()
    # a chave composta permitiu as três linhas coexistirem
    total = db.execute(
        text("SELECT COUNT(*) FROM beneficiarios WHERE codigo = 'BEN-000001'")
    ).scalar_one()
    assert total == 3, "esperava w2h-demo + tenant_a + tenant_b com o mesmo código"
    por_tenant = db.execute(
        text("SELECT COUNT(DISTINCT tenant_id) FROM beneficiarios WHERE codigo = 'BEN-000001'")
    ).scalar_one()
    assert por_tenant == 3
    db.rollback()  # não persiste o cenário de teste


def test_tabelas_v1_2_sao_tenant_aware():
    insp = {t for t in Base.metadata.tables}
    # `receitas_contrato` (layout v1.2) virou `receitas_reconhecidas` — Consolidação V1
    for t in ("agg_contrato_competencia", "receitas_reconhecidas", "ingestion_runs",
              "pipeline_runs", "source_connections", "source_entities",
              "data_quality_results"):
        assert t in insp
        assert "tenant_id" in Base.metadata.tables[t].columns


# --------------------------------------------------------------- teste source-agnostic
_MAPPING_EXEMPLO = {
    # campo_da_origem -> campo_canonico_w2health
    "codigo_benef": "id_beneficiario",
    "data_atend": "data_evento",
    "valor_conta": "valor_apresentado",
    "valor_glosa": "valor_glosado",
}


def _aplicar_mapping(linha_origem: dict, mapping: dict) -> dict:
    return {alvo: linha_origem[origem] for origem, alvo in mapping.items() if origem in linha_origem}


def test_source_agnostic_mapping_para_canonico_e_agregacao():
    """Uma linha de origem 'estranha' vira modelo canônico e é agregável — sem que o
    nome original da coluna apareça em lugar nenhum do motor."""
    origem = [
        {"codigo_benef": "X1", "data_atend": "2026-01-05", "valor_conta": 1000.0, "valor_glosa": 100.0},
        {"codigo_benef": "X1", "data_atend": "2026-01-20", "valor_conta": 500.0, "valor_glosa": 0.0},
    ]
    canonico = [_aplicar_mapping(linha, _MAPPING_EXEMPLO) for linha in origem]
    assert set(canonico[0]) == {"id_beneficiario", "data_evento", "valor_apresentado", "valor_glosado"}

    bruta = sum(r["valor_apresentado"] for r in canonico)
    glosa = sum(r["valor_glosado"] for r in canonico)
    liquida = bruta - glosa  # sem coparticipação neste exemplo
    assert (bruta, glosa, liquida) == (1500.0, 100.0, 1400.0)

    # nenhum SQL analítico referencia nomes de tabela/coluna de fornecedor
    raiz = Path(__file__).resolve().parents[1] / "app" / "analytics"
    proibidos = ("MV_", "TASY", "BENNER", "cd_atendimento", "vl_conta", "codigo_benef", "valor_conta")
    for py in raiz.rglob("*.py"):
        conteudo = py.read_text(encoding="utf-8")
        for termo in proibidos:
            assert termo not in conteudo, f"{py.name} referencia termo de origem: {termo}"
