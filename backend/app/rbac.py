"""Declarative RBAC registry (AGENTS.md §11, §27.4).

Before this module, checks were added endpoint-by-endpoint (``require_role``)
and only ever landed on ``admin.py`` — the rest of the routers accepted any
authenticated caller regardless of role (§23.12 gap #15). ``PERMISSIONS`` is
§11's table as code; ``require_permission`` is the one dependency every router
uses, so the table and the enforcement can't drift apart again.

**Scope, not just role name.** §27.4 also calls for restricting a TEAM_ADMIN
(and, by the same reasoning, a DEVELOPER/VIEWER — every non-PLATFORM_ADMIN
role) to their own team: holding the right role name was not enough, because
nothing checked that the *target* project/pipeline/artifact actually belonged
to the caller's team. Concretely, before ``require_scope``/``scoped_team_id``
below: any authenticated DEVELOPER could edit another team's project settings,
create/generate/validate/push another team's pipeline, or read another team's
artifact content just by knowing/guessing its id — and ``GET /projects``,
``GET /pipelines``, ``GET /artifacts`` called with no filter returned every
team's rows to any authenticated user, not just the caller's own team. Fixed
2026-09-12 — closes the remainder of gap #15 (was tracked in
IMPLEMENTATION_PLAN.md as "Remaining: the scope half of §27.4").

``connectors.py`` deliberately has no entry/gate here: it's a static,
non-tenant catalog with nothing to protect, and gating it behind
``current_user`` (which needs a DB round trip) would make a read-only,
DB-independent endpoint fail whenever the metastore is unreachable.

Team directory endpoints (``GET /teams``, ``/teams/{id}``,
``/teams/{id}/members``) are deliberately left org-wide-visible, matching
§11's unqualified "View ✓" for every role — this app has no concept of a
"private" team the way it has private project/pipeline data, so no
``require_scope`` call was added there. ``environment`` stays global too
(deliberate — see the comment on its own ``PERMISSIONS`` entry below).

**Why this is a Python dict, not a database table (AGENTS.md §23.13).** §25's
Standards policy (``standards_policies``) is deliberately the opposite shape —
customer-editable rows, because it is org-configurable governance a
``STANDARDS_ADMIN`` is meant to change without a deploy. RBAC is not that: who
may push to Git or edit another team's project is a security boundary, and a
security boundary that can be edited through a UI/API without review is a
privilege-escalation path waiting to happen. Keeping ``PERMISSIONS`` in one
reviewable, version-controlled file means every access-control change goes
through the same code review as everything else. Don't "unify" this with
Standards' data-driven model for consistency — that would be a regression.
"""
from __future__ import annotations

from fastapi import Depends

from app import models
from app.api.deps import current_user
from app.errors import ApiError

ALL_ROLES = {"PLATFORM_ADMIN", "TEAM_ADMIN", "DEVELOPER", "VIEWER"}
_CAN_EDIT_OR_ABOVE = {"PLATFORM_ADMIN", "TEAM_ADMIN", "DEVELOPER"}

