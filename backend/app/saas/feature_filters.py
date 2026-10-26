"""Saneamento de respostas COMPOSTAS por feature (defesa contra "feature bypass").

Bloquear a rota `/analytics/beneficiarios` não basta: a explicação da sinistralidade, o
drill-down, as coortes, os insights e os alertas embutem amostras de beneficiários e de
prestadores. Quando o tenant não tem a capability correspondente, esses blocos são
**removidos** da resposta e a remoção é declarada em `restricoes_plano` — a interface
mostra "não disponível no plano", nunca um dado parcial disfarçado.

Os cálculos não mudam: só o que é devolvido.
"""

from __future__ import annotations

from typing import Any

BENEFICIARY = "beneficiary_intelligence"
PROVIDER = "provider_intelligence"
CONTRACT = "contract_intelligence"

#: chave da resposta → feature exigida para devolvê-la
_CHAVES_RESTRITAS: dict[str, str] = {
    "concentracao_variacao_beneficiarios": BENEFICIARY,
    "beneficiarios_maior_despesa": BENEFICIARY,
    "beneficiarios_amostra": BENEFICIARY,
    "top_beneficiarios": BENEFICIARY,
    "prestadores_maior_despesa": PROVIDER,
    "prestadores_maior_contribuicao_variacao": PROVIDER,
    # Evolução V1 — investigação de custos: detalhe da decomposição por prestador
    "por_prestador": PROVIDER,
}

#: prefixo de rota de deep-link → feature exigida
_ROTAS: tuple[tuple[str, str], ...] = (
    ("/beneficiarios", BENEFICIARY),
    ("/prestadores", PROVIDER),
    ("/contratos", CONTRACT),
    ("/sinistralidade", "loss_ratio_intelligence"),
    ("/insights", "insights"),
)

#: entidade de regra de alerta → feature exigida para ver o alerta
_ENTIDADE_ALERTA: dict[str, str] = {
    "beneficiario": BENEFICIARY,
    "prestador": PROVIDER,
    "contrato": CONTRACT,
}

#: dimensões de explicação que exigem feature própria
DIMENSAO_FEATURE: dict[str, str] = {"prestador": PROVIDER, "contrato": CONTRACT}


def _vazio(valor: Any) -> Any:
    return [] if isinstance(valor, list) else None


def sanitize(payload: Any, features: frozenset[str]) -> Any:
    """Remove recursivamente blocos restritos. Registra em `restricoes_plano` (topo)."""
    removidos: set[str] = set()

    def walk(obj: Any) -> Any:
        if isinstance(obj, dict):
            out = {}
            for k, v in obj.items():
                req = _CHAVES_RESTRITAS.get(k)
                if req is not None and req not in features:
                    out[k] = _vazio(v)
                    removidos.add(req)
                else:
                    out[k] = walk(v)
            return out
        if isinstance(obj, list):
            return [walk(x) for x in obj]
        return obj

    limpo = walk(payload)
    if removidos and isinstance(limpo, dict):
        limpo["restricoes_plano"] = sorted(removidos)
    return limpo


def deep_link_permitido(deep_link: dict | None, features: frozenset[str]) -> bool:
    rota = (deep_link or {}).get("rota") or ""
    for prefixo, feat in _ROTAS:
        if rota == prefixo or rota.startswith(prefixo + "/"):
            return feat in features
    return True


def filter_insights(itens: list[dict], features: frozenset[str]) -> list[dict]:
    out = []
    for i in itens:
        if not deep_link_permitido(i.get("deep_link"), features):
            continue
        limpo = sanitize(i, features)
        limpo.pop("restricoes_plano", None)
        out.append(limpo)
    return out


def filter_alertas(itens: list[dict], features: frozenset[str]) -> list[dict]:
    out = []
    for a in itens:
        req = _ENTIDADE_ALERTA.get(a.get("entidade", ""))
        if req is not None and req not in features:
            continue
        if not deep_link_permitido(a.get("deep_link"), features):
            continue
        out.append(a)
    return out
