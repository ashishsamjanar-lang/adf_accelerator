import { execFileSync } from "node:child_process";
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import path from "node:path";
import ExcelJS from "exceljs";
import {
  DQ_COLUMNS,
  DQ_DATA_START_ROW,
  DQ_HEADER_ROW,
  HEADER_FIELDS,
  HEADER_START_ROW,
  TRANSFORM_COLUMNS,
  TRANSFORM_DATA_START_ROW,
  TRANSFORM_HEADER_ROW,
} from "@/lib/adf/sttm";

/**
 * Dummy (non-PHI) healthcare medallion metadata for `healthcare-medallion.spec.ts`: the Bronze
 * onboarding CSV, Silver/Gold STTM objects in the Data Modular template layout (the primary upload
 * format), and two Gold objects in the legacy one-sheet-per-object layout. Workbooks are written
 * into the templates the UI itself hands out (downloaded in the spec), never built from scratch.
 */

// ── Bronze: §26 onboarding CSV (source_system is the Bronze landing token) ────────────────────────

export const BRONZE_CSV = [
  "source_system,source_type,source_schema,source_object,target_type,target_file_format,loading_behavior,watermark_column,watermark_column_type,write_behavior,upsert_keys,batch_group,owner,data_classification",
  "epic,table,dbo,PATIENT,table,Parquet,DeltaLoad,UPDATE_DATE,datetime,upsert,PAT_ID,clarity-core,clinical-data-eng,restricted",
  "epic,table,dbo,PAT_ENC,table,Parquet,DeltaLoad,CONTACT_DATE,datetime,append,,clarity-core,clinical-data-eng,restricted",
  "epic,table,dbo,CLARITY_SER,table,Parquet,FullLoad,,,overwrite,,clarity-core,clinical-data-eng,confidential",
  "epic,table,dbo,ORDER_RESULTS,table,Parquet,DeltaLoad,RESULT_TIME,datetime,append,,clarity-lab,clinical-data-eng,restricted",
  "epic,table,dbo,ICD10_CODE,table,Parquet,FullLoad,,,overwrite,,reference,clinical-data-eng,public",
  "facets,table,CLAIMS,CLAIM_HEADER,table,Parquet,FullLoad,,,overwrite,,claims-nightly,payer-data-eng,confidential",
  "facets,table,CLAIMS,CLAIM_LINE,table,Parquet,FullLoad,,,overwrite,,claims-nightly,payer-data-eng,confidential",
  "facets,table,CLAIMS,MEMBER,table,Parquet,FullLoad,,,overwrite,,claims-nightly,payer-data-eng,restricted",
  "cred,table,credentialing,provider,table,Parquet,DeltaLoad,last_credentialed_at,datetime,append,,provider,provider-data-eng,confidential",
].join("\n");

export const BRONZE_OBJECT_COUNT = BRONZE_CSV.split("\n").length - 1;

// ── Data Modular template (STTM_Template sheet: Section 1 attributes + Section 2 mapping rows) ────

export type Cells = Record<string, string>;
export interface DataModularObject {
  /** Workbook file name stem — also the preview/commit sheet label. */
  file: string;
  section1: Cells;
  rows: Cells[];
}

const col = (target: string, source: string, extra: Cells = {}): Cells => ({ Record_Type: "DATA", Target_Column_Name: target, Source_Column: source, ...extra });