# (resource, action) -> roles allowed, mirroring AGENTS.md §11's table.
# PLATFORM_ADMIN is always allowed (see `_allowed`) even where not listed here.
PERMISSIONS: dict[tuple[str, str], set[str]] = {
    ("team", "view"): ALL_ROLES,
    ("team", "create"): {"PLATFORM_ADMIN"},
    ("project", "view"): ALL_ROLES,
    ("project", "create"): {"PLATFORM_ADMIN"},
    # §11 lists this as "configurable" for DEVELOPER — allowed by default until
    # §25's per-project Standards policy can turn it off per org/team.
    ("project", "edit"): _CAN_EDIT_OR_ABOVE,
    ("template", "view"): ALL_ROLES,
    ("pipeline", "view"): ALL_ROLES,
    ("pipeline", "create"): _CAN_EDIT_OR_ABOVE,
    ("pipeline", "edit"): _CAN_EDIT_OR_ABOVE,
    ("pipeline", "generate"): _CAN_EDIT_OR_ABOVE,
    ("pipeline", "validate"): _CAN_EDIT_OR_ABOVE,
    ("artifact", "view"): ALL_ROLES,
    # Saved connections (§24.17 connection_library) — same bar as pipelines:
    # any role may view a project's connections, edit/create/delete needs
    # DEVELOPER or above.
    ("connection", "view"): ALL_ROLES,
    ("connection", "create"): _CAN_EDIT_OR_ABOVE,
    ("connection", "edit"): _CAN_EDIT_OR_ABOVE,
    ("connection", "delete"): _CAN_EDIT_OR_ABOVE,
    # Managed identities (§32.6/§32.8, gap #21 phase MI3) — registering or editing a UAMI
    # pick-list entry is infrastructure trust configuration, the same tier as Standards
    # policy administration: TEAM_ADMIN+ only, not DEVELOPER (unlike connections above).
    ("managed_identity", "view"): ALL_ROLES,
    ("managed_identity", "create"): {"PLATFORM_ADMIN", "TEAM_ADMIN"},
    ("managed_identity", "edit"): {"PLATFORM_ADMIN", "TEAM_ADMIN"},
    ("managed_identity", "delete"): {"PLATFORM_ADMIN", "TEAM_ADMIN"},
    # §27.5 SSO provider config — org-wide trust configuration, PLATFORM_ADMIN only
    # (even to view: it names the IdP tenant and the Key Vault secret reference).
    ("sso_configuration", "view"): {"PLATFORM_ADMIN"},
    ("sso_configuration", "edit"): {"PLATFORM_ADMIN"},
    ("git", "push"): _CAN_EDIT_OR_ABOVE,
    ("git", "create_pr"): _CAN_EDIT_OR_ABOVE,
    ("environment", "view"): ALL_ROLES,
    # Environment *connection config* is infra-adjacent, same bar as project
    # edit — not full PLATFORM_ADMIN-only (§23.12 gap 12: still never a deploy
    # action, just who may record the storage/SQL/Key Vault/factory targets).
    ("environment", "manage"): {"PLATFORM_ADMIN", "TEAM_ADMIN"},
    ("admin", "manage_users"): {"PLATFORM_ADMIN", "TEAM_ADMIN"},
    ("admin", "view_audit"): {"PLATFORM_ADMIN", "TEAM_ADMIN"},
    # New-account onboarding is centralized (user's own stated preference:
    # "platform admin should be added from backend", extended to "users are
    # provisioned by the platform admin" — no self-signup). Editing an
    # existing user (role/team/active) is also open to TEAM_ADMIN, but scoped
    # to their own team and never to grant PLATFORM_ADMIN or move a user to a
    # different team — enforced in app/api/users.py, not just here.
    ("user", "create"): {"PLATFORM_ADMIN"},
    ("user", "edit"): {"PLATFORM_ADMIN", "TEAM_ADMIN"},
    # STTM Silver/Gold Object Registry (§33.10): reuses the 4 existing roles,
    # no new role invented, per explicit user instruction. create/edit/
    # upload/commit -> DEVELOPER+ (same bar as pipeline edit); hard delete ->
    # TEAM_ADMIN+ (checked again inline in the route for the `?hard=true`
    # branch specifically, same two-layer pattern as users.py's own extra
    # restrictions beyond the role-name check); read (list/get, incl. the
    # Build dropdown) -> every role including VIEWER.
    ("silver_object", "view"): ALL_ROLES,
    ("silver_object", "edit"): _CAN_EDIT_OR_ABOVE,
    ("silver_object", "delete"): _CAN_EDIT_OR_ABOVE,
    ("mart_object", "view"): ALL_ROLES,
    ("mart_object", "edit"): _CAN_EDIT_OR_ABOVE,
    ("mart_object", "delete"): _CAN_EDIT_OR_ABOVE,
    # Bronze Source Onboarding Registry (§26): same bar as silver/mart above.
    ("onboarding_object", "view"): ALL_ROLES,
    ("onboarding_object", "edit"): _CAN_EDIT_OR_ABOVE,
    ("onboarding_object", "delete"): _CAN_EDIT_OR_ABOVE,
    # Data Quality rules on a Bronze onboarding object (§30.6's "Author DQ rules" row —
    # PLATFORM_ADMIN/TEAM_ADMIN/DEVELOPER, not VIEWER).
    ("dq_rule", "view"): ALL_ROLES,
    ("dq_rule", "create"): _CAN_EDIT_OR_ABOVE,
    ("dq_rule", "delete"): _CAN_EDIT_OR_ABOVE,
    # Per-project external integrations (§29.1 layer 6 live ARM check, Purview
    # lineage push): configuring the service principal/sandbox is an admin
    # action (§29.4); running a check or a push is a developer one.
    ("integration", "view"): ALL_ROLES,
    ("integration", "configure"): {"PLATFORM_ADMIN", "TEAM_ADMIN"},
    ("live_check", "run"): _CAN_EDIT_OR_ABOVE,
    ("lineage", "push"): _CAN_EDIT_OR_ABOVE,
}


