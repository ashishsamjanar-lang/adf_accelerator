"""Data Modular STTM template import (AGENTS.md §33.14): `STTM_Data_Modular_Template_v2b.xlsx`,
as exported by the sibling Data Modeling Accelerator, uploaded straight into the Silver/Gold
registry.

The fixtures under `tests/fixtures/data_modular/` were produced by data_moduler's own exporter
(`app.services.sttm_service.export_sttm_table_workbook` / `export_layer_bundle`), so these tests
pin the adapter to real output, not a hand-built imitation. `silver_bundle_raw_export.zip` is
unmodified exporter output; the two `.xlsx` files additionally have Section 1's
domain/subject-area and a few Section 2 DQ cells filled in, as a business user would.
"""
from __future__ import annotations

import zipfile
from io import BytesIO
from pathlib import Path

import openpyxl
import pytest
from fastapi.testclient import TestClient

from app.onboarding.sttm_parser import (
    MAX_BUNDLE_ENTRY_BYTES,
    BundleEntryTooLargeError,
    parse_workbook,
)
from app.onboarding.sttm import validate_data_quality_rules
from app.onboarding.sttm_data_modular import regex_to_like, to_tsql, untranslated_spark

FIXTURES = Path(__file__).parent / "fixtures" / "data_modular"
XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
SILVER_FILE = "core_customer.xlsx"
GOLD_FILE = "mart_customer_sales.xlsx"
TEMPLATE_FILE = "STTM_Data_Modular_Template_v2b.xlsx"


def _fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _edit(content: bytes, section1: dict[str, str | None] | None = None, rows: dict[str, dict[str, str]] | None = None) -> bytes:
    """Rewrites Section 1 values and/or Section 2 cells (keyed by Target_Column_Name)."""
    wb = openpyxl.load_workbook(BytesIO(content))
    ws = wb["STTM_Template"]
    for row in range(3, 66):
        name = ws.cell(row=row, column=1).value
        if section1 and name in section1:
            ws.cell(row=row, column=2).value = section1[name]  # `cell(value=None)` would not clear it
    headers = [c.value for c in ws[68]]
    for row in range(69, ws.max_row + 1):
        target = ws.cell(row=row, column=headers.index("Target_Column_Name") + 1).value
        for header, value in ((rows or {}).get(target) or {}).items():
            ws.cell(row=row, column=headers.index(header) + 1).value = value
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _only_sheet(content: bytes, layer: str, file_name: str = SILVER_FILE) -> dict:
    report = parse_workbook(content, layer, None, file_name=file_name)
    assert report.sheet_total == 1
    return report.sheets[0].to_dict()


def _errors(sheet: dict) -> list[str]:
    return [i["message"] for i in sheet["issues"] if i["severity"] == "error"]


def _step(sheet: dict, step_type: str) -> dict:
    return next(t for t in sheet["parsed"]["transformations"] if t["type"] == step_type)


# --------------------------------------------------------------------------- template parity
def test_shipped_template_copies_are_identical() -> None:
    """DMT-13: the two in-repo copies (frontend download + test fixture) must not drift from each
    other — checkable in any CI/Docker build, unlike the sibling-repo check below."""
    frontend_copy = Path(__file__).resolve().parents[2] / "frontend" / "public" / "templates" / TEMPLATE_FILE
    assert frontend_copy.read_bytes() == _fixture(TEMPLATE_FILE)


def test_bundled_template_matches_data_moduler_copy() -> None:
    """The template adf_accelerator ships must stay byte-identical to the one data_moduler exports
    (only checkable when the sibling repo is checked out next to this one)."""
    sibling = Path(__file__).resolve().parents[3] / "data_moduler" / "docs" / "templates" / TEMPLATE_FILE
    if not sibling.exists():
        pytest.skip("data_moduler sibling repo not checked out")
    assert _fixture(TEMPLATE_FILE) == sibling.read_bytes()
    frontend_copy = Path(__file__).resolve().parents[2] / "frontend" / "public" / "templates" / TEMPLATE_FILE
    assert frontend_copy.read_bytes() == sibling.read_bytes()


# --------------------------------------------------------------------------- silver
def test_silver_workbook_translates_to_typed_steps() -> None:
    sheet = _only_sheet(_fixture(SILVER_FILE), "silver")
    assert sheet["valid"], sheet["issues"]
    assert sheet["sheetName"] == "core_customer"
    parsed = sheet["parsed"]
    assert parsed["curatedEntity"] == "customer"
    assert parsed["targetTable"] == "curated.customer"
    assert parsed["tag"] == "crm"  # Target_Subject_Area
    assert parsed["martDomain"] is None  # Silver never carries a mart domain
    assert parsed["sourceObjectKeys"] == ["raw_customer"]
    assert parsed["loadingBehavior"] == "DeltaLoad"  # Load_Type INCREMENTAL
    assert parsed["writeBehavior"] == "merge"  # Load_Strategy MERGE
    assert (parsed["watermarkColumn"], parsed["watermarkColumnType"]) == ("modified_date", "datetime")
    assert parsed["businessKeys"] == ["customer_id"]
    assert [t["type"] for t in parsed["transformations"]] == ["join", "conform_columns", "cast_schema", "dedupe"]


