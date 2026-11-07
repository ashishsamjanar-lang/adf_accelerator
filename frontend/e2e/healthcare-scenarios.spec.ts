import { writeFileSync } from "node:fs";
import path from "node:path";
import { expect, test, type Page } from "@playwright/test";
import {
  HC_OUT,
  card,
  continueTo,
  fieldCell,
  fillLinkedService,
  healthcareApi,
  labelled,
  selectProject,
  type ConnectorFill,
  type HealthcareApi,
} from "./support/healthcare";

/**
 * Ten real-world healthcare data-platform scenarios, driven end to end through the real Build wizard
 * UI with dummy (non-PHI) values. Every scenario: fill all 5 steps → Generate the client-side preview
 * (frontend generator) → open every generated file and use "Copy code" → Create pipeline, whose
 * generate/validate is answered by the REAL worker generator + validator (`support/worker_bridge.py`)
 * → assert the worker passed it and that frontend and worker generated the same file set.
 * Generated artifacts land in `test-results/healthcare/<scenario>/` for inspection.
 */

test.use({ permissions: ["clipboard-read", "clipboard-write"] });

// ── Dummy healthcare connections (secrets are Key Vault secret *names* only) ──────────────────────

const CLARITY_SQL: ConnectorFill = {
  connector: "sql_server",
  fields: { "Server name": "clarity-sql01.stmarys.local", "Database name": "Clarity", "User name": "svc_adf_clarity", "Password secret name": "clarity-sql-password" },
};
const HEALTH_LAKE: ConnectorFill = {
  connector: "azure_data_lake_storage_gen2",
  fields: { "Storage account name": "stmaryshealthlake", "Endpoint URL": "https://stmaryshealthlake.dfs.core.windows.net", "Account key secret name": "healthlake-account-key" },
};
const CURATED_SQL: ConnectorFill = {
  connector: "azure_sql_database",
  fields: { "Server name": "stmarys-curated.database.windows.net", "Database name": "clinical_curated", "User name": "svc_adf_loader", "Password secret name": "curated-sql-password" },
};
const CLAIMS_DW: ConnectorFill = {
  connector: "azure_synapse_analytics",
  fields: { "Server name": "stmarys-dw.sql.azuresynapse.net", "Database name": "claims_dw", "User name": "svc_adf_loader", "Password secret name": "synapse-loader-password" },
};

interface ObjectRow {
  object: string;
  watermark?: string;
  loading?: "FullLoad" | "DeltaLoad";
}
interface DqRule {
  name: string;
  column: string;
  rule: string;
  value?: string;
  enforcement?: "warn" | "block";
}
interface Transform {
  name: string;
  type: string;
  fields: Record<string, string>;
}

interface Scenario {
  slug: string;
  title: string;
  name: string;
  pattern?: string;
  layer?: "Silver" | "Gold";
  curatedEntity?: string;
  martDomain?: string;
  sourceSystem?: string;
  dispatch?: Array<[string, string]>;
  source: ConnectorFill;
  objects?: ObjectRow[];
  pastedObjects?: string;
  target: ConnectorFill;
  targetPath?: string;
  sinkFormat?: string;
  writeBehavior?: "Insert" | "Upsert";
  upsertKeys?: string;
  loading?: "incremental" | "full" | "initial" | "composite_watermark";
  watermarkColumns?: string[];
  initialWatermark?: string;
  hardDelete?: "soft_delete" | "cdc";
  transforms?: Transform[];
  dq?: DqRule[];
  checks?: string[];
  triggerType?: "schedule" | "tumbling" | "blobEvents";
  blobTrigger?: { storageLs: string; container: string; endsWith: string };
  emails?: string;
  /** File names the frontend preview must contain (beyond the shared assertions). */
  expectFiles: string[];
}

