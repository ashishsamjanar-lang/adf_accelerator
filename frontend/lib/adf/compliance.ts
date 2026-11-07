/**
 * §25.6 compliance scan + §25.2 policy export — the Standards page's two
 * header actions. Both are pure, client-side functions: the scan re-checks a
 * project's already-generated artifacts against the *current* naming standard
 * (the only §25.3 category with a live evaluator — see `naming.ts`), and the
 * export serializes the project's standards document as `standards/policy.json`.
 * Neither writes anything; enforcement itself stays in Build/validate/push.
 */
import type { ArtifactType, ArtifactVersion, AuditColumnOverrides, NamingArtifactType, NamingConvention, OnboardingObjectEntry, PipelineDefinition } from "@/types";
import { standardAuditColumns, type MedallionLayer } from "./medallion";
import { evaluateName, type NamingViolation } from "./naming";

const NAMING_TYPE_BY_ARTIFACT: Partial<Record<ArtifactType, NamingArtifactType>> = {
  pipeline: "pipeline",
  dataset: "dataset",
  linked_service: "linkedService",
  trigger: "trigger",
};

export interface ComplianceFinding extends NamingViolation {
  artifactId: string;
  pipelineName: string;
  fileName: string;
  artifactName: string;
  namingType: NamingArtifactType;
}

export interface ComplianceScanResult {
  scanned: number;
  findings: ComplianceFinding[];
}

/** ADF resource name = the artifact file's basename without its extension. */
export function artifactResourceName(fileName: string): string {
  const base = fileName.split("/").pop() ?? fileName;
  return base.replace(/\.[^.]+$/, "");
}

/** The newest `version` of each in-scope pipeline's artifacts — older immutable versions are history. */
export function latestVersions(artifacts: ArtifactVersion[], pipelineIds: Set<string>): ArtifactVersion[] {
  const inScope = artifacts.filter((a) => pipelineIds.has(a.pipelineId));
  const latest = new Map<string, string>();
  for (const a of inScope) {
    const current = latest.get(a.pipelineId);
    if (!current || a.version.localeCompare(current, undefined, { numeric: true }) > 0) latest.set(a.pipelineId, a.version);
  }
  return inScope.filter((a) => a.version === latest.get(a.pipelineId));
}

/**
 * Scan every nameable artifact (pipeline/dataset/linked service/trigger) of the
 * given pipelines' latest generated versions. Only the newest `version` per
 * pipeline is checked — older immutable versions are history, not live output.
 */
export function scanNamingCompliance(
  artifacts: ArtifactVersion[],
  pipelineIds: Set<string>,
  convention: NamingConvention | undefined,
): ComplianceScanResult {
  const findings: ComplianceFinding[] = [];
  let scanned = 0;
  for (const a of latestVersions(artifacts, pipelineIds)) {
    const namingType = NAMING_TYPE_BY_ARTIFACT[a.artifactType];
    if (!namingType) continue;
    scanned += 1;
    const artifactName = artifactResourceName(a.fileName);
    const violation = evaluateName(artifactName, namingType, convention);
    if (violation) {
      findings.push({ ...violation, artifactId: a.id, pipelineName: a.pipelineName, fileName: a.fileName, artifactName, namingType });
    }
  }
  return { scanned, findings };
}

/**
 * §25.5 always-on `security.kv` rule — no inline secret in any artifact. A port of
 * `worker/app/validator/rules.py`'s SECRET_PATTERNS / `_ALLOWED_SECRET_SHAPES`
 * (keep in parity); the worker's validate step stays the enforcing copy, this
 * only re-scans already-stored content for the Standards page.
 */