def _allowed(user: models.User, resource: str, action: str) -> bool:
    if user.role == "PLATFORM_ADMIN":
        return True
    allowed_roles = PERMISSIONS.get((resource, action))
    if allowed_roles is None:
        # Fail closed: an (resource, action) pair with no registry entry is a
        # bug in the route, not an open door.
        return False
    return user.role in allowed_roles


def require_permission(resource: str, action: str):
    """FastAPI dependency: 403s unless ``PERMISSIONS[(resource, action)]`` allows the caller's role."""

    def _dep(user: models.User = Depends(current_user)) -> models.User:
        if not _allowed(user, resource, action):
            raise ApiError(403, "forbidden", f"Requires permission to {action} {resource}.")
        return user

    return _dep


# A team_id no real row can ever have (ids are generated via `new_id`, which
# never produces this), used by `scoped_team_id` to make "caller has no team"
# filter to zero rows instead of accidentally falling through to "no filter".
_NO_TEAM = "__no_team__"


def require_scope(user: models.User, owning_team_id: str | None) -> None:
    """§27.4 scope check for a single already-loaded resource.

    Call this *after* loading the target project/pipeline/artifact/push, in
    addition to (not instead of) the role check ``require_permission`` already
    did — role says *what* the caller's role may do, this says *whether this
    particular row* is theirs to do it to. ``PLATFORM_ADMIN`` bypasses (same
    as ``_allowed``); every other role must have ``user.team_id ==
    owning_team_id``. A ``None`` owning_team_id means the resource has no team
    boundary (e.g. a template) and is never scope-checked.
    """
    if user.role == "PLATFORM_ADMIN":
        return
    if owning_team_id is not None and user.team_id != owning_team_id:
        raise ApiError(403, "forbidden", "You do not have access to this team's resources.")


def scoped_team_id(user: models.User, requested_team_id: str | None) -> str | None:
    """§27.4 scope check for a *list* endpoint (no single row to load yet).

    Resolves the ``team_id`` a list query should actually filter by, so a
    caller can never widen a list past their own team:

    - ``PLATFORM_ADMIN`` gets ``requested_team_id`` back unchanged (``None``
      means "every team", same as before this fix).
    - Anyone else asking for a *different* team's id is rejected outright
      (403) — an explicit cross-team request is a clearer bug/probe signal
      than silently substituting their own team's data instead.
    - Anyone else asking for no filter (``None``) is narrowed to their own
      ``user.team_id`` — this is the actual fix: previously "no filter" meant
      "every team, for every role," which is how ``GET /projects``,
      ``GET /pipelines`` and ``GET /artifacts`` leaked the whole org to any
      authenticated caller.
    - A non-admin with no ``team_id`` of their own (data anomaly — every
      seeded non-admin should have one) gets the ``_NO_TEAM`` sentinel, which
      matches no real row, rather than falling through to "no filter."
    """
    if user.role == "PLATFORM_ADMIN":
        return requested_team_id
    if requested_team_id is not None and requested_team_id != user.team_id:
        raise ApiError(403, "forbidden", "You do not have access to this team's resources.")
    return user.team_id or _NO_TEAM
