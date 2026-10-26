"""Laboratório de cenários da investigação de custos (Evolução V1).

Tenant sintético próprio (`w2h-cenarios`), com **um contrato por cenário**. Preços fixos
(sem ruído) e contagens controladas na competência de comparação (mai/2026) e na
competência-alvo (jun/2026) — o resultado esperado de cada cenário é conhecido e está no
`cenarios_gabarito`. O histórico (jan/2025–abr/2026) tem variação Poisson em torno da base.

O motor NÃO conhece os cenários: os testes (`tests/test_cenarios_investigacao.py`) só
chamam `investigacao.investigar(..., contrato_id=...)` e comparam com o gabarito.

    python -m app.seed.cenarios_investigacao            # cria/recria o tenant w2h-cenarios

Separado da massa "Vida Plena" de propósito: injetar estes cenários na mesma carteira
mudaria os totais mensais dos quais os 19 cenários existentes dependem.
"""

from __future__ import annotations

import argparse
import random
import zlib
from collections.abc import Callable
from datetime import date

import numpy as np
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.tenant_scope import bind_tenant
from app.models import (
    Beneficiario,
    CenarioGabarito,
    Contrato,
    Especialidade,
    EventoAssistencial,
    Plano,
    PlanoContrato,
    PrecoContratado,
    Prestador,
    Procedimento,
    Receita,
    Regiao,
    SeedManifest,
    Tenant,
)
from app.seed.aggregate import rebuild_aggregations
from app.seed.generator import _bulk_insert, wipe_dados

TENANT = "w2h-cenarios"
TENANT_NOME = "Laboratório de Cenários (sintético)"
INICIO = date(2025, 1, 1)
COMPARACAO = date(2026, 5, 1)
ALVO = date(2026, 6, 1)
GLOSA = 0.02
RECEITA_POR_VIDA = 450.0

# código -> (descrição, especialidade, grupo, tipo de atendimento, perfil, preço/unid., qtd/evento)
PROCS: dict[str, tuple] = {
    "CONS": ("Consulta eletiva", "CLI", "Consultas", "consulta", "variavel", 120.0, 1),
    "EXLAB": ("Exame laboratorial", "DIA", "Exames laboratoriais", "exame", "variavel", 40.0, 1),
    "RM": ("Ressonância magnética", "DIA", "Exames de imagem", "exame", "pontual", 900.0, 1),
    "FISIO": ("Fisioterapia (sessão)", "TER", "Terapias", "terapia", "recorrente", 70.0, 2),
    "INT": (
        "Internação clínica (diária)",
        "CLI",
        "Internações",
        "internacao",
        "pontual",
        1500.0,
        3,
    ),
    "CIR": ("Cirurgia de médio porte", "CIR", "Cirurgias", "cirurgia", "pontual", 6000.0, 1),
    "PS": (
        "Atendimento de pronto-socorro",
        "CLI",
        "Pronto-socorro",
        "pronto_socorro",
        "variavel",
        250.0,
        1,
    ),
}
ESPECS = {
    "CLI": ("Clínica médica", "clinica"),
    "DIA": ("Diagnóstico", "diagnostico"),
    "TER": ("Terapias", "terapia"),
    "CIR": ("Cirurgia geral", "cirurgica"),
}
# código -> (nome, tipo, especialidade principal, nível de preço)
PRESTS: dict[str, tuple] = {
    "H1": ("Hospital Alfa", "hospital", "CLI", 1.0),
    "H2": ("Hospital Beta", "hospital", "CIR", 1.6),
    "H3": ("Hospital Sigma", "hospital", "CLI", 1.0),
    "C1": ("Clínica Gama", "clinica", "CLI", 1.0),
    "C2": ("Clínica Delta", "clinica", "TER", 1.1),
    "L1": ("Laboratório Épsilon", "laboratorio", "DIA", 1.0),
    "L2": ("Laboratório Zeta", "laboratorio", "DIA", 1.2),
    "L3": ("Laboratório Ômicron", "laboratorio", "DIA", 1.0),
    "P1": ("Pronto Atendimento Ômega", "pronto_atendimento", "CLI", 1.0),
}

