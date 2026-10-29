"""Envio dos resumos de alertas por e-mail (Evolução, item 4).

* Transporte: SMTP quando `SMTP_HOST` está configurado; senão SIMULADO — a mensagem é
  gravada como `.eml` em `NOTIFICACOES_OUTBOX_DIR` (dev/homologação), status `simulado`.
* Agendamento: `python -m app.ops.enviar_alertas --frequencia semanal|mensal` (cron/agendador
  da infraestrutura) ou o botão "Enviar agora" (MANAGER+) — mesma função.
* Idempotência: a mesma assinatura não recebe de novo o resumo da mesma competência dentro
  da janela da frequência (7 dias / 28 dias), salvo `forcar=True`.
* Cada envio fica registrado em `envios_notificacao` (sem o corpo da mensagem).
"""

from __future__ import annotations

import smtplib
import uuid
from datetime import UTC, date, datetime, timedelta
from email.message import EmailMessage
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analytics import notificacoes as conteudo
from app.core.config import get_settings
from app.models.gestao import AssinaturaAlerta, EnvioNotificacao

JANELA = {"semanal": timedelta(days=7), "mensal": timedelta(days=28)}


def _mensagem(destinatario: str, assunto: str, texto: str, html: str) -> EmailMessage:
    s = get_settings()
    msg = EmailMessage()
    msg["From"] = s.smtp_from
    msg["To"] = destinatario
    msg["Subject"] = assunto
    msg["Message-ID"] = f"<{uuid.uuid4()}@w2health>"
    msg.set_content(texto)
    msg.add_alternative(html, subtype="html")
    return msg


def transportar(msg: EmailMessage) -> str:
    """Entrega a mensagem; devolve `enviado` (SMTP) ou `simulado` (outbox)."""
    s = get_settings()
    if s.smtp_host:
        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=20) as smtp:
            if s.smtp_starttls:
                smtp.starttls()
            if s.smtp_user:
                smtp.login(s.smtp_user, s.smtp_password.get_secret_value() if s.smtp_password else "")
            smtp.send_message(msg)
        return "enviado"
    pasta = Path(s.notificacoes_outbox_dir)
    pasta.mkdir(parents=True, exist_ok=True)
    nome = f"{datetime.now(UTC):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:8]}.eml"
    (pasta / nome).write_bytes(bytes(msg))
    return "simulado"


def _ja_enviado(session: Session, a: AssinaturaAlerta, competencia: date) -> bool:
    desde = datetime.now(UTC) - JANELA.get(a.frequencia, JANELA["semanal"])
    return session.execute(
        select(EnvioNotificacao.id).where(
            EnvioNotificacao.tenant_id == a.tenant_id, EnvioNotificacao.assinatura_id == a.id,
            EnvioNotificacao.competencia == competencia,
            EnvioNotificacao.status.in_(("enviado", "simulado")),
            EnvioNotificacao.enviado_em >= desde,
        ).limit(1)
    ).first() is not None


def executar(session: Session, *, tenant_id: str, tenant_nome: str, features: frozenset[str],
             frequencia: str, competencia: date, forcar: bool = False,
             somente_assinatura: int | None = None) -> dict:
    q = select(AssinaturaAlerta).where(AssinaturaAlerta.tenant_id == tenant_id,
                                       AssinaturaAlerta.ativo.is_(True),
                                       AssinaturaAlerta.frequencia == frequencia)
    if somente_assinatura is not None:
        q = select(AssinaturaAlerta).where(AssinaturaAlerta.tenant_id == tenant_id,
                                           AssinaturaAlerta.id == somente_assinatura)
    base_url = get_settings().app_public_url
    resultado = {"competencia": competencia.isoformat(), "frequencia": frequencia,
                 "enviados": 0, "simulados": 0, "ignorados": 0, "falhas": 0, "vazios": 0}
    cache: dict[tuple, dict] = {}
    for a in session.execute(q).scalars().all():
        if not forcar and _ja_enviado(session, a, competencia):
            resultado["ignorados"] += 1
            continue
        chave = (a.severidade_minima, a.incluir_insights)
        if chave not in cache:
            cache[chave] = conteudo.montar_resumo(
                session, competencia, features, severidade_minima=a.severidade_minima,
                incluir_insights=a.incluir_insights, base_url=base_url)
        resumo = cache[chave]
        assunto, texto, html = conteudo.renderizar(resumo, tenant_nome, a.frequencia)
        status, erro = "vazio", None
        if resumo["itens"]:
            try:
                status = transportar(_mensagem(a.email, assunto, texto, html))
            except (OSError, smtplib.SMTPException) as e:
                status, erro = "falhou", str(e)[:300]
        resultado[{"enviado": "enviados", "simulado": "simulados", "falhou": "falhas",
                   "vazio": "vazios"}[status]] += 1
        session.add(EnvioNotificacao(
            tenant_id=tenant_id, assinatura_id=a.id, competencia=competencia, canal="email",
            destinatario=a.email, assunto=assunto[:200], status=status, erro=erro,
            itens={"total": resumo["total"],
                   "por_severidade": {s: sum(1 for i in resumo["itens"] if i["severidade"] == s)
                                      for s in conteudo.ORDEM_SEV}},
        ))
    session.commit()
    return resultado
