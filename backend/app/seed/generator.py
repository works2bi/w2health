"""Geração da base sintética: catálogos, carteira, receita e eventos base.

Vetorizado com numpy por competência. Reprodutível pelo `seed` do `SeedConfig`.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date

import numpy as np
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.faixas import faixa_etaria
from app.models import (
    AggBeneficiarioCompetencia,
    AggCompetenciaDimensao,
    AggPrestadorCompetencia,
    AggSinistralidadeCompetencia,
    Beneficiario,
    CenarioGabarito,
    Competencia,
    Contrato,
    Diagnostico,
    Especialidade,
    EventoAssistencial,
    Plano,
    PlanoContrato,
    Prestador,
    Procedimento,
    Receita,
    Regiao,
    SeedManifest,
)
from app.seed import affinities as aff
from app.seed.catalogs import (
    _CLIN_NOMES,
    _HOSP_NOMES,
    CONTRATOS,
    DIAGNOSTICOS,
    ESPECIALIDADES,
    GRUPO_PERFIL_UTILIZACAO,
    PLANOS,
    PRESTADOR_TIPOS,
    PROCEDIMENTOS,
    REGIOES,
)
from app.seed.config import SeedConfig

MESES_PT = [
    "", "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
    "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro",
]
ESPEC_SLUGS = [e[0] for e in ESPECIALIDADES]


@dataclass
class Catalogo:
    """Ids e arrays derivados dos catálogos, para uso vetorizado na geração."""

    regiao_ids: list[int]
    regiao_pesos: np.ndarray
    plano_ids: list[int]
    plano_ticket: np.ndarray
    plano_copart_pct: np.ndarray       # índice alinhado a plano_ids — 0 se plano sem coparticipação
    contrato_por_plano: dict[int, list[int]]
    contrato_pesos_por_plano: dict[int, np.ndarray]  # v1.2 — pesos (vidas_alvo) alinhados a contrato_por_plano
    espec_id: dict[str, int]           # slug -> id
    espec_id_arr: np.ndarray           # index alinhado a ESPEC_SLUGS
    proc_by_espec: dict[str, list[dict]]   # slug -> [{id, codigo, custo_base, tipo, idade_min/max, peso}]
    prest_by_espec: dict[str, dict]        # slug -> {ids, regiao, nivel}
    diag_by_espec: dict[str, list[int]]
    proc_meta: dict[int, dict]             # proc_id -> meta (para agregação por grupo)


# ----------------------------------------------------------------------------------
# Catálogos
# ----------------------------------------------------------------------------------
def load_catalogos(session: Session, cfg: SeedConfig, rng: np.random.Generator) -> Catalogo:
    # Competências — calendário GLOBAL: só insere as que faltam (vários tenants o compartilham)
    existentes = set(session.execute(select(Competencia.competencia)).scalars())
    for m in cfg.competencias():
        if m in existentes:
            continue
        session.add(
            Competencia(
                competencia=m,
                ano=m.year,
                mes=m.month,
                mes_nome=MESES_PT[m.month],
                trimestre=(m.month - 1) // 3 + 1,
                is_inverno=m.month in (6, 7, 8),
            )
        )

    tid = cfg.tenant_id

    # Regiões
    regioes = [
        Regiao(tenant_id=tid, cidade=c, uf=uf, macrorregiao=mr) for (c, uf, mr, _w) in REGIOES
    ]
    session.add_all(regioes)
    session.flush()
    regiao_ids = [r.id for r in regioes]
    regiao_pesos = np.array([w for (_c, _uf, _mr, w) in REGIOES], dtype=float)
    regiao_pesos /= regiao_pesos.sum()

    # Planos
    planos = [
        Plano(
            tenant_id=tid, codigo=cod, nome=nome, segmentacao=seg, ticket_medio_base=tk,
            tem_coparticipacao=copart, percentual_coparticipacao=copart_pct,
        )
        for (cod, nome, seg, tk, copart, copart_pct) in PLANOS
    ]
    session.add_all(planos)
    session.flush()
    plano_id_by_cod = {p.codigo: p.id for p in planos}
    plano_ids = [p.id for p in planos]
    plano_ticket = np.array([float(p.ticket_medio_base) for p in planos], dtype=float)
    plano_copart_pct = np.array(
        [float(p.percentual_coparticipacao) if p.tem_coparticipacao else 0.0 for p in planos],
        dtype=float,
    )

    # Contratos (v1.2 — vários por plano, tamanhos distintos). Contrato × plano é N:N
    # (`planos_contratos`); o catálogo sintético usa 1 plano por contrato.
    contrato_por_plano: dict[int, list[int]] = {pid: [] for pid in plano_ids}
    contrato_pesos_por_plano: dict[int, list[float]] = {pid: [] for pid in plano_ids}
    contratos = []
    for n_ctr, (_cod, nome, tipo, vidas_alvo) in enumerate(CONTRATOS, start=1):
        c = Contrato(
            tenant_id=tid, codigo=f"CTR-{n_ctr:04d}", nome=nome, tipo=tipo,
            vidas_alvo=int(vidas_alvo),
        )
        contratos.append(c)
    session.add_all(contratos)
    session.flush()
    session.add_all([
        PlanoContrato(tenant_id=tid, id_contrato=c.id, id_plano=plano_id_by_cod[cod])
        for c, (cod, _n, _t, _v) in zip(contratos, CONTRATOS, strict=True)
    ])
    session.flush()
    for c, (cod, _n, _t, _v) in zip(contratos, CONTRATOS, strict=True):
        pid = plano_id_by_cod[cod]
        contrato_por_plano[pid].append(c.id)
        contrato_pesos_por_plano[pid].append(float(max(c.vidas_alvo, 1)))
    contrato_pesos_np = {
        pid: (np.array(w, dtype=float) / sum(w)) if w else np.array([], dtype=float)
        for pid, w in contrato_pesos_por_plano.items()
    }

    # Especialidades
    especs = [
        Especialidade(tenant_id=tid, codigo=slug.upper()[:20], nome=nome, grupo=grupo)
        for (slug, nome, grupo) in ESPECIALIDADES
    ]
    session.add_all(especs)
    session.flush()
    espec_id = {ESPECIALIDADES[i][0]: especs[i].id for i in range(len(especs))}
    espec_id_arr = np.array([espec_id[s] for s in ESPEC_SLUGS])

    # Procedimentos
    proc_rows = []
    for (cod, desc, eslug, grupo, cx, custo, tipo, imin, imax) in PROCEDIMENTOS:
        proc_rows.append(
            Procedimento(
                tenant_id=tid, codigo=cod, descricao=desc, id_especialidade=espec_id[eslug],
                grupo_procedimento=grupo, complexidade=cx, custo_base=custo,
                tipo_atendimento_tipico=tipo, idade_min=imin, idade_max=imax,
                perfil_utilizacao=GRUPO_PERFIL_UTILIZACAO.get(grupo, "variavel"),
            )
        )
    session.add_all(proc_rows)
    session.flush()
    proc_by_espec: dict[str, list[dict]] = {s: [] for s in ESPEC_SLUGS}
    proc_meta: dict[int, dict] = {}
    for pr, spec in zip(proc_rows, PROCEDIMENTOS, strict=True):
        eslug = spec[2]
        d = {
            "id": pr.id, "codigo": pr.codigo, "custo_base": float(pr.custo_base),
            "tipo": pr.tipo_atendimento_tipico, "idade_min": pr.idade_min,
            "idade_max": pr.idade_max, "peso": aff.PESO_PROCEDIMENTO.get(pr.codigo, 1.0),
            "grupo": pr.grupo_procedimento,
        }
        proc_by_espec[eslug].append(d)
        proc_meta[pr.id] = {"grupo": pr.grupo_procedimento, "espec": eslug, "codigo": pr.codigo}

    # Diagnósticos
    diags = [
        Diagnostico(
            tenant_id=tid, cid=cid, descricao=desc,
            id_especialidade=espec_id.get(es) if es else None,
        )
        for (cid, desc, es) in DIAGNOSTICOS
    ]
    session.add_all(diags)
    session.flush()
    diag_by_espec: dict[str, list[int]] = {s: [] for s in ESPEC_SLUGS}
    for dg, spec in zip(diags, DIAGNOSTICOS, strict=True):
        if spec[2]:
            diag_by_espec[spec[2]].append(dg.id)

    # Prestadores
    prest_rows = _build_prestadores(rng, espec_id, regiao_ids, tenant_id=tid)
    for n_prest, p in enumerate(prest_rows, start=1):  # chave de negócio (Fase 2)
        p.codigo = f"PRE-{n_prest:04d}"
    session.add_all(prest_rows)
    session.flush()
    prest_by_espec: dict[str, dict] = {}
    for slug in ESPEC_SLUGS:
        sub = [p for p in prest_rows if p.id_especialidade_principal == espec_id[slug]]
        if not sub:  # garante cobertura mínima
            sub = prest_rows[:2]
        prest_by_espec[slug] = {
            "ids": np.array([p.id for p in sub]),
            "regiao": np.array([p.id_regiao for p in sub]),
            "nivel": np.array([float(p.nivel_preco) for p in sub]),
        }

    session.flush()
    return Catalogo(
        regiao_ids=regiao_ids, regiao_pesos=regiao_pesos, plano_ids=plano_ids,
        plano_ticket=plano_ticket, plano_copart_pct=plano_copart_pct,
        contrato_por_plano=contrato_por_plano,
        contrato_pesos_por_plano=contrato_pesos_np,
        espec_id=espec_id, espec_id_arr=espec_id_arr, proc_by_espec=proc_by_espec,
        prest_by_espec=prest_by_espec, diag_by_espec=diag_by_espec, proc_meta=proc_meta,
    )


def _build_prestadores(rng, espec_id, regiao_ids, tenant_id: str) -> list[Prestador]:
    rows: list[Prestador] = []
    hosp_iter = iter(_HOSP_NOMES)
    clin_iter = iter(_CLIN_NOMES)
    # distribuição de especialidade principal por tipo de prestador
    espec_por_tipo = {
        "hospital": ["clinica_medica", "cardiologia", "ortopedia", "pneumologia",
                     "neurologia", "gineco_obst", "gastro", "oncologia"],
        "clinica": ESPEC_SLUGS,
        "laboratorio": ["radiologia", "clinica_medica"],
        "pronto_atendimento": ["clinica_medica", "pediatria", "ortopedia"],
        "consultorio": ["clinica_medica", "pediatria", "dermatologia", "psiquiatria",
                        "cardiologia", "gineco_obst", "ortopedia", "oftalmologia",
                        "urologia", "gastro", "neurologia"],
    }
    for tipo, qtd in PRESTADOR_TIPOS:
        for _ in range(qtd):
            slug = espec_por_tipo[tipo][rng.integers(0, len(espec_por_tipo[tipo]))]
            if tipo == "hospital":
                nome = f"Hospital {next(hosp_iter, 'Municipal ' + str(rng.integers(1, 99)))}"
            elif tipo == "pronto_atendimento":
                nome = f"Pronto Atendimento {next(clin_iter, str(rng.integers(1, 99)))}"
            elif tipo == "laboratorio":
                nome = f"Laboratório {next(clin_iter, str(rng.integers(1, 99)))}"
            elif tipo == "consultorio":
                nome = f"Consultório {next(clin_iter, str(rng.integers(1, 99)))}"
            else:
                nome = f"Clínica {next(clin_iter, str(rng.integers(1, 99)))}"
            nivel = float(np.clip(rng.normal(1.0, 0.10), 0.82, 1.30))
            rows.append(
                Prestador(
                    tenant_id=tenant_id, nome_ficticio=nome, tipo_prestador=tipo,
                    id_regiao=int(rng.choice(regiao_ids)),
                    id_especialidade_principal=espec_id[slug],
                    nivel_preco=round(nivel, 3),
                )
            )
    return rows


# ----------------------------------------------------------------------------------
# Carteira
# ----------------------------------------------------------------------------------
@dataclass
class Carteira:
    ids: np.ndarray
    idade_ym: np.ndarray        # ano*12+mes de nascimento
    sexo: np.ndarray            # 'M'/'F'
    plano_pos: np.ndarray       # índice em cat.plano_ids
    regiao_id: np.ndarray
    adesao_ym: np.ndarray
    saida_ym: np.ndarray        # 999999 se ativo
    contrato_id: np.ndarray = field(default_factory=lambda: np.array([]))  # v1.2 — id do contrato por beneficiário
    scenario_flags: dict = field(default_factory=dict)


def generate_beneficiarios(
    session: Session, cfg: SeedConfig, rng: np.random.Generator, cat: Catalogo
) -> Carteira:
    n = cfg.n_beneficiarios
    # idade
    faixa_idx = rng.choice(
        len(aff.AGE_PYRAMID), size=n,
        p=np.array([w for (_a, _b, w) in aff.AGE_PYRAMID]) /
        sum(w for (_a, _b, w) in aff.AGE_PYRAMID),
    )
    lo = np.array([a for (a, _b, _w) in aff.AGE_PYRAMID])[faixa_idx]
    hi = np.array([b for (_a, b, _w) in aff.AGE_PYRAMID])[faixa_idx]
    idade = (lo + rng.random(n) * (hi - lo + 1)).astype(int)
    idade = np.clip(idade, 0, 95)

    hoje = cfg.inicio
    nasc_ym = (hoje.year * 12 + hoje.month) - idade * 12 - rng.integers(0, 12, n)

    sexo = np.where(rng.random(n) < aff.SEXO_PESOS["F"], "F", "M")
    plano_pos = rng.choice(len(cat.plano_ids), size=n)
    regiao_id = rng.choice(cat.regiao_ids, size=n, p=cat.regiao_pesos)

    inicio_ym = cfg.inicio.year * 12 + cfg.inicio.month
    fim_ym = cfg.fim.year * 12 + cfg.fim.month
    # 82% já ativos antes do início; demais aderem ao longo do período
    ja_ativo = rng.random(n) < 0.82
    adesao_ym = np.where(
        ja_ativo,
        inicio_ym - rng.integers(1, 60, n),
        inicio_ym + rng.integers(0, fim_ym - inicio_ym, n),
    )
    # churn: ~0.6%/mês -> tempo até saída ~ geométrico; maioria sem saída no período
    saida_ym = np.full(n, 999_999)
    sai = rng.random(n) < 0.11
    dur = rng.integers(3, fim_ym - inicio_ym + 6, n)
    saida_ym = np.where(sai, np.maximum(adesao_ym + dur, inicio_ym + 2), 999_999)

    # id_contrato: escolha ponderada pelo tamanho-alvo (vidas_alvo) do contrato, dentro do
    # plano do beneficiário (v1.2). RNG DEDICADO — não perturba o stream principal usado
    # pela geração de eventos e pelos cenários (S1–S13 permanecem calibrados).
    crng = np.random.default_rng(cfg.seed + 909)
    contrato_ids = np.zeros(n, dtype=np.int64)
    for pos, pid in enumerate(cat.plano_ids):
        mask = plano_pos == pos
        m = int(mask.sum())
        if m == 0:
            continue
        opts = np.asarray(cat.contrato_por_plano[pid], dtype=np.int64)
        probs = cat.contrato_pesos_por_plano[pid]
        contrato_ids[mask] = crng.choice(opts, size=m, p=probs if probs.size else None)

    # persistência
    rows = []
    dnasc = []
    for i in range(n):
        ny, nm = divmod(int(nasc_ym[i]) - 1, 12)
        nm += 1
        bd = date(ny, nm, int(rng.integers(1, 28)))
        dnasc.append(bd)
        ay, am = divmod(int(adesao_ym[i]) - 1, 12)
        am += 1
        adesao = date(ay, am, 1)
        saida = None
        if saida_ym[i] != 999_999:
            sy, sm = divmod(int(saida_ym[i]) - 1, 12)
            sm += 1
            saida = date(sy, sm, 1)
        idade_hoje = (cfg.inicio.year * 12 + cfg.inicio.month - int(nasc_ym[i])) // 12
        rows.append(
            {
                "codigo": f"BEN-{i + 1:06d}",
                "sexo": str(sexo[i]),
                "data_nascimento": bd,
                "faixa_etaria": faixa_etaria(int(idade_hoje)),
                "id_regiao": int(regiao_id[i]),
                "id_plano": int(cat.plano_ids[int(plano_pos[i])]),
                "id_contrato": int(contrato_ids[i]),
                "data_adesao": adesao,
                "data_saida": saida,
                "status": "ativo" if saida is None or saida > cfg.fim else "inativo",
            }
        )
    _bulk_insert(session, Beneficiario, rows, cfg.chunk, tenant_id=cfg.tenant_id)
    ids = np.array(session.execute(select(Beneficiario.id).order_by(Beneficiario.id)).scalars().all())

    return Carteira(
        ids=ids, idade_ym=nasc_ym, sexo=sexo, plano_pos=plano_pos, regiao_id=regiao_id,
        adesao_ym=adesao_ym, saida_ym=saida_ym, contrato_id=contrato_ids,
    )


# ----------------------------------------------------------------------------------
# Eventos
# ----------------------------------------------------------------------------------
def generate_eventos(
    session: Session,
    cfg: SeedConfig,
    rng: np.random.Generator,
    cat: Catalogo,
    cart: Carteira,
    scenario_hooks: list | None = None,
    owned_proc_ids: set[int] | None = None,
    blocked_prestador_ids: set[int] | None = None,
    glosa_mult_por_mes: dict[date, float] | None = None,
    copart_mult_por_mes: dict[date, float] | None = None,
) -> dict:
    """Gera eventos mês a mês. Retorna estatísticas (despesa/exposição por competência).

    `owned_proc_ids`: procedimentos gerados exclusivamente por cenários — a geração
    orgânica não os seleciona (para que o efeito plantado domine o agregado).
    `blocked_prestador_ids`: prestadores que a geração orgânica não usa (cenário de
    prestador fora do padrão — só recebe eventos do hook).
    `glosa_mult_por_mes` / `copart_mult_por_mes`: multiplicadores da taxa média de glosa
    e do percentual de coparticipação, por competência (cenários financeiros A-D).
    """
    meses = cfg.competencias()
    scenario_hooks = scenario_hooks or []
    owned_proc_ids = owned_proc_ids or set()
    blocked_arr = np.array(sorted(blocked_prestador_ids or []), dtype=np.int64)
    glosa_mult_por_mes = glosa_mult_por_mes or {}
    copart_mult_por_mes = copart_mult_por_mes or {}

    proc_ids_by_espec = {s: np.array([p["id"] for p in cat.proc_by_espec[s]]) for s in ESPEC_SLUGS}
    proc_custo_by_espec = {
        s: np.array([p["custo_base"] for p in cat.proc_by_espec[s]]) for s in ESPEC_SLUGS
    }
    proc_peso_by_espec = {
        s: np.array([
            1e-9 if p["id"] in owned_proc_ids else p["peso"]
            for p in cat.proc_by_espec[s]
        ])
        for s in ESPEC_SLUGS
    }
    proc_imin_by_espec = {
        s: np.array([p["idade_min"] for p in cat.proc_by_espec[s]]) for s in ESPEC_SLUGS
    }
    proc_imax_by_espec = {
        s: np.array([p["idade_max"] for p in cat.proc_by_espec[s]]) for s in ESPEC_SLUGS
    }
    proc_tipo_by_espec = {
        s: np.array([p["tipo"] for p in cat.proc_by_espec[s]], dtype=object)
        for s in ESPEC_SLUGS
    }
    THERAPY = {"FIS-SES", "FIS-RPG", "PSI-PSICO", "FON-SES", "TO-SES", "ONC-QT1",
               "ONC-QT2", "ONC-RXT", "NEF-HD"}
    proc_is_therapy_by_espec = {
        s: np.array([p["codigo"] in THERAPY for p in cat.proc_by_espec[s]])
        for s in ESPEC_SLUGS
    }

    despesa_mes: dict[date, float] = {}
    expo_mes: dict[date, int] = {}
    ativos_plano_mes: dict[tuple[date, int], int] = {}
    total_eventos = 0

    for t in meses:
        t_ym = t.year * 12 + t.month
        ativo = (cart.adesao_ym <= t_ym) & (cart.saida_ym > t_ym)
        a_idx = np.nonzero(ativo)[0]
        A = a_idx.size
        expo_mes[t] = A
        idade = ((t_ym - cart.idade_ym[a_idx]) // 12).astype(int)
        sexo = cart.sexo[a_idx]

        # ativos por plano (para receita)
        for pos, pid in enumerate(cat.plano_ids):
            ativos_plano_mes[(t, pid)] = int(np.sum(cart.plano_pos[a_idx] == pos))

        # taxa mensal + ruído sazonal leve
        lam = aff.taxa_anual_base(idade) / 12.0 * cfg.escala_eventos
        lam *= 1.0 + 0.02 * rng.standard_normal(A)
        lam = np.clip(lam, 0.02, 2.5)
        n_ev = rng.poisson(lam)

        # hooks de cenário podem ajustar n_ev / seleção — aplicados adiante
        ev_benef_pos = np.repeat(np.arange(A), n_ev)
        E = ev_benef_pos.size
        if E == 0:
            despesa_mes[t] = 0.0
            continue

        ev_idade = idade[ev_benef_pos]
        ev_sexo = sexo[ev_benef_pos]

        # especialidade via Gumbel-max
        pesos_esp = aff.pesos_especialidade(ev_idade.astype(float), ev_sexo)
        W = np.stack([np.clip(pesos_esp[s], 1e-6, None) for s in ESPEC_SLUGS], axis=1)
        g = rng.gumbel(size=W.shape)
        esp_choice = np.argmax(np.log(W) + g, axis=1)

        # arrays de saída
        out_proc = np.zeros(E, dtype=np.int64)
        out_prest = np.zeros(E, dtype=np.int64)
        out_espid = np.zeros(E, dtype=np.int64)
        out_tipo = np.empty(E, dtype=object)
        out_qtd = np.ones(E, dtype=np.int64)
        out_apres = np.zeros(E, dtype=float)
        out_diag = np.zeros(E, dtype=object)
        ev_benef_id = cart.ids[a_idx][ev_benef_pos]
        ev_regiao = cart.regiao_id[a_idx][ev_benef_pos]
        ev_cenario = np.empty(E, dtype=object)
        ev_cenario[:] = None

        for si, slug in enumerate(ESPEC_SLUGS):
            mask = esp_choice == si
            m = np.nonzero(mask)[0]
            if m.size == 0:
                continue
            out_espid[m] = cat.espec_id[slug]
            pid_arr = proc_ids_by_espec[slug]
            pw = proc_peso_by_espec[slug]
            imin = proc_imin_by_espec[slug]
            imax = proc_imax_by_espec[slug]
            age_m = ev_idade[m][:, None]
            wmat = np.broadcast_to(pw, (m.size, pw.size)).copy()
            wmat[(age_m < imin[None, :]) | (age_m > imax[None, :])] = 1e-9
            gp = rng.gumbel(size=wmat.shape)
            pc = np.argmax(np.log(np.clip(wmat, 1e-9, None)) + gp, axis=1)
            out_proc[m] = pid_arr[pc]
            out_tipo[m] = proc_tipo_by_espec[slug][pc]
            # quantidade para terapias
            is_th = proc_is_therapy_by_espec[slug][pc]
            q = np.where(is_th, rng.integers(1, 4, m.size), 1)
            out_qtd[m] = q
            # valor apresentado
            custo = proc_custo_by_espec[slug][pc]
            prest = cat.prest_by_espec[slug]
            # prestador: mesma região pesa 3x
            same = (prest["regiao"][None, :] == ev_regiao[m][:, None]).astype(float)
            wprov = 1.0 + 2.0 * same
            if blocked_arr.size:
                wprov[:, np.isin(prest["ids"], blocked_arr)] = 1e-9
            gpr = rng.gumbel(size=wprov.shape)
            prc = np.argmax(np.log(wprov) + gpr, axis=1)
            out_prest[m] = prest["ids"][prc]
            nivel = prest["nivel"][prc]
            ruido = rng.lognormal(mean=0.0, sigma=0.16, size=m.size)
            out_apres[m] = custo * nivel * ruido * q
            # diagnóstico
            dlist = cat.diag_by_espec.get(slug, [])
            if dlist:
                take = rng.random(m.size) < 0.62
                dd = np.where(take, rng.choice(dlist, size=m.size), 0)
                out_diag[m] = [int(x) if x else None for x in dd]
            else:
                out_diag[m] = [None] * m.size

        # ---- hooks de cenário (Etapa 4) ----
        ctx = dict(
            t=t, rng=rng, cat=cat, cart=cart, a_idx=a_idx, idade=idade, sexo=sexo,
            ev_benef_pos=ev_benef_pos, ev_benef_id=ev_benef_id, ev_idade=ev_idade,
            ev_regiao=ev_regiao, out_proc=out_proc, out_prest=out_prest,
            out_espid=out_espid, out_tipo=out_tipo, out_qtd=out_qtd, out_apres=out_apres,
            out_diag=out_diag, ev_cenario=ev_cenario, espec_id=cat.espec_id,
        )
        extra_rows: list[dict] = []
        for hook in scenario_hooks:
            res = hook(ctx)
            if res:
                extra_rows.extend(res)

        # glosa e pago
        glosa_mult = glosa_mult_por_mes.get(t, 1.0)
        glosa_frac = np.clip(rng.normal(0.03 * glosa_mult, 0.03, E), 0.0, 0.35)
        apres = np.round(out_apres, 2)
        glosado = np.round(apres * glosa_frac, 2)
        pago = np.round(apres - glosado, 2)

        # coparticipação: incide sobre valor_pago, só em tipos tipicamente sujeitos a
        # copay, conforme o plano do beneficiário (Etapa B da v1.1).
        copart_mult = copart_mult_por_mes.get(t, 1.0)
        ev_plano_pos = cart.plano_pos[a_idx][ev_benef_pos]
        pct_copart = cat.plano_copart_pct[ev_plano_pos] * copart_mult
        elegivel_copart = np.isin(out_tipo, ["consulta", "exame", "terapia", "pronto_socorro"])
        coparticipacao = np.round(np.where(elegivel_copart, pago * pct_copart, 0.0), 2)

        last_day = calendar.monthrange(t.year, t.month)[1]
        dias = rng.integers(1, last_day + 1, E)

        rows = []
        for i in range(E):
            rows.append(
                {
                    "id_beneficiario": int(ev_benef_id[i]),
                    "id_prestador": int(out_prest[i]),
                    "id_procedimento": int(out_proc[i]),
                    "id_especialidade": int(out_espid[i]),
                    "id_diagnostico": out_diag[i],
                    "id_regiao": int(ev_regiao[i]),
                    "data_evento": date(t.year, t.month, int(dias[i])),
                    "competencia": t,
                    "tipo_atendimento": str(out_tipo[i]),
                    "quantidade": int(out_qtd[i]),
                    "valor_apresentado": float(apres[i]),
                    "valor_glosado": float(glosado[i]),
                    "valor_pago": float(pago[i]),
                    "valor_coparticipacao": float(coparticipacao[i]),
                    "cenario_tag": ev_cenario[i],
                }
            )
        rows.extend(extra_rows)
        _bulk_insert(session, EventoAssistencial, rows, cfg.chunk, tenant_id=cfg.tenant_id)
        total_eventos += len(rows)
        despesa_mes[t] = float(sum(r["valor_pago"] for r in rows))

    return {
        "despesa_mes": despesa_mes,
        "expo_mes": expo_mes,
        "ativos_plano_mes": ativos_plano_mes,
        "total_eventos": total_eventos,
    }


# ----------------------------------------------------------------------------------
# Receita (calibrada para a sinistralidade-alvo do baseline)
# ----------------------------------------------------------------------------------
def generate_receitas(
    session: Session, cfg: SeedConfig, rng: np.random.Generator, cat: Catalogo, stats: dict,
    suprimir_reajuste: set[int] | None = None,
    receita_ajuste_pontual: dict[date, float] | None = None,
) -> None:
    meses = cfg.competencias()
    suprimir_reajuste = suprimir_reajuste or set()
    receita_ajuste_pontual = receita_ajuste_pontual or {}

    despesa_total = sum(stats["despesa_mes"].values())
    expo_total = sum(stats["expo_mes"].values()) or 1
    # receita PMPM alvo para que despesa/receita média ~ sinistralidade_alvo
    pmpm_receita_alvo = despesa_total / expo_total / cfg.sinistralidade_alvo

    ticket_rel = cat.plano_ticket / cat.plano_ticket.mean()

    reajuste_acumulado = 1.0
    rows = []
    for t in meses:
        if t.month == 5 and t.year > cfg.inicio.year and t.year not in suprimir_reajuste:
            reajuste_acumulado *= 1.11
        for pos, pid in enumerate(cat.plano_ids):
            ativos = stats["ativos_plano_mes"].get((t, pid), 0)
            if ativos == 0:
                continue
            receita = (
                ativos
                * pmpm_receita_alvo
                * ticket_rel[pos]
                * reajuste_acumulado
                * (1.0 + 0.01 * rng.standard_normal())
                * receita_ajuste_pontual.get(t, 1.0)
            )
            rows.append(
                {
                    "competencia": t,
                    "id_plano": int(pid),
                    "quantidade_beneficiarios": int(ativos),
                    "receita_contraprestacao": round(float(receita), 2),
                }
            )
    _bulk_insert(session, Receita, rows, cfg.chunk, tenant_id=cfg.tenant_id)


# ----------------------------------------------------------------------------------
# util
# ----------------------------------------------------------------------------------
def new_event_row(
    *,
    t: date,
    rng: np.random.Generator,
    id_beneficiario: int,
    id_prestador: int,
    id_procedimento: int,
    id_especialidade: int,
    id_regiao: int,
    tipo_atendimento: str,
    custo: float,
    nivel_preco: float,
    cenario_tag: str,
    quantidade: int = 1,
    fator_custo: float = 1.0,
    id_diagnostico: int | None = None,
    sigma: float = 0.12,
    percentual_coparticipacao: float = 0.0,
) -> dict:
    """Constrói uma linha completa de evento (com glosa/pago/coparticipação) para
    injeção por cenário."""
    apres = round(custo * nivel_preco * fator_custo * quantidade * rng.lognormal(0.0, sigma), 2)
    glosa = round(apres * float(np.clip(rng.normal(0.03, 0.03), 0.0, 0.15)), 2)
    pago = round(apres - glosa, 2)
    copart = round(pago * percentual_coparticipacao, 2) if percentual_coparticipacao else 0.0
    last_day = calendar.monthrange(t.year, t.month)[1]
    return {
        "id_beneficiario": int(id_beneficiario),
        "id_prestador": int(id_prestador),
        "id_procedimento": int(id_procedimento),
        "id_especialidade": int(id_especialidade),
        "id_diagnostico": id_diagnostico,
        "id_regiao": int(id_regiao),
        "data_evento": date(t.year, t.month, int(rng.integers(1, last_day + 1))),
        "competencia": t,
        "tipo_atendimento": tipo_atendimento,
        "quantidade": int(quantidade),
        "valor_apresentado": apres,
        "valor_glosado": glosa,
        "valor_pago": pago,
        "valor_coparticipacao": copart,
        "cenario_tag": cenario_tag,
    }


def _bulk_insert(
    session: Session, model, rows: list[dict], chunk: int, tenant_id: str | None = None
) -> None:
    """Inserção em massa via COPY FROM STDIN (psycopg3) — ordens de grandeza mais rápido
    que executemany em conexões com latência (ex.: port-forward do Docker Desktop).

    `tenant_id`: quando informado, carimba `tenant_id` em toda linha (v1.2). Fica fora
    do dict de negócio para não poluir a lógica de geração.
    """
    if not rows:
        return
    if tenant_id is not None:
        rows = [{"tenant_id": tenant_id, **r} for r in rows]
    # Fase 2: linhagem do caminho sintético (mesmos metadados de qualquer fonte externa)
    linhagem = session.info.get("w2h_lineage")
    if linhagem:
        rows = [{**r, **linhagem} for r in rows]
    table = model.__tablename__
    cols = list(rows[0].keys())
    col_list = ", ".join(cols)
    raw = session.connection().connection  # DBAPI (psycopg3)
    with raw.cursor() as cur, cur.copy(f"COPY {table} ({col_list}) FROM STDIN") as copy:
        for row in rows:
            copy.write_row(tuple(row.get(c) for c in cols))
    session.flush()


def wipe_dados(session: Session, tenant_id: str) -> None:
    """Limpa os dados de UM tenant (ordem respeita FKs).

    Fundação SaaS V1: antes apagava todos os tenants; agora é tenant-scoped para permitir
    semear vários tenants sintéticos lado a lado. Não mexe em `alembic_version`, no control
    plane (tenants, usuários…), em `regras_alerta` (configuração do gestor) nem no
    calendário global `competencias` (compartilhado).
    """
    from app.models import AggContratoCompetencia, ReceitaContrato

    for model in (
        AggCompetenciaDimensao, AggPrestadorCompetencia, AggBeneficiarioCompetencia,
        AggContratoCompetencia, AggSinistralidadeCompetencia, ReceitaContrato,
        EventoAssistencial, Receita, Beneficiario, PlanoContrato,
        Prestador, Procedimento, Diagnostico, Especialidade, Contrato, Plano,
        Regiao, CenarioGabarito, SeedManifest,
    ):
        session.execute(delete(model).where(model.tenant_id == tenant_id))
    session.flush()