def test_silver_lookup_becomes_aliased_left_join() -> None:
    join = _step(_only_sheet(_fixture(SILVER_FILE), "silver"), "join")
    assert join["joinEntity"] == "core.ref_country"  # DMT-04: qualified from Target_Schema
    assert join["joinType"] == "LEFT"
    assert join["joinCondition"] == "a.country_code = b.country_code"
    assert join["columns"] == "a.*, b.country_name AS country_name"


def test_silver_conform_strips_table_qualifiers_and_renames() -> None:
    conform = _step(_only_sheet(_fixture(SILVER_FILE), "silver"), "conform_columns")
    assert conform["columns"] == (
        "cust_id:customer_id, CONCAT(TRIM(first_name), ' ', TRIM(last_name)):full_name, "
        "LOWER(email):email, country_name, modified_date"
    )


def test_silver_cast_only_where_target_type_differs() -> None:
    assert _step(_only_sheet(_fixture(SILVER_FILE), "silver"), "cast_schema")["castExpression"] == "customer_id:INT"


def test_silver_dq_rules_map_types_and_enforcement() -> None:
    rules = _only_sheet(_fixture(SILVER_FILE), "silver")["parsed"]["dataQualityRules"]
    assert [(r["rule"], r["column"], r["enforcement"]) for r in rules] == [
        ("IS_NOT_NULL", "customer_id", "block"),  # DQ_Severity ERROR
        ("UNIQUE", "customer_id", "block"),
        ("REGEX_MATCH", "email", "warn"),
    ]
    assert rules[2]["name"] == "email_format"  # single-type row keeps its DQ_Rule_ID


def test_scd2_replaces_dedupe_and_excludes_housekeeping_columns() -> None:
    content = _edit(_fixture(SILVER_FILE), section1={"SCD_Type": "SCD2", "SCD_Effective_From": "modified_date"})
    sheet = _only_sheet(content, "silver")
    assert sheet["valid"], sheet["issues"]
    types = [t["type"] for t in sheet["parsed"]["transformations"]]
    assert types[-1] == "scd2" and "dedupe" not in types
    scd2 = _step(sheet, "scd2")
    assert scd2["keyColumns"] == "customer_id"
    assert scd2["orderColumn"] == "modified_date"
    assert scd2["columns"] == "full_name, email, country_name"
    # DMT-09: a history name that collides with the change-detection column keeps the default, warned.
    assert "validFromColumn" not in scd2
    assert any("SCD_Effective_From modified_date" in i["message"] for i in sheet["issues"])


def test_scd2_uses_the_templates_history_column_names() -> None:
    content = _edit(_fixture(SILVER_FILE), section1={
        "SCD_Type": "SCD2", "SCD_Effective_From": "eff_from", "SCD_Effective_To": "eff_to", "SCD_Current_Flag": "is_current",
    })
    scd2 = _step(_only_sheet(content, "silver"), "scd2")
    assert (scd2["validFromColumn"], scd2["validToColumn"], scd2["currentFlagColumn"]) == ("eff_from", "eff_to", "is_current")


def test_raw_export_bundle_without_a_domain_imports_tagged_with_its_layer() -> None:
    """Unmodified exporter output: the merge key comes from Data Modular's `load_policy:` remark;
    the template has no tag cell, so with no domain the layer stands in, with a warning (DMT-01)."""
    report = parse_workbook(_fixture("silver_bundle_raw_export.zip"), "silver", None, file_name="silver.zip")
    sheet = report.sheets[0].to_dict()
    assert sheet["sheetName"] == "core_customer"
    assert _errors(sheet) == []
    assert sheet["parsed"]["tag"] == "silver"
    assert any(i["field"] == "tag" and i["severity"] == "warning" for i in sheet["issues"])


def test_dq_rules_over_the_cap_keep_the_first_five_with_a_warning() -> None:
    content = _edit(_fixture(SILVER_FILE), rows={"email": {"DQ_Rule_Type": "NOT_NULL, UNIQUE"}, "country_name": {"DQ_Rule_Type": "NOT_NULL, UNIQUE"}})
    sheet = _only_sheet(content, "silver")
    assert sheet["valid"], _errors(sheet)
    assert len(sheet["parsed"]["dataQualityRules"]) == 5
    assert any("kept the first 5" in i["message"] for i in sheet["issues"] if i["severity"] == "warning")


def test_layer_mismatch_and_bronze_are_rejected() -> None:
    gold_into_silver = _only_sheet(_fixture(GOLD_FILE), "silver", GOLD_FILE)
    assert any("doesn't match this silver upload" in m for m in _errors(gold_into_silver))
    bronze = _only_sheet(_edit(_fixture(SILVER_FILE), section1={"Target_Layer": "BRONZE"}), "silver")
    assert any("Source Onboarding" in m for m in _errors(bronze))


