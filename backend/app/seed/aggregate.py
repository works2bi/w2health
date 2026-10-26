"""Job de (re)construção da camada analítica a partir da fato bruta.

Roda inteiramente no PostgreSQL (INSERT ... SELECT ... GROUP BY) — rápido mesmo com
centenas de milhares de eventos. Chamado ao final do seed e por `python -m app.seed.aggregate`
quando os dados brutos mudam.

v1.2:
  * é **tenant-scoped** — reconstrói só o tenant informado (`tenant_id`, default `w2h-demo`);
  * `agg_competencia_dimensao`, `agg_prestador_competencia` e `agg_beneficiario_competencia`
    ganham `despesa_bruta / glosas / coparticipacao / despesa_liquida` (aditivo à `despesa`,
    que segue sendo `Σ valor_pago`);
  * nova `agg_contrato_competencia` — base de Contract Intelligence, **sem receita/
    sinistralidade próprias**.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.tenant import DEFAULT_TENANT
from app.core.thresholds import ALTO_CUSTO_MES  # limiar (R$/mês) de "beneficiário de alto custo"

# dimensao -> (join extra, expressão da chave, expressão do rótulo)
_DIMENSOES: dict[str, tuple[str, str, str]] = {
    "grupo_despesa": (
        "JOIN procedimentos pr ON pr.id = e.id_procedimento",
        "pr.grupo_procedimento",
        "pr.grupo_procedimento",
    ),
    "tipo_atendimento": ("", "e.tipo_atendimento", "e.tipo_atendimento"),
    "especialidade": (
        "JOIN especialidades es ON es.id = e.id_especialidade",
        "es.id::text",
        "es.nome",
    ),
    "procedimento": (
        "JOIN procedimentos pr ON pr.id = e.id_procedimento",
        "pr.id::text",
        "pr.descricao",
    ),
    "prestador": (
        "JOIN prestadores p ON p.id = e.id_prestador",
        "p.id::text",
        "p.nome_ficticio",
    ),
    "regiao": (
        "JOIN regioes r ON r.id = e.id_regiao",
        "r.id::text",
        "concat(r.cidade, '/', r.uf)",
    ),
    "faixa_etaria": (
        "JOIN beneficiarios b ON b.id = e.id_beneficiario",
        "b.faixa_etaria",
        "b.faixa_etaria",
    ),
    "sexo": (
        "JOIN beneficiarios b ON b.id = e.id_beneficiario",
        "b.sexo",
        "b.sexo",
    ),
    # plano/contrato: SÓ o vínculo da competência gravado no evento (Consolidação V1).
    # Evento sem vínculo vigente vai para a chave `nao_atribuido` — nunca para o contrato
    # atual do beneficiário. (Registros anteriores foram preenchidos pela migration d0e1f2a3b4c5.)
    "plano": (
        "LEFT JOIN planos pl ON pl.id = e.id_plano",
        "COALESCE(pl.id::text, 'nao_atribuido')",
        "COALESCE(pl.nome, 'Não atribuído')",
    ),
    "contrato": (
        "LEFT JOIN contratos ct ON ct.id = e.id_contrato",
        "COALESCE(ct.id::text, 'nao_atribuido')",
        "COALESCE(ct.nome, 'Não atribuído')",
    ),
}

#: janela de dados do PRÓPRIO tenant (o calendário `competencias` é global)
_SQL_JANELA = """c.competencia BETWEEN
      (SELECT min(m) FROM (SELECT min(competencia) AS m FROM eventos_assistenciais WHERE tenant_id = :tenant
                           UNION ALL SELECT min(competencia) FROM receitas WHERE tenant_id = :tenant
                           UNION ALL SELECT min(competencia) FROM receitas_reconhecidas
                                     WHERE tenant_id = :tenant) j)
  AND (SELECT max(m) FROM (SELECT max(competencia) AS m FROM eventos_assistenciais WHERE tenant_id = :tenant
                           UNION ALL SELECT max(competencia) FROM receitas WHERE tenant_id = :tenant
                           UNION ALL SELECT max(competencia) FROM receitas_reconhecidas
                                     WHERE tenant_id = :tenant) j)"""

#: vidas por competência × contrato pelo VÍNCULO VIGENTE (Consolidação V1): beneficiário com
#: histórico conta pelo vínculo que contém o 1º dia da competência (situação vazia/ativo);
#: sem histórico, pelo cadastro (adesão ≤ competência < saída). Quem não tem vínculo vigente
#: não é exposto em contrato algum — nunca pelo contrato atual.
_SQL_VIDAS_CTE = f"""
hist AS (SELECT DISTINCT id_beneficiario FROM beneficiario_vinculos WHERE tenant_id = :tenant),
expo AS (
    SELECT c.competencia, v.id_contrato, COUNT(*) AS vidas
    FROM competencias c
    JOIN beneficiario_vinculos v
      ON v.tenant_id = :tenant AND v.vigencia_inicio <= c.competencia
     AND (v.vigencia_fim IS NULL OR v.vigencia_fim > c.competencia)
     AND COALESCE(v.situacao, 'ativo') = 'ativo'
    WHERE {_SQL_JANELA}
    GROUP BY c.competencia, v.id_contrato
    UNION ALL
    SELECT c.competencia, b.id_contrato, COUNT(*) AS vidas
    FROM competencias c
    JOIN beneficiarios b
      ON b.tenant_id = :tenant AND b.data_adesao <= c.competencia
     AND (b.data_saida IS NULL OR b.data_saida > c.competencia)
    WHERE {_SQL_JANELA}
      AND NOT EXISTS (SELECT 1 FROM hist h WHERE h.id_beneficiario = b.id)
    GROUP BY c.competencia, b.id_contrato
),
vidas_ctr AS (SELECT competencia, id_contrato, SUM(vidas) AS vidas FROM expo
              GROUP BY competencia, id_contrato)"""


# expressões de composição financeira reaproveitadas por várias INSERTs
_COMPOSICAO = """
    SUM(e.valor_pago)                                              AS despesa,
    SUM(e.valor_apresentado)                                       AS despesa_bruta,
    SUM(e.valor_glosado)                                           AS glosas,
    SUM(e.valor_coparticipacao)                                    AS coparticipacao,
    SUM(e.valor_apresentado - e.valor_glosado - e.valor_coparticipacao) AS despesa_liquida
