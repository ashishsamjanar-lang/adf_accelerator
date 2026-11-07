"""SSO identity-provider adapters (AGENTS.md §27.2/§27.5) — **scaffolding only**.

Nothing here is wired into sign-in: ``AUTH_MODE=oidc|saml`` still 501s in
``app/api/deps.py``, and the ``/auth/sso/*`` routes answer 501
``sso_not_implemented``. ``AUTH_MODE=password`` is the supported sign-in. See
``base.py`` for the contract and the intended flow.
"""
from __future__ import annotations

from app import models
from app.identity.base import ExternalIdentity, IdentityProvider, LoginRedirect, SsoNotImplementedError
from app.identity.oidc import OidcIdentityProvider
from app.identity.saml import SamlIdentityProvider

_PROVIDERS: dict[str, type[IdentityProvider]] = {
    "oidc": OidcIdentityProvider,
    "saml": SamlIdentityProvider,
}


def get_identity_provider(configuration: models.SsoConfiguration, callback_base_url: str) -> IdentityProvider:
    """The adapter for one ``app.sso_configurations`` row, by its ``protocol``."""
    try:
        provider_cls = _PROVIDERS[configuration.protocol]
    except KeyError as exc:
        raise ValueError(f"Unknown SSO protocol {configuration.protocol!r}; expected oidc or saml.") from exc
    return provider_cls(configuration, callback_base_url)


__all__ = [
    "ExternalIdentity",
    "IdentityProvider",
    "LoginRedirect",
    "OidcIdentityProvider",
    "SamlIdentityProvider",
    "SsoNotImplementedError",
    "get_identity_provider",
]
