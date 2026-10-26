"""v1.2 — regras de Data Quality (source-agnostic).

Testam `data_platform/quality/rules.py`: classificação ERROR/WARNING/INFO e a decisão
de bloquear ou não o processamento. Rodam sobre linhas do modelo canônico (dicts) —
não tocam o banco.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_RULES_PATH = Path(__file__).resolve().parents[2] / "data_platform" / "quality" / "rules.py"
_spec = importlib.util.spec_from_file_location("dp_quality_rules", _RULES_PATH)
rules = importlib.util.module_from_spec(_spec)
sys.modules["dp_quality_rules"] = rules
_spec.loader.exec_module(rules)


def _ctx():
    return rules.QualityContext(
        beneficiarios_ids={1, 2, 3}, contratos_ids={10, 11}, prestadores_ids={100, 101},
        planos_ids={5},
    )


def test_evento_valido_nao_gera_violacao():
    linha = {
        "tenant_id": "w2h-demo", "id_beneficiario": 1, "id_contrato": 10, "id_prestador": 100,
        "data_evento": "2026-03-04", "competencia": "2026-03-01",
        "valor_apresentado": 1000.0, "valor_glosado": 100.0, "valor_pago": 900.0,
        "valor_coparticipacao": 90.0, "despesa_liquida": 810.0,
        "source_system": "sistema_x", "source_record_id": "A1",
    }
    v = rules.avaliar("evento_assistencial", [linha], _ctx())
    assert v == []
    assert rules.bloqueia_processamento(v) is False


def test_erros_bloqueiam_processamento():
    linhas = [
        {"tenant_id": "w2h-demo", "id_beneficiario": 999, "id_prestador": 100,  # benef inexistente
         "data_evento": "2026-03-04", "competencia": "2026-03-01",
         "valor_apresentado": 100.0, "valor_glosado": 10.0, "valor_pago": 90.0},
        {"tenant_id": "w2h-demo", "id_beneficiario": 1, "id_prestador": 100,
         "data_evento": None, "competencia": "2026-03-10",  # sem data + competência não é dia 1
         "valor_apresentado": -5.0, "valor_glosado": 0.0, "valor_pago": -5.0},  # valor negativo
        {"tenant_id": "w2h-demo", "id_beneficiario": 2, "id_prestador": 100,
         "data_evento": "2026-03-04", "competencia": "2026-03-01",
         "valor_apresentado": 100.0, "valor_glosado": 250.0, "valor_pago": -150.0},  # glosa > apres
    ]
    v = rules.avaliar("evento_assistencial", linhas, _ctx())
    ids = {x.rule_id for x in v}
    assert {"benef_inexistente_em_evento", "evento_sem_data", "competencia_invalida",
            "valor_negativo", "glosa_maior_que_apresentado"} <= ids
    assert all(x.severity in (rules.ERROR, rules.WARNING, rules.INFO) for x in v)
    assert rules.bloqueia_processamento(v) is True


def test_warnings_nao_bloqueiam():
    linha = {
        "tenant_id": "w2h-demo", "id_beneficiario": 1, "id_prestador": 100, "id_contrato": 10,
        "data_evento": "2026-03-04", "competencia": "2026-03-01",
        "valor_apresentado": 1000.0, "valor_glosado": 100.0, "valor_pago": 900.0,
        "valor_coparticipacao": 950.0,          # > valor_pago  -> WARNING
        "despesa_liquida": 123.0,               # inconsistente  -> WARNING
        "source_system": "s", "source_record_id": "DUP",
    }
    v = rules.avaliar("evento_assistencial", [linha, dict(linha)], _ctx())  # 2x -> duplicado
    sev = {x.rule_id: x.severity for x in v}
    assert sev.get("coparticipacao_acima_permitido") == rules.WARNING
    assert sev.get("despesa_liquida_inconsistente") == rules.WARNING
    assert sev.get("evento_duplicado") == rules.WARNING
    assert rules.bloqueia_processamento(v) is False


def test_beneficiario_data_saida_antes_adesao_e_erro():
    linha = {"tenant_id": "w2h-demo", "id_contrato": 10,
             "data_adesao": "2025-06-01", "data_saida": "2025-01-01"}
    v = rules.avaliar("beneficiario", [linha], _ctx())
    assert any(x.rule_id == "data_saida_antes_adesao" and x.severity == rules.ERROR for x in v)


def test_receita_duplicada_e_warning():
    linha = {"tenant_id": "w2h-demo", "competencia": "2026-03-01", "id_plano": 5,
             "receita_contraprestacao": 1000.0}
    v = rules.avaliar("receita", [linha, dict(linha)], _ctx())
    assert any(x.rule_id == "receita_duplicada" and x.severity == rules.WARNING for x in v)
    assert rules.bloqueia_processamento(v) is False


# ------------------------------------------------------------------ Evolução V1 — vigências
def _preco(ini, fim=None, valor=100.0, prest="PR-1", proc="P-1"):
    return {"tenant_id": "t", "prestador_codigo": prest, "procedimento_codigo": proc,
            "vigencia_inicio": ini, "vigencia_fim": fim, "valor_unitario": valor, "_line": 2}


def test_vigencias_encadeadas_sao_validas():
    v = rules.avaliar("preco_contratado", [_preco("2026-01-01", "2026-04-01"),
                                           _preco("2026-04-01", None, 110.0)])
    assert v == []


def test_vigencia_sobreposta_e_invertida_bloqueiam():
    v = rules.avaliar("preco_contratado", [_preco("2026-01-01", None), _preco("2026-03-01", None)])
    assert {x.rule_id for x in v} == {"vigencia_sobreposta"} and rules.bloqueia_processamento(v)
    v = rules.avaliar("preco_contratado", [_preco("2026-05-01", "2026-04-01")])
    assert {x.rule_id for x in v} == {"vigencia_invertida"}
    # chaves diferentes não se sobrepõem
    assert rules.avaliar("preco_contratado", [_preco("2026-01-01"), _preco("2026-01-01", proc="P-2")]) == []


def test_preco_nao_positivo_bloqueia():
    v = rules.avaliar("preco_contratado", [_preco("2026-01-01", valor=0)])
    assert {x.rule_id for x in v} == {"preco_nao_positivo"} and rules.bloqueia_processamento(v)


def test_vinculo_sobreposto_bloqueia():
    lin = [{"tenant_id": "t", "beneficiario_codigo": "B1", "vigencia_inicio": "2025-01-01",
            "vigencia_fim": "2026-01-01"},
           {"tenant_id": "t", "beneficiario_codigo": "B1", "vigencia_inicio": "2025-06-01",
            "vigencia_fim": None}]
    assert {x.rule_id for x in rules.avaliar("vinculo_beneficiario", lin)} == {"vigencia_sobreposta"}


# ------------------------------------------------------------------ Consolidação V1 — receita
def test_fatura_unidade_de_cobranca_e_total():
    ok = {"tenant_id": "t", "unidade_cobranca": "empresa", "empresa_codigo": "E", "valor_bruto": 100,
          "valor_desconto": 10, "valor_acrescimo": 5, "valor_total": 95}
    assert rules.avaliar("fatura", [ok]) == []
    ambos = {**ok, "contrato_codigo": "C"}
    assert {v.rule_id for v in rules.avaliar("fatura", [ambos])} == {"unidade_cobranca_inconsistente"}
    total = {**ok, "valor_total": 100}
    assert {v.rule_id for v in rules.avaliar("fatura", [total])} == {"fatura_total_inconsistente"}


def test_item_e_receita_negativos_so_em_ajuste_ou_desconto():
    assert rules.avaliar("fatura_item", [{"valor": -10, "natureza": "desconto"}]) == []
    assert {v.rule_id for v in rules.avaliar("fatura_item", [{"valor": -10, "natureza": "mensalidade"}])} == {"valor_negativo"}
    base = {"competencia": "2026-03-01", "receita_contraprestacao": -5}
    assert rules.avaliar("receita_reconhecida", [{**base, "natureza": "ajuste"}]) == []
    assert {v.rule_id for v in rules.avaliar("receita_reconhecida", [{**base, "natureza": "contraprestacao"}])} == {"valor_negativo"}


def test_receita_com_contrato_e_empresa_ao_mesmo_tempo_e_ambigua():
    r = {"competencia": "2026-03-01", "receita_contraprestacao": 1, "contrato_codigo": "C", "empresa_codigo": "E"}
    assert {v.rule_id for v in rules.avaliar("receita_reconhecida", [r])} == {"receita_nivel_ambiguo"}


def test_estorno_admite_valor_negativo_no_evento():
    ev = {"tenant_id": "t", "data_evento": "2026-03-04", "competencia": "2026-03-01",
          "valor_apresentado": -100.0, "valor_glosado": 0, "valor_pago": -100.0, "valor_coparticipacao": 0}
    assert {v.rule_id for v in rules.avaliar("evento_assistencial", [ev])} == {"valor_negativo"}
    assert rules.avaliar("evento_assistencial", [{**ev, "natureza_lancamento": "estorno"}]) == []


def test_empresa_contrato_uma_contratante_por_vez():
    linhas = [{"tenant_id": "t", "contrato_codigo": "C1", "papel": "contratante",
               "vigencia_inicio": "2025-01-01", "vigencia_fim": None},
              {"tenant_id": "t", "contrato_codigo": "C1", "papel": "contratante",
               "vigencia_inicio": "2025-06-01", "vigencia_fim": None},
              {"tenant_id": "t", "contrato_codigo": "C1", "papel": "administradora",
               "vigencia_inicio": "2025-06-01", "vigencia_fim": None}]
    assert {v.rule_id for v in rules.avaliar("empresa_contrato", linhas)} == {"vigencia_sobreposta"}