const SCENARIOS: Scenario[] = [
  {
    slug: "01-ehr-clarity-bronze",
    title: "EHR (Epic Clarity) patient/encounter/provider → Bronze lake, master/child, incremental",
    name: "ehr_clarity_bronze_load",
    source: CLARITY_SQL,
    objects: [
      { object: "dbo.PATIENT", watermark: "UPDATE_DATE" },
      { object: "dbo.PAT_ENC", watermark: "CONTACT_DATE" },
      { object: "dbo.CLARITY_SER", loading: "FullLoad" },
    ],
    target: HEALTH_LAKE,
    targetPath: "/bronze/epic/clarity/",
    loading: "incremental",
    watermarkColumns: ["UPDATE_DATE"],
    dq: [
      { name: "patient_mrn_not_null", column: "PAT_MRN_ID", rule: "IS_NOT_NULL", enforcement: "block" },
      { name: "patient_id_unique", column: "PAT_ID", rule: "UNIQUE", enforcement: "warn" },
    ],
    checks: ["Generate lineage.json"],
    emails: "dataops@stmarys-health.org",
    expectFiles: ["PL_ehr_clarity_bronze_load_master.json", "PL_ehr_clarity_bronze_load_child.json", "adf_control_table.sql", "lineage.json"],
  },
  {
    slug: "02-claims-facets-single",
    title: "Payer claims (Facets on Oracle) → Synapse DW, single metadata-driven pipeline, full snapshot",
    name: "claims_facets_full_load",
    pattern: "single_pipeline",
    source: { connector: "oracle", fields: { "Server / TNS alias": "FACETSPRD", "User name": "svc_adf_claims", "Password secret name": "facets-oracle-password" } },
    pastedObjects: "CLAIMS.CLAIM_HEADER\nCLAIMS.CLAIM_LINE\nCLAIMS.CLAIM_DIAGNOSIS\nCLAIMS.MEMBER_ELIGIBILITY",
    target: CLAIMS_DW,
    targetPath: "/staging/claims/",
    writeBehavior: "Insert",
    loading: "full",
    dq: [
      { name: "claim_amount_range", column: "BILLED_AMT", rule: "RANGE_BETWEEN", value: "0,5000000", enforcement: "warn" },
      { name: "claims_landed", column: "CLAIM_ID", rule: "ROW_COUNT_MIN", value: "1", enforcement: "block" },
    ],
    expectFiles: ["PL_claims_facets_full_load.json", "adf_control_table.sql"],
  },
  {
    slug: "03-hl7-lab-file-arrival",
    title: "HL7 ORU lab results dropped as CSV → lake, independent full load on file arrival",
    name: "lab_results_oru_ingest",
    pattern: "independent_full",
    source: {
      connector: "azure_blob_storage",
      fields: { "Storage account name": "sthl7inbound", "Endpoint URL": "https://sthl7inbound.blob.core.windows.net", "Account key secret name": "hl7-inbound-key" },
    },
    objects: [{ object: "lab_results_oru.csv" }],
    target: HEALTH_LAKE,
    targetPath: "/bronze/lab/oru/",
    sinkFormat: "Parquet",
    loading: "full",
    triggerType: "blobEvents",
    blobTrigger: { storageLs: "LS_azure_blob_storage_source", container: "hl7-inbound", endsWith: ".csv" },
    expectFiles: ["PL_lab_results_oru_ingest.json"],
  },
  {
    slug: "04-pharmacy-dispense-watermark",
    title: "Pharmacy dispensing (PostgreSQL) → ODS, independent watermark delta, upsert",
    name: "pharmacy_dispense_delta",
    pattern: "independent_watermark",
    source: {
      connector: "postgresql",
      fields: { "Server name": "rx-pg.postgres.database.azure.com", "Database name": "pharmacy", "User name": "svc_adf_rx", "Password secret name": "pharmacy-pg-password" },
    },
    objects: [{ object: "public.dispense_event", watermark: "dispensed_at" }],
    target: CURATED_SQL,
    writeBehavior: "Upsert",
    upsertKeys: "dispense_id",
    loading: "incremental",
    watermarkColumns: ["dispensed_at"],
    initialWatermark: "2020-01-01",
    dq: [{ name: "ndc_length", column: "ndc_code", rule: "LENGTH_LE", value: "11", enforcement: "warn" }],
    expectFiles: ["PL_pharmacy_dispense_delta.json", "adf_watermark_table.sql"],
  },
  {
    slug: "05-silver-patient-conformed",
    title: "Silver: conformed patient master (rename, dedupe on MRN, cast types)",
    name: "silver_patient_conform",
    layer: "Silver",
    curatedEntity: "patient",
    source: CLARITY_SQL,
    objects: [{ object: "dbo.PATIENT", watermark: "UPDATE_DATE" }],
    target: CURATED_SQL,
    writeBehavior: "Upsert",
    upsertKeys: "mrn",
    loading: "incremental",
    watermarkColumns: ["UPDATE_DATE"],
    transforms: [
      { name: "conform_patient_columns", type: "conform_columns", fields: { "Column mapping": "PAT_MRN_ID:mrn, PAT_FIRST_NAME:first_name, PAT_LAST_NAME:last_name, BIRTH_DATE:birth_date, SEX_C:sex_code, UPDATE_DATE:updated_at" } },
      // The order column must survive the conform step's projection (checkTransformationChainColumns).
      { name: "dedupe_by_mrn", type: "dedupe", fields: { "Key column(s)": "mrn", "Order column (latest wins)": "updated_at" } },
      { name: "cast_patient_types", type: "cast_schema", fields: { Casts: "birth_date:DATE, sex_code:VARCHAR(1)" } },
    ],
    dq: [{ name: "mrn_not_null", column: "mrn", rule: "IS_NOT_NULL", enforcement: "block" }],
    checks: ["Apply standard audit columns for this layer (silver)"],
    expectFiles: ["PL_silver_patient_conform_master.json", "PL_silver_patient_conform_child.json"],
  },
  {
    slug: "06-gold-readmissions-mart",
    title: "Gold: 30-day readmissions mart (join encounters, aggregate, project)",
    name: "gold_readmission_30d",
    layer: "Gold",
    curatedEntity: "readmission_30d",
    martDomain: "clinical_quality",
    source: CURATED_SQL,
    objects: [{ object: "curated.encounter", loading: "FullLoad" }],
    target: CLAIMS_DW,
    loading: "full",
    transforms: [
      {
        name: "join_prior_admission",
        type: "join",
        fields: {
          "Join entity/table": "curated.patient",
          "Join type (INNER/LEFT)": "INNER",
          "Join condition": "a.patient_id = b.patient_id",
          "Output columns": "a.patient_id, a.encounter_id, a.discharge_date, a.drg_code, b.birth_date",
        },
      },
      { name: "count_readmissions", type: "aggregate", fields: { "Group by column(s)": "patient_id, drg_code", "Aggregate expression(s)": "COUNT(*) AS readmit_count" } },
      { name: "final_mart_columns", type: "select_columns", fields: { "Final column list": "patient_id, drg_code, readmit_count" } },
    ],
    dq: [{ name: "readmit_count_range", column: "readmit_count", rule: "RANGE_BETWEEN", value: "0,100", enforcement: "warn" }],
    expectFiles: ["PL_gold_readmission_30d_master.json"],
  },
  {
    slug: "07-gold-provider-scd2",
    title: "Gold: provider dimension with SCD2 history (credentialing system on MySQL)",
    name: "gold_provider_dim_scd2",
    layer: "Gold",
    curatedEntity: "provider",
    martDomain: "provider_network",
    source: {
      connector: "mysql",
      fields: { "Server name": "cred-mysql.stmarys.local", "Database name": "credentialing", "User name": "svc_adf_cred", "Password secret name": "credentialing-mysql-password" },
    },
    objects: [{ object: "credentialing.provider", watermark: "last_credentialed_at" }],
    target: CURATED_SQL,
    loading: "incremental",
    watermarkColumns: ["last_credentialed_at"],
    transforms: [
      {
        name: "provider_history",
        type: "scd2",
        fields: { "Natural key column(s)": "npi", "Change-detection column": "last_credentialed_at", "Tracked columns": "specialty, network_status, practice_location" },
      },
    ],
    dq: [{ name: "npi_length", column: "npi", rule: "LENGTH_LE", value: "10", enforcement: "block" }],
    expectFiles: ["PL_gold_provider_dim_scd2_child.json"],
  },
  {
    slug: "08-adt-composite-watermark",
    title: "ADT (admit/discharge/transfer) events, composite watermark (event date + sequence)",
    name: "adt_events_composite_load",
    sourceSystem: "cerner",
    source: { ...CLARITY_SQL, fields: { ...CLARITY_SQL.fields, "Server name": "cerner-adt.stmarys.local", "Database name": "ADT_STAGE" } },
    objects: [{ object: "adt.ADT_EVENT" }, { object: "adt.ADT_EVENT_DETAIL" }],
    target: HEALTH_LAKE,
    targetPath: "/bronze/cerner/adt/",
    loading: "composite_watermark",
    watermarkColumns: ["event_date", "event_seq"],
    triggerType: "tumbling",
    expectFiles: ["pl_master.json", "pl_cerner_child.json"],
  },
  {
    slug: "09-appointments-soft-delete",
    title: "Outpatient appointments → ODS with soft-delete reconciliation (cancelled bookings)",
    name: "appointment_sched_sync",
    source: { ...CLARITY_SQL, fields: { ...CLARITY_SQL.fields, "Database name": "Cadence" } },
    objects: [{ object: "dbo.APPOINTMENT", watermark: "LAST_CHANGED" }],
    target: CURATED_SQL,
    writeBehavior: "Upsert",
    upsertKeys: "appointment_id",
    loading: "incremental",
    watermarkColumns: ["LAST_CHANGED"],
    hardDelete: "soft_delete",
    dq: [
      { name: "appt_fresh", column: "LAST_CHANGED", rule: "FRESHNESS_HOURS", value: "24", enforcement: "warn" },
      { name: "duration_range", column: "DURATION_MIN", rule: "RANGE_BETWEEN", value: "5,480", enforcement: "warn" },
    ],
    triggerType: "schedule",
    expectFiles: ["PL_appointment_sched_sync_child.json"],
  },
  {
    slug: "10-his-department-dispatch",
    title: "Hospital information system: one master dispatching to lab + radiology children",
    name: "his_department_dispatch",
    dispatch: [
      ["lab", "PL_lab_child"],
      ["radiology", "PL_radiology_child"],
    ],
    source: { ...CLARITY_SQL, fields: { ...CLARITY_SQL.fields, "Server name": "his-sql.stmarys.local", "Database name": "HIS" } },
    objects: [{ object: "lab.ORDER_RESULT", watermark: "RESULT_DTTM" }, { object: "rad.IMAGING_STUDY", watermark: "STUDY_DTTM" }],
    target: HEALTH_LAKE,
    targetPath: "/bronze/his/",
    loading: "incremental",
    watermarkColumns: ["RESULT_DTTM"],
    checks: ["Generate arm-template-parameters-definition.json"],
    expectFiles: ["PL_his_department_dispatch_master.json"],
  },
];

