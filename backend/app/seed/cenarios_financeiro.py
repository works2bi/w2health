"""Laboratório cadastral/financeiro (Consolidação do canônico V1) — tenant `w2h-financeiro`.

Massa pequena e DETERMINÍSTICA (jan–jun/2026) com resultado conhecido para os 12 cenários
da consolidação; o gabarito fica em `cenarios_gabarito` e é verificado por
`tests/test_cenarios_financeiro.py`. Os mesmos dados passam pela MESMA regra de atribuição
do pipeline (`silver.atribuicao_receita` e a regra de vínculo do evento).

    python -m app.seed.cenarios_financeiro

Estrutura:
* EMP-ALFA (empresarial) — CT-A1 (planos PL-A e PL-B) e CT-A2: faturamento DIRETO por contrato;
* EMP-BETA — CT-B1, CT-B2: fatura CONSOLIDADA da empresa COM detalhamento contrato × plano;
* EMP-GAMA — CT-G1, CT-G2: fatura CONSOLIDADA SEM detalhamento (receita no nível empresa);
* CT-SEM (PF) — sem receita atribuível;
* receita órfã (contrato `CT-INEXISTENTE`) — preservada como não atribuída;
* B-X muda de contrato (CT-A1 → CT-A2 em abr/2026), B-Y muda de plano (PL-A → PL-B em
  abr/2026), B-Z tem vínculo encerrado em abr/2026 e um atendimento em mai/2026 (não atribuído);
* receita legada por plano presente e igual ao total reconhecido (fonte única: reconhecida).

Tickets: PL-A R$ 300, PL-B R$ 500 por vida-mês. Cada beneficiário exposto faz 1 consulta
(R$ 100) por mês; B-X faz um exame (R$ 50) em fev e em mai. Fatura emitida no mês anterior à
cobertura (competência de faturamento ≠ reconhecimento); recebimento no mês da cobertura —
exceto a fatura GAMA de jun (não recebida) e CT-A2 de mai (recebida pela metade).
"""

from __future__ import annotations

import argparse
from datetime import date
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.data_platform.silver import MESES_PT, atribuicao_receita
from app.db.tenant_scope import bind_tenant
from app.models import (
    Beneficiario,
    BeneficiarioVinculo,
    CenarioGabarito,
    Contrato,
    Empresa,
    EmpresaContrato,
    Especialidade,
    EventoAssistencial,
    Fatura,
    FaturaItem,
    Plano,
    PlanoContrato,
    Prestador,
    Procedimento,
    Recebimento,
    Receita,
    ReceitaReconhecida,
    Regiao,
    SeedManifest,
    Tenant,
)
from app.seed.aggregate import rebuild_aggregations
from app.seed.generator import wipe_dados

TENANT = "w2h-financeiro"
TENANT_NOME = "Laboratório Cadastral e Financeiro (sintético)"
MESES = [date(2026, m, 1) for m in range(1, 7)]
MUDANCA = date(2026, 4, 1)
TICKET = {"PL-A": Decimal("300.00"), "PL-B": Decimal("500.00")}
CONSULTA, EXAME = Decimal("100.00"), Decimal("50.00")
ORFA = Decimal("250.00")
ACRESCIMO = Decimal("15.00")  # juros na fatura FAT-202604-CT-A1: faturado ≠ reconhecido

