"""Generate docs/adf_forge_ddl.sql from docs/DDL.md.

DDL.md is the single hand-edited reference; this script derives the runnable script from it:

* Part 1 (PostgreSQL): the ``app`` schema block, renamed ``adf_forge_app``, with every table or column
  tagged ``(planned)`` dropped, plus ``COMMENT ON TABLE`` / ``COMMENT ON COLUMN`` built from the inline
  ``--`` comments so the documentation lives in the database catalog too.
* Part 2 (T-SQL): every ``control`` block, renamed ``adf_forge_control``, each create guarded by
  ``IF OBJECT_ID(...) IS NULL`` (the same form the generator emits), plus idempotent
  ``MS_Description`` extended properties per column.

Usage (from the repo's adf_accelerator/ folder):  python docs/scripts/build_ddl_sql.py
"""
from __future__ import annotations

import re
from pathlib import Path

DOCS = Path(__file__).resolve().parent.parent
SRC = DOCS / "DDL.md"
OUT = DOCS / "adf_forge_ddl.sql"

APP_SCHEMA = "adf_forge_app"
CONTROL_SCHEMA = "adf_forge_control"

CREATE_RE = re.compile(r"^CREATE TABLE (?:IF NOT EXISTS )?(\w+)\.(\w+) \(")
CONSTRAINT_RE = re.compile(r"^\s*(PRIMARY KEY|CONSTRAINT|FOREIGN KEY|UNIQUE)\b")
COLUMN_RE = re.compile(r'^\s*("?\w+"?)\s+(.*)$')


def sql_blocks(markdown: str) -> list[tuple[str, str]]:
    """Return (section, body) for each ```sql fence; section is '1' or '2' by the preceding ## heading."""
    blocks, section, fence = [], "", None
    for line in markdown.splitlines():
        if line.startswith("## "):
            m = re.match(r"## (\d)\.", line)
            section = m.group(1) if m else section
        if line.strip() == "```sql" and fence is None:
            fence = []
        elif line.strip() == "```" and fence is not None:
            blocks.append((section, "\n".join(fence)))
            fence = None
        elif fence is not None:
            fence.append(line)
    return blocks


def split_comment(line: str) -> tuple[str, str]:
    """Split 'code -- comment' outside single-quoted literals."""
    in_str = False
    for i, ch in enumerate(line):
        if ch == "'":
            in_str = not in_str
        elif not in_str and line.startswith("--", i):
            return line[:i].rstrip(), line[i + 2 :].strip()
    return line.rstrip(), ""


def parse_tables(body: str) -> list[dict]:
    """Tables in order: name, schema, table comment, items [(code, comment, is_column, col_name)], planned."""
    lines = body.splitlines()
    tables, i = [], 0
    while i < len(lines):
        m = CREATE_RE.match(lines[i])
        if not m:
            i += 1
            continue
        _, head_comment = split_comment(lines[i])
        # Contiguous comment lines directly above (no blank line, no ---- separators) = table comment.
        above, j = [], i - 1
        if j >= 0 and lines[j].startswith("IF OBJECT_ID("):
            j -= 1  # the generator's T-SQL create guard, re-emitted by render_table
        while j >= 0 and lines[j].startswith("--") and not lines[j].startswith("-- ---"):
            above.insert(0, lines[j][2:].strip())
            j -= 1
        items, i = [], i + 1
        while not lines[i].startswith(")"):
            code, comment = split_comment(lines[i])
            code = code.strip().rstrip(",")
            if code:
                is_col = not CONSTRAINT_RE.match(code)
                col = COLUMN_RE.match(code).group(1) if is_col else ""
                items.append((code, comment, is_col, col))
            i += 1
        tables.append(
            {
                "schema": m.group(1),
                "name": m.group(2),
                "comment": " ".join(above),
                "planned": "(planned)" in head_comment or any(a.startswith("(planned)") for a in above),
                "items": items,
            }
        )
        i += 1
    return tables


