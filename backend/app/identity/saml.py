"""SAML 2.0 provider (ADFS, Entra ID enterprise apps, PingFederate, ...).

**Not implemented** — see ``app/identity/base.py``. To implement:

* ``begin_login``: load IdP metadata from ``metadata_url`` (cache it), build a
  signed AuthnRequest for SP ``entity_id`` with ``RelayState`` = ``state``,
  HTTP-Redirect binding.
* ``complete_login``: accept the HTTP-POST ``SAMLResponse``, verify the
  assertion signature against the IdP metadata certificate, ``Audience ==
  entity_id``, ``Destination == redirect_uri``, NotBefore/NotOnOrAfter and
  ``InResponseTo``, then read NameID, email/name attributes and the configured
  ``group_claim`` attribute. A library such as ``python3-saml`` is the expected
  dependency — do not hand-roll XML signature verification.
"""
from __future__ import annotations

from collections.abc import Mapping

from app.identity.base import ExternalIdentity, IdentityProvider, LoginRedirect, SsoNotImplementedError


class SamlIdentityProvider(IdentityProvider):
    def begin_login(self) -> LoginRedirect:
        raise SsoNotImplementedError("SAML AuthnRequest")

    def complete_login(
        self, params: Mapping[str, str], *, expected_state: str, expected_nonce: str | None
    ) -> ExternalIdentity:
        raise SsoNotImplementedError("SAML response validation")
