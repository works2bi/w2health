"""Gera um PACOTE DE INTEGRAÇÃO sintético no layout "operadora genérica".

Propositalmente INDEPENDENTE do W2Health: só biblioteca padrão, nenhum import do backend,
nomes de coluna que NÃO são os do modelo canônico (`matricula`, `dt_atendimento`,
`tipo_guia`, `vl_glosa`...), datas dd/mm/aaaa e decimal com vírgula. Simula o que um
cliente exportaria do próprio sistema — o W2Health só entende esses arquivos através do
mapping `data_platform/mappings/generic_operator_v1.yaml`.

Todos os dados são FICTÍCIOS (nenhuma pessoa, prestador ou operadora real).

Uso:
    python generate_package.py --out ./pacote --beneficiarios 1000 --seed 2026
    python generate_package.py --out ./pacote_b --beneficiarios 600 --seed 7 --sem-receitas
"""

from __future__ import annotations

import argparse
import csv
import math
import random
from datetime import date, timedelta
from pathlib import Path

ESPECIALIDADES = [
    ("ESP-CLIN", "Clínica Médica", "clinica"), ("ESP-CARD", "Cardiologia", "clinica"),
    ("ESP-ORTO", "Ortopedia", "cirurgica"), ("ESP-PED", "Pediatria", "clinica"),
    ("ESP-GINE", "Ginecologia e Obstetrícia", "clinica"), ("ESP-ONCO", "Oncologia", "clinica"),
    ("ESP-IMG", "Diagnóstico por Imagem", "diagnostico"), ("ESP-LAB", "Patologia Clínica", "diagnostico"),
    ("ESP-FISIO", "Fisioterapia", "terapia"), ("ESP-CIRG", "Cirurgia Geral", "cirurgica"),
]
# codigo, descricao, especialidade, grupo, perfil, tipo_guia, custo_base
PROCEDIMENTOS = [
    ("P-1001", "Consulta clínica eletiva", "ESP-CLIN", "Consultas", "recorrente", "1", 180),
    ("P-1002", "Consulta cardiológica", "ESP-CARD", "Consultas", "recorrente", "1", 260),
    ("P-1003", "Consulta ortopédica", "ESP-ORTO", "Consultas", "recorrente", "1", 240),
    ("P-1004", "Consulta pediátrica", "ESP-PED", "Consultas", "recorrente", "1", 200),
    ("P-1005", "Consulta ginecológica", "ESP-GINE", "Consultas", "recorrente", "1", 220),
    ("P-2001", "Hemograma completo", "ESP-LAB", "Exames laboratoriais", "recorrente", "2", 35),
    ("P-2002", "Painel bioquímico", "ESP-LAB", "Exames laboratoriais", "recorrente", "2", 90),
    ("P-2003", "Ecocardiograma", "ESP-CARD", "Exames de imagem", "variavel", "2", 520),
    ("P-2004", "Teste ergométrico", "ESP-CARD", "Exames de imagem", "variavel", "2", 380),
    ("P-2005", "Ressonância magnética", "ESP-IMG", "Exames de imagem", "variavel", "2", 1400),
    ("P-2006", "Tomografia computadorizada", "ESP-IMG", "Exames de imagem", "variavel", "2", 950),
    ("P-2007", "Ultrassonografia", "ESP-IMG", "Exames de imagem", "variavel", "2", 280),
    ("P-3001", "Sessão de fisioterapia", "ESP-FISIO", "Terapias", "recorrente", "5", 110),
    ("P-3002", "Quimioterapia ambulatorial", "ESP-ONCO", "Quimioterapia e alto custo", "recorrente", "5", 9800),
    ("P-4001", "Atendimento de pronto-socorro", "ESP-CLIN", "Pronto-socorro", "variavel", "4", 650),
    ("P-5001", "Internação clínica (diária)", "ESP-CLIN", "Internações clínicas", "pontual", "3", 3200),
    ("P-6001", "Artroscopia de joelho", "ESP-ORTO", "Cirurgias", "pontual", "6", 14500),
    ("P-6002", "Colecistectomia videolaparoscópica", "ESP-CIRG", "Cirurgias", "pontual", "6", 16800),
    ("P-6003", "Parto cesáreo", "ESP-GINE", "Obstetrícia", "pontual", "6", 9200),
    ("P-7001", "Prótese ortopédica (OPME)", "ESP-ORTO", "OPME", "pontual", "7", 21000),
]
CIDADES = [("São Paulo", "SP", 0.5), ("Campinas", "SP", 0.25), ("Belo Horizonte", "MG", 0.25)]
PLANOS = [  # codigo, nome, segmentacao, copart S/N, percentual (vírgula), ticket
    ("PL-ESSENCIAL", "Essencial Ambulatorial", "ambulatorial", "S", "0,2000", 310),
    ("PL-PADRAO", "Padrão Enfermaria", "completo", "S", "0,1000", 520),
    ("PL-SUPERIOR", "Superior Apartamento", "completo", "N", "0,0000", 780),
    ("PL-EMPRESA", "Empresarial Coletivo", "completo", "N", "0,0000", 610),
]
TIPOS_PREST = ["hospital", "clinica", "laboratorio", "pronto_atendimento", "consultorio"]
COPART_TIPOS = {"1", "2", "4", "5"}  # consulta, exame, PS, terapia