def test_unsupported_cells_are_reported_not_dropped_silently() -> None:
    content = _edit(
        _fixture(SILVER_FILE),
        rows={"email": {"Filter_Condition": "email IS NOT NULL", "DQ_Rule_Type": "REFERENTIAL"},
              "country_name": {"Lookup_Default_Value": "Unknown"}},
    )
    warnings = [i["message"] for i in _only_sheet(content, "silver")["issues"] if i["severity"] == "warning"]
    assert any('DQ_Rule_Type "REFERENTIAL"' in w for w in warnings)


def test_filter_condition_becomes_the_first_step() -> None:
    """DMT-03: distinct Filter_Conditions are ANDed into one `filter` step on the Bronze input."""
    content = _edit(_fixture(SILVER_FILE), rows={
        "email": {"Filter_Condition": "email IS NOT NULL"},
        "full_name": {"Filter_Condition": "email IS NOT NULL"},
        "country_name": {"Filter_Condition": "nvl(status, 'A') <> 'X'"},
    })
    sheet = _only_sheet(content, "silver")
    assert sheet["valid"], _errors(sheet)
    first = sheet["parsed"]["transformations"][0]
    assert first == {"name": "filter_rows", "type": "filter", "filterCondition": "(email IS NOT NULL) AND (ISNULL(status, 'A') <> 'X')"}


def test_filter_condition_drops_the_source_object_qualifier() -> None:
    """RUN-10: the filter step reads the flattened Bronze table, so `raw_customer.` is stripped."""
    content = _edit(_fixture(SILVER_FILE), rows={"email": {"Filter_Condition": "raw_customer.is_deleted = 0"}})
    first = _only_sheet(content, "silver")["parsed"]["transformations"][0]
    assert first["filterCondition"] == "is_deleted = 0"


def test_blank_template_is_parsed_and_reported_invalid() -> None:
    sheet = _only_sheet(_fixture(TEMPLATE_FILE), "silver", TEMPLATE_FILE)
    assert not sheet["valid"]
    assert "curated_entity is required." in _errors(sheet)


# --------------------------------------------------------------------------- gold
def test_gold_workbook_translates_to_aggregate_then_select() -> None:
    sheet = _only_sheet(_fixture(GOLD_FILE), "gold", GOLD_FILE)
    assert sheet["valid"], sheet["issues"]
    parsed = sheet["parsed"]
    assert parsed["martDomain"] == "sales"
    assert parsed["targetTable"] == "mart.sales_customer_sales"
    agg, select = parsed["transformations"]
    assert agg == {
        "name": "aggregate", "type": "aggregate", "groupByColumns": "customer_id",
        "aggregateExpression": "SUM(amount) AS total_amount, COUNT(order_id) AS order_count",
    }
    assert select["columns"] == "customer_id, total_amount, order_count"
    rule = parsed["dataQualityRules"][0]
    assert (rule["rule"], rule["value"], rule["enforcement"]) == ("RANGE_BETWEEN", "0,1000000", "block")


def test_gold_derived_non_aggregate_column_is_computed_in_the_final_select() -> None:
    """DMT-02: with no aggregate, a derived/renamed Gold column is computed in `select_final`."""
    content = _edit(_fixture(GOLD_FILE), rows={
        "total_amount": {"Aggregation_Function": None, "Aggregation_Expression": None, "Transformation_Type": "DERIVED",
                         "Transformation_Expression": "orders.amount * 1.2"},
        "order_count": {"Aggregation_Function": None, "Aggregation_Expression": None, "Transformation_Type": "DIRECT",
                        "Transformation_Expression": None},
    })
    sheet = _only_sheet(content, "gold", GOLD_FILE)
    assert sheet["valid"], sheet["issues"]
    assert "amount * 1.2 AS total_amount" in _step(sheet, "select_columns")["columns"]


def test_gold_derived_grain_column_is_staged_before_the_aggregate() -> None:
    """DMT-02: a derived GROUP BY column is staged under its target name, then grouped on."""
    content = _edit(_fixture(GOLD_FILE), rows={
        "customer_id": {"Transformation_Type": "DERIVED", "Transformation_Expression": "UPPER(orders.customer_id)"},
    })
    sheet = _only_sheet(content, "gold", GOLD_FILE)
    assert sheet["valid"], sheet["issues"]
    types = [t["type"] for t in sheet["parsed"]["transformations"]]
    assert types[:2] == ["select_columns", "aggregate"]
    assert sheet["parsed"]["transformations"][0]["columns"] == "*, UPPER(customer_id) AS customer_id"
    assert _step(sheet, "aggregate")["groupByColumns"] == "customer_id"


def test_gold_without_domain_is_rejected() -> None:
    content = _edit(_fixture(GOLD_FILE), section1={"Target_Domain": None})
    assert "mart_domain is required for Gold objects." in _errors(_only_sheet(content, "gold", GOLD_FILE))