export const SILVER_OBJECTS: DataModularObject[] = [
  {
    file: "silver_patient",
    section1: {
      Target_Table_ID: "patient", Target_Table_Name: "patient", Target_Layer: "SILVER", Target_Domain: "clinical",
      Target_Subject_Area: "patient_identity", Load_Type: "INCREMENTAL", Load_Strategy: "MERGE",
      Watermark_Column: "updated_at", SCD_Business_Key: "mrn", Data_Classification: "restricted",
      PHI_Flag: "Y", PII_Flag: "Y", Retention_Period: "10 years", Regulatory_Tag: "HIPAA", Technical_Owner: "clinical-data-eng",
      Mapping_Status: "ACTIVE", Approval_Status: "APPROVED", Target_Table_Description: "Conformed patient master (one row per MRN).",
    },
    rows: [
      col("patient_id", "PAT_ID", { Source_Object: "PATIENT", Target_Ordinal: "1", Source_Data_Type: "varchar", Target_Data_Type: "varchar" }),
      col("mrn", "PAT_MRN_ID", { Source_Object: "PATIENT", Target_Ordinal: "2", Natural_Key_Flag: "Y", DQ_Rule_Type: "NOT_NULL", DQ_Action: "FAIL" }),
      col("first_name", "PAT_FIRST_NAME", { Source_Object: "PATIENT", Target_Ordinal: "3", Transformation_Expression: "initcap(trim(PATIENT.PAT_FIRST_NAME))" }),
      col("last_name", "PAT_LAST_NAME", { Source_Object: "PATIENT", Target_Ordinal: "4", Transformation_Expression: "upper(trim(PATIENT.PAT_LAST_NAME))" }),
      col("birth_date", "BIRTH_DATE", { Source_Object: "PATIENT", Target_Ordinal: "5", Source_Data_Type: "datetime", Target_Data_Type: "date" }),
      col("sex_code", "SEX_C", { Source_Object: "PATIENT", Target_Ordinal: "6", Transformation_Expression: "coalesce(PATIENT.SEX_C, 'U')", DQ_Rule_Type: "LENGTH", DQ_Threshold: "1" }),
      col("updated_at", "UPDATE_DATE", { Source_Object: "PATIENT", Target_Ordinal: "7", Source_Data_Type: "datetime", Target_Data_Type: "datetime2" }),
      { Record_Type: "DATA", Source_Object: "PATIENT", Filter_Condition: "PATIENT.IS_TEST_PATIENT = 'N'" },
    ],
  },
  {
    file: "silver_encounter",
    section1: {
      Target_Table_ID: "encounter", Target_Table_Name: "encounter", Target_Layer: "SILVER", Target_Domain: "clinical",
      Target_Subject_Area: "encounters", Target_Schema: "curated", Load_Type: "INCREMENTAL", Load_Strategy: "MERGE",
      Watermark_Column: "contact_date", Data_Classification: "restricted", PHI_Flag: "Y", Technical_Owner: "clinical-data-eng",
    },
    rows: [
      col("encounter_id", "PAT_ENC_CSN_ID", { Source_Object: "PAT_ENC", Target_Ordinal: "1", Primary_Key_Flag: "Y", Key_Sequence: "1", DQ_Rule_Type: "UNIQUE", DQ_Severity: "CRITICAL" }),
      col("patient_id", "PAT_ID", { Source_Object: "PAT_ENC", Target_Ordinal: "2", DQ_Rule_Type: "NOT_NULL", DQ_Threshold: "99.5%" }),
      col("contact_date", "CONTACT_DATE", { Source_Object: "PAT_ENC", Target_Ordinal: "3", Source_Data_Type: "datetime" }),
      col("admit_ts", "HOSP_ADMSN_TIME", { Source_Object: "PAT_ENC", Target_Ordinal: "4" }),
      col("discharge_ts", "HOSP_DISCH_TIME", { Source_Object: "PAT_ENC", Target_Ordinal: "5" }),
      col("length_of_stay_days", "", { Source_Object: "PAT_ENC", Target_Ordinal: "6", Transformation_Expression: "datediff(PAT_ENC.HOSP_DISCH_TIME, PAT_ENC.HOSP_ADMSN_TIME)", DQ_Rule_Type: "RANGE", DQ_Rule_Expression: "length_of_stay_days BETWEEN 0 AND 365" }),
      col("drg_code", "DRG_CODE", { Source_Object: "PAT_ENC", Target_Ordinal: "7" }),
      col("primary_dx_desc", "", { Source_Object: "PAT_ENC", Target_Ordinal: "8", Lookup_Table: "ICD10_CODE", Lookup_Condition: "PAT_ENC.PRIMARY_DX_CODE = ICD10_CODE.CODE", Lookup_Return_Column: "DESCRIPTION", Lookup_Default_Value: "UNKNOWN" }),
    ],
  },
  {
    file: "silver_claim_line",
    section1: {
      Target_Table_ID: "claim_line", Target_Table_Name: "claim_line", Target_Layer: "SILVER", Target_Domain: "payer",
      Target_Subject_Area: "claims", Load_Type: "FULL", Load_Strategy: "REPLACE", Data_Classification: "confidential",
      PHI_Flag: "Y", Technical_Owner: "payer-data-eng", Partition_Column: "service_date",
    },
    rows: [
      col("claim_id", "CLAIM_ID", { Source_Object: "CLAIM_LINE", Target_Ordinal: "1", Primary_Key_Flag: "Y", Key_Sequence: "1" }),
      col("line_no", "LINE_NO", { Source_Object: "CLAIM_LINE", Target_Ordinal: "2", Primary_Key_Flag: "Y", Key_Sequence: "2" }),
      col("member_id", "MEMBER_ID", { Source_Object: "CLAIM_LINE", Target_Ordinal: "3", DQ_Rule_Type: "NOT_NULL", DQ_Action: "REJECT" }),
      col("service_date", "SVC_FROM_DT", { Source_Object: "CLAIM_LINE", Target_Ordinal: "4", Source_Data_Type: "varchar", Target_Data_Type: "date" }),
      col("billed_amt", "BILLED_AMT", { Source_Object: "CLAIM_LINE", Target_Ordinal: "5", Source_Data_Type: "float", Target_Data_Type: "decimal", Target_Precision: "18", Target_Scale: "2", DQ_Rule_Type: "RANGE", DQ_Rule_Expression: "billed_amt BETWEEN 0 AND 5000000" }),
      col("paid_amt", "PAID_AMT", { Source_Object: "CLAIM_LINE", Target_Ordinal: "6", Transformation_Expression: "nvl(CLAIM_LINE.PAID_AMT, 0)" }),
      col("procedure_code", "CPT_CODE", { Source_Object: "CLAIM_LINE", Target_Ordinal: "7", DQ_Rule_Type: "REGEX", DQ_Rule_Expression: "^[0-9]{5}$" }),
      { Record_Type: "DATA", Source_Object: "CLAIM_LINE", Filter_Condition: "CLAIM_LINE.CLAIM_STATUS <> 'VOID'" },
    ],
  },
  {
    file: "silver_provider",
    section1: {
      Target_Table_ID: "provider", Target_Table_Name: "provider", Target_Layer: "SILVER", Target_Domain: "provider",
      Target_Subject_Area: "provider_network", Load_Type: "INCREMENTAL", Load_Strategy: "MERGE", SCD_Type: "SCD2",
      SCD_Business_Key: "npi", Watermark_Column: "last_credentialed_at", SCD_Effective_From: "effective_from",
      SCD_Effective_To: "effective_to", SCD_Current_Flag: "is_current", Data_Classification: "confidential", Technical_Owner: "provider-data-eng",
    },
    rows: [
      col("npi", "npi", { Source_Object: "provider", Target_Ordinal: "1", DQ_Rule_Type: "LENGTH", DQ_Threshold: "10", DQ_Action: "FAIL" }),
      col("provider_name", "full_name", { Source_Object: "provider", Target_Ordinal: "2" }),
      col("specialty", "specialty", { Source_Object: "provider", Target_Ordinal: "3" }),
      col("network_status", "network_status", { Source_Object: "provider", Target_Ordinal: "4" }),
      col("practice_location", "practice_location", { Source_Object: "provider", Target_Ordinal: "5" }),
      col("last_credentialed_at", "last_credentialed_at", { Source_Object: "provider", Target_Ordinal: "6", Source_Data_Type: "datetime" }),
    ],
  },
  {
    file: "silver_lab_result",
    section1: {
      Target_Table_ID: "lab_result", Target_Table_Name: "lab_result", Target_Layer: "SILVER", Target_Domain: "clinical",
      Target_Subject_Area: "laboratory", Load_Type: "INCREMENTAL", Load_Strategy: "INSERT", Watermark_Column: "result_ts",
      Data_Classification: "restricted", Mapping_Status: "DRAFT",
    },
    rows: [
      col("order_id", "ORDER_PROC_ID", { Source_Object: "ORDER_RESULTS", Target_Ordinal: "1" }),
      col("component", "COMPONENT_NAME", { Source_Object: "ORDER_RESULTS", Target_Ordinal: "2", Transformation_Expression: "upper(trim(ORDER_RESULTS.COMPONENT_NAME))" }),
      col("result_value", "ORD_NUM_VALUE", { Source_Object: "ORDER_RESULTS", Target_Ordinal: "3", Transformation_Expression: "cast(ORDER_RESULTS.ORD_NUM_VALUE as double)" }),
      col("result_ts", "RESULT_TIME", { Source_Object: "ORDER_RESULTS", Target_Ordinal: "4", Source_Data_Type: "datetime", DQ_Rule_Type: "FRESHNESS", DQ_Threshold: "24" }),
    ],
  },
];

