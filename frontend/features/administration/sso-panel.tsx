"use client";

import { useCallback, useEffect, useState } from "react";
import { Pencil, Plus, Save, Trash2, X } from "lucide-react";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { EmptyState } from "@/components/common/empty-state";
import { ROLE_LABELS } from "@/lib/state";
import { api, ApiError } from "@/lib/api/client";
import type { SsoConfiguration, SsoConfigurationInput, SsoRoleMapping, Team } from "@/types";

const EMPTY: SsoConfigurationInput = {
  name: "",
  protocol: "oidc",
  issuerUrl: "",
  clientId: "",
  clientSecretKeyVault: "",
  clientSecretName: "",
  metadataUrl: "",
  entityId: "",
  groupClaim: "groups",
  jitProvisioning: true,
  enabled: false,
  roleMappings: [],
};

function toInput(cfg: SsoConfiguration): SsoConfigurationInput {
  return {
    ...EMPTY,
    name: cfg.name,
    protocol: cfg.protocol,
    issuerUrl: cfg.issuerUrl ?? "",
    clientId: cfg.clientId ?? "",
    clientSecretKeyVault: cfg.clientSecretKeyVault ?? "",
    clientSecretName: cfg.clientSecretName ?? "",
    metadataUrl: cfg.metadataUrl ?? "",
    entityId: cfg.entityId ?? "",
    groupClaim: cfg.groupClaim,
    jitProvisioning: cfg.jitProvisioning,
    enabled: cfg.enabled,
    roleMappings: cfg.roleMappings,
  };
}

/**
 * §27.5 SSO admin config (PLATFORM_ADMIN) — identity-provider metadata and group→role mappings for
 * JIT provisioning. Stored server-side only: sign-in still runs on AUTH_MODE (mock/password); the
 * OIDC/SAML adapter that will read these rows is not built yet, which the panel says up front.
 */
