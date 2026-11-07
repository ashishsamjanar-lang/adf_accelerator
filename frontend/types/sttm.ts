/**
 * §33 STTM-Based Silver/Gold Object Registry — `app.silver_objects`/
 * `app.mart_objects` row shape, mirrored from `backend/app/api/schemas.py`'s
 * `RegistryObjectOut`/`RegistryObjectIn` (camelCase on the wire, same as
 * every other domain type here). `transformations`/`dataQualityRules` are
 * the *exact* shapes `lib/adf/medallion.ts`'s `TransformationRule[]` and
 * `SpecDraft.dataQualityRules` already use (§33.2 — no new engine).
 */
import type { TransformationRule } from "@/lib/adf/medallion";

export type SttmLayer = "silver" | "mart";
/** The medallion layer a registry entry generates as — "silver" registry
 *  rows are always medallion layer "silver"; "mart" registry rows are
 *  always medallion layer "gold" (the registry is named after the DB table,
 *  the medallion layer after the Build wizard's own vocabulary). */
export const STTM_MEDALLION_LAYER: Record<SttmLayer, "silver" | "gold"> = { silver: "silver", mart: "gold" };

export type SttmWriteBehavior = "append" | "overwrite" | "upsert" | "merge";
export type SttmLoadingBehavior = "FullLoad" | "DeltaLoad";
export type SttmDataClassification = "public" | "internal" | "confidential" | "restricted";
export type SttmObjectStatus = "draft" | "valid" | "invalid";
export type SttmProvenance = "upload" | "manual";

export interface SttmDataQualityRule {
  name: string;
  column: string;
  rule: "IS_NOT_NULL" | "UNIQUE" | "LENGTH_LE" | "RANGE_BETWEEN" | "REGEX_MATCH" | "ROW_COUNT_MIN" | "FRESHNESS_HOURS";
  value?: string;
  enforcement?: "warn" | "block";
  /** DMT-05: breach only when more than this % (0-100) of checked rows violate a row-level rule. */
  maxViolationPct?: string;
}

/** One `app.silver_objects`/`app.mart_objects` row. `martDomain` is always
 *  `null` on a silver-layer object. */
export interface RegistryObject {
  objectKey: string;
  projectId: string;
  version: number;
  enabled: boolean;
  tag: string;
  curatedEntity: string;
  martDomain?: string | null;
  sourceObjectKeys: string[];
  owner?: string | null;
  batchGroup?: string | null;
  domain?: string | null;
  writeBehavior: SttmWriteBehavior;
  loadingBehavior: SttmLoadingBehavior;
  watermarkColumn?: string | null;
  watermarkColumnType?: string | null;
  businessKeys: string[];
  transformations: TransformationRule[];
  dataQualityRules: SttmDataQualityRule[];
  targetTable: string;
  dataClassification?: SttmDataClassification | null;
  /** gaps.md DMT-08: typed governance fields (null = not stated). */
  containsPii?: boolean | null;
  containsPhi?: boolean | null;
  containsPci?: boolean | null;
  criticalDataElement?: boolean | null;
  auditRequired?: boolean | null;
  retentionPeriod?: string | null;
  regulatoryTag?: string | null;
  businessCriticality?: string | null;
  notes: string;
  provenanceSource: SttmProvenance;
  importId?: string | null;
  status: SttmObjectStatus;
  createdBy?: string | null;
  createdByName?: string | null;
  updatedBy?: string | null;
  updatedByName?: string | null;
  createdAt?: string | null;
  updatedAt?: string | null;
  /** §33.6 rule 6 (soft, non-blocking) — derived at read time, never stored:
   *  a DQ rule whose `column` doesn't appear among this object's own
   *  transformations' output columns. Optional only because older cached
   *  responses/fixtures may predate this field. */
  warnings?: SttmSheetIssue[];
}

/** Create/update body — a subset of `RegistryObject`'s fields, the ones a
 *  form or an STTM sheet actually supplies (server fills in the rest). */
export type RegistryObjectInput = Partial<
  Pick<
    RegistryObject,
    | "objectKey"
    | "enabled"
    | "tag"
    | "curatedEntity"
    | "martDomain"
    | "sourceObjectKeys"
    | "owner"
    | "batchGroup"
    | "domain"
    | "writeBehavior"
    | "loadingBehavior"
    | "watermarkColumn"
    | "watermarkColumnType"
    | "businessKeys"
    | "transformations"
    | "dataQualityRules"
    | "dataClassification"
    | "containsPii"
    | "containsPhi"
    | "containsPci"
    | "criticalDataElement"
    | "auditRequired"
    | "retentionPeriod"
    | "regulatoryTag"
    | "businessCriticality"
    | "notes"
  >
>;

export interface SttmSheetIssue {
  severity: "error" | "warning" | "info";
  message: string;
  field?: string;
}

export interface SttmParsedSheet {
  sheetName: string;
  objectKey: string | null;
  curatedEntity: string | null;
  valid: boolean;
  issues: SttmSheetIssue[];
  parsed: RegistryObjectInput | null;
  /** Version of the already-existing registry row this sheet's `objectKey`
   *  matched at upload time (`null`/absent for a brand-new object or for a
   *  client-side preview parse, which has no DB to check against). Echo this
   *  back as `expectedVersions[objectKey]` on commit (§33.9/§33.11) to catch
   *  a genuinely concurrent edit landing between preview and commit. */
  existingVersion?: number | null;
}

export interface SttmParseReport {
  fileHash: string;
  sheetTotal: number;
  sheetValid: number;
  sheetError: number;
  sheets: SttmParsedSheet[];
}

export interface SttmImportRecord {
  id: string;
  projectId: string;
  layer: SttmLayer;
  fileName: string;
  fileHash: string;
  sheetTotal: number;
  sheetValid: number;
  sheetError: number;
  mode: "upsert";
  committedBy?: string | null;
  committedAt?: string | null;
  sheets: SttmParsedSheet[];
  createdAt?: string | null;
}
