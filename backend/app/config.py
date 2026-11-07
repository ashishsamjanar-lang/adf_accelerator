from __future__ import annotations

import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# The ONE runtime-parameter file for every service (config/platform.env, see
# config/platform.env.example). Resolved from this file's location — not the
# process's working directory — so `uvicorn`, `pytest`, `alembic` and
# `python -m app.manage` all read it no matter where they are launched from.
# PLATFORM_ENV_FILE overrides the path; inside a container the file is usually
# absent and docker-compose / Container Apps / Helm inject the same keys as real
# environment variables instead (which always win over the file).
PLATFORM_ENV_FILE = Path(
    os.environ.get("PLATFORM_ENV_FILE") or Path(__file__).resolve().parents[2] / "config" / "platform.env"
)

# Dev-only fallback Fernet key (32 zero bytes, base64-encoded) — never used
# unless GIT_TOKEN_FERNET_KEY is unset. Overriding it is required for anything
# beyond local docker-compose (see app/crypto.py, AGENTS.md §23.12 gap #17).
_DEV_ONLY_FERNET_KEY = "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA="

# Placeholder values shipped in config/platform.env.example — never real
# config, so a fail-fast check (below) treats them the same as "unset".
_PLACEHOLDER_PREFIX = "replace-with-"


class Settings(BaseSettings):
    app_name: str = "adf-accelerator"
    app_env: str = "local"
    log_level: str = "INFO"
    database_url: str = "postgresql+psycopg://accelerator:change-me-local-only@localhost:5432/accelerator"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    # mock (code default, no auth — dev/demo/CI only) | password (username or
    # email + password, admin-created accounts; what config/platform.env.example
    # ships) | oidc | saml (§27.2 — scaffolded in app/identity/, not implemented;
    # current_user() 501s).
    auth_mode: str = "mock"
    # AUTH_MODE=password session timeouts (§27.3) — idle (re-auth after
    # inactivity) and absolute (re-auth after N hours regardless of activity).
    auth_idle_timeout_minutes: int = 30
    auth_absolute_timeout_hours: int = 12
    # Failed-login lockout (§27 extension) — after this many consecutive
    # failures the account is locked for auth_lockout_minutes.
    auth_max_failed_logins: int = 5
    auth_lockout_minutes: int = 15
    # Minimum length for an admin-chosen initial password and every password change.
    auth_min_password_length: int = 8

    # SQLAlchemy engine sizing (app/db/session.py). Defaults give one process
    # up to 20 concurrent DB-bound requests (pydantic's default 5+10=15 was
    # sized for light dev traffic, not ~25 concurrently-active users) while
    # staying well under Postgres's default max_connections=100 even with a
    # few API replicas. Bump via env if a deploy runs more replicas or a
    # smaller Postgres max_connections.
    db_pool_size: int = 10
    db_max_overflow: int = 10

    # SSO (AUTH_MODE=oidc|saml, §27.2) — scaffolding only (app/identity/), no
    # login path uses these yet. Per-provider settings (issuer, client id, secret
    # *reference*, group→role mappings) live in app.sso_configurations, managed
    # from Admin → Users; only the app-wide callback origin belongs here.
    # Public origin the IdP redirects back to, e.g. https://adf-forge.contoso.com
    sso_callback_base_url: str = ""

    # Previously only read ad hoc via os.environ in worker/app/gitops/
    # credential_vault.py — a second, untyped config path alongside this one
    # (IMPLEMENTATION_PLAN.md gap B-5). The backend itself doesn't call Key
    # Vault directly today, but typing it here lets a startup check catch a
    # missing value before a live QA/PROD deploy instead of a silent
    # worker-side fallback to env-var/SimulatedAdapter behavior.
    key_vault_name: str = ""

    # ADF factory identifiers — documented in config/platform.env.example,
    # not read by any code path yet (this app never deploys, §20); typed here
    # so they're validated/available once a downstream hand-off needs them
    # (IMPLEMENTATION_PLAN.md gap A-5) instead of silently dropped by `extra="ignore"`.
    adf_subscription_id: str = ""
    adf_resource_group: str = ""
    adf_factory_name: str = ""

    # §30.1/§30.8 — append-only audit log retention window, purged only by the
    # `python -m app.manage purge-audit-log` CLI (never a user-facing action).
    # Default kept generous (400 days, §30.8 open assumption #2) until a
    # customer's compliance need says otherwise.
    audit_retention_days: int = 400

    source_repository_url: str = ""
    # Fallback Git credential when a push carries none of its own and no
    # per-project Key Vault reference resolves (worker/app/gitops/credential_vault.py).
    git_token: str = ""
    # Shared with the worker (same env var) so it can decrypt what this service
    # encrypts before persisting a per-push Git token (§23.12 gap #17).
    git_token_fernet_key: str = _DEV_ONLY_FERNET_KEY

    model_config = SettingsConfigDict(env_file=PLATFORM_ENV_FILE, extra="ignore")


settings = Settings()


class InsecureConfigError(RuntimeError):
    """A required value is still an insecure default outside local dev (gap B-5)."""


def check_production_config(settings: Settings) -> list[str]:
    """Non-fatal config problems for anything beyond local dev (gap B-5).

    Returns human-readable warnings the caller should log loudly. Separate
    from :func:`require_secure_fernet_key`, which is the one check worth
    crashing startup over — see that function's docstring. Never called for
    ``app_env == "local"``; insecure defaults are the *intended* behavior
    there.
    """
    problems: list[str] = []
    if settings.app_env == "local":
        return problems
    if settings.auth_mode == "mock":
        problems.append(
            "AUTH_MODE=mock outside local dev — anyone who can reach the app acts as the seeded platform admin. "
            "Set AUTH_MODE=password."
        )
    if settings.auth_mode in ("oidc", "saml"):
        problems.append(
            f"AUTH_MODE={settings.auth_mode} is scaffolded but not implemented — every request will 501. "
            "Use AUTH_MODE=password."
        )
    if not settings.key_vault_name or settings.key_vault_name.startswith(_PLACEHOLDER_PREFIX):
        problems.append(
            "KEY_VAULT_NAME is unset or still the example placeholder — Git credential resolution will "
            "silently fall back to env vars / the offline SimulatedAdapter (see worker's credential_vault.py)."
        )
    return problems


def require_secure_fernet_key(settings: Settings) -> None:
    """Hard-fail startup outside local dev if the Fernet key is still the
    published dev-only default (gap B-5).

    Unlike the advisory checks above, this one is worth crashing over: the
    dev key is published in this file, so a per-push Git token encrypted
    with it (app/crypto.py, §23.12 gap #17) is not actually protected —
    running with it anywhere real is worse than not starting at all.
    """
    if settings.app_env != "local" and settings.git_token_fernet_key == _DEV_ONLY_FERNET_KEY:
        raise InsecureConfigError(
            f"GIT_TOKEN_FERNET_KEY is still the published dev-only default for APP_ENV={settings.app_env!r}. "
            "Generate a real key (see config/platform.env.example) and set it identically on the API and "
            "worker before starting outside local dev."
        )
