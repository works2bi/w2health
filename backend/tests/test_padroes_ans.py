"""Padrões ANS — TISS + SIB → pacote canônico → pipeline; conferência DIOPS (Evolução, item 6)."""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import text

from app.data_platform import padroes_ans as ans
from tests.conftest import SUPERADMIN_EMAIL, create_tenant, drain_queue, iso_client

pytestmark = pytest.mark.scenarios

NS = 'xmlns:ans="http://www.ans.gov.br/padroes/tiss/schemas"'

TISS = f"""<?xml version="1.0" encoding="UTF-8"?>
<ans:mensagemTISS {NS}>
  <ans:cabecalho>
    <ans:identificacaoTransacao><ans:tipoTransacao>ENVIO_LOTE_GUIAS</ans:tipoTransacao>
      <ans:sequencialTransacao>1</ans:sequencialTransacao>
      <ans:dataRegistroTransacao>2026-03-05</ans:dataRegistroTransacao></ans:identificacaoTransacao>
    <ans:Padrao>4.01.00</ans:Padrao>
  </ans:cabecalho>
  <ans:prestadorParaOperadora><ans:loteGuias><ans:numeroLote>L-77</ans:numeroLote><ans:guiasTISS>
    <ans:guiaConsulta>
      <ans:cabecalhoConsulta><ans:numeroGuiaPrestador>C-1</ans:numeroGuiaPrestador></ans:cabecalhoConsulta>
      <ans:dadosBeneficiario><ans:numeroCarteira>CART-001</ans:numeroCarteira></ans:dadosBeneficiario>
      <ans:contratadoExecutante><ans:codigoPrestadorNaOperadora>PR-10</ans:codigoPrestadorNaOperadora>
        <ans:nomeContratado>Clínica Exemplo</ans:nomeContratado><ans:CNES>1234567</ans:CNES></ans:contratadoExecutante>
      <ans:profissionalExecutante><ans:CBOS>225125</ans:CBOS></ans:profissionalExecutante>
      <ans:dadosAtendimento><ans:dataAtendimento>2026-02-20</ans:dataAtendimento>
        <ans:procedimento><ans:codigoTabela>22</ans:codigoTabela><ans:codigoProcedimento>10101012</ans:codigoProcedimento>
          <ans:valorProcedimento>150.00</ans:valorProcedimento></ans:procedimento></ans:dadosAtendimento>
    </ans:guiaConsulta>
    <ans:guiaSP-SADT>
      <ans:cabecalhoGuia><ans:numeroGuiaPrestador>S-9</ans:numeroGuiaPrestador></ans:cabecalhoGuia>
      <ans:dadosBeneficiario><ans:numeroCarteira>CART-002</ans:numeroCarteira></ans:dadosBeneficiario>
      <ans:dadosExecutante><ans:contratadoExecutante><ans:codigoPrestadorNaOperadora>PR-20</ans:codigoPrestadorNaOperadora>
        <ans:nomeContratado>Laboratório Exemplo</ans:nomeContratado></ans:contratadoExecutante></ans:dadosExecutante>
      <ans:dadosAtendimento><ans:tipoAtendimento>05</ans:tipoAtendimento></ans:dadosAtendimento>
      <ans:procedimentosExecutados>
        <ans:procedimentoExecutado><ans:dataExecucao>2026-03-02</ans:dataExecucao>
          <ans:procedimento><ans:codigoTabela>22</ans:codigoTabela><ans:codigoProcedimento>40304361</ans:codigoProcedimento>
            <ans:descricaoProcedimento>Hemograma completo</ans:descricaoProcedimento></ans:procedimento>
          <ans:quantidadeExecutada>2</ans:quantidadeExecutada><ans:valorTotal>30.50</ans:valorTotal></ans:procedimentoExecutado>
        <ans:procedimentoExecutado><ans:dataExecucao>2026-03-02</ans:dataExecucao>
          <ans:procedimento><ans:codigoProcedimento>40302040</ans:codigoProcedimento></ans:procedimento>
          <ans:valorTotal>12.00</ans:valorTotal></ans:procedimentoExecutado>
      </ans:procedimentosExecutados>
    </ans:guiaSP-SADT>
  </ans:guiasTISS></ans:loteGuias></ans:prestadorParaOperadora>
</ans:mensagemTISS>""".encode()

SIB = b"""<?xml version="1.0" encoding="UTF-8"?>
<mensagemSIB><beneficiarios>
  <beneficiario><codigoBeneficiario>CART-001</codigoBeneficiario><nome>NAO DEVE SER LIDO</nome>
    <cpf>00000000000</cpf><dataNascimento>1980-05-10</dataNascimento><sexo>3</sexo>
    <numeroPlanoANS>480011223</numeroPlanoANS><dataContratacao>2024-01-01</dataContratacao>
    <cnpjEmpresaContratante>11222333000144</cnpjEmpresaContratante>
    <codigoMunicipioResidencia>3550308</codigoMunicipioResidencia></beneficiario>
  <beneficiario><codigoBeneficiario>CART-002</codigoBeneficiario><dataNascimento>2010-11-02</dataNascimento>
    <sexo>1</sexo><numeroPlanoANS>480099887</numeroPlanoANS><dataContratacao>2025-06-01</dataContratacao>
    <codigoMunicipioResidencia>4106902</codigoMunicipioResidencia></beneficiario>
</beneficiarios></mensagemSIB>"""


