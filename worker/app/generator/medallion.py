"""Medallion (Bronze/Silver/Gold) layer support — direct port of
``frontend/lib/adf/medallion.ts``. See that module's docstring for the full
design rationale (illustrative T-SQL, layer-wise table naming standard,
Bronze unchanged / Silver+Gold append a transformation chain).

Named ``MedallionLayer`` — not ``Layer``/``ArtifactLayer`` — to avoid
colliding with the unrelated §23.9 ADF-folder-deployment-order concept
already used throughout this generator (``_json_artifact``/``_text_artifact``'s
``layer`` parameter: "pipeline" | "metadata" | "runtime" | ...).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

MedallionLayer = str  # "bronze" | "silver" | "gold"

MEDALLION_LAYERS: tuple[str, ...] = ("bronze", "silver", "gold")

# §33.3 widening: Silver additionally allows "join", Gold additionally allows
# "scd2" — direct port of the same widening in medallion.ts. See that file's
# comment for the full rationale.
SILVER_TRANSFORM_TYPES: tuple[str, ...] = ("conform_columns", "dedupe", "cast_schema", "scd2", "join", "filter")
GOLD_TRANSFORM_TYPES: tuple[str, ...] = ("join", "aggregate", "select_columns", "scd2", "filter", "dedupe")

# Transformation types that write straight to the final layer target table
# instead of an intermediate staging table. Only scd2 (rerun-safe); every other
# step stages so the idempotent LoadToLayerTarget follows (E2E-08).
TERMINAL_TRANSFORM_TYPES: frozenset[str] = frozenset({"scd2"})

MEDALLION_LAYER_TRANSFORM_TYPES: dict[str, tuple[str, ...]] = {
    "bronze": (),
    "silver": SILVER_TRANSFORM_TYPES,
    "gold": GOLD_TRANSFORM_TYPES,
}

MEDALLION_LAYER_TARGET_PATTERN_HINT: dict[str, str] = {
    "bronze": "raw.<source>_<object>",
    "silver": "curated.<entity>",
    "gold": "mart.<domain>_<entity>",
}

LAYER_PIPELINE_PREFIX: dict[str, str] = {"bronze": "pl_raw_", "silver": "pl_silver_", "gold": "pl_gold_"}

_ENTITY_TOKEN_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")


@dataclass
class TransformationRule:
    name: str = ""
    type: str = "conform_columns"
    columns: str | None = None
    key_columns: str | None = None
    order_column: str | None = None
    cast_expression: str | None = None
    join_entity: str | None = None
    join_type: str | None = None
    join_condition: str | None = None
    group_by_columns: str | None = None
    aggregate_expression: str | None = None
    filter_condition: str | None = None
    current_flag_column: str | None = None
    valid_from_column: str | None = None
    valid_to_column: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"name": self.name, "type": self.type}
        for py_key, js_key in (
            ("columns", "columns"),
            ("key_columns", "keyColumns"),
            ("order_column", "orderColumn"),
            ("cast_expression", "castExpression"),
            ("join_entity", "joinEntity"),
            ("join_type", "joinType"),
            ("join_condition", "joinCondition"),
            ("group_by_columns", "groupByColumns"),
            ("aggregate_expression", "aggregateExpression"),
            ("filter_condition", "filterCondition"),
            ("current_flag_column", "currentFlagColumn"),
            ("valid_from_column", "validFromColumn"),
            ("valid_to_column", "validToColumn"),
        ):
            value = getattr(self, py_key)
            if value:
                out[js_key] = value
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TransformationRule":
        d = data or {}
        return cls(
            name=str(d.get("name", "")),
            type=str(d.get("type", "conform_columns")),
            columns=(d.get("columns")) or None,
            key_columns=(d.get("keyColumns") or d.get("key_columns")) or None,
            order_column=(d.get("orderColumn") or d.get("order_column")) or None,
            cast_expression=(d.get("castExpression") or d.get("cast_expression")) or None,
            join_entity=(d.get("joinEntity") or d.get("join_entity")) or None,
            join_type=(d.get("joinType") or d.get("join_type")) or None,
            join_condition=(d.get("joinCondition") or d.get("join_condition")) or None,
            group_by_columns=(d.get("groupByColumns") or d.get("group_by_columns")) or None,
            aggregate_expression=(d.get("aggregateExpression") or d.get("aggregate_expression")) or None,
            filter_condition=(d.get("filterCondition") or d.get("filter_condition")) or None,
            current_flag_column=(d.get("currentFlagColumn") or d.get("current_flag_column")) or None,
            valid_from_column=(d.get("validFromColumn") or d.get("valid_from_column")) or None,
            valid_to_column=(d.get("validToColumn") or d.get("valid_to_column")) or None,
        )


def _to_snake(value: str | None) -> str:
    token = re.sub(r"[^a-z0-9]+", "_", (value or "").strip().lower()).strip("_")
    return token or "object"


def _split_top_level(value: str) -> list[str]:
    """Split on top-level commas only — a comma nested inside parentheses
    (e.g. a SQL type like ``DECIMAL(18,2)`` in a cast_schema rule) doesn't
    end the current item. Direct port of splitTopLevel in medallion.ts."""
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


def split_mapping_pair(pair: str) -> tuple[str, str]:
    """DMT-06: split a ``src:dst`` / ``col:type`` pair on its LAST top-level colon (outside quotes and
    parentheses, not part of a ``::`` cast). Port of splitMappingPair in medallion.ts."""
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
        elif ch == ":" and depth == 0 and pair[i - 1 : i] != ":" and pair[i + 1 : i + 2] != ":":
            at = i
    return (pair.strip(), "") if at < 0 else (pair[:at].strip(), pair[at + 1 :].strip())


_DEFAULT_ZONE_PREFIX: dict[str, str] = {"bronze": "raw", "silver": "curated", "gold": "mart"}


def resolve_zone_prefix(layer: str, layer_prefixes: dict[str, str] | None = None) -> str:
    """Direct port of resolveZonePrefix in medallion.ts: the project's custom
    zone prefix for `layer` (Standards page, `NamingConvention.layerPrefixes`)
    when configured and valid, else the raw/curated/mart default."""
    custom = ((layer_prefixes or {}).get(layer) or "").strip()
    if not custom:
        return _DEFAULT_ZONE_PREFIX[layer]
    cleaned = re.sub(r"[^a-z0-9_]+", "_", custom.lower()).strip("_")
    return cleaned or _DEFAULT_ZONE_PREFIX[layer]


def layer_target_table_name(
    layer: str,
    *,
    source_type: str | None = None,
    object_name: str | None = None,
    entity: str | None = None,
    domain: str | None = None,
    layer_prefixes: dict[str, str] | None = None,
) -> str:
    """Direct port of layerTargetTableName in medallion.ts — the layer-wise
    table naming standard, generalized from the multi-source-etl sample
    guide's target-table-naming section. `layer_prefixes` is the project's
    Standards-configured override (§25.3); omitted/empty keeps today's
    raw/curated/mart default."""
    prefix = resolve_zone_prefix(layer, layer_prefixes)
    if layer == "bronze":
        return f"{prefix}.{_to_snake(source_type)}_{_to_snake(object_name)}"
    if layer == "silver":
        return f"{prefix}.{_to_snake(entity)}"
    return f"{prefix}.{_to_snake(domain)}_{_to_snake(entity)}"


@dataclass
class ValidationIssue:
    """Structurally identical to (and duck-type compatible with, via
    ``to_dict``) ``app.generator.spec.ValidationIssue`` — kept as a separate
    class here to avoid a circular import (``spec.py`` imports this module)."""

    severity: str
    message: str
    field: str | None = None
    source: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"severity": self.severity, "message": self.message}
        if self.field:
            out["field"] = self.field
        if self.source:
            out["source"] = self.source
        return out


def validate_medallion_fields(layer: str, *, curated_entity: str | None, mart_domain: str | None) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if layer == "bronze":
        return issues
    entity = (curated_entity or "").strip()
    hint = MEDALLION_LAYER_TARGET_PATTERN_HINT[layer]
    if not entity:
        issues.append(
            ValidationIssue("error", f'A curated entity name is required for the {layer} layer (used to build "{hint}").', "curatedEntity")
        )
    elif not _ENTITY_TOKEN_RE.match(_to_snake(entity)):
        issues.append(ValidationIssue("error", f'Curated entity name "{entity}" must be lowercase letters, digits, and underscores.', "curatedEntity"))
    if layer == "gold":
        domain = (mart_domain or "").strip()
        gold_hint = MEDALLION_LAYER_TARGET_PATTERN_HINT["gold"]
        if not domain:
            issues.append(
                ValidationIssue("error", f'A mart domain is required for the Gold layer (used to build "{gold_hint}").', "martDomain")
            )
        elif not _ENTITY_TOKEN_RE.match(_to_snake(domain)):
            issues.append(ValidationIssue("error", f'Mart domain "{domain}" must be lowercase letters, digits, and underscores.', "martDomain"))
    return issues


# Mirrors medallion.ts MAX_TRANSFORMATION_RULES — codegen slices to this same cap (G1).
MAX_TRANSFORMATION_RULES = 10


def validate_transformation_rules(layer: str, rules: list[TransformationRule]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if layer == "bronze":
        return issues
    allowed_types = set(MEDALLION_LAYER_TRANSFORM_TYPES.get(layer, ()))
    if len(rules) > MAX_TRANSFORMATION_RULES:
        issues.append(ValidationIssue("error", f"Maximum {MAX_TRANSFORMATION_RULES} transformations allowed.", "transformations"))
    if len(rules) == 0:
        issues.append(ValidationIssue("error", f"At least one transformation is required for the {layer} layer.", "transformations"))
    for i, rule in enumerate(rules):
        label = rule.name.strip() or f"Transformation {i + 1}"
        if not rule.name.strip():
            issues.append(ValidationIssue("error", f"Transformation {i + 1} needs a name.", "transformations"))
        if rule.type not in allowed_types:
            issues.append(ValidationIssue("error", f'Transformation "{label}" type "{rule.type}" isn\'t valid for the {layer} layer.', "transformations"))
        if rule.type == "conform_columns" and not (rule.columns or "").strip():
            issues.append(ValidationIssue("error", f'Transformation "{label}" (conform_columns) needs a column mapping.', "transformations"))
        elif rule.type == "cast_schema" and not (rule.cast_expression or "").strip():
            issues.append(ValidationIssue("error", f'Transformation "{label}" (cast_schema) needs at least one "column:TYPE" cast.', "transformations"))
        elif rule.type == "dedupe":
            if not (rule.key_columns or "").strip():
                issues.append(ValidationIssue("error", f'Transformation "{label}" (dedupe) needs key column(s).', "transformations"))
            if not (rule.order_column or "").strip():
                issues.append(ValidationIssue("error", f'Transformation "{label}" (dedupe) needs an order column (which row "wins").', "transformations"))
        elif rule.type == "scd2":
            if not (rule.key_columns or "").strip():
                issues.append(ValidationIssue("error", f'Transformation "{label}" (scd2) needs natural key column(s).', "transformations"))
            if not (rule.order_column or "").strip():
                issues.append(ValidationIssue("error", f'Transformation "{label}" (scd2) needs a change-detection column.', "transformations"))
            if not (rule.columns or "").strip():
                issues.append(ValidationIssue("warning", f'Transformation "{label}" (scd2) has no tracked columns — only the keys and change-detection column are kept in history.', "transformations"))
        elif rule.type == "join":
            if not (rule.join_entity or "").strip():
                issues.append(ValidationIssue("error", f'Transformation "{label}" (join) needs the other entity/table name.', "transformations"))
            if not (rule.join_condition or "").strip():
                issues.append(ValidationIssue("error", f'Transformation "{label}" (join) needs a join condition.', "transformations"))
            if not (rule.columns or "").strip():
                issues.append(ValidationIssue("warning", f'Transformation "{label}" (join) has no output column list — "a.*, b.*" duplicates the join keys and SQL Server will reject it.', "transformations"))
        elif rule.type == "aggregate":
            if not (rule.group_by_columns or "").strip():
                issues.append(ValidationIssue("error", f'Transformation "{label}" (aggregate) needs GROUP BY column(s).', "transformations"))
            if not (rule.aggregate_expression or "").strip():
                issues.append(ValidationIssue("error", f'Transformation "{label}" (aggregate) needs at least one aggregate expression.', "transformations"))
        elif rule.type == "select_columns" and not (rule.columns or "").strip():
            issues.append(ValidationIssue("error", f'Transformation "{label}" (select_columns) needs the final column list.', "transformations"))
        elif rule.type == "filter" and not (rule.filter_condition or "").strip():
            issues.append(ValidationIssue("error", f'Transformation "{label}" (filter) needs a filter condition.', "transformations"))
    issues.extend(check_transformation_chain_columns(rules))
    return issues


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
            pairs = [split_mapping_pair(p) for p in _split_top_level(str(get("columns") or ""))]
            refs = [src.strip() for src, _ in pairs if _PLAIN_IDENT_RE.match(src.strip())]
            nxt = _projected_columns([dst.strip() or src for src, dst in pairs])
        elif rtype == "select_columns":
            items = _split_top_level(str(get("columns") or ""))
            refs = [c for c in (_TRAILING_ALIAS_RE.sub("", it).strip() for it in items) if _PLAIN_IDENT_RE.match(c)]
            nxt = _projected_columns(items)
        elif rtype == "cast_schema":
            cols = [split_mapping_pair(p)[0].strip() for p in _split_top_level(str(get("castExpression") or ""))]
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


_RULE_ATTRS = {
    "columns": "columns",
    "castExpression": "cast_expression",
    "keyColumns": "key_columns",
    "orderColumn": "order_column",
    "groupByColumns": "group_by_columns",
    "aggregateExpression": "aggregate_expression",
}


def check_transformation_chain_columns(rules: list[TransformationRule]) -> list[ValidationIssue]:
    """Port of checkTransformationChainColumns in medallion.ts — soft step-by-step column check:
    warns when a step reads a plain column an earlier projection dropped. Always a warning."""
    steps = [
        (rule.name.strip() or f"Transformation {i + 1}", rule.type, lambda k, r=rule: getattr(r, _RULE_ATTRS[k]))
        for i, rule in enumerate(rules)
    ]
    return _chain_column_issues(steps, ValidationIssue)


def _hash_part(alias: str, column: str) -> str:
    """RUN-18: CONCAT_WS skips NULLs, so `(NULL,'x')` and `('x',NULL)` would hash the same; a NULL
    becomes CHAR(0) instead. Direct port of hashPart in medallion.ts."""
    return f"ISNULL(CAST({alias}.{column} AS NVARCHAR(MAX)), CHAR(0))"


def transformation_rule_sql(rule: TransformationRule, *, read_from: str, write_to: str, target_table: str) -> str:
    """Direct port of transformationRuleSql in medallion.ts."""
    if rule.type == "conform_columns":
        mapping = _split_top_level(rule.columns or "")
        select_items = []
        for pair in mapping:
            src, dst = split_mapping_pair(pair)
            select_items.append(f"{src} AS {dst}" if dst else src)
        select_list = ", ".join(select_items) if select_items else "*"
        return f"DROP TABLE IF EXISTS {write_to};\nSELECT {select_list} INTO {write_to} FROM {read_from};"

    if rule.type == "dedupe":
        keys = rule.key_columns or ""
        order = rule.order_column or "1"
        return (
            f"DROP TABLE IF EXISTS {write_to};\n"
            f"WITH Ranked AS (\n"
            f"  SELECT *, ROW_NUMBER() OVER (PARTITION BY {keys} ORDER BY {order} DESC) AS _rn\n"
            f"  FROM {read_from}\n"
            f")\n"
            f"SELECT * INTO {write_to} FROM Ranked WHERE _rn = 1;\n"
            f"ALTER TABLE {write_to} DROP COLUMN _rn;"
        )

    if rule.type == "cast_schema":
        casts = _split_top_level(rule.cast_expression or "")
        alters = []
        for pair in casts:
            col, sql_type = split_mapping_pair(pair)
            if col and sql_type:
                alters.append(f"ALTER TABLE {write_to} ALTER COLUMN {col} {sql_type};")
        return f"DROP TABLE IF EXISTS {write_to};\nSELECT * INTO {write_to} FROM {read_from};\n" + "\n".join(alters)

    if rule.type == "scd2":
        keys = [k.strip() for k in (rule.key_columns or "").split(",") if k.strip()]
        order = rule.order_column or "modified_date"
        # G3: carry the tracked business attributes (`columns`) and detect change via a row hash of them.
        attrs = [c for c in _split_top_level(rule.columns or "") if c not in keys and c != order]
        on_clause = " AND ".join(f"tgt.{k} = src.{k}" for k in keys) or "1=0"
        # DMT-09: the history columns take the rule's names when set (the target DDL's own names).
        is_current = (rule.current_flag_column or "").strip() or "_is_current"
        valid_from = (rule.valid_from_column or "").strip() or "_valid_from"
        valid_to = (rule.valid_to_column or "").strip() or "_valid_to"
        insert_cols = ", ".join([*keys, *attrs, order, is_current, valid_from, valid_to])
        new_row_select = ", ".join([*(f"src.{k}" for k in keys), *(f"src.{a}" for a in attrs), f"src.{order}", "1", "GETUTCDATE()", "NULL"])
        first_key = keys[0] if keys else "id"
        if attrs:
            tracked = [*attrs, order]
            changed = (
                f"HASHBYTES('SHA2_256', CONCAT_WS('|', {', '.join(_hash_part('tgt', c) for c in tracked)})) <> "
                f"HASHBYTES('SHA2_256', CONCAT_WS('|', {', '.join(_hash_part('src', c) for c in tracked)}))"
            )
        else:
            changed = f"tgt.{order} <> src.{order}"
        # One source row per key (the latest by the change column): a source holding several versions of a
        # key — e.g. a Gold SCD2 over a Silver SCD2 history table — would otherwise match one target row
        # twice, which MERGE rejects.
        latest = (
            f"(SELECT * FROM (SELECT *, ROW_NUMBER() OVER (PARTITION BY {', '.join(keys)} ORDER BY {order} DESC) "
            f"AS _scd2_rn FROM {read_from}) v WHERE _scd2_rn = 1)"
            if keys
            else read_from
        )
        return (
            f"-- Step 1: close out changed current rows.\n"
            f"MERGE {target_table} AS tgt\n"
            f"USING {latest} AS src\n"
            f"ON {on_clause} AND tgt.{is_current} = 1\n"
            f"WHEN MATCHED AND {changed} THEN\n"
            f"  UPDATE SET tgt.{is_current} = 0, tgt.{valid_to} = GETUTCDATE();\n"
            f"-- Step 2: insert new + newly-changed rows as the new current version.\n"
            f"INSERT INTO {target_table} ({insert_cols})\n"
            f"SELECT {new_row_select}\n"
            f"FROM {latest} src\n"
            f"LEFT JOIN {target_table} tgt ON {on_clause} AND tgt.{is_current} = 1\n"
            f"WHERE tgt.{first_key} IS NULL;"
        )

    if rule.type == "join":
        join_type = rule.join_type or "INNER"
        other = rule.join_entity or "curated.other_entity"
        condition = rule.join_condition or "1=1"
        # G4: `a.*, b.*` duplicates the join-key columns, which SELECT ... INTO rejects — prefer the
        # rule's explicit projection (`columns`); the bare fallback is flagged by validation.
        projection = (rule.columns or "").strip() or "a.*, b.*"
        return (
            f"DROP TABLE IF EXISTS {write_to};\n"
            f"SELECT {projection}\n"
            f"INTO {write_to}\n"
            f"FROM {read_from} a\n"
            f"{join_type} JOIN {other} b ON {condition};"
        )

    if rule.type == "aggregate":
        group_by = rule.group_by_columns or ""
        agg = rule.aggregate_expression or "COUNT(*) AS row_count"
        return (
            f"DROP TABLE IF EXISTS {write_to};\n"
            f"SELECT {group_by}, {agg}\n"
            f"INTO {write_to}\n"
            f"FROM {read_from}\n"
            f"GROUP BY {group_by};"
        )

    if rule.type == "select_columns":
        cols = (rule.columns or "*").strip() or "*"
        return f"DROP TABLE IF EXISTS {write_to};\nSELECT {cols} INTO {write_to} FROM {read_from};"

    if rule.type == "filter":
        condition = (rule.filter_condition or "").strip() or "1=1"
        return f"DROP TABLE IF EXISTS {write_to};\nSELECT * INTO {write_to} FROM {read_from} WHERE {condition};"

    return f'-- Unsupported transformation type "{rule.type}".'


def layer_load_key_columns(rules: list[TransformationRule], fallback: str | None = None) -> list[str]:
    """G5: the grain of the transformed rows — the last dedupe/scd2 key or aggregate
    GROUP BY in the chain, else the pipeline's upsert keys. Port of layerLoadKeyColumns."""
    keys = fallback or ""
    for rule in rules:
        if rule.type in ("dedupe", "scd2") and (rule.key_columns or "").strip():
            keys = rule.key_columns or ""
        elif rule.type == "aggregate" and (rule.group_by_columns or "").strip():
            keys = rule.group_by_columns or ""
    return [k.strip() for k in keys.split(",") if k.strip()]