EMPRESAS = {
    "EMP-ALFA": ("Alfa Indústria (fictícia)", "empresarial"),
    "EMP-BETA": ("Beta Serviços (fictícia)", "empresarial"),
    "EMP-GAMA": ("Associação Gama (fictícia)", "adesao"),
}
# contrato -> (empresa | None, tipo, cobrança, [(plano, n_vidas)])
CONTRATOS = {
    "CT-A1": ("EMP-ALFA", "Empresarial", "contrato", [("PL-A", 6), ("PL-B", 4)]),
    "CT-A2": ("EMP-ALFA", "Empresarial", "contrato", [("PL-A", 8)]),
    "CT-B1": ("EMP-BETA", "Empresarial", "empresa_detalhada", [("PL-A", 6)]),
    "CT-B2": ("EMP-BETA", "Empresarial", "empresa_detalhada", [("PL-B", 5)]),
    "CT-G1": ("EMP-GAMA", "Adesao", "empresa_sem_detalhe", [("PL-A", 7)]),
    "CT-G2": ("EMP-GAMA", "Adesao", "empresa_sem_detalhe", [("PL-B", 4)]),
    "CT-SEM": (None, "PF", "sem_receita", [("PL-A", 3)]),
}
#: planos oferecidos por contrato (CT-A1 tem os dois — cenário 2; CT-A2 aceita PL-A)
PLANOS_DO_CONTRATO = {
    "CT-A1": ("PL-A", "PL-B"),
    "CT-A2": ("PL-A",),
    "CT-B1": ("PL-A",),
    "CT-B2": ("PL-B",),
    "CT-G1": ("PL-A",),
    "CT-G2": ("PL-B",),
    "CT-SEM": ("PL-A",),
}


def _anterior(m: date) -> date:
    return date(m.year - 1, 12, 1) if m.month == 1 else date(m.year, m.month - 1, 1)