# --------------------------------------------------------------------------- bundles
def test_bundle_with_two_workbooks_yields_one_sheet_each() -> None:
    other = _edit(_fixture(SILVER_FILE), section1={"Target_Table_ID": "customer_v2", "Target_Table_Name": "customer_v2"})
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("a/core_customer.xlsx", _fixture(SILVER_FILE))
        z.writestr("b/core_customer.xlsx", other)
        z.writestr("readme.txt", "ignored")
    report = parse_workbook(buf.getvalue(), "silver", None, file_name="bundle.zip")
    assert [s.sheet_name for s in report.sheets] == ["core_customer", "core_customer (2)"]
    assert report.sheet_valid == 2


def test_bundle_entry_size_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.onboarding.sttm_parser.MAX_BUNDLE_ENTRY_BYTES", 10)
    with pytest.raises(BundleEntryTooLargeError):
        parse_workbook(_fixture("silver_bundle.zip"), "silver", None, file_name="silver_bundle.zip")
    assert MAX_BUNDLE_ENTRY_BYTES > 10


# --------------------------------------------------------------------------- API: upload -> commit -> registry
def test_upload_commit_data_modular_workbooks(client: TestClient, seed: dict[str, str]) -> None:
    # E2E-10: commit needs every upstream key registered (Silver reads raw_customer, Gold reads orders).
    for source_object in ("raw_customer", "orders"):
        bronze = {"source_system": "crm", "source_object": source_object, "source_type": "table",
                  "target_type": "table", "target_file_format": "Parquet", "loading_behavior": "FullLoad"}
        assert client.post("/api/v1/projects/proj-a/onboarding/objects", json=bronze).status_code == 201
    upload = client.post(
        "/api/v1/projects/proj-a/silver-objects/sttm-import",
        files={"file": ("silver_bundle.zip", _fixture("silver_bundle.zip"), "application/zip")},
    )
    assert upload.status_code == 201, upload.text
    imp = upload.json()
    assert (imp["sheetTotal"], imp["sheetValid"]) == (1, 1)
    commit = client.post(f"/api/v1/projects/proj-a/silver-objects/sttm-import/{imp['id']}/commit", json={})
    assert commit.status_code == 200, commit.text
    assert commit.json()["committed"] == ["core_customer"]

    gold = client.post(
        "/api/v1/projects/proj-a/mart-objects/sttm-import",
        files={"file": (GOLD_FILE, _fixture(GOLD_FILE), XLSX_TYPE)},
    )
    assert gold.status_code == 201, gold.text
    client.post(f"/api/v1/projects/proj-a/mart-objects/sttm-import/{gold.json()['id']}/commit", json={})

    silver_obj = client.get("/api/v1/projects/proj-a/silver-objects").json()[0]
    assert silver_obj["curatedEntity"] == "customer"
    assert [t["type"] for t in silver_obj["transformations"]] == ["join", "conform_columns", "cast_schema", "dedupe"]
    mart_obj = client.get("/api/v1/projects/proj-a/mart-objects").json()[0]
    assert mart_obj["martDomain"] == "sales"
    assert len(mart_obj["dataQualityRules"]) == 1


def test_preview_warns_on_source_keys_missing_from_bronze(client: TestClient, seed: dict[str, str]) -> None:
    def key_warnings() -> list[str]:
        resp = client.post(
            "/api/v1/projects/proj-a/silver-objects/sttm-import",
            files={"file": (SILVER_FILE, _fixture(SILVER_FILE), XLSX_TYPE)},
        )
        assert resp.status_code == 201, resp.text
        sheet = resp.json()["sheets"][0]
        assert sheet["valid"]
        return [i["message"] for i in sheet["issues"] if i.get("field") == "sourceObjectKeys"]

    assert any("raw_customer" in m for m in key_warnings())
    bronze = {"source_system": "crm", "source_object": "raw_customer", "source_type": "table",
              "target_type": "table", "target_file_format": "Parquet", "loading_behavior": "FullLoad"}
    assert client.post("/api/v1/projects/proj-a/onboarding/objects", json=bronze).status_code == 201
    assert key_warnings() == []


def test_source_keys_resolve_to_the_bronze_object_key(client: TestClient, seed: dict[str, str]) -> None:
    """DMT-12: `raw_customer` (the template's Source_Object) resolves to the Bronze key `crm.customer`."""
    bronze = {"source_system": "crm", "source_object": "customer", "source_type": "table",
              "target_type": "table", "target_file_format": "Parquet", "loading_behavior": "FullLoad"}
    assert client.post("/api/v1/projects/proj-a/onboarding/objects", json=bronze).status_code == 201
    resp = client.post(
        "/api/v1/projects/proj-a/silver-objects/sttm-import",
        files={"file": (SILVER_FILE, _fixture(SILVER_FILE), XLSX_TYPE)},
    )
    sheet = resp.json()["sheets"][0]
    assert sheet["parsed"]["sourceObjectKeys"] == ["crm.customer"]
    assert not [i for i in sheet["issues"] if i.get("field") == "sourceObjectKeys"]


