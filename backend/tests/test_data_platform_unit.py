"""Fase 2 — testes UNITÁRIOS da Data Platform (sem banco): PipelineContext, mapping
(estrutura, casts, obrigatórios, mapa de valores, normalização), validação de arquivos,
RAW storage (imutabilidade, path traversal), reconciliação e readiness."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.core.tenant import TenantContextMissing
from app.data_platform import readiness
from app.data_platform.context import PipelineContext, PipelineError
from app.data_platform.mapping import MappingError, apply_entity, load_mapping_file, parse_mapping
from app.data_platform.paths import data_platform_dir
from app.data_platform.reconciliation import FAIL, PASS, WARNING, ReconCheck, overall
from app.data_platform.runner import FileValidationError, package_checksum, validate_files
from app.data_platform.storage import LocalFilesystemRawStorage, RawStorageError, raw_key

GENERIC = Path(data_platform_dir()) / "mappings" / "generic_operator_v1.yaml"


def _m(entities, **top):
    return {"mapping_id": "teste_map", "version": 1, "source_format": {"delimiter": ";"},
            "entities": entities, **top}


# ============================================================================ PipelineContext
def test_pipeline_sem_tenant_falha():
    for t in (None, "", "TENANT INVALIDO", "x"):
        with pytest.raises(TenantContextMissing):
            PipelineContext(tenant_id=t, source_connection_id=1, triggered_by="teste")


def test_pipeline_sem_responsavel_falha():
    with pytest.raises(PipelineError):
        PipelineContext(tenant_id="tenant-a", source_connection_id=1, triggered_by="")


def test_pipeline_context_carrega_ids_de_correlacao():
    ctx = PipelineContext(tenant_id="tenant-a", source_connection_id=3, triggered_by="op")
    ctx2 = ctx.with_runs(pipeline_run_id=7, ingestion_run_id=9)
    assert ctx2.log_fields() == {"tenant_id": "tenant-a", "source_connection_id": 3,
                                 "pipeline_run_id": 7, "ingestion_run_id": 9,
                                 "correlation_id": ctx.correlation_id, "job_id": None}
    # Fase 3: o job entra no contexto de log do worker
    assert ctx2.with_runs(job_id=5).log_fields()["job_id"] == 5


# ============================================================================ mapping
def test_mapping_generico_do_repositorio_e_valido():
    m = load_mapping_file(GENERIC)
    assert m.ref == "generic_operator@v1"
    assert {e.target_entity for e in m.entities} == {
        "especialidade", "plano", "contrato", "plano_contrato", "prestador", "procedimento",
        "beneficiario", "receita", "evento_assistencial"}
    assert m.max_rejected_ratio == 0.0 and m.financial_tolerance_abs == Decimal("0")


@pytest.mark.parametrize("entidade,erro", [
    ({"source_entity": "x_y", "target_entity": "nao_existe", "fields": {"a": "b"}}, "não existe"),
    ({"source_entity": "planos", "target_entity": "plano", "fields": {"codigo": "c"}}, "obrigatórios"),
    ({"source_entity": "planos", "target_entity": "plano",
      "fields": {"codigo": "c", "nome": "n", "campo_inventado": "x"}}, "inexistente"),
    ({"source_entity": "planos", "target_entity": "plano",
      "fields": {"codigo": "c", "nome": {"source": "n", "type": "int"}}}, "incompatível"),
    ({"source_entity": "planos", "target_entity": "plano",
      "fields": {"codigo": "c", "nome": {"source": "n", "python": "eval()"}}}, "desconhecidas"),
    ({"source_entity": "planos", "target_entity": "plano",
      "fields": {"codigo": "c", "nome": {"source": "n", "normalize": ["exec"]}}}, "normalização"),
    ({"source_entity": "planos", "target_entity": "plano",
      "fields": {"codigo": "c", "nome": {"type": "string"}}}, "sem source exige default"),
    ({"source_entity": "ev", "target_entity": "evento_assistencial", "fields": {
        "source_record_id": "a", "beneficiario_codigo": "b", "prestador_codigo": "c",
        "procedimento_codigo": "d", "data_evento": {"source": "e", "type": "date"},
        "valor_apresentado": {"source": "f", "type": "decimal"},
        "tipo_atendimento": {"source": "g", "map": {"1": "teleconsulta"}}}}, "vocabulário"),
    ({"source_entity": "planos", "target_entity": "plano", "load_strategy": "COMPETENCIA_SNAPSHOT",
      "fields": {"codigo": "c", "nome": "n"}}, "COMPETENCIA_SNAPSHOT"),
])
def test_mapping_invalido_e_recusado(entidade, erro):
    with pytest.raises(MappingError, match=erro):
        parse_mapping(_m([entidade]))


def test_mapping_campo_obrigatorio_nao_pode_ser_marcado_opcional():
    with pytest.raises(MappingError, match="obrigatório no contrato"):
        parse_mapping(_m([{"source_entity": "planos", "target_entity": "plano",
                           "fields": {"codigo": {"source": "c", "required": False}, "nome": "n"}}]))


def _ev_mapping():
    return parse_mapping(_m([{
        "source_entity": "eventos", "target_entity": "evento_assistencial",
        "fields": {
            "source_record_id": {"source": "id", "normalize": ["strip"]},
            "beneficiario_codigo": {"source": "ben", "normalize": ["strip", "upper"]},
            "prestador_codigo": "pre", "procedimento_codigo": "proc",
            "data_evento": {"source": "dt", "type": "date"},
            "competencia": {"source": "dt", "type": "month"},
            "tipo_atendimento": {"source": "tp", "map": {"1": "consulta", "2": "exame"}},
            "valor_apresentado": {"source": "vl", "type": "decimal"},
            "valor_glosado": {"source": "gl", "type": "decimal", "default": 0},
        }}], source_format={"delimiter": ";", "decimal_separator": ",", "date_formats": ["%d/%m/%Y"]}))


def test_mapping_aplica_rename_cast_default_mapa_normalizacao():
    m = _ev_mapping()
    linhas = [{"id": " A1 ", "ben": " ben-000001", "pre": "P1", "proc": "X", "dt": "15/03/2026",
               "tp": "2", "vl": "1.234,56", "gl": ""}]
    res = apply_entity(m, m.entities[0], linhas, list(linhas[0]))
    assert not res.rejects
    v = res.rows[0].values
    assert v["source_record_id"] == "A1" and v["beneficiario_codigo"] == "BEN-000001"
    assert v["data_evento"] == date(2026, 3, 15) and v["competencia"] == date(2026, 3, 1)
    assert v["tipo_atendimento"] == "exame"
    assert v["valor_apresentado"] == Decimal("1234.56") and v["valor_glosado"] == Decimal("0")


@pytest.mark.parametrize("campo,valor,motivo", [
    ("dt", "2026-13-45", "data inválida"),
    ("vl", "abc", "decimal inválido"),
    ("tp", "9", "sem correspondência"),
    ("ben", "", "obrigatório ausente"),
])
def test_linha_invalida_e_rejeitada_com_motivo(campo, valor, motivo):
    m = _ev_mapping()
    linha = {"id": "A1", "ben": "B1", "pre": "P1", "proc": "X", "dt": "15/03/2026", "tp": "1",
             "vl": "10,00", "gl": "0"}
    linha[campo] = valor
    res = apply_entity(m, m.entities[0], [linha], list(linha))
    assert not res.rows and len(res.rejects) == 1
    assert any(motivo in r for r in res.rejects[0].reasons)
    assert res.rejects[0].line == 2 and res.rejects[0].source_record_id == "A1"


def test_coluna_ausente_no_arquivo_rejeita_a_entidade():
    m = _ev_mapping()
    res = apply_entity(m, m.entities[0], [{"id": "1"}], ["id"])
    assert res.rejects and res.rejects[0].line == 0 and "colunas ausentes" in res.rejects[0].reasons[0]


# ============================================================================ arquivos
def _files_ok() -> dict[str, bytes]:
    return {"especialidades.csv": b"cod_especialidade;nome_especialidade;grupo\nESP;Cl\xc3\xadnica;clinica\n"}


def test_validacao_de_arquivo_aceita_csv_correto():
    m = load_mapping_file(GENERIC)
    out = validate_files(m, _files_ok(), max_bytes=10_000, max_files=5)
    cab, linhas, _ = out["especialidades"]
    assert cab == ["cod_especialidade", "nome_especialidade", "grupo"] and len(linhas) == 1


@pytest.mark.parametrize("files,erro", [
    ({}, "nenhum arquivo"),
    ({"especialidades.xlsx": b"x"}, "nome de arquivo inválido"),
    ({"../../etc/passwd.csv": b"a;b\n1;2\n"}, "não pertence ao mapping"),
    ({"tabela_desconhecida.csv": b"a;b\n1;2\n"}, "não pertence ao mapping"),
    ({"especialidades.csv": b""}, "vazio"),
    ({"especialidades.csv": b"a;b\x00\n"}, "binário"),
    ({"especialidades.csv": "a;b\nç;x\n".encode("latin-1")}, "encoding"),
    ({"especialidades.csv": b"a;b\n1;2;3\n"}, "colunas"),
    ({"especialidades.csv": b"a;a\n1;2\n"}, "repetidas"),
    ({"especialidades.csv": b"a;b\n" + b"1;2\n" * 5000}, "limite"),
])
def test_validacao_de_arquivo_recusa_entradas_inseguras(files, erro):
    m = load_mapping_file(GENERIC)
    with pytest.raises(FileValidationError, match=erro):
        validate_files(m, files, max_bytes=10_000, max_files=5)


def test_checksum_do_pacote_e_estavel_e_sensivel_a_conteudo():
    a = {"x.csv": b"1", "y.csv": b"2"}
    assert package_checksum(a) == package_checksum(dict(reversed(list(a.items()))))
    assert package_checksum(a) != package_checksum({"x.csv": b"1", "y.csv": b"3"})


# ============================================================================ RAW storage
def test_raw_storage_e_imutavel_e_por_tenant(tmp_path):
    st = LocalFilesystemRawStorage(tmp_path)
    key = raw_key("tenant-a", 1, "eventos", 10, "eventos.csv")
    assert key == "tenant/tenant-a/raw/1/eventos/10/eventos.csv"
    ref = st.put(key, b"conteudo")
    assert st.get(key) == b"conteudo" and ref.size == 8 and len(ref.sha256) == 64
    with pytest.raises(RawStorageError, match="imutável"):
        st.put(key, b"outro")
    assert st.list("tenant/tenant-a") == [key] and st.list("tenant/tenant-b") == []


@pytest.mark.parametrize("seg", ["..", "a/b", "", "nome com espaço"])
def test_raw_key_recusa_path_traversal(seg):
    with pytest.raises(RawStorageError):
        raw_key("tenant-a", 1, seg, 1, "x.csv")


# ============================================================================ reconciliação / readiness
def test_reconciliacao_pass_warning_fail_com_tolerancia_explicita():
    c = lambda exp, act, tol: ReconCheck("x", "e", "total", Decimal(exp), Decimal(act), Decimal(tol))  # noqa: E731
    assert c("10.00", "10.00", "0").status == PASS
    assert c("10.00", "10.01", "0.05").status == WARNING
    assert c("10.00", "10.01", "0").status == FAIL  # sem tolerância silenciosa
    assert overall([c("1", "1", "0"), c("1", "1.01", "0.5")]) == WARNING
    assert overall([c("1", "1", "0"), c("1", "2", "0")]) == FAIL


def test_readiness_contratada_mas_sem_receita_nao_fica_pronta():
    sinais = {k: True for k in readiness.SIGNAL_LABEL}
    sinais["receita"] = False
    r = readiness.evaluate(sinais)
    assert r["loss_ratio_intelligence"].status == readiness.NOT_READY
    assert "receita" in r["loss_ratio_intelligence"].reason
    assert r["provider_intelligence"].status == readiness.READY
    sinais["receita"], sinais["glosa"] = True, False
    assert readiness.evaluate(sinais)["financial_composition"].status == readiness.PARTIAL
