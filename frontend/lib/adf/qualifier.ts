/**
 * Source-object qualifier resolution — turns a source system's own object
 * identifier (schema.table, catalog.schema.table, or a schema-less
 * entity/OData path) into (a) the exact identifier used to query the source
 * and (b) an ADF-legal name segment for generated artifacts.
 *
 * This fixes the root cause tracked in the "naming-convention edge cases"
 * plan: a single free-text "schema.table" string was split naively on "."
 * with no knowledge of the connector's real identifier shape
 * (`pipeline-codegen.ts`'s old `splitObjectName` and `spec.ts`'s
 * `sourceDatasetDefaults` both did `parts.slice(0, -1).join(".")` for
 * "schema" and fell back to a hard-coded `"dbo"` when there was no dot at
 * all). That silently produced wrong or meaningless dataset properties for:
 *  - 3-part sources (Snowflake, SAP HANA with an explicit tenant/catalog);
 *  - schema-less sources (SAP ECC/OData, SAP Cloud for Customer, and every
 *    other SaaS/OData connector) — `"dbo"` isn't a schema they have;
 *  - object names that legitimately contain a dot;
 *  - names ADF's own naming rules forbid in a generated artifact name
 *    (§24.5), e.g. SAP's `/BIC/AZCUSTOMER`-style namespaced BW names;
 *  - source systems that are case-sensitive by convention (Oracle, SAP
 *    HANA/ECC uppercase; Postgres/Cassandra lowercase) — an object onboarded
 *    in the "wrong" case must still resolve correctly against the source.
 *
 * Kept in parity with `worker/app/generator/qualifier.py` (frontend is the
 * reference implementation, same convention as `pipeline-codegen.ts` /
 * `codegen.py` — see DEVELOPMENT_NOTES.md's generator-parity note).
 */
import type { SourceTargetType } from "@/types";
import { connectorSchema } from "./connections";

export type QualifierPart = "catalog" | "schema" | "object";
export type CaseConvention = "insensitive" | "upper" | "lower";

export interface QualifierProfile {
  /** Identifier segments this connector's objects are made of, in order. Always ends with "object". */
  parts: QualifierPart[];
  /** The real-world case convention unquoted identifiers fold to on this source. */
  caseConvention: CaseConvention;
  objectKind: "table" | "entity" | "file";
}

/** The common case: a database-scoped relational object, `schema.table`. */
const DEFAULT_PROFILE: QualifierProfile = { parts: ["schema", "object"], caseConvention: "insensitive", objectKind: "table" };
/** A flat SaaS/API/OData entity name — no schema concept, don't split on "." at all. */
const ENTITY_PROFILE: QualifierProfile = { parts: ["object"], caseConvention: "insensitive", objectKind: "entity" };
/** A relational object whose database *and* schema are already pinned by the linked service. */
const TABLE_ONLY_PROFILE: QualifierProfile = { parts: ["object"], caseConvention: "insensitive", objectKind: "table" };
/** Object/file storage — the folder/file path is handled by its own dedicated fields, not this module. */
const FILE_PROFILE: QualifierProfile = { parts: ["object"], caseConvention: "insensitive", objectKind: "file" };

/**
 * Explicit overrides for connectors whose identifier depth or case
 * convention differs from what their `kind` alone would suggest. Anything
 * not listed here falls back to `defaultProfileForKind` — this is
 * intentionally a short, verifiable list (§1/§5 "no fabricated values":
 * only documented, well-established conventions), not an attempt to
 * hand-tune every one of the ~90 registered connectors.
 */
const QUALIFIER_PROFILE_OVERRIDES: Partial<Record<SourceTargetType, QualifierProfile>> = {
  // 3-part: the linked service normally pins the catalog/database, but a
  // shared SAP HANA/Snowflake linked service serving several tenant
  // DBs/databases needs to override it per object. Both fold to uppercase
  // unless the real object is quoted mixed-case (caseOverride escape hatch).
  sap_hana: { parts: ["catalog", "schema", "object"], caseConvention: "upper", objectKind: "table" },
  snowflake: { parts: ["catalog", "schema", "object"], caseConvention: "upper", objectKind: "table" },

  // Schema-less: SAP's own application-layer connectors expose one flat
  // entity/service-path name, never a database schema.
  sap_ecc: ENTITY_PROFILE,
  sap_cloud_for_customer: ENTITY_PROFILE,
  // SAP BW: the object is an Open Hub destination / InfoProvider technical
  // name (often namespaced /BIC/..., /BI0/...), not a schema.table pair.
  sap_bw: ENTITY_PROFILE,

  // BigQuery's "server" field is already the project id (hasDatabase:
  // false at the linked-service level) — the real per-object identifier is
  // still 2-part, dataset.table, not 1-part.
  google_big_query: DEFAULT_PROFILE,

  // Documented case-folding conventions that differ from "insensitive".
  oracle: { ...DEFAULT_PROFILE, caseConvention: "upper" },
  postgresql: { ...DEFAULT_PROFILE, caseConvention: "lower" },
  azure_database_postgresql: { ...DEFAULT_PROFILE, caseConvention: "lower" },
  cassandra: { ...DEFAULT_PROFILE, caseConvention: "lower" },

  // Database *and* schema are both pinned on the linked service itself —
  // the per-object identifier is just the table name.
  mysql: TABLE_ONLY_PROFILE,
  azure_database_mysql: TABLE_ONLY_PROFILE,
  apache_impala: TABLE_ONLY_PROFILE,
  apache_hbase: TABLE_ONLY_PROFILE,
  apache_phoenix: TABLE_ONLY_PROFILE,
  azure_databricks_delta_lake: TABLE_ONLY_PROFILE,
};