def test_lookup_default_value_coalesces_the_miss() -> None:
    """DMT-04: the default is applied on the LEFT join instead of NULL."""
    content = _edit(_fixture(SILVER_FILE), rows={"country_name": {"Lookup_Default_Value": "Unknown"}})
    assert _step(_only_sheet(content, "silver"), "join")["columns"] == "a.*, COALESCE(b.country_name, 'Unknown') AS country_name"


def test_ansi_expressions_are_rewritten_to_tsql() -> None:
    """DMT-06: Spark/ANSI idioms become T-SQL, `||` included (as NULL-propagating `+`)."""
    content = _edit(_fixture(SILVER_FILE), rows={
        "email": {"Transformation_Expression": "NVL(email, 'n/a')"},
        "modified_date": {"Transformation_Expression": "current_timestamp()"},
        "full_name": {"Transformation_Expression": "first_name || ' ' || last_name"},
    })
    sheet = _only_sheet(content, "silver")
    columns = _step(sheet, "conform_columns")["columns"]
    assert "ISNULL(email, 'n/a'):email" in columns and "CURRENT_TIMESTAMP:modified_date" in columns
    assert "first_name + ' ' + last_name:full_name" in columns


@pytest.mark.parametrize(("spark", "tsql"), [
    ("lcase(x)", "LOWER(x)"), ("ucase(x)", "UPPER(x)"), ("ceil(x)", "CEILING(x)"), ("pow(x, 2)", "POWER(x, 2)"),
    ("ln(x)", "LOG(x)"), ("char_length(x)", "LEN(x)"), ("to_date(x)", "CAST(x AS DATE)"),
    ("to_timestamp(x)", "CAST(x AS DATETIME2)"), ("to_date(x, 'yyyy-MM-dd')", "to_date(x, 'yyyy-MM-dd')"),
    ("'ln(x)'", "'ln(x)'"), ("a || 'x||y'", "a + 'x||y'"),
    ("date_add(d, 7)", "DATEADD(day, 7, d)"), ("date_sub(d, n)", "DATEADD(day, -(n), d)"),
    ("datediff(end_d, start_d)", "DATEDIFF(day, start_d, end_d)"), ("instr(s, 'x')", "CHARINDEX('x', s)"),
    ("if(a > 0, 1, 0)", "IIF(a > 0, 1, 0)"), ("iif(a, 1, 0)", "iif(a, 1, 0)"),
    ("cast(x as string)", "cast(x AS NVARCHAR(MAX))"), ("x::string", "CAST(x AS NVARCHAR(MAX))"),
    ("cast(x as double)", "cast(x AS FLOAT)"), ("cast(x as float)", "cast(x AS REAL)"),
    ("cast(x as boolean)", "cast(x AS BIT)"), ("x::long", "CAST(x AS BIGINT)"),
    ("cast(x as timestamp)", "cast(x AS DATETIME2)"), ("cast(x as decimal(10,2))", "cast(x as decimal(10,2))"),
    ("nvl2(a, 'y', 'n')", "IIF(a IS NOT NULL, 'y', 'n')"),
    # RUN-11: a two-argument substr/substring gets T-SQL's required length; three arguments are kept.
    ("substr(s, 3)", "SUBSTRING(s, 3, 2147483647)"), ("substring(s, 3)", "SUBSTRING(s, 3, 2147483647)"),
    ("substr(s, 1, 2)", "SUBSTRING(s, 1, 2)"),
    # RUN-11: date parts, month arithmetic, padding and boolean literals.
    ("add_months(d, 3)", "DATEADD(month, 3, d)"), ("last_day(d)", "EOMONTH(d)"), ("dayofmonth(d)", "DAY(d)"),
    ("dayofweek(d)", "DATEPART(weekday, d)"), ("dayofyear(d)", "DATEPART(dayofyear, d)"),
    ("weekofyear(d)", "DATEPART(iso_week, d)"), ("quarter(d)", "DATEPART(quarter, d)"),
    ("lpad(c, 5, '0')", "IIF(LEN(c) >= 5, LEFT(c, 5), RIGHT(REPLICATE('0', 5) + c, 5))"),
    ("rpad(c, 5, ' ')", "LEFT(c + REPLICATE(' ', 5), 5)"),
    ("is_active = true", "is_active = 1"), ("flag=FALSE", "flag= 0"), ("x = 'true'", "x = 'true'"),
])
def test_more_spark_functions_are_rewritten(spark: str, tsql: str) -> None:
    """DMT-06: further Spark spellings with a one-to-one T-SQL form; string literals stay as written."""
    assert to_tsql(spark) == tsql


