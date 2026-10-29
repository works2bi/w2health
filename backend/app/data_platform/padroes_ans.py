"""Leitura dos padrões ANS → pacote canônico (Evolução, item 6).

* **TISS** (XML `mensagemTISS` com `loteGuias`): guias de consulta, SP/SADT e resumo de
  internação viram EVENTOS (um por procedimento executado), mais os cadastros que a guia
  carrega — prestador executante, procedimento (TUSS) e especialidade (CBO do profissional).
  A data do lote (`dataRegistroTransacao`) é a data de APRESENTAÇÃO da conta — é ela que
  alimenta a completude da competência (item 1).
* **SIB** (XML de beneficiários): beneficiários, planos (registro ANS do produto) e contratos
  (CNPJ da contratante; PF = contrato individual). Nome, CPF, CNS e nome da mãe NÃO são lidos.
* **DIOPS** (balancete trimestral em CSV `conta;descricao;valor`): não é carga — é CONFERÊNCIA
  da receita de contraprestações (conta 311) e dos eventos indenizáveis (conta 411) contra a
  camada analítica do mesmo trimestre.

O resultado do TISS + SIB é um pacote de CSVs no mapping `ans_padroes_v1`, que segue o
pipeline normal (mapping → DQ → Silver → Gold → reconciliação). Nada é gravado aqui.

Segurança: XML com DOCTYPE/ENTITY é recusado (sem expansão de entidades nem DTD externa).
Leitores tolerantes a namespace e às variações de versão (busca pelo nome local da tag).
"""

from __future__ import annotations

import csv
import io
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.tenant_scope import tenant_of

MAPPING_ID, MAPPING_VERSION = "ans_padroes", 1
ESPEC_NAO_INFORMADA = ("NI", "Não informada", "nao_informada")

# tipo de atendimento TISS (tabela 50) → vocabulário canônico
_TIPO_TISS = {"01": "terapia", "02": "terapia", "03": "terapia", "04": "consulta", "05": "exame",
              "06": "terapia", "07": "terapia", "08": "terapia", "11": "terapia", "13": "exame",
              "21": "exame", "22": "terapia", "23": "exame"}
_UF_IBGE = {"11": "RO", "12": "AC", "13": "AM", "14": "RR", "15": "PA", "16": "AP", "17": "TO",
            "21": "MA", "22": "PI", "23": "CE", "24": "RN", "25": "PB", "26": "PE", "27": "AL",
            "28": "SE", "29": "BA", "31": "MG", "32": "ES", "33": "RJ", "35": "SP", "41": "PR",
            "42": "SC", "43": "RS", "50": "MS", "51": "MT", "52": "GO", "53": "DF"}
_SEXO_SIB = {"1": "M", "3": "F", "M": "M", "F": "F"}


class PadraoANSError(ValueError):
    pass


@dataclass
class Pacote:
    tabelas: dict[str, dict[str, dict]] = field(default_factory=dict)  # entidade → chave → linha
    eventos: list[dict] = field(default_factory=list)
    rejeitos: list[dict] = field(default_factory=list)
    arquivos: list[dict] = field(default_factory=list)

    def add(self, entidade: str, chave: str, linha: dict) -> None:
        self.tabelas.setdefault(entidade, {}).setdefault(chave, linha)


# ------------------------------------------------------------------------------ XML util
def _parse(dados: bytes, nome: str) -> ET.Element:
    cabeca = dados[:4096].upper()
    if b"<!DOCTYPE" in cabeca or b"<!ENTITY" in dados.upper():
        raise PadraoANSError(f"{nome}: XML com DOCTYPE/ENTITY não é aceito")
    try:
        return ET.fromstring(dados)
    except ET.ParseError as e:
        raise PadraoANSError(f"{nome}: XML inválido ({str(e)[:80]})") from e


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _todos(el: ET.Element, nome: str) -> Iterable[ET.Element]:
    return (e for e in el.iter() if _local(e.tag) == nome)


def _um(el: ET.Element | None, *caminho: str) -> ET.Element | None:
    """Primeiro descendente que segue o caminho de nomes locais (cada passo em qualquer nível)."""
    atual = el
    for nome in caminho:
        if atual is None:
            return None
        atual = next(_todos(atual, nome), None)
    return atual