#: tabela-base de 500 vidas (todas as células múltiplas de 5 → ×1,4 e ×0,6 exatos)
BASE: dict[tuple[str, str], int] = {
    ("CONS", "C1"): 90,
    ("CONS", "C2"): 60,
    ("EXLAB", "L1"): 140,
    ("EXLAB", "L2"): 60,
    ("RM", "L1"): 10,
    ("RM", "L2"): 10,
    ("FISIO", "C1"): 30,
    ("INT", "H1"): 10,
    ("INT", "H2"): 5,
    ("CIR", "H1"): 10,
    ("CIR", "H2"): 5,
    ("PS", "P1"): 30,
}
#: CEN-06 — células múltiplas de 8 (entrada de idosos com taxa 2,5× → ×5/8 exato)
BASE_PERFIL: dict[tuple[str, str], int] = {
    ("CONS", "C1"): 80,
    ("CONS", "C2"): 40,
    ("EXLAB", "L1"): 160,
    ("EXLAB", "L2"): 40,
    ("RM", "L1"): 8,
    ("RM", "L2"): 8,
    ("FISIO", "C1"): 24,
    ("INT", "H1"): 8,
    ("INT", "H2"): 8,
    ("CIR", "H1"): 8,
    ("PS", "P1"): 32,
}
#: CEN-07 — mesmos volumes, mas internação/cirurgia em H3 e exames em L3 (com tabela)
BASE_TABELA: dict[tuple[str, str], int] = {
    ("CONS", "C1"): 90,
    ("CONS", "C2"): 60,
    ("EXLAB", "L3"): 200,
    ("RM", "L3"): 20,
    ("FISIO", "C1"): 30,
    ("INT", "H3"): 15,
    ("CIR", "H3"): 15,
    ("PS", "P1"): 30,
}
PARES_COM_TABELA = (("INT", "H3"), ("CIR", "H3"), ("EXLAB", "L3"), ("RM", "L3"))
REAJUSTE_TABELA = 0.10


def _meses() -> list[date]:
    out, y, m = [], INICIO.year, INICIO.month
    while (y, m) <= (ALVO.year, ALVO.month):
        out.append(date(y, m, 1))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _preco_base(proc: str, prest: str) -> float:
    return round(PROCS[proc][5] * PRESTS[prest][3], 2)


def _escala(tab: dict, fator_num: int, fator_den: int) -> dict:
    out = {}
    for k, n in tab.items():
        assert (n * fator_num) % fator_den == 0, (k, n)
        out[k] = n * fator_num // fator_den
    return out


def _seed(*partes) -> int:
    return zlib.crc32("|".join(map(str, partes)).encode())