export function SsoPanel({ teams }: { teams: Team[] }) {
  const [configs, setConfigs] = useState<SsoConfiguration[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<{ id?: string; draft: SsoConfigurationInput } | null>(null);
  const [saving, setSaving] = useState(false);

  const load = useCallback(() => {
    api.admin
      .ssoConfigurations()
      .then((rows) => {
        setConfigs(rows);
        setError(null);
      })
      .catch((err) => {
        setConfigs([]);
        setError(err instanceof Error ? err.message : "Could not load SSO configurations.");
      });
  }, []);
  useEffect(load, [load]);

  function patch(update: Partial<SsoConfigurationInput>) {
    setEditing((prev) => (prev ? { ...prev, draft: { ...prev.draft, ...update } } : prev));
  }

  function patchMapping(index: number, update: Partial<SsoRoleMapping>) {
    if (!editing) return;
    patch({ roleMappings: editing.draft.roleMappings.map((m, i) => (i === index ? { ...m, ...update } : m)) });
  }

  async function save() {
    if (!editing) return;
    setSaving(true);
    try {
      if (editing.id) await api.admin.updateSsoConfiguration(editing.id, editing.draft);
      else await api.admin.createSsoConfiguration(editing.draft);
      setEditing(null);
      load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not save the SSO configuration.");
    } finally {
      setSaving(false);
    }
  }

  async function remove(cfg: SsoConfiguration) {
    try {
      await api.admin.deleteSsoConfiguration(cfg.id);
      load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not delete the SSO configuration.");
    }
  }

  const draft = editing?.draft;
  return (
    <section className="panel catalog-panel mt-6" aria-label="Single sign-on">
      <div className="flex items-start justify-between gap-3 px-4 pt-4">
        <div>
          <h2 className="text-sm font-semibold text-ink">Single sign-on (SSO)</h2>
          <p className="text-xs text-muted">
            Identity-provider settings and group → role mappings. Saved for the upcoming OIDC/SAML sign-in — sign-in
            still uses the configured AUTH_MODE today.
          </p>
        </div>
        {!editing ? (
          <Button variant="outline" size="sm" onClick={() => setEditing({ draft: { ...EMPTY } })}>
            <Plus className="h-3.5 w-3.5" />Add provider
          </Button>
        ) : null}
      </div>
      {error ? <Alert className="mx-4 mt-3" variant="error" description={error} /> : null}

      {draft ? (
        <div className="space-y-3 p-4">
          <div className="form-fields">
            <label>
              <span>Name</span>
              <Input value={draft.name} onChange={(e) => patch({ name: e.target.value })} placeholder="e.g. Entra ID" />
            </label>
            <label>
              <span>Protocol</span>
              <Select value={draft.protocol} onChange={(e) => patch({ protocol: e.target.value as "oidc" | "saml" })}>
                <option value="oidc">OIDC</option>
                <option value="saml">SAML 2.0</option>
              </Select>
            </label>
            <label>
              <span>Group claim</span>
              <Input value={draft.groupClaim} onChange={(e) => patch({ groupClaim: e.target.value })} />
            </label>
            {draft.protocol === "oidc" ? (
              <>
                <label>
                  <span>Issuer URL</span>
                  <Input value={draft.issuerUrl ?? ""} onChange={(e) => patch({ issuerUrl: e.target.value })} />
                </label>
                <label>
                  <span>Client ID</span>
                  <Input value={draft.clientId ?? ""} onChange={(e) => patch({ clientId: e.target.value })} />
                </label>
              </>
            ) : (
              <>
                <label>
                  <span>Metadata URL</span>
                  <Input value={draft.metadataUrl ?? ""} onChange={(e) => patch({ metadataUrl: e.target.value })} />
                </label>
                <label>
                  <span>Entity ID</span>
                  <Input value={draft.entityId ?? ""} onChange={(e) => patch({ entityId: e.target.value })} />
                </label>
              </>
            )}
            <label>
              <span>Client secret — Key Vault name</span>
              <Input value={draft.clientSecretKeyVault ?? ""} onChange={(e) => patch({ clientSecretKeyVault: e.target.value })} />
            </label>
            <label>
              <span>Client secret — secret name</span>
              <Input value={draft.clientSecretName ?? ""} onChange={(e) => patch({ clientSecretName: e.target.value })} />
            </label>
            <div className="flex flex-col justify-end gap-1 text-sm">
              <label className="flex items-center gap-2">
                <input type="checkbox" checked={draft.jitProvisioning} onChange={(e) => patch({ jitProvisioning: e.target.checked })} />
                Create users on first sign-in (JIT)
              </label>
              <label className="flex items-center gap-2">
                <input type="checkbox" checked={draft.enabled} onChange={(e) => patch({ enabled: e.target.checked })} />
                Enabled
              </label>
            </div>
          </div>
          <p className="text-xs text-muted">The secret is a Key Vault reference — never paste a secret value here.</p>

          <div>
            <h3 className="text-xs font-semibold text-ink">Group → role mappings (first match wins)</h3>
            {draft.roleMappings.map((m, i) => (
              <div key={i} className="mt-2 grid items-center gap-2 md:grid-cols-[1fr_180px_180px_auto]">
                <Input aria-label={`Group ${i + 1}`} placeholder="IdP group value" value={m.groupValue} onChange={(e) => patchMapping(i, { groupValue: e.target.value })} />
                <Select aria-label={`Role ${i + 1}`} value={m.role} onChange={(e) => patchMapping(i, { role: e.target.value as SsoRoleMapping["role"] })}>
                  {Object.entries(ROLE_LABELS).map(([role, label]) => (
                    <option key={role} value={role}>{label}</option>
                  ))}
                </Select>
                <Select
                  aria-label={`Team ${i + 1}`}
                  value={m.teamId ?? ""}
                  disabled={m.role === "PLATFORM_ADMIN"}
                  onChange={(e) => patchMapping(i, { teamId: e.target.value || null })}
                >
                  <option value="">{m.role === "PLATFORM_ADMIN" ? "All teams" : "Select team"}</option>
                  {teams.map((t) => (
                    <option key={t.id} value={t.id}>{t.name}</option>
                  ))}
                </Select>
                <Button variant="ghost" size="icon" aria-label={`Remove mapping ${i + 1}`} onClick={() => patch({ roleMappings: draft.roleMappings.filter((_, j) => j !== i) })}>
                  <X className="h-3.5 w-3.5" />
                </Button>
              </div>
            ))}
            <Button variant="ghost" size="sm" className="mt-2" onClick={() => patch({ roleMappings: [...draft.roleMappings, { groupValue: "", role: "VIEWER", teamId: null }] })}>
              <Plus className="h-3.5 w-3.5" />Add mapping
            </Button>
          </div>

          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={() => setEditing(null)}>Cancel</Button>
            <Button onClick={() => void save()} disabled={saving || !draft.name.trim()}>
              <Save className="h-3.5 w-3.5" />Save provider
            </Button>
          </div>
        </div>
      ) : configs === null ? (
        <EmptyState status="loading" title="Loading SSO configurations" message="" />
      ) : configs.length === 0 ? (
        <EmptyState title="No identity providers" message="Add an OIDC or SAML provider to prepare for single sign-on." />
      ) : (
        <div className="divide-y divide-line">
          {configs.map((cfg) => (
            <div key={cfg.id} className="project-row" style={{ gridTemplateColumns: "minmax(0,1fr) 160px auto" }}>
              <div>
                <strong>{cfg.name}</strong>
                <span>{cfg.protocol === "oidc" ? cfg.issuerUrl : cfg.metadataUrl}</span>
              </div>
              <span className="text-xs text-muted">{cfg.roleMappings.length} group mapping(s)</span>
              <div className="flex items-center justify-end gap-1.5">
                <Badge variant={cfg.enabled ? "green" : "gray"}>{cfg.enabled ? "Enabled" : "Disabled"}</Badge>
                <Badge variant="blue">{cfg.protocol.toUpperCase()}</Badge>
                <Button variant="ghost" size="sm" aria-label={`Edit ${cfg.name}`} onClick={() => setEditing({ id: cfg.id, draft: toInput(cfg) })}>
                  <Pencil className="h-3.5 w-3.5" />
                </Button>
                <Button variant="ghost" size="sm" aria-label={`Delete ${cfg.name}`} onClick={() => void remove(cfg)}>
                  <Trash2 className="h-3.5 w-3.5" />
                </Button>
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
