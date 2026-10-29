"""Resumo de alertas e insights para envio (Evolução, item 4).

Regras de conteúdo (LGPD — docs/LGPD_TECHNICAL_CONTROLS.md):
* a mensagem NUNCA traz beneficiário identificado: alertas de entidade `beneficiario` viram
  só uma contagem ("N alertas sobre beneficiários — detalhe no W2Health"); códigos no
  formato `BEN-…` são mascarados por garantia;
* só entra o que o plano do tenant permite (mesmos filtros de feature da API);
* cada item leva o link para a tela de origem — a análise continua no produto.
"""

from __future__ import annotations

import re
from datetime import date
from urllib.parse import urlencode

from sqlalchemy.orm import Session

from app.analytics import alerts, insights
from app.saas.feature_filters import filter_alertas, filter_insights

ORDEM_SEV = {"critica": 0, "atencao": 1, "informativo": 2}
SEV_INSIGHT = {"alta": "critica", "media": "atencao", "baixa": "informativo", "info": "informativo",
               "positiva": "informativo"}
ROTULO_SEV = {"critica": "Crítico", "atencao": "Atenção", "informativo": "Informativo"}
_COD_BENEF = re.compile(r"\bBEN-\d+\b")
MAX_INSIGHTS = 5


def _limpo(txt: str) -> str:
    return _COD_BENEF.sub("BEN-•••", txt or "")


def _link(base_url: str, deep_link: dict | None, competencia: date) -> str:
    dl = deep_link or {}
    params = {"competencia": competencia.strftime("%Y-%m"), **(dl.get("params") or {})}
    params["competencia"] = str(params["competencia"])[:7]
    return f"{base_url.rstrip('/')}{dl.get('rota', '/')}?{urlencode(params)}"


def montar_resumo(session: Session, competencia: date, features: frozenset[str], *,
                  severidade_minima: str = "atencao", incluir_insights: bool = True,
                  base_url: str = "http://localhost:3000") -> dict:
    corte = ORDEM_SEV.get(severidade_minima, 1)
    itens: list[dict] = []
    benef = 0
    if "alerts" in features:
        for a in filter_alertas([x.as_dict() for x in alerts.avaliar_regras(session, competencia)], features):
            if ORDEM_SEV.get(a["severidade"], 9) > corte:
                continue
            if a["entidade"] == "beneficiario":
                benef += 1
                continue
            itens.append({
                "origem": "alerta", "severidade": a["severidade"],
                "titulo": _limpo(f"{a['regra_nome']}: {a['rotulo']}"),
                "detalhe": f"{a['indicador_rotulo']} = {a['valor_observado']:g} "
                           f"({a['operador']} {a['limite']:g} {a['unidade']})".strip(),
                "link": _link(base_url, a["deep_link"], competencia),
            })
    if incluir_insights and "insights" in features:
        ins = [i for i in filter_insights(insights.gerar(session, competencia), features)
               if ORDEM_SEV.get(SEV_INSIGHT.get(i["severidade"], "informativo"), 9) <= corte
               and i["severidade"] != "positiva"]
        for i in ins[:MAX_INSIGHTS]:
            itens.append({
                "origem": "insight", "severidade": SEV_INSIGHT.get(i["severidade"], "informativo"),
                "titulo": _limpo(i["titulo"]), "detalhe": _limpo(i["descricao"]),
                "link": _link(base_url, i["deep_link"], competencia),
            })
    itens.sort(key=lambda x: (ORDEM_SEV.get(x["severidade"], 9), x["origem"] != "alerta"))
    if benef:
        itens.append({
            "origem": "alerta", "severidade": "atencao",
            "titulo": f"{benef} alerta(s) sobre beneficiários",
            "detalhe": "Por privacidade, o detalhe dos beneficiários fica só no W2Health.",
            "link": _link(base_url, {"rota": "/insights"}, competencia),
        })
    return {"competencia": competencia.isoformat(), "severidade_minima": severidade_minima,
            "total": len(itens), "alertas_beneficiario_agregados": benef, "itens": itens}


def _mes(competencia: date) -> str:
    nomes = ("Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho", "Julho", "Agosto",
             "Setembro", "Outubro", "Novembro", "Dezembro")
    return f"{nomes[competencia.month - 1]}/{competencia.year}"


def renderizar(resumo: dict, tenant_nome: str, frequencia: str) -> tuple[str, str, str]:
    """(assunto, texto, html) do e-mail."""
    comp = date.fromisoformat(resumo["competencia"])
    n_crit = sum(1 for i in resumo["itens"] if i["severidade"] == "critica")
    assunto = (f"W2Health · {tenant_nome} · {_mes(comp)}: "
               f"{resumo['total']} ponto(s) de atenção" + (f", {n_crit} crítico(s)" if n_crit else ""))
    linhas = [f"Resumo {frequencia} de alertas e insights — {tenant_nome} — competência {_mes(comp)}", ""]
    for i in resumo["itens"]:
        linhas += [f"[{ROTULO_SEV[i['severidade']]}] {i['titulo']}", f"  {i['detalhe']}", f"  {i['link']}", ""]
    if not resumo["itens"]:
        linhas.append("Nenhum alerta ou insight acima da severidade escolhida nesta competência.")
    linhas += ["", "Você recebe este resumo porque assinou os alertas no W2Health. "
               "Para mudar a frequência ou cancelar, acesse Configuração › Alertas por e-mail."]
    texto = "\n".join(linhas)

    cor = {"critica": "#b42318", "atencao": "#b54708", "informativo": "#175cd3"}
    blocos = "".join(
        f'<tr><td style="padding:10px 0;border-bottom:1px solid #e4e7ec">'
        f'<span style="color:{cor[i["severidade"]]};font-weight:600;font-size:12px">'
        f'{ROTULO_SEV[i["severidade"]].upper()}</span><br>'
        f'<a href="{i["link"]}" style="color:#101828;font-weight:600;text-decoration:none">{_esc(i["titulo"])}</a>'
        f'<div style="color:#475467;font-size:13px">{_esc(i["detalhe"])}</div></td></tr>'
        for i in resumo["itens"]
    ) or ('<tr><td style="padding:10px 0;color:#475467">Nenhum alerta ou insight acima da '
          'severidade escolhida nesta competência.</td></tr>')
    html = (
        '<div style="font-family:Arial,sans-serif;max-width:640px;margin:auto;color:#101828">'
        f'<h2 style="margin:0 0 4px">Resumo {frequencia} · {_esc(tenant_nome)}</h2>'
        f'<div style="color:#475467;margin-bottom:12px">Competência {_mes(comp)}</div>'
        f'<table style="width:100%;border-collapse:collapse">{blocos}</table>'
        '<p style="color:#667085;font-size:12px;margin-top:16px">Você recebe este resumo porque '
        'assinou os alertas no W2Health. Para mudar a frequência ou cancelar, acesse '
        'Configuração › Alertas por e-mail.</p></div>'
    )
    return assunto, texto, html


def _esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
