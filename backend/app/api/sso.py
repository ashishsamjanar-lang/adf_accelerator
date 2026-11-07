"""SSO provider configuration endpoints (§27.2/§27.5, ID2 — admin half only).

Stores what an OIDC/SAML ``IdentityProvider`` adapter will need (issuer/metadata,
client id, Key Vault secret reference, group claim, group→role mappings for JIT
provisioning). **No login path reads these rows yet** — ``AUTH_MODE=oidc|saml``
still 501s in ``current_user()``; ``enabled`` is recorded intent, not a switch.
PLATFORM_ADMIN only. A mapping's role must be one of the four §11 roles, and any
non-PLATFORM_ADMIN role must name an existing team (JIT needs somewhere to put the user).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models, repository
from app.api.deps import current_user, db_session
from app.api.schemas import SsoConfigurationIn, SsoConfigurationOut
from app.db.base import new_id
from app.errors import ApiError
from app.rbac import ALL_ROLES, require_permission
from app.services import _actor_name, _iso

router = APIRouter(prefix="/admin/sso-configurations", tags=["admin"])

_view = [Depends(require_permission("sso_configuration", "view"))]
_edit = [Depends(require_permission("sso_configuration", "edit"))]


def _to_dict(db: Session, cfg: models.SsoConfiguration) -> dict:
    return {
        "id": cfg.id,
        "name": cfg.name,
        "protocol": cfg.protocol,
        "issuerUrl": cfg.issuer_url,
        "clientId": cfg.client_id,
        "clientSecretKeyVault": cfg.client_secret_key_vault,
        "clientSecretName": cfg.client_secret_name,
        "metadataUrl": cfg.metadata_url,
        "entityId": cfg.entity_id,
        "groupClaim": cfg.group_claim,
        "jitProvisioning": cfg.jit_provisioning,
        "enabled": cfg.enabled,
        "roleMappings": [{"groupValue": m.group_value, "role": m.role, "teamId": m.team_id} for m in cfg.role_mappings],
        "createdAt": _iso(cfg.created_at),
        "updatedAt": _iso(cfg.updated_at),
        "updatedByName": _actor_name(db, cfg.updated_by),
    }


def _clean(value: str | None) -> str | None:
    return (value or "").strip() or None


def _apply(db: Session, cfg: models.SsoConfiguration, body: SsoConfigurationIn) -> None:
    name = body.name.strip()
    if not name:
        raise ApiError(422, "sso_name_required", "A name is required.")
    protocol = body.protocol.strip().lower()
    if protocol not in {"oidc", "saml"}:
        raise ApiError(422, "sso_protocol_invalid", "Protocol must be 'oidc' or 'saml'.")
    if protocol == "oidc" and not (_clean(body.issuer_url) and _clean(body.client_id)):
        raise ApiError(422, "sso_oidc_incomplete", "OIDC needs an issuer URL and a client ID.")
    if protocol == "saml" and not (_clean(body.metadata_url) and _clean(body.entity_id)):
        raise ApiError(422, "sso_saml_incomplete", "SAML needs a metadata URL and an entity ID.")
    if bool(_clean(body.client_secret_key_vault)) != bool(_clean(body.client_secret_name)):
        raise ApiError(
            422, "sso_secret_reference_incomplete", "Give both the Key Vault name and the secret name, or neither."
        )
    existing = db.scalar(select(models.SsoConfiguration).where(models.SsoConfiguration.name == name))
    if existing is not None and existing.id != cfg.id:
        raise ApiError(409, "sso_name_taken", f"An SSO configuration named '{name}' already exists.")

    mappings: list[models.SsoRoleMapping] = []
    for position, m in enumerate(body.role_mappings):
        group, role, team_id = m.group_value.strip(), m.role.strip(), _clean(m.team_id)
        label = f"Mapping {position + 1}"
        if not group:
            raise ApiError(422, "sso_mapping_group_required", f"{label} needs a group value.")
        if role not in ALL_ROLES:
            raise ApiError(422, "sso_mapping_role_invalid", f"{label} has unknown role '{role}'.")
        if role == "PLATFORM_ADMIN":
            team_id = None
        elif not team_id:
            raise ApiError(422, "sso_mapping_team_required", f"{label} needs a team for role {role}.")
        elif repository.get_team(db, team_id) is None:
            raise ApiError(422, "sso_mapping_team_not_found", f"{label}: team '{team_id}' does not exist.")
        mappings.append(
            models.SsoRoleMapping(id=new_id("ssom"), position=position, group_value=group, role=role, team_id=team_id)
        )

    cfg.name = name
    cfg.protocol = protocol
    cfg.issuer_url = _clean(body.issuer_url) if protocol == "oidc" else None
    cfg.client_id = _clean(body.client_id) if protocol == "oidc" else None
    cfg.client_secret_key_vault = _clean(body.client_secret_key_vault)
    cfg.client_secret_name = _clean(body.client_secret_name)
    cfg.metadata_url = _clean(body.metadata_url) if protocol == "saml" else None
    cfg.entity_id = _clean(body.entity_id) if protocol == "saml" else None
    cfg.group_claim = _clean(body.group_claim) or "groups"
    cfg.jit_provisioning = body.jit_provisioning
    cfg.enabled = body.enabled
    cfg.role_mappings = mappings


def _load(db: Session, config_id: str) -> models.SsoConfiguration:
    cfg = db.get(models.SsoConfiguration, config_id)
    if cfg is None:
        raise ApiError(
            status.HTTP_404_NOT_FOUND, "sso_configuration_not_found", f"SSO configuration '{config_id}' does not exist."
        )
    return cfg


@router.get("", response_model=list[SsoConfigurationOut], dependencies=_view)
def list_configurations(db: Session = Depends(db_session)) -> list[dict]:
    rows = db.scalars(select(models.SsoConfiguration).order_by(models.SsoConfiguration.name)).all()
    return [_to_dict(db, c) for c in rows]


@router.post("", response_model=SsoConfigurationOut, status_code=status.HTTP_201_CREATED, dependencies=_edit)
def create(body: SsoConfigurationIn, db: Session = Depends(db_session), user: models.User = Depends(current_user)) -> dict:
    cfg = models.SsoConfiguration(id=new_id("sso"), created_by=user.id, updated_by=user.id)
    _apply(db, cfg, body)
    db.add(cfg)
    repository.record_audit(db, actor=user.id, action="sso_configuration.created", target=cfg.name)
    db.flush()
    return _to_dict(db, cfg)


@router.put("/{config_id}", response_model=SsoConfigurationOut, dependencies=_edit)
def update(
    config_id: str, body: SsoConfigurationIn, db: Session = Depends(db_session), user: models.User = Depends(current_user)
) -> dict:
    cfg = _load(db, config_id)
    _apply(db, cfg, body)
    cfg.updated_by = user.id
    repository.record_audit(db, actor=user.id, action="sso_configuration.updated", target=cfg.name)
    db.flush()
    return _to_dict(db, cfg)


@router.delete("/{config_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None, dependencies=_edit)
def remove(config_id: str, db: Session = Depends(db_session), user: models.User = Depends(current_user)) -> None:
    cfg = _load(db, config_id)
    repository.record_audit(db, actor=user.id, action="sso_configuration.deleted", target=cfg.name)
    db.delete(cfg)
    db.flush()