@pytest.mark.parametrize(("spark", "tsql"), [
    # DMT-06: nested arguments are rewritten innermost first.
    ("datediff(current_date(), to_date(x))", "DATEDIFF(day, CAST(x AS DATE), CAST(GETDATE() AS DATE))"),
    ("date_add(to_date(x), 1)", "DATEADD(day, 1, CAST(x AS DATE))"),
    ("substr(upper(name), 2)", "SUBSTRING(upper(name), 2, 2147483647)"),
    ("cast(nvl(x, 0) as double)", "cast(ISNULL(x, 0) AS FLOAT)"), ("x::decimal(10,2)", "CAST(x AS decimal(10,2))"),
    ("(a || b) || c", "(a + b) + c"),
    # RUN-11: hashes, epoch seconds and portable date_format patterns.
    ("md5(email)", "LOWER(CONVERT(VARCHAR(32), HASHBYTES('MD5', CAST(email AS VARCHAR(MAX))), 2))"),
    ("sha2(email, 256)", "LOWER(CONVERT(VARCHAR(64), HASHBYTES('SHA2_256', CAST(email AS VARCHAR(MAX))), 2))"),
    ("sha2(e, 384)", "sha2(e, 384)"),
    ("unix_timestamp()", "DATEDIFF_BIG(SECOND, '1970-01-01', GETUTCDATE())"),
    ("from_unixtime(ts)", "FORMAT(DATEADD(SECOND, ts, '1970-01-01'), 'yyyy-MM-dd HH:mm:ss')"),
    ("date_format(d, 'yyyy-MM-dd HH:mm')", "FORMAT(d, 'yyyy-MM-dd HH:mm')"),
    # RUN-11: w/D/Q and narrow day names become DATEPART/LEFT segments; QQQQ stays untranslated.
    ("date_format(d, 'w')", "CAST(DATEPART(iso_week, d) AS VARCHAR(3))"),
    ("date_format(d, 'yyyy-ww')", "FORMAT(d, 'yyyy-') + RIGHT('00' + CAST(DATEPART(iso_week, d) AS VARCHAR(3)), 2)"),
    ("date_format(d, 'DDD')", "RIGHT('000' + CAST(DATEPART(dayofyear, d) AS VARCHAR(3)), 3)"),
    ("date_format(d, 'yyyy QQQ')", "FORMAT(d, 'yyyy ') + 'Q' + CAST(DATEPART(quarter, d) AS VARCHAR(1))"),
    ("date_format(d, 'Q-M')", "CAST(DATEPART(quarter, d) AS VARCHAR(3)) + FORMAT(d, '-M')"),
    ("date_format(d, 'QM')", "CAST(DATEPART(quarter, d) AS VARCHAR(3)) + FORMAT(d, '%M')"),
    ("date_format(d, 'EEEEE')", "LEFT(FORMAT(d, 'dddd'), 1)"), ("date_format(d, 'QQQQ')", "date_format(d, 'QQQQ')"),
    ("date_format(d, 'M')", "FORMAT(d, '%M')"),
    ("initcap(name)", "(SELECT STRING_AGG(UPPER(LEFT(value, 1)) + LOWER(SUBSTRING(value, 2, 2147483647)), ' ') "
     "WITHIN GROUP (ORDER BY ordinal) FROM STRING_SPLIT(name, ' ', 1))"),
    # RUN-11: Java-only date_format letters and trunc/date_trunc with a literal unit.
    ("date_format(d, 'EEE, dd MMM')", "FORMAT(d, 'ddd, dd MMM')"), ("date_format(d, 'EEEE h:mm a')", "FORMAT(d, 'dddd h:mm tt')"),
    ("date_format(ts, 'HH:mm:ss.SSS')", "FORMAT(ts, 'HH:mm:ss.fff')"),
    ("trunc(order_date, 'MM')", "DATEFROMPARTS(YEAR(order_date), MONTH(order_date), 1)"),
    ("trunc(d, 'year')", "DATEFROMPARTS(YEAR(d), 1, 1)"), ("trunc(d, 'hour')", "trunc(d, 'hour')"),
    ("date_trunc('quarter', d)", "DATEFROMPARTS(YEAR(d), (DATEPART(quarter, d) - 1) * 3 + 1, 1)"),
    ("date_trunc('week', d)", "DATEADD(day, -((DATEPART(weekday, d) + @@DATEFIRST - 2) % 7), CAST(d AS DATE))"),
    ("date_trunc('HOUR', to_timestamp(s))", "DATEADD(hour, DATEDIFF(hour, 0, CAST(s AS DATETIME2)), CAST(0 AS DATETIME))"),
    ("date_trunc('second', ts)", "date_trunc('second', ts)"),
])
def test_nested_and_further_spark_functions_are_rewritten(spark: str, tsql: str) -> None:
    assert to_tsql(spark) == tsql


def test_unmapped_section1_cells_are_reported_and_kept_in_notes() -> None:
    """DMT-08: physical cells with no registry field are listed in one warning and kept as
    `Name: value` lines in the object's notes; governance cells become typed fields instead."""
    content = _edit(_fixture(SILVER_FILE), section1={"Target_Schema": "silver", "PII_Flag": "Y", "PHI_Flag": "N", "Retention_Period": "7y"})
    sheet = _only_sheet(content, "silver")
    issue = next(i for i in sheet["issues"] if i.get("field") == "section1")
    assert "Target_Schema" in issue["message"]
    assert not any(n in issue["message"] for n in ("PII_Flag", "PHI_Flag", "Retention_Period"))
    parsed = sheet["parsed"]
    assert "Target_Schema: silver" in parsed["notes"] and "PII_Flag" not in parsed["notes"]
    assert (parsed["containsPii"], parsed["containsPhi"], parsed["containsPci"]) == (True, False, None)
    assert parsed["retentionPeriod"] == "7y" and parsed["regulatoryTag"] is None