def layer_final_load_sql(target_table: str, read_from: str, *, full_refresh: bool, keys: list[str]) -> str:
    """G5: idempotent final load — truncate-and-load on a full refresh, delete-then-insert
    on the key columns otherwise, so a rerun/restart (§34) never duplicates rows."""
    # RUN-09: columns are matched by name at run time, not by position — a target with a different column
    # order or extra (e.g. audit) columns loads correctly; target-only columns keep their defaults.
    columns = (
        "-- Insert by column name: the staged columns that also exist in the target.\n"
        "DECLARE @cols NVARCHAR(MAX) = (SELECT STRING_AGG(QUOTENAME(s.name), ', ') WITHIN GROUP (ORDER BY s.column_id) "
        f"FROM sys.columns s WHERE s.object_id = OBJECT_ID(N'{read_from}') AND COL_LENGTH(N'{target_table}', s.name) IS NOT NULL);\n"
        f"IF @cols IS NULL THROW 50001, N'No column of {read_from} exists in {target_table}.', 1;"
    )
    insert = f"EXEC(N'INSERT INTO {target_table} (' + @cols + N') SELECT ' + @cols + N' FROM {read_from};');"
    if full_refresh:
        return f"{columns}\nBEGIN TRANSACTION;\nTRUNCATE TABLE {target_table};\n{insert}\nCOMMIT;"
    if not keys:
        return f"-- No key columns (dedupe/scd2 keys, aggregate GROUP BY or upsert keys): a rerun appends duplicates.\n{columns}\n{insert}"
    match = " AND ".join(f"tgt.{k} = src.{k}" for k in keys)
    return (
        f"{columns}\n"
        f"BEGIN TRANSACTION;\n"
        f"DELETE tgt FROM {target_table} tgt WHERE EXISTS (SELECT 1 FROM {read_from} src WHERE {match});\n"
        f"{insert}\n"
        f"COMMIT;"
    )
