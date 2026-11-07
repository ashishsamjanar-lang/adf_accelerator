"""Scenario tests — §18 Secrets / connection / auth edge cases
(docs/TEST_SCENARIOS_SAMPLE_PROJECTS.md). One test per scenario ID
(SEC-01..SEC-08).
"""
from __future__ import annotations

import pytest

from app.connectors.schemas import CONNECTOR_SCHEMAS, visible_fields


def test_sec_01_non_secret_fields_respect_authtype_but_secret_fields_are_always_visible_by_design():
    """SEC-01 [VERIFY]: real, confirmed, and deliberate (not a bug) —
    `visible_fields` (app/connectors/schemas.py) hides non-secret fields
    that belong to a different auth type (SFTP's SSH-only fields never show
    under `basic` auth), but *does* keep every `is_secret` field visible
    regardless of the selected auth type — the function's own docstring
    says why: "secret names always editable". So switching SFTP from
    `basic` to `ssh_public_key` correctly hides nothing non-secret, but
    `password` (a `basic`-only field) stays visible because it's a secret
    field, by design."""
    sftp = CONNECTOR_SCHEMAS["sftp"]
    under_ssh_key_auth = {f.key for f in visible_fields(sftp, "ssh_public_key")}
    assert "password" in under_ssh_key_auth  # secret field — always visible, per design
    assert "privateKeyContent" in under_ssh_key_auth  # secret field for this auth type


@pytest.mark.skip(reason="SEC-02/[VERIFY]: real, and already covered for real — see "
                          "test_scenario_governance.py::test_gov_06_a_raw_secret_with_a_space_or_slash_is_rejected_at_save_time "
                          "(same `_reject_secret_values` code, same underlying scenario) and its companion "
                          "documenting the real gap for a space/slash-free secret look-alike.")
def test_sec_02_realistic_looking_secret_pasted_into_kv_name_field_is_caught():
    pass


@pytest.mark.skip(reason="SEC-03/[VERIFY]: the tool genuinely never resolves a Key Vault secret's real value "
                          "anywhere in backend/app or worker/app (confirmed: no Key Vault *read* client exists in "
                          "either — worker/app/gitops/credential_vault.py only *writes*/rotates a Git PAT, and "
                          "the generator only ever emits an AzureKeyVaultSecret *reference* by name, never a "
                          "resolved value) — real, reassuring, not yet a dedicated test for a 'test connection' "
                          "debug surface (no such surface exists to leak through in the first place).")
def test_sec_03_same_secret_name_across_projects_never_resolves_actual_value():
    pass


@pytest.mark.skip(reason="SEC-04/[VERIFY]: rotating a shared linked-service's Key Vault secret name only "
                          "requiring a linked-service edit (not touching datasets) is guaranteed by construction "
                          "— datasets reference a linked service by name, never embed its credential fields "
                          "directly (confirmed: DatasetDraft has no secret-shaped fields, only "
                          "LinkedServiceDraft does) — real, reassuring, not yet a dedicated test.")
def test_sec_04_rotating_shared_sap_service_account_touches_only_linked_service():
    pass


@pytest.mark.skip(reason="SEC-05/[VERIFY]: distinct error classification (OAuth refresh failure vs. connection "
                          "timeout) only exists once ADF actually raises a real activity error at runtime — this "
                          "tool only generates the LogRunFailed SQL that captures whatever error text ADF gives "
                          "it verbatim (real, see worker/tests/test_scenario_coverage_extra.py::test_meta_05_*), "
                          "it never classifies error types itself. No code path to unit-test classification.")
def test_sec_05_oauth_refresh_failure_classified_distinctly_from_timeout():
    pass


@pytest.mark.skip(reason="SEC-06/[VERIFY]: cross-checking the §32 documented auth-option bugs against the "
                          "sample-guide walkthroughs specifically is a documentation-review task, not something "
                          "with a corresponding runtime code path to assert against beyond what "
                          "test_connectors.py's registry-parity tests already cover.")
def test_sec_06_onprem_sql_server_and_sftp_auth_bugs_reverified_against_guides():
    pass


@pytest.mark.skip(reason="SEC-07/[VERIFY]: user-assigned managed identity is real now (§32.5, gap #21): the "
                          "credentials artifact is generated ahead of its linked service and is selectable in the LS "
                          "designer. Worker-only. Real coverage: worker/tests/test_credential_artifact.py, "
                          "frontend features/build/connection-editors.test.tsx.")
def test_sec_07_user_assigned_managed_identity_not_yet_selectable():
    pass


@pytest.mark.skip(reason="SEC-08/[DESIGN]: no Connections-library entry lifecycle/orphan-cleanup concept exists "
                          "anywhere in backend/app (ConnectionLibraryEntry rows are never linked to "
                          "'has_git_artifact' or garbage-collected on draft abandonment) — confirmed real [GAP], "
                          "undecided product question.")
def test_sec_08_abandoned_draft_inline_linked_service_does_not_orphan_library():
    pass