def test_rowversion_watermark_is_typed_bigint() -> None:
    """DMT-14: a rowversion watermark compares as bigint, not as hex text."""
    content = _edit(_fixture(SILVER_FILE), section1={"Watermark_Column": "modified_date"},
                    rows={"modified_date": {"Target_Data_Type": "rowversion", "Source_Data_Type": "rowversion"}})
    sheet = _only_sheet(content, "silver")
    assert sheet["parsed"]["watermarkColumnType"] == "bigint"
    assert any(i.get("field") == "watermarkColumnType" for i in sheet["issues"])


@pytest.mark.parametrize(
    ("section1", "enabled"),
    [
        ({"Mapping_Status": "DRAFT"}, False),
        ({"Mapping_Status": "REVIEW"}, False),
        ({"Mapping_Status": "APPROVED", "Approval_Status": "PENDING"}, False),
        ({"Mapping_Status": "APPROVED", "Approval_Status": "REJECTED"}, False),
        ({"Mapping_Status": "APPROVED", "Approval_Status": "APPROVED"}, True),
    ],
)
def test_unapproved_mappings_import_disabled(section1: dict[str, str], enabled: bool) -> None:
    """DMT-10: only DEPRECATED used to disable an object; DRAFT/REVIEW/PENDING/REJECTED now do too."""
    sheet = _only_sheet(_edit(_fixture(SILVER_FILE), section1=section1), "silver")
    assert sheet["parsed"]["enabled"] is enabled
    assert any(i["field"] == "enabled" for i in sheet["issues"]) is not enabled


def test_unqualified_lookup_reads_from_target_schema() -> None:
    """DMT-04: an unqualified Lookup_Table is joined from Section 1's Target_Schema when it is set."""
    sheet = _only_sheet(_edit(_fixture(SILVER_FILE), section1={"Target_Schema": "ref"}), "silver")
    assert _step(sheet, "join")["joinEntity"] == "ref.ref_country"
    assert any("joined as ref.ref_country" in i["message"] for i in sheet["issues"])


def test_dq_threshold_and_uncarried_dq_cells_are_reported() -> None:
    """DMT-05: a percentage pass rate is carried as maxViolationPct; a plain-number threshold on
    IS_NOT_NULL/UNIQUE and the constraint/reconciliation/reject cells are warned about."""
    content = _edit(_fixture(SILVER_FILE), rows={"customer_id": {
        "DQ_Threshold": "95%", "Reject_Table": "rej_customer", "Reject_Allowed": "N", "Reconciliation_Required": "Y",
    }})
    sheet = _only_sheet(content, "silver")
    messages = [i["message"] for i in sheet["issues"] if i.get("field") == "dataQualityRules"]
    rules = [r for r in sheet["parsed"]["dataQualityRules"] if r["column"] == "customer_id"]
    assert rules and all(r["maxViolationPct"] == "5" for r in rules if r["rule"] in ("IS_NOT_NULL", "UNIQUE"))
    assert not any('DQ_Threshold "95%"' in m for m in messages)
    plain = _edit(_fixture(SILVER_FILE), rows={"customer_id": {"DQ_Threshold": "10"}})
    plain_messages = [i["message"] for i in _only_sheet(plain, "silver")["issues"] if i.get("field") == "dataQualityRules"]
    assert any('DQ_Threshold "10"' in m and "IS_NOT_NULL" in m for m in plain_messages)
    cells = next(m for m in messages if "not carried — ADF DQ" in m)
    assert "Reject_Table" in cells and "Reconciliation_Required" in cells and "Reject_Allowed" not in cells


def test_ambiguous_source_key_is_warned_not_resolved(client: TestClient, seed: dict[str, str]) -> None:
    """DMT-12: `raw_customer` matching two Bronze objects stays as written, with a warning naming both."""
    for system in ("crm", "erp"):
        bronze = {"source_system": system, "source_object": "customer", "source_type": "table",
                  "target_type": "table", "target_file_format": "Parquet", "loading_behavior": "FullLoad"}
        assert client.post("/api/v1/projects/proj-a/onboarding/objects", json=bronze).status_code == 201
    resp = client.post(
        "/api/v1/projects/proj-a/silver-objects/sttm-import",
        files={"file": (SILVER_FILE, _fixture(SILVER_FILE), XLSX_TYPE)},
    )
    sheet = resp.json()["sheets"][0]
    assert sheet["parsed"]["sourceObjectKeys"] == ["raw_customer"]
    [message] = [i["message"] for i in sheet["issues"] if i.get("field") == "sourceObjectKeys"]
    assert "crm.customer" in message and "erp.customer" in message


