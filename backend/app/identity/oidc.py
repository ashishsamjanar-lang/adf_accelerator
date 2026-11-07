"""OpenID Connect provider (Entra ID, Okta, Auth0, Google, Keycloak, ...).

**Not implemented** — see ``app/identity/base.py``. To implement:

* ``begin_login``: fetch ``{issuer_url}/.well-known/openid-configuration``
  (cache it), build an authorization-code request with PKCE (S256), scopes
  ``openid profile email``, random ``state`` + ``nonce``.
* ``complete_login``: check ``state``, exchange the code at the token endpoint
  with the client secret resolved from Key Vault
  (``client_secret_key_vault``/``client_secret_name``), verify the ID token's
  signature against the JWKS, ``iss``, ``aud == client_id``, ``exp`` and
  ``nonce``, then read ``sub``/``email``/``name`` and the configured
  ``group_claim``.
"""
from __future__ import annotations

from collections.abc import Mapping

from app.identity.base import ExternalIdentity, IdentityProvider, LoginRedirect, SsoNotImplementedError


class OidcIdentityProvider(IdentityProvider):
    def begin_login(self) -> LoginRedirect:
        raise SsoNotImplementedError("OIDC login redirect")

    def complete_login(
        self, params: Mapping[str, str], *, expected_state: str, expected_nonce: str | None
    ) -> ExternalIdentity:
        raise SsoNotImplementedError("OIDC callback validation")
