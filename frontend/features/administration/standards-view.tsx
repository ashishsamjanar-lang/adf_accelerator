"use client";

import { useState } from "react";
import { Download, Loader2, Save, ScanLine, ShieldCheck } from "lucide-react";
import { FeatureProps } from "@/features/types";
import { PageHeader } from "@/components/common/page-header";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { cn } from "@/lib/utils";
import { useProjectSettings } from "@/lib/api/hooks";
import { api } from "@/lib/api/client";
import { describeRule } from "@/lib/adf/naming";
import {
  MEDALLION_LAYERS,
  MEDALLION_LAYER_INFO,
  MAX_CUSTOM_METADATA_COLUMNS,
  resolveZonePrefix,
  standardAuditColumns,
  type MedallionLayer,
} from "@/lib/adf/medallion";
import { PairEditor } from "@/features/build/wizard-shared";
import { EMPTY_PROJECT_SETTINGS } from "@/lib/api/empty-values";
import {
  buildPolicyDocument,
  scanNamingCompliance,
  latestVersions,
  scanSecretCompliance,
  scanAdvisoryCompliance,
  scanOnboardingCompliance,
  scanConnectorCompliance,
  type AdvisoryScanResult,
  type ConnectorFinding,
  type OnboardingFinding,
  type ComplianceScanResult,
  type SecretScanResult,
} from "@/lib/adf/compliance";
import { downloadTextFile } from "@/lib/download";
import { CONNECTOR_GROUPS, CONNECTOR_SCHEMAS } from "@/lib/adf/connections";
import type { AuditColumnOverrides, NamingArtifactType, NamingRule } from "@/types";

type Level = "off" | "warn" | "block";

interface StaticRule {
  id: string;
  category: string;
  rule: string;
  detail: string;
  level: Level;
  alwaysOn?: boolean;
}

// Every row here except "naming.pattern" is display-only: no evaluator exists
// for these yet anywhere in backend/worker (§25.3's broader catalogue is
// target design — see AGENTS.md §25, IMPLEMENTATION_PLAN.md). Rendering them
// as live off/warn/block toggles that quietly did nothing was itself a gap;
// showing them as fixed badges is honest about what's actually enforced.
const STATIC_CATALOGUE: StaticRule[] = [
  { id: "tagging.required", category: "Tagging", rule: "Required tag keys on every pipeline", detail: "Configured under Required tags below — block rejects Push to Git, warn is reported by the scan.", level: "off" },
  { id: "structure.layout", category: "Structure", rule: "Canonical ADF folder layout", detail: "Each resource in its type folder (linkedService/ dataset/ pipeline/ trigger/ …) with a file name matching its resource name.", level: "off" },
  { id: "connection.connectors", category: "Connection", rule: "Allowed connector list", detail: "Pipelines' source/target connectors only from the allow-list below.", level: "off" },
  { id: "connection.auth", category: "Connection", rule: "Allowed auth types only", detail: "e.g. managed identity or service principal — no SQL auth.", level: "off" },
  { id: "security.kv", category: "Security", rule: "Secrets as Key Vault reference only", detail: "No inline passwords, connection strings or tokens in any artifact.", level: "block", alwaysOn: true },
  { id: "security.transport", category: "Security", rule: "Encrypted, authenticated linked-service transport", detail: "No plain HTTP, TLS/certificate validation off, Anonymous auth, or Encrypt=False connection strings.", level: "off" },
  { id: "security.pii", category: "Security", rule: "Data classification required on onboarded sources", detail: "Every enabled onboarded object must carry a data_classification (the registry has no separate PII flag).", level: "off" },
  { id: "param.literals", category: "Parameterization", rule: "Env-specific values must be parameters", detail: "Paths, servers and containers cannot be literals.", level: "off" },
  { id: "runtime.retry", category: "Runtime", rule: "Minimum retry / interval / timeout", detail: "Copy activities must set a retry policy and Azure Monitor logging.", level: "off" },
  { id: "git.base", category: "Git", rule: "Base branch must be dev", detail: "Pull requests target the configured collaboration branch only.", level: "block", alwaysOn: true },
  { id: "git.branch", category: "Git", rule: "Feature-branch name pattern", detail: "Push branches follow the project's Git feature-branch pattern — warned in the Push to Git dialog.", level: "off" },
  { id: "onboarding.fields", category: "Onboarding", rule: "Owner + target + target format required", detail: "Every enabled onboarded object must name an owner, a target object/path and a file format (no domain column exists yet).", level: "off" },
  { id: "onboarding.watermark", category: "Onboarding", rule: "DeltaLoad object needs a watermark column", detail: "Incremental objects must declare a watermark column and type.", level: "off" },
  { id: "audit.columns", category: "Audit columns", rule: "Standard audit columns per medallion layer", detail: "e.g. _source_system, _pipeline_run_id, _ingestion_ts_utc on Bronze/Silver/Gold targets (gap #32).", level: "off" },
  { id: "docs.description", category: "Documentation", rule: "Description required on pipeline / LS / dataset", detail: "Minimum length configurable.", level: "off" },
];