/** Deliberately broken Silver uploads — the preview/commit must reject or skip them. */
export const SILVER_BAD_OBJECTS: DataModularObject[] = [
  {
    file: "silver_wrong_layer",
    section1: { Target_Table_ID: "bed_census", Target_Table_Name: "bed_census", Target_Layer: "BRONZE", Target_Subject_Area: "capacity" },
    rows: [col("bed_id", "BED_ID", { Source_Object: "BED" })],
  },
  {
    file: "silver_orphan_source",
    section1: { Target_Table_ID: "immunization", Target_Table_Name: "immunization", Target_Layer: "SILVER", Target_Subject_Area: "immunizations" },
    rows: [col("immunization_id", "IMM_ID", { Source_Object: "IMMUNE" })],
  },
];

export const GOLD_OBJECTS: DataModularObject[] = [
  {
    file: "gold_readmission_30d",
    section1: {
      Target_Table_ID: "readmission_30d", Target_Table_Name: "readmission_30d", Target_Layer: "GOLD", Target_Domain: "clinical_quality",
      Target_Subject_Area: "quality_measures", Load_Type: "FULL", Load_Strategy: "REPLACE", Data_Classification: "confidential",
    },
    rows: [
      col("patient_id", "patient_id", { Source_Object: "encounter", Target_Ordinal: "1" }),
      col("drg_code", "drg_code", { Source_Object: "encounter", Target_Ordinal: "2" }),
      col("encounter_count", "", { Source_Object: "encounter", Target_Ordinal: "3", Aggregation_Expression: "COUNT(*)", Aggregation_Group: "patient_id, drg_code", DQ_Rule_Type: "RANGE", DQ_Rule_Expression: "encounter_count BETWEEN 0 AND 500" }),
      col("avg_length_of_stay", "", { Source_Object: "encounter", Target_Ordinal: "4", Aggregation_Expression: "AVG(length_of_stay_days)", Aggregation_Group: "patient_id, drg_code" }),
    ],
  },
  {
    file: "gold_claims_cost_monthly",
    section1: {
      Target_Table_ID: "claims_cost_monthly", Target_Table_Name: "claims_cost_monthly", Target_Layer: "GOLD", Target_Domain: "finance",
      Target_Subject_Area: "cost_of_care", Load_Type: "FULL", Load_Strategy: "REPLACE", Data_Classification: "confidential",
    },
    rows: [
      col("member_id", "member_id", { Source_Object: "claim_line", Target_Ordinal: "1" }),
      col("service_month", "", { Source_Object: "claim_line", Target_Ordinal: "2", Transformation_Expression: "date_format(claim_line.service_date, 'yyyy-MM')" }),
      col("total_paid", "", { Source_Object: "claim_line", Target_Ordinal: "3", Aggregation_Expression: "SUM(paid_amt)", Aggregation_Group: "member_id, service_month", DQ_Rule_Type: "RANGE", DQ_Rule_Expression: "total_paid BETWEEN 0 AND 10000000" }),
      col("claim_count", "", { Source_Object: "claim_line", Target_Ordinal: "4", Aggregation_Expression: "COUNT(DISTINCT claim_id)", Aggregation_Group: "member_id, service_month" }),
    ],
  },
];