def test_conversao_tiss_sib():
    arquivos, rel = ans.converter({"lote.xml": TISS}, {"sib.xml": SIB})
    assert rel["contagens"]["eventos"] == 3
    assert rel["contagens"]["beneficiarios"] == 2
    assert rel["valor_apresentado_total"] == "192.50"
    assert rel["competencias_apresentacao"] == ["2026-03"]
    assert rel["beneficiarios_sem_cadastro_sib"] == 0
    eventos = arquivos["eventos.csv"].decode()
    assert "2026-02-20;2026-03-05" in eventos  # atendimento em fev, apresentado em mar
    assert ";exame;2;30.50;" in eventos
    benef = arquivos["beneficiarios.csv"].decode()
    assert "NAO DEVE SER LIDO" not in benef and "00000000000" not in benef
    assert "CART-001;F;1980-05-10;480011223;PJ-11222333000144;IBGE 3550308;SP" in benef
    assert "PF-INDIVIDUAL" in arquivos["contratos.csv"].decode()
    assert "225125" in arquivos["especialidades.csv"].decode()
    prest = arquivos["prestadores.csv"].decode()
    assert "PR-10;Clínica Exemplo;consultorio;CNES 1234567;SP;225125" in prest  # UF do beneficiário
    assert ";PR;" in prest and rel["prestadores_sem_uf"] == 0


def test_xml_com_doctype_recusado():
    malicioso = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><mensagemTISS/>'
    with pytest.raises(ans.PadraoANSError, match="DOCTYPE"):
        ans.converter({"x.xml": malicioso}, {"sib.xml": SIB})


def test_arquivo_que_nao_e_tiss():
    with pytest.raises(ans.PadraoANSError, match="não é uma mensagem TISS"):
        ans.converter({"x.xml": SIB}, {"sib.xml": SIB})


def test_pacote_ans_passa_pelo_pipeline_com_data_de_apresentacao(iso_env):
    create_tenant(iso_env, "ans-a")
    sa = iso_client(iso_env, SUPERADMIN_EMAIL, None)
    fonte = sa.post("/api/admin/tenants/ans-a/sources", json={
        "name": "Arquivos TISS/SIB", "source_type": "FILE", "source_system": "padroes_ans",
        "configuration": {"mapping_id": "ans_padroes", "mapping_version": 1}})
    assert fonte.status_code == 201, fonte.text
    sid = fonte.json()["id"]
    arqs = [("tiss", ("lote.xml", TISS, "text/xml")), ("sib", ("sib.xml", SIB, "text/xml"))]
    previa = sa.post(f"/api/admin/tenants/ans-a/sources/{sid}/padroes-ans", files=arqs)
    assert previa.status_code == 200, previa.text
    assert previa.json()["carga"] is None
    r = sa.post(f"/api/admin/tenants/ans-a/sources/{sid}/padroes-ans", files=arqs,
                data={"processar": "true"})
    assert r.status_code == 202, r.text
    drain_queue()
    run = sa.get(f"/api/admin/tenants/ans-a/ingestion-runs/{r.json()['carga']['ingestion_run_id']}").json()["run"]
    assert run["status"] in ("SUCCESS", "SUCCESS_WITH_WARNINGS"), run["error_summary"]
    with iso_env.owner() as s:
        linhas = s.execute(text(
            "SELECT competencia, competencia_apresentacao, valor_apresentado FROM eventos_assistenciais "
            "WHERE tenant_id = 'ans-a' ORDER BY data_evento, valor_apresentado")).all()
    assert [(c, a) for c, a, _v in linhas] == [
        (date(2026, 2, 1), date(2026, 3, 1)), (date(2026, 3, 1), date(2026, 3, 1)),
        (date(2026, 3, 1), date(2026, 3, 1))]


def test_fonte_com_outro_mapping_recusa_conversao(iso_env):
    create_tenant(iso_env, "ans-b")
    sa = iso_client(iso_env, SUPERADMIN_EMAIL, None)
    sid = sa.post("/api/admin/tenants/ans-b/sources", json={
        "name": "Pacote CSV", "source_type": "FILE", "source_system": "generic_csv",
        "configuration": {"mapping_id": "generic_operator", "mapping_version": 1}}).json()["id"]
    r = sa.post(f"/api/admin/tenants/ans-b/sources/{sid}/padroes-ans",
                files=[("tiss", ("l.xml", TISS, "text/xml")), ("sib", ("s.xml", SIB, "text/xml"))])
    assert r.status_code == 422 and "ans_padroes" in r.text


def test_conferencia_diops(db):
    gold = db.execute(text(
        "SELECT SUM(receita), SUM(despesa_liquida) FROM agg_sinistralidade_competencia "
        "WHERE tenant_id = 'w2h-demo' AND competencia BETWEEN '2026-01-01' AND '2026-03-01'")).one()
    receita = float(gold[0])
    csv_ = (f"conta;descricao;valor\n311;Contraprestações efetivas;{receita:.2f}\n"
            f"411;Eventos indenizáveis líquidos;{float(gold[1]) * 1.10:.2f}\n").encode()
    res = ans.conferir_diops(db, csv_, 2026, 1)
    st = {linha["conta"]: linha["status"] for linha in res["linhas"]}
    assert st == {"311": "ok", "411": "divergente"}
    assert res["meses_com_dados"] == 3