/** Derive a sensible default from the connector's registered `kind` when there's no explicit override above. */
function defaultProfileForKind(kind: string | undefined): QualifierProfile {
  if (kind === "file") return FILE_PROFILE;
  if (kind === "service" || kind === "llm") return ENTITY_PROFILE;
  return DEFAULT_PROFILE;
}

/** The qualifier profile for a connector, metadata-driven off the connector registry (never hand-guessed per pipeline). */
export function getQualifierProfile(connectorType: SourceTargetType | string | undefined): QualifierProfile {
  if (!connectorType) return DEFAULT_PROFILE;
  const override = QUALIFIER_PROFILE_OVERRIDES[connectorType as SourceTargetType];
  if (override) return override;
  return defaultProfileForKind(connectorSchema(connectorType).kind);
}

export interface QualifierEntryInput {
  /** Free-text fallback (legacy single-string field, or the bare object segment when catalog/schema are given separately). */
  object: string;
  catalog?: string;
  schema?: string;
  /** Escape hatch: use this exact string to query the source, bypassing parsing/case-conformance entirely. */
  qualifierOverride?: string;
  /** Escape hatch: skip automatic case-conformance for this one object (a genuinely quoted mixed-case identifier). */
  caseOverride?: "as-typed";
}

export interface ResolvedQualifiedName {
  catalog?: string;
  schema?: string;
  object: string;
  /** Exact identifier to use when the pipeline actually queries the source — case-conformed per connector, or verbatim on an override. */
  forQuery: string;
  /** Human-readable dotted display form, same casing as `forQuery`. */
  display: string;
}

function foldCase(value: string, convention: CaseConvention): string {
  if (convention === "upper") return value.toUpperCase();
  if (convention === "lower") return value.toLowerCase();
  return value;
}

/**
 * Split a legacy dotted free-text string into { catalog?, schema?, object }
 * per the connector's profile. Unlike the old blind `split(".")`, segment
 * count is driven by `profile.parts`:
 *  - an entity/1-part profile never splits — the whole string is one object
 *    segment, so a legitimate dot (an OData path, a versioned file name)
 *    survives intact;
 *  - a 2-part profile treats everything left of the last "." as schema;
 *  - a 3-part profile treats the segment left of that as catalog, joining
 *    any further-left segments into catalog rather than guessing.
 */
function parseQualifiedString(raw: string, profile: QualifierProfile): Omit<QualifierEntryInput, "qualifierOverride" | "caseOverride"> {
  const clean = (raw ?? "").trim();
  if (profile.parts.length <= 1) return { object: clean };
  const segments = clean.split(".").filter((s) => s.length > 0);
  if (segments.length <= 1) return { object: clean || "" };
  const object = segments[segments.length - 1];
  if (profile.parts.length === 2 || segments.length === 2) {
    return { schema: segments.slice(0, -1).join("."), object };
  }
  // 3-part profile with 3+ segments: last two dot-separated segments before
  // the object are catalog and schema; anything further left folds into catalog.
  const schema = segments[segments.length - 2];
  const catalog = segments.slice(0, segments.length - 2).join(".");
  return { catalog, schema, object };
}

/**
 * Resolve a source object entry into the identifier used to query the
 * source (case-conformed, connector-aware) and a display form, given either
 * structured fields or the legacy dotted string.
 */
export function resolveQualifiedName(entry: QualifierEntryInput, profile: QualifierProfile): ResolvedQualifiedName {
  if (entry.qualifierOverride?.trim()) {
    const literal = entry.qualifierOverride.trim();
    return { object: literal, forQuery: literal, display: literal };
  }
  const parsed =
    entry.catalog || entry.schema
      ? { catalog: entry.catalog?.trim() || undefined, schema: entry.schema?.trim() || undefined, object: entry.object.trim() }
      : parseQualifiedString(entry.object, profile);

  const useAsTyped = entry.caseOverride === "as-typed";
  const fold = (v: string) => (useAsTyped ? v : foldCase(v, profile.caseConvention));

  const catalog = parsed.catalog ? fold(parsed.catalog) : undefined;
  const schema = parsed.schema ? fold(parsed.schema) : undefined;
  const object = fold(parsed.object || "");

  const display = [catalog, schema, object].filter(Boolean).join(".");
  return { catalog, schema, object, forQuery: display, display };
}