// ── Legacy one-sheet-per-object layout (header Field|Value + TRANSFORMATIONS + DQ blocks) ─────────

export interface LegacyObject {
  sheet: string;
  header: Partial<Record<(typeof HEADER_FIELDS)[number], string>>;
  transformations: Array<Partial<Record<(typeof TRANSFORM_COLUMNS)[number], string>>>;
  dq: Array<Partial<Record<(typeof DQ_COLUMNS)[number], string>>>;
}

export const GOLD_LEGACY_OBJECTS: LegacyObject[] = [
  {
    sheet: "dim_provider",
    header: {
      object_key: "dim_provider", tag: "provider_network", curated_entity: "dim_provider", mart_domain: "provider_network",
      source_object_keys: "provider", enabled: "TRUE", owner: "provider-data-eng", write_behavior: "merge",
      loading_behavior: "DeltaLoad", watermark_column: "last_credentialed_at", watermark_column_type: "datetime",
      business_keys: "npi", data_classification: "confidential",
    },
    transformations: [
      { name: "provider_history", type: "scd2", key_columns: "npi", order_column: "last_credentialed_at", columns: "specialty, network_status, practice_location" },
    ],
    dq: [{ name: "npi_not_null", column: "npi", rule: "IS_NOT_NULL", enforcement: "block" }],
  },
  {
    sheet: "patient_360",
    header: {
      object_key: "patient_360", tag: "population_health", curated_entity: "patient_360", mart_domain: "population_health",
      source_object_keys: "encounter|patient", enabled: "TRUE", owner: "clinical-data-eng", write_behavior: "overwrite",
      loading_behavior: "FullLoad", data_classification: "restricted",
    },
    transformations: [
      { name: "join_patient", type: "join", join_entity: "curated.patient", join_type: "INNER", join_condition: "a.patient_id = b.patient_id", columns: "a.patient_id, a.encounter_id, a.contact_date, a.drg_code, b.mrn, b.birth_date, b.sex_code" },
      // Gold allows no `dedupe` and the legacy layout has no filter column, so "latest encounter per
      // patient" can't be expressed here (logged as a finding) — the mart keeps every joined encounter.
      { name: "final_columns", type: "select_columns", columns: "patient_id, mrn, birth_date, sex_code, encounter_id, contact_date, drg_code" },
    ],
    dq: [
      { name: "p360_mrn_not_null", column: "mrn", rule: "IS_NOT_NULL", enforcement: "block" },
      { name: "p360_patient_unique", column: "patient_id", rule: "UNIQUE", enforcement: "warn" },
    ],
  },
];