"""


def rebuild_aggregations(session: Session, tenant_id: str = DEFAULT_TENANT) -> dict[str, int]:
    """Reconstrói todas as tabelas `agg_*` para um tenant. Retorna contagens por tabela."""
    p = {"tenant": tenant_id}
    for tbl in (
        "agg_sinistralidade_competencia", "agg_competencia_dimensao",
        "agg_prestador_competencia", "agg_beneficiario_competencia",
        "agg_contrato_competencia",
    ):
        session.execute(text(f"DELETE FROM {tbl} WHERE tenant_id = :tenant"), p)

    # ---- sinistralidade por competência ----
    # Consolidação V1: UMA fonte de receita por competência — reconhecida (se houver linha
    # não estimada) senão legada por plano; NUNCA as duas somadas. As duas presentes e
    # diferentes acima de R$ 0,01 → `receita_status = 'divergente'` (a API não publica a
    # sinistralidade dessa competência; o pipeline reprova a carga).
    session.execute(
        text(
            f"""
            INSERT INTO agg_sinistralidade_competencia
                (tenant_id, competencia, receita, despesa_bruta, glosas, coparticipacao,
                 despesa_liquida, sinistralidade_bruta, sinistralidade_liquida,
                 beneficiarios_ativos, exposicao_beneficiario_mes, eventos, custo_pmpm,
                 receita_media_beneficiario, fonte_receita, receita_status, receita_legada,
                 receita_atribuida_contrato, receita_empresa_nao_detalhada,
                 receita_nao_atribuida, cobertura_receita_contratual)
            WITH {_SQL_VIDAS_CTE},
            vidas_total AS (SELECT competencia, SUM(vidas) AS vidas FROM vidas_ctr GROUP BY competencia),
            rl AS (
                SELECT competencia, SUM(receita_contraprestacao) AS receita,
                       SUM(quantidade_beneficiarios) AS expo
                FROM receitas WHERE tenant_id = :tenant GROUP BY competencia
            ),
            rr AS (
                SELECT competencia, SUM(receita_contraprestacao) AS receita,
                       SUM(quantidade_beneficiarios) AS expo,
                       SUM(receita_contraprestacao) FILTER (
                           WHERE nivel_atribuicao IN ('contrato_plano', 'contrato')) AS atrib,
                       SUM(receita_contraprestacao) FILTER (WHERE nivel_atribuicao = 'empresa') AS emp,
                       SUM(receita_contraprestacao) FILTER (WHERE nivel_atribuicao = 'nao_atribuida') AS na
                FROM receitas_reconhecidas
                WHERE tenant_id = :tenant AND NOT estimada GROUP BY competencia
            ),
            rc AS (
                SELECT c.competencia,
                       CASE WHEN rr.competencia IS NOT NULL THEN rr.receita ELSE rl.receita END AS receita,
                       CASE WHEN rr.competencia IS NOT NULL
                            THEN COALESCE(NULLIF(rr.expo, 0), vt.vidas) ELSE rl.expo END AS expo,
                       CASE WHEN rr.competencia IS NOT NULL THEN 'reconhecida'
                            WHEN rl.competencia IS NOT NULL THEN 'legada_plano'
                            ELSE 'sem_receita' END AS fonte,
                       CASE WHEN rr.competencia IS NOT NULL AND rl.competencia IS NOT NULL
                                 AND abs(rr.receita - rl.receita) > 0.01 THEN 'divergente'
                            ELSE 'ok' END AS status,
                       rl.receita AS legada, rr.atrib, rr.emp, rr.na,
                       CASE WHEN rr.receita > 0 THEN COALESCE(rr.atrib, 0) / rr.receita END AS cobertura
                FROM competencias c
                LEFT JOIN rl ON rl.competencia = c.competencia
                LEFT JOIN rr ON rr.competencia = c.competencia
                LEFT JOIN vidas_total vt ON vt.competencia = c.competencia
            )
            SELECT
                CAST(:tenant AS varchar),
                c.competencia,
                COALESCE(rc.receita, 0)                                   AS receita,
                COALESCE(ev.despesa_bruta, 0)                             AS despesa_bruta,
                COALESCE(ev.glosas, 0)                                    AS glosas,
                COALESCE(ev.coparticipacao, 0)                            AS coparticipacao,
                COALESCE(ev.despesa_liquida, 0)                           AS despesa_liquida,
                CASE WHEN COALESCE(rc.receita,0) > 0
                     THEN COALESCE(ev.despesa_bruta,0) / rc.receita * 100 ELSE 0 END,
                CASE WHEN COALESCE(rc.receita,0) > 0
                     THEN COALESCE(ev.despesa_liquida,0) / rc.receita * 100 ELSE 0 END,
                COALESCE(rc.expo, 0),
                COALESCE(rc.expo, 0),
                COALESCE(ev.eventos, 0),
                CASE WHEN COALESCE(rc.expo,0) > 0
                     THEN COALESCE(ev.despesa_liquida,0) / rc.expo ELSE 0 END,
                CASE WHEN COALESCE(rc.expo,0) > 0
                     THEN COALESCE(rc.receita,0) / rc.expo ELSE 0 END,
                rc.fonte, rc.status, rc.legada,
                COALESCE(rc.atrib, 0), COALESCE(rc.emp, 0), COALESCE(rc.na, 0), rc.cobertura
            FROM competencias c
            LEFT JOIN rc ON rc.competencia = c.competencia
            LEFT JOIN (
                SELECT competencia,
                       SUM(valor_apresentado)                                       AS despesa_bruta,
                       SUM(valor_glosado)                                           AS glosas,
                       SUM(valor_coparticipacao)                                    AS coparticipacao,
                       SUM(valor_apresentado - valor_glosado - valor_coparticipacao) AS despesa_liquida,
                       COUNT(*)                                                     AS eventos
                FROM eventos_assistenciais WHERE tenant_id = :tenant GROUP BY competencia
            ) ev ON ev.competencia = c.competencia
            -- Fase 2: só a janela de dados do PRÓPRIO tenant (o calendário é global)
            WHERE {_SQL_JANELA}
            """
        ),
        p,
    )

    # ---- dimensões (com composição financeira propagada — v1.2) ----
    for dim, (joins, chave_expr, rotulo_expr) in _DIMENSOES.items():
        session.execute(
            text(
                f"""
                INSERT INTO agg_competencia_dimensao
                    (tenant_id, competencia, dimensao, chave, rotulo,
                     despesa, despesa_bruta, glosas, coparticipacao, despesa_liquida,
                     eventos, quantidade, beneficiarios, custo_medio, freq_por_mil)
                SELECT
                    CAST(:tenant AS varchar),
                    e.competencia,
                    :dim AS dimensao,
                    {chave_expr} AS chave,
                    MIN({rotulo_expr}) AS rotulo,
                    {_COMPOSICAO},
                    COUNT(*) AS eventos,
                    SUM(e.quantidade) AS quantidade,
                    COUNT(DISTINCT e.id_beneficiario) AS beneficiarios,
                    CASE WHEN COUNT(*) > 0 THEN SUM(e.valor_pago) / COUNT(*) ELSE 0 END,
                    CASE WHEN s.exposicao_beneficiario_mes > 0
                         THEN COUNT(*)::float / s.exposicao_beneficiario_mes * 1000 ELSE 0 END
                FROM eventos_assistenciais e
                {joins}
                JOIN agg_sinistralidade_competencia s
                     ON s.competencia = e.competencia AND s.tenant_id = :tenant
                WHERE e.tenant_id = :tenant
                GROUP BY e.competencia, {chave_expr}, s.exposicao_beneficiario_mes
                """
            ),
            {"dim": dim, **p},
        )

    # ---- prestador x competência ----
    session.execute(
        text(
            """
            INSERT INTO agg_prestador_competencia
                (tenant_id, competencia, id_prestador,
                 despesa, despesa_bruta, glosas, coparticipacao, despesa_liquida,
                 eventos, beneficiarios, custo_medio, participacao,
                 procedimento_top_id, procedimento_top_share)
            WITH base AS (
                SELECT e.competencia, e.id_prestador,
                       SUM(e.valor_pago) AS despesa,
                       SUM(e.valor_apresentado) AS despesa_bruta,
                       SUM(e.valor_glosado) AS glosas,
                       SUM(e.valor_coparticipacao) AS coparticipacao,
                       SUM(e.valor_apresentado - e.valor_glosado - e.valor_coparticipacao) AS despesa_liquida,
                       COUNT(*) AS eventos,
                       COUNT(DISTINCT e.id_beneficiario) AS beneficiarios
                FROM eventos_assistenciais e
                WHERE e.tenant_id = :tenant
                GROUP BY e.competencia, e.id_prestador
            ),
            topp AS (
                SELECT competencia, id_prestador, id_procedimento, dsp,
                       ROW_NUMBER() OVER (PARTITION BY competencia, id_prestador
                                          ORDER BY dsp DESC) AS rn,
                       SUM(dsp) OVER (PARTITION BY competencia, id_prestador) AS tot
                FROM (
                    SELECT competencia, id_prestador, id_procedimento,
                           SUM(valor_pago) AS dsp
                    FROM eventos_assistenciais
                    WHERE tenant_id = :tenant
                    GROUP BY competencia, id_prestador, id_procedimento
                ) z
            ),
            mes AS (
                SELECT competencia, SUM(valor_pago) AS despesa_mes
                FROM eventos_assistenciais WHERE tenant_id = :tenant GROUP BY competencia
            )
            SELECT CAST(:tenant AS varchar), b.competencia, b.id_prestador, b.despesa, b.despesa_bruta,
                   b.glosas, b.coparticipacao, b.despesa_liquida, b.eventos, b.beneficiarios,
                   CASE WHEN b.eventos > 0 THEN b.despesa / b.eventos ELSE 0 END,
                   CASE WHEN m.despesa_mes > 0 THEN b.despesa / m.despesa_mes ELSE 0 END,
                   tp.id_procedimento,
                   CASE WHEN tp.tot > 0 THEN tp.dsp / tp.tot ELSE 0 END
            FROM base b
            JOIN mes m ON m.competencia = b.competencia
            LEFT JOIN topp tp ON tp.competencia = b.competencia
                             AND tp.id_prestador = b.id_prestador AND tp.rn = 1
            """
        ),
        p,
    )

    # ---- beneficiário x competência (com id_contrato + composição — v1.2) ----
    session.execute(
        text(
            """
            INSERT INTO agg_beneficiario_competencia
                (tenant_id, competencia, id_beneficiario, id_contrato,
                 despesa, despesa_bruta, glosas, coparticipacao, despesa_liquida, eventos)
            SELECT CAST(:tenant AS varchar), e.competencia, e.id_beneficiario,
                   MIN(e.id_contrato),
                   SUM(e.valor_pago),
                   SUM(e.valor_apresentado),
                   SUM(e.valor_glosado),
                   SUM(e.valor_coparticipacao),
                   SUM(e.valor_apresentado - e.valor_glosado - e.valor_coparticipacao),
                   COUNT(*)
            FROM eventos_assistenciais e
            JOIN beneficiarios b ON b.id = e.id_beneficiario
            WHERE e.tenant_id = :tenant
            GROUP BY e.competencia, e.id_beneficiario
            """
        ),
        p,
    )

    # ---- contrato x competência (Contract Intelligence — v1.2; Consolidação V1) ----
    # vidas = exposição pelo VÍNCULO VIGENTE (_SQL_VIDAS_CTE); despesa/eventos direto do
    # evento por `e.id_contrato` (um beneficiário que troca de contrato no mês divide a
    # despesa corretamente); gini/top5/alto custo sobre a despesa líquida por beneficiário
    # DENTRO do contrato; receita = reconhecida ATRIBUÍDA ao contrato (nunca empresa sem
    # detalhamento, nunca estimada); sinistralidade só com receita atribuída, competência
    # sem divergência e SEM receita consolidada não detalhada da empresa contratante do
    # contrato na competência (ela pode conter parte da receita deste contrato) — senão
    # nula + motivo. Nunca rateio.
    session.execute(
        text(
            f"""
            INSERT INTO agg_contrato_competencia
                (tenant_id, competencia, id_contrato, vidas,
                 despesa, despesa_bruta, glosas, coparticipacao, despesa_liquida,
                 eventos, beneficiarios_com_evento, custo_pmpm, gini, top5_share,
                 n_beneficiarios_alto_custo, receita_reconhecida, sinistralidade_liquida,
                 motivo_sem_sinistralidade)
            WITH {_SQL_VIDAS_CTE},
            ben AS (
                SELECT e.competencia, e.id_contrato, e.id_beneficiario,
                       SUM(e.valor_pago) AS despesa, SUM(e.valor_apresentado) AS despesa_bruta,
                       SUM(e.valor_glosado) AS glosas, SUM(e.valor_coparticipacao) AS coparticipacao,
                       SUM(e.valor_apresentado - e.valor_glosado - e.valor_coparticipacao) AS despesa_liquida,
                       COUNT(*) AS eventos
                FROM eventos_assistenciais e
                WHERE e.tenant_id = :tenant AND e.id_contrato IS NOT NULL
                GROUP BY e.competencia, e.id_contrato, e.id_beneficiario
            ),
            ben_r AS (
                SELECT ben.*,
                       ROW_NUMBER() OVER (PARTITION BY competencia, id_contrato
                                          ORDER BY despesa_liquida ASC) AS rnk_asc,
                       ROW_NUMBER() OVER (PARTITION BY competencia, id_contrato
                                          ORDER BY despesa_liquida DESC) AS rnk_desc
                FROM ben
            ),
            ctr AS (
                SELECT competencia, id_contrato,
                       SUM(despesa) AS despesa, SUM(despesa_bruta) AS despesa_bruta,
                       SUM(glosas) AS glosas, SUM(coparticipacao) AS coparticipacao,
                       SUM(despesa_liquida) AS despesa_liquida, SUM(eventos) AS eventos,
                       COUNT(*) FILTER (WHERE despesa_liquida > 0) AS n_ben,
                       SUM(rnk_asc * despesa_liquida) FILTER (WHERE despesa_liquida > 0) AS srv,
                       SUM(despesa_liquida) FILTER (WHERE rnk_desc <= 5 AND despesa_liquida > 0) AS top5,
                       COUNT(*) FILTER (WHERE despesa_liquida >= :alto) AS n_alto
                FROM ben_r
                GROUP BY competencia, id_contrato
            ),
            rrc AS (
                SELECT competencia, id_contrato, SUM(receita_contraprestacao) AS receita
                FROM receitas_reconhecidas
                WHERE tenant_id = :tenant AND NOT estimada
                  AND nivel_atribuicao IN ('contrato_plano', 'contrato')
                GROUP BY competencia, id_contrato
            ),
            emp_nd AS (
                SELECT DISTINCT rr.competencia, ec.id_contrato
                FROM receitas_reconhecidas rr
                JOIN empresas_contratos ec
                  ON ec.tenant_id = rr.tenant_id AND ec.id_empresa = rr.id_empresa
                 AND ec.papel = 'contratante' AND ec.vigencia_inicio <= rr.competencia
                 AND (ec.vigencia_fim IS NULL OR ec.vigencia_fim > rr.competencia)
                WHERE rr.tenant_id = :tenant AND NOT rr.estimada AND rr.nivel_atribuicao = 'empresa'
            ),
            chaves AS (
                SELECT competencia, id_contrato FROM vidas_ctr WHERE id_contrato IS NOT NULL
                UNION SELECT competencia, id_contrato FROM ctr
                UNION SELECT competencia, id_contrato FROM rrc
            )
            SELECT CAST(:tenant AS varchar), k.competencia, k.id_contrato, COALESCE(v.vidas, 0),
                   COALESCE(ctr.despesa,0), COALESCE(ctr.despesa_bruta,0),
                   COALESCE(ctr.glosas,0), COALESCE(ctr.coparticipacao,0),
                   COALESCE(ctr.despesa_liquida,0), COALESCE(ctr.eventos,0),
                   COALESCE(ctr.n_ben,0),
                   CASE WHEN COALESCE(v.vidas,0) > 0 THEN COALESCE(ctr.despesa_liquida,0) / v.vidas ELSE 0 END,
                   CASE WHEN COALESCE(ctr.n_ben,0) > 0 AND COALESCE(ctr.despesa_liquida,0) > 0
                        THEN (2.0 * ctr.srv) / (ctr.n_ben * ctr.despesa_liquida)
                             - (ctr.n_ben + 1.0) / ctr.n_ben
                        ELSE 0 END,
                   CASE WHEN COALESCE(ctr.despesa_liquida,0) > 0
                        THEN COALESCE(ctr.top5,0) / ctr.despesa_liquida ELSE 0 END,
                   COALESCE(ctr.n_alto,0),
                   rrc.receita,
                   CASE WHEN rrc.receita > 0 AND s.receita_status = 'ok' AND nd.id_contrato IS NULL
                        THEN COALESCE(ctr.despesa_liquida, 0) / rrc.receita * 100 END,
                   CASE WHEN rrc.receita IS NULL OR rrc.receita <= 0 THEN 'sem_receita_atribuida'
                        WHEN s.receita_status <> 'ok' THEN 'receita_divergente'
                        WHEN nd.id_contrato IS NOT NULL THEN 'receita_empresa_sem_detalhamento'
                   END
            FROM chaves k
            JOIN competencias c ON c.competencia = k.competencia
            LEFT JOIN vidas_ctr v ON v.competencia = k.competencia AND v.id_contrato = k.id_contrato
            LEFT JOIN ctr ON ctr.competencia = k.competencia AND ctr.id_contrato = k.id_contrato
            LEFT JOIN rrc ON rrc.competencia = k.competencia AND rrc.id_contrato = k.id_contrato
            LEFT JOIN agg_sinistralidade_competencia s
                   ON s.tenant_id = :tenant AND s.competencia = k.competencia
            LEFT JOIN emp_nd nd ON nd.competencia = k.competencia AND nd.id_contrato = k.id_contrato
            WHERE {_SQL_JANELA}
            """
        ),
        {"alto": ALTO_CUSTO_MES, **p},
    )
    session.flush()

    return {
        "agg_sinistralidade_competencia": _count(session, tenant_id, "agg_sinistralidade_competencia"),
        "agg_competencia_dimensao": _count(session, tenant_id, "agg_competencia_dimensao"),
        "agg_prestador_competencia": _count(session, tenant_id, "agg_prestador_competencia"),
        "agg_beneficiario_competencia": _count(session, tenant_id, "agg_beneficiario_competencia"),
        "agg_contrato_competencia": _count(session, tenant_id, "agg_contrato_competencia"),
    }


def _count(session: Session, tenant_id: str, table: str) -> int:
    return int(session.execute(
        text(f"SELECT COUNT(*) FROM {table} WHERE tenant_id = :t"), {"t": tenant_id}
    ).scalar_one())


if __name__ == "__main__":  # python -m app.seed.aggregate [--tenant CODIGO]
    import argparse

    from app.db.session import AdminSessionLocal
    from app.db.tenant_scope import bind_tenant

    ap = argparse.ArgumentParser(description="Reconstrói as agg_* de um tenant")
    ap.add_argument("--tenant", default=DEFAULT_TENANT)
    tenant = ap.parse_args().tenant
    with AdminSessionLocal() as s:
        bind_tenant(s, tenant)
        print(rebuild_aggregations(s, tenant_id=tenant))
        s.commit()