def run_lab(session: Session, tenant_id: str = TENANT, verbose: bool = True) -> dict:
    bind_tenant(session, tenant_id)
    t = session.get(Tenant, tenant_id)
    if t is None:
        session.add(Tenant(id=tenant_id, name=TENANT_NOME, status="ACTIVE", is_synthetic=True))
    else:
        t.is_synthetic = True
    session.flush()
    wipe_dados(session, tenant_id)

    from app.data_platform import synthetic

    lin = synthetic.start(session, tenant_id)
    meses_cal = [_anterior(MESES[0]), *MESES]
    session.execute(
        text(
            "INSERT INTO competencias (competencia, ano, mes, mes_nome, trimestre, is_inverno) "
            "SELECT m, extract(year FROM m)::int, extract(month FROM m)::int, "
            "(CAST(:nomes AS text[]))[extract(month FROM m)::int], "
            "(extract(month FROM m)::int - 1) / 3 + 1, extract(month FROM m)::int IN (6, 7, 8) "
            "FROM unnest(CAST(:meses AS date[])) AS m ON CONFLICT DO NOTHING"
        ),
        {"meses": meses_cal, "nomes": [MESES_PT[i] for i in range(1, 13)]},
    )

    def add(obj, rid: str | None = None):
        for k, v in lin.items():
            setattr(obj, k, v)
        obj.source_record_id = rid
        session.add(obj)
        return obj

    reg = add(
        Regiao(tenant_id=tenant_id, cidade="Campinas", uf="SP", macrorregiao="Sudeste"),
        "Campinas/SP",
    )
    esp = add(
        Especialidade(tenant_id=tenant_id, codigo="CLI", nome="Clínica médica", grupo="clinica"),
        "CLI",
    )
    planos = {
        c: add(
            Plano(
                tenant_id=tenant_id,
                codigo=c,
                nome=f"Plano {c[-1]}",
                segmentacao="completo",
                tem_coparticipacao=False,
                percentual_coparticipacao=0,
            ),
            c,
        )
        for c in TICKET
    }
    session.flush()
    proc = {
        c: add(
            Procedimento(
                tenant_id=tenant_id,
                codigo=c,
                descricao=d,
                id_especialidade=esp.id,
                grupo_procedimento=g,
                tipo_atendimento_tipico=tp,
            ),
            c,
        )
        for c, d, g, tp in (
            ("CONS", "Consulta", "Consultas", "consulta"),
            ("EXA", "Exame", "Exames", "exame"),
        )
    }
    prest = add(
        Prestador(
            tenant_id=tenant_id,
            codigo="P1",
            nome_ficticio="Clínica Campinas",
            tipo_prestador="clinica",
            id_regiao=reg.id,
            id_especialidade_principal=esp.id,
        ),
        "P1",
    )
    emps = {
        c: add(Empresa(tenant_id=tenant_id, codigo=c, nome=n, tipo=tp, status="ativa"), c)
        for c, (n, tp) in EMPRESAS.items()
    }
    ctrs = {
        c: add(Contrato(tenant_id=tenant_id, codigo=c, nome=f"Contrato {c}", tipo=v[1]), c)
        for c, v in CONTRATOS.items()
    }
    session.flush()
    for c, pls in PLANOS_DO_CONTRATO.items():
        for p in pls:
            add(
                PlanoContrato(tenant_id=tenant_id, id_contrato=ctrs[c].id, id_plano=planos[p].id),
                f"{c}/{p}",
            )
    for c, (e, *_r) in CONTRATOS.items():
        if e:
            add(
                EmpresaContrato(
                    tenant_id=tenant_id,
                    id_empresa=emps[e].id,
                    id_contrato=ctrs[c].id,
                    papel="contratante",
                    vigencia_inicio=date(2025, 1, 1),
                ),
                f"{c}/contratante",
            )
    session.flush()

    # ---- carteira: (código, contrato, plano) atuais + histórico de vínculo de B-X, B-Y, B-Z
    benef: dict[str, Beneficiario] = {}
    for c, (_e, _tp, _cob, grupos) in CONTRATOS.items():
        n = 0
        for p, qtd in grupos:
            for _ in range(qtd):
                n += 1
                cod = f"{c}-{n:02d}"
                benef[cod] = add(
                    Beneficiario(
                        tenant_id=tenant_id,
                        codigo=cod,
                        sexo="F" if n % 2 else "M",
                        data_nascimento=date(1985, 1, 1),
                        faixa_etaria="40-49",
                        id_regiao=reg.id,
                        id_plano=planos[p].id,
                        id_contrato=ctrs[c].id,
                        data_adesao=date(2025, 1, 1),
                        status="ativo",
                    ),
                    cod,
                )
    # B-X = CT-A1-01 (PL-A) muda para CT-A2 em abr; B-Y = CT-A1-02 (PL-A) muda para PL-B;
    # B-Z = CT-B1-01: vínculo encerrado em abr (saída da carteira)
    bx, by, bz = benef["CT-A1-01"], benef["CT-A1-02"], benef["CT-B1-01"]
    bx.id_contrato = ctrs["CT-A2"].id  # cadastro ATUAL — o histórico é que decide o passado
    by.id_plano = planos["PL-B"].id
    bz.data_saida, bz.status = MUDANCA, "inativo"
    session.flush()
    hist = [
        (bx, "CT-A1", "PL-A", date(2025, 1, 1), MUDANCA, "ativo"),
        (bx, "CT-A2", "PL-A", MUDANCA, None, "ativo"),
        (by, "CT-A1", "PL-A", date(2025, 1, 1), MUDANCA, "ativo"),
        (by, "CT-A1", "PL-B", MUDANCA, None, "ativo"),
        (bz, "CT-B1", "PL-A", date(2025, 1, 1), MUDANCA, "ativo"),
    ]
    for b, c, p, ini, fim, sit in hist:
        add(
            BeneficiarioVinculo(
                tenant_id=tenant_id,
                id_beneficiario=b.id,
                id_contrato=ctrs[c].id,
                id_plano=planos[p].id,
                vigencia_inicio=ini,
                vigencia_fim=fim,
                situacao=sit,
            ),
            f"{b.codigo}/{ini}",
        )
    com_hist = {b.id for b, *_ in hist}

    def vinculo(b: Beneficiario, dia: date) -> tuple[int | None, int | None]:
        """A MESMA regra do Silver (`SilverLoader._vinculo_resolver`)."""
        if b.id in com_hist:
            for hb, c, p, ini, fim, _s in hist:
                if hb is b and ini <= dia and (fim is None or dia < fim):
                    return ctrs[c].id, planos[p].id
            return None, None
        if b.data_adesao <= dia and (b.data_saida is None or dia < b.data_saida):
            return b.id_contrato, b.id_plano
        return None, None

    id_ctr = {v.id: k for k, v in ctrs.items()}
    id_pl = {v.id: k for k, v in planos.items()}

    # ---- eventos + receita por (mês, contrato, plano) a partir de quem está exposto no dia 1
    eventos, receita = [], {}
    for m in MESES:
        for b in benef.values():
            c, p = vinculo(b, m)
            if c is None:
                continue
            k = (m, id_ctr[c], id_pl[p])
            if CONTRATOS[id_ctr[c]][2] != "sem_receita":  # CT-SEM: receita não carregada
                receita[k] = receita.get(k, Decimal(0)) + TICKET[id_pl[p]]
            eventos.append((b, date(m.year, m.month, 10), "CONS", CONSULTA))
        if m in (date(2026, 2, 1), date(2026, 5, 1)):
            eventos.append((bx, date(m.year, m.month, 15), "EXA", EXAME))
    eventos.append((bz, date(2026, 5, 5), "CONS", CONSULTA))  # sem vínculo vigente
    for i, (b, dia, pc, valor) in enumerate(eventos):
        c, p = vinculo(b, dia)
        add(
            EventoAssistencial(
                tenant_id=tenant_id,
                id_beneficiario=b.id,
                id_prestador=prest.id,
                id_procedimento=proc[pc].id,
                id_especialidade=esp.id,
                id_regiao=reg.id,
                id_contrato=c,
                id_plano=p,
                data_evento=dia,
                competencia=dia.replace(day=1),
                competencia_apresentacao=dia.replace(day=1),
                tipo_atendimento="consulta" if pc == "CONS" else "exame",
                quantidade=1,
                numero_guia=f"G-{i:05d}",
                numero_conta=f"C-{dia:%Y%m}-{b.codigo}",
                natureza_lancamento="normal",
                valor_pago_origem="derivado",
                valor_apresentado=valor,
                valor_glosado=0,
                valor_pago=valor,
                valor_coparticipacao=0,
                cenario_tag="fin",
            ),
            f"EV-{i:05d}",
        )
    session.flush()

    # ---- faturamento, recebimento, receita reconhecida e receita legada
    cod_ctr = {k: v.id for k, v in ctrs.items()}
    cod_pl = {k: v.id for k, v in planos.items()}
    cod_emp = {k: v.id for k, v in emps.items()}
    legada: dict[tuple[date, str], Decimal] = {}

    def reconhecer(m: date, rid: str, valor: Decimal, doc: str | None, qtd: int, **codigos):
        row = {k: v for k, v in codigos.items() if v}
        attr = atribuicao_receita(row, cod_ctr, cod_pl, cod_emp)
        add(
            ReceitaReconhecida(
                tenant_id=tenant_id,
                competencia=m,
                natureza="contraprestacao",
                numero_documento=doc,
                quantidade_beneficiarios=qtd,
                receita_contraprestacao=valor,
                estimada=False,
                **attr,
            ),
            rid,
        )

    def receber(fat: Fatura, m: date, valor: Decimal, n: str):
        add(
            Recebimento(
                tenant_id=tenant_id,
                numero_recebimento=n,
                id_fatura=fat.id,
                data_recebimento=date(m.year, m.month, 10),
                competencia_recebimento=m,
                valor_recebido=valor,
            ),
            n,
        )

    for m in MESES:
        por_ctr: dict[str, list[tuple[str, Decimal]]] = {}
        for (mm, c, p), v in receita.items():
            if mm == m:
                por_ctr.setdefault(c, []).append((p, v))
                legada[(m, p)] = legada.get((m, p), Decimal(0)) + v
        # direto por contrato (ALFA)
        for c in ("CT-A1", "CT-A2"):
            itens = sorted(por_ctr.get(c, []))
            bruto = sum(v for _p, v in itens)
            acr = ACRESCIMO if (c, m) == ("CT-A1", MUDANCA) else Decimal(0)
            doc = f"FAT-{m:%Y%m}-{c}"
            fat = add(
                Fatura(
                    tenant_id=tenant_id,
                    numero_documento=doc,
                    unidade_cobranca="contrato",
                    id_contrato=ctrs[c].id,
                    competencia_faturamento=_anterior(m),
                    data_emissao=date(_anterior(m).year, _anterior(m).month, 20),
                    valor_bruto=bruto,
                    valor_desconto=0,
                    valor_acrescimo=acr,
                    valor_total=bruto + acr,
                    status="emitida",
                ),
                doc,
            )
            session.flush()
            for j, (p, v) in enumerate(itens, 1):
                add(
                    FaturaItem(
                        tenant_id=tenant_id,
                        id_fatura=fat.id,
                        numero_item=str(j),
                        natureza="mensalidade",
                        id_contrato=ctrs[c].id,
                        id_plano=planos[p].id,
                        competencia_referencia=m,
                        valor=v,
                    ),
                    f"{doc}/{j}",
                )
                reconhecer(
                    m,
                    f"RR-{m:%Y%m}-{c}-{p}",
                    v,
                    doc,
                    int(v / TICKET[p]),
                    contrato_codigo=c,
                    plano_codigo=p,
                )
            if acr:
                add(
                    FaturaItem(
                        tenant_id=tenant_id,
                        id_fatura=fat.id,
                        numero_item="J",
                        natureza="acrescimo",
                        id_contrato=ctrs[c].id,
                        valor=acr,
                    ),
                    f"{doc}/J",
                )
            parcial = (c, m) == ("CT-A2", date(2026, 5, 1))
            receber(fat, m, (bruto + acr) / 2 if parcial else bruto + acr, f"REC-{doc}")
        # consolidado da empresa COM detalhamento (BETA)
        doc = f"FAT-{m:%Y%m}-EMP-BETA"
        itens = [(c, p, v) for c in ("CT-B1", "CT-B2") for p, v in sorted(por_ctr.get(c, []))]
        bruto = sum(v for *_x, v in itens)
        fat = add(
            Fatura(
                tenant_id=tenant_id,
                numero_documento=doc,
                unidade_cobranca="empresa",
                id_empresa=emps["EMP-BETA"].id,
                competencia_faturamento=_anterior(m),
                valor_bruto=bruto,
                valor_desconto=0,
                valor_acrescimo=0,
                valor_total=bruto,
                status="emitida",
            ),
            doc,
        )
        session.flush()
        for j, (c, p, v) in enumerate(itens, 1):
            add(
                FaturaItem(
                    tenant_id=tenant_id,
                    id_fatura=fat.id,
                    numero_item=str(j),
                    natureza="mensalidade",
                    id_contrato=ctrs[c].id,
                    id_plano=planos[p].id,
                    competencia_referencia=m,
                    valor=v,
                ),
                f"{doc}/{j}",
            )
            reconhecer(
                m,
                f"RR-{m:%Y%m}-{c}-{p}",
                v,
                doc,
                int(v / TICKET[p]),
                contrato_codigo=c,
                plano_codigo=p,
            )
        receber(fat, m, bruto, f"REC-{doc}")
        # consolidado SEM detalhamento (GAMA): a receita fica no nível empresa
        doc = f"FAT-{m:%Y%m}-EMP-GAMA"
        bruto = sum(v for c in ("CT-G1", "CT-G2") for _p, v in por_ctr.get(c, []))
        vidas = sum(int(v / TICKET[p]) for c in ("CT-G1", "CT-G2") for p, v in por_ctr.get(c, []))
        fat = add(
            Fatura(
                tenant_id=tenant_id,
                numero_documento=doc,
                unidade_cobranca="empresa",
                id_empresa=emps["EMP-GAMA"].id,
                competencia_faturamento=_anterior(m),
                valor_bruto=bruto,
                valor_desconto=0,
                valor_acrescimo=0,
                valor_total=bruto,
                status="emitida",
            ),
            doc,
        )
        session.flush()
        reconhecer(m, f"RR-{m:%Y%m}-EMP-GAMA", bruto, doc, vidas, empresa_codigo="EMP-GAMA")
        if m != date(2026, 6, 1):
            receber(fat, m, bruto, f"REC-{doc}")
        # receita sem correspondência cadastral (preservada, não atribuída)
        reconhecer(m, f"RR-{m:%Y%m}-ORFA", ORFA, None, 0, contrato_codigo="CT-INEXISTENTE")
        legada[(m, "PL-A")] = legada.get((m, "PL-A"), Decimal(0)) + ORFA
    for (m, p), v in sorted(legada.items()):
        add(
            Receita(
                tenant_id=tenant_id,
                competencia=m,
                id_plano=planos[p].id,
                quantidade_beneficiarios=int(
                    sum(
                        int(x / TICKET[pp])
                        for (mm, _c, pp), x in receita.items()
                        if mm == m and pp == p
                    )
                ),
                receita_contraprestacao=v,
            ),
            f"{m:%Y-%m}/{p}",
        )
    session.flush()

    gab = [
        ("fin01_empresa_varios_contratos", "Uma empresa com múltiplos contratos", "EMP-ALFA"),
        ("fin02_contrato_varios_planos", "Um contrato com múltiplos planos", "CT-A1"),
        ("fin03_faturamento_direto_contrato", "Faturamento direto por contrato", "CT-A1"),
        (
            "fin04_faturamento_consolidado_empresa",
            "Faturamento consolidado por empresa",
            "EMP-BETA",
        ),
        (
            "fin05_empresa_com_detalhamento",
            "Receita empresarial com detalhamento contratual",
            "EMP-BETA",
        ),
        (
            "fin06_empresa_sem_detalhamento",
            "Receita empresarial sem detalhamento contratual",
            "EMP-GAMA",
        ),
        ("fin07_mudanca_contrato", "Beneficiário mudando de contrato", bx.codigo),
        ("fin08_mudanca_plano", "Beneficiário mudando de plano", by.codigo),
        ("fin09_despesa_antes_depois", "Despesa antes e depois da mudança", bx.codigo),
        ("fin10_vidas_historicas", "Comparação histórica de vidas", "CT-A1"),
        ("fin11_receita_sem_cadastro", "Receita sem correspondência cadastral", "CT-INEXISTENTE"),
        ("fin12_contrato_sem_receita", "Contrato sem receita atribuível", "CT-SEM"),
    ]
    session.add_all(
        CenarioGabarito(
            tenant_id=tenant_id,
            codigo=c,
            nome=n,
            competencia_alvo=MUDANCA,
            dimensao=None,
            chave_alvo=k,
            descricao=n,
            params={},
        )
        for c, n, k in gab
    )
    for tabela in ("beneficiarios", "eventos_assistenciais", "receitas", "receitas_reconhecidas"):
        session.execute(text(f"ANALYZE {tabela}"))
    counts = rebuild_aggregations(session, tenant_id=tenant_id)
    synthetic.finish(
        session,
        tenant_id,
        lin,
        counts=counts,
        eventos=len(eventos),
        competencia_inicio=MESES[0],
        competencia_fim=MESES[-1],
    )
    session.add(
        SeedManifest(
            tenant_id=tenant_id,
            seed=0,
            beneficiarios=len(benef),
            inicio=MESES[0],
            fim=MESES[-1],
            escala_eventos=1.0,
            contagens={"eventos": len(eventos), "cenarios": len(gab), **counts},
        )
    )
    session.commit()
    if verbose:
        print(
            f"[financeiro] {len(benef)} beneficiários · {len(eventos)} eventos · {counts}",
            flush=True,
        )
    return {"beneficiarios": len(benef), "eventos": len(eventos), **counts}


def main() -> None:
    from app.db.session import AdminSessionLocal

    ap = argparse.ArgumentParser(description="Laboratório cadastral e financeiro")
    ap.add_argument("--tenant", default=TENANT)
    with AdminSessionLocal() as s:
        run_lab(s, tenant_id=ap.parse_args().tenant)


if __name__ == "__main__":
    main()
