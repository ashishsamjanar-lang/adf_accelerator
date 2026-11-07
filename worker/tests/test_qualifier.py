"""pytest for app/generator/qualifier.py — the Python port of
`frontend/lib/adf/qualifier.ts` (kept in parity, mirrors
`frontend/lib/adf/qualifier.test.ts` case-for-case).

Root-cause coverage for the naming-convention-edge-cases fix: the old
`codegen.py`/`spec.py` behavior blindly split on "." and defaulted to "dbo" —
these cases are exactly what that got wrong.
"""
from __future__ import annotations

from app.generator.qualifier import (
    QualifierEntryInput,
    QualifierProfile,
    build_safe_qualifier_token,
    check_qualifier_collision,
    check_sap_extraction_support,
    get_qualifier_profile,
    resolve_qualified_name,
    sanitize_for_adf_name,
)


def test_default_profile_for_unknown_connector():
    assert get_qualifier_profile(None) == QualifierProfile(("schema", "object"), "insensitive", "table")


def test_file_connector_derives_from_registry_kind():
    assert get_qualifier_profile("azure_blob_storage").object_kind == "file"


def test_service_connector_derives_from_registry_kind():
    assert get_qualifier_profile("salesforce").parts == ("object",)


def test_two_part_relational_unchanged_behavior():
    resolved = resolve_qualified_name(QualifierEntryInput(object="dbo.customer"), get_qualifier_profile("sql_server"))
    assert (resolved.schema, resolved.object, resolved.forQuery) == ("dbo", "customer", "dbo.customer")


def test_bare_table_has_no_schema():
    resolved = resolve_qualified_name(QualifierEntryInput(object="customer"), get_qualifier_profile("sql_server"))
    assert resolved.schema is None
    assert resolved.object == "customer"


def test_sap_hana_three_part_resolves_correctly_instead_of_mis_joining():
    resolved = resolve_qualified_name(QualifierEntryInput(object="PRD.SAPABAP1.MARA"), get_qualifier_profile("sap_hana"))
    assert (resolved.catalog, resolved.schema, resolved.object) == ("PRD", "SAPABAP1", "MARA")
    assert resolved.forQuery == "PRD.SAPABAP1.MARA"


def test_structured_fields_bypass_string_parsing_and_still_case_fold():
    resolved = resolve_qualified_name(
        QualifierEntryInput(object="mara", catalog="prd", schema="sapabap1"), get_qualifier_profile("sap_hana")
    )
    assert (resolved.catalog, resolved.schema, resolved.object) == ("PRD", "SAPABAP1", "MARA")


def test_extra_leading_segments_fold_into_catalog():
    resolved = resolve_qualified_name(QualifierEntryInput(object="a.b.c.table"), get_qualifier_profile("sap_hana"))
    assert (resolved.catalog, resolved.schema, resolved.object) == ("A.B", "C", "TABLE")


def test_schema_less_sap_ecc_never_gets_a_dbo_schema():
    resolved = resolve_qualified_name(
        QualifierEntryInput(object="API_SALES_ORDER_SRV.A_SalesOrder"), get_qualifier_profile("sap_ecc")
    )
    assert resolved.schema is None
    assert resolved.catalog is None
    assert resolved.object == "API_SALES_ORDER_SRV.A_SalesOrder"


def test_salesforce_object_with_a_dot_is_not_split():
    resolved = resolve_qualified_name(QualifierEntryInput(object="Account.Contact"), get_qualifier_profile("salesforce"))
    assert resolved.schema is None
    assert resolved.forQuery == "Account.Contact"


def test_oracle_upper_cases_a_lowercase_object():
    resolved = resolve_qualified_name(QualifierEntryInput(object="mara"), get_qualifier_profile("oracle"))
    assert resolved.forQuery == "MARA"


def test_postgres_lower_cases_a_mixed_case_object():
    resolved = resolve_qualified_name(QualifierEntryInput(object="Public.Customer"), get_qualifier_profile("postgresql"))
    assert resolved.forQuery == "public.customer"


def test_case_override_as_typed_skips_folding():
    resolved = resolve_qualified_name(
        QualifierEntryInput(object="Sapabap1.Mara", case_override="as-typed"), get_qualifier_profile("sap_hana")
    )
    assert resolved.forQuery == "Sapabap1.Mara"