def _txt(el: ET.Element | None, *caminho: str) -> str:
    alvo = _um(el, *caminho) if caminho else el
    return (alvo.text or "").strip() if alvo is not None and alvo.text else ""


def _dec(s: str) -> Decimal:
    try:
        return Decimal(s.replace(",", ".")) if s else Decimal(0)
    except InvalidOperation as e:
        raise PadraoANSError(f"valor inválido: {s!r}") from e


def _data(s: str) -> date | None:
    try:
        return date.fromisoformat(s[:10]) if s else None
    except ValueError:
        return None


# ------------------------------------------------------------------------------ TISS
def ler_tiss(dados: bytes, nome: str, pac: Pacote) -> None:
    raiz = _parse(dados, nome)
    if _local(raiz.tag) != "mensagemTISS":
        raise PadraoANSError(f"{nome}: não é uma mensagem TISS (raiz {_local(raiz.tag)})")
    apresentacao = _data(_txt(raiz, "cabecalho", "dataRegistroTransacao"))
    lote = _txt(raiz, "numeroLote") or nome
    if apresentacao is None:
        raise PadraoANSError(f"{nome}: cabeçalho sem dataRegistroTransacao (data de apresentação)")
    pac.add("especialidades", ESPEC_NAO_INFORMADA[0], dict(zip(("codigo", "nome", "grupo"),
                                                               ESPEC_NAO_INFORMADA, strict=True)))
    n0 = len(pac.eventos)
    for guia in raiz.iter():
        tipo_guia = _local(guia.tag)
        if tipo_guia not in ("guiaConsulta", "guia-SP-SADT", "guiaSP-SADT", "guiaResumoInternacao"):
            continue
        numero = _txt(guia, "numeroGuiaPrestador") or _txt(guia, "numeroGuiaOperadora")
        carteira = _txt(guia, "dadosBeneficiario", "numeroCarteira")
        exec_ = _um(guia, "contratadoExecutante") or _um(guia, "dadosExecutante")
        prest_cod = _txt(exec_, "codigoPrestadorNaOperadora") or _txt(exec_, "cnpjContratado") \
            or _txt(exec_, "cpfContratado")
        prest_nome = _txt(exec_, "nomeContratado") or f"Prestador {prest_cod}"
        cnes = _txt(guia, "CNES") or _txt(exec_, "CNES")
        cbo = _txt(guia, "profissionalExecutante", "CBOS") or _txt(guia, "CBOS")
        esp = cbo or ESPEC_NAO_INFORMADA[0]
        if cbo:
            pac.add("especialidades", cbo, {"codigo": cbo, "nome": f"CBO {cbo}", "grupo": "cbo"})
        if not carteira or not prest_cod:
            pac.rejeitos.append({"arquivo": nome, "guia": numero,
                                 "motivo": "guia sem carteira do beneficiário ou código do prestador"})
            continue
        tipo_prest = "hospital" if tipo_guia == "guiaResumoInternacao" else (
            "clinica" if tipo_guia != "guiaConsulta" else "consultorio")
        pac.add("prestadores", prest_cod, {
            "codigo": prest_cod, "nome": prest_nome, "tipo_prestador": tipo_prest,
            "cidade": f"CNES {cnes}" if cnes else "Não informada", "uf": "NI",
            "especialidade_codigo": esp})

        if tipo_guia == "guiaConsulta":
            tipo = "consulta"
            itens = [(_um(guia, "dadosAtendimento") or guia, _txt(guia, "dataAtendimento"))]
        else:
            tipo = "internacao" if tipo_guia == "guiaResumoInternacao" else \
                _TIPO_TISS.get(_txt(guia, "tipoAtendimento"), "exame")
            itens = [(p, _txt(p, "dataExecucao")) for p in _todos(guia, "procedimentoExecutado")]
            if tipo_guia == "guiaResumoInternacao":
                inicio = _txt(guia, "dataInicioFaturamento")
                itens = [(p, d or inicio) for p, d in itens]
        for seq, (item, dia) in enumerate(itens, start=1):
            proc = _um(item, "procedimento") or item
            cod = _txt(proc, "codigoProcedimento")
            data_ev = _data(dia)
            if not cod or data_ev is None:
                pac.rejeitos.append({"arquivo": nome, "guia": numero,
                                     "motivo": "item sem código de procedimento ou data de execução"})
                continue
            tabela = _txt(proc, "codigoTabela") or "22"
            desc = _txt(proc, "descricaoProcedimento") or f"Procedimento TUSS {cod}"
            pac.add("procedimentos", cod, {"codigo": cod, "descricao": desc, "especialidade_codigo": esp,
                                           "grupo_procedimento": f"Tabela {tabela}"})
            qtd = _txt(item, "quantidadeExecutada") or "1"
            valor = _dec(_txt(item, "valorTotal") or _txt(item, "valorProcedimento"))
            pac.eventos.append({
                "source_record_id": f"{lote}:{numero}:{seq}",
                "beneficiario_codigo": carteira, "prestador_codigo": prest_cod,
                "procedimento_codigo": cod, "especialidade_codigo": esp,
                "data_evento": data_ev.isoformat(), "data_apresentacao": apresentacao.isoformat(),
                "numero_guia": numero, "tipo_atendimento": tipo,
                "quantidade": str(int(Decimal(qtd))) if qtd else "1",
                "valor_apresentado": f"{valor:.2f}", "valor_glosado": "0.00",
                "valor_pago": f"{valor:.2f}", "valor_coparticipacao": "0.00",
            })
    pac.arquivos.append({"arquivo": nome, "padrao": "TISS", "lote": lote,
                         "data_apresentacao": apresentacao.isoformat(), "eventos": len(pac.eventos) - n0})