// ── Step drivers ─────────────────────────────────────────────────────────────────────────────────

async function fillObjects(page: Page, s: Scenario) {
  const objectsCard = card(page, "Source objects — input files / table names");
  if (s.pastedObjects) {
    await objectsCard.getByRole("tab", { name: /Upload file/ }).click();
    await labelled(objectsCard, "…or paste the list").fill(s.pastedObjects);
    const count = s.pastedObjects.split("\n").length;
    await objectsCard.getByRole("button", { name: `Import ${count} objects` }).click();
    await objectsCard.getByRole("tab", { name: /Fill form/ }).click();
    // Importing appends: drop the wizard's default placeholder row if it is still there.
    const objectInputs = objectsCard.getByPlaceholder("e.g. customer, ORDERS, or file.csv");
    if ((await objectInputs.first().inputValue()) === "dbo.customer") await objectsCard.getByTitle("Remove object row").first().click();
    await expect(objectInputs).toHaveCount(count);
    return;
  }
  const rows = s.objects ?? [];
  for (let i = 0; i < rows.length; i++) {
    if (i > 0) await objectsCard.getByRole("button", { name: /Add object/ }).click();
    const row = objectsCard.locator(".editor-row.three").nth(i);
    await row.getByPlaceholder("e.g. customer, ORDERS, or file.csv").fill(rows[i].object);
    if (rows[i].watermark) await row.getByPlaceholder("Watermark column (optional)").fill(rows[i].watermark!);
    if (rows[i].loading) await row.locator("select").selectOption(rows[i].loading!);
  }
}