def lit(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def render_table(t: dict, schema: str, *, tsql: bool) -> str:
    items = [it for it in t["items"] if not it[1].startswith("(planned)")]
    width = max(len(it[0]) for it in items) + 1
    body = []
    for n, (code, comment, _, _) in enumerate(items):
        sep = "," if n < len(items) - 1 else ""
        code = code.replace("app.", f"{APP_SCHEMA}.")
        body.append((f"    {(code + sep).ljust(width)}" + (f" -- {comment}" if comment else "")).rstrip())
    qualified = f"{schema}.{t['name']}"
    head = f"CREATE TABLE {qualified} ("
    if tsql:
        head = f"IF OBJECT_ID(N'{qualified}', N'U') IS NULL\nCREATE TABLE {qualified} ("
    out = [head, *body, ");"]
    if t["comment"] and not tsql:
        out.append(f"COMMENT ON TABLE {qualified} IS {lit(t['comment'])};")
    for code, comment, is_col, col in items:
        if not (is_col and comment):
            continue
        if tsql:
            out.append(
                f"IF NOT EXISTS (SELECT 1 FROM sys.extended_properties WHERE name = N'MS_Description' "
                f"AND major_id = OBJECT_ID(N'{qualified}') "
                f"AND minor_id = COLUMNPROPERTY(OBJECT_ID(N'{qualified}'), N'{col}', 'ColumnId'))\n"
                f"  EXEC sp_addextendedproperty N'MS_Description', N{lit(comment)}, "
                f"N'SCHEMA', N'{schema}', N'TABLE', N'{t['name']}', N'COLUMN', N'{col}';"
            )
        else:
            out.append(f"COMMENT ON COLUMN {qualified}.{col} IS {lit(comment)};")
    if tsql:
        out.append("GO")
    return "\n".join(out)


def app_indexes(body: str) -> list[str]:
    out = []
    for line in body.splitlines():
        if line.startswith("CREATE INDEX") and "(planned)" not in line:
            code, comment = split_comment(line)
            out.append(code.replace("app.", f"{APP_SCHEMA}.") + (f"  -- migration {comment}" if comment else ""))
    return out


HEADER = f"""-- =============================================================================
-- ADF Accelerator — runnable DDL (GENERATED — do not edit by hand)
-- Source: docs/DDL.md  ->  python docs/scripts/build_ddl_sql.py
--
-- Part 1  {APP_SCHEMA}      Application metastore (PostgreSQL 15+). Live tables only
--         (migrations 0001–0021); (planned) tables/columns from DDL.md are omitted. Column
--         documentation is stored with COMMENT ON, so `\\d+ {APP_SCHEMA}.<table>` shows it.
--         In the running app, Alembic owns this schema (tables land in `public`); use this
--         part for review, a fresh reference database, or catalog/lineage tooling.
-- Part 2  {CONTROL_SCHEMA}  ADF control store (Azure SQL / SQL Server, T-SQL). Run it in the
--         customer's control database, not the app's Postgres. Column docs are stored as
--         MS_Description extended properties.
-- The two parts use different SQL dialects — run each against its own engine.
-- =============================================================================
"""


def main() -> None:
    blocks = sql_blocks(SRC.read_text(encoding="utf-8"))
    app_body = next(b for s, b in blocks if s == "1")
    control_bodies = [b for s, b in blocks if s == "2"]

    parts = [HEADER, "", "-- " + "=" * 77, "-- PART 1 — Application metastore (PostgreSQL 15+)", "-- " + "=" * 77, ""]
    parts += [
        "-- Once, from an admin connection:  CREATE DATABASE adf_forge;  then connect to it.",
        f"CREATE SCHEMA IF NOT EXISTS {APP_SCHEMA};",
        "",
    ]
    for t in parse_tables(app_body):
        if not t["planned"]:
            parts += [render_table(t, APP_SCHEMA, tsql=False), ""]
    parts += ["-- Indexes", *app_indexes(app_body), ""]

    parts += ["", "-- " + "=" * 77, "-- PART 2 — ADF control store (Azure SQL / SQL Server, T-SQL)", "-- " + "=" * 77, ""]
    parts += [
        f"IF SCHEMA_ID(N'{CONTROL_SCHEMA}') IS NULL EXEC(N'CREATE SCHEMA {CONTROL_SCHEMA}');",
        "GO",
        "",
    ]
    for body in control_bodies:
        for t in parse_tables(body):
            parts += [render_table(t, CONTROL_SCHEMA, tsql=True), ""]

    OUT.write_text("\n".join(parts).rstrip() + "\n", encoding="utf-8")
    print(f"wrote {OUT.relative_to(DOCS.parent)}")


if __name__ == "__main__":
    main()
