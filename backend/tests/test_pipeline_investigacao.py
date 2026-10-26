"""Evolução V1 — entidades opcionais da investigação de custos pelo pipeline REAL.

    CSV (vinculos.csv, precos.csv) → RAW → mapping → DQ → Silver → Gold → /analytics/investigacao

* o vínculo HISTÓRICO decide o contrato de cada evento pela data do atendimento (e fica
  congelado no evento — despesa antiga não é reclassificada);
* a tabela de preço com nova vigência vira evidência de reajuste no motor;
* vigência sobreposta é ERROR e bloqueia a carga (nada é promovido).
"""

from __future__ import annotations

import csv
import io

import pytest
from sqlalchemy import text

from tests.conftest import SUPERADMIN_EMAIL, create_tenant, create_user, iso_client, upload_and_wait
from tests.test_pipeline_e2e import _pacote

pytestmark = pytest.mark.scenarios

CSV_D = "csv-d"


def _ler(files: dict[str, bytes], nome: str) -> list[dict]:
    return list(csv.DictReader(io.StringIO(files[nome].decode()), delimiter=";"))


def _csv(cab: list[str], linhas: list[list]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\n")
    w.writerow(cab)
    w.writerows(linhas)
    return buf.getvalue().encode()


@pytest.fixture(scope="module")
def env_d(iso_env, tmp_path_factory):
    create_tenant(iso_env, CSV_D)
    create_user(iso_env, "csv.d@iso.example", tenant=CSV_D, role="MANAGER")
    sa = iso_client(iso_env, SUPERADMIN_EMAIL, None)
    r = sa.post(f"/api/admin/tenants/{CSV_D}/sources", json={
        "name": "Pacote CSV", "source_type": "FILE", "source_system": "generic_csv",
        "configuration": {"mapping_id": "generic_operator", "mapping_version": 1}})
    assert r.status_code == 201, r.text
    pacote = _pacote(tmp_path_factory.mktemp("pd"), seed=44, benef=120)

    benef = {b["matricula"]: b for b in _ler(pacote, "beneficiarios.csv")}
    pares = {}
    for linha in _ler(pacote, "plano_contratos.csv"):
        pares.setdefault(linha["cod_plano"], []).append(linha["cod_contrato"])
    eventos = _ler(pacote, "eventos.csv")
    # beneficiário com eventos antes E depois de abr/2026, cujo plano é vendido em 2+ contratos
    meses_uso: dict[str, set[int]] = {}
    for e in eventos:  # pacote: jan–jun/2026, datas dd/mm/aaaa
        meses_uso.setdefault(e["matricula"], set()).add(int(e["dt_atendimento"][3:5]))
    alvo = next(m for m, b in benef.items() if len(pares.get(b["cod_plano"], [])) > 1
                and min(meses_uso.get(m, {99})) < 4 <= max(meses_uso.get(m, {0})))
    b = benef[alvo]
    outro = next(c for c in pares[b["cod_plano"]] if c != b["cod_contrato"])
    vinculos = _csv(["matricula", "cod_contrato", "cod_plano", "dt_inicio", "dt_fim"], [
        [alvo, b["cod_contrato"], b["cod_plano"], "01/01/2024", "01/04/2026"],
        [alvo, outro, b["cod_plano"], "01/04/2026", ""],
    ])
    # tabela de preço para os 5 pares mais frequentes: +10% a partir de abr/2026
    freq: dict[tuple, int] = {}
    for e in eventos:
        k = (e["cod_prestador"], e["cod_procedimento"])
        freq[k] = freq.get(k, 0) + 1
    top = sorted(freq, key=freq.get, reverse=True)[:5]
    precos = _csv(["cod_prestador", "cod_procedimento", "dt_inicio_vigencia", "dt_fim_vigencia",
                   "vl_unitario", "tipo_remuneracao"],
                  [r for (p, pr) in top for r in ([p, pr, "01/01/2026", "01/04/2026", "100,00", "Tabela"],
                                                  [p, pr, "01/04/2026", "", "110,00", "Tabela"])])
    run = upload_and_wait(sa, CSV_D, r.json()["id"], {**pacote, "vinculos.csv": vinculos,
                                                       "precos.csv": precos})
    return {"sa": sa, "fonte": r.json()["id"], "pacote": pacote, "run": run, "alvo": alvo,
            "contrato_original": b["cod_contrato"], "contrato_novo": outro, "top": top,
            "iso": iso_env}


def test_carga_com_entidades_opcionais_publicada(env_d):
    r = env_d["run"]
    assert r["status"] == "SUCCESS" and r["stage"] == "AVAILABLE", r
    with env_d["iso"].owner() as s:
        n_precos = s.execute(text("SELECT count(*) FROM precos_contratados WHERE tenant_id = :t"),
                             {"t": CSV_D}).scalar_one()
        n_vinc = s.execute(text("SELECT count(*) FROM beneficiario_vinculos WHERE tenant_id = :t"),
                           {"t": CSV_D}).scalar_one()
        sem_vinculo = s.execute(text("SELECT count(*) FROM eventos_assistenciais WHERE tenant_id = :t "
                                     "AND id_contrato IS NULL"), {"t": CSV_D}).scalar_one()
        # Consolidação V1: sem vigência na data do atendimento o evento NÃO é atribuído — o
        # pacote genérico tem atendimentos fora de adesão/saída; tem de ser exatamente esses
        fora = s.execute(text(
            "SELECT count(*) FROM eventos_assistenciais e JOIN beneficiarios b ON b.id = e.id_beneficiario "
            "WHERE e.tenant_id = :t AND CASE "
            " WHEN EXISTS (SELECT 1 FROM beneficiario_vinculos v WHERE v.id_beneficiario = b.id) "
            " THEN NOT EXISTS (SELECT 1 FROM beneficiario_vinculos v WHERE v.id_beneficiario = b.id "
            "      AND v.vigencia_inicio <= e.data_evento "
            "      AND (v.vigencia_fim IS NULL OR e.data_evento < v.vigencia_fim)) "
            " ELSE NOT (b.data_adesao <= e.data_evento "
            "      AND (b.data_saida IS NULL OR e.data_evento < b.data_saida)) END"),
            {"t": CSV_D}).scalar_one()
    assert n_precos == 10 and n_vinc == 2 and sem_vinculo == fora


def test_vinculo_historico_decide_o_contrato_do_evento(env_d):
    with env_d["iso"].owner() as s:
        rows = s.execute(text(
            "SELECT e.data_evento, ct.codigo FROM eventos_assistenciais e "
            "JOIN beneficiarios b ON b.id = e.id_beneficiario JOIN contratos ct ON ct.id = e.id_contrato "
            "WHERE e.tenant_id = :t AND b.codigo = :m"), {"t": CSV_D, "m": env_d["alvo"]}).all()
    antes = {c for d, c in rows if d.isoformat() < "2026-04-01"}
    depois = {c for d, c in rows if d.isoformat() >= "2026-04-01"}
    assert antes == {env_d["contrato_original"]} and depois == {env_d["contrato_novo"]}


def test_investigacao_reconhece_reajuste_de_tabela(env_d):
    c = iso_client(env_d["iso"], "csv.d@iso.example", CSV_D)
    r = c.get("/api/analytics/investigacao", params={"competencia": "2026-04"})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["decomposicao"]["reconciliacao"]["ok"]
    assert j["precos"]["pares_com_tabela"] >= 1
    assert j["precos"]["mudancas_de_tabela"]
    assert all(m["variacao_pct"] == pytest.approx(10.0) for m in j["precos"]["mudancas_de_tabela"])
    efeito = next(e for e in j["decomposicao"]["efeitos"] if e["codigo"] == "preco_contratual")
    assert efeito["disponivel"] is True


def test_vigencia_sobreposta_bloqueia_a_carga(env_d):
    ruim = _csv(["cod_prestador", "cod_procedimento", "dt_inicio_vigencia", "dt_fim_vigencia",
                 "vl_unitario", "tipo_remuneracao"],
                [[*env_d["top"][0], "01/01/2026", "", "100,00", "tabela"],
                 [*env_d["top"][0], "01/03/2026", "", "120,00", "tabela"]])
    with env_d["iso"].owner() as s:
        antes = s.execute(text("SELECT count(*) FROM precos_contratados WHERE tenant_id = :t"),
                          {"t": CSV_D}).scalar_one()
    r = upload_and_wait(env_d["sa"], CSV_D, env_d["fonte"], {**env_d["pacote"], "precos.csv": ruim})
    assert r["status"] == "FAILED" and r["stage"] == "RECEIVED"
    det = env_d["sa"].get(f"/api/admin/tenants/{CSV_D}/ingestion-runs/{r['ingestion_run_id']}").json()
    assert ("vigencia_sobreposta", "ERROR") in {(d["rule_id"], d["severity"]) for d in det["data_quality"]}
    with env_d["iso"].owner() as s:
        depois = s.execute(text("SELECT count(*) FROM precos_contratados WHERE tenant_id = :t"),
                           {"t": CSV_D}).scalar_one()
    assert depois == antes