def br(v: float) -> str:
    return f"{v:.2f}".replace(".", ",")


def dbr(d: date) -> str:
    return d.strftime("%d/%m/%Y")


def meses(inicio: date, n: int) -> list[date]:
    out, y, m = [], inicio.year, inicio.month
    for _ in range(n):
        out.append(date(y, m, 1))
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def gerar(out: Path, n_benef: int, seed: int, inicio: date, n_meses: int, com_receitas: bool) -> dict:
    rnd = random.Random(seed)
    out.mkdir(parents=True, exist_ok=True)
    comps = meses(inicio, n_meses)
    fim = comps[-1]

    def w(nome, cab, linhas):
        with open(out / f"{nome}.csv", "w", newline="", encoding="utf-8") as f:
            wr = csv.writer(f, delimiter=";")
            wr.writerow(cab)
            wr.writerows(linhas)
        return len(linhas)

    contagem = {}
    contagem["especialidades"] = w("especialidades", ["cod_especialidade", "nome_especialidade", "grupo"],
                                   ESPECIALIDADES)
    contagem["planos"] = w("planos", ["cod_plano", "nome_plano", "segmentacao", "coparticipacao",
                                      "perc_coparticipacao"], [p[:5] for p in PLANOS])
    # contrato × plano é N:N: os contratos empresariais oferecem também o plano superior
    contratos, planos_contrato = [], {}
    for i in range(1, 11):
        plano = PLANOS[(i - 1) % len(PLANOS)][0]
        tipo = "PF" if plano in ("PL-ESSENCIAL",) else ("Empresarial" if plano == "PL-EMPRESA" else "PME")
        cod = f"CT-{i:03d}"
        contratos.append((cod, f"Contrato {i:03d}", tipo))
        planos_contrato[cod] = [plano, "PL-SUPERIOR"] if plano == "PL-EMPRESA" else [plano]
    contagem["contratos"] = w("contratos", ["cod_contrato", "descricao_contrato", "tipo_contrato"],
                              contratos)
    contagem["plano_contratos"] = w("plano_contratos", ["cod_contrato", "cod_plano"],
                                    [(c, p) for c, ps in planos_contrato.items() for p in ps])
    prestadores = []
    esp_cods = [e[0] for e in ESPECIALIDADES]
    for i in range(1, 31):
        cidade, uf, _ = CIDADES[i % len(CIDADES)]
        esp = esp_cods[i % len(esp_cods)]
        prestadores.append((f"PR-{i:04d}", f"Prestador Fictício {i:02d}", TIPOS_PREST[i % len(TIPOS_PREST)],
                            cidade, uf, esp))
    contagem["prestadores"] = w("prestadores", ["cod_prestador", "razao_social", "tipo", "municipio", "uf",
                                                "cod_especialidade_principal"], prestadores)
    contagem["procedimentos"] = w("procedimentos", ["cod_procedimento", "descricao", "cod_especialidade",
                                                    "grupo", "perfil"], [p[:5] for p in PROCEDIMENTOS])

    # beneficiários
    benef = []
    for i in range(1, n_benef + 1):
        idade = max(0, min(90, int(rnd.gauss(38, 19))))
        nasc = date(inicio.year - idade, rnd.randint(1, 12), rnd.randint(1, 28))
        ct = contratos[rnd.randrange(len(contratos))]
        opcoes = planos_contrato[ct[0]]
        plano = opcoes[i % len(opcoes)]  # determinístico — não consome o rnd
        cidade, uf, _ = rnd.choices(CIDADES, weights=[c[2] for c in CIDADES])[0]
        adesao = inicio - timedelta(days=rnd.randint(30, 2500)) if rnd.random() < 0.85 else \
            comps[rnd.randrange(len(comps))] + timedelta(days=rnd.randint(0, 20))
        saida = None
        if rnd.random() < 0.08:
            saida = comps[rnd.randrange(len(comps))] + timedelta(days=rnd.randint(0, 25))
            if saida <= adesao:
                saida = None
        benef.append({"cod": f"BEN-{i:06d}", "sexo": rnd.choice(["Feminino", "Masculino"]), "nasc": nasc,
                      "plano": plano, "contrato": ct[0], "cidade": cidade, "uf": uf,
                      "adesao": adesao, "saida": saida, "idade": idade})
    contagem["beneficiarios"] = w(
        "beneficiarios", ["matricula", "sexo", "dt_nascimento", "cod_plano", "cod_contrato", "municipio",
                          "uf", "dt_adesao", "dt_cancelamento"],
        [(b["cod"], b["sexo"], dbr(b["nasc"]), b["plano"], b["contrato"], b["cidade"], b["uf"],
          dbr(b["adesao"]), dbr(b["saida"]) if b["saida"] else "") for b in benef])

    # eventos
    prest_por_esp: dict[str, list[str]] = {}
    for p in prestadores:
        prest_por_esp.setdefault(p[5], []).append(p[0])
    copart_plano = {p[0]: float(p[4].replace(",", ".")) for p in PLANOS}
    eventos, n = [], 0
    despesa_mes = {c: 0.0 for c in comps}
    ativos_plano = {(c, p[0]): 0 for c in comps for p in PLANOS}
    for c in comps:
        for b in benef:
            ativo = b["adesao"] <= c + timedelta(days=27) and (b["saida"] is None or b["saida"] > c)
            if not ativo:
                continue
            ativos_plano[(c, b["plano"])] += 1
            taxa = 0.55 + 0.012 * abs(b["idade"] - 30)
            k = sum(1 for _ in range(6) if rnd.random() < taxa / 6)
            for _ in range(k):
                proc = rnd.choices(PROCEDIMENTOS, weights=[14, 5, 4, 4, 4, 12, 7, 2, 2, 1.2, 1.2, 3,
                                                           5, 0.08, 2.5, 0.6, 0.15, 0.12, 0.1, 0.06])[0]
                prest_lista = prest_por_esp.get(proc[2]) or [prestadores[0][0]]
                prest = rnd.choice(prest_lista)
                custo = proc[6] * math.exp(rnd.gauss(0, 0.15))
                # prestador fictício com custo médio acima dos pares a partir de jan/2026
                if prest == "PR-0002" and c >= date(2026, 1, 1):
                    custo *= 1.4
                qtd = 1 if proc[5] != "3" else rnd.randint(1, 5)
                apresentado = round(custo * qtd, 2)
                glosa = round(apresentado * max(0.0, min(0.15, rnd.gauss(0.03, 0.03))), 2)
                pago = round(apresentado - glosa, 2)
                copart = round(pago * copart_plano[b["plano"]], 2) if proc[5] in COPART_TIPOS else 0.0
                dia = c + timedelta(days=rnd.randint(0, 27))
                n += 1
                eventos.append((f"CTA-{c:%Y%m}-{n:07d}", b["cod"], prest, proc[0], dbr(dia), proc[5],
                                qtd, br(apresentado), br(glosa), br(pago), br(copart)))
                despesa_mes[c] += apresentado - glosa - copart
            # tendência plantada (frequência): mais exames cardiológicos a partir de mar/2026
            if c >= date(2026, 3, 1) and b["idade"] >= 45 and rnd.random() < 0.09:
                proc = PROCEDIMENTOS[7] if rnd.random() < 0.5 else PROCEDIMENTOS[8]
                prest = rnd.choice(prest_por_esp.get(proc[2]) or [prestadores[0][0]])
                apresentado = round(proc[6] * math.exp(rnd.gauss(0, 0.12)), 2)
                glosa = round(apresentado * 0.03, 2)
                pago = round(apresentado - glosa, 2)
                copart = round(pago * copart_plano[b["plano"]], 2)
                n += 1
                eventos.append((f"CTA-{c:%Y%m}-{n:07d}", b["cod"], prest, proc[0],
                                dbr(c + timedelta(days=rnd.randint(0, 27))), "2", 1, br(apresentado),
                                br(glosa), br(pago), br(copart)))
                despesa_mes[c] += apresentado - glosa - copart
    contagem["eventos"] = w("eventos", ["id_item", "matricula", "cod_prestador", "cod_procedimento",
                                        "dt_atendimento", "tipo_guia", "qtd", "vl_apresentado", "vl_glosa",
                                        "vl_pago", "vl_coparticipacao"], eventos)

    # receitas — mensalidade por plano × vidas ativas, calibrada para sinistralidade ~78%
    if com_receitas:
        base = sum(despesa_mes[c] for c in comps[:6]) / 6
        vidas_base = sum(ativos_plano[(c, p[0])] * p[5] for c in comps[:6] for p in PLANOS) / 6
        fator = base / 0.78 / max(vidas_base, 1)
        receitas = []
        for c in comps:
            reajuste = 1.0 if c < date(2026, 5, 1) else 1.12
            for p in PLANOS:
                vidas = ativos_plano[(c, p[0])]
                if vidas:
                    receitas.append((f"{c:%Y-%m}", p[0], vidas,
                                     br(vidas * p[5] * fator * reajuste * rnd.uniform(0.99, 1.01))))
        contagem["receitas"] = w("receitas", ["competencia", "cod_plano", "qtd_vidas", "valor_contraprestacao"],
                                 receitas)
    return contagem


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", required=True)
    ap.add_argument("--beneficiarios", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--inicio", default="2025-01")
    ap.add_argument("--meses", type=int, default=18)
    ap.add_argument("--sem-receitas", action="store_true", help="simula cliente sem dado de receita")
    a = ap.parse_args()
    y, m = a.inicio.split("-")
    c = gerar(Path(a.out), a.beneficiarios, a.seed, date(int(y), int(m), 1), a.meses, not a.sem_receitas)
    print({k: v for k, v in c.items()})


if __name__ == "__main__":
    main()
