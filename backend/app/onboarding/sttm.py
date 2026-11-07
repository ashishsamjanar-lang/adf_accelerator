"""STTM Silver/Gold registry validation (AGENTS.md §33.3/§33.6).

A third, standalone vendored copy of the transformation/DQ-rule shapes
already defined in ``frontend/lib/adf/medallion.ts`` (+ ``spec.ts``'s
``validateDataQualityRules``) and ported to ``worker/app/generator/
medallion.py`` (+ ``spec.py``) — same "keep in parity, not import across
service boundaries" convention this codebase already uses for
``app/standards.py`` (naming) and ``app/connectors/schemas.py`` (connector
registry), since ``backend/`` and ``worker/`` are separate deployable
services with independent dependency trees and neither imports the other.

This module only re-validates the **structural rule shape** (types, required
fields, value formats) — it never re-derives SQL or ADF JSON; that stays the
generator's job (§33.2: "reuse the existing engine, don't build a new one").
`docs/DDL.md` §1 and `AGENTS.md` §33.4/§33.6 are the source of truth for the
rules enforced here.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# §33.3 widening: Silver additionally allows "join", Gold additionally allows
# "scd2" — kept in parity with the same widening in medallion.ts/medallion.py.
SILVER_TRANSFORM_TYPES: frozenset[str] = frozenset({"conform_columns", "dedupe", "cast_schema", "scd2", "join", "filter"})
GOLD_TRANSFORM_TYPES: frozenset[str] = frozenset({"join", "aggregate", "select_columns", "scd2", "filter", "dedupe"})
LAYER_TRANSFORM_TYPES: dict[str, frozenset[str]] = {"silver": SILVER_TRANSFORM_TYPES, "gold": GOLD_TRANSFORM_TYPES}

DQ_RULE_TYPES: frozenset[str] = frozenset(
    {"IS_NOT_NULL", "UNIQUE", "LENGTH_LE", "RANGE_BETWEEN", "REGEX_MATCH", "ROW_COUNT_MIN", "FRESHNESS_HOURS"}
)
WRITE_BEHAVIORS: frozenset[str] = frozenset({"append", "overwrite", "upsert", "merge"})
LOADING_BEHAVIORS: frozenset[str] = frozenset({"FullLoad", "DeltaLoad"})

MAX_TRANSFORMATIONS = 10  # §33.3: raised from 5
MAX_DQ_RULES = 5

ENTITY_TOKEN_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")


def to_snake(value: str | None) -> str:
    token = re.sub(r"[^a-z0-9]+", "_", (value or "").strip().lower()).strip("_")
    return token or "object"


_DEFAULT_ZONE_PREFIX: dict[str, str] = {"bronze": "raw", "silver": "curated", "gold": "mart"}


def resolve_zone_prefix(layer: str, layer_prefixes: dict[str, str] | None = None) -> str:
    """Direct port of resolveZonePrefix in medallion.ts: a project's custom
    zone prefix for `layer` (Standards page, `NamingConvention.layerPrefixes`,
    AGENTS.md §25.3) when configured and valid, else the curated/mart default."""
    custom = ((layer_prefixes or {}).get(layer) or "").strip()
    if not custom:
        return _DEFAULT_ZONE_PREFIX[layer]
    cleaned = re.sub(r"[^a-z0-9_]+", "_", custom.lower()).strip("_")
    return cleaned or _DEFAULT_ZONE_PREFIX[layer]


def layer_target_table_name(
    layer: str, *, entity: str | None, domain: str | None = None, layer_prefixes: dict[str, str] | None = None
) -> str:
    """Direct port of layerTargetTableName (silver/gold branches only — this
    registry never handles bronze). `layer_prefixes` is the project's
    Standards-configured override; omitted/empty keeps the curated/mart default."""
    prefix = resolve_zone_prefix(layer, layer_prefixes)
    if layer == "silver":
        return f"{prefix}.{to_snake(entity)}"
    return f"{prefix}.{to_snake(domain)}_{to_snake(entity)}"


@dataclass
class Issue:
    severity: str
    message: str
    field: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"severity": self.severity, "message": self.message}
        if self.field:
            out["field"] = self.field
        return out


@dataclass
class Issues:
    items: list[Issue] = field(default_factory=list)

    def error(self, message: str, field_: str | None = None) -> None:
        self.items.append(Issue("error", message, field_))

    def warn(self, message: str, field_: str | None = None) -> None:
        self.items.append(Issue("warning", message, field_))

    @property
    def has_errors(self) -> bool:
        return any(i.severity == "error" for i in self.items)


def validate_transformation_rules(layer: str, rules: list[dict[str, Any]]) -> list[Issue]:
    issues: list[Issue] = []
    allowed = LAYER_TRANSFORM_TYPES.get(layer, frozenset())
    if len(rules) > MAX_TRANSFORMATIONS:
        issues.append(Issue("error", f"Maximum {MAX_TRANSFORMATIONS} transformations allowed.", "transformations"))
    if len(rules) == 0:
        issues.append(Issue("error", f"At least one transformation is required for the {layer} layer.", "transformations"))
    for i, rule in enumerate(rules):
        name = str(rule.get("name") or "").strip()
        rtype = str(rule.get("type") or "")
        label = name or f"Transformation {i + 1}"
        if not name:
            issues.append(Issue("error", f"Transformation {i + 1} needs a name.", "transformations"))
        if rtype not in allowed:
            issues.append(Issue("error", f'Transformation "{label}" type "{rtype}" isn\'t valid for the {layer} layer.', "transformations"))
        if rtype == "conform_columns" and not str(rule.get("columns") or "").strip():
            issues.append(Issue("error", f'Transformation "{label}" (conform_columns) needs a column mapping.', "transformations"))
        elif rtype == "cast_schema" and not str(rule.get("castExpression") or "").strip():
            issues.append(Issue("error", f'Transformation "{label}" (cast_schema) needs at least one "column:TYPE" cast.', "transformations"))
        elif rtype in ("dedupe", "scd2"):
            if not str(rule.get("keyColumns") or "").strip():
                issues.append(Issue("error", f'Transformation "{label}" ({rtype}) needs key column(s).', "transformations"))
            if not str(rule.get("orderColumn") or "").strip():
                issues.append(Issue("error", f'Transformation "{label}" ({rtype}) needs an order column.', "transformations"))
            if rtype == "scd2" and not str(rule.get("columns") or "").strip():
                issues.append(Issue("warning", f'Transformation "{label}" (scd2) has no tracked columns — only the keys and change-detection column are kept in history.', "transformations"))
        elif rtype == "join":
            if not str(rule.get("joinEntity") or "").strip():
                issues.append(Issue("error", f'Transformation "{label}" (join) needs the other entity/table name.', "transformations"))
            if not str(rule.get("joinCondition") or "").strip():
                issues.append(Issue("error", f'Transformation "{label}" (join) needs a join condition.', "transformations"))
            if not str(rule.get("columns") or "").strip():
                issues.append(Issue("warning", f'Transformation "{label}" (join) has no output column list — "a.*, b.*" duplicates the join keys and SQL Server will reject it.', "transformations"))
        elif rtype == "aggregate":
            if not str(rule.get("groupByColumns") or "").strip():
                issues.append(Issue("error", f'Transformation "{label}" (aggregate) needs GROUP BY column(s).', "transformations"))
            if not str(rule.get("aggregateExpression") or "").strip():
                issues.append(Issue("error", f'Transformation "{label}" (aggregate) needs at least one aggregate expression.', "transformations"))
        elif rtype == "select_columns" and not str(rule.get("columns") or "").strip():
            issues.append(Issue("error", f'Transformation "{label}" (select_columns) needs the final column list.', "transformations"))
        elif rtype == "filter" and not str(rule.get("filterCondition") or "").strip():
            issues.append(Issue("error", f'Transformation "{label}" (filter) needs a filter condition.', "transformations"))
    issues.extend(check_transformation_chain_columns(rules))
    return issues


def _split_top_level(value: str) -> list[str]:
    """Top-level comma split (commas inside parentheses don't split) — port of medallion.ts's splitTopLevel."""
    parts: list[str] = []
    depth = 0
    current = ""
    for ch in value:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        if ch == "," and depth == 0:
            parts.append(current.strip())
            current = ""
        else:
            current += ch
    if current.strip():
        parts.append(current.strip())
    return [p for p in parts if p]


def _split_mapping_pair(pair: str) -> tuple[str, str]:
    """Split `src:dst` on its last top-level colon (outside quotes/parentheses, not `::`) — port of splitMappingPair."""
    depth = 0
    quote: str | None = None
    at = -1
    for i, ch in enumerate(pair):
        if quote:
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        elif ch == ":" and depth == 0 and (i == 0 or pair[i - 1] != ":") and (i + 1 >= len(pair) or pair[i + 1] != ":"):
            at = i
    return (pair, "") if at < 0 else (pair[:at], pair[at + 1 :])


_PLAIN_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_ALIAS_RE = re.compile(r"(?i)\bAS\s+\[?([A-Za-z_][A-Za-z0-9_]*)\]?\s*$")
_TRAILING_ALIAS_RE = re.compile(r"(?i)\s+AS\s+\[?[A-Za-z_][A-Za-z0-9_]*\]?\s*$")
_BARE_RE = re.compile(r"^(?:[A-Za-z_][A-Za-z0-9_]*\.)?([A-Za-z_][A-Za-z0-9_]*)$")
_PASS = object()


def _projected_columns(items: list[str]) -> set[str] | None:
    out: set[str] = set()
    for item in items:
        alias = _ALIAS_RE.search(item)
        trimmed = item.strip()
        if alias:
            out.add(alias.group(1).lower())
        elif trimmed.endswith("*"):
            return None
        elif bare := _BARE_RE.match(trimmed):
            out.add(bare.group(1).lower())
    return out or None


def _plain_columns(value: Any) -> list[str]:
    return [c for c in _split_top_level(str(value or "")) if _PLAIN_IDENT_RE.match(c)]


def _chain_column_issues(steps: list[tuple[str, str, Any]], make_issue: Any) -> list[Any]:
    """Shared walk for ``check_transformation_chain_columns``: ``steps`` is (label, type, getter)."""
    issues: list[Any] = []
    known: set[str] | None = None
    producer = ""
    for label, rtype, get in steps:
        refs: list[str] = []
        nxt: Any = _PASS
        if rtype == "conform_columns":
            pairs = [_split_mapping_pair(p) for p in _split_top_level(str(get("columns") or ""))]
            refs = [src.strip() for src, _ in pairs if _PLAIN_IDENT_RE.match(src.strip())]
            nxt = _projected_columns([dst.strip() or src for src, dst in pairs])
        elif rtype == "select_columns":
            items = _split_top_level(str(get("columns") or ""))
            refs = [c for c in (_TRAILING_ALIAS_RE.sub("", it).strip() for it in items) if _PLAIN_IDENT_RE.match(c)]
            nxt = _projected_columns(items)
        elif rtype == "cast_schema":
            cols = [_split_mapping_pair(p)[0].strip() for p in _split_top_level(str(get("castExpression") or ""))]
            refs = [c for c in cols if _PLAIN_IDENT_RE.match(c)]
        elif rtype == "dedupe":
            refs = _plain_columns(get("keyColumns")) + _plain_columns(get("orderColumn"))
        elif rtype == "scd2":
            refs = (
                _plain_columns(get("keyColumns"))
                + _plain_columns(str(get("orderColumn") or "").strip() or "modified_date")
                + _plain_columns(get("columns"))
            )
        elif rtype == "join":
            columns = str(get("columns") or "")
            nxt = _projected_columns(_split_top_level(columns)) if columns.strip() else None
        elif rtype == "aggregate":
            group_by = _split_top_level(str(get("groupByColumns") or ""))
            refs = [c for c in group_by if _PLAIN_IDENT_RE.match(c)]
            nxt = _projected_columns(group_by + _split_top_level(str(get("aggregateExpression") or "")))
        if known is not None:
            seen: set[str] = set()
            for col in refs:
                key = col.lower()
                if key in known or key in seen:
                    continue
                seen.add(key)
                issues.append(make_issue(
                    "warning",
                    f'Transformation "{label}" ({rtype}) uses column "{col}", which the earlier step "{producer}" '
                    "doesn't output — the generated SQL would fail at run time.",
                    "transformations",
                ))
        if nxt is not _PASS:
            known = nxt
            producer = label
    return issues


def check_transformation_chain_columns(rules: list[dict[str, Any]]) -> list[Issue]:
    """Vendored port of checkTransformationChainColumns (frontend medallion.ts / worker medallion.py):
    warns when a step reads a plain column an earlier projection dropped. Always a warning."""
    steps = [
        (str(rule.get("name") or "").strip() or f"Transformation {i + 1}", str(rule.get("type") or ""), rule.get)
        for i, rule in enumerate(rules)
    ]
    return _chain_column_issues(steps, Issue)


# DMT-05: rules whose ViolationCount counts rows, so a percentage of the checked rows is meaningful.
DQ_PCT_RULE_TYPES = frozenset({"IS_NOT_NULL", "UNIQUE", "LENGTH_LE", "RANGE_BETWEEN", "REGEX_MATCH"})


def validate_data_quality_rules(rules: list[dict[str, Any]]) -> list[Issue]:
    issues: list[Issue] = []
    if len(rules) > MAX_DQ_RULES:
        issues.append(Issue("error", f"Maximum {MAX_DQ_RULES} data quality rules allowed.", "dataQualityRules"))
    for i, rule in enumerate(rules):
        name = str(rule.get("name") or "").strip()
        column = str(rule.get("column") or "").strip()
        rtype = str(rule.get("rule") or "")
        value = str(rule.get("value") or "")
        enforcement = str(rule.get("enforcement") or "warn")
        label = name or f"Rule {i + 1}"
        if not name:
            issues.append(Issue("error", f"Data quality rule {i + 1} needs a name.", "dataQualityRules"))
        if not column and rtype != "ROW_COUNT_MIN":
            issues.append(Issue("error", f'Data quality rule "{label}" needs a column.', "dataQualityRules"))
        if rtype not in DQ_RULE_TYPES:
            issues.append(Issue("error", f'Data quality rule "{label}" has an unknown rule type.', "dataQualityRules"))
        if enforcement not in ("warn", "block"):
            issues.append(Issue("error", f'Data quality rule "{label}" enforcement must be "warn" or "block".', "dataQualityRules"))
        if rtype == "LENGTH_LE" and not re.match(r"^\d+$", value.strip()):
            issues.append(Issue("error", f'Data quality rule "{label}" (LENGTH_LE) needs a whole-number max length in Value.', "dataQualityRules"))
        if rtype in ("ROW_COUNT_MIN", "FRESHNESS_HOURS") and not re.match(r"^\d+$", value.strip()):
            what = "minimum row count" if rtype == "ROW_COUNT_MIN" else "maximum age in hours"
            issues.append(Issue("error", f'Data quality rule "{label}" ({rtype}) needs a whole-number {what} in Value.', "dataQualityRules"))
        if rtype == "RANGE_BETWEEN":
            parts = [p.strip() for p in value.split(",")]
            ok = len(parts) == 2 and all(parts)
            if ok:
                try:
                    ok = float(parts[0]) <= float(parts[1])
                except ValueError:
                    ok = False
            if not ok:
                issues.append(Issue("error", f'Data quality rule "{label}" (RANGE_BETWEEN) needs "min,max" in Value.', "dataQualityRules"))
        pct = str(rule.get("maxViolationPct") or "").strip()
        if pct and rtype not in DQ_PCT_RULE_TYPES:
            issues.append(Issue("error", f'Data quality rule "{label}" ({rtype}) is a whole-table check; a max violation % applies only to row-level rules.', "dataQualityRules"))
        elif pct and not (re.fullmatch(r"\d+(?:\.\d+)?", pct) and float(pct) <= 100):
            issues.append(Issue("error", f'Data quality rule "{label}" max violation % must be a number from 0 to 100.', "dataQualityRules"))
        if rtype == "REGEX_MATCH" and not value.strip():
            issues.append(Issue("error", f'Data quality rule "{label}" (REGEX_MATCH) needs a pattern in Value.', "dataQualityRules"))
        elif rtype == "REGEX_MATCH" and _looks_like_regex(value):
            # RUN-17: the generated check is PATINDEX, so a real regex never matches and every row fails.
            issues.append(Issue(
                "warning",
                f'Data quality rule "{label}" (REGEX_MATCH) is generated as T-SQL PATINDEX (LIKE wildcards %, _, [ ]), not a regex; "{value.strip()}" looks like a regex and would flag every row. Rewrite it as a LIKE pattern (e.g. %_@_%._%).',
                "dataQualityRules",
            ))
    issues.extend(_dq_name_collisions([str(r.get("name") or "") for r in rules]))
    return issues


def _looks_like_regex(pattern: str) -> bool:
    """RUN-17: regex-only syntax LIKE/PATINDEX doesn't have — vendored copy of spec.py's looks_like_regex."""
    return re.search(r"^\^|\$$|[+\\(){}|*?]", pattern.strip()) is not None


def _dq_name_collisions(names: list[str]) -> list[Issue]:
    """Two names that sanitize to the same activity token would emit duplicate
    DQ_<name>_Check activities (DQ-06) — parity with spec.ts."""
    issues: list[Issue] = []
    seen: dict[str, str] = {}
    for raw in names:
        name = raw.strip()
        if not name:
            continue
        token = re.sub(r"[^A-Za-z0-9_]", "_", name).lower()
        if token in seen:
            issues.append(
                Issue("error", f'Data quality rules "{seen[token]}" and "{name}" produce the same activity name — rename one.', "dataQualityRules")
            )
        else:
            seen[token] = name
    return issues


def _transformation_output_columns(transformations: list[dict[str, Any]]) -> set[str]:
    """Best-effort extraction of the column *names a transformation actually
    produces* from the free-text fields a rule already carries — used only by
    `check_dq_columns_against_transformations` below. Deliberately narrow:
    `dedupe`/`scd2`/`join` don't rename or drop columns (pass-through), so
    they contribute nothing here; a registry object built entirely from those
    has no column-shaping info to check against at all."""
    columns: set[str] = set()
    for rule in transformations:
        rtype = str(rule.get("type") or "")
        if rtype in ("conform_columns", "select_columns"):
            for part in str(rule.get("columns") or "").split(","):
                part = part.strip()
                if not part:
                    continue
                alias = re.search(r"(?i)\bAS\s+([A-Za-z_][A-Za-z0-9_]*)\s*$", part)
                columns.add(alias.group(1) if alias else part.split(":")[-1].strip() if ":" in part else part)
        elif rtype == "cast_schema":
            for part in str(rule.get("castExpression") or rule.get("cast_expression") or "").split(","):
                part = part.strip()
                if ":" in part:
                    columns.add(part.split(":")[0].strip())
        elif rtype == "aggregate":
            for part in str(rule.get("groupByColumns") or rule.get("group_by_columns") or "").split(","):
                part = part.strip()
                if part:
                    columns.add(part)
            for part in str(rule.get("aggregateExpression") or rule.get("aggregate_expression") or "").split(","):
                match = re.search(r"(?i)\bAS\s+([A-Za-z_][A-Za-z0-9_]*)\s*$", part.strip())
                if match:
                    columns.add(match.group(1))
    return columns


def check_dq_columns_against_transformations(
    transformations: list[dict[str, Any]], dq_rules: list[dict[str, Any]]
) -> list[Issue]:
    """§33.6 rule 6 (soft, non-blocking): warn when a DQ rule's `column`
    doesn't appear among any transformation's *output* columns. Always a
    `warning`, never an `error` — §30.3's scope boundary means this app never
    introspects live schema, so it can only flag what the sheet itself
    suggests is a mismatch, never assert one for certain. Only fires when at
    least one transformation actually names output columns to check against
    (`conform_columns`/`cast_schema`/`select_columns`/`aggregate`); an object
    built entirely from `dedupe`/`scd2`/`join` steps carries no such
    information, and warning on every rule in that case would be noise, not a
    real signal."""
    known = _transformation_output_columns(transformations)
    if not known:
        return []
    issues: list[Issue] = []
    for i, rule in enumerate(dq_rules):
        column = str(rule.get("column") or "").strip()
        if not column or column in known:
            continue
        label = str(rule.get("name") or "").strip() or f"Rule {i + 1}"
        issues.append(
            Issue(
                "warning",
                f'Data quality rule "{label}" checks column "{column}", which doesn\'t appear in any '
                "transformation's output columns.",
                "dataQualityRules",
            )
        )
    return issues


def validate_object_header(layer: str, data: dict[str, Any]) -> list[Issue]:
    """§33.6 rules 1-6 (header-level; uniqueness/collision checks (7-8) happen
    at commit time against the DB, cross-layer advisories (9) at the caller
    level since they need visibility across both registries, guardrails (10)
    at the parse layer)."""
    issues: list[Issue] = []
    tag = str(data.get("tag") or "").strip()
    if not tag:
        issues.append(Issue("error", "tag is required (the mandatory classification column).", "tag"))

    entity = str(data.get("curatedEntity") or data.get("curated_entity") or "").strip()
    if not entity:
        issues.append(Issue("error", "curated_entity is required.", "curatedEntity"))
    elif not ENTITY_TOKEN_RE.match(to_snake(entity)):
        issues.append(Issue("error", f'curated_entity "{entity}" must be lowercase letters, digits, and underscores.', "curatedEntity"))

    if layer == "gold":
        domain = str(data.get("martDomain") or data.get("mart_domain") or "").strip()
        if not domain:
            issues.append(Issue("error", "mart_domain is required for Gold objects.", "martDomain"))
        elif not ENTITY_TOKEN_RE.match(to_snake(domain)):
            issues.append(Issue("error", f'mart_domain "{domain}" must be lowercase letters, digits, and underscores.', "martDomain"))

    source_keys = data.get("sourceObjectKeys") or data.get("source_object_keys") or []
    if isinstance(source_keys, str):
        source_keys = [s.strip() for s in source_keys.split("|") if s.strip()]
    if not source_keys:
        issues.append(Issue("error", "source_object_keys is required (at least one upstream object).", "sourceObjectKeys"))

    loading_behavior = str(data.get("loadingBehavior") or data.get("loading_behavior") or "FullLoad")
    if loading_behavior == "DeltaLoad":
        wm_col = str(data.get("watermarkColumn") or data.get("watermark_column") or "").strip()
        wm_type = str(data.get("watermarkColumnType") or data.get("watermark_column_type") or "").strip()
        if not wm_col or not wm_type:
            issues.append(Issue("error", "watermark_column and watermark_column_type are required when loading_behavior=DeltaLoad.", "watermarkColumn"))

    write_behavior = str(data.get("writeBehavior") or data.get("write_behavior") or "append")
    if write_behavior in ("upsert", "merge"):
        business_keys = data.get("businessKeys") or data.get("business_keys") or []
        if isinstance(business_keys, str):
            business_keys = [s.strip() for s in business_keys.split(",") if s.strip()]
        if not business_keys:
            issues.append(Issue("error", "business_keys is required when write_behavior is upsert or merge.", "businessKeys"))

    return issues
