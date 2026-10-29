"""Seed — competência de APRESENTAÇÃO das contas (Evolução do produto, item 1).

Na vida real a conta de um atendimento chega à operadora semanas ou meses depois — por
isso a competência mais recente é sempre PRELIMINAR. O gerador sintético cria o evento já
"completo"; aqui só se carimba `competencia_apresentacao` com uma defasagem realista por
tipo de atendimento (internação demora mais que consulta).

* determinístico e SEM consumir o RNG do gerador (hash do id do evento);
* a data de corte da carga é o fim da janela (`fim`); a apresentação nunca passa dele;
* com `remover_pendentes` (seed `--carga-parcial`, padrão da demo): na ÚLTIMA competência as
  contas cuja apresentação cairia depois do corte são REMOVIDAS — como numa carga real, elas
  ainda não chegaram. A última competência parece "boa demais" e o motor de completude a
  mostra preliminar, com a estimativa do que falta. Altera dez/2026 (cenário S13);
* sem a opção (suíte de testes) nada é removido: a apresentação só é limitada ao corte.
  Efeito conhecido: a massa já é completa, então a completude estimada dos últimos meses
  fica abaixo de 100% e a projeção superestima — serve para testar o motor, não para demo.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import text
from sqlalchemy.orm import Session

# percentis acumulados (0–100) do hash para defasagem 0, 1, 2, 3 meses
_DEFASAGEM = {
    "internacao": (25, 70, 92, 100),
    "pronto_socorro": (55, 88, 97, 100),
    "terapia": (60, 90, 98, 100),
    "_padrao": (70, 93, 98, 100),
}


def _caso_sql() -> str:
    def faixas(c: tuple[int, ...]) -> str:
        return (f"CASE WHEN h < {c[0]} THEN 0 WHEN h < {c[1]} THEN 1 "
                f"WHEN h < {c[2]} THEN 2 ELSE 3 END")

    ramos = " ".join(
        f"WHEN tipo_atendimento = '{tipo}' THEN {faixas(c)}"
        for tipo, c in _DEFASAGEM.items() if tipo != "_padrao"
    )
    return f"CASE {ramos} ELSE {faixas(_DEFASAGEM['_padrao'])} END"


def marcar_apresentacao(session: Session, tenant_id: str, fim: date,
                        remover_pendentes: bool = False) -> tuple[int, int]:
    """(eventos carimbados, eventos da última competência ainda não apresentados no corte)."""
    p = {"t": tenant_id, "fim": fim}
    res = session.execute(
        text(
            f"""
            UPDATE eventos_assistenciais e
               SET competencia_apresentacao = (e.competencia + make_interval(months => d.lag))::date
              FROM (SELECT id, {_caso_sql()} AS lag
                      FROM (SELECT id, tipo_atendimento, abs(hashtext(id::text)) % 100 AS h
                              FROM eventos_assistenciais
                             WHERE tenant_id = :t AND competencia_apresentacao IS NULL) x) d
             WHERE e.id = d.id
            """
        ),
        p,
    )
    removidos = 0
    if remover_pendentes:
        removidos = session.execute(
            text("DELETE FROM eventos_assistenciais WHERE tenant_id = :t AND competencia = :fim "
                 "AND competencia_apresentacao > :fim"), p,
        ).rowcount or 0
    session.execute(
        text("UPDATE eventos_assistenciais SET competencia_apresentacao = :fim "
             "WHERE tenant_id = :t AND competencia_apresentacao > :fim"), p,
    )
    return (res.rowcount or 0) - removidos, removidos
