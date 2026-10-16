"""Contrato OPERACIONAL canônico v1 — a interface de carga do modelo W2Health.

Espelha `data_platform/contracts/*.yaml` (fonte de verdade) no subconjunto que esta fase
torna executável. Chaves estrangeiras chegam por **código de negócio** (`plano_codigo`,
`beneficiario_codigo`...) e são resolvidas para ids técnicos na promoção para Silver.

Onde o contrato canônico diz "opcional" mas o serving atual precisa do dado (ex.: região do
prestador, contrato do beneficiário), o contrato operacional v1 marca como obrigatório e o
motivo fica documentado em docs/MAPPING_FRAMEWORK.md §Contrato operacional. Nenhuma regra
de negócio é inventada: derivações aqui são as declaradas nos contratos YAML.
"""

from __future__ import annotations

from dataclasses import dataclass

TIPOS = ("string", "int", "decimal", "date", "month", "bool")

TIPO_ATENDIMENTO = ("consulta", "exame", "terapia", "pronto_socorro", "internacao",
                    "cirurgia", "opme")
SEXO = ("M", "F")
PERFIL_UTILIZACAO = ("pontual", "recorrente", "variavel")


@dataclass(frozen=True)
class CanonField:
    name: str
    type: str
    required: bool
    vocabulary: tuple[str, ...] | None = None
    description: str = ""


@dataclass(frozen=True)
class CanonEntity:
    name: str
    fields: tuple[CanonField, ...]
    business_key: tuple[str, ...]

    def field(self, name: str) -> CanonField | None:
        return next((f for f in self.fields if f.name == name), None)


def _f(name, type_, required, vocabulary=None, description=""):
    return CanonField(name, type_, required, vocabulary, description)


ENTITIES: dict[str, CanonEntity] = {
    "especialidade": CanonEntity("especialidade", (
        _f("codigo", "string", True), _f("nome", "string", True), _f("grupo", "string", False),
    ), ("codigo",)),
    "plano": CanonEntity("plano", (
        _f("codigo", "string", True), _f("nome", "string", True),
        _f("segmentacao", "string", False), _f("tem_coparticipacao", "bool", False),
        _f("percentual_coparticipacao", "decimal", False,
           description="fração 0–1 aplicada sobre valor_pago (informativo nesta fase)"),
    ), ("codigo",)),
    "contrato": CanonEntity("contrato", (
        _f("codigo", "string", True),
        _f("nome", "string", False), _f("tipo", "string", False),
    ), ("codigo",)),
    # contrato × plano é N:N — 1 linha por plano oferecido pelo contrato
    "plano_contrato": CanonEntity("plano_contrato", (
        _f("contrato_codigo", "string", True), _f("plano_codigo", "string", True),
    ), ("contrato_codigo", "plano_codigo")),
    "prestador": CanonEntity("prestador", (
        _f("codigo", "string", True), _f("nome", "string", True),
        _f("tipo_prestador", "string", False),
        _f("cidade", "string", True), _f("uf", "string", True),
        _f("especialidade_codigo", "string", True,
           description="especialidade principal — grupo de pares da detecção de anomalia"),
    ), ("codigo",)),
    "procedimento": CanonEntity("procedimento", (
        _f("codigo", "string", True), _f("descricao", "string", True),
        _f("especialidade_codigo", "string", True),
        _f("grupo_procedimento", "string", False),
        _f("perfil_utilizacao", "string", False, PERFIL_UTILIZACAO),
    ), ("codigo",)),
    "beneficiario": CanonEntity("beneficiario", (
        _f("codigo", "string", True, description="identificador pseudonimizado"),
        _f("data_nascimento", "date", True), _f("sexo", "string", False, SEXO),
        _f("plano_codigo", "string", True,
           description="plano do beneficiário — tem de ser um dos planos do contrato"),
        _f("contrato_codigo", "string", True),
        _f("cidade", "string", True), _f("uf", "string", True),
        _f("data_adesao", "date", True), _f("data_saida", "date", False),
    ), ("codigo",)),
    "receita": CanonEntity("receita", (
        _f("competencia", "month", True), _f("plano_codigo", "string", True),
        _f("quantidade_beneficiarios", "int", True),
        _f("receita_contraprestacao", "decimal", True),
    ), ("competencia", "plano_codigo")),
    "evento_assistencial": CanonEntity("evento_assistencial", (
        _f("source_record_id", "string", True),
        _f("beneficiario_codigo", "string", True), _f("prestador_codigo", "string", True),
        _f("procedimento_codigo", "string", True), _f("especialidade_codigo", "string", False),
        _f("data_evento", "date", True),
        _f("competencia", "month", False,
           description="se ausente: mês de data_evento (DECISÃO de onboarding — documentar)"),
        _f("tipo_atendimento", "string", True, TIPO_ATENDIMENTO),
        _f("quantidade", "int", False),
        _f("valor_apresentado", "decimal", True),
        _f("valor_glosado", "decimal", False),
        _f("valor_pago", "decimal", False,
           description="se ausente: valor_apresentado − valor_glosado (contrato)"),
        _f("valor_coparticipacao", "decimal", False,
           description="se ausente: 0 — registrado em Data Quality (INFO)"),
    ), ("source_record_id",)),
}

#: ordem de carga (dependências — docs/INTEGRATION_GUIDE.md §5)
LOAD_ORDER = ("especialidade", "plano", "contrato", "plano_contrato", "prestador",
              "procedimento", "beneficiario", "receita", "evento_assistencial")

#: Macrorregião por UF (divisão regional oficial do IBGE) — derivação factual da região.
MACRORREGIAO_UF = {
    **dict.fromkeys(("AC", "AP", "AM", "PA", "RO", "RR", "TO"), "Norte"),
    **dict.fromkeys(("AL", "BA", "CE", "MA", "PB", "PE", "PI", "RN", "SE"), "Nordeste"),
    **dict.fromkeys(("DF", "GO", "MT", "MS"), "Centro-Oeste"),
    **dict.fromkeys(("ES", "MG", "RJ", "SP"), "Sudeste"),
    **dict.fromkeys(("PR", "RS", "SC"), "Sul"),
}
