"""Fase 2 — teste END-TO-END da Fundação Operacional de Integração.

    CSV externo → RAW → mapping → canônico/Silver → DQ → Gold → Serving → MESMOS endpoints

Cenário crítico: Tenant CSV-A e Tenant CSV-B com os MESMOS identificadores de negócio
(BEN-000001, CT-001, PR-0001...), ambos vindos de arquivo externo. Todas as cargas passam
pela rota administrativa real e rodam com o papel `w2health_pipeline` (RLS).
"""

from __future__ import annotations

import csv
import importlib.util
import io
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.data_platform.paths import data_platform_dir
from app.data_platform.storage import get_raw_storage
from app.db.tenant_scope import bind_tenant
from tests.conftest import SUPERADMIN_EMAIL, create_tenant, create_user, iso_client, upload_and_wait

pytestmark = pytest.mark.scenarios

CSV_A, CSV_B, CSV_C = "csv-a", "csv-b", "csv-c"


def _gerador():
    p = Path(data_platform_dir()) / "examples" / "generic_operator" / "generate_package.py"
    spec = importlib.util.spec_from_file_location("w2h_generic_pkg", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _pacote(pasta: Path, *, seed: int, benef: int, receitas: bool = True) -> dict[str, bytes]:
    from datetime import date

    _gerador().gerar(pasta, benef, seed, date(2026, 1, 1), 6, receitas)
    return {f.name: f.read_bytes() for f in sorted(pasta.glob("*.csv"))}


def _upload(sa, tenant: str, source_id: int, files: dict[str, bytes]) -> dict:
    """Fase 3: upload ASSÍNCRONO — 202 + job; o worker real (mesma fila) processa e o
    resultado é lido da ingestão. Reenvio idêntico/arquivo recusado respondem 200 direto."""
    return upload_and_wait(sa, tenant, source_id, files)


def _soma_csv(files: dict[str, bytes], mes: str) -> tuple[Decimal, Decimal]:
    """(despesa líquida, receita) calculadas DIRETO do arquivo — fonte da verdade do teste."""
    ev = csv.DictReader(io.StringIO(files["eventos.csv"].decode()), delimiter=";")
    d = lambda v: Decimal(v.replace(",", ".") or "0")  # noqa: E731
    liq = sum((d(r["vl_apresentado"]) - d(r["vl_glosa"]) - d(r["vl_coparticipacao"])
               for r in ev if r["dt_atendimento"][3:] == f"{mes[5:]}/{mes[:4]}"), Decimal(0))
    rec = sum((d(r["valor_contraprestacao"]) for r in csv.DictReader(
        io.StringIO(files["receitas.csv"].decode()), delimiter=";") if r["competencia"] == mes), Decimal(0))
    return liq, rec


@pytest.fixture(scope="module")
def csv_env(iso_env, tmp_path_factory):
    for t in (CSV_A, CSV_B, CSV_C):
        create_tenant(iso_env, t)
    create_user(iso_env, "csv.a@iso.example", tenant=CSV_A, role="MANAGER")
    create_user(iso_env, "csv.b@iso.example", tenant=CSV_B, role="MANAGER")
    create_user(iso_env, "csv.c@iso.example", tenant=CSV_C, role="MANAGER")
    sa = iso_client(iso_env, SUPERADMIN_EMAIL, None)
    fontes = {}
    for t in (CSV_A, CSV_B, CSV_C):
        r = sa.post(f"/api/admin/tenants/{t}/sources", json={
            "name": "Pacote CSV", "source_type": "FILE", "source_system": "generic_csv",
            "configuration": {"mapping_id": "generic_operator", "mapping_version": 1}})
        assert r.status_code == 201, r.text
        fontes[t] = r.json()["id"]
    pa = _pacote(tmp_path_factory.mktemp("pa"), seed=11, benef=150)
    pb = _pacote(tmp_path_factory.mktemp("pb"), seed=22, benef=120)
    pc = _pacote(tmp_path_factory.mktemp("pc"), seed=33, benef=80, receitas=False)
    run_a = _upload(sa, CSV_A, fontes[CSV_A], pa)
    run_b = _upload(sa, CSV_B, fontes[CSV_B], pb)
    run_c = _upload(sa, CSV_C, fontes[CSV_C], pc)
    return {"sa": sa, "fontes": fontes, "pa": pa, "pb": pb, "pc": pc,
            "run_a": run_a, "run_b": run_b, "run_c": run_c, "iso": iso_env, "tmp": tmp_path_factory}


# ================================================================== 1–9: carga do Tenant A
def test_carga_publicada_e_reconciliada(csv_env):
    r = csv_env["run_a"]
    assert r["status"] == "SUCCESS" and r["stage"] == "AVAILABLE"
    assert r["reconciliation"] == "PASS"
    assert r["received"] == r["valid"] and r["rejected"] == 0


def test_raw_rastreavel_e_integro(csv_env):
    iso = csv_env["iso"]
    with iso.owner() as s:
        raws = s.execute(text("SELECT tenant_id, storage_key, sha256, records, source_entity FROM raw_objects "
                              "WHERE ingestion_run_id = :r"), {"r": csv_env["run_a"]["ingestion_run_id"]}).all()
    assert len(raws) == 9
    st = get_raw_storage()
    import hashlib

    for tenant, key, sha, _n, ent in raws:
        assert tenant == CSV_A and key.startswith(f"tenant/{CSV_A}/raw/")
        assert hashlib.sha256(st.get(key)).hexdigest() == sha
        assert st.get(key) == csv_env["pa"][f"{ent}.csv"]  # RAW = conteúdo original, sem alteração


def test_silver_tem_linhagem_ate_a_ingestao(csv_env):
    with csv_env["iso"].owner() as s:
        rows = s.execute(text(
            "SELECT DISTINCT source_system, source_connection_id, ingestion_run_id FROM eventos_assistenciais "
            "WHERE tenant_id = :t"), {"t": CSV_A}).all()
        sem_id = s.execute(text("SELECT count(*) FROM eventos_assistenciais WHERE tenant_id = :t "
                                "AND source_record_id IS NULL"), {"t": CSV_A}).scalar_one()
    assert rows == [("generic_csv", csv_env["fontes"][CSV_A], csv_env["run_a"]["ingestion_run_id"])]
    assert sem_id == 0


def test_gold_so_contem_a_janela_do_tenant(csv_env):
    with csv_env["iso"].owner() as s:
        meses = s.execute(text("SELECT competencia FROM agg_sinistralidade_competencia WHERE tenant_id = :t "
                               "ORDER BY 1"), {"t": CSV_A}).scalars().all()
    assert [m.isoformat()[:7] for m in meses] == [f"2026-0{i}" for i in range(1, 7)]


def test_mesmos_endpoints_com_valores_esperados_do_arquivo(csv_env):
    c = iso_client(csv_env["iso"], "csv.a@iso.example", CSV_A)
    liq, rec = _soma_csv(csv_env["pa"], "2026-03")
    k = c.get("/api/executive/overview?competencia=2026-03").json()["kpis"]
    assert Decimal(str(k["despesa"])) == pytest.approx(liq, abs=Decimal("0.01"))
    assert Decimal(str(k["receita"])) == pytest.approx(rec, abs=Decimal("0.01"))
    s = c.get("/api/analytics/sinistralidade?competencia=2026-03")
    assert s.status_code == 200
    assert float(s.json()["sinistralidade_atual"]) == pytest.approx(float(liq / rec * 100), abs=0.01)
    assert c.get("/api/analytics/contratos?competencia=2026-03").json()["itens"]
    assert c.get("/api/analytics/prestadores?competencia=2026-03").json()["itens"]
    ben = c.get("/api/analytics/beneficiarios/BEN-000001")
    assert ben.status_code == 200 and ben.json()["beneficiario"]["codigo"] == "BEN-000001"
    assert c.get("/api/analytics/insights?competencia=2026-06").status_code == 200


# ================================================================== 15–19: Tenant B / isolamento
def test_mesmos_ids_de_negocio_nos_dois_tenants_sem_colisao(csv_env):
    with csv_env["iso"].owner() as s:
        n = s.execute(text("SELECT count(DISTINCT tenant_id) FROM beneficiarios WHERE codigo = 'BEN-000001' "
                           "AND tenant_id IN (:a, :b)"), {"a": CSV_A, "b": CSV_B}).scalar_one()
        ctr = s.execute(text("SELECT count(*) FROM contratos WHERE codigo = 'CT-001' AND tenant_id IN (:a, :b)"),
                        {"a": CSV_A, "b": CSV_B}).scalar_one()
    assert n == 2 and ctr == 2


def test_usuario_a_so_ve_dados_a_mesmo_com_ids_iguais(csv_env):
    iso = csv_env["iso"]
    ca = iso_client(iso, "csv.a@iso.example", CSV_A)
    cb = iso_client(iso, "csv.b@iso.example", CSV_B)
    with iso.owner() as s:
        id_a = s.execute(text("SELECT id FROM beneficiarios WHERE tenant_id = :t AND codigo = 'BEN-000001'"),
                         {"t": CSV_A}).scalar_one()
        id_b = s.execute(text("SELECT id FROM beneficiarios WHERE tenant_id = :t AND codigo = 'BEN-000001'"),
                         {"t": CSV_B}).scalar_one()
        ids_a = set(s.execute(text("SELECT id FROM beneficiarios WHERE tenant_id = :t"), {"t": CSV_A}).scalars())
    assert ca.get("/api/analytics/beneficiarios/BEN-000001").json()["beneficiario"]["id"] == id_a
    assert cb.get("/api/analytics/beneficiarios/BEN-000001").json()["beneficiario"]["id"] == id_b
    assert ca.get(f"/api/analytics/beneficiarios/{id_b}").status_code == 404
    lista = ca.get("/api/analytics/beneficiarios?competencia=2026-03&page_size=200").json()["itens"]
    assert lista and {i["id"] for i in lista} <= ids_a


def test_sinistralidade_de_a_nunca_contem_valores_de_b(csv_env):
    ca = iso_client(csv_env["iso"], "csv.a@iso.example", CSV_A)
    for mes in ("2026-02", "2026-05"):
        liq_a, rec_a = _soma_csv(csv_env["pa"], mes)
        liq_b, _ = _soma_csv(csv_env["pb"], mes)
        k = ca.get(f"/api/executive/overview?competencia={mes}").json()["kpis"]
        assert Decimal(str(k["despesa"])) == pytest.approx(liq_a, abs=Decimal("0.01"))
        assert Decimal(str(k["despesa"])) != pytest.approx(liq_a + liq_b, abs=Decimal("1"))


def test_fonte_de_outro_tenant_nao_e_utilizavel(csv_env):
    sa = csv_env["sa"]
    r = sa.post(f"/api/admin/tenants/{CSV_B}/sources/{csv_env['fontes'][CSV_A]}/uploads",
                files=[("files", ("eventos.csv", b"a;b\n1;2\n", "text/csv"))])
    assert r.status_code == 422 and "fonte inexistente" in r.json()["detail"]
    fontes_a = sa.get(f"/api/admin/tenants/{CSV_A}/sources").json()["itens"]
    assert {f["tenant_id"] for f in fontes_a} == {CSV_A}
    with csv_env["iso"].app() as s:  # ingestões de B invisíveis no contexto de A (RLS)
        bind_tenant(s, CSV_A)
        tenants = set(s.execute(text("SELECT DISTINCT tenant_id FROM ingestion_runs")).scalars())
        raw_t = set(s.execute(text("SELECT DISTINCT tenant_id FROM raw_objects")).scalars())
    assert tenants == {CSV_A} and raw_t == {CSV_A}


# ================================================================== idempotência / reprocesso
def test_mesmo_pacote_nao_duplica(csv_env):
    sa, iso = csv_env["sa"], csv_env["iso"]
    with iso.owner() as s:
        antes = s.execute(text("SELECT count(*) FROM eventos_assistenciais WHERE tenant_id = :t"),
                          {"t": CSV_A}).scalar_one()
    r = _upload(sa, CSV_A, csv_env["fontes"][CSV_A], csv_env["pa"])
    assert r["status"] == "SUCCESS" and r["duplicate_of"] == csv_env["run_a"]["ingestion_run_id"]
    with iso.owner() as s:
        depois = s.execute(text("SELECT count(*) FROM eventos_assistenciais WHERE tenant_id = :t"),
                           {"t": CSV_A}).scalar_one()
    assert depois == antes


def test_visao_geral_mostra_ultima_carga_efetiva_e_nao_o_reenvio(csv_env):
    """O reenvio idêntico é a última EXECUÇÃO, mas não a última CARGA: registros e
    reconciliação da visão geral vêm da carga publicada."""
    sa = csv_env["sa"]
    dup = _upload(sa, CSV_B, csv_env["fontes"][CSV_B], csv_env["pb"])
    assert dup["duplicate_of"] == csv_env["run_b"]["ingestion_run_id"]
    item = next(i for i in sa.get("/api/admin/integrations").json()["itens"] if i["tenant_id"] == CSV_B)
    assert item["last_run"]["duplicate_of"] == csv_env["run_b"]["ingestion_run_id"]
    assert item["last_published"]["id"] == csv_env["run_b"]["ingestion_run_id"]
    assert item["last_published"]["records_valid"] > 0
    assert item["last_published_reconciliation"] == "PASS"


def test_reprocesso_de_competencia_corrige_e_remove_sem_duplicar(csv_env):
    sa, iso = csv_env["sa"], csv_env["iso"]
    linhas = csv_env["pa"]["eventos.csv"].decode().splitlines()
    cab, corpo = linhas[0], linhas[1:]
    mar = [linha for linha in corpo if "/03/2026;" in linha]
    removido, alterado = mar[0], mar[1]
    campos = alterado.split(";")
    campos[7] = "99999,99"  # valor apresentado corrigido na origem
    campos[8], campos[9], campos[10] = "0,00", "99999,99", "0,00"
    novo_alt = ";".join(campos)
    novo = [cab] + [novo_alt if linha == alterado else linha for linha in corpo if linha != removido]
    files = {**csv_env["pa"], "eventos.csv": ("\n".join(novo) + "\n").encode()}
    r = _upload(sa, CSV_A, csv_env["fontes"][CSV_A], files)
    assert r["status"] == "SUCCESS" and r["reconciliation"] == "PASS" and r["duplicate_of"] is None
    with iso.owner() as s:
        total = s.execute(text("SELECT count(*) FROM eventos_assistenciais WHERE tenant_id = :t"),
                          {"t": CSV_A}).scalar_one()
        some = s.execute(text("SELECT count(*) FROM eventos_assistenciais WHERE tenant_id = :t "
                              "AND source_record_id = :id"), {"t": CSV_A, "id": removido.split(";")[0]}).scalar_one()
        valor = s.execute(text("SELECT valor_apresentado FROM eventos_assistenciais WHERE tenant_id = :t "
                               "AND source_record_id = :id"), {"t": CSV_A, "id": campos[0]}).scalar_one()
    assert total == len(corpo) - 1 and some == 0 and valor == Decimal("99999.99")
    csv_env["pa"] = files  # o estado atual da origem passa a ser o corrigido


# ================================================================== DQ gate
def test_data_quality_bloqueia_promocao(csv_env):
    sa, iso = csv_env["sa"], csv_env["iso"]
    with iso.owner() as s:
        antes = s.execute(text("SELECT count(*), sum(valor_apresentado) FROM eventos_assistenciais "
                               "WHERE tenant_id = :t"), {"t": CSV_B}).one()
    linhas = csv_env["pb"]["eventos.csv"].decode().splitlines()
    campos = linhas[1].split(";")
    campos[7] = "-50,00"  # valor negativo → ERROR
    ruim = {**csv_env["pb"], "eventos.csv": ("\n".join([linhas[0], ";".join(campos)] + linhas[2:]) + "\n").encode()}
    r = _upload(sa, CSV_B, csv_env["fontes"][CSV_B], ruim)
    assert r["status"] == "FAILED" and r["stage"] == "RECEIVED" and r["errors"] >= 1
    det = sa.get(f"/api/admin/tenants/{CSV_B}/ingestion-runs/{r['ingestion_run_id']}").json()
    regras = {(d["rule_id"], d["severity"], d["blocking"]) for d in det["data_quality"]}
    assert ("valor_negativo", "ERROR", False) in regras and ("gate_rejeicao", "ERROR", True) in regras
    assert all("vl_apresentado" not in str(d["sample"]) for d in det["data_quality"])  # sem payload
    with iso.owner() as s:
        depois = s.execute(text("SELECT count(*), sum(valor_apresentado) FROM eventos_assistenciais "
                                "WHERE tenant_id = :t"), {"t": CSV_B}).one()
    assert tuple(depois) == tuple(antes)  # nada promovido


def test_contrato_com_varios_planos(csv_env):
    """Contrato × plano é N:N: CT-004 (empresarial) oferece 2 planos e tem beneficiários
    nos dois; todo beneficiário está num plano do próprio contrato."""
    with csv_env["iso"].owner() as s:
        planos = s.execute(text(
            "SELECT pl.codigo FROM planos_contratos pc JOIN contratos ct ON ct.id = pc.id_contrato "
            "JOIN planos pl ON pl.id = pc.id_plano WHERE pc.tenant_id = :t AND ct.codigo = 'CT-004' "
            "ORDER BY 1"), {"t": CSV_A}).scalars().all()
        dos_benef = s.execute(text(
            "SELECT DISTINCT pl.codigo FROM beneficiarios b JOIN contratos ct ON ct.id = b.id_contrato "
            "JOIN planos pl ON pl.id = b.id_plano WHERE b.tenant_id = :t AND ct.codigo = 'CT-004' "
            "ORDER BY 1"), {"t": CSV_A}).scalars().all()
    assert planos == ["PL-EMPRESA", "PL-SUPERIOR"]
    assert dos_benef == planos


def test_plano_fora_do_contrato_bloqueia(csv_env):
    sa = csv_env["sa"]
    linhas = csv_env["pb"]["beneficiarios.csv"].decode().splitlines()
    i = next(n for n, linha in enumerate(linhas[1:], start=1) if linha.split(";")[4] == "CT-001")
    campos = linhas[i].split(";")
    campos[3] = "PL-PADRAO"  # CT-001 só oferece PL-ESSENCIAL
    linhas[i] = ";".join(campos)
    ruim = {**csv_env["pb"], "beneficiarios.csv": ("\n".join(linhas) + "\n").encode()}
    r = _upload(sa, CSV_B, csv_env["fontes"][CSV_B], ruim)
    assert r["status"] == "FAILED" and r["stage"] == "RECEIVED"
    det = sa.get(f"/api/admin/tenants/{CSV_B}/ingestion-runs/{r['ingestion_run_id']}").json()
    regras = {(d["rule_id"], d["severity"]) for d in det["data_quality"]}
    assert ("plano_fora_do_contrato", "ERROR") in regras


def test_arquivo_obrigatorio_ausente_falha(csv_env):
    sem_eventos = {k: v for k, v in csv_env["pb"].items() if k != "eventos.csv"}
    r = _upload(csv_env["sa"], CSV_B, csv_env["fontes"][CSV_B], sem_eventos)
    assert r["status"] == "FAILED"


def test_csv_malformado_e_recusado_sem_executar(csv_env):
    r = _upload(csv_env["sa"], CSV_B, csv_env["fontes"][CSV_B], {"eventos.csv": b'a;b\n"aberto;2\n'})
    assert r["status"] == "FAILED" and ("malformado" in r["message"] or "colunas" in r["message"])


# ================================================================== readiness
def test_feature_contratada_mas_sem_dados_fica_indisponivel(csv_env):
    c = iso_client(csv_env["iso"], "csv.c@iso.example", CSV_C)  # pacote SEM receitas
    r = c.get("/api/executive/overview?competencia=2026-03")
    assert r.status_code == 403 and r.json()["code"] == "capability_not_ready"
    assert "receita" in r.json()["detail"]
    assert c.get("/api/analytics/prestadores?competencia=2026-03").status_code == 200
    caps = {x["key"]: x for x in c.get("/api/auth/me").json()["capabilities"]}
    lr = caps["loss_ratio_intelligence"]
    assert lr["entitled"] is True and lr["available"] is False and lr["data_status"] == "NOT_READY"
    assert caps["provider_intelligence"]["available"] is True
    adm = csv_env["sa"].get(f"/api/admin/tenants/{CSV_C}/readiness").json()["itens"]
    assert any(i["key"] == "executive_overview" and i["entitled"] and not i["available"] for i in adm)


# ================================================================== independência da origem
def test_mesmo_endpoint_para_tenant_sintetico_e_tenant_csv(csv_env):
    sint = iso_client(csv_env["iso"], "a.manager@iso.example", "tenant-a")
    ext = iso_client(csv_env["iso"], "csv.a@iso.example", CSV_A)
    for rota in ("/api/executive/overview?competencia=2026-03",
                 "/api/analytics/sinistralidade/explain?competencia=2026-03&dimensao=especialidade",
                 "/api/analytics/contratos?competencia=2026-03"):
        a, b = sint.get(rota), ext.get(rota)
        assert a.status_code == b.status_code == 200, rota
        assert set(a.json()) == set(b.json()), rota  # mesmo contrato de resposta


def test_motor_analitico_nao_conhece_a_origem():
    raiz = Path(__file__).resolve().parents[1] / "app"
    proibidos = ("source_type", "generic_csv", "SYNTHETIC", "synthetic_generator", "ingestion_run",
                 "raw_objects", "data_platform")
    for pasta in ("analytics", "repositories"):
        for py in (raiz / pasta).rglob("*.py"):
            if py.name == "transparency.py":  # metadado de exibição (procedência), não cálculo
                continue
            conteudo = py.read_text(encoding="utf-8")
            for termo in proibidos:
                assert termo not in conteudo, f"{py.name} conhece a origem: {termo}"


# ================================================================== papel de pipeline
def test_papel_de_pipeline_sem_privilegio_e_sob_rls(csv_env):
    eng = csv_env["iso"].pipeline_engine
    with eng.connect() as conn:
        sup, byp = conn.execute(text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")).one()
        assert not sup and not byp
        assert conn.execute(text("SELECT count(*) FROM eventos_assistenciais")).scalar_one() == 0  # sem tenant
        with pytest.raises(DBAPIError):
            conn.execute(text("SELECT email FROM users"))
        conn.rollback()
        with pytest.raises(DBAPIError):
            conn.execute(text("SELECT * FROM tenant_secrets"))
        conn.rollback()
    with eng.connect() as conn, conn.begin():
        conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": CSV_A})
        tenants = set(conn.execute(text("SELECT DISTINCT tenant_id FROM eventos_assistenciais")).scalars())
        assert tenants == {CSV_A}
        with pytest.raises(DBAPIError):
            conn.execute(text("UPDATE eventos_assistenciais SET tenant_id = :b"), {"b": CSV_B})


# ================================================================== lineage / onboarding / auditoria
def test_lineage_da_metrica_ate_o_raw(csv_env):
    lin = csv_env["sa"].get(f"/api/admin/tenants/{CSV_A}/lineage?competencia=2026-03").json()
    assert lin["gold"]["tabela"] == "agg_sinistralidade_competencia"
    origens = lin["silver"]["eventos_assistenciais"]
    assert origens and all(o["source_system"] == "generic_csv" for o in origens)
    ing = lin["ingestoes"][0]
    assert ing["source"]["source_type"] == "FILE" and ing["raw_objects"]
    assert all(r["storage_key"].startswith(f"tenant/{CSV_A}/") for r in ing["raw_objects"])


def test_onboarding_avanca_e_decisoes_manuais_sao_controladas(csv_env):
    sa = csv_env["sa"]
    ob = sa.get(f"/api/admin/tenants/{CSV_A}/onboarding").json()
    assert ob["state"] == "CAPABILITIES_READY"
    r = sa.post(f"/api/admin/tenants/{CSV_A}/onboarding", json={"state": "ACTIVE", "note": "pular"})
    assert r.status_code == 422  # ACTIVE exige HOMOLOGATED
    assert sa.post(f"/api/admin/tenants/{CSV_A}/onboarding",
                   json={"state": "HOMOLOGATED", "note": "indicadores conferidos"}).json()["state"] == "HOMOLOGATED"
    assert sa.post(f"/api/admin/tenants/{CSV_A}/onboarding",
                   json={"state": "ACTIVE", "note": "liberado"}).json()["state"] == "ACTIVE"


def test_admin_integracoes_nao_exibe_segredo(csv_env):
    itens = csv_env["sa"].get("/api/admin/integrations").json()["itens"]
    assert any(i["tenant_id"] == CSV_A for i in itens)
    assert all("secret_reference" not in i for i in itens)


def test_acesso_individual_e_auditado_sem_conteudo_clinico(csv_env):
    iso = csv_env["iso"]
    c = iso_client(iso, "csv.a@iso.example", CSV_A)
    assert c.get("/api/analytics/beneficiarios/BEN-000001").status_code == 200
    with iso.owner() as s:
        row = s.execute(text("SELECT entity_type, entity_id, details::text FROM audit_logs "
                             "WHERE action = 'data.beneficiary.view' AND tenant_id = :t "
                             "ORDER BY id DESC LIMIT 1"), {"t": CSV_A}).one()
        bid = s.execute(text("SELECT id FROM beneficiarios WHERE tenant_id = :t AND codigo = 'BEN-000001'"),
                        {"t": CSV_A}).scalar_one()
    assert row[0] == "beneficiario" and row[1] == str(bid)
    assert "procedimento" not in row[2] and "valor" not in row[2]


def test_carga_e_auditada(csv_env):
    with csv_env["iso"].owner() as s:
        n = s.execute(text("SELECT count(*) FROM audit_logs WHERE action LIKE 'pipeline.ingestion_%' "
                           "AND tenant_id = :t"), {"t": CSV_A}).scalar_one()
    assert n >= 2