/**
 * Replace every character ADF's naming rules forbid in a linked
 * service/dataset/pipeline/trigger name (§24.5: `. + ? / < > * % & : \`,
 * `-`, and space) with `_`, so a sanitized source segment always produces a
 * generated artifact name ADF itself will accept — e.g. SAP's
 * `/BIC/AZCUSTOMER` becomes `_BIC_AZCUSTOMER`. This never touches the value
 * used to actually query the source (`resolveQualifiedName(...).forQuery`);
 * it is only for the generated artifact's own name.
 */
export function sanitizeForAdfName(raw: string): string {
  const trimmed = (raw ?? "").trim();
  // Replace each *run* of illegal characters (absorbing any literal
  // underscores immediately touching it) with a single "_", rather than
  // blindly collapsing every underscore run in the string — the latter
  // mangled legitimate double-underscore conventions that never contained an
  // illegal character at all, most notably Salesforce's literal `__c`/`__r`
  // custom-field/relationship suffix (`Renewal_Date__c` would collapse to
  // `Renewal_Date_c`, potentially colliding with an unrelated object).
  const replaced = trimmed.replace(/_*[.+?/<>*%&:\\\-\s]+_*/g, "_").replace(/^_+|_+$/g, "");
  return replaced || "object";
}

/** A sanitized, underscore-joined token suitable for embedding in a generated artifact name (e.g. `SAPABAP1_MARA`). */
export function buildSafeQualifierToken(resolved: ResolvedQualifiedName): string {
  return [resolved.catalog, resolved.schema, resolved.object]
    .filter((part): part is string => !!part)
    .map(sanitizeForAdfName)
    .join("_");
}

export interface QualifierCollision {
  /** The colliding generated-name token. */
  token: string;
  /** The onboarding/object keys that all resolve to it. */
  keys: string[];
}

/**
 * Flags when two or more source objects resolve to the same generated
 * target-name token after sanitization — e.g. two different sources both
 * producing `curated.policy` as their target (a real, previously-unenforced
 * gap: see DEVELOPMENT_NOTES.md's 2026-09-12 note). This is meant to be wired in as an
 * always-on check (§25.5 "safety rules" — a collision is a correctness bug,
 * not a style preference), not a configurable naming pattern.
 */
export function checkQualifierCollision(entries: Array<{ key: string; token: string }>): QualifierCollision[] {
  const byToken = new Map<string, string[]>();
  for (const { key, token } of entries) {
    const keys = byToken.get(token) ?? [];
    keys.push(key);
    byToken.set(token, keys);
  }
  return [...byToken.entries()]
    .filter(([, keys]) => keys.length > 1)
    .map(([token, keys]) => ({ token, keys }));
}

export interface SapExtractionIssue {
  level: "warning" | "error";
  message: string;
}

/**
 * Flag-only check for the separate class of SAP problem naming can't fix:
 * some objects aren't readable by a plain Copy activity at all, regardless
 * of how their identifier is parsed. This never blocks generation logic —
 * it only surfaces a validation message pointing at the right connector, so
 * a user doesn't generate a Copy activity that will fail at runtime no
 * matter how the object name is qualified.
 */
export function checkSapExtractionSupport(connectorType: SourceTargetType | string | undefined, objectSegment: string): SapExtractionIssue | null {
  const object = (objectSegment ?? "").trim();
  if (!object) return null;
  // SAP BW's namespace convention (/BI0/... = SAP-delivered, /BIC/... =
  // customer-created) marks a BW InfoObject/InfoProvider technical name.
  // These aren't queryable through a generic table/HANA connector; the tool
  // already models the right connector for BW (`sap_bw`, Open Hub) — a
  // namespaced name on any *other* connector means the wrong one was picked.
  const isBwNamespaced = /^\/BI[C0]\//i.test(object);
  if (isBwNamespaced && connectorType !== "sap_bw") {
    return {
      level: "error",
      message: `"${object}" looks like an SAP BW namespaced object (/BIC/ or /BI0/). A generic table connector can't read it directly — use the SAP BW (Open Hub) connector instead.`,
    };
  }
  // Best-effort heuristic, not exhaustive: a short list of classic SAP
  // tables commonly documented as pool/cluster tables (e.g. in FI/SD ABAP
  // references), which historically require an RFC/function-module
  // extraction rather than a direct SQL read on some SAP database layers.
  // Flagged as a warning (confirm, don't silently proceed), not a hard
  // block, since this list is not a verified exhaustive source of truth.
  const KNOWN_POOL_CLUSTER_TABLES = new Set(["BSEG", "KONV", "BSEC", "BSED"]);
  if (connectorType && connectorType !== "sap_bw" && KNOWN_POOL_CLUSTER_TABLES.has(object.toUpperCase())) {
    return {
      level: "warning",
      message: `"${object}" is commonly documented as an SAP pool/cluster table, which some SAP database layers don't expose to a direct SQL read. Confirm this object is accessible via ${connectorType}, or extract it via an RFC/function-module layer this accelerator does not generate.`,
    };
  }
  return null;
}
