# Regras de Data Quality (catálogo legível)

Implementação executável: [`rules.py`](rules.py). Testes:
`backend/tests/test_data_quality.py`. Detalhe conceitual: `docs/DATA_QUALITY.md`.

| rule_id | Entidade | Severidade | Dispara quando | Efeito |
|---|---|---|---|---|
| `tenant_id_ausente` | todas | **ERROR** | linha canônica sem `tenant_id` | bloqueia a entidade |
| `benef_inexistente_em_evento` | evento_assistencial | **ERROR** | `id_beneficiario` do evento não existe em `silver.beneficiario` | bloqueia |
| `contrato_inexistente` | evento_assistencial, beneficiario | **ERROR** | `id_contrato` referenciado não existe | bloqueia |
| `plano_fora_do_contrato` | beneficiario | **ERROR** | par (`id_contrato`, `id_plano`) do beneficiário não existe em `plano_contrato` — o plano não é oferecido pelo contrato | bloqueia |
| `prestador_inexistente` | evento_assistencial | **ERROR** | `id_prestador` não existe | bloqueia |
| `valor_negativo` | evento_assistencial, receita | **ERROR** | `valor_apresentado < 0` ou `valor_pago < 0` ou receita `< 0` | bloqueia |
| `competencia_invalida` | evento_assistencial, receita | **ERROR** | competência não é o 1º dia de um mês válido | bloqueia |
| `evento_sem_data` | evento_assistencial | **ERROR** | `data_evento` ausente | bloqueia |
| `glosa_maior_que_apresentado` | evento_assistencial | **ERROR** | `valor_glosado > valor_apresentado` | bloqueia |
| `data_saida_antes_adesao` | beneficiario | **ERROR** | `data_saida < data_adesao` | bloqueia |
| `evento_duplicado` | evento_assistencial | WARNING | mesma `(source_system, source_record_id)` mais de uma vez no lote | segue; registra |
| `receita_duplicada` | receita | WARNING | mesma `(competencia, id_plano)` mais de uma vez | segue; registra |
| `despesa_liquida_inconsistente` | evento_assistencial | WARNING | `\|despesa_liquida − (bruta − glosa − copart)\| > 0,01` | segue; recalcula na Gold |
| `coparticipacao_acima_permitido` | evento_assistencial | WARNING | `valor_coparticipacao > valor_pago` | segue; registra |

**Decisão de processamento** (`rules.bloqueia_processamento`): qualquer violação `ERROR`
impede a entidade de avançar para Silver/Gold. `WARNING` grava em
`data_quality_results` e o pipeline continua. `INFO` é apenas anotação.
