"""Mapping framework — SOURCE → CANÔNICO, declarativo (YAML), versionado e validado.

Formato (detalhe em docs/MAPPING_FRAMEWORK.md):

    mapping_id: generic_operator
    version: 1
    source_format: {type: csv, delimiter: ";", encoding: utf-8,
                    decimal_separator: ",", date_formats: ["%d/%m/%Y"]}
    quality: {max_rejected_ratio: 0.0}
    reconciliation: {financial_tolerance_abs: 0.0, count_tolerance_abs: 0}
    entities:
      - source_entity: eventos            # arquivo eventos.csv
        target_entity: evento_assistencial
        required: true
        load_strategy: COMPETENCIA_SNAPSHOT
        fields:
          source_record_id: {source: id_item, type: string}
          tipo_atendimento: {source: tipo_guia, type: string,
                             map: {"1": consulta, "2": exame}}
          valor_glosado:    {source: vl_glosa, type: decimal, default: 0}

Transformações suportadas (fechadas, testadas — nenhuma linguagem arbitrária no YAML):
rename (`source`), cast (`type`), `default`, `required`/nulos, datas (`date_formats` /
`date_format`), mês de competência (`type: month`), mapa de valores (`map`),
normalização (`normalize`: strip, upper, lower, collapse_spaces, digits_only).

Validação do mapping (antes de qualquer carga): entidade e campos canônicos existentes,
tipos compatíveis, todo campo canônico obrigatório mapeado (ou com `default`), chaves
desconhecidas proibidas. Mapping inválido = pipeline não começa.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import yaml

from app.data_platform.canonical import ENTITIES, TIPOS

LOAD_STRATEGIES = ("UPSERT", "COMPETENCIA_SNAPSHOT")
NORMALIZERS = ("strip", "upper", "lower", "collapse_spaces", "digits_only")
_FIELD_KEYS = {"source", "type", "required", "default", "normalize", "map", "map_default",
               "date_format"}
_ENTITY_KEYS = {"source_entity", "target_entity", "required", "load_strategy", "fields",
                "description"}
_TOP_KEYS = {"mapping_id", "version", "description", "source_format", "quality",
             "reconciliation", "entities"}
_SOURCE_ENTITY = re.compile(r"^[a-z][a-z0-9_]{1,60}$")


class MappingError(ValueError):
    """Mapping inválido (estrutura/semântica). Mensagem segura."""


@dataclass(frozen=True)
class FieldSpec:
    target: str
    source: str | None
    type: str
    required: bool
    default: Any = None
    normalize: tuple[str, ...] = ()
    value_map: dict[str, str] | None = None
    map_default: str | None = None
    date_format: str | None = None


@dataclass(frozen=True)
class EntityMapping:
    source_entity: str
    target_entity: str
    required: bool
    load_strategy: str
    fields: tuple[FieldSpec, ...]

    @property
    def source_columns(self) -> set[str]:
        return {f.source for f in self.fields if f.source}


@dataclass(frozen=True)
class Mapping:
    mapping_id: str
    version: int
    delimiter: str
    encoding: str
    decimal_separator: str
    date_formats: tuple[str, ...]
    max_rejected_ratio: float
    financial_tolerance_abs: Decimal
    count_tolerance_abs: int
    entities: tuple[EntityMapping, ...]
    description: str = ""

    def by_source(self, source_entity: str) -> EntityMapping | None:
        return next((e for e in self.entities if e.source_entity == source_entity), None)

    @property
    def ref(self) -> str:
        return f"{self.mapping_id}@v{self.version}"


# ===================================================================== carga/validação
def parse_mapping(doc: dict) -> Mapping:
    if not isinstance(doc, dict):
        raise MappingError("mapping deve ser um objeto YAML")
    desconhecidas = set(doc) - _TOP_KEYS
    if desconhecidas:
        raise MappingError(f"chaves desconhecidas no mapping: {sorted(desconhecidas)}")
    mid = doc.get("mapping_id")
    if not isinstance(mid, str) or not re.match(r"^[a-z][a-z0-9_]{2,60}$", mid):
        raise MappingError("mapping_id inválido")
    version = doc.get("version")
    if not isinstance(version, int) or version < 1:
        raise MappingError("version deve ser inteiro >= 1")
    fmt = doc.get("source_format") or {}
    if fmt.get("type", "csv") != "csv":
        raise MappingError("somente source_format.type=csv nesta fase")
    q = doc.get("quality") or {}
    r = doc.get("reconciliation") or {}
    ratio = float(q.get("max_rejected_ratio", 0.0))
    if not 0.0 <= ratio <= 1.0:
        raise MappingError("quality.max_rejected_ratio deve estar entre 0 e 1")
    ents = doc.get("entities")
    if not isinstance(ents, list) or not ents:
        raise MappingError("entities deve ser uma lista não vazia")
    entidades = tuple(_parse_entity(e) for e in ents)
    alvos = [e.target_entity for e in entidades]
    if len(set(alvos)) != len(alvos):
        raise MappingError("cada entidade canônica só pode aparecer uma vez no mapping")
    fontes = [e.source_entity for e in entidades]
    if len(set(fontes)) != len(fontes):
        raise MappingError("source_entity repetida")
    return Mapping(
        mapping_id=mid, version=version, description=str(doc.get("description", "")),
        delimiter=str(fmt.get("delimiter", ",")), encoding=str(fmt.get("encoding", "utf-8")),
        decimal_separator=str(fmt.get("decimal_separator", ".")),
        date_formats=tuple(fmt.get("date_formats") or ("%Y-%m-%d",)),
        max_rejected_ratio=ratio,
        financial_tolerance_abs=Decimal(str(r.get("financial_tolerance_abs", "0"))),
        count_tolerance_abs=int(r.get("count_tolerance_abs", 0)),
        entities=entidades,
    )


def _parse_entity(e: dict) -> EntityMapping:
    if not isinstance(e, dict):
        raise MappingError("entidade inválida")
    desconhecidas = set(e) - _ENTITY_KEYS
    if desconhecidas:
        raise MappingError(f"chaves desconhecidas na entidade: {sorted(desconhecidas)}")
    se, te = e.get("source_entity"), e.get("target_entity")
    if not isinstance(se, str) or not _SOURCE_ENTITY.match(se):
        raise MappingError(f"source_entity inválida: {se!r}")
    canon = ENTITIES.get(te)
    if canon is None:
        raise MappingError(f"target_entity não existe no modelo canônico: {te!r}")
    strategy = e.get("load_strategy", "UPSERT")
    if strategy not in LOAD_STRATEGIES:
        raise MappingError(f"load_strategy inválida: {strategy}")
    if strategy == "COMPETENCIA_SNAPSHOT" and te not in ("evento_assistencial", "receita",
                                                          "receita_reconhecida"):
        raise MappingError("COMPETENCIA_SNAPSHOT só se aplica a fatos por competência")
    raw_fields = e.get("fields")
    if not isinstance(raw_fields, dict) or not raw_fields:
        raise MappingError(f"{se}: fields vazio")
    specs = []
    for target, spec in raw_fields.items():
        cf = canon.field(target)
        if cf is None:
            raise MappingError(f"{se}: campo canônico inexistente em {te}: {target!r}")
        specs.append(_parse_field(se, target, spec, cf.type, cf.required, cf.vocabulary))
    mapeados = {s.target for s in specs}
    faltando = [f.name for f in canon.fields if f.required and f.name not in mapeados]
    if faltando:
        raise MappingError(f"{se}: campos canônicos obrigatórios sem mapeamento: {faltando}")
    return EntityMapping(se, te, bool(e.get("required", True)), strategy, tuple(specs))


def _parse_field(se: str, target: str, spec: Any, canon_type: str, canon_required: bool,
                 vocabulary) -> FieldSpec:
    if isinstance(spec, str):  # atalho: campo: coluna_origem
        spec = {"source": spec}
    if not isinstance(spec, dict):
        raise MappingError(f"{se}.{target}: especificação inválida")
    desconhecidas = set(spec) - _FIELD_KEYS
    if desconhecidas:
        raise MappingError(f"{se}.{target}: chaves desconhecidas {sorted(desconhecidas)}")
    tipo = spec.get("type", canon_type)
    if tipo not in TIPOS:
        raise MappingError(f"{se}.{target}: tipo inválido {tipo!r}")
    compat = {canon_type} | ({"date", "month"} if canon_type == "month" else set())
    if tipo not in compat:
        raise MappingError(f"{se}.{target}: tipo {tipo} incompatível com o canônico {canon_type}")
    source = spec.get("source")
    if source is not None and (not isinstance(source, str) or not source.strip()):
        raise MappingError(f"{se}.{target}: source inválido")
    if source is None and "default" not in spec:
        raise MappingError(f"{se}.{target}: sem source exige default")
    norm = tuple(spec.get("normalize") or ())
    for n in norm:
        if n not in NORMALIZERS:
            raise MappingError(f"{se}.{target}: normalização desconhecida {n!r}")
    vmap = spec.get("map")
    if vmap is not None:
        if not isinstance(vmap, dict) or not vmap:
            raise MappingError(f"{se}.{target}: map deve ser um dicionário")
        vmap = {str(k): str(v) for k, v in vmap.items()}
        if vocabulary:
            fora = [v for v in vmap.values() if v not in vocabulary]
            if fora:
                raise MappingError(f"{se}.{target}: valores fora do vocabulário canônico: {fora}")
    required = bool(spec.get("required", canon_required))
    if canon_required and not required:
        raise MappingError(f"{se}.{target}: campo obrigatório no contrato canônico")
    return FieldSpec(target=target, source=source, type=tipo, required=required,
                     default=spec.get("default"), normalize=norm, value_map=vmap,
                     map_default=spec.get("map_default"), date_format=spec.get("date_format"))


def load_mapping_file(path: str | Path) -> Mapping:
    try:
        doc = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise MappingError("YAML inválido") from e
    return parse_mapping(doc)


# ===================================================================== aplicação
@dataclass
class MappedRow:
    line: int
    values: dict[str, Any]


@dataclass
class RowReject:
    line: int
    reasons: list[str]
    source_record_id: str | None = None


@dataclass
class EntityResult:
    entity: EntityMapping
    rows: list[MappedRow] = field(default_factory=list)
    rejects: list[RowReject] = field(default_factory=list)
    received: int = 0


def _normalize(v: str, ops: tuple[str, ...]) -> str:
    for op in ops:
        if op == "strip":
            v = v.strip()
        elif op == "upper":
            v = v.upper()
        elif op == "lower":
            v = v.lower()
        elif op == "collapse_spaces":
            v = re.sub(r"\s+", " ", v).strip()
        elif op == "digits_only":
            v = re.sub(r"\D", "", v)
    return v


def _parse_date(v: str, formats: tuple[str, ...]) -> date:
    for fmt in formats:
        try:
            return datetime.strptime(v, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"data inválida '{v}'")


def _cast(v: Any, spec: FieldSpec, m: Mapping) -> Any:
    if isinstance(v, str) and spec.type != "string":
        v = v.strip()
    t = spec.type
    if t == "string":
        return str(v)
    if t == "int":
        try:
            return int(str(v))
        except ValueError as e:
            raise ValueError(f"inteiro inválido '{v}'") from e
    if t == "decimal":
        if isinstance(v, (int, float, Decimal)):
            return Decimal(str(v))
        s = str(v)
        if m.decimal_separator == ",":
            s = s.replace(".", "").replace(",", ".")
        try:
            d = Decimal(s)
        except InvalidOperation as e:
            raise ValueError(f"decimal inválido '{v}'") from e
        if not d.is_finite():
            raise ValueError(f"decimal inválido '{v}'")
        return d
    if t in ("date", "month"):
        if isinstance(v, date):
            d = v
        else:
            fmts = (spec.date_format,) if spec.date_format else m.date_formats + ("%Y-%m", "%Y-%m-%d")
            d = _parse_date(str(v), tuple(fmts))
        return d.replace(day=1) if t == "month" else d
    if t == "bool":
        s = str(v).strip().lower()
        if s in ("1", "true", "t", "s", "sim", "y", "yes"):
            return True
        if s in ("0", "false", "f", "n", "nao", "não", "no"):
            return False
        raise ValueError(f"booleano inválido '{v}'")
    raise ValueError(f"tipo não suportado {t}")


def apply_entity(m: Mapping, em: EntityMapping, rows: list[dict[str, str]],
                 header: list[str]) -> EntityResult:
    """Aplica o mapping linha a linha. Linha inválida vira `RowReject` (com motivos);
    nenhuma linha é "corrigida" silenciosamente."""
    res = EntityResult(entity=em, received=len(rows))
    ausentes = sorted(c for c in em.source_columns if c not in header)
    if ausentes:
        res.rejects = [RowReject(0, [f"colunas ausentes no arquivo: {ausentes}"])]
        return res
    id_spec = next((f for f in em.fields if f.target == "source_record_id"), None)
    for i, row in enumerate(rows, start=2):  # linha 1 = cabeçalho
        valores: dict[str, Any] = {}
        motivos: list[str] = []
        for spec in em.fields:
            bruto = row.get(spec.source) if spec.source else None
            if isinstance(bruto, str):
                bruto = _normalize(bruto, spec.normalize)
                if bruto == "":
                    bruto = None
            if bruto is not None and spec.value_map is not None:
                mapeado = spec.value_map.get(str(bruto))
                if mapeado is None:
                    if spec.map_default is None:
                        motivos.append(f"{spec.target}: valor '{bruto}' sem correspondência no mapa")
                        continue
                    mapeado = spec.map_default
                bruto = mapeado
            if bruto is None:
                if spec.default is not None:
                    bruto = spec.default
                elif spec.required:
                    motivos.append(f"{spec.target}: obrigatório ausente")
                    continue
                else:
                    valores[spec.target] = None
                    continue
            try:
                valor = _cast(bruto, spec, m)
            except ValueError as e:
                motivos.append(f"{spec.target}: {e}")
                continue
            canon = ENTITIES[em.target_entity].field(spec.target)
            if canon.vocabulary and valor not in canon.vocabulary:
                motivos.append(f"{spec.target}: valor '{valor}' fora do vocabulário canônico")
                continue
            valores[spec.target] = valor
        if motivos:
            sid = row.get(id_spec.source) if id_spec and id_spec.source else None
            res.rejects.append(RowReject(i, motivos, sid))
        else:
            res.rows.append(MappedRow(i, valores))
    return res
