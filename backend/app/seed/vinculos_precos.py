"""Seed — vínculo da competência nos eventos e tabelas de preço contratadas (Evolução V1).

* `vincular_eventos`: grava em cada evento o contrato/plano do beneficiário quando o
  atendimento cai dentro do vínculo do cadastro (adesão ≤ data < saída) — a mesma regra do
  Silver; fora dele o evento fica não atribuído (na massa Vida Plena não há nenhum caso);
* `gerar_precos_contratados`: tabela de preço VIGENTE desde o início da janela para os
  pares prestador × procedimento de hospitais e laboratórios, no mesmo valor que o
  gerador usa para precificar (`custo_base × nivel_preco` por unidade). Nenhum evento é
  alterado. Como a tabela não muda, um aumento de custo nesses pares (ex.: cenário `s7`,
  ressonância +30%) é, corretamente, "aumento de custo unitário SEM evidência de reajuste".
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import text
from sqlalchemy.orm import Session

TIPOS_COM_TABELA = ("hospital", "laboratorio")


def vincular_eventos(session: Session, tenant_id: str) -> int:
    res = session.execute(
        text(
            """
            UPDATE eventos_assistenciais e
               SET id_contrato = b.id_contrato, id_plano = b.id_plano
              FROM beneficiarios b
             WHERE b.id = e.id_beneficiario AND e.tenant_id = :t AND e.id_contrato IS NULL
               -- mesma regra do Silver (Consolidação V1): só dentro do vínculo do cadastro
               AND b.data_adesao <= e.data_evento
               AND (b.data_saida IS NULL OR e.data_evento < b.data_saida)
            """
        ),
        {"t": tenant_id},
    )
    return res.rowcount or 0


def gerar_precos_contratados(session: Session, tenant_id: str, inicio: date) -> int:
    lin = session.info.get("w2h_lineage") or {}
    res = session.execute(
        text(
            """
            INSERT INTO precos_contratados
                (tenant_id, id_prestador, id_procedimento, vigencia_inicio, vigencia_fim,
                 valor_unitario, tipo_remuneracao, source_system, source_connection_id,
                 ingestion_run_id, source_record_id)
            SELECT :t, p.id, pr.id, :ini, NULL, ROUND(pr.custo_base * p.nivel_preco, 2), 'tabela',
                   :ss, :sc, :ir,
                   COALESCE(p.codigo, p.id::text) || '/' || COALESCE(pr.codigo, pr.id::text)
                       || '/' || :ini_txt
              FROM (SELECT DISTINCT id_prestador, id_procedimento
                      FROM eventos_assistenciais WHERE tenant_id = :t) par
              JOIN prestadores p ON p.id = par.id_prestador
              JOIN procedimentos pr ON pr.id = par.id_procedimento
             WHERE p.tipo_prestador = ANY(:tipos)
               AND pr.custo_base IS NOT NULL AND p.nivel_preco IS NOT NULL
            """
        ),
        {"t": tenant_id, "ini": inicio, "ini_txt": inicio.isoformat(),
         "tipos": list(TIPOS_COM_TABELA), "ss": lin.get("source_system"),
         "sc": lin.get("source_connection_id"), "ir": lin.get("ingestion_run_id")},
    )
    return res.rowcount or 0