// ── Workbook writers ──────────────────────────────────────────────────────────────────────────────

/** Fills a copy of the downloaded Data Modular template: Section 1 by attribute name, Section 2 by header. */
export async function dataModularWorkbook(template: Buffer, obj: DataModularObject): Promise<Buffer> {
  const wb = new ExcelJS.Workbook();
  await wb.xlsx.load(template as unknown as ArrayBuffer);
  const ws = wb.getWorksheet("STTM_Template");
  if (!ws) throw new Error("Downloaded template has no STTM_Template sheet");
  let headerRow = 0;
  const attrRow: Record<string, number> = {};
  for (let r = 1; r <= 200; r++) {
    const a = String(ws.getCell(r, 1).value ?? "").trim();
    if (a === "Record_Type") {
      headerRow = r;
      break;
    }
    if (a) attrRow[a] = r;
  }
  if (!headerRow) throw new Error("Template has no Section 2 header row");
  for (const [name, value] of Object.entries(obj.section1)) {
    if (!attrRow[name]) throw new Error(`Template Section 1 has no attribute ${name}`);
    ws.getCell(attrRow[name], 2).value = value;
  }
  const headerCol: Record<string, number> = {};
  ws.getRow(headerRow).eachCell((cell, c) => (headerCol[String(cell.value ?? "").trim()] = c));
  obj.rows.forEach((row, i) => {
    for (const [name, value] of Object.entries(row)) {
      if (!headerCol[name]) throw new Error(`Template Section 2 has no column ${name}`);
      if (value) ws.getCell(headerRow + 1 + i, headerCol[name]).value = value;
    }
  });
  return Buffer.from(await wb.xlsx.writeBuffer());
}