const LEVELS: Level[] = ["off", "warn", "block"];

/** "data-domain, pii ,source-system" -> ["data-domain", "pii", "source-system"], de-duplicated. */
function splitTagKeys(text: string): string[] {
  return [...new Set(text.split(",").map((k) => k.trim()).filter(Boolean))];
}

// Catalogue rows the compliance scan reports on (advisory only — `scanAdvisoryCompliance`).
const ADVISORY_RULE_IDS = new Set([
  "security.transport", "runtime.retry", "docs.description", "tagging.required", "connection.auth", "param.literals",
  "onboarding.fields", "onboarding.watermark", "structure.layout", "connection.connectors", "security.pii", "git.branch",
  "audit.columns",
]);

const NAMING_RULE_TYPES: { id: NamingArtifactType; label: string }[] = [
  { id: "pipeline", label: "Pipeline" },
  { id: "dataset", label: "Dataset" },
  { id: "linkedService", label: "Linked service" },
  { id: "trigger", label: "Trigger" },
];
const NAMING_CASES: { value: NonNullable<NamingRule["case"]>; label: string }[] = [
  { value: "none", label: "No case rule" },
  { value: "snake", label: "snake_case" },
  { value: "pascal", label: "PascalCase" },
  { value: "camel", label: "camelCase" },
  { value: "upper", label: "UPPER_CASE" },
  { value: "lower", label: "lowercase" },
];

interface StandardsViewProps extends FeatureProps {
  projectId?: string;
}

