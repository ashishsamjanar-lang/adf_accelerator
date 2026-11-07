from __future__ import annotations

import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Same single runtime-parameter file as the API (see backend/app/config.py),
# resolved from this file's location rather than the working directory.
PLATFORM_ENV_FILE = Path(
    os.environ.get("PLATFORM_ENV_FILE") or Path(__file__).resolve().parents[2] / "config" / "platform.env"
)

# Dev-only fallback Fernet key (32 zero bytes, base64-encoded) — must match
# the API's default in backend/app/config.py so a bare docker-compose stack
# still works; override GIT_TOKEN_FERNET_KEY in both services for anything
# beyond local dev (see app/crypto.py, AGENTS.md §23.12 gap #17).
_DEV_ONLY_FERNET_KEY = "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA="

# Placeholder values shipped in config/platform.env.example — never real
# config, so a fail-fast check (below) treats them the same as "unset".
_PLACEHOLDER_PREFIX = "replace-with-"


class Settings(BaseSettings):
    app_name: str = "adf-accelerator"
    app_env: str = "local"
    log_level: str = "INFO"
    database_url: str = "postgresql+psycopg://accelerator:change-me-local-only@localhost:5432/accelerator"
    # How often the worker polls the jobs table (seconds).
    worker_poll_interval: float = 2.0
    # Max attempts before a job is marked permanently failed.
    worker_max_attempts: int = 3
    # Shared with the API (same env var) — decrypts the per-push Git token it encrypts.
    git_token_fernet_key: str = _DEV_ONLY_FERNET_KEY

    # Previously only read ad hoc via ``os.environ`` in
    # ``app/gitops/credential_vault.py`` — a second, untyped config path
    # alongside this one (IMPLEMENTATION_PLAN.md gap B-5). Now the single
    # source of truth; ``credential_vault.py`` reads these, not ``os.environ``,
    # for anything with a fixed name (a per-project secret *name* is still a
    # dynamic ``os.environ`` lookup by design — see that module).
    key_vault_name: str = ""
    git_token: str = ""

    model_config = SettingsConfigDict(env_file=PLATFORM_ENV_FILE, extra="ignore")


settings = Settings()


class InsecureConfigError(RuntimeError):
    """A required value is still an insecure default outside local dev (gap B-5)."""


def require_secure_fernet_key(settings: Settings) -> None:
    """Hard-fail startup outside local dev if the Fernet key is still the
    published dev-only default (gap B-5, mirrors backend/app/config.py).

    A per-push Git token encrypted with the published dev key (app/crypto.py,
    §23.12 gap #17) is not actually protected — running with it anywhere real
    is worse than not starting at all.
    """
    if settings.app_env != "local" and settings.git_token_fernet_key == _DEV_ONLY_FERNET_KEY:
        raise InsecureConfigError(
            f"GIT_TOKEN_FERNET_KEY is still the published dev-only default for APP_ENV={settings.app_env!r}. "
            "Generate a real key (see config/platform.env.example) and set it identically on the API and "
            "worker before starting outside local dev."
        )


def check_production_config(settings: Settings) -> list[str]:
    """Non-fatal config warnings for anything beyond local dev (gap B-5)."""
    if settings.app_env == "local":
        return []
    problems: list[str] = []
    if not settings.key_vault_name or settings.key_vault_name.startswith(_PLACEHOLDER_PREFIX):
        problems.append(
            "KEY_VAULT_NAME is unset or still the example placeholder — Git credential resolution will "
            "silently fall back to env vars / the offline SimulatedAdapter (see credential_vault.py)."
        )
    return problems