const SECRET_PATTERNS: [string, RegExp][] = [
  ["AWS access key", /AKIA[0-9A-Z]{16}/g],
  ["PEM private key", /-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----/g],
  ["Bearer token literal", /"(?:Authorization|authorization)"\s*:\s*"Bearer\s+[A-Za-z0-9._\-]{16,}"/g],
  ["Password in connection string", /(?:password|pwd)\s*=\s*(?!@|\{\{|SecretRef:)[^;"'\s]{3,}/gi],
  ["AccountKey literal", /AccountKey=(?!@|\{\{)[A-Za-z0-9+/=]{20,}/gi],
  ["Shared access signature", /[?&]sig=[A-Za-z0-9%]{20,}/g],
  ["GitHub token", /gh[pousr]_[A-Za-z0-9]{20,}/g],
  ["Slack token", /xox[baprs]-[A-Za-z0-9-]{10,}/g],
  ["Generic api_key literal", /"(?:api_?key|client_?secret|access_?token)"\s*:\s*"[A-Za-z0-9._\-]{16,}"/gi],
];
const ALLOWED_SECRET_SHAPES = /AzureKeyVaultSecret|@\{linkedService\(\)|SecretRef:|@pipeline\(\)|@\{dataset\(\)/;

/** Names of the secret patterns found in `content` (at most one hit per pattern). */
export function findInlineSecrets(content: string): string[] {
  const hits: string[] = [];
  for (const [name, pattern] of SECRET_PATTERNS) {
    for (const match of content.matchAll(pattern)) {
      const start = match.index ?? 0;
      const window = content.slice(Math.max(0, start - 40), start + match[0].length + 40);
      if (ALLOWED_SECRET_SHAPES.test(window)) continue;
      hits.push(name);
      break;
    }
  }
  return hits;
}

export interface SecretFinding {
  artifactId: string;
  pipelineName: string;
  fileName: string;
  patterns: string[];
}

export interface SecretScanResult {
  scanned: number;
  /** Latest-version artifacts with no stored `content` to scan (list responses may omit it). */
  skipped: number;
  findings: SecretFinding[];
}

/** Scan the latest version of every artifact of the given pipelines for inline secrets. */
export function scanSecretCompliance(artifacts: ArtifactVersion[], pipelineIds: Set<string>): SecretScanResult {
  const inScope = latestVersions(artifacts, pipelineIds);
  const findings: SecretFinding[] = [];
  let scanned = 0;
  let skipped = 0;
  for (const a of inScope) {
    if (typeof a.content !== "string") {
      skipped += 1;
      continue;
    }
    scanned += 1;
    const patterns = findInlineSecrets(a.content);
    if (patterns.length) findings.push({ artifactId: a.id, pipelineName: a.pipelineName, fileName: a.fileName, patterns });
  }
  return { scanned, skipped, findings };
}

export interface PolicyCatalogueEntry {
  id: string;
  category: string;
  rule: string;
  level: "off" | "warn" | "block";
  alwaysOn?: boolean;
}

/**
 * `standards/policy.json` (§25.2): the project's naming standard (live) plus
 * the rest of the §25.3 catalogue, each marked `evaluated: false` so a reader
 * of the export can't mistake a display-only row for an enforced one.
 */
export function buildPolicyDocument(
  project: { id: string; name: string },
  convention: NamingConvention | undefined,
  catalogue: PolicyCatalogueEntry[],
): string {
  const doc = {
    $schema: "adf-accelerator/standards-policy/v1",
    project: { id: project.id, name: project.name },
    naming: {
      id: "naming.pattern",
      evaluated: true,
      enforcement: convention?.enforcement ?? "off",
      rules: convention?.rules ?? {},
      layerPrefixes: convention?.layerPrefixes ?? {},
    },
    rules: catalogue.map((r) => ({
      id: r.id,
      category: r.category,
      rule: r.rule,
      level: r.level,
      alwaysOn: !!r.alwaysOn,
      evaluated: !!r.alwaysOn,
    })),
  };
  return `${JSON.stringify(doc, null, 2)}\n`;
}

/**
 * Advisory §25.3 checks over stored artifact content — reported on the Standards
 * page only, never enforced (their catalogue rows stay "off"):
 * - `security.transport`: port of the worker's `_insecure_settings` (keep in parity).
 * - `runtime.retry`: a Copy activity without `policy.retry >= 1`.
 * - `docs.description`: a pipeline / dataset / linked service without a description.
 * - `tagging.required`: a pipeline without any `annotations`.
 * - `connection.auth`: a linked service on credential-based auth (SQL/Basic/Windows/AccessKey)
 *   instead of managed identity / service principal.
 * - `param.literals`: an environment-specific linked-service/dataset value (server, host, URL,
 *   account, container, path) written as a literal instead of an expression/parameter.
 * - `audit.columns`: a pipeline Copy activity missing its layer's standard audit columns
 *   (`additionalColumns`; layer read off the generated `metadata-driven/<layer>` folder).
 * - `structure.layout` (scan only, needs the file name — see `layoutChecks`).
 */
export type AdvisoryRuleId =
  | "security.transport" | "runtime.retry" | "docs.description" | "tagging.required" | "connection.auth" | "param.literals"
  | "audit.columns" | "structure.layout";

export interface AdvisoryFinding {
  artifactId: string;
  pipelineName: string;
  fileName: string;
  ruleId: AdvisoryRuleId;
  message: string;
}

export interface AdvisoryScanResult {
  scanned: number;
  skipped: number;
  findings: AdvisoryFinding[];
}

const INSECURE_CONN_FLAGS: [RegExp, string][] = [
  [/encrypt\s*=\s*(false|no|optional)/i, "disables transport encryption (Encrypt=False)"],
  [/trustservercertificate\s*=\s*(true|yes)/i, "skips server certificate validation (TrustServerCertificate=True)"],
];
const URL_KEYS = new Set(["url", "baseUrl", "endpoint", "serviceUri", "restServiceUrl"]);
const TLS_OFF_KEYS = new Set(["enableSsl", "useEncryptedEndpoints", "enableServerCertificateValidation", "validateServerCertificate"]);

/** Transport/auth settings in a linked service body that weaken security. */
export function findInsecureSettings(node: unknown, found: string[] = []): string[] {
  if (Array.isArray(node)) {
    for (const item of node) findInsecureSettings(item, found);
  } else if (node && typeof node === "object") {
    for (const [key, value] of Object.entries(node)) {
      if (URL_KEYS.has(key) && typeof value === "string" && value.toLowerCase().startsWith("http://")) found.push(`${key} uses plain HTTP`);
      else if (TLS_OFF_KEYS.has(key) && value === false) found.push(`${key} is false`);
      else if (key === "authenticationType" && value === "Anonymous") found.push("authenticationType is Anonymous");
      else if (key === "connectionString" && typeof value === "string") {
        for (const [rx, why] of INSECURE_CONN_FLAGS) if (rx.test(value)) found.push(`connection string ${why}`);
      } else findInsecureSettings(value, found);
    }
  }
  return found;
}

function copyActivitiesWithoutRetry(node: unknown, found: string[] = []): string[] {
  if (Array.isArray(node)) {
    for (const item of node) copyActivitiesWithoutRetry(item, found);
  } else if (node && typeof node === "object") {
    const obj = node as Record<string, unknown>;
    if (obj.type === "Copy" && typeof obj.name === "string") {
      const retry = Number((obj.policy as Record<string, unknown> | undefined)?.retry ?? 0);
      if (!(retry >= 1)) found.push(obj.name);
    }
    for (const value of Object.values(obj)) copyActivitiesWithoutRetry(value, found);
  }
  return found;
}

const CREDENTIAL_AUTH_TYPES = new Set(["SQL", "Basic", "Windows", "AccessKey"]);
const ENV_SPECIFIC_KEYS = new Set(["server", "host", "url", "baseUrl", "endpoint", "serviceUri", "accountName", "container", "fileSystem", "folderPath", "bucketName"]);

/** `key` paths whose value is a hard-coded env-specific string (not an `@` expression or `{type: "Expression"}` object). */
export function findEnvLiterals(node: unknown, found: string[] = []): string[] {
  if (Array.isArray(node)) {
    for (const item of node) findEnvLiterals(item, found);
  } else if (node && typeof node === "object") {
    for (const [key, value] of Object.entries(node)) {
      if (ENV_SPECIFIC_KEYS.has(key) && typeof value === "string" && value.trim() && !value.trim().startsWith("@")) found.push(key);
      else findEnvLiterals(value, found);
    }
  }
  return found;
}

// Delete-handling staging copies (pipeline-codegen.ts `deleteHandlingActivities`) only move keys, never landed rows.
const AUXILIARY_COPY_ACTIVITIES = new Set(["StageSourceKeys", "StageCdcChanges"]);

/** Copy activities (nested containers included) with their `additionalColumns` names. */
function copyActivityColumns(node: unknown, found: { name: string; columns: string[] }[] = []): { name: string; columns: string[] }[] {
  if (Array.isArray(node)) {
    for (const item of node) copyActivityColumns(item, found);
  } else if (node && typeof node === "object") {
    const obj = node as Record<string, unknown>;
    if (obj.type === "Copy" && typeof obj.name === "string" && !AUXILIARY_COPY_ACTIVITIES.has(obj.name)) {
      const source = (obj.typeProperties as { source?: { typeProperties?: { additionalColumns?: unknown } } } | undefined)?.source;
      const extra = source?.typeProperties?.additionalColumns;
      const columns = Array.isArray(extra) ? extra.map((c) => String((c as { name?: unknown })?.name ?? "")) : [];
      found.push({ name: obj.name, columns });
    }
    for (const value of Object.values(obj)) copyActivityColumns(value, found);
  }
  return found;
}

function pipelineLayer(props: Record<string, unknown>): MedallionLayer {
  const folder = (props.folder as { name?: unknown } | undefined)?.name;
  const match = typeof folder === "string" ? /(bronze|silver|gold)$/.exec(folder) : null;
  return (match?.[1] as MedallionLayer | undefined) ?? "bronze";
}

/** ADF's canonical Git folder per artifact type — keep in parity with the worker's `_ADF_FOLDER_BY_ARTIFACT_TYPE`. */
export const ADF_FOLDER_BY_ARTIFACT_TYPE: Partial<Record<ArtifactType, string>> = {
  credential: "credential",
  linked_service: "linkedService",
  dataset: "dataset",
  pipeline: "pipeline",
  trigger: "trigger",
  factory: "factory",
};

/**
 * `structure.layout`: an ADF Git resource must sit in its type's folder, and ADF Git mode only
 * imports a resource whose file name matches its `name`. Bare file names are fine (push adds the
 * folder); `factory` is skipped since `globalParameters.json` isn't named after its resource.
 */
export function layoutChecks(artifactType: ArtifactType, fileName: string, doc: unknown): string[] {
  const folder = ADF_FOLDER_BY_ARTIFACT_TYPE[artifactType];
  if (!folder || artifactType === "factory") return [];
  const out: string[] = [];
  const parts = fileName.split("/");
  if (parts.length > 1 && parts[parts.length - 2] !== folder) {
    out.push(`Stored under '${parts[parts.length - 2]}/' — ADF expects '${folder}/'`);
  }
  const name = doc && typeof doc === "object" ? (doc as Record<string, unknown>).name : undefined;
  if (typeof name === "string" && name && name !== artifactResourceName(fileName)) {
    out.push(`Resource name '${name}' doesn't match file name '${parts[parts.length - 1]}'`);
  }
  return out;
}

const DESCRIBED_TYPES: Partial<Record<ArtifactType, string>> = { pipeline: "Pipeline", dataset: "Dataset", linked_service: "Linked service" };

export interface AdvisoryOptions {
  auditColumns?: AuditColumnOverrides;
  /** Project `requiredTags.keys` (gaps.md GOV-05) — when set, `tagging.required` reports missing keys. */
  requiredTagKeys?: string[];
}

/**
 * The required tag keys with no matching annotation; `key` or `key:value` satisfies `key`,
 * case-insensitively. Keep in parity with backend/app/services.py::missing_required_tags.
 */
export function missingRequiredTags(annotations: unknown[], keys: string[]): string[] {
  const present = new Set(annotations.map((a) => String(a).split(":")[0].trim().toLowerCase()).filter(Boolean));
  return keys.filter((k) => k.trim() && !present.has(k.trim().toLowerCase()));
}

/** Run the advisory checks on one artifact's parsed JSON. */
export function advisoryChecks(
  artifactType: ArtifactType,
  doc: unknown,
  opts: AdvisoryOptions = {},
): { ruleId: AdvisoryRuleId; message: string }[] {
  const props = (doc && typeof doc === "object" ? (doc as Record<string, unknown>).properties : undefined) as Record<string, unknown> | undefined;
  if (!props || typeof props !== "object") return [];
  const out: { ruleId: AdvisoryRuleId; message: string }[] = [];
  if (artifactType === "linked_service") {
    for (const why of new Set(findInsecureSettings(props))) out.push({ ruleId: "security.transport", message: why });
    const tp = props.typeProperties as Record<string, unknown> | undefined;
    const auth = tp?.authenticationType;
    if (typeof auth === "string" && CREDENTIAL_AUTH_TYPES.has(auth)) {
      out.push({ ruleId: "connection.auth", message: `authenticationType is ${auth} — prefer managed identity or service principal` });
    }
  }
  if (artifactType === "linked_service" || artifactType === "dataset") {
    for (const key of new Set(findEnvLiterals(props.typeProperties))) {
      out.push({ ruleId: "param.literals", message: `${key} is a literal — parameterize environment-specific values` });
    }
  }
  if (artifactType === "pipeline") {
    const annotations = Array.isArray(props.annotations) ? props.annotations : [];
    const requiredKeys = (opts.requiredTagKeys ?? []).filter((k) => k.trim());
    if (requiredKeys.length) {
      const missing = missingRequiredTags(annotations, requiredKeys);
      if (missing.length) out.push({ ruleId: "tagging.required", message: `Pipeline is missing required tag(s) ${missing.join(", ")}` });
    } else if (annotations.length === 0) {
      out.push({ ruleId: "tagging.required", message: "Pipeline has no annotations" });
    }
    const expected = standardAuditColumns(pipelineLayer(props), opts.auditColumns).map((c) => c.name);
    for (const copy of copyActivityColumns(props.activities)) {
      const missing = expected.filter((c) => !copy.columns.includes(c));
      if (missing.length) out.push({ ruleId: "audit.columns", message: `Copy activity '${copy.name}' is missing audit column(s) ${missing.join(", ")}` });
    }
    for (const name of new Set(copyActivitiesWithoutRetry(props.activities))) {
      out.push({ ruleId: "runtime.retry", message: `Copy activity '${name}' has no retry policy (policy.retry < 1)` });
    }
  }
  const label = DESCRIBED_TYPES[artifactType];
  if (label && !(typeof props.description === "string" && props.description.trim())) {
    out.push({ ruleId: "docs.description", message: `${label} has no description` });
  }
  return out;
}

/** Scan the latest version of every JSON artifact of the given pipelines with the advisory checks. */
export function scanAdvisoryCompliance(
  artifacts: ArtifactVersion[],
  pipelineIds: Set<string>,
  opts: AdvisoryOptions = {},
): AdvisoryScanResult {
  const findings: AdvisoryFinding[] = [];
  let scanned = 0;
  let skipped = 0;
  for (const a of latestVersions(artifacts, pipelineIds)) {
    if (!(a.artifactType in DESCRIBED_TYPES) && !(a.artifactType in ADF_FOLDER_BY_ARTIFACT_TYPE)) continue;
    if (typeof a.content !== "string") {
      skipped += 1;
      continue;
    }
    let doc: unknown;
    try {
      doc = JSON.parse(a.content);
    } catch {
      skipped += 1;
      continue;
    }
    scanned += 1;
    const found = [
      ...advisoryChecks(a.artifactType, doc, opts),
      ...layoutChecks(a.artifactType, a.fileName, doc).map((message) => ({ ruleId: "structure.layout" as const, message })),
    ];
    for (const f of found) {
      findings.push({ artifactId: a.id, pipelineName: a.pipelineName, fileName: a.fileName, ...f });
    }
  }
  return { scanned, skipped, findings };
}

export type OnboardingRuleId = "onboarding.fields" | "onboarding.watermark" | "security.pii";

export interface OnboardingFinding {
  objectKey: string;
  ruleId: OnboardingRuleId;
  message: string;
}

/**
 * §25.3 onboarding rules over the project's enabled Bronze registry objects (scan only).
 * `onboarding.fields`: owner, target file format and a target object/path are set — the registry
 * has no domain column, so "domain" isn't checked. `onboarding.watermark`: a DeltaLoad object
 * declares both a watermark column and its type. `security.pii`: the registry has no separate PII flag,
 * so every enabled object must carry a `data_classification`.
 */
export function scanOnboardingCompliance(objects: OnboardingObjectEntry[]): { scanned: number; findings: OnboardingFinding[] } {
  const findings: OnboardingFinding[] = [];
  const enabled = objects.filter((o) => o.enabled);
  for (const o of enabled) {
    const missing = [
      !o.owner?.trim() && "owner",
      !o.target_file_format?.trim() && "target_file_format",
      !(o.target_object?.trim() || o.target_path?.trim()) && "target_object/target_path",
    ].filter(Boolean);
    if (missing.length) findings.push({ objectKey: o.object_key, ruleId: "onboarding.fields", message: `Missing ${missing.join(", ")}` });
    if (!o.data_classification) {
      findings.push({ objectKey: o.object_key, ruleId: "security.pii", message: "No data_classification set" });
    }
    if (o.loading_behavior === "DeltaLoad") {
      const wm = [!o.watermark_column?.trim() && "watermark_column", !o.watermark_column_type && "watermark_column_type"].filter(Boolean);
      if (wm.length) findings.push({ objectKey: o.object_key, ruleId: "onboarding.watermark", message: `DeltaLoad object missing ${wm.join(", ")}` });
    }
  }
  return { scanned: enabled.length, findings };
}

export interface ConnectorFinding {
  pipelineName: string;
  message: string;
}

/**
 * `connection.connectors`: every in-scope pipeline's source/target connector must be on the project's
 * allow-list. An empty list means "not configured" — nothing is checked.
 */
export function scanConnectorCompliance(pipelines: PipelineDefinition[], allowed: string[]): { configured: boolean; scanned: number; findings: ConnectorFinding[] } {
  if (!allowed.length) return { configured: false, scanned: 0, findings: [] };
  const ok = new Set(allowed);
  const findings: ConnectorFinding[] = [];
  for (const p of pipelines) {
    for (const [side, type] of [["Source", p.spec?.source?.type], ["Target", p.spec?.target?.type]] as const) {
      if (type && !ok.has(type)) findings.push({ pipelineName: p.name, message: `${side} connector '${type}' is not on the allow-list` });
    }
  }
  return { configured: true, scanned: pipelines.length, findings };
}

/**
 * `git.branch`: does a feature branch follow the project's `featureBranchPattern`? Each `{token}`
 * matches one run of `[A-Za-z0-9._-]`; everything else is literal. A blank pattern accepts anything.
 */
export function branchMatchesPattern(branch: string, pattern: string): boolean {
  if (!pattern.trim()) return true;
  const rx = pattern
    .trim()
    .split(/(\{[^}]+\})/)
    .map((part) => (/^\{[^}]+\}$/.test(part) ? "[A-Za-z0-9._-]+" : part.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")))
    .join("");
  return new RegExp(`^${rx}$`).test(branch.trim());
}
