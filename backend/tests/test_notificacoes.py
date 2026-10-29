"""Alertas por e-mail: assinatura, prévia sem dado de beneficiário, envio e idempotência
(Evolução, item 4)."""

from __future__ import annotations

from email import message_from_bytes

import pytest
from sqlalchemy import text

from app.core.config import get_settings

pytestmark = pytest.mark.scenarios


@pytest.fixture
def outbox(tmp_path, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "notificacoes_outbox_dir", str(tmp_path))
    monkeypatch.setattr(s, "smtp_host", None)
    return tmp_path


def test_assinatura_pessoal_usa_email_da_sessao(leitor):
    assert leitor.get("/api/notificacoes/minha-assinatura").json()["assinatura"] is None
    r = leitor.put("/api/notificacoes/minha-assinatura",
                   json={"frequencia": "mensal", "severidade_minima": "critica"})
    assert r.status_code == 200, r.text
    a = r.json()["assinatura"]
    assert a["email"] == "viewer@test.example" and a["frequencia"] == "mensal"
    d = leitor.get("/api/notificacoes/minha-assinatura").json()
    assert d["assinatura"]["severidade_minima"] == "critica" and d["transporte"] == "simulado"


def test_previa_nao_expoe_beneficiario(gestor):
    d = gestor.get("/api/notificacoes/previa?competencia=2026-07&severidade_minima=informativo").json()
    assert d["total"] > 0
    corpo = d["texto"] + d["html"]
    assert "BEN-0" not in corpo
    assert "/beneficiarios/" not in corpo
    assert all(i["link"].startswith("http") for i in d["itens"])


def test_envio_simulado_grava_eml_historico_e_e_idempotente(gestor, leitor, outbox, db):
    db.execute(text("DELETE FROM envios_notificacao WHERE tenant_id = 'w2h-demo'"))
    db.commit()
    gestor.put("/api/notificacoes/minha-assinatura",
               json={"frequencia": "semanal", "severidade_minima": "informativo"})
    r = gestor.post("/api/notificacoes/enviar?frequencia=semanal&competencia=2026-07")
    assert r.status_code == 200, r.text
    assert r.json()["simulados"] >= 1
    arquivos = list(outbox.glob("*.eml"))
    assert arquivos
    msg = message_from_bytes(arquivos[0].read_bytes())
    assert msg["To"] == "manager@test.example" and "2026" in msg["Subject"]

    de_novo = gestor.post("/api/notificacoes/enviar?frequencia=semanal&competencia=2026-07").json()
    assert de_novo["simulados"] == 0 and de_novo["ignorados"] >= 1

    hist = gestor.get("/api/notificacoes/envios").json()["itens"]
    assert hist and hist[0]["status"] == "simulado" and "texto" not in hist[0]


def test_viewer_nao_dispara_envio(leitor):
    assert leitor.post("/api/notificacoes/enviar?frequencia=semanal").status_code == 403