# ------------------------------------------------------------------------------ SIB
def ler_sib(dados: bytes, nome: str, pac: Pacote) -> None:
    raiz = _parse(dados, nome)
    n = 0
    for b in _todos(raiz, "beneficiario"):
        codigo = _txt(b, "codigoBeneficiario")
        nasc = _data(_txt(b, "dataNascimento"))
        plano = _txt(b, "numeroPlanoANS") or _txt(b, "codigoPlanoOperadora")
        adesao = _data(_txt(b, "dataContratacao"))
        if not codigo or nasc is None or not plano or adesao is None:
            pac.rejeitos.append({"arquivo": nome, "beneficiario": codigo or "?",
                                 "motivo": "registro SIB sem código, nascimento, plano ou contratação"})
            continue
        cnpj = _txt(b, "cnpjEmpresaContratante")
        contrato = f"PJ-{cnpj}" if cnpj else "PF-INDIVIDUAL"
        ibge = _txt(b, "codigoMunicipioResidencia")
        cancel = _data(_txt(b, "dataCancelamento"))
        pac.add("planos", plano, {"codigo": plano, "nome": f"Plano ANS {plano}",
                                  "segmentacao": _txt(b, "segmentacao").lower()})
        pac.add("contratos", contrato, {
            "codigo": contrato, "nome": f"Contratante CNPJ {cnpj}" if cnpj else "Individual/familiar",
            "tipo": "empresarial" if cnpj else "individual"})
        pac.add("plano_contratos", f"{contrato}|{plano}", {"contrato_codigo": contrato, "plano_codigo": plano})
        pac.add("beneficiarios", codigo, {
            "codigo": codigo, "sexo": _SEXO_SIB.get(_txt(b, "sexo").upper(), ""),
            "data_nascimento": nasc.isoformat(), "plano_codigo": plano, "contrato_codigo": contrato,
            "cidade": f"IBGE {ibge}" if ibge else "Não informada", "uf": _UF_IBGE.get(ibge[:2], "NI"),
            "data_adesao": adesao.isoformat(), "data_saida": cancel.isoformat() if cancel else ""})
        n += 1
    if n == 0 and not any(r["arquivo"] == nome for r in pac.rejeitos):
        raise PadraoANSError(f"{nome}: nenhum <beneficiario> encontrado (é um arquivo SIB?)")
    pac.arquivos.append({"arquivo": nome, "padrao": "SIB", "beneficiarios": n})


