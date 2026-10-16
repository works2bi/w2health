# Framework de ingestão — W2Health (Fase 2)

Como um pacote de dados de um cliente entra no W2Health. Implementado para fontes **FILE**
(CSV). Código: `backend/app/data_platform/{runner,connections,storage}.py`.

## 1. Fonte de dados (`source_connections`)

| Campo | Conteúdo |
|---|---|
| `tenant_id` | dono da fonte (RLS) |
| `name` | rótulo único no tenant |
| `source_type` | `FILE` · `DATABASE` · `API` · `SYNTHETIC` |
| `source_system` | rótulo livre da origem (ex.: `generic_csv`) — **não** é nome de produto real |
| `status` | `ACTIVE` · `DISABLED` (desativada não recebe carga) |
| `configuration` | só chaves conhecidas e não sensíveis por tipo (FILE: `mapping_id`, `mapping_version`, `description`; DATABASE: `engine`, `host`, `port`, `database`, `schema`; API: `base_url`). Chave desconhecida ou com cara de credencial (`pass`, `secret`, `token`, `key`…) é recusada |
| `secret_reference` | `tenant:<chave>` (cofre do tenant, Fernet) ou `env:<NOME>` (só DEV). O valor nunca é exibido nem logado; a API devolve só `has_secret` |
| `last_run_at` / `last_success_at` / `last_error_at` / `last_error_summary` | estado operacional |

Cadastro: Admin → Integrações (SUPER_ADMIN) ou `cli register-source`. "Validar" checa,
para FILE, se o mapping referenciado existe e é válido e se o RAW está disponível; para
DATABASE/API responde honestamente "ainda não implementado nesta fase".

## 2. Execução (`ingestion_runs`)

| Campo | Significado |
|---|---|
| `status` | `PENDING` · `RUNNING` · `SUCCESS` · `PARTIAL` · `FAILED` |
| `stage` | último estágio **alcançado**: `RECEIVED` · `VALIDATED` · `PROCESSED` · `RECONCILED` · `AVAILABLE` |
| `records_received / valid / rejected` | por pacote |
| `warnings_count / errors_count` | achados de DQ |
| `competencia_inicio / fim` | janela de negócio do pacote |
| `checksum` | SHA-256 do pacote (nomes normalizados + bytes de cada arquivo, ordenados) |
| `mapping_ref` | `generic_operator@v1` |
| `duplicate_of` | ingestão anterior idêntica (se for reenvio) |
| `correlation_id` | correlação com logs |
| `error_summary` | motivo da falha, por passo — sem conteúdo de linha |

Na interface os estágios aparecem como **Recebido → Validado → Processado → Reconciliado →
Disponível**. Um arquivo que só chegou mostra "Recebido", nunca "sucesso".

## 3. Validação de arquivos (antes de qualquer processamento)

- nome `<entidade>.csv` (`^[a-z][a-z0-9_]{1,60}\.csv$`), sem diretórios (path traversal recusado);
- só `.csv`; até `UPLOAD_MAX_FILES` (20) arquivos e `UPLOAD_MAX_FILE_MB` (50 MB) cada;
- encoding do mapping (UTF-8, BOM tolerado), delimitador do mapping, linhas com o mesmo
  número de colunas do cabeçalho, cabeçalho sem duplicata;
- arquivo vazio, binário (byte nulo), repetido no pacote ou de entidade desconhecida pelo mapping é recusado;
- o conteúdo nunca é executado nem interpretado como fórmula/código.

## 4. Idempotência e reprocessamento

| Situação | Comportamento |
|---|---|
| Reenvio do **mesmo** pacote | checksum igual a uma ingestão `AVAILABLE` → nova ingestão `SUCCESS` com `duplicate_of`; nada é regravado |
| Dimensões (especialidade, plano, contrato, prestador, procedimento, beneficiário) | UPSERT por `(tenant_id, codigo)`; ausência no arquivo **não** apaga (não se infere exclusão) |
| `plano_contrato` (N:N) | UPSERT por `(tenant_id, contrato, plano)`; par ausente no arquivo **não** é removido |
| Fatos (`evento_assistencial`, `receita`) | `COMPETENCIA_SNAPSHOT`: as competências presentes no pacote são substituídas — eventos, apenas os da **mesma** fonte; um registro removido na origem some ao reprocessar a competência |
| Chave repetida dentro do arquivo | WARNING `evento_duplicado`/`chave_duplicada`; mantém a última ocorrência |
| Carga reprovada | nada publicado; o estado anterior do tenant fica intacto |

Testes: `test_mesmo_pacote_nao_duplica`,
`test_reprocesso_de_competencia_corrige_e_remove_sem_duplicar`,
`test_data_quality_bloqueia_promocao`.

## 5. Upload controlado (Admin)

`/admin/integracoes` → escolher tenant → fonte FILE ativa → selecionar os CSVs → "Processar
carga". **Fase 3:** a resposta chega em segundos com `QUEUED` + `job_id` (o RAW já está
gravado); a lista de execuções se atualiza sozinha até o desfecho (status, barra de estágios,
tentativas, duração, recebidos/válidos/rejeitados/erros/avisos, reconciliação). Reenvio
idêntico ou arquivo recusado respondem na hora (200). Limites configuráveis: tamanho e
quantidade de arquivos, colunas (200), bytes por linha (64 KB), linhas (5 milhões), MIME,
binário disfarçado de `.csv`; uploads por usuário/hora (30); "Detalhes" abre passos, DQ, reconciliação e os
objetos RAW. Apenas SUPER_ADMIN (`platform:tenants:manage`); tudo auditado.

## 6. Pacote de exemplo

`data_platform/examples/generic_operator/`:
- `generate_package.py` — gerador standalone (só stdlib), layout **fictício**;
- `pacote/*.csv` — 1.000 beneficiários, 18 competências, 12.093 eventos (versionado com
  `-text` no `.gitattributes` para preservar os bytes e o checksum).

```powershell
# CLI (DEV): mesmo caminho do upload
backend/.venv/Scripts/python -m app.data_platform.cli ingest --tenant vida-plena-csv `
  --source 1 --dir ../data_platform/examples/generic_operator/pacote --by "cli:operador"
```

## 7. Fora do escopo desta fase

Conectores DATABASE/API (extração), CDC/incremental por `source_updated_at`, agendamento,
fila/worker para pacotes grandes, TISS XML.