# ----------------------------------------------------------------------------- cenários
# Cada cenário: código, nome, vidas, tabela da comparação, tabela do alvo, preço no alvo,
# efeito esperado e extras (eventos adicionais por mês).
def _cenarios() -> list[dict]:
    mig = dict(BASE)
    mig.update({("INT", "H1"): 3, ("INT", "H2"): 12, ("CIR", "H1"): 3, ("CIR", "H2"): 12})
    mix = dict(BASE)
    mix.update({("CONS", "C1"): 72, ("CONS", "C2"): 48, ("CIR", "H1"): 30, ("CIR", "H2"): 15})
    return [
        dict(
            codigo="inv01_frequencia",
            contrato="CEN-01",
            nome="Mais atendimentos, custo unitário estável",
            vidas=500,
            base=BASE,
            alvo=_escala(BASE, 7, 5),
            efeito="frequencia",
            descricao="Todas as células +40% em jun/2026; preços e população constantes.",
        ),
        dict(
            codigo="inv02_custo_medio",
            contrato="CEN-02",
            nome="Custo médio sobe com frequência estável",
            vidas=500,
            base=BASE,
            alvo=BASE,
            fator_preco_alvo=1.15,
            efeito="custo_unitario",
            descricao="Mesmas contagens; valor apresentado +15% em todos os pares, SEM tabela "
            "de preço — não pode ser afirmado como reajuste.",
        ),
        dict(
            codigo="inv03_migracao_hospital",
            contrato="CEN-03",
            nome="Migração para hospital mais caro",
            vidas=500,
            base=BASE,
            alvo=mig,
            efeito="mix_prestadores",
            descricao="Internação e cirurgia: Hospital Alfa 2/3 → 1/5 do volume; Hospital Beta "
            "(preço 1,6×) passa a 4/5. Total de eventos e preços iguais.",
        ),
        dict(
            codigo="inv04_mix_procedimentos",
            contrato="CEN-04",
            nome="Mix de procedimentos mais complexo",
            vidas=500,
            base=BASE,
            alvo=mix,
            efeito="mix_procedimentos",
            descricao="−30 consultas e +30 cirurgias (mesma divisão entre prestadores): total "
            "de eventos constante.",
        ),
        dict(
            codigo="inv05_recorrentes",
            contrato="CEN-05",
            nome="Concentração em beneficiários recorrentes",
            vidas=500,
            base=BASE,
            alvo=BASE,
            grupo_recorrente=30,
            efeito="frequencia",
            descricao="30 beneficiários com fisioterapia + consulta em TODOS os meses; em "
            "jun/2026 cada um faz 3 sessões de fisioterapia em vez de 1.",
        ),
        dict(
            codigo="inv06_perfil",
            contrato="CEN-06",
            nome="Mudança de perfil etário da carteira",
            vidas=500,
            base=BASE_PERFIL,
            alvo=BASE_PERFIL,
            entrada_idosos=125,
            efeito="perfil_etario",
            descricao="125 beneficiários de 60-69 anos aderem em jun/2026 com taxa de "
            "utilização 2,5× a dos 30-39 e o mesmo mix; taxas por faixa constantes.",
        ),
        dict(
            codigo="inv07_reajuste_tabela",
            contrato="CEN-07",
            nome="Reajuste de preço com evidência",
            vidas=500,
            base=BASE_TABELA,
            alvo=BASE_TABELA,
            tabela=True,
            efeito="preco_contratual",
            descricao="Tabela contratada de Hospital Sigma e Laboratório Ômicron +10% a partir "
            "de jun/2026; valores apresentados = tabela. Contagens iguais.",
        ),
        dict(
            codigo="inv08_defasagem",
            contrato="CEN-08",
            nome="Aumento aparente por apresentação tardia",
            vidas=500,
            base=BASE,
            alvo=BASE,
            atrasados=True,
            efeito="defasagem",
            descricao="Em jun/2026 chegam 5 internações e 5 cirurgias atendidas em abr/2026 "
            "(apresentação tardia); atendimentos do próprio mês idênticos a maio.",
        ),
        dict(
            codigo="inv09_reducao",
            contrato="CEN-09",
            nome="Redução de custo assistencial",
            vidas=500,
            base=BASE,
            alvo=_escala(BASE, 3, 5),
            efeito="frequencia",
            descricao="Todas as células −40% em jun/2026; preços e população constantes.",
        ),
        dict(
            codigo="inv10_insuficiente",
            contrato="CEN-10",
            nome="Dados insuficientes para determinar a causa",
            vidas=6,
            base={("CONS", "C1"): 3, ("EXLAB", "L1"): 2},
            alvo={("CONS", "C1"): 3, ("EXLAB", "L1"): 2, ("CIR", "H1"): 1},
            so_dois_meses=True,
            efeito="indeterminado",
            descricao="6 vidas com 2 competências de histórico; uma cirurgia isolada em jun/2026.",
        ),
    ]


# ----------------------------------------------------------------------------- geração
def _evento(
    t: date,
    dia: date,
    benef: tuple,
    proc: str,
    prest: str,
    unit: float,
    contrato_id: int,
    plano_id: int,
    regiao_id: int,
    ids: dict,
    tag: str,
) -> dict:
    qtd = PROCS[proc][6]
    apres = round(unit * qtd, 2)
    glosa = round(apres * GLOSA, 2)
    return {
        "id_beneficiario": benef[0],
        "id_prestador": ids["prest"][prest],
        "id_procedimento": ids["proc"][proc],
        "id_especialidade": ids["esp"][PROCS[proc][1]],
        "id_diagnostico": None,
        "id_regiao": regiao_id,
        "id_contrato": contrato_id,
        "id_plano": plano_id,
        "id_estabelecimento": None,
        "id_prestador_solicitante": None,
        "data_evento": dia,
        "competencia": t,
        "tipo_atendimento": PROCS[proc][3],
        "quantidade": qtd,
        "valor_apresentado": apres,
        "valor_glosado": glosa,
        "valor_pago": round(apres - glosa, 2),
        "valor_coparticipacao": 0.0,
        "cenario_tag": tag,
    }