def test_qualifier_override_bypasses_everything():
    resolved = resolve_qualified_name(
        QualifierEntryInput(object="ignored", qualifier_override='"Weird Case".Table'), get_qualifier_profile("sap_hana")
    )
    assert resolved.forQuery == '"Weird Case".Table'


def test_sanitize_replaces_every_illegal_character():
    assert sanitize_for_adf_name("/BIC/AZCUSTOMER") == "BIC_AZCUSTOMER"
    assert sanitize_for_adf_name("sales order-2024") == "sales_order_2024"
    assert sanitize_for_adf_name("a.b+c?d/e<f>g*h%i&j:k\\l") == "a_b_c_d_e_f_g_h_i_j_k_l"


def test_sanitize_never_returns_empty():
    assert sanitize_for_adf_name("///") == "object"


def test_sanitize_preserves_a_legitimate_double_underscore_not_touching_any_illegal_character():
    """Regression (SF-01, docs/TEST_SCENARIOS_SAMPLE_PROJECTS.md §2): Salesforce's
    literal `__c`/`__r` custom-field/relationship suffix must survive
    unchanged — a blind "collapse every underscore run" pass previously
    turned `Renewal_Date__c` into `Renewal_Date_c`, which could silently
    collide with an unrelated custom object sharing the same base name."""
    assert sanitize_for_adf_name("Renewal_Date__c") == "Renewal_Date__c"
    assert sanitize_for_adf_name("Owner__r") == "Owner__r"


def test_sanitize_still_collapses_adjacent_illegal_characters_touching_a_literal_underscore():
    # An illegal character immediately next to a literal underscore still
    # collapses to one separator — only *untouched* underscore runs are
    # preserved verbatim (the case above).
    assert sanitize_for_adf_name("sales_-order") == "sales_order"
    assert sanitize_for_adf_name("a_/b") == "a_b"


def test_collision_flags_matching_tokens():
    collisions = check_qualifier_collision(
        [("curated.policy", "curated_policy"), ("curated-policy", "curated_policy"), ("curated.claim", "curated_claim")]
    )
    assert len(collisions) == 1
    assert collisions[0].token == "curated_policy"
    assert collisions[0].keys == ["curated.policy", "curated-policy"]


def test_collision_empty_when_all_unique():
    assert check_qualifier_collision([("a", "a"), ("b", "b")]) == []


def test_case_insensitive_profile_does_not_fold_case_for_collision_purposes():
    """Documents a real, currently-unaddressed gap (SAP-05b, docs/
    TEST_SCENARIOS_SAMPLE_PROJECTS.md §1): a connector's "insensitive" case
    convention only means resolve_qualified_name() doesn't *transform* case
    (unlike the "upper"/"lower" conventions, which do) — it does NOT mean two
    objects differing only by case are treated as the same object for
    collision purposes. `Y_Report` and `y_report` on a case-insensitive
    source (e.g. SQL Server) resolve to *different* tokens and so silently
    generate two separate artifacts for what the source considers one table."""
    profile = get_qualifier_profile("sql_server")
    assert profile.case_convention == "insensitive"
    resolved_a = resolve_qualified_name(QualifierEntryInput(object="Y_Report"), profile)
    resolved_b = resolve_qualified_name(QualifierEntryInput(object="y_report"), profile)
    token_a = build_safe_qualifier_token(resolved_a)
    token_b = build_safe_qualifier_token(resolved_b)
    assert token_a != token_b  # "Y_Report" vs "y_report" — not folded
    assert check_qualifier_collision([("Y_Report", token_a), ("y_report", token_b)]) == []


def test_sap_bw_namespace_blocked_on_non_bw_connector():
    issue = check_sap_extraction_support("sap_hana", "/BIC/AZCUSTOMER")
    assert issue is not None and issue.level == "error"


def test_sap_bw_namespace_allowed_on_sap_bw_connector():
    assert check_sap_extraction_support("sap_bw", "/BIC/AZCUSTOMER") is None


def test_known_pool_cluster_table_warns():
    issue = check_sap_extraction_support("sap_hana", "BSEG")
    assert issue is not None and issue.level == "warning"


def test_ordinary_transparent_table_is_silent():
    assert check_sap_extraction_support("sap_hana", "MARA") is None


def test_build_safe_qualifier_token_joins_sanitized_segments():
    resolved = resolve_qualified_name(QualifierEntryInput(object="PRD./BIC/AZ.MARA"), get_qualifier_profile("sap_hana"))
    assert build_safe_qualifier_token(resolved) == "PRD_BIC_AZ_MARA"