export function StandardsView({ data, user, projectId }: StandardsViewProps) {
  const canEdit = user.role === "PLATFORM_ADMIN" || user.role === "TEAM_ADMIN";
  const project = data.projects.data.find((p) => p.id === projectId) ?? data.projects.data[0];

  // Real GET/PUT /api/v1/projects/{id}/settings — this page reads and writes
  // the *same* `namingConvention` document as Project Settings > Naming
  // conventions; only the `namingConvention` field is ever touched here, the
  // rest of the fetched document is round-tripped untouched on save.
  const settingsResource = useProjectSettings(project?.id, EMPTY_PROJECT_SETTINGS);
  const convention = settingsResource.data.namingConvention;

  const [enforcement, setEnforcement] = useState<Level>(convention?.enforcement ?? "off");
  const [rules, setRules] = useState<Partial<Record<NamingArtifactType, NamingRule>>>(convention?.rules ?? {});
  const [layerPrefixes, setLayerPrefixes] = useState<Partial<Record<MedallionLayer, string>>>(convention?.layerPrefixes ?? {});
  const [auditColumns, setAuditColumns] = useState<AuditColumnOverrides>(settingsResource.data.auditColumns ?? {});
  const [allowedConnectors, setAllowedConnectors] = useState<string[]>(settingsResource.data.allowedConnectors ?? []);
  // gaps.md GOV-05/WD-06 — required tag keys, edited as one comma-separated field.
  const [requiredTagsLevel, setRequiredTagsLevel] = useState<Level>(settingsResource.data.requiredTags?.enforcement ?? "off");
  const [requiredTagKeys, setRequiredTagKeys] = useState((settingsResource.data.requiredTags?.keys ?? []).join(", "));
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string>();
  const [saveSuccess, setSaveSuccess] = useState(false);
  const [scan, setScan] = useState<ComplianceScanResult>();
  const [secretScan, setSecretScan] = useState<SecretScanResult>();
  const [advisoryScan, setAdvisoryScan] = useState<AdvisoryScanResult>();
  // `failed` = the onboarding registry couldn't be fetched — reported, never shown as clean.
  const [connectorScan, setConnectorScan] = useState<{ configured: boolean; scanned: number; findings: ConnectorFinding[] }>();
  const [onboardingScan, setOnboardingScan] = useState<{ scanned: number; findings: OnboardingFinding[]; failed?: boolean }>();

  // Re-seed local edit state whenever a fresh settings document loads
  // (initial fetch, or after this page's own save reloads it) — but only
  // while the admin hasn't started editing, so an in-flight edit is never
  // silently overwritten by a background refresh.
  const [lastLoadedAt, setLastLoadedAt] = useState(settingsResource.data);
  if (settingsResource.data !== lastLoadedAt && !dirty) {
    setLastLoadedAt(settingsResource.data);
    setEnforcement(settingsResource.data.namingConvention?.enforcement ?? "off");
    setRules(settingsResource.data.namingConvention?.rules ?? {});
    setLayerPrefixes(settingsResource.data.namingConvention?.layerPrefixes ?? {});
    setAuditColumns(settingsResource.data.auditColumns ?? {});
    setAllowedConnectors(settingsResource.data.allowedConnectors ?? []);
    setRequiredTagsLevel(settingsResource.data.requiredTags?.enforcement ?? "off");
    setRequiredTagKeys((settingsResource.data.requiredTags?.keys ?? []).join(", "));
  }

  function toggleConnector(key: string, on: boolean) {
    setAllowedConnectors((prev) => (on ? [...prev, key].sort() : prev.filter((k) => k !== key)));
    setDirty(true);
  }

  function updateAuditColumns(layer: MedallionLayer, rows: Array<{ name: string; value: string }>) {
    setAuditColumns((prev) => ({ ...prev, [layer]: rows }));
    setDirty(true);
  }

  function updateRule(type: NamingArtifactType, patch: Partial<NamingRule>) {
    setRules((prev) => ({ ...prev, [type]: { ...prev[type], ...patch } }));
    setDirty(true);
  }

  function updateLayerPrefix(layer: MedallionLayer, value: string) {
    setLayerPrefixes((prev) => ({ ...prev, [layer]: value }));
    setDirty(true);
  }

  function setLevel(level: Level) {
    setEnforcement(level);
    setDirty(true);
  }

  async function handleSave() {
    if (!project) return;
    setSaving(true);
    setSaveError(undefined);
    setSaveSuccess(false);
    try {
      const document = {
        ...settingsResource.data,
        projectId: project.id,
        namingConvention: {
          ...settingsResource.data.namingConvention,
          pipelinePattern: settingsResource.data.namingConvention?.pipelinePattern ?? "{project}_{source}_{target}_pl",
          datasetPattern: settingsResource.data.namingConvention?.datasetPattern ?? "{project}_{source}_{object}_ds",
          linkedServicePattern: settingsResource.data.namingConvention?.linkedServicePattern ?? "{connector}_{environment}_ls",
          parameterPattern: settingsResource.data.namingConvention?.parameterPattern ?? "{pipeline}_{name}_param",
          triggerPattern: settingsResource.data.namingConvention?.triggerPattern ?? "{pipeline}_{schedule}_tr",
          enforcement,
          rules,
          layerPrefixes,
        },
        auditColumns,
        allowedConnectors,
        requiredTags: { enforcement: requiredTagsLevel, keys: splitTagKeys(requiredTagKeys) },
      };
      const ok = await settingsResource.save(document);
      if (!ok) {
        setSaveError(settingsResource.saveError ?? "Failed to save the naming standard.");
        return;
      }
      setDirty(false);
      setSaveSuccess(true);
      setTimeout(() => setSaveSuccess(false), 3000);
    } finally {
      setSaving(false);
    }
  }

  // Scans against the *saved* standard plus any unsaved edits on this page, so
  // an admin can preview what a stricter rule would flag before saving it.
  function currentConvention() {
    return { ...settingsResource.data.namingConvention, enforcement, rules, layerPrefixes } as typeof convention;
  }

  async function handleScan() {
    if (!project) return;
    const pipelineIds = new Set(data.pipelines.data.filter((p) => p.projectId === project.id).map((p) => p.id));
    setScan(scanNamingCompliance(data.artifacts.data, pipelineIds, currentConvention()));
    // The artifact list omits `content`; fetch it for the latest versions only. A failed
    // fetch leaves that artifact content-less, so it's reported as skipped, never as clean.
    const latest = latestVersions(data.artifacts.data, pipelineIds);
    const detailed = await Promise.all(
      latest.map((a) => (typeof a.content === "string" ? Promise.resolve(a) : api.artifacts.details(a.id).catch(() => a))),
    );
    setSecretScan(scanSecretCompliance(detailed, pipelineIds));
    setAdvisoryScan(scanAdvisoryCompliance(detailed, pipelineIds, { auditColumns, requiredTagKeys: splitTagKeys(requiredTagKeys) }));
    setConnectorScan(scanConnectorCompliance(data.pipelines.data.filter((p) => pipelineIds.has(p.id)), allowedConnectors));
    const objects = await api.onboarding.list(project.id).catch(() => undefined);
    setOnboardingScan(objects ? scanOnboardingCompliance(objects) : { scanned: 0, findings: [], failed: true });
  }

  function handleExport() {
    if (!project) return;
    downloadTextFile("policy.json", buildPolicyDocument(project, currentConvention(), STATIC_CATALOGUE), "application/json;charset=utf-8");
  }

  const summary = STATIC_CATALOGUE.reduce(
    (acc, rule) => ({ ...acc, [rule.level]: acc[rule.level] + 1 }),
    { off: 0, warn: 0, block: 0 } as Record<Level, number>,
  );
  summary[enforcement] += 1;

  const byCategory = new Map<string, StaticRule[]>();
  for (const rule of STATIC_CATALOGUE) {
    const list = byCategory.get(rule.category) ?? [];
    list.push(rule);
    byCategory.set(rule.category, list);
  }

  return (
    <div className="animate-fade-in">
      <PageHeader
        eyebrow="ADMINISTRATION"
        title="Standards"
        detail={`Enterprise policy for ${project?.name ?? "this project"} — naming standards are enforced end to end (Build form, validate, and a Block gate on Push to Git); the rest of the catalogue below is shown for transparency but not yet evaluated anywhere. Nothing is enforced until you opt in.`}
        action={
          <div className="flex items-center gap-2">
            <Button variant="outline" disabled={!project} onClick={() => void handleScan()} title="Re-check this project's latest generated artifacts against the naming standard, the always-on no-inline-secrets rule, the advisory artifact checks, and the onboarding registry checks (§25.6).">
              <ScanLine className="h-3.5 w-3.5" />Run compliance scan
            </Button>
            <Button variant="outline" disabled={!project} onClick={handleExport} title="Download this project's standards as standards/policy.json (§25.2).">
              <Download className="h-3.5 w-3.5" />Export policy.json
            </Button>
          </div>
        }
      />

      {saveSuccess && (
        <Alert variant="success" title="Naming standard saved" description="Enforcement is live for new Build-form input, validate, and Push to Git." className="mb-4" />
      )}
      {saveError && <Alert variant="error" title="Save failed" description={saveError} className="mb-4" />}

      {scan && (
        <section className="panel mb-4" aria-label="Compliance scan results">
          <div className="panel-title">
            <h2>Compliance scan</h2>
            <Button variant="ghost" size="sm" onClick={() => { setScan(undefined); setSecretScan(undefined); setAdvisoryScan(undefined); }}>Dismiss</Button>
          </div>
          {enforcement === "off" ? (
            <p className="text-sm text-muted">Naming enforcement is off — set it to Warn or Block to scan {scan.scanned} artifact name(s).</p>
          ) : scan.findings.length === 0 ? (
            <p className="text-sm text-muted">{scan.scanned} artifact name(s) checked — all compliant with the naming standard.</p>
          ) : (
            <div className="table-wrap">
              <p className="text-sm text-muted mb-2">{scan.findings.length} of {scan.scanned} artifact name(s) violate the naming standard. Regenerate the pipeline after renaming to clear them.</p>
              <table>
                <thead>
                  <tr><th>Pipeline</th><th>Artifact</th><th>Level</th><th>Problem</th></tr>
                </thead>
                <tbody>
                  {scan.findings.map((f) => (
                    <tr key={f.artifactId}>
                      <td>{f.pipelineName}</td>
                      <td className="font-mono text-xs">{f.fileName}</td>
                      <td><Badge variant={f.level === "block" ? "red" : "amber"}>{f.level}</Badge></td>
                      <td className="text-xs">{f.message}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {secretScan && (
            <div className="mt-4" aria-label="Inline secret scan">
              <h3 className="text-sm font-semibold mb-1">No inline secrets <Badge variant="red">block · always on</Badge></h3>
              {secretScan.findings.length === 0 ? (
                <p className="text-sm text-muted">
                  {secretScan.scanned} artifact(s) checked — no inline secret found.
                  {secretScan.skipped ? ` ${secretScan.skipped} artifact(s) had no stored content to scan.` : ""}
                </p>
              ) : (
                <div className="table-wrap">
                  <p className="text-sm text-muted mb-2">{secretScan.findings.length} of {secretScan.scanned} artifact(s) contain a possible inline secret — replace it with a Key Vault reference and regenerate.</p>
                  <table>
                    <thead>
                      <tr><th>Pipeline</th><th>Artifact</th><th>Pattern</th></tr>
                    </thead>
                    <tbody>
                      {secretScan.findings.map((f) => (
                        <tr key={f.artifactId}>
                          <td>{f.pipelineName}</td>
                          <td className="font-mono text-xs">{f.fileName}</td>
                          <td className="text-xs">{f.patterns.join(", ")}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}
          {advisoryScan && (
            <div className="mt-4" aria-label="Advisory checks">
              <h3 className="text-sm font-semibold mb-1">Advisory checks <Badge variant="gray">scan only · not enforced</Badge></h3>
              {advisoryScan.findings.length === 0 ? (
                <p className="text-sm text-muted">
                  {advisoryScan.scanned} artifact(s) checked for folder layout, transport security, auth type, parameterized env values, audit columns, Copy retry policy, annotations and descriptions — nothing to report.
                  {advisoryScan.skipped ? ` ${advisoryScan.skipped} artifact(s) had no readable content to scan.` : ""}
                </p>
              ) : (
                <div className="table-wrap">
                  <p className="text-sm text-muted mb-2">{advisoryScan.findings.length} finding(s) across {advisoryScan.scanned} artifact(s). These rules aren't enforced yet — review and fix in the source connection or pipeline settings.</p>
                  <table>
                    <thead>
                      <tr><th>Pipeline</th><th>Artifact</th><th>Rule</th><th>Finding</th></tr>
                    </thead>
                    <tbody>
                      {advisoryScan.findings.map((f, i) => (
                        <tr key={`${f.artifactId}-${i}`}>
                          <td>{f.pipelineName}</td>
                          <td className="font-mono text-xs">{f.fileName}</td>
                          <td className="font-mono text-xs">{f.ruleId}</td>
                          <td className="text-xs">{f.message}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}
          {connectorScan && (
            <div className="mt-4" aria-label="Connector allow-list">
              <h3 className="text-sm font-semibold mb-1">Connector allow-list <Badge variant="gray">scan only · not enforced</Badge></h3>
              {!connectorScan.configured ? (
                <p className="text-sm text-muted">No allow-list configured — pick connectors under &quot;Allowed connectors&quot; below to check against.</p>
              ) : connectorScan.findings.length === 0 ? (
                <p className="text-sm text-muted">{connectorScan.scanned} pipeline(s) checked — every source/target connector is allowed.</p>
              ) : (
                <ul className="text-sm">
                  {connectorScan.findings.map((f, i) => (
                    <li key={`${f.pipelineName}-${i}`}><strong>{f.pipelineName}</strong>: {f.message}</li>
                  ))}
                </ul>
              )}
            </div>
          )}
          {onboardingScan && (
            <div className="mt-4" aria-label="Onboarding checks">
              <h3 className="text-sm font-semibold mb-1">Onboarding registry checks <Badge variant="gray">scan only · not enforced</Badge></h3>
              {onboardingScan.failed ? (
                <p className="text-sm text-muted">Couldn&apos;t load this project&apos;s onboarding registry — nothing was checked.</p>
              ) : onboardingScan.findings.length === 0 ? (
                <p className="text-sm text-muted">{onboardingScan.scanned} enabled object(s) checked for owner, target, data classification and DeltaLoad watermark — nothing to report.</p>
              ) : (
                <div className="table-wrap">
                  <p className="text-sm text-muted mb-2">{onboardingScan.findings.length} finding(s) across {onboardingScan.scanned} enabled object(s). Fix them on the Onboarding page.</p>
                  <table>
                    <thead>
                      <tr><th>Object</th><th>Rule</th><th>Finding</th></tr>
                    </thead>
                    <tbody>
                      {onboardingScan.findings.map((f, i) => (
                        <tr key={`${f.objectKey}-${i}`}>
                          <td className="font-mono text-xs">{f.objectKey}</td>
                          <td className="font-mono text-xs">{f.ruleId}</td>
                          <td className="text-xs">{f.message}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}
        </section>
      )}

      <div className="metric-grid" style={{ gridTemplateColumns: "repeat(3, 1fr)" }}>
        {LEVELS.map((level) => (
          <article className="metric-card" key={level}>
            <span className="capitalize">{level === "off" ? "Not enforced" : level === "warn" ? "Warn" : "Block"}</span>
            <strong>{summary[level]}</strong>
            <small className="metric-note">{level === "block" ? "gates the push" : level === "warn" ? "advisory, acknowledged on push" : "rule is inactive"}</small>
          </article>
        ))}
      </div>

      <section className="panel mb-4">
        <div className="panel-title">
          <h2>Naming</h2>
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Rule</th>
                <th style={{ width: 220 }}>Enforcement</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>
                  <strong className="block text-ink">Name pattern per artifact type</strong>
                  <span className="text-xs text-muted">Prefix / suffix / case / max length, checked live and gating Push to Git when set to Block.</span>
                </td>
                <td>
                  <div className="seg seg-sm" role="group" aria-label="Naming pattern enforcement">
                    {LEVELS.map((level) => (
                      <button
                        key={level}
                        className={cn("seg-btn", enforcement === level && "active")}
                        disabled={!canEdit}
                        onClick={() => setLevel(level)}
                      >
                        {level}
                      </button>
                    ))}
                  </div>
                </td>
              </tr>
              <tr>
                <td colSpan={2}>
                  <div className="table-wrap">
                    <table>
                      <thead>
                        <tr>
                          <th>Artifact type</th>
                          <th>Prefix</th>
                          <th>Suffix</th>
                          <th>Case</th>
                          <th>Max length</th>
                        </tr>
                      </thead>
                      <tbody>
                        {NAMING_RULE_TYPES.map(({ id, label }) => {
                          const rule = rules[id] ?? {};
                          return (
                            <tr key={id}>
                              <td>
                                <strong className="block text-ink">{label}</strong>
                                <span className="text-xs text-muted">{describeRule(rule)}</span>
                              </td>
                              <td>
                                <Input value={rule.prefix ?? ""} onChange={(e) => updateRule(id, { prefix: e.target.value })} disabled={!canEdit} placeholder="e.g. PL_" />
                              </td>
                              <td>
                                <Input value={rule.suffix ?? ""} onChange={(e) => updateRule(id, { suffix: e.target.value })} disabled={!canEdit} placeholder="e.g. _v1" />
                              </td>
                              <td>
                                <Select value={rule.case ?? "none"} onChange={(e) => updateRule(id, { case: e.target.value as NamingRule["case"] })} disabled={!canEdit}>
                                  {NAMING_CASES.map((c) => (
                                    <option key={c.value} value={c.value}>{c.label}</option>
                                  ))}
                                </Select>
                              </td>
                              <td>
                                <Input
                                  type="number"
                                  min={1}
                                  value={rule.maxLength ?? ""}
                                  onChange={(e) => updateRule(id, { maxLength: e.target.value ? Number(e.target.value) : undefined })}
                                  disabled={!canEdit}
                                  placeholder="none"
                                />
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        {canEdit && (
          <div className="flex justify-end p-3">
            <Button onClick={handleSave} disabled={saving || !dirty}>
              {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
              {saving ? "Saving..." : "Save naming standard"}
            </Button>
          </div>
        )}
      </section>

      <section className="panel mb-4">
        <div className="panel-title">
          <h2>Layer prefixes</h2>
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Medallion layer</th>
                <th style={{ width: 200 }}>Custom zone prefix</th>
                <th>Target table it produces</th>
              </tr>
            </thead>
            <tbody>
              {MEDALLION_LAYERS.map((layer) => {
                const info = MEDALLION_LAYER_INFO[layer];
                const resolved = resolveZonePrefix(layer, layerPrefixes);
                return (
                  <tr key={layer}>
                    <td>
                      <strong className="block text-ink capitalize">{layer}</strong>
                      <span className="text-xs text-muted">{info.description}</span>
                    </td>
                    <td>
                      <Input
                        value={layerPrefixes[layer] ?? ""}
                        onChange={(e) => updateLayerPrefix(layer, e.target.value)}
                        disabled={!canEdit}
                        placeholder={info.zonePrefix}
                      />
                    </td>
                    <td>
                      <span className="text-xs text-muted">{info.targetPatternHint.replace(/^\w+/, resolved)}</span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <p className="px-3 pb-2 text-xs text-muted">
          Bronze/Silver/Gold target tables are always <em>computed</em>, never freely typed — this is
          the standard that computation follows. Leave a layer blank to keep its default
          (raw/curated/mart); set a custom prefix and every newly generated pipeline and STTM
          registry object for that layer is built with it instead. There's no separate warn/block
          step: unlike the Naming rules above, a custom prefix is compiled straight into the
          generated name.
        </p>
        {canEdit && (
          <div className="flex justify-end p-3">
            <Button onClick={handleSave} disabled={saving || !dirty}>
              {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
              {saving ? "Saving..." : "Save layer prefixes"}
            </Button>
          </div>
        )}
      </section>

      <section className="panel mb-4" aria-label="Standard audit columns">
        <div className="panel-title">
          <h2>Standard audit columns</h2>
        </div>
        <p className="px-3 pt-2 text-xs text-muted">
          The columns Build&apos;s &quot;Apply standard audit columns&quot; checkbox stamps on a pipeline of each
          layer (as Copy <code>additionalColumns</code>). Leave a layer empty to keep the built-in list shown
          as its placeholder. Values are ADF expressions or literals — use system expressions such as{" "}
          <code>@pipeline().RunId</code> rather than fixed business values.
        </p>
        <div className="grid gap-3 p-3 md:grid-cols-3">
          {MEDALLION_LAYERS.map((layer) => (
            <div key={layer}>
              <PairEditor
                title={`${layer[0].toUpperCase()}${layer.slice(1)}`}
                hint={`Built-in: ${standardAuditColumns(layer).map((c) => c.name).join(", ")}`}
                rows={auditColumns[layer] ?? []}
                onChange={(rows) => canEdit && updateAuditColumns(layer, rows)}
                max={MAX_CUSTOM_METADATA_COLUMNS}
                namePlaceholder="e.g. _batch_id"
                valuePlaceholder="e.g. @pipeline().RunId"
              />
            </div>
          ))}
        </div>
        {canEdit && (
          <div className="flex justify-end p-3">
            <Button onClick={handleSave} disabled={saving || !dirty}>
              {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
              {saving ? "Saving..." : "Save audit columns"}
            </Button>
          </div>
        )}
      </section>

      <section className="panel mb-4" aria-label="Allowed connectors">
        <div className="panel-title">
          <h2>Allowed connectors</h2>
          <Badge variant="gray">{allowedConnectors.length ? `${allowedConnectors.length} allowed` : "no allow-list"}</Badge>
        </div>
        <p className="px-3 pt-2 text-xs text-muted">
          The connection.connectors rule: the compliance scan reports any pipeline whose source or target connector
          isn&apos;t ticked here. Leave everything unticked to skip the check.
        </p>
        <div className="grid gap-3 p-3 md:grid-cols-3">
          {CONNECTOR_GROUPS.map((group) => (
            <fieldset key={group.category} className="text-xs">
              <legend className="font-semibold capitalize mb-1">{group.category}</legend>
              {group.connectors.map((key) => (
                <label key={key} className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    checked={allowedConnectors.includes(key)}
                    onChange={(e) => canEdit && toggleConnector(key, e.target.checked)}
                    disabled={!canEdit}
                  />
                  {CONNECTOR_SCHEMAS[key]?.displayName ?? key}
                </label>
              ))}
            </fieldset>
          ))}
        </div>
        {canEdit && (
          <div className="flex justify-end p-3">
            <Button onClick={handleSave} disabled={saving || !dirty}>
              {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
              {saving ? "Saving..." : "Save allowed connectors"}
            </Button>
          </div>
        )}
      </section>

      <section className="panel mb-4" aria-label="Required tags">
        <div className="panel-title">
          <h2>Required tags</h2>
          <Badge variant="gray">{requiredTagsLevel}</Badge>
        </div>
        <p className="px-3 pt-2 text-xs text-muted">
          The tagging.required rule: every pipeline must carry each key as an annotation (<code>pii</code> or{" "}
          <code>data-domain:hr</code>). Block rejects Push to Git; warn only reports it in the compliance scan.
        </p>
        <div className="grid gap-3 p-3 md:grid-cols-[1fr_10rem]">
          <Input
            aria-label="Required tag keys"
            value={requiredTagKeys}
            placeholder="e.g. data-domain, pii, source-system"
            disabled={!canEdit}
            onChange={(e) => {
              setRequiredTagKeys(e.target.value);
              setDirty(true);
            }}
          />
          <Select
            aria-label="Required tags enforcement"
            value={requiredTagsLevel}
            disabled={!canEdit}
            onChange={(e) => {
              setRequiredTagsLevel(e.target.value as Level);
              setDirty(true);
            }}
          >
            {LEVELS.map((l) => (
              <option key={l} value={l}>
                {l}
              </option>
            ))}
          </Select>
        </div>
        {canEdit && (
          <div className="flex justify-end p-3">
            <Button onClick={handleSave} disabled={saving || !dirty}>
              {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
              {saving ? "Saving..." : "Save required tags"}
            </Button>
          </div>
        )}
      </section>

      {[...byCategory.entries()].map(([category, list]) => (
        <section className="panel mb-4" key={category}>
          <div className="panel-title">
            <h2>{category}</h2>
            {list.every((rule) => !rule.alwaysOn && !ADVISORY_RULE_IDS.has(rule.id)) && <Badge variant="gray">Coming soon</Badge>}
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Rule</th>
                  <th style={{ width: 220 }}>Enforcement</th>
                </tr>
              </thead>
              <tbody>
                {list.map((rule) => (
                  <tr key={rule.id}>
                    <td>
                      <strong className="block text-ink">{rule.rule}</strong>
                      <span className="text-xs text-muted">{rule.detail}</span>
                    </td>
                    <td>
                      <span className={cn("status-pill", rule.level === "block" ? "red" : rule.level === "warn" ? "amber" : "gray")}>
                        <i />
                        {rule.alwaysOn ? "Always on" : rule.level}
                      </span>
                      {!rule.alwaysOn && ADVISORY_RULE_IDS.has(rule.id) && (
                        <span className="ml-2 text-xs text-muted" title="Reported by Run compliance scan; not enforced at validate or push yet.">
                          Scan only
                        </span>
                      )}
                      {!rule.alwaysOn && !ADVISORY_RULE_IDS.has(rule.id) && (
                        <span className="ml-2 text-xs text-muted" title="No evaluator exists for this rule yet.">
                          Coming soon
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ))}

      <p className="text-xs text-muted">
        <ShieldCheck className="mr-1.5 inline h-3.5 w-3.5 align-text-bottom" />
        Always-on safety rules (no inline secrets, feature-branch-only push, valid ADF names, no dangling
        references, parseable JSON/SQL) apply regardless of this policy.
      </p>
    </div>
  );
}