# ------------------------------------------------------------------------------ pacote
_COLUNAS = {
    "especialidades": ("codigo", "nome", "grupo"),
    "planos": ("codigo", "nome", "segmentacao"),
    "contratos": ("codigo", "nome", "tipo"),
    "plano_contratos": ("contrato_codigo", "plano_codigo"),
    "prestadores": ("codigo", "nome", "tipo_prestador", "cidade", "uf", "especialidade_codigo"),
    "procedimentos": ("codigo", "descricao", "especialidade_codigo", "grupo_procedimento"),
    "beneficiarios": ("codigo", "sexo", "data_nascimento", "plano_codigo", "contrato_codigo",
                      "cidade", "uf", "data_adesao", "data_saida"),
    "eventos": ("source_record_id", "beneficiario_codigo", "prestador_codigo", "procedimento_codigo",
                "especialidade_codigo", "data_evento", "data_apresentacao", "numero_guia",
                "tipo_atendimento", "quantidade", "valor_apresentado", "valor_glosado", "valor_pago",
                "valor_coparticipacao"),
}


def _csv(colunas: tuple[str, ...], linhas: Iterable[dict]) -> bytes:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=colunas, delimiter=";", lineterminator="\n", extrasaction="ignore")
    w.writeheader()
    for linha in linhas:
        w.writerow(linha)
    return buf.getvalue().encode("utf-8")


def _uf_prestadores(pac: Pacote, uf_padrao: str | None) -> int:
    """A guia não traz UF do prestador: usa a UF predominante dos beneficiários atendidos
    (SIB); sem isso, a UF padrão informada. Devolve quantos ficaram sem UF."""
    uf_benef = {c: b["uf"] for c, b in pac.tabelas.get("beneficiarios", {}).items() if b["uf"] != "NI"}
    contagem: dict[str, dict[str, int]] = {}
    for e in pac.eventos:
        uf = uf_benef.get(e["beneficiario_codigo"])
        if uf:
            por_uf = contagem.setdefault(e["prestador_codigo"], {})
            por_uf[uf] = por_uf.get(uf, 0) + 1
    sem = 0
    for cod, p in pac.tabelas.get("prestadores", {}).items():
        if cod in contagem:
            p["uf"] = max(contagem[cod].items(), key=lambda kv: kv[1])[0]
        elif uf_padrao:
            p["uf"] = uf_padrao.upper()
        else:
            sem += 1
    return sem


def converter(tiss: dict[str, bytes], sib: dict[str, bytes],
              uf_padrao: str | None = None) -> tuple[dict[str, bytes], dict]:
    """(arquivos CSV do mapping `ans_padroes_v1`, relatório da conversão)."""
    if uf_padrao and uf_padrao.upper() not in _UF_IBGE.values():
        raise PadraoANSError(f"UF padrão inválida: {uf_padrao}")
    if not tiss:
        raise PadraoANSError("envie ao menos um XML TISS (lote de guias)")
    if not sib:
        raise PadraoANSError("envie o XML SIB dos beneficiários (o pacote exige o cadastro)")
    pac = Pacote()
    for nome, dados in tiss.items():
        ler_tiss(dados, nome, pac)
    for nome, dados in sib.items():
        ler_sib(dados, nome, pac)
    prestadores_sem_uf = _uf_prestadores(pac, uf_padrao)
    sem_cadastro = {e["beneficiario_codigo"] for e in pac.eventos} - set(pac.tabelas.get("beneficiarios", {}))
    arquivos = {f"{ent}.csv": _csv(cols, pac.eventos if ent == "eventos" else pac.tabelas.get(ent, {}).values())
                for ent, cols in _COLUNAS.items()}
    total = sum((Decimal(e["valor_apresentado"]) for e in pac.eventos), Decimal(0))
    relatorio = {
        "mapping": {"mapping_id": MAPPING_ID, "mapping_version": MAPPING_VERSION},
        "arquivos": pac.arquivos,
        "contagens": {ent: (len(pac.eventos) if ent == "eventos" else len(pac.tabelas.get(ent, {})))
                      for ent in _COLUNAS},
        "valor_apresentado_total": f"{total:.2f}",
        "competencias_apresentacao": sorted({e["data_apresentacao"][:7] for e in pac.eventos}),
        "rejeitados": pac.rejeitos[:200],
        "total_rejeitados": len(pac.rejeitos),
        "beneficiarios_sem_cadastro_sib": len(sem_cadastro),
        "prestadores_sem_uf": prestadores_sem_uf,
        "limitacoes": [
            "Glosa e valor pago não vêm na guia: valor pago = apresentado até integrar o "
            "demonstrativo de análise de conta.",
            "Cidade/UF do prestador não vêm na guia: UF = a predominante dos beneficiários "
            "atendidos (ou a UF padrão informada); cidade = CNES. Complete pelo cadastro da rede.",
            "Especialidade = CBO do profissional executante; sem CBO, 'Não informada'.",
        ],
    }
    return arquivos, relatorio


