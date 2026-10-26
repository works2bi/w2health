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
TIPO_REMUNERACAO = ("tabela", "pacote", "diaria", "honorario", "outro")
# Consolidação do canônico V1
SITUACAO_VINCULO = ("ativo", "suspenso", "cancelado")
TIPO_EMPRESA = ("empresarial", "adesao", "associacao", "entidade_classe", "outro")
STATUS_EMPRESA = ("ativa", "inativa")
PAPEL_EMPRESA = ("contratante", "estipulante", "administradora", "subestipulante")
UNIDADE_COBRANCA = ("contrato", "empresa")
STATUS_FATURA = ("emitida", "cancelada", "substituida")
NATUREZA_ITEM = ("mensalidade", "coparticipacao", "taxa", "ajuste", "retroativo", "desconto",
                 "acrescimo", "outro")
NATUREZA_RECEITA = ("contraprestacao", "ajuste", "outro")
NATUREZA_LANCAMENTO = ("normal", "estorno", "ajuste", "cancelamento")


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
    # Evolução V1 (investigação de custos) — histórico de vínculo, OPCIONAL. Resolve o
    # contrato/plano de cada evento pela data do atendimento.
    "vinculo_beneficiario": CanonEntity("vinculo_beneficiario", (
        _f("beneficiario_codigo", "string", True), _f("contrato_codigo", "string", True),
        _f("plano_codigo", "string", True,
           description="tem de ser um dos planos do contrato (plano_contrato)"),
        _f("vigencia_inicio", "date", True),
        _f("vigencia_fim", "date", False, description="exclusiva; vazio = vigente"),
        _f("situacao", "string", False, SITUACAO_VINCULO,
           description="vazio = ativo; exposição conta só ativo (Consolidação V1)"),
    ), ("beneficiario_codigo", "vigencia_inicio")),
    # Evolução V1 — preço unitário contratado, OPCIONAL. Única evidência aceita pelo motor
    # para afirmar "reajuste de preço".
    "preco_contratado": CanonEntity("preco_contratado", (
        _f("prestador_codigo", "string", True), _f("procedimento_codigo", "string", True),
        _f("vigencia_inicio", "date", True),
        _f("vigencia_fim", "date", False, description="exclusiva; vazio = vigente"),
        _f("valor_unitario", "decimal", True, description="valor por unidade (quantidade) do item"),
        _f("tipo_remuneracao", "string", False, TIPO_REMUNERACAO),
    ), ("prestador_codigo", "procedimento_codigo", "vigencia_inicio")),
    # ---- Consolidação do canônico V1 — empresa e fatos financeiros da receita (opcionais)
    "empresa": CanonEntity("empresa", (
        _f("codigo", "string", True), _f("nome", "string", True),
        _f("tipo", "string", False, TIPO_EMPRESA), _f("status", "string", False, STATUS_EMPRESA),
    ), ("codigo",)),
    "empresa_contrato": CanonEntity("empresa_contrato", (
        _f("empresa_codigo", "string", True), _f("contrato_codigo", "string", True),
        _f("papel", "string", False, PAPEL_EMPRESA, description="vazio = contratante"),
        _f("vigencia_inicio", "date", True),
        _f("vigencia_fim", "date", False, description="exclusiva; vazio = vigente"),
    ), ("contrato_codigo", "papel", "vigencia_inicio")),
    "fatura": CanonEntity("fatura", (
        _f("numero_documento", "string", True),
        _f("unidade_cobranca", "string", True, UNIDADE_COBRANCA),
        _f("contrato_codigo", "string", False, description="obrigatório se unidade = contrato"),
        _f("empresa_codigo", "string", False, description="obrigatório se unidade = empresa"),
        _f("competencia_faturamento", "month", True),
        _f("data_emissao", "date", False), _f("data_vencimento", "date", False),
        _f("valor_bruto", "decimal", True),
        _f("valor_desconto", "decimal", False), _f("valor_acrescimo", "decimal", False),
        _f("valor_total", "decimal", False,
           description="se ausente: bruto − desconto + acréscimo; se informado, tem de bater"),
        _f("status", "string", False, STATUS_FATURA),
    ), ("numero_documento",)),
    "fatura_item": CanonEntity("fatura_item", (
        _f("numero_documento", "string", True), _f("numero_item", "string", True),
        _f("natureza", "string", False, NATUREZA_ITEM, description="vazio = mensalidade"),
        _f("contrato_codigo", "string", False), _f("plano_codigo", "string", False),
        _f("competencia_referencia", "month", False),
        _f("quantidade_beneficiarios", "int", False),
        _f("valor", "decimal", True),
    ), ("numero_documento", "numero_item")),
    "recebimento": CanonEntity("recebimento", (
        _f("numero_recebimento", "string", True),
        _f("numero_documento", "string", False),
        _f("data_recebimento", "date", True),
        _f("competencia_recebimento", "month", False, description="se ausente: mês de data_recebimento"),
        _f("valor_recebido", "decimal", True),
    ), ("numero_recebimento",)),
    "receita_reconhecida": CanonEntity("receita_reconhecida", (
        _f("source_record_id", "string", True),
        _f("competencia", "month", True, description="competência de RECONHECIMENTO"),
        _f("contrato_codigo", "string", False), _f("plano_codigo", "string", False),
        _f("empresa_codigo", "string", False,
           description="cobrança consolidada da empresa SEM detalhamento por contrato"),
        _f("natureza", "string", False, NATUREZA_RECEITA, description="vazio = contraprestacao"),
        _f("numero_documento", "string", False),
        _f("quantidade_beneficiarios", "int", False),
        _f("receita_contraprestacao", "decimal", True),
    ), ("competencia", "source_record_id")),
    "receita": CanonEntity("receita", (
        _f("competencia", "month", True), _f("plano_codigo", "string", True),
        _f("quantidade_beneficiarios", "int", True),
        _f("receita_contraprestacao", "decimal", True),
    ), ("competencia", "plano_codigo")),
    "evento_assistencial": CanonEntity("evento_assistencial", (
        _f("source_record_id", "string", True),
        _f("beneficiario_codigo", "string", True), _f("prestador_codigo", "string", True),
        _f("procedimento_codigo", "string", True), _f("especialidade_codigo", "string", False),
        _f("estabelecimento_codigo", "string", False,
           description="local do atendimento (prestador) — opcional; executante = prestador_codigo"),
        _f("prestador_solicitante_codigo", "string", False,
           description="quem solicitou (prestador) — opcional"),
        _f("data_evento", "date", True),
        _f("competencia", "month", False,
           description="se ausente: mês de data_evento (DECISÃO de onboarding — documentar)"),
        _f("competencia_apresentacao", "month", False),
        _f("competencia_pagamento", "month", False),
        _f("numero_guia", "string", False), _f("numero_conta", "string", False),
        _f("unidade_medida", "string", False),
        _f("natureza_lancamento", "string", False, NATUREZA_LANCAMENTO,
           description="vazio = normal; estorno/ajuste/cancelamento admitem valor negativo"),
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
LOAD_ORDER = ("especialidade", "plano", "contrato", "plano_contrato", "empresa",
              "empresa_contrato", "prestador", "procedimento", "beneficiario",
              "vinculo_beneficiario", "preco_contratado", "receita", "fatura", "fatura_item",
              "recebimento", "receita_reconhecida", "evento_assistencial")

#: Macrorregião por UF (divisão regional oficial do IBGE) — derivação factual da região.
MACRORREGIAO_UF = {
    **dict.fromkeys(("AC", "AP", "AM", "PA", "RO", "RR", "TO"), "Norte"),
    **dict.fromkeys(("AL", "BA", "CE", "MA", "PB", "PE", "PI", "RN", "SE"), "Nordeste"),
    **dict.fromkeys(("DF", "GO", "MT", "MS"), "Centro-Oeste"),
    **dict.fromkeys(("ES", "MG", "RJ", "SP"), "Sudeste"),
    **dict.fromkeys(("PR", "RS", "SC"), "Sul"),
}
