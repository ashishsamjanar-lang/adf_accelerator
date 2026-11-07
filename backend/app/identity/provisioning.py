"""Just-in-time user provisioning from a verified SSO identity (§27.5).

**Not implemented** — see ``app/identity/base.py``. Intended rules:

* Match an existing user by email (case-insensitive). None found and
  ``configuration.jit_provisioning`` is off → reject (403); on → create one.
* Role/team come from the first ``sso_role_mappings`` row (by ``position``)
  whose ``group_value`` is in ``identity.groups``; no match → reject rather than
  guess a role (rule #5: no fabricated values).
* Never touch ``password_hash`` — an SSO account has none, and a
  password-mode account keeps its own.
* Audit ``auth.sso_provisioned`` / ``auth.sso_login`` like password logins.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app import models
from app.identity.base import ExternalIdentity, SsoNotImplementedError


def provision_user(
    db: Session, identity: ExternalIdentity, configuration: models.SsoConfiguration
) -> models.User:
    raise SsoNotImplementedError("just-in-time user provisioning")