def _distribuir(
    t: date,
    tabela: dict,
    benefs: list[tuple],
    chave_rng: str,
    cen: dict,
    preco: Callable[[str, str, date], float],
    ctx: dict,
    dia_atend: date | None = None,
) -> list[dict]:
    rows = []
    for (proc, prest), n in sorted(tabela.items()):
        rnd = random.Random(_seed(cen["codigo"], proc, prest, chave_rng))
        for i in range(n):
            b = benefs[rnd.randrange(len(benefs))]
            dia = dia_atend or date(t.year, t.month, 1 + (i % 27))
            rows.append(
                _evento(
                    t,
                    dia,
                    b,
                    proc,
                    prest,
                    preco(proc, prest, t),
                    ctx["contrato"],
                    ctx["plano"],
                    ctx["regiao"],
                    ctx["ids"],
                    cen["codigo"],
                )
            )
    return rows


def run_lab(session: Session, tenant_id: str = TENANT, verbose: bool = True) -> dict:
    def log(msg: str) -> None:
        if verbose:
            print(f"[cenarios] {msg}", flush=True)

    bind_tenant(session, tenant_id)
    tenant = session.get(Tenant, tenant_id)
    if tenant is None:
        session.add(Tenant(id=tenant_id, name=TENANT_NOME, status="ACTIVE", is_synthetic=True))
    else:
        tenant.is_synthetic = True
    session.flush()
    wipe_dados(session, tenant_id)

    from app.data_platform import synthetic

    lineage = synthetic.start(session, tenant_id)
    meses = _meses()
    from app.data_platform.silver import MESES_PT

    session.execute(
        text(
            "INSERT INTO competencias (competencia, ano, mes, mes_nome, trimestre, is_inverno) "
            "SELECT m, extract(year FROM m)::int, extract(month FROM m)::int, "
            "(CAST(:nomes AS text[]))[extract(month FROM m)::int], "
            "(extract(month FROM m)::int - 1) / 3 + 1, extract(month FROM m)::int IN (6, 7, 8) "
            "FROM unnest(CAST(:meses AS date[])) AS m ON CONFLICT DO NOTHING"
        ),
        {"meses": meses, "nomes": [MESES_PT[i] for i in range(1, 13)]},
    )

    # ---- catálogo
    reg = Regiao(tenant_id=tenant_id, cidade="São Paulo", uf="SP", macrorregiao="Sudeste")
    session.add(reg)
    esp = {
        c: Especialidade(tenant_id=tenant_id, codigo=c, nome=n, grupo=g)
        for c, (n, g) in ESPECS.items()
    }
    session.add_all(esp.values())
    plano = Plano(
        tenant_id=tenant_id,
        codigo="PLN-A",
        nome="Plano Referência",
        segmentacao="completo",
        tem_coparticipacao=False,
        percentual_coparticipacao=0,
    )
    session.add(plano)
    session.flush()
    proc = {
        c: Procedimento(
            tenant_id=tenant_id,
            codigo=c,
            descricao=v[0],
            id_especialidade=esp[v[1]].id,
            grupo_procedimento=v[2],
            tipo_atendimento_tipico=v[3],
            perfil_utilizacao=v[4],
            custo_base=v[5],
            complexidade=None,
        )
        for c, v in PROCS.items()
    }
    prest = {
        c: Prestador(
            tenant_id=tenant_id,
            codigo=c,
            nome_ficticio=v[0],
            tipo_prestador=v[1],
            id_regiao=reg.id,
            id_especialidade_principal=esp[v[2]].id,
            nivel_preco=v[3],
        )
        for c, v in PRESTS.items()
    }
    session.add_all([*proc.values(), *prest.values()])
    session.flush()
    ids = {
        "proc": {c: p.id for c, p in proc.items()},
        "prest": {c: p.id for c, p in prest.items()},
        "esp": {c: e.id for c, e in esp.items()},
    }

    # ---- tabela de preço (CEN-07): base até mai/2026, +10% desde jun/2026
    for pc, ph in PARES_COM_TABELA:
        base = _preco_base(pc, ph)
        session.add_all(
            [
                PrecoContratado(
                    tenant_id=tenant_id,
                    id_prestador=ids["prest"][ph],
                    id_procedimento=ids["proc"][pc],
                    vigencia_inicio=INICIO,
                    vigencia_fim=ALVO,
                    valor_unitario=base,
                    tipo_remuneracao="tabela",
                    **lineage,
                    source_record_id=f"{ph}/{pc}/{INICIO}",
                ),
                PrecoContratado(
                    tenant_id=tenant_id,
                    id_prestador=ids["prest"][ph],
                    id_procedimento=ids["proc"][pc],
                    vigencia_inicio=ALVO,
                    vigencia_fim=None,
                    valor_unitario=round(base * (1 + REAJUSTE_TABELA), 2),
                    tipo_remuneracao="tabela",
                    **lineage,
                    source_record_id=f"{ph}/{pc}/{ALVO}",
                ),
            ]
        )

    gabarito, eventos, vidas_mes = [], [], {m: 0 for m in meses}
    rng_np = np.random.default_rng(20261023)
    for cen in _cenarios():
        ctr = Contrato(
            tenant_id=tenant_id,
            codigo=cen["contrato"],
            nome=f"{cen['contrato']} · {cen['nome']}",
            tipo="Empresarial",
            vidas_alvo=cen["vidas"],
        )
        session.add(ctr)
        session.flush()
        session.add(PlanoContrato(tenant_id=tenant_id, id_contrato=ctr.id, id_plano=plano.id))
        session.flush()
        adesao = COMPARACAO if cen.get("so_dois_meses") else date(2024, 1, 1)
        benefs = _beneficiarios(
            session,
            tenant_id,
            cen,
            ctr.id,
            plano.id,
            reg.id,
            "30-39",
            date(1990, 3, 1),
            adesao,
            cen["vidas"],
            0,
        )
        ctx = {"contrato": ctr.id, "plano": plano.id, "regiao": reg.id, "ids": ids}
        meses_cen = [m for m in meses if m >= adesao]
        for m in meses_cen:
            vidas_mes[m] += len(benefs)

        def preco(p: str, h: str, t: date, _cen=cen) -> float:
            v = _preco_base(p, h)
            if _cen.get("tabela") and (p, h) in PARES_COM_TABELA and t >= ALVO:
                return round(v * (1 + REAJUSTE_TABELA), 2)
            if _cen.get("fator_preco_alvo") and t >= ALVO:
                return round(v * _cen["fator_preco_alvo"], 2)
            return v

        grupo = benefs[: cen.get("grupo_recorrente", 0)]
        resto = benefs[len(grupo) :] or benefs
        for t in meses_cen:
            if t == ALVO:
                tabela, chave = cen["alvo"], "base"
            elif t == COMPARACAO:
                tabela, chave = cen["base"], "base"
            else:
                tabela = {k: int(rng_np.poisson(n)) for k, n in cen["base"].items()}
                chave = t.isoformat()
            eventos += _distribuir(t, tabela, resto, chave, cen, preco, ctx)
            if grupo:  # CEN-05: grupo recorrente todo mês; 3 sessões no alvo
                for b in grupo:
                    sessoes = 3 if t == ALVO else 1
                    for s in range(sessoes):
                        eventos.append(
                            _evento(
                                t,
                                date(t.year, t.month, 2 + s),
                                b,
                                "FISIO",
                                "C1",
                                preco("FISIO", "C1", t),
                                ctr.id,
                                plano.id,
                                reg.id,
                                ids,
                                cen["codigo"],
                            )
                        )
                    eventos.append(
                        _evento(
                            t,
                            date(t.year, t.month, 10),
                            b,
                            "CONS",
                            "C1",
                            preco("CONS", "C1", t),
                            ctr.id,
                            plano.id,
                            reg.id,
                            ids,
                            cen["codigo"],
                        )
                    )
            if cen.get("atrasados") and t == ALVO:
                for proc_, prest_ in (("INT", "H1"), ("CIR", "H1")):
                    eventos += _distribuir(
                        t,
                        {(proc_, prest_): 5},
                        resto,
                        "atrasados",
                        cen,
                        preco,
                        ctx,
                        dia_atend=date(2026, 4, 15),
                    )
        if cen.get("entrada_idosos"):
            idosos = _beneficiarios(
                session,
                tenant_id,
                cen,
                ctr.id,
                plano.id,
                reg.id,
                "60-69",
                date(1960, 3, 1),
                ALVO,
                cen["entrada_idosos"],
                cen["vidas"],
            )
            vidas_mes[ALVO] += len(idosos)
            tab_idosos = _escala(cen["base"], 5, 8)
            eventos += _distribuir(ALVO, tab_idosos, idosos, "idosos", cen, preco, ctx)
        gabarito.append(
            CenarioGabarito(
                tenant_id=tenant_id,
                codigo=cen["codigo"],
                nome=cen["nome"],
                competencia_alvo=ALVO,
                dimensao="contrato",
                chave_alvo=str(ctr.id),
                rotulo_alvo=ctr.nome,
                efeito_esperado=cen["efeito"],
                descricao=cen["descricao"],
                params={
                    "contrato_codigo": cen["contrato"],
                    "comparacao": COMPARACAO.isoformat(),
                    "vidas": cen["vidas"],
                },
            )
        )
        log(f"{cen['codigo']}: contrato {ctr.id}")

    _bulk_insert(session, EventoAssistencial, eventos, 5000, tenant_id=tenant_id)
    session.add_all(
        [
            Receita(
                tenant_id=tenant_id,
                competencia=m,
                id_plano=plano.id,
                quantidade_beneficiarios=v,
                receita_contraprestacao=round(v * RECEITA_POR_VIDA, 2),
                **lineage,
                source_record_id=f"{m:%Y-%m}/PLN-A",
            )
            for m, v in vidas_mes.items()
            if v
        ]
    )
    session.add_all(gabarito)
    session.flush()
    for tabela in ("beneficiarios", "eventos_assistenciais", "receitas", "precos_contratados"):
        session.execute(text(f"ANALYZE {tabela}"))
    counts = rebuild_aggregations(session, tenant_id=tenant_id)
    synthetic.finish(
        session,
        tenant_id,
        lineage,
        counts=counts,
        eventos=len(eventos),
        competencia_inicio=INICIO,
        competencia_fim=ALVO,
    )
    session.add(
        SeedManifest(
            tenant_id=tenant_id,
            seed=20261023,
            beneficiarios=sum(c["vidas"] for c in _cenarios()),
            inicio=INICIO,
            fim=ALVO,
            escala_eventos=1.0,
            contagens={"eventos": len(eventos), "cenarios": len(gabarito), **counts},
        )
    )
    session.commit()
    log(f"{len(eventos)} eventos · {len(gabarito)} cenários · {counts}")
    return {"eventos": len(eventos), "cenarios": len(gabarito), **counts}


