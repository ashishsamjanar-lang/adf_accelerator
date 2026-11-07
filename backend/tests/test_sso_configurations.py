"""SSO configuration admin endpoints (AGENTS.md §27.5, ID2 admin half).

Metadata only — no login path reads these rows yet. PLATFORM_ADMIN only; client
secrets are Key Vault references, never values; group→role mappings validate
the role and (for non-platform roles) the team.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

BASE = "/api/v1/admin/sso-configurations"
OIDC = {
    "name": "Entra ID",
    "protocol": "oidc",
    "issuerUrl": "https://login.microsoftonline.com/tenant/v2.0",
    "clientId": "app-client-id",
    "clientSecretKeyVault": "kv-platform",
    "clientSecretName": "sso-client-secret",
}


def test_create_list_update_delete(client: TestClient, seed: dict[str, str]) -> None:
    created = client.post(
        BASE,
        json={**OIDC, "roleMappings": [{"groupValue": "adf-devs", "role": "DEVELOPER", "teamId": seed["team_id"]},
                                       {"groupValue": "adf-admins", "role": "PLATFORM_ADMIN", "teamId": seed["team_id"]}]},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["enabled"] is False and body["groupClaim"] == "groups"
    assert body["roleMappings"][1] == {"groupValue": "adf-admins", "role": "PLATFORM_ADMIN", "teamId": None}

    listed = client.get(BASE).json()
    assert [c["name"] for c in listed] == ["Entra ID"]

    updated = client.put(f"{BASE}/{body['id']}", json={**OIDC, "enabled": True, "roleMappings": []})
    assert updated.status_code == 200
    assert updated.json()["enabled"] is True and updated.json()["roleMappings"] == []

    assert client.delete(f"{BASE}/{body['id']}").status_code == 204
    assert client.get(BASE).json() == []


def test_validation_errors(client: TestClient, seed: dict[str, str]) -> None:
    def code(payload: dict) -> str:
        return client.post(BASE, json=payload).json()["error"]["code"]

    assert code({**OIDC, "issuerUrl": ""}) == "sso_oidc_incomplete"
    assert code({"name": "Okta", "protocol": "saml", "metadataUrl": "https://x"}) == "sso_saml_incomplete"
    assert code({**OIDC, "protocol": "ldap"}) == "sso_protocol_invalid"
    assert code({**OIDC, "clientSecretName": ""}) == "sso_secret_reference_incomplete"
    assert code({**OIDC, "roleMappings": [{"groupValue": "g", "role": "OWNER"}]}) == "sso_mapping_role_invalid"
    assert code({**OIDC, "roleMappings": [{"groupValue": "g", "role": "VIEWER"}]}) == "sso_mapping_team_required"
    assert code({**OIDC, "roleMappings": [{"groupValue": "g", "role": "VIEWER", "teamId": "nope"}]}) == "sso_mapping_team_not_found"
    assert client.post(BASE, json=OIDC).status_code == 201
    assert client.post(BASE, json=OIDC).status_code == 409


def test_platform_admin_only(client: TestClient, as_user, seed: dict[str, str]) -> None:
    as_user("TEAM_ADMIN")
    assert client.get(BASE).status_code == 403
    assert client.post(BASE, json=OIDC).status_code == 403
