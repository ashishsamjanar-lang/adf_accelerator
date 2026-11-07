"""Source-object qualifier resolution — the Python port of
`frontend/lib/adf/qualifier.ts` (kept in parity per DEVELOPMENT_NOTES.md's generator-
parity note, same convention as `codegen.py` / `pipeline-codegen.ts`).

Turns a source system's own object identifier (schema.table,
catalog.schema.table, or a schema-less entity/OData path) into (a) the exact
identifier used to query the source and (b) an ADF-legal name segment for
generated artifacts. Fixes the root cause tracked in the "naming-convention
edge cases" plan: `spec.py`'s old `source_dataset_defaults` and
`codegen.py`'s old `_split_object_name` both did
`".".join(parts[:-1])`-style splitting and fell back to a hard-coded "dbo"
when there was no dot at all — wrong for 3-part sources (Snowflake, SAP HANA
with a catalog), schema-less sources (SAP ECC/OData, Salesforce, ...), names
that legitimately contain a dot, and case-sensitive sources (Oracle, SAP
HANA/ECC uppercase; Postgres/Cassandra lowercase).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from app.connectors.schemas import connector_schema

QualifierPart = Literal["catalog", "schema", "object"]
CaseConvention = Literal["insensitive", "upper", "lower"]


@dataclass(frozen=True)
class QualifierProfile:
    parts: tuple[QualifierPart, ...]
    case_convention: CaseConvention
    object_kind: Literal["table", "entity", "file"]


_DEFAULT_PROFILE = QualifierProfile(("schema", "object"), "insensitive", "table")
_ENTITY_PROFILE = QualifierProfile(("object",), "insensitive", "entity")
_TABLE_ONLY_PROFILE = QualifierProfile(("object",), "insensitive", "table")
_FILE_PROFILE = QualifierProfile(("object",), "insensitive", "file")

# Explicit overrides for connectors whose identifier depth or case
# convention differs from what their `kind` alone would suggest — kept as a
# short, verifiable list (only documented, well-established conventions),
# not an attempt to hand-tune every registered connector.
_QUALIFIER_PROFILE_OVERRIDES: dict[str, QualifierProfile] = {
    "sap_hana": QualifierProfile(("catalog", "schema", "object"), "upper", "table"),
    "snowflake": QualifierProfile(("catalog", "schema", "object"), "upper", "table"),
    "sap_ecc": _ENTITY_PROFILE,
    "sap_cloud_for_customer": _ENTITY_PROFILE,
    "sap_bw": _ENTITY_PROFILE,
    "google_big_query": _DEFAULT_PROFILE,
    "oracle": QualifierProfile(("schema", "object"), "upper", "table"),
    "postgresql": QualifierProfile(("schema", "object"), "lower", "table"),
    "azure_database_postgresql": QualifierProfile(("schema", "object"), "lower", "table"),
    "cassandra": QualifierProfile(("schema", "object"), "lower", "table"),
    "mysql": _TABLE_ONLY_PROFILE,
    "azure_database_mysql": _TABLE_ONLY_PROFILE,
    "apache_impala": _TABLE_ONLY_PROFILE,
    "apache_hbase": _TABLE_ONLY_PROFILE,
    "apache_phoenix": _TABLE_ONLY_PROFILE,
    "azure_databricks_delta_lake": _TABLE_ONLY_PROFILE,
}


def _default_profile_for_kind(kind: str | None) -> QualifierProfile:
    if kind == "file":
        return _FILE_PROFILE
    if kind in ("service", "llm"):
        return _ENTITY_PROFILE
    return _DEFAULT_PROFILE


def get_qualifier_profile(connector_type: str | None) -> QualifierProfile:
    """The qualifier profile for a connector, metadata-driven off the connector registry."""
    if not connector_type:
        return _DEFAULT_PROFILE
    override = _QUALIFIER_PROFILE_OVERRIDES.get(connector_type)
    if override:
        return override
    return _default_profile_for_kind(connector_schema(connector_type).kind)


@dataclass
class QualifierEntryInput:
    object: str
    catalog: str | None = None
    schema: str | None = None
    qualifier_override: str | None = None
    case_override: Literal["as-typed"] | None = None


@dataclass
class ResolvedQualifiedName:
    object: str
    forQuery: str
    display: str
    catalog: str | None = None
    schema: str | None = None


def _fold_case(value: str, convention: CaseConvention) -> str:
    if convention == "upper":
        return value.upper()
    if convention == "lower":
        return value.lower()
    return value


def _parse_qualified_string(raw: str, profile: QualifierProfile) -> tuple[str | None, str | None, str]:
    """Split a legacy dotted free-text string into (catalog, schema, object) per
    the connector's profile — segment count is driven by `profile.parts`, not a
    blind dot count, so a 1-part (entity) profile never splits at all."""
    clean = (raw or "").strip()
    if len(profile.parts) <= 1:
        return None, None, clean
    segments = [s for s in clean.split(".") if s]
    if len(segments) <= 1:
        return None, None, clean
    obj = segments[-1]
    if len(profile.parts) == 2 or len(segments) == 2:
        return None, ".".join(segments[:-1]), obj
    schema = segments[-2]
    catalog = ".".join(segments[:-2])
    return catalog, schema, obj


def resolve_qualified_name(entry: QualifierEntryInput, profile: QualifierProfile) -> ResolvedQualifiedName:
    if entry.qualifier_override and entry.qualifier_override.strip():
        literal = entry.qualifier_override.strip()
        return ResolvedQualifiedName(object=literal, forQuery=literal, display=literal)

    if entry.catalog or entry.schema:
        catalog, schema, obj = entry.catalog, entry.schema, entry.object.strip()
    else:
        catalog, schema, obj = _parse_qualified_string(entry.object, profile)

    use_as_typed = entry.case_override == "as-typed"

    def fold(v: str) -> str:
        return v if use_as_typed else _fold_case(v, profile.case_convention)

    resolved_catalog = fold(catalog.strip()) if catalog else None
    resolved_schema = fold(schema.strip()) if schema else None
    resolved_object = fold(obj or "")

    display = ".".join(p for p in (resolved_catalog, resolved_schema, resolved_object) if p)
    return ResolvedQualifiedName(
        catalog=resolved_catalog, schema=resolved_schema, object=resolved_object, forQuery=display, display=display
    )


# Matches one run of illegal characters, absorbing any literal underscores
# immediately touching it, and replaces the whole run with a single "_" — so
# adjacent illegal characters (e.g. " -") still collapse to one separator,
# without disturbing underscores that appear nowhere near an illegal
# character. A blind "collapse every underscore run" pass (the previous
# behavior) mangled legitimate double-underscore conventions that never
# contained an illegal character at all — most notably Salesforce's literal
# `__c`/`__r` custom-field/relationship suffix, which collapsed to `_c`/`_r`
# and could silently merge with an unrelated object of the same base name.
_ILLEGAL_RUN_RE = re.compile(r"_*[.+?/<>*%&:\\\-\s]+_*")
_LEADING_TRAILING_UNDERSCORES_RE = re.compile(r"^_+|_+$")


def sanitize_for_adf_name(raw: str) -> str:
    """Replace every character ADF's naming rules forbid (§24.5) with "_", so a
    sanitized source segment always produces a generated artifact name ADF
    itself will accept. Never applied to the value used to query the source."""
    trimmed = (raw or "").strip()
    replaced = _ILLEGAL_RUN_RE.sub("_", trimmed)
    replaced = _LEADING_TRAILING_UNDERSCORES_RE.sub("", replaced)
    return replaced or "object"


def build_safe_qualifier_token(resolved: ResolvedQualifiedName) -> str:
    parts = [p for p in (resolved.catalog, resolved.schema, resolved.object) if p]
    return "_".join(sanitize_for_adf_name(p) for p in parts)


@dataclass
class QualifierCollision:
    token: str
    keys: list[str]


def check_qualifier_collision(entries: list[tuple[str, str]]) -> list[QualifierCollision]:
    """Flags when two or more (key, token) entries share the same generated
    target-name token after sanitization — an always-on correctness check
    (§25.5), not a configurable naming-pattern preference."""
    by_token: dict[str, list[str]] = {}
    for key, token in entries:
        by_token.setdefault(token, []).append(key)
    return [QualifierCollision(token=token, keys=keys) for token, keys in by_token.items() if len(keys) > 1]


@dataclass
class SapExtractionIssue:
    level: Literal["warning", "error"]
    message: str


_BW_NAMESPACE_RE = re.compile(r"^/BI[C0]/", re.IGNORECASE)
# Best-effort heuristic, not exhaustive: classic SAP tables commonly
# documented as pool/cluster tables (e.g. in FI/SD ABAP references).
_KNOWN_POOL_CLUSTER_TABLES = {"BSEG", "KONV", "BSEC", "BSED"}


def check_sap_extraction_support(connector_type: str | None, object_segment: str) -> SapExtractionIssue | None:
    """Flag-only check for the separate class of SAP problem naming can't fix:
    some objects aren't readable by a plain Copy activity at all, regardless of
    how their identifier is parsed. Never blocks generation logic — only
    surfaces a validation message pointing at the right connector."""
    obj = (object_segment or "").strip()
    if not obj:
        return None
    if _BW_NAMESPACE_RE.match(obj) and connector_type != "sap_bw":
        return SapExtractionIssue(
            "error",
            f'"{obj}" looks like an SAP BW namespaced object (/BIC/ or /BI0/). '
            "A generic table connector can't read it directly — use the SAP BW (Open Hub) connector instead.",
        )
    if connector_type and connector_type != "sap_bw" and obj.upper() in _KNOWN_POOL_CLUSTER_TABLES:
        return SapExtractionIssue(
            "warning",
            f'"{obj}" is commonly documented as an SAP pool/cluster table, which some SAP database layers '
            f"don't expose to a direct SQL read. Confirm this object is accessible via {connector_type}, "
            "or extract it via an RFC/function-module layer this accelerator does not generate.",
        )
    return None