def test_run_11_untranslated_spark_is_reported_outside_literals() -> None:
    assert untranslated_spark(to_tsql("date_format(order_date, 'QQQQ')")) == ["date_format"]
    assert untranslated_spark(to_tsql("to_date(x, 'yyyy-MM-dd')")) == ["to_date"]
    assert set(untranslated_spark("a <=> b AND flag = true AND name rlike '^a'")) == {"<=>", "rlike", "= true/false"}
    # Rewritten forms, look-alike names and text inside a literal are not flagged.
    assert untranslated_spark(to_tsql("to_date(x)")) == []
    assert untranslated_spark(to_tsql("flag = true AND lpad(c, 2, '0') = last_day(d)")) == []
    assert untranslated_spark("my_trunc(x) + 'date_format(' + upper(y)") == []


def test_run_17_regex_in_regex_match_warns() -> None:
    rule = {"name": "email_fmt", "column": "email", "rule": "REGEX_MATCH", "value": "^[^@]+@.+$"}
    issues = validate_data_quality_rules([rule])
    assert [i.severity for i in issues] == ["warning"] and "LIKE pattern" in issues[0].message
    assert validate_data_quality_rules([{**rule, "value": "%_@_%._%"}]) == []


@pytest.mark.parametrize(("regex", "like"), [
    ("^[A-Z]{2}\\d{4}$", "[A-Z][A-Z][0-9][0-9][0-9][0-9]"),
    ("^.+@.+\\..+$", "_%@_%._%"),
    ("^ORD-\\d+", "ORD-[0-9]%"), ("^[^@]+@.+$", "[^@]%@_%"), ("^(a|b)$", None), ("^a?$", None),
    # RUN-17: a repeat is exact at an unanchored edge or as `[^c]+c`; otherwise it isn't.
    ("[0-9]+$", "%[0-9]"), ("[A-Z]*-x$", "%-x"), ("^[A-Z]+$", None), ("^a[0-9]+b$", None), ("^[^@]+#", None),
    ("^100%_x.*$", "100[%][_]x%"), ("\\d{3}", "%[0-9][0-9][0-9]%"), ("^it's$", None),
])
def test_run_17_regex_is_translated_to_like_when_exact(regex: str, like: str | None) -> None:
    assert regex_to_like(regex) == like


def test_run_17_imported_regex_becomes_a_like_pattern() -> None:
    content = _edit(_fixture(SILVER_FILE), rows={"customer_id": {"DQ_Rule_Type": "REGEX", "DQ_Rule_Expression": "^C\\d{5}$"}})
    sheet = _only_sheet(content, "silver")
    rule = next(r for r in sheet["parsed"]["dataQualityRules"] if r["rule"] == "REGEX_MATCH")
    assert rule["value"] == "C[0-9][0-9][0-9][0-9][0-9]"
    assert any("translated to the LIKE pattern" in i["message"] for i in sheet["issues"])


def _join_warnings(client: TestClient) -> tuple[dict, list[str]]:
    resp = client.post(
        "/api/v1/projects/proj-a/silver-objects/sttm-import",
        files={"file": (SILVER_FILE, _fixture(SILVER_FILE), XLSX_TYPE)},
    )
    assert resp.status_code == 201, resp.text
    sheet = resp.json()["sheets"][0]
    return _step(sheet, "join"), [i["message"] for i in sheet["issues"] if "Join table" in i["message"]]


def test_lookup_table_resolves_to_the_bronze_landed_table(client: TestClient, seed: dict[str, str]) -> None:
    """Healthcare finding: a Lookup_Table onboarded only to Bronze joins its landed raw.<system>_<object> table."""
    bronze = {"source_system": "mdm", "source_object": "dbo.ref_country", "source_type": "table",
              "target_type": "table", "target_file_format": "Parquet", "loading_behavior": "FullLoad"}
    assert client.post("/api/v1/projects/proj-a/onboarding/objects", json=bronze).status_code == 201
    join, warnings = _join_warnings(client)
    assert join["joinEntity"] == "raw.mdm_ref_country"
    assert any("resolved to raw.mdm_ref_country" in w for w in warnings)


def test_unresolved_lookup_table_is_warned(client: TestClient, seed: dict[str, str]) -> None:
    join, warnings = _join_warnings(client)
    assert join["joinEntity"] == "core.ref_country"
    assert any("isn't produced by any Bronze/Silver/Gold registry object" in w for w in warnings)


def test_untyped_watermark_names_the_data_type_cells() -> None:
    """Healthcare finding: the template has no watermark-type cell — say which cells the type comes from."""
    content = _edit(_fixture(SILVER_FILE), section1={"Watermark_Column": "modified_date"},
                    rows={"modified_date": {"Target_Data_Type": None, "Source_Data_Type": None}})
    sheet = _only_sheet(content, "silver")
    assert any("needs a Target_Data_Type or Source_Data_Type" in m for m in _errors(sheet))