async function fillWatermarks(page: Page, columns: string[]) {
  const wmCard = card(page, "Watermark & control table");
  const inputs = wmCard.getByPlaceholder("e.g. modified_date");
  for (let i = 0; i < columns.length; i++) {
    if (i >= (await inputs.count())) await wmCard.getByRole("button", { name: /Add watermark column/ }).click();
    await inputs.nth(i).fill(columns[i]);
  }
}

async function fillDq(page: Page, rules: DqRule[]) {
  const dqCard = card(page, "Validation & data quality");
  for (let i = 0; i < rules.length; i++) {
    await dqCard.getByRole("button", { name: /Add rule/ }).click();
    const row = dqCard.locator(".editor-row.five").nth(i);
    await row.locator("input").nth(0).fill(rules[i].name);
    await row.locator("input").nth(1).fill(rules[i].column);
    await row.locator("select").nth(0).selectOption(rules[i].rule);
    if (rules[i].value) await row.locator("input").nth(2).fill(rules[i].value!);
    await row.locator("select").nth(1).selectOption(rules[i].enforcement ?? "warn");
  }
}

async function fillTransforms(page: Page, transforms: Transform[]) {
  const tCard = card(page, "Transformations");
  for (let i = 0; i < transforms.length; i++) {
    await tCard.getByRole("button", { name: /Add transformation/ }).click();
    const row = tCard.locator(".transform-row").nth(i);
    await row.locator(".transform-row-head input").fill(transforms[i].name);
    await row.locator(".transform-row-head select").selectOption(transforms[i].type);
    for (const [label, value] of Object.entries(transforms[i].fields)) await labelled(row, label).fill(value);
  }
}