# ------------------------------------------------------------------------------ DIOPS
CONTAS_DIOPS = {
    "311": ("receita_contraprestacoes", "Contraprestações efetivas de plano de assistência à saúde"),
    "411": ("eventos_indenizaveis", "Eventos indenizáveis líquidos"),
}


def conferir_diops(session: Session, dados: bytes, ano: int, trimestre: int,
                   tolerancia_pct: float = 1.0) -> dict:
    if trimestre not in (1, 2, 3, 4):
        raise PadraoANSError("trimestre deve ser 1, 2, 3 ou 4")
    try:
        texto = dados.decode("utf-8-sig")
    except UnicodeDecodeError:
        texto = dados.decode("latin-1")
    leitor = csv.DictReader(io.StringIO(texto), delimiter=";")
    cols = {c.strip().lower() for c in (leitor.fieldnames or [])}
    if not {"conta", "valor"} <= cols:
        raise PadraoANSError("o balancete DIOPS precisa das colunas conta;descricao;valor")
    declarado: dict[str, Decimal] = {}
    for linha in leitor:
        lin = {k.strip().lower(): (v or "").strip() for k, v in linha.items() if k}
        conta = lin["conta"].replace(".", "")
        for prefixo, (chave, _d) in CONTAS_DIOPS.items():
            if conta == prefixo:
                declarado[chave] = _dec(lin["valor"].replace(".", "").replace(",", ".")
                                        if "," in lin["valor"] else lin["valor"])
    meses = [date(ano, 3 * (trimestre - 1) + i, 1) for i in (1, 2, 3)]
    gold = session.execute(text(
        "SELECT COALESCE(SUM(receita), 0) AS receita, COALESCE(SUM(despesa_liquida), 0) AS despesa, "
        "COUNT(*) AS meses FROM agg_sinistralidade_competencia "
        "WHERE tenant_id = :t AND competencia = ANY(:m)"), {"t": tenant_of(session), "m": meses}).mappings().one()
    w2h = {"receita_contraprestacoes": Decimal(str(gold["receita"])),
           "eventos_indenizaveis": Decimal(str(gold["despesa"]))}
    linhas = []
    for prefixo, (chave, desc) in CONTAS_DIOPS.items():
        dec, calc = declarado.get(chave), w2h[chave]
        dif = None if dec is None else calc - dec
        pct = None if dec in (None, Decimal(0)) else float(dif / dec * 100)
        linhas.append({
            "conta": prefixo, "descricao": desc, "diops": f"{dec:.2f}" if dec is not None else None,
            "w2health": f"{calc:.2f}", "diferenca": f"{dif:.2f}" if dif is not None else None,
            "diferenca_pct": round(pct, 2) if pct is not None else None,
            "status": "ausente_no_diops" if dec is None else
                      ("ok" if abs(pct or 0) <= tolerancia_pct else "divergente"),
        })
    return {
        "ano": ano, "trimestre": trimestre, "meses": [m.isoformat() for m in meses],
        "meses_com_dados": gold["meses"], "tolerancia_pct": tolerancia_pct, "linhas": linhas,
        "observacao": ("Despesa do W2Health = despesa líquida (apresentado − glosa − coparticipação) "
                       "por competência de ATENDIMENTO; o DIOPS segue o regime contábil (competência "
                       "de aviso/reconhecimento e PEONA/IBNR). Diferenças de calendário são esperadas "
                       "— a conferência aponta onde olhar, não qual lado está certo."),
    }
