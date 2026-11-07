"""The ``IdentityProvider`` adapter contract for SSO (AGENTS.md §27.2/§27.5).

**Scaffolding only — not implemented.** Every concrete provider in this
package raises :class:`SsoNotImplementedError`; the ``/auth/sso/*`` routes turn
that into a 501. It exists so the shape of SSO is fixed in code before anyone
wires a real IdP, the same way ``worker/app/gitops/adapter.py`` fixes the Git
adapter shape per provider.

Intended flow once implemented (per request, no server-side SSO state beyond
the existing ``sessions`` table):

1. ``GET /auth/sso/{configuration_id}/login`` → :meth:`IdentityProvider.begin_login`
   returns the IdP redirect URL (OIDC authorization-code + PKCE, or a SAML
   AuthnRequest). ``state``/``nonce`` travel in a short-lived signed cookie.
2. IdP redirects back to ``/auth/sso/{configuration_id}/callback`` →
   :meth:`IdentityProvider.complete_login` validates the response (signature,
   issuer, audience, expiry, nonce) and returns an :class:`ExternalIdentity`.
3. :func:`app.identity.provisioning.provision_user` maps it to a ``users`` row
   (JIT create/update from ``sso_role_mappings``), then the caller issues the
   same cookie session ``AUTH_MODE=password`` already uses.

Client secrets are Key Vault *references* on ``app.sso_configurations`` (rule
#6) — a provider resolves them at call time and never persists or logs them.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field

from app import models


class SsoNotImplementedError(NotImplementedError):
    """Raised by every SSO code path until a real provider is implemented."""

    def __init__(self, what: str) -> None:
        super().__init__(f"SSO is scaffolded but not implemented yet: {what}.")


@dataclass(frozen=True)
class LoginRedirect:
    """Where to send the browser, plus the values the callback must see again."""

    url: str
    state: str
    nonce: str | None = None


@dataclass(frozen=True)
class ExternalIdentity:
    """The verified result of an IdP login, independent of protocol."""

    subject: str  # stable IdP subject (OIDC `sub` / SAML NameID)
    email: str
    name: str
    groups: tuple[str, ...] = field(default_factory=tuple)
    issuer: str = ""


class IdentityProvider(ABC):
    """One configured IdP (a row of ``app.sso_configurations``)."""

    def __init__(self, configuration: models.SsoConfiguration, callback_base_url: str) -> None:
        self.configuration = configuration
        self.callback_base_url = callback_base_url.rstrip("/")

    @property
    def redirect_uri(self) -> str:
        return f"{self.callback_base_url}/api/v1/auth/sso/{self.configuration.id}/callback"

    @abstractmethod
    def begin_login(self) -> LoginRedirect:
        """Build the redirect that starts a login at the IdP."""

    @abstractmethod
    def complete_login(
        self, params: Mapping[str, str], *, expected_state: str, expected_nonce: str | None
    ) -> ExternalIdentity:
        """Validate the IdP's callback and return the verified identity."""

    def logout_url(self) -> str | None:
        """Optional IdP-side (single) logout URL. None = local logout only."""
        return None