/** Fills the downloaded legacy template: drops its blank NEW_OBJECT_1 sheet, adds one sheet per object. */
export async function legacyWorkbook(template: Buffer, objects: LegacyObject[]): Promise<Buffer> {
  const wb = new ExcelJS.Workbook();
  await wb.xlsx.load(template as unknown as ArrayBuffer);
  const blank = wb.getWorksheet("NEW_OBJECT_1");
  if (blank) wb.removeWorksheet(blank.id);
  for (const obj of objects) {
    const ws = wb.addWorksheet(obj.sheet);
    ws.getCell(1, 1).value = "Field";
    ws.getCell(1, 2).value = "Value";
    HEADER_FIELDS.forEach((field, i) => {
      ws.getCell(HEADER_START_ROW + i, 1).value = field;
      ws.getCell(HEADER_START_ROW + i, 2).value = obj.header[field] ?? "";
    });
    TRANSFORM_COLUMNS.forEach((c, i) => (ws.getCell(TRANSFORM_HEADER_ROW, i + 1).value = c));
    obj.transformations.forEach((t, r) => TRANSFORM_COLUMNS.forEach((c, i) => t[c] && (ws.getCell(TRANSFORM_DATA_START_ROW + r, i + 1).value = t[c]!)));
    DQ_COLUMNS.forEach((c, i) => (ws.getCell(DQ_HEADER_ROW, i + 1).value = c));
    obj.dq.forEach((d, r) => DQ_COLUMNS.forEach((c, i) => d[c] && (ws.getCell(DQ_DATA_START_ROW + r, i + 1).value = d[c]!)));
  }
  return Buffer.from(await wb.xlsx.writeBuffer());
}

/** Data Modular's layer-wise `.zip` bundle (one workbook per table), zipped with Python's stdlib. */
export function zipBundle(dir: string, files: Record<string, Buffer>, zipName: string): Buffer {
  mkdirSync(dir, { recursive: true });
  for (const [name, data] of Object.entries(files)) writeFileSync(path.join(dir, name), data);
  const zipPath = path.join(dir, zipName);
  const script = "import sys, zipfile, os\nz = zipfile.ZipFile(sys.argv[1], 'w', zipfile.ZIP_DEFLATED)\nfor f in sys.argv[2:]:\n    z.write(f, os.path.basename(f))\nz.close()";
  execFileSync(process.env.PYTHON ?? "python", ["-c", script, zipPath, ...Object.keys(files).map((f) => path.join(dir, f))]);
  return readFileSync(zipPath);
}