async function basics(page: Page, s: Scenario) {
  await page.goto("/");
  await page.getByRole("button", { name: /^Build/ }).first().click();
  await expect(page.getByText("Step 1 of 5 · Basics")).toBeVisible();
  await selectProject(page);
  if (s.layer) {
    await page.locator(".layer-card", { hasText: s.layer }).click();
    await labelled(page, "Curated entity name").fill(s.curatedEntity!);
    if (s.martDomain) await labelled(page, "Mart domain").fill(s.martDomain);
  }
  await labelled(page, "Pipeline name").fill(s.name);
  if (s.pattern) await labelled(page, "Pipeline pattern").selectOption(s.pattern);
  if (s.sourceSystem) await labelled(page, "Source system (optional)").fill(s.sourceSystem);
  for (let i = 0; i < (s.dispatch ?? []).length; i++) {
    await page.getByRole("button", { name: /Add dispatch case/ }).click();
    await page.getByLabel(`TaskGroup value ${i + 1}`).fill(s.dispatch![i][0]);
    await page.getByLabel(`Child pipeline ${i + 1}`).fill(s.dispatch![i][1]);
  }
}

async function sourceStep(page: Page, s: Scenario) {
  await continueTo(page, "Step 2 of 5 · Source");
  await fillLinkedService(page, s.source);
  await fillObjects(page, s);
}

async function targetStep(page: Page, s: Scenario) {
  await continueTo(page, "Step 3 of 5 · Target");
  await fillLinkedService(page, s.target);
  if (s.targetPath) await labelled(page, "Target path").fill(s.targetPath);
  if (s.sinkFormat) await labelled(page, "Sink file format").selectOption(s.sinkFormat);
  if (s.writeBehavior) await labelled(page, "Write behavior (relational target)").selectOption(s.writeBehavior);
  if (s.upsertKeys) await labelled(page, "Upsert key column(s)").fill(s.upsertKeys);
}

async function settingsStep(page: Page, s: Scenario) {
  await continueTo(page, "Step 4 of 5 · Pipeline Settings");
  if (s.loading) await labelled(page, "Loading behavior").selectOption(s.loading);
  if (s.loading === "composite_watermark" && (await labelled(page, "Watermark strategy").count())) {
    await labelled(page, "Watermark strategy").selectOption("composite");
  }
  if (s.watermarkColumns) await fillWatermarks(page, s.watermarkColumns);
  if (s.initialWatermark) await labelled(page, "Initial watermark value").fill(s.initialWatermark);
  if (s.hardDelete) await labelled(page, "Hard-delete handling").selectOption(s.hardDelete);
  if (s.transforms) await fillTransforms(page, s.transforms);
  if (s.dq) await fillDq(page, s.dq);
  for (const check of s.checks ?? []) {
    await page.locator("label.form-check", { hasText: check }).locator("input[type=checkbox]").check();
  }
  if (s.emails) await labelled(page, "Notification emails").fill(s.emails);
  if (s.triggerType) await labelled(page, "Trigger type").selectOption(s.triggerType);
  if (s.blobTrigger) {
    await labelled(page, "Storage linked service").fill(s.blobTrigger.storageLs);
    await labelled(page, "Container name").fill(s.blobTrigger.container);
    await labelled(page, "Blob path ends with (optional)").fill(s.blobTrigger.endsWith);
  }
}

