"use client";

/**
 * Per-project external integrations (`/projects/{id}/integrations/{kind}`):
 * - `live_validation` — §29.1 layer 6: the worker submits a pipeline's artifacts as an ARM
 *   *validate*-mode deployment against a sandbox resource group. Never deploys anything.
 * - `purview` — Onboarding's "Push to Purview" sends registry lineage to this account.
 * Auth is a service principal whose secret is a Key Vault *reference*, or the worker's own identity
 * when left blank. Configure is TEAM_ADMIN+ (server-enforced; the inputs just mirror it).
 */
import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { api, ApiError } from "@/lib/api/client";
import type { IntegrationKind } from "@/types";

const AUTH_FIELDS: [string, string, string?][] = [
  ["tenantId", "Tenant ID"],
  ["clientId", "Client (app) ID"],
  ["clientSecretRef", "Client secret — Key Vault secret name", "Blank = the worker's own managed/workload identity"],
];

const SECTIONS: { kind: IntegrationKind; title: string; blurb: string; fields: [string, string, string?][] }[] = [
  {
    kind: "live_validation",
    title: "Live ARM check (validation layer 6)",
    blurb: "Validate-mode only: ARM preflights the generated factory resources in a sandbox resource group — nothing is created or changed. Findings are warnings.",
    fields: [
      ["subscriptionId", "Sandbox subscription ID"],
      ["resourceGroup", "Sandbox resource group"],
      ["location", "Location", "e.g. westeurope"],
      ["factoryName", "Sandbox factory name", "Defaults to adfacc-live-check"],
      ...AUTH_FIELDS,
    ],
  },
  {
    kind: "purview",
    title: "Microsoft Purview lineage",
    blurb: "Registry lineage (source → Bronze → Silver/Gold) is upserted into this Purview account's Data Map from the Onboarding page.",
    fields: [["accountName", "Purview account name"], ...AUTH_FIELDS],
  },
];

function IntegrationSection({ projectId, canManage, section }: { projectId: string; canManage: boolean; section: (typeof SECTIONS)[number] }) {
  const [enabled, setEnabled] = useState(false);
  const [config, setConfig] = useState<Record<string, string>>({});
  const [status, setStatus] = useState<{ error?: string; saved?: boolean }>({});

  useEffect(() => {
    if (!projectId) return;
    api.integrations
      .get(projectId, section.kind)
      .then((row) => {
        setEnabled(row.enabled);
        setConfig(row.config);
        setStatus({});
      })
      .catch((err) => setStatus({ error: err instanceof ApiError ? err.message : "Unable to load this integration." }));
  }, [projectId, section.kind]);

  async function save() {
    try {
      const row = await api.integrations.save(projectId, section.kind, { enabled, config });
      setConfig(row.config);
      setStatus({ saved: true });
    } catch (err) {
      setStatus({ error: err instanceof ApiError ? err.message : "Couldn't save this integration." });
    }
  }

  return (
    <div className="form-card">
      <h3 className="form-card-title">{section.title}</h3>
      <p className="mb-3 text-xs text-ink-2">{section.blurb}</p>
      <div className="form-fields">
        {section.fields.map(([key, label, hint]) => (
          <label key={key}>
            <span>{label}</span>
            <Input
              value={config[key] ?? ""}
              placeholder={hint}
              disabled={!canManage}
              onChange={(e) => setConfig((c) => ({ ...c, [key]: e.target.value }))}
            />
          </label>
        ))}
      </div>
      <div className="form-checks mt-4 flex items-center justify-between gap-3">
        <Switch checked={enabled} onChange={setEnabled} label="Enabled" disabled={!canManage} />
        {canManage ? <Button size="sm" onClick={save}>Save</Button> : null}
      </div>
      {status.error ? <p className="upload-warning mt-3">{status.error}</p> : null}
      {status.saved ? <p className="mt-3 text-xs font-semibold text-emerald-700">Saved.</p> : null}
    </div>
  );
}

export function IntegrationsCard({ projectId, canManage }: { projectId: string; canManage: boolean }) {
  return (
    <>
      {SECTIONS.map((section) => (
        <IntegrationSection key={section.kind} projectId={projectId} canManage={canManage} section={section} />
      ))}
    </>
  );
}