def _beneficiarios(
    session: Session,
    tenant_id: str,
    cen: dict,
    contrato_id: int,
    plano_id: int,
    regiao_id: int,
    faixa: str,
    nascimento: date,
    adesao: date,
    n: int,
    offset: int,
) -> list[tuple]:
    rows = [
        Beneficiario(
            tenant_id=tenant_id,
            codigo=f"{cen['contrato']}-{offset + i + 1:04d}",
            sexo="F" if i % 2 else "M",
            data_nascimento=nascimento,
            faixa_etaria=faixa,
            id_regiao=regiao_id,
            id_plano=plano_id,
            id_contrato=contrato_id,
            data_adesao=adesao,
            status="ativo",
            **session.info.get("w2h_lineage", {}),
            source_record_id=f"{cen['contrato']}-{offset + i + 1:04d}",
        )
        for i in range(n)
    ]
    session.add_all(rows)
    session.flush()
    return [(b.id, faixa) for b in rows]


def main() -> None:
    from app.db.session import AdminSessionLocal

    ap = argparse.ArgumentParser(description="Laboratório de cenários da investigação de custos")
    ap.add_argument("--tenant", default=TENANT)
    args = ap.parse_args()
    with AdminSessionLocal() as s:
        run_lab(s, tenant_id=args.tenant)


if __name__ == "__main__":
    main()