/** Generate the client-side preview, open + copy every file, then Create (→ real worker). */
async function reviewAndCreate(page: Page, s: Scenario, api: HealthcareApi, outSlug = s.slug) {
  await continueTo(page, "Step 5 of 5 · Review & Generate");
  await expect(page.getByText("Specification is valid. Generate the ADF artifacts to continue.")).toBeVisible();
  await page.getByRole("button", { name: /Generate pipeline code/ }).click();
  const names = page.locator(".artifact-select .artifact-name");
  await expect(names.first()).toBeVisible();
  const previewFiles = await names.allInnerTexts();
  for (const expected of s.expectFiles) expect(previewFiles.some((f) => f.endsWith(expected)), `preview has ${expected}`).toBe(true);

  // Every artifact button opens its code; "Copy code" works for each.
  const buttons = page.locator(".artifact-select");
  for (let i = 0; i < (await buttons.count()); i++) {
    await buttons.nth(i).click();
    const code = page.locator("pre.code-viewer");
    await expect(code).not.toBeEmpty();
    await page.getByRole("button", { name: /Copy code|Copied!/ }).click();
    const shown = await code.innerText();
    // The OS clipboard may hand text back with CRLF line endings (Windows) — compare normalized.
    const lines = (t: string) => t.replace(/\r/g, "").split("\n").map((l) => l.trimEnd()).join("\n").trim();
    await expect.poll(async () => lines(await page.evaluate(() => navigator.clipboard.readText()))).toBe(lines(shown));
    // Secrets are Key Vault references only — no literal password/key values in any generated file.
    expect(shown, `${await buttons.nth(i).locator(".artifact-name").innerText()} has no inline secret`).not.toMatch(/"(password|accountKey|clientSecret|servicePrincipalKey)"\s*:\s*"[^@{]/i);
  }

  await page.getByRole("button", { name: /^Create pipeline$/ }).click();
  await expect(page.getByRole("heading", { name: "Validation results" })).toBeVisible();

  const worker = api.worker();
  expect(worker, "worker generator ran").not.toBeNull();
  const summary = {
    scenario: s.title,
    pipeline: s.name,
    frontendPreviewFiles: previewFiles,
    workerFiles: worker!.artifacts.map((a) => a.fileName),
    draftIssues: worker!.draftIssues,
    validation: worker!.validation,
  };
  writeFileSync(path.join(HC_OUT, outSlug, "_summary.json"), JSON.stringify(summary, null, 2));

  expect(worker!.draftIssues, "worker accepted the draft").toEqual([]);
  const errors = (worker!.validation?.issues ?? []).filter((i) => i.severity === "error");
  expect(errors, "worker validation errors").toEqual([]);
  expect(worker!.validation?.state).toBe("passed");
  await expect(page.getByText("Validated", { exact: true })).toBeVisible();

  // Frontend preview and worker port must generate the same file set (manifest.json is worker/backend-only).
  const norm = (files: string[]) => files.map((f) => f.split("/").pop()!).filter((f) => f !== "manifest.json").sort();
  expect.soft(norm(previewFiles), "frontend vs worker file set").toEqual(norm(summary.workerFiles));

  // The saved draft carries the healthcare values the user typed.
  const draft = api.draft() as { name: string; sourceLinkedService: { fields: Record<string, string> } };
  expect(draft.name).toBe(s.name);
  expect(Object.values(draft.sourceLinkedService.fields)).toContain(Object.values(s.source.fields)[0]);
  return previewFiles;
}

for (const s of SCENARIOS) {
  test(`${s.slug}: ${s.title}`, async ({ page }) => {
    const pageErrors: string[] = [];
    page.on("pageerror", (e) => pageErrors.push(e.message));
    const api = await healthcareApi(page, s.slug);
    await basics(page, s);
    await sourceStep(page, s);
    await targetStep(page, s);
    await settingsStep(page, s);
    await reviewAndCreate(page, s, api);
    expect(pageErrors, "uncaught page errors").toEqual([]);
  });
}

// ── Scenario 1 continued: the rest of the lifecycle — Drafts → Catalog → Push to Git → PR ────────

test("01b-ehr lifecycle: Drafts → Review artifacts → Catalog select all → Push to Git → PR", async ({ page }) => {
  const s = SCENARIOS[0];
  const api = await healthcareApi(page, `${s.slug}-lifecycle`);
  await basics(page, s);
  await sourceStep(page, s);
  await targetStep(page, s);
  await settingsStep(page, s);
  await reviewAndCreate(page, s, api, `${s.slug}-lifecycle`);

  await page.getByRole("button", { name: /Continue to Drafts/ }).click();
  const row = page.locator("main").getByText(s.name).first();
  await expect(row).toBeVisible();
  await page.getByRole("button", { name: "Review artifacts" }).first().click();

  const workerFiles = api.worker()!.artifacts.length;
  await page.getByRole("checkbox", { name: "Select all matching artifacts" }).check();
  await page.getByRole("button", { name: new RegExp(`^Push \\(${workerFiles}\\)`) }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByPlaceholder("feature/my-pipeline").fill("feature/ehr-clarity-bronze");
  await dialog.getByRole("button", { name: /Push & create PR/ }).click();
  await expect(dialog.getByText("Pull request created")).toBeVisible();
  const push = api.calls.find((c) => c.method === "POST" && c.path === "/git/push")?.body as { artifactIds: string[]; featureBranch: string; baseBranch: string };
  expect(push.artifactIds).toHaveLength(workerFiles);
  expect(push).toMatchObject({ featureBranch: "feature/ehr-clarity-bronze", baseBranch: "dev" });
});

// ── Negative paths: the wizard must block bad healthcare input before anything is generated ─────

test("11-negative: invalid names, missing secrets and a missing Silver transformation are blocked", async ({ page }) => {
  const api = await healthcareApi(page, "11-negative");
  await page.goto("/");
  await page.getByRole("button", { name: /^Build/ }).first().click();
  await selectProject(page);

  // Bad pipeline name (spaces, capitals, punctuation) blocks Basics.
  await labelled(page, "Pipeline name").fill("Patient Billing!");
  await page.getByRole("button", { name: /^Continue$/ }).last().click();
  await expect(page.getByText("Step 1 of 5 · Basics")).toBeVisible();
  await expect(page.locator("main .text-red").first()).toBeVisible();
  await labelled(page, "Pipeline name").fill("patient_billing_load");

  // Silver without a curated entity is blocked too.
  await page.locator(".layer-card", { hasText: "Silver" }).click();
  await page.getByRole("button", { name: /^Continue$/ }).last().click();
  await expect(page.getByText("Step 1 of 5 · Basics")).toBeVisible();
  await labelled(page, "Curated entity name").fill("billing_account");

  // Source with no Key Vault secret name is blocked (secrets are references, never values).
  await continueTo(page, "Step 2 of 5 · Source");
  await fillLinkedService(page, { ...CLARITY_SQL, fields: { ...CLARITY_SQL.fields, "Password secret name": "" } });
  await page.getByRole("button", { name: /^Continue$/ }).last().click();
  await expect(page.getByText("Step 2 of 5 · Source")).toBeVisible();
  await expect(page.getByText("Password secret name is required.").first()).toBeVisible();
  await fieldCell(page, "Password secret name").fill("billing-sql-password");
  await continueTo(page, "Step 3 of 5 · Target");
  await fillLinkedService(page, CURATED_SQL);
  await continueTo(page, "Step 4 of 5 · Pipeline Settings");

  // Silver with zero transformations must not reach Review.
  await page.getByRole("button", { name: /^Continue$/ }).last().click();
  await expect(page.getByText("Step 4 of 5 · Pipeline Settings")).toBeVisible();
  await fillTransforms(page, [{ name: "dedupe_account", type: "dedupe", fields: { "Key column(s)": "account_id", "Order column (latest wins)": "UPDATE_DATE" } }]);
  await continueTo(page, "Step 5 of 5 · Review & Generate");

  // Back to fields and forward again keeps everything (no lost state).
  await page.getByRole("button", { name: /Back/ }).last().click();
  await expect(page.getByText("Step 4 of 5 · Pipeline Settings")).toBeVisible();
  await expect(card(page, "Transformations").locator(".transform-row")).toHaveCount(1);
  await continueTo(page, "Step 5 of 5 · Review & Generate");
  expect(api.calls.some((c) => c.method === "POST" && c.path === "/pipelines"), "nothing saved yet").toBe(false);
});
