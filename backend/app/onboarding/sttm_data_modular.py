"""Data Modular STTM template adapter (AGENTS.md §33.14).

The sibling Data Modeling Accelerator (`data_moduler/`) exports its Source-to-Target Mapping as
`STTM_Data_Modular_Template_v2b.xlsx` — one workbook per target table (or a `.zip` of them, its
layer-wise bundle), each with one `STTM_Template` sheet:

* **Section 1** (rows 3-65): `Attribute | Value` pairs of table-level metadata (`Target_Table_ID`,
  `Target_Layer`, `Load_Type`, `SCD_Type`, ...).
* **Section 2** (header row starting `Record_Type`, row 68 in the shipped template): one row per
  source contribution to a target column, with transformation, lookup, aggregation and DQ cells.

adf_accelerator's registry object is *typed steps* (conform/cast/dedupe/scd2/join for Silver,
join/aggregate/select/scd2 for Gold) plus a small DQ rule set, so this module translates the
column-level mapping into that shape. It produces the same wire body `sttm_parser` builds from the
legacy per-sheet layout, so the exact same validators, commit path and generator run afterwards —
there is no second transformation engine. Section 2 cells that change the data but have no ADF
equivalent are reported as a sheet `warning` (or `error` when dropping them would produce wrong
output); Section 1 governance/physical cells with no registry field are not imported. See
`gaps.md` (DMT-*) for both lists.

Kept free of `openpyxl` worksheet specifics beyond cell reads so the mapping rules stay testable.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from app.onboarding.sttm import DQ_PCT_RULE_TYPES, DQ_RULE_TYPES, MAX_DQ_RULES, Issue, to_snake

TEMPLATE_SHEET = "STTM_Template"
TEMPLATE_FILE_NAME = "STTM_Data_Modular_Template_v2b.xlsx"
RECORD_TYPE_HEADER = "Record_Type"
_SECTION1_FIRST_ROW = 3
_HEADER_SCAN_LIMIT = 200  # Section 2's header row is found by scanning column A, not hardcoded

_RECORD_DATA = "DATA"
_RECORD_CONTROL = "CONTROL"
_FIELD_TRANSFORMATIONS = "transformations"
_FIELD_DQ = "dataQualityRules"

_LAYER_ALIASES: dict[str, str] = {
    "SILVER": "silver", "CURATED": "silver",
    "GOLD": "gold", "MART": "gold",
    "BRONZE": "bronze", "RAW": "bronze",
}
_WRITE_BEHAVIORS: dict[str, str] = {
    "MERGE": "merge", "UPSERT": "upsert", "REPLACE": "overwrite", "OVERWRITE": "overwrite",
    "INSERT": "append", "APPEND": "append", "INSERT_ONLY": "append",
}
_DELTA_LOAD_TYPES = frozenset({"INCREMENTAL", "CDC"})
_DQ_TYPE_ALIASES: dict[str, str] = {
    "NOT_NULL": "IS_NOT_NULL", "IS_NOT_NULL": "IS_NOT_NULL", "NOTNULL": "IS_NOT_NULL",
    "UNIQUE": "UNIQUE", "UNIQUENESS": "UNIQUE",
    "RANGE": "RANGE_BETWEEN", "RANGE_BETWEEN": "RANGE_BETWEEN",
    "REGEX": "REGEX_MATCH", "REGEX_MATCH": "REGEX_MATCH", "PATTERN": "REGEX_MATCH",
    "LENGTH": "LENGTH_LE", "LENGTH_LE": "LENGTH_LE", "MAX_LENGTH": "LENGTH_LE",
    "ROW_COUNT": "ROW_COUNT_MIN", "ROW_COUNT_MIN": "ROW_COUNT_MIN",
    "FRESHNESS": "FRESHNESS_HOURS", "FRESHNESS_HOURS": "FRESHNESS_HOURS",
}
_BLOCKING_DQ_ACTIONS = frozenset({"FAIL", "ABORT", "BLOCK", "STOP", "REJECT", "QUARANTINE"})
_BLOCKING_DQ_SEVERITIES = frozenset({"ERROR", "CRITICAL", "HIGH", "BLOCKER"})
_YES = frozenset({"Y", "YES", "TRUE", "1"})
# Data Modular writes the full S12.9 load policy (merge key, watermark, ...) as one JSON line in
# `Table_Remarks`, because the template has no cell for the merge key; a hand-authored workbook lacks it.
_LOAD_POLICY_REMARK = "load_policy: "
_QUALIFIED = re.compile(r"\b([A-Za-z_][\w$]*)\.([A-Za-z_][\w$]*)\b")


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _code(value: Any) -> str:
    """Business spellings ("Pass Through", "scd-2") to one comparable token ("PASS_THROUGH", "SCD_2")."""
    return re.sub(r"[^A-Z0-9]+", "_", _text(value).upper()).strip("_")


def _split_list(value: Any) -> list[str]:
    return [part.strip() for part in re.split(r"[,|;]", _text(value)) if part.strip()]


def is_data_modular_sheet(ws: Any) -> bool:
    """True when `ws` is a Data Modular `STTM_Template` sheet (has a Section 2 `Record_Type` header)."""
    return ws.title == TEMPLATE_SHEET and _section2_header_row(ws) is not None


def _section2_header_row(ws: Any) -> int | None:
    for row in range(1, _HEADER_SCAN_LIMIT + 1):
        if _text(ws.cell(row=row, column=1).value) == RECORD_TYPE_HEADER:
            return row
    return None


@dataclass
class MappingRow:
    number: int
    values: dict[str, Any]

    def get(self, header: str) -> str:
        return _text(self.values.get(header))


@dataclass
class DataModularSheet:
    section1: dict[str, str]
    rows: list[MappingRow] = field(default_factory=list)

    def attr(self, name: str) -> str:
        return self.section1.get(name, "")

    def first_attr(self, *names: str) -> str:
        return next((self.attr(n) for n in names if self.attr(n)), "")


def read_sheet(ws: Any) -> DataModularSheet:
    """Reads Section 1's `Attribute | Value` pairs and Section 2's non-empty rows."""
    header_row = _section2_header_row(ws)
    if header_row is None:
        raise ValueError(f'Sheet "{ws.title}" has no Section 2 "{RECORD_TYPE_HEADER}" header row.')
    section1: dict[str, str] = {}
    for row in range(_SECTION1_FIRST_ROW, header_row):
        name = _text(ws.cell(row=row, column=1).value)
        if name and not name.upper().startswith("SECTION"):
            section1[name] = _text(ws.cell(row=row, column=2).value)
    headers = [_text(c.value) for c in next(ws.iter_rows(min_row=header_row, max_row=header_row))]
    rows: list[MappingRow] = []
    for number, cells in enumerate(ws.iter_rows(min_row=header_row + 1, values_only=True), start=header_row + 1):
        values = {h: v for h, v in zip(headers, cells) if h and v not in (None, "")}
        if values:
            rows.append(MappingRow(number, values))
    return DataModularSheet(section1, rows)


# --------------------------------------------------------------------------- column mapping
@dataclass
class _Column:
    """One target column: the first (primary-source) row of its Mapping_ID."""

    row: MappingRow
    target: str
    expression: str  # unqualified SQL producing the column, "" when the row gives nothing to select
    source_column: str
    ordinal: int

    @property
    def is_passthrough(self) -> bool:
        return self.expression == self.target

    @property
    def is_aggregate(self) -> bool:
        return bool(self.row.get("Aggregation_Function") or self.row.get("Aggregation_Expression")) or (
            _code(self.row.get("Transformation_Type")) == "AGGREGATE"
        )


def _strip_qualifiers(expression: str, tables: set[str]) -> str:
    """`raw_customer.first_name` -> `first_name` for the tables the chain has already flattened."""
    lowered = {t.lower() for t in tables}
    return _QUALIFIED.sub(lambda m: m.group(2) if m.group(1).lower() in lowered else m.group(0), expression)


# DMT-06: the template's ANSI/Spark idioms that have a safe T-SQL spelling. Applied outside string
# literals only. `||` becomes T-SQL `+`: both yield NULL when an operand is NULL (CONCAT would not).
_TSQL_REWRITES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b(?:current_timestamp|now)\s*\(\s*\)", re.I), "CURRENT_TIMESTAMP"),
    (re.compile(r"\bcurrent_date\b(?:\s*\(\s*\))?", re.I), "CAST(GETDATE() AS DATE)"),
    (re.compile(r"\b(?:nvl|ifnull)\s*\(", re.I), "ISNULL("),
    # RUN-11: T-SQL SUBSTRING needs a length; a two-simple-argument call reads to the end of the string.
    (re.compile(r"\bsubstr(?:ing)?\s*\(\s*([^(),]+?)\s*,\s*([^(),]+?)\s*\)", re.I), r"SUBSTRING(\1, \2, 2147483647)"),
    (re.compile(r"\bsubstr\s*\(", re.I), "SUBSTRING("),
    (re.compile(r"\b(?:length|char_length|character_length)\s*\(", re.I), "LEN("),
    (re.compile(r"\blcase\s*\(", re.I), "LOWER("),
    (re.compile(r"\bucase\s*\(", re.I), "UPPER("),
    (re.compile(r"\bceil\s*\(", re.I), "CEILING("),
    (re.compile(r"\bpow\s*\(", re.I), "POWER("),
    (re.compile(r"\bln\s*\(", re.I), "LOG("),
    # Single-argument casts only; a format argument (`to_date(x, 'yyyy-MM-dd')`) is left as written.
    (re.compile(r"\bto_date\s*\(\s*([^(),]+?)\s*\)", re.I), r"CAST(\1 AS DATE)"),
    (re.compile(r"\bto_timestamp\s*\(\s*([^(),]+?)\s*\)", re.I), r"CAST(\1 AS DATETIME2)"),
    # Two simple (unnested) arguments only; argument order follows T-SQL's DATEADD/DATEDIFF/CHARINDEX.
    (re.compile(r"\bdate_add\s*\(\s*([^(),]+?)\s*,\s*([^(),]+?)\s*\)", re.I), r"DATEADD(day, \2, \1)"),
    (re.compile(r"\bdate_sub\s*\(\s*([^(),]+?)\s*,\s*([^(),]+?)\s*\)", re.I), r"DATEADD(day, -(\2), \1)"),
    (re.compile(r"\bdatediff\s*\(\s*([^(),]+?)\s*,\s*([^(),]+?)\s*\)", re.I), r"DATEDIFF(day, \2, \1)"),
    (re.compile(r"\binstr\s*\(\s*([^(),]+?)\s*,\s*([^(),]+?)\s*\)", re.I), r"CHARINDEX(\2, \1)"),
    (re.compile(r"\bnvl2\s*\(\s*([^(),]+?)\s*,\s*([^(),]+?)\s*,\s*([^(),]+?)\s*\)", re.I), r"IIF(\1 IS NOT NULL, \2, \3)"),
    # RUN-11: date parts and month arithmetic. Spark's dayofweek is 1 = Sunday, T-SQL's default DATEFIRST;
    # weekofyear is ISO in Spark.
    (re.compile(r"\badd_months\s*\(\s*([^(),]+?)\s*,\s*([^(),]+?)\s*\)", re.I), r"DATEADD(month, \2, \1)"),
    (re.compile(r"\blast_day\s*\(\s*([^(),]+?)\s*\)", re.I), r"EOMONTH(\1)"),
    *(
        (re.compile(rf"\b{spark}\s*\(\s*([^(),]+?)\s*\)", re.I), tsql)
        for spark, tsql in (
            ("dayofmonth", r"DAY(\1)"), ("dayofweek", r"DATEPART(weekday, \1)"), ("dayofyear", r"DATEPART(dayofyear, \1)"),
            ("weekofyear", r"DATEPART(iso_week, \1)"), ("quarter", r"DATEPART(quarter, \1)"),
        )
    ),
    # Spark lpad/rpad cut a longer string to the first n characters.
    (
        re.compile(r"\blpad\s*\(\s*([^(),]+?)\s*,\s*([^(),]+?)\s*,\s*([^(),]+?)\s*\)", re.I),
        r"IIF(LEN(\1) >= \2, LEFT(\1, \2), RIGHT(REPLICATE(\3, \2) + \1, \2))",
    ),
    (re.compile(r"\brpad\s*\(\s*([^(),]+?)\s*,\s*([^(),]+?)\s*,\s*([^(),]+?)\s*\)", re.I), r"LEFT(\1 + REPLICATE(\3, \2), \2)"),
    (re.compile(r"=\s*true\b", re.I), "= 1"),
    (re.compile(r"=\s*false\b", re.I), "= 0"),
    (re.compile(r"\bif\s*\(", re.I), "IIF("),
    # RUN-11: initcap upper-cases each space-separated word's first letter and lower-cases the rest
    # (STRING_SPLIT's ordinal argument needs SQL Server 2022 / Azure SQL Database). NULL stays NULL.
    (
        re.compile(r"\binitcap\s*\(\s*([^(),]+?)\s*\)", re.I),
        r"(SELECT STRING_AGG(UPPER(LEFT(value, 1)) + LOWER(SUBSTRING(value, 2, 2147483647)), ' ') "
        r"WITHIN GROUP (ORDER BY ordinal) FROM STRING_SPLIT(\1, ' ', 1))",
    ),
    # RUN-11: hashes (Spark hashes the UTF-8 bytes, so the value is cast to VARCHAR: equal for ASCII
    # text) and epoch seconds (Spark applies the session time zone; T-SQL reads the value as given).
    *(
        (
            re.compile(rf"\b{spark}\s*\(\s*([^(),]+?)\s*{bits}\)", re.I),
            rf"LOWER(CONVERT(VARCHAR({width}), HASHBYTES('{algo}', CAST(\1 AS VARCHAR(MAX))), 2))",
        )
        for spark, bits, algo, width in (
            ("md5", "", "MD5", 32), ("sha1", "", "SHA1", 40),
            ("sha2", r",\s*(?:256|0)\s*", "SHA2_256", 64), ("sha2", r",\s*512\s*", "SHA2_512", 128),
        )
    ),
    (re.compile(r"\bunix_timestamp\s*\(\s*\)", re.I), "DATEDIFF_BIG(SECOND, '1970-01-01', GETUTCDATE())"),
    (re.compile(r"\bunix_timestamp\s*\(\s*([^(),]+?)\s*\)", re.I), r"DATEDIFF_BIG(SECOND, '1970-01-01', \1)"),
    (
        re.compile(r"\bfrom_unixtime\s*\(\s*([^(),]+?)\s*\)", re.I),
        r"FORMAT(DATEADD(SECOND, \1, '1970-01-01'), 'yyyy-MM-dd HH:mm:ss')",
    ),
    # Spark type names inside a CAST; Spark `float` is 4-byte (T-SQL REAL), `double` 8-byte (FLOAT).
    # `float` is rewritten first, so the FLOAT that `double` becomes is not turned into REAL.
    *(
        (re.compile(rf"\bAS\s+{spark}\s*\)", re.I), f"AS {tsql})")
        for spark, tsql in (
            ("string", "NVARCHAR(MAX)"), ("float", "REAL"), ("double", "FLOAT"), ("boolean", "BIT"),
            ("long", "BIGINT"), ("timestamp", "DATETIME2"),
        )
    ),
    (re.compile(r"\|\|"), "+"),
)
_STRING_LITERAL = re.compile(r"('(?:[^']|'')*')")
_CAST_SHORTHAND = re.compile(r"([A-Za-z_][\w.]*)::([A-Za-z_]\w*(?:\s*\([\d,\s]+\))?)")
# DMT-06: a call or parenthesised group with no parentheses inside; rewritten innermost first.
_INNERMOST_GROUP = re.compile(r"(?:\b[A-Za-z_]\w*\s*)?\([^()]*\)")
_DATE_FORMAT = re.compile(r"\bdate_format\s*\(\s*([^(),]+?)\s*,\s*\x00(\d+)\x00\s*\)", re.I)
# RUN-11: Java date-pattern letters T-SQL FORMAT (.NET) reads the same (y M d H h m s) or under another
# letter (E -> ddd/dddd day name, a -> tt AM/PM, S -> f fraction).
_PORTABLE_DATE_PATTERN = re.compile(r"'[yMdHhmsEaSwDQq\-/:., _]*'")
_JAVA_ONLY_DATE_LETTERS = re.compile(r"E+|a|S+")
# RUN-11: pattern letters .NET FORMAT has no specifier for: week of year (w), day of year (D) and
# quarter (Q/q), plus the narrow day name (EEEEE). Each run becomes its own DATEPART/LEFT segment.
_DATE_LETTER_RUN = re.compile(r"w+|D+|[Qq]+|E{5}|[^wDQqE]+|E{1,4}")
_DATEPART_LETTERS = {"w": "iso_week", "D": "dayofyear", "Q": "quarter", "q": "quarter"}
# RUN-11: trunc(date, unit) / date_trunc(unit, ts) with a literal unit; Spark's week starts on Monday.
_TRUNC = re.compile(r"\btrunc\s*\(\s*([^(),]+?)\s*,\s*\x00(\d+)\x00\s*\)", re.I)
_DATE_TRUNC = re.compile(r"\bdate_trunc\s*\(\s*\x00(\d+)\x00\s*,\s*([^(),]+?)\s*\)", re.I)
_TRUNC_UNITS = {
    "year": "year", "yyyy": "year", "yy": "year", "quarter": "quarter", "month": "month", "mon": "month",
    "mm": "month", "week": "week", "day": "day", "dd": "day", "hour": "hour", "minute": "minute",
}
_TRUNC_TSQL = {
    "year": "DATEFROMPARTS(YEAR({x}), 1, 1)",
    "quarter": "DATEFROMPARTS(YEAR({x}), (DATEPART(quarter, {x}) - 1) * 3 + 1, 1)",
    "month": "DATEFROMPARTS(YEAR({x}), MONTH({x}), 1)",
    "week": "DATEADD(day, -((DATEPART(weekday, {x}) + @@DATEFIRST - 2) % 7), CAST({x} AS DATE))",
    "day": "CAST({x} AS DATE)",
    "hour": "DATEADD(hour, DATEDIFF(hour, 0, {x}), CAST(0 AS DATETIME))",
    "minute": "DATEADD(minute, DATEDIFF(minute, 0, {x}), CAST(0 AS DATETIME))",
}
# date-only units for trunc(); date_trunc also takes day/hour/minute.
_TRUNC_DATE_UNITS = frozenset({"year", "quarter", "month", "week"})


def to_tsql(expression: str) -> str:
    """Rewrites known ANSI/Spark idioms to T-SQL, outside string literals."""
    # Literals are swapped for opaque tokens so a call with a literal argument (`instr(s, '@')`) still
    # matches as one call, while the literal's own text is never rewritten.
    literals: list[str] = []

    def _stash(match: re.Match[str]) -> str:
        literals.append(match.group(1))
        return f"\x00{len(literals) - 1}\x00"

    def _net_pattern(chunk: str) -> str:
        # A one-letter .NET pattern ('M', 'd') is a standard format; '%' makes it the custom specifier.
        if len(chunk) == 1 and chunk.isalpha() and chunk not in "Ea":
            return f"%{chunk}"
        return _JAVA_ONLY_DATE_LETTERS.sub(
            lambda m: {"E": "dddd" if len(m.group(0)) >= 4 else "ddd", "a": "tt"}.get(m.group(0)[0], "f" * len(m.group(0))),
            chunk,
        )

    def _date_format(match: re.Match[str]) -> str:
        value, pattern = match.group(1), literals[int(match.group(2))]
        if not _PORTABLE_DATE_PATTERN.fullmatch(pattern):
            return match.group(0)
        runs = _DATE_LETTER_RUN.findall(pattern[1:-1])
        if not any(r[0] in _DATEPART_LETTERS or r == "EEEEE" for r in runs):
            return f"FORMAT({value}, '{_net_pattern(pattern[1:-1])}')"
        # Concatenate FORMAT chunks with the DATEPART segments FORMAT can't express (RUN-11).
        segments: list[str] = []
        chunk = ""
        for run in runs:
            letter = run[0]
            if letter not in _DATEPART_LETTERS and run != "EEEEE":
                chunk += run
                continue
            if chunk:
                segments.append(f"FORMAT({value}, '{_net_pattern(chunk)}')" if re.search(r"[A-Za-z]", chunk) else f"'{chunk}'")
                chunk = ""
            if run == "EEEEE":
                segments.append(f"LEFT(FORMAT({value}, 'dddd'), 1)")
            elif letter in "Qq" and len(run) > 2:
                if len(run) > 3:
                    return match.group(0)  # QQQQ ("1st quarter") has no exact T-SQL spelling
                segments.append(f"'Q' + CAST(DATEPART(quarter, {value}) AS VARCHAR(1))")
            elif len(run) > 1:
                segments.append(f"RIGHT('{'0' * len(run)}' + CAST(DATEPART({_DATEPART_LETTERS[letter]}, {value}) AS VARCHAR(3)), {len(run)})")
            else:
                segments.append(f"CAST(DATEPART({_DATEPART_LETTERS[letter]}, {value}) AS VARCHAR(3))")
        if chunk:
            segments.append(f"FORMAT({value}, '{_net_pattern(chunk)}')" if re.search(r"[A-Za-z]", chunk) else f"'{chunk}'")
        return " + ".join(segments)

    def _truncate(value: str, unit_token: str, allowed: frozenset[str] | None, original: str) -> str:
        unit = _TRUNC_UNITS.get(literals[int(unit_token)].strip("'").strip().lower())
        if unit is None or (allowed is not None and unit not in allowed):
            return original
        return _TRUNC_TSQL[unit].format(x=value)

    def _rewrite(text: str) -> str:
        text = _DATE_FORMAT.sub(_date_format, text)
        text = _TRUNC.sub(lambda m: _truncate(m.group(1), m.group(2), _TRUNC_DATE_UNITS, m.group(0)), text)
        text = _DATE_TRUNC.sub(lambda m: _truncate(m.group(2), m.group(1), None, m.group(0)), text)
        for pattern, replacement in _TSQL_REWRITES:
            text = pattern.sub(replacement, text)
        return text

    # DMT-06: each innermost group is rewritten exactly once and stashed as a token, so an outer call
    # sees a rewritten nested argument as one simple argument (`datediff(current_date(), to_date(x))`).
    groups: list[str] = []

    def _group(match: re.Match[str]) -> str:
        groups.append(_rewrite(match.group(0)))
        return f"\x01{len(groups) - 1}\x01"

    text = _CAST_SHORTHAND.sub(r"CAST(\1 AS \2)", _STRING_LITERAL.sub(_stash, expression))
    while (grouped := _INNERMOST_GROUP.sub(_group, text)) != text:
        text = grouped
    text = _rewrite(text)
    while "\x01" in text:
        text = re.sub(r"\x01(\d+)\x01", lambda m: groups[int(m.group(1))], text)
    return re.sub(r"\x00(\d+)\x00", lambda m: literals[int(m.group(1))], text)


# RUN-11: Spark/ANSI syntax `to_tsql` doesn't rewrite and T-SQL doesn't have. to_date/to_timestamp
# only survive the rewrite in their two-argument (format) form.
_SPARK_ONLY_FUNCTIONS = (
    "date_format", "to_date", "to_timestamp", "unix_timestamp", "from_unixtime", "initcap", "lpad", "rpad",
    "months_between", "add_months", "last_day", "trunc", "date_trunc", "md5", "sha1", "sha2", "split",
    "explode", "array_contains", "size", "dayofweek", "dayofmonth", "dayofyear", "weekofyear", "quarter",
    "decode", "regexp_replace", "regexp_extract", "collect_list", "collect_set", "substring_index",
)
_SPARK_ONLY_CALL = re.compile(r"(?i)\b(" + "|".join(_SPARK_ONLY_FUNCTIONS) + r")\s*\(")
_SPARK_ONLY_SYNTAX = re.compile(r"(?i)<=>|\brlike\b|=\s*(?:true|false)\b")


def untranslated_spark(expression: str) -> list[str]:
    """RUN-11: the Spark/ANSI functions and operators left in an already-translated expression,
    outside string literals — each fails in the T-SQL Script activity."""
    text = _STRING_LITERAL.sub("''", expression)
    found = [m.group(1).lower() for m in _SPARK_ONLY_CALL.finditer(text)]
    found += [m.group(0).split()[0].lower() if m.group(0)[0] != "=" else "= true/false" for m in _SPARK_ONLY_SYNTAX.finditer(text)]
    return list(dict.fromkeys(found))


def _object_name(value: str) -> str:
    return value.split(".")[-1].strip()


def _columns(sheet: DataModularSheet, tables: set[str], issues: list[Issue]) -> list[_Column]:
    seen: set[str] = set()
    columns: list[_Column] = []
    for index, row in enumerate(r for r in sheet.rows if _code(r.get("Record_Type") or _RECORD_DATA) == _RECORD_DATA):
        mapping_id = row.get("Mapping_ID") or f"row{row.number}"
        target = row.get("Target_Column_Name")
        if mapping_id in seen or not target:
            continue  # an ADDITIONAL_SOURCE row of an N-to-1 mapping; its expression lives on the first row
        seen.add(mapping_id)
        raw = row.get("Aggregation_Expression") or row.get("Transformation_Expression") or row.get("Standard_SQL")
        source_column = row.get("Source_Column")
        if row.get("Lookup_Table") and not raw:
            raw = row.get("Lookup_Return_Column") or target
        expression = _strip_qualifiers(raw, tables) if raw else source_column
        if raw:
            expression = to_tsql(expression)
            if leftover := untranslated_spark(expression):
                issues.append(Issue("warning", f'Row {row.number}: expression for "{target}" uses {", ".join(leftover)}, which T-SQL lacks — the ADF Script fails at run time; rewrite it in T-SQL.', _FIELD_TRANSFORMATIONS))
        if not expression:
            issues.append(Issue("warning", f'Row {row.number}: target column "{target}" has no source column or expression — left out of the transformations.', _FIELD_TRANSFORMATIONS))
        ordinal_text = row.get("Target_Ordinal")
        ordinal = int(ordinal_text) if ordinal_text.isdigit() else 10_000 + index
        columns.append(_Column(row, target, expression, source_column, ordinal))
    columns.sort(key=lambda c: c.ordinal)
    return columns


def _sql_type(row: MappingRow) -> str:
    base = row.get("Target_Data_Type").upper()
    if not base or "(" in base:
        return base
    length, precision, scale = row.get("Target_Length"), row.get("Target_Precision"), row.get("Target_Scale")
    if precision and scale:
        return f"{base}({precision},{scale})"
    if precision or length:
        return f"{base}({precision or length})"
    return base


def _type_differs(row: MappingRow) -> bool:
    target = row.get("Target_Data_Type")
    return bool(target) and _code(target) != _code(row.get("Source_Data_Type"))


def load_policy(sheet: DataModularSheet) -> dict[str, Any]:
    """The `load_policy: {json}` line Data Modular writes into `Table_Remarks`, or {} when absent/unreadable."""
    for line in sheet.attr("Table_Remarks").splitlines():
        if line.startswith(_LOAD_POLICY_REMARK):
            try:
                policy = json.loads(line[len(_LOAD_POLICY_REMARK):])
            except ValueError:
                return {}
            return policy if isinstance(policy, dict) else {}
    return {}


def _keys(sheet: DataModularSheet) -> list[str]:
    explicit = _split_list(sheet.attr("SCD_Business_Key"))
    if explicit:
        return explicit
    unique_key = load_policy(sheet).get("unique_key")
    if isinstance(unique_key, list) and unique_key:
        return [str(k) for k in unique_key]
    flagged = [
        (r.get("Key_Sequence"), r.get("Target_Column_Name"))
        for r in sheet.rows
        if r.get("Target_Column_Name") and (_code(r.get("Natural_Key_Flag")) in _YES or _code(r.get("Primary_Key_Flag")) in _YES)
    ]
    flagged.sort(key=lambda kv: int(kv[0]) if kv[0].isdigit() else 10_000)
    return list(dict.fromkeys(name for _, name in flagged))


def _alias_join_condition(condition: str, lookup_name: str, source_tables: set[str]) -> str:
    """`raw_order.country_code = ref_country.code` -> `a.country_code = b.code` (the join step's aliases)."""
    sources = {t.lower() for t in source_tables}

    def alias(match: re.Match[str]) -> str:
        table = match.group(1).lower()
        if table == lookup_name.lower():
            return f"b.{match.group(2)}"
        return f"a.{match.group(2)}" if table in sources else match.group(0)

    return _QUALIFIED.sub(alias, condition)


def _lookup_joins(sheet: DataModularSheet, source_tables: set[str], issues: list[Issue]) -> list[dict[str, Any]]:
    """One `join` step per distinct Lookup_Table. The source side is aliased `a`, the lookup `b`."""
    joins: dict[str, dict[str, Any]] = {}
    for row in sheet.rows:
        table = row.get("Lookup_Table")
        if not table:
            continue
        lookup_name = _object_name(table)
        # DMT-04: an unqualified lookup reads from Section 1's Target_Schema when one is given.
        schema = sheet.attr("Target_Schema")
        entity = f"{schema}.{table}" if "." not in table and schema else table
        if table not in joins:
            condition = row.get("Lookup_Condition")
            if not condition and row.get("Lookup_Key"):
                condition = " AND ".join(f"{k} = {lookup_name}.{_object_name(k)}" for k in _split_list(row.get("Lookup_Key")))
            condition = _alias_join_condition(condition, lookup_name, source_tables)
            if "." not in table:
                where = f"joined as {entity} (Target_Schema)" if schema else "the join reads it from the target store's default schema"
                issues.append(Issue("warning", f'Lookup_Table "{table}" is not schema-qualified; {where}.', _FIELD_TRANSFORMATIONS))
            failure = _code(row.get("Lookup_Failure_Action"))
            joins[table] = {
                "name": f"lookup_{to_snake(lookup_name)}",
                "type": "join",
                "joinEntity": entity,
                "joinType": "INNER" if failure in ("REJECT", "FAIL") else "LEFT",
                "joinCondition": condition,
                "_returns": [],
                "_defaults": {},
            }
        for column in _split_list(row.get("Lookup_Return_Column")) or [row.get("Target_Column_Name")]:
            plain = _object_name(column)
            if plain and plain not in joins[table]["_returns"]:
                joins[table]["_returns"].append(plain)
            # DMT-04: a miss on a LEFT join gets the default instead of NULL.
            if plain and row.get("Lookup_Default_Value"):
                joins[table]["_defaults"][plain] = _sql_literal(row.get("Lookup_Default_Value"))
        unknown = _code(row.get("Unknown_Member_Handling")) or _code(row.get("Lookup_Failure_Action"))
        if unknown in ("DEFAULT", "UNKNOWN_MEMBER") and not row.get("Lookup_Default_Value"):
            issues.append(Issue("warning", f"Row {row.number}: {unknown} handling needs a Lookup_Default_Value — unmatched rows get NULL.", _FIELD_TRANSFORMATIONS))
    steps = []
    for step in joins.values():
        returns, defaults = step.pop("_returns"), step.pop("_defaults")
        step["columns"] = ", ".join(
            ["a.*", *(f"COALESCE(b.{c}, {defaults[c]}) AS {c}" if c in defaults else f"b.{c} AS {c}" for c in returns)]
        )
        steps.append(step)
    return steps


def _sql_literal(value: str) -> str:
    """A Lookup_Default_Value as SQL: numbers, NULL and already-quoted text pass through."""
    if re.fullmatch(r"-?\d+(\.\d+)?|NULL", value, re.I) or (value.startswith("'") and value.endswith("'")):
        return value
    return "'" + value.replace("'", "''") + "'"


def _scd2_step(sheet: DataModularSheet, columns: list[_Column], keys: list[str], order: str, issues: list[Issue]) -> dict[str, Any] | None:
    if not keys or not order:
        issues.append(Issue("error", "SCD_Type is SCD2 but the SCD business key (SCD_Business_Key or Natural/Primary_Key_Flag rows) or a change-detection column (Watermark_Column/Incremental_Column) is missing.", _FIELD_TRANSFORMATIONS))
        return None
    housekeeping = {sheet.attr(n) for n in ("SCD_Effective_From", "SCD_Effective_To", "SCD_Current_Flag", "SCD_Hash_Column") if sheet.attr(n)}
    tracked = [c.target for c in columns if c.target not in keys and c.target != order and c.target not in housekeeping]
    step = {"name": "scd2_history", "type": "scd2", "keyColumns": ", ".join(keys), "orderColumn": order, "columns": ", ".join(tracked)}
    # DMT-09: the scd2 step writes the template's own history column names when it names them.
    # A name that is already a key/tracked/change-detection column would be inserted twice, so the
    # generated default stays for that one, with a warning.
    taken = {*keys, *tracked, order}
    for cell, key, generated in (
        ("SCD_Current_Flag", "currentFlagColumn", "_is_current"),
        ("SCD_Effective_From", "validFromColumn", "_valid_from"),
        ("SCD_Effective_To", "validToColumn", "_valid_to"),
    ):
        name = sheet.attr(cell)
        if name and name in taken:
            issues.append(Issue("warning", f"{cell} {name} is also a mapped/key column; the history column is generated as {generated} instead — align the target DDL.", _FIELD_TRANSFORMATIONS))
        elif name:
            step[key] = name
    return step


def _filter_step(sheet: DataModularSheet, tables: set[str], issues: list[Issue]) -> dict[str, Any] | None:
    """DMT-03: the distinct `Filter_Condition`s (data_moduler repeats a table filter on every row) as
    one `filter` step on the Bronze input, before any join renames or drops its columns."""
    conditions = list(dict.fromkeys(row.get("Filter_Condition") for row in sheet.rows if row.get("Filter_Condition")))
    if not conditions:
        return None
    # RUN-10: the step reads the flattened Bronze table, so `raw_customer.is_deleted` loses its qualifier.
    translated = [to_tsql(_strip_qualifiers(c, tables)) for c in conditions]
    if any(t != c for t, c in zip(translated, conditions)):
        issues.append(Issue("warning", "Filter_Condition was rewritten from ANSI/Spark SQL to T-SQL; review the filter step.", _FIELD_TRANSFORMATIONS))
    if leftover := list(dict.fromkeys(f for t in translated for f in untranslated_spark(t))):
        issues.append(Issue("warning", f"Filter_Condition uses {', '.join(leftover)}, which T-SQL lacks — the filter step fails at run time; rewrite it in T-SQL.", _FIELD_TRANSFORMATIONS))
    condition = " AND ".join(f"({c})" for c in translated) if len(translated) > 1 else translated[0]
    return {"name": "filter_rows", "type": "filter", "filterCondition": condition}


def _silver_transformations(sheet: DataModularSheet, columns: list[_Column], joins: list[dict[str, Any]], keys: list[str], order: str, issues: list[Issue]) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = list(joins)
    # Always conform, even when every column passes through: it is what drops the unmapped Bronze
    # columns (and a join's `a.*` extras) so the Silver table holds exactly the mapped columns.
    selectable = [c for c in columns if c.expression]
    if selectable:
        pairs = [c.expression if c.is_passthrough else f"{c.expression}:{c.target}" for c in selectable]
        steps.append({"name": "conform_columns", "type": "conform_columns", "columns": ", ".join(pairs)})
    casts = [f"{c.target}:{_sql_type(c.row)}" for c in columns if _type_differs(c.row)]
    if casts:
        steps.append({"name": "cast_schema", "type": "cast_schema", "castExpression": ", ".join(casts)})
    if _code(sheet.attr("SCD_Type")) == "SCD2":
        scd2 = _scd2_step(sheet, columns, keys, order, issues)
        if scd2:
            steps.append(scd2)
    elif keys and order:
        steps.append({"name": "dedupe_latest", "type": "dedupe", "keyColumns": ", ".join(keys), "orderColumn": order})
    return steps


def _gold_transformations(sheet: DataModularSheet, columns: list[_Column], joins: list[dict[str, Any]], keys: list[str], order: str, issues: list[Issue]) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = list(joins)
    lookup_targets = {c.target for c in columns if c.row.get("Lookup_Table")}
    aggregates = [c for c in columns if c.is_aggregate]
    if aggregates:
        groups = list(dict.fromkeys(g for c in aggregates for g in _split_list(c.row.get("Aggregation_Group"))))
        group_columns = groups or [c.target for c in columns if not c.is_aggregate]
        by_target = {c.target: c for c in columns}
        # DMT-02: a derived/renamed grain column is staged under its target name first, so the
        # aggregate step can GROUP BY it.
        derived = [
            by_target[n] for n in group_columns
            if n in by_target and by_target[n].expression and not by_target[n].is_passthrough and n not in lookup_targets
        ]
        if derived:
            steps.append({"name": "derive_grain", "type": "select_columns", "columns": ", ".join(["*", *(f"{c.expression} AS {c.target}" for c in derived)])})
        steps.append({
            "name": "aggregate",
            "type": "aggregate",
            "groupByColumns": ", ".join(group_columns),
            "aggregateExpression": ", ".join(f"{c.expression} AS {c.target}" for c in aggregates),
        })
    if _code(sheet.attr("SCD_Type")) == "SCD2":
        scd2 = _scd2_step(sheet, columns, keys, order, issues)
        if scd2:
            steps.append(scd2)
    elif columns:
        # DMT-02: with no aggregate, derived/renamed columns are computed in the final select.
        steps.append({"name": "select_final", "type": "select_columns", "columns": ", ".join(
            f"{c.expression} AS {c.target}" if not aggregates and not c.is_passthrough and c.target not in lookup_targets else c.target
            for c in columns if c.expression
        )})
    return steps


# --------------------------------------------------------------------------- data quality
_LIKE_SPECIALS = {"%": "[%]", "_": "[_]", "[": "[[]"}
_REGEX_CLASS = re.compile(r"\[(\^?)((?:\\d|[A-Za-z0-9 .@\-])+)\]")
_REGEX_ESCAPES = {"d": "[0-9]", "w": "[A-Za-z0-9_]"}


def regex_to_like(pattern: str) -> str | None:
    """RUN-17: REGEX_MATCH is generated as PATINDEX (whole-value LIKE). A regex with an exact LIKE
    equivalent (literals, `.`, `.*`, `.+`, `\\d`, `\\w`, a bracket class, `{n}`, `^`/`$`, a `+`/`*`
    repeat at an unanchored edge, and `[^c]+c`) is translated; anything else (alternation, groups,
    `?`, other repeats, ranges) returns None. A value
    with no regex syntax is returned unchanged by the caller, never passed here as a regex."""
    text = pattern.strip()
    if "'" in text or not re.search(r"^\^|\$$|[+\\(){}|*?]", text):
        return None
    start, end = text.startswith("^"), text.endswith("$") and not text.endswith("\\$")
    body = text[1 if start else 0 : len(text) - 1 if end else len(text)]
    tokens: list[tuple[str, str | None]] = []
    i = 0
    while i < len(body):
        ch = body[i]
        if ch == ".":
            atom, i = "_", i + 1
        elif ch == "\\" and i + 1 < len(body):
            nxt = body[i + 1]
            atom = _REGEX_ESCAPES.get(nxt) or (None if nxt.isalnum() else _LIKE_SPECIALS.get(nxt, nxt))
            i += 2
        elif ch == "[":
            m = _REGEX_CLASS.match(body, i)
            if not m:
                return None
            atom, i = f"[{m.group(1)}{m.group(2).replace(chr(92) + 'd', '0-9')}]", m.end()
        elif ch in "()|?+*{}^$":
            return None
        else:
            atom, i = _LIKE_SPECIALS.get(ch, ch), i + 1
        if atom is None:
            return None
        quantifier = re.match(r"\{(\d{1,2})\}|[*+?]", body[i:])
        if quantifier and quantifier.group(1):
            atom *= int(quantifier.group(1))
        elif quantifier and quantifier.group(0) == "?":
            return None
        i += len(quantifier.group(0)) if quantifier else 0
        tokens.append((atom, quantifier.group(0) if quantifier and quantifier.group(0) in "*+" else None))
    out: list[str] = []
    for index, (atom, repeat) in enumerate(tokens):
        if repeat is None:
            out.append(atom)
        elif atom == "_":
            out.append("%" if repeat == "*" else "_%")
        elif (index == 0 and not start) or (index == len(tokens) - 1 and not end):
            # An unanchored edge absorbs the repeats: `^[A-Z]+` is "starts with [A-Z]".
            out.append("" if repeat == "*" else atom)
        elif repeat == "+" and index + 1 < len(tokens) and tokens[index + 1][1] is None and atom == f"[^{tokens[index + 1][0]}]":
            # `[^@]+@`: every character up to the first @ is a non-@, so `[^@]%@` is exact.
            out.append(f"{atom}%")
        else:
            return None
    return ("" if start else "%") + "".join(out) + ("" if end else "%")


def _pass_rate(threshold: str) -> float | None:
    """DMT-05: a `DQ_Threshold` written as a percentage ("95%", "99.5 %") is a minimum pass rate."""
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*%\s*", threshold or "")
    return float(match.group(1)) if match and float(match.group(1)) <= 100 else None


def _format_pct(value: float) -> str:
    return f"{round(value, 4):g}"


def _dq_value(rule: str, row: MappingRow, *, ignore_threshold: bool = False) -> str:
    expression, threshold = row.get("DQ_Rule_Expression"), ("" if ignore_threshold else row.get("DQ_Threshold"))
    if rule == "LENGTH_LE":
        return threshold or row.get("Target_Length") or expression
    if rule == "REGEX_MATCH":
        return regex_to_like(expression) or expression
    if rule == "RANGE_BETWEEN":
        between = re.search(r"(?i)between\s+(-?[\d.]+)\s+and\s+(-?[\d.]+)", expression)
        return f"{between.group(1)},{between.group(2)}" if between else (threshold or expression)
    return threshold or expression


# DMT-05: Section 2 cells with no ADF DQ equivalent, reported per row; Y/N flags count only when Y.
_UNCARRIED_DQ_CELLS = (
    "Constraint_Name", "Constraint_Type", "Constraint_Expression",
    "Reconciliation_Required", "Reconciliation_Type", "Reconciliation_Expression", "Reconciliation_Query",
    "Error_Handling_Strategy", "Reject_Allowed", "Reject_Table", "Quarantine_Table",
)
_FLAG_CELLS = frozenset({"Reconciliation_Required", "Reject_Allowed"})


def _dq_rules(sheet: DataModularSheet, issues: list[Issue]) -> list[dict[str, Any]]:
    rules: list[dict[str, Any]] = []
    for row in sheet.rows:
        column = row.get("Target_Column_Name")
        enforcement = "block" if (
            _code(row.get("DQ_Action")) in _BLOCKING_DQ_ACTIONS or _code(row.get("DQ_Severity")) in _BLOCKING_DQ_SEVERITIES
        ) else "warn"
        for raw_type in _split_list(row.get("DQ_Rule_Type")):
            rule = _DQ_TYPE_ALIASES.get(_code(raw_type))
            if rule is None or rule not in DQ_RULE_TYPES:
                issues.append(Issue("warning", f'Row {row.number}: DQ_Rule_Type "{raw_type}" on "{column}" has no ADF DQ equivalent ({", ".join(sorted(DQ_RULE_TYPES))}) — skipped.', _FIELD_DQ))
                continue
            single = len(_split_list(row.get("DQ_Rule_Type"))) == 1
            name = (row.get("DQ_Rule_ID") if single and row.get("DQ_Rule_ID") else "") or f"{rule.lower()}_{column}".strip("_")
            entry = {"name": name, "column": "" if rule == "ROW_COUNT_MIN" else column, "rule": rule, "enforcement": enforcement}
            threshold = row.get("DQ_Threshold")
            pass_rate = _pass_rate(threshold)
            value = _dq_value(rule, row, ignore_threshold=pass_rate is not None)
            if rule == "REGEX_MATCH" and value != row.get("DQ_Rule_Expression"):
                issues.append(Issue("warning", f'Row {row.number}: regex "{row.get("DQ_Rule_Expression")}" on "{column}" translated to the LIKE pattern "{value}" (REGEX_MATCH runs as PATINDEX).', _FIELD_DQ))
            if value and rule not in ("IS_NOT_NULL", "UNIQUE"):
                entry["value"] = value
            if pass_rate is not None and rule in DQ_PCT_RULE_TYPES:
                # DMT-05: a "95%" pass rate allows up to 5% violating rows.
                entry["maxViolationPct"] = _format_pct(100 - pass_rate)
            elif threshold and (pass_rate is not None or rule in ("IS_NOT_NULL", "UNIQUE")):
                issues.append(Issue("warning", f'Row {row.number}: DQ_Threshold "{threshold}" on {rule} "{column}" is not carried — the check fails on any violating row (only a percentage pass rate on a row-level rule is).', _FIELD_DQ))
            rules.append(entry)
        cells = [h for h in _UNCARRIED_DQ_CELLS if row.get(h) and (h not in _FLAG_CELLS or _code(row.get(h)) in _YES)]
        if cells:
            issues.append(Issue("warning", f'Row {row.number}: {", ".join(cells)} on "{column}" not carried — ADF DQ has no constraint, reconciliation or reject/quarantine routing.', _FIELD_DQ))
    if len(rules) > MAX_DQ_RULES:
        # DMT-05: keep the first MAX_DQ_RULES (sheet order) instead of rejecting the whole table.
        dropped = ", ".join(r["name"] for r in rules[MAX_DQ_RULES:])
        issues.append(Issue("warning", f"{len(rules)} DQ rules found; the registry holds at most {MAX_DQ_RULES} — kept the first {MAX_DQ_RULES}, skipped: {dropped}.", _FIELD_DQ))
        rules = rules[:MAX_DQ_RULES]
    return rules


# --------------------------------------------------------------------------- header
def _layer_issues(sheet: DataModularSheet, layer: str) -> list[Issue]:
    raw = sheet.attr("Target_Layer")
    if not raw:
        return [Issue("warning", f"Target_Layer is blank; treating the workbook as {layer}.", "layer")]
    resolved = _LAYER_ALIASES.get(_code(raw))
    if resolved == layer:
        return []
    if resolved == "bronze":
        return [Issue("error", "Target_Layer is BRONZE — Bronze objects are onboarded on the Source Onboarding page, not via STTM upload.", "layer")]
    return [Issue("error", f'Target_Layer "{raw}" doesn\'t match this {layer} upload — upload it to the {resolved or raw} registry instead.', "layer")]


def _watermark_type(sheet: DataModularSheet, column: str, issues: list[Issue]) -> str | None:
    if not column:
        return None
    row = next((r for r in sheet.rows if r.get("Target_Column_Name") == column or r.get("Source_Column") == column), None)
    sql_type = (row.get("Target_Data_Type") or row.get("Source_Data_Type") or "").lower() if row else ""
    if not sql_type:
        # The template has no watermark-type cell: the type comes from the column's Section 2 data types.
        where = f"row {row.number}" if row else "a Section 2 row (Target_Column_Name/Source_Column)"
        issues.append(Issue("error", f'Watermark column "{column}" needs a Target_Data_Type or Source_Data_Type on {where} — its watermark type is derived from it.', "watermarkColumnType"))
        return None
    if "rowversion" in sql_type or re.fullmatch(r"(var)?binary\s*\(\s*8\s*\)", sql_type):
        # DMT-14: an 8-byte rowversion orders like a bigint; a string watermark would compare hex text.
        issues.append(Issue("warning", f'Watermark "{column}" is {sql_type} — typed bigint; store its watermark value as CAST({column} AS BIGINT).', "watermarkColumnType"))
        return "bigint"
    if any(t in sql_type for t in ("date", "time")):
        return "datetime"
    if "bigint" in sql_type or sql_type.startswith("long"):
        return "bigint"
    if "int" in sql_type:
        return "int"
    return "string"


# DMT-08: Section 1 cells with no registry field. Reported once so the loss is visible.
_UNMAPPED_SECTION1 = (
    "Partition_Column", "Partition_Type", "Cluster_Columns", "Distribution_Key",
    "Target_Catalog", "Target_Database", "Target_Schema",
    "Execution_Environment", "Mapping_Version", "Approved_By", "Change_Request_ID",
    "SCD_Hash_Column", "SCD_Change_Detection_Rule",
)


# DMT-08: Section 1 governance cells carried as typed registry fields (Y/N flags and free text).
_GOVERNANCE_FLAG_CELLS = {
    "PII_Flag": "containsPii", "PHI_Flag": "containsPhi", "PCI_Flag": "containsPci",
    "Critical_Data_Element": "criticalDataElement", "Audit_Required": "auditRequired",
}
_GOVERNANCE_TEXT_CELLS = {"Retention_Period": "retentionPeriod", "Regulatory_Tag": "regulatoryTag", "Business_Criticality": "businessCriticality"}


def _governance_fields(sheet: DataModularSheet) -> dict[str, Any]:
    """A blank cell stays None (not stated); a filled flag is True only for Y/YES/TRUE/1."""
    fields: dict[str, Any] = {wire: (_code(sheet.attr(cell)) in _YES) if sheet.attr(cell) else None for cell, wire in _GOVERNANCE_FLAG_CELLS.items()}
    fields.update({wire: sheet.attr(cell) or None for cell, wire in _GOVERNANCE_TEXT_CELLS.items()})
    return fields


def _unmapped_cells(sheet: DataModularSheet) -> list[str]:
    filled = [
        n for n in sheet.section1
        if sheet.attr(n) and n in _UNMAPPED_SECTION1 and (not n.endswith("_Flag") or _code(sheet.attr(n)) in _YES)
    ]
    materialization = _code(sheet.attr("Materialization_Type"))
    if materialization and materialization != "MERGE":
        filled.append("Materialization_Type")
    return filled


def _unmapped_issue(filled: list[str]) -> Issue | None:
    if not filled:
        return None
    return Issue("warning", f"No ADF field, kept in the object's notes only: {', '.join(filled)}. The target schema comes from the layer's zone prefix.", "section1")


def _notes(sheet: DataModularSheet, filled: list[str]) -> str:
    """DMT-08: the governance/physical cells with no registry field ride along in `notes`, one
    `Name: value` line each, so they survive the import instead of being dropped."""
    lines = [f"{n}: {sheet.attr(n)}" for n in filled]
    description = sheet.attr("Target_Table_Description")
    if not lines:
        return description
    block = "Data Modular template:\n" + "\n".join(lines)
    return f"{description}\n\n{block}" if description else block


_UNRELEASED_MAPPING_STATUSES = {"DEPRECATED", "DRAFT", "REVIEW", "IN_REVIEW"}
_UNAPPROVED_STATUSES = {"PENDING", "REJECTED"}


def _approval_issue(sheet: DataModularSheet) -> Issue | None:
    """DMT-10: an unreleased (`Mapping_Status`) or unapproved (`Approval_Status`) mapping imports
    disabled, so it can't reach a pipeline until someone enables it on purpose."""
    mapping, approval = _code(sheet.attr("Mapping_Status")), _code(sheet.attr("Approval_Status"))
    if mapping in _UNRELEASED_MAPPING_STATUSES:
        return Issue("warning", f"Mapping_Status is {mapping} — imported disabled.", "enabled")
    if approval in _UNAPPROVED_STATUSES:
        return Issue("warning", f"Approval_Status is {approval} — imported disabled until the mapping is approved.", "enabled")
    return None


def to_registry_body(sheet: DataModularSheet, layer: str) -> tuple[dict[str, Any], list[Issue]]:
    """Translates one Data Modular workbook into the registry wire body `validate_object_header`,
    `validate_transformation_rules` and `validate_data_quality_rules` check. Returns the body plus
    the template-specific issues (layer mismatch, unsupported cells) found while translating."""
    issues = _layer_issues(sheet, layer)
    data_rows = [r for r in sheet.rows if _code(r.get("Record_Type") or _RECORD_DATA) == _RECORD_DATA]
    source_tables = {_object_name(r.get("Source_Object")) for r in data_rows if r.get("Source_Object")}
    lookup_tables = {_object_name(r.get("Lookup_Table")) for r in data_rows if r.get("Lookup_Table")}
    joins = _lookup_joins(sheet, source_tables, issues)
    columns = _columns(sheet, source_tables | lookup_tables, issues)
    keys = _keys(sheet)
    watermark = sheet.first_attr("Watermark_Column", "Incremental_Column") or str(load_policy(sheet).get("watermark_column") or "")
    load_type = _code(sheet.attr("Load_Type"))
    filter_step = _filter_step(sheet, source_tables, issues)
    if any(r.get("Source_Role").upper().startswith("ADDITIONAL") for r in data_rows) and len(source_tables) > 1 and not joins:
        issues.append(Issue("warning", f"Mappings read from {len(source_tables)} source objects without a Lookup/Join; the generated chain reads only the Build pipeline's own Bronze table.", _FIELD_TRANSFORMATIONS))

    build = _gold_transformations if layer == "gold" else _silver_transformations
    transformations = build(sheet, columns, joins, keys, watermark, issues)
    if filter_step:
        transformations.insert(0, filter_step)
    entity = sheet.first_attr("Target_Table_Name", "Target_Table_ID")
    unmapped = _unmapped_cells(sheet)
    classification = sheet.first_attr("Target_Subject_Area", "Target_Subdomain", "Target_Domain")
    # DMT-01: a plain export without a model domain still imports, tagged with its layer (warned below).
    tag = to_snake(classification) if classification else layer
    body: dict[str, Any] = {
        "objectKey": to_snake(sheet.attr("Target_Table_ID") or entity),
        "enabled": _approval_issue(sheet) is None,
        # The template has no `tag` cell; the most specific business classification stands in.
        "tag": tag,
        "curatedEntity": to_snake(_object_name(entity)) if entity else "",
        "martDomain": to_snake(sheet.attr("Target_Domain")) if layer == "gold" and sheet.attr("Target_Domain") else None,
        "sourceObjectKeys": sorted(to_snake(t) for t in source_tables),
        "owner": sheet.first_attr("Technical_Owner", "Data_Owner", "Data_Steward") or None,
        "batchGroup": sheet.first_attr("Pipeline_Name", "Load_Frequency") or None,
        "domain": sheet.first_attr("Target_Domain", "Target_Subject_Area") or None,
        "writeBehavior": _WRITE_BEHAVIORS.get(_code(sheet.attr("Load_Strategy")), "merge" if _code(sheet.attr("Materialization_Type")) == "MERGE" else "append"),
        "loadingBehavior": "DeltaLoad" if load_type in _DELTA_LOAD_TYPES else "FullLoad",
        "watermarkColumn": watermark or None,
        "watermarkColumnType": _watermark_type(sheet, watermark, issues),
        "businessKeys": keys,
        "transformations": transformations,
        "dataQualityRules": _dq_rules(sheet, issues),
        "dataClassification": sheet.attr("Data_Classification") or None,
        **_governance_fields(sheet),
        "notes": _notes(sheet, unmapped),
    }
    for extra in (_approval_issue(sheet), _unmapped_issue(unmapped)):
        if extra is not None:
            issues.append(extra)
    if not classification:
        issues.append(Issue("warning", f'No Target_Subject_Area, Target_Subdomain or Target_Domain in Section 1 — tagged "{layer}". Fill one of them to classify the object.', "tag"))
    return body, issues
