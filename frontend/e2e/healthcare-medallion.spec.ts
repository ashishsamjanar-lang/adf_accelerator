import { spawn, type ChildProcess } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, readdirSync, statSync, writeFileSync } from "node:fs";
import path from "node:path";
import { expect, test, type Page } from "@playwright/test";
import { E2E_PROJECT } from "./support/api-stub";
import {
  HC_OUT,
  card,
  continueTo,
  fillLinkedService,
  healthcareApi,
  labelled,
  selectProject,
  type ConnectorFill,
  type HealthcareApi,
} from "./support/healthcare";
import {
  BRONZE_CSV,
  BRONZE_OBJECT_COUNT,
  GOLD_LEGACY_OBJECTS,
  GOLD_OBJECTS,
  SILVER_BAD_OBJECTS,
  SILVER_OBJECTS,
  dataModularWorkbook,
  legacyWorkbook,
  zipBundle,
} from "./support/healthcare-sttm";

/**
 * Healthcare medallion journey, end to end from the frontend with dummy (non-PHI) metadata:
 *
 *  0. Onboarding — Bronze objects from a CSV; Silver objects from Data Modular STTM workbooks (filled
 *     into the template the UI hands out, uploaded as one .zip bundle, incl. two bad ones); Gold objects
 *     from Data Modular workbooks plus a legacy one-sheet-per-object workbook. All of this hits the REAL
 *     backend (`support/backend_bridge.py`, throwaway SQLite): real parser, key resolution, commit.
 *  1–10. Build — two Bronze, four Silver, four Gold pipelines, each built from those registries in the
 *     real wizard (Bronze objects via the onboarding picker, Silver/Gold via the registry picker), then
 *     generated + validated by the REAL worker (`support/worker_bridge.py`).
 *
 * Hard assertions cover what must work; `probe`s record behavior that looks wrong in the generated SQL
 * as findings (`test-results/healthcare-medallion/_findings.json`) instead of failing the run.
 */

const OUT = path.join(path.dirname(HC_OUT), "healthcare-medallion");
const BACKEND_PORT = Number(process.env.HC_BACKEND_PORT ?? 8799);
const BACKEND = `http://127.0.0.1:${BACKEND_PORT}`;
const PYTHON = process.env.PYTHON ?? "python";

// Not serial: one failing scenario must not skip the rest. A worker restarted after a failure re-runs
// beforeAll, so the metastore file is keyed to the runner process and survives the restart.

let backend: ChildProcess | null = null;
const FINDINGS = path.join(OUT, "_findings.json");

test.beforeAll(async () => {
  mkdirSync(OUT, { recursive: true });
  // A fresh run (new runner process) starts a fresh findings file; a restarted worker appends to it.
  const marker = path.join(OUT, `.run-${process.ppid}`);
  if (!existsSync(marker)) {
    writeFileSync(FINDINGS, "{}");
    writeFileSync(marker, "");
  }
  backend = spawn(PYTHON, [path.resolve("e2e/support/backend_bridge.py"), String(BACKEND_PORT), path.join(OUT, `metastore-${process.ppid}.db`), "--keep"], { stdio: ["ignore", "pipe", "pipe"] });
  let log = "";
  await new Promise<void>((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(`backend_bridge did not start:\n${log}`)), 60_000);
    backend!.stdout!.on("data", (d) => {
      log += String(d);
      if (log.includes("READY")) {
        clearTimeout(timer);
        resolve();
      }
    });
    backend!.stderr!.on("data", (d) => (log += String(d)));
    backend!.on("exit", (code) => reject(new Error(`backend_bridge exited ${code}:\n${log}`)));
  });
});

test.afterAll(() => {
  backend?.kill();
});

async function backendGet<T>(p: string): Promise<T> {
  const res = await fetch(`${BACKEND}/api/v1${p}`);
  expect(res.ok, `GET ${p}`).toBe(true);
  return (await res.json()) as T;
}

function note(slug: string, finding: string) {
  const findings = JSON.parse(readFileSync(FINDINGS, "utf-8")) as Record<string, string[]>;
  if (!(findings[slug] ??= []).includes(finding)) findings[slug].push(finding);
  writeFileSync(FINDINGS, JSON.stringify(findings, null, 2));
}

// ── Dummy healthcare connections (secrets are Key Vault secret *names* only) ──────────────────────

const CLARITY_SQL: ConnectorFill = {
  connector: "sql_server",
  fields: { "Server name": "clarity-sql01.stmarys.local", "Database name": "Clarity", "User name": "svc_adf_clarity", "Password secret name": "clarity-sql-password" },
};
const FACETS_ORACLE: ConnectorFill = { connector: "oracle", fields: { "Server / TNS alias": "FACETSPRD", "User name": "svc_adf_claims", "Password secret name": "facets-oracle-password" } };
const CRED_MYSQL: ConnectorFill = {
  connector: "mysql",
  fields: { "Server name": "cred-mysql.stmarys.local", "Database name": "credentialing", "User name": "svc_adf_cred", "Password secret name": "credentialing-mysql-password" },
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

// ── 0. Onboarding: Bronze CSV, Silver .zip bundle, Gold workbooks — all through the real backend ─────

async function openOnboarding(page: Page, tab: "Bronze" | "Silver" | "Gold") {
  await page.goto("/");
  await page.getByRole("button", { name: /^Onboarding/ }).first().click();
  if (tab !== "Bronze") await page.getByRole("button", { name: tab, exact: true }).click();
}

async function download(page: Page, trigger: () => Promise<void>): Promise<Buffer> {
  const [dl] = await Promise.all([page.waitForEvent("download"), trigger()]);
  return readFileSync((await dl.path())!);
}

async function uploadSttm(page: Page, name: string, buffer: Buffer) {
  await page.getByRole("button", { name: /STTM upload/ }).click();
  const mime = name.endsWith(".zip") ? "application/zip" : "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
  await page.locator('input[type="file"]').setInputFiles({ name, mimeType: mime, buffer });
}

interface SheetOut {
  sheetName: string;
  valid: boolean;
  objectKey: string | null;
  issues: Array<{ severity: string; message: string }>;
  parsed: Record<string, unknown> | null;
}
interface ImportOut {
  sheets: SheetOut[];
}
interface RegistryOut {
  objectKey: string;
  enabled: boolean;
  tag: string;
  curatedEntity: string;
  martDomain: string | null;
  sourceObjectKeys: string[];
  targetTable: string;
  transformations: Array<Record<string, string>>;
  dataQualityRules: Array<Record<string, string>>;
  businessKeys: string[];
  watermarkColumn: string | null;
  writeBehavior: string;
  loadingBehavior: string;
}

test("00-onboard: Bronze CSV + Silver/Gold STTM (Data Modular bundle + legacy workbook) through the real backend", async ({ page }) => {
  const slug = "00-onboard";
  const api = await healthcareApi(page, slug, { backendUrl: BACKEND });

  // Bronze — §26 CSV import → preview → commit.
  await openOnboarding(page, "Bronze");
  await page.getByRole("button", { name: /CSV \/ Excel import/ }).click();
  await page.locator("textarea").first().fill(BRONZE_CSV);
  await expect(page.getByText(new RegExp(`${BRONZE_OBJECT_COUNT} objects found`))).toBeVisible();
  const bulkResp = page.waitForResponse((r) => r.url().endsWith("/onboarding/objects/bulk") && r.request().method() === "POST");
  await page.getByRole("button", { name: new RegExp(`Add ${BRONZE_OBJECT_COUNT} objects to registry`) }).click();
  expect((await bulkResp).ok()).toBe(true);
  await expect(page.getByRole("cell", { name: "epic.PATIENT", exact: true }).first()).toBeVisible();
  const bronze = await backendGet<Array<{ object_key: string }>>(`/projects/${E2E_PROJECT.id}/onboarding/objects`);
  expect(bronze.map((b) => b.object_key).sort()).toContain("epic.PATIENT");
  expect(bronze).toHaveLength(BRONZE_OBJECT_COUNT);

  // Silver — download the Data Modular template from the UI, fill one workbook per table, zip them.
  await openOnboarding(page, "Silver");
  const dmTemplate = await download(page, () => page.getByRole("link", { name: /STTM template \(\.xlsx\)/ }).click());
  const silverFiles: Record<string, Buffer> = {};
  for (const obj of [...SILVER_OBJECTS, ...SILVER_BAD_OBJECTS]) silverFiles[`${obj.file}.xlsx`] = await dataModularWorkbook(dmTemplate, obj);
  const bundle = zipBundle(path.join(OUT, slug, "silver"), silverFiles, "silver_healthcare_bundle.zip");
  const importResp = page.waitForResponse((r) => r.url().endsWith("/silver-objects/sttm-import") && r.request().method() === "POST");
  await uploadSttm(page, "silver_healthcare_bundle.zip", bundle);
  const silverImport = (await (await importResp).json()) as ImportOut;
  writeFileSync(path.join(OUT, slug, "silver_import.json"), JSON.stringify(silverImport, null, 2));

  const sheet = (name: string) => silverImport.sheets.find((s) => s.sheetName === name)!;
  for (const obj of SILVER_OBJECTS) expect(sheet(obj.file).valid, `${obj.file}: ${JSON.stringify(sheet(obj.file).issues)}`).toBe(true);
  expect(sheet("silver_wrong_layer").valid, "Target_Layer BRONZE is rejected").toBe(false);
  // DMT-12: bare Source_Object names resolve to the Bronze onboarding keys at preview.
  expect(sheet("silver_patient").parsed!.sourceObjectKeys).toEqual(["epic.PATIENT"]);
  expect(sheet("silver_claim_line").parsed!.sourceObjectKeys).toEqual(["facets.CLAIM_LINE"]);
  expect(sheet("silver_orphan_source").issues.some((i) => /not found in this project's Bronze/.test(i.message)), "orphan warned at preview").toBe(true);

  await page.getByRole("button", { name: /Commit \d+ objects?/ }).click();
  await expect(page.getByText(/Committed 5 objects; 1 skipped — silver_orphan_source: Source object key\(s\) not found/)).toBeVisible();
  const silver = await backendGet<RegistryOut[]>(`/projects/${E2E_PROJECT.id}/silver-objects`);
  expect(silver.map((s) => s.objectKey).sort()).toEqual(["claim_line", "encounter", "lab_result", "patient", "provider"]);
  writeFileSync(path.join(OUT, slug, "silver_registry.json"), JSON.stringify(silver, null, 2));
  const lab = silver.find((s) => s.objectKey === "lab_result")!;
  expect(lab.enabled, "Mapping_Status DRAFT imports disabled (DMT-10)").toBe(false);

  // Gold — Data Modular workbooks one at a time, then the legacy multi-sheet workbook.
  await openOnboarding(page, "Gold");
  for (const obj of GOLD_OBJECTS) {
    const resp = page.waitForResponse((r) => r.url().endsWith("/mart-objects/sttm-import") && r.request().method() === "POST");
    await uploadSttm(page, `${obj.file}.xlsx`, await dataModularWorkbook(dmTemplate, obj));
    const imp = (await (await resp).json()) as ImportOut;
    writeFileSync(path.join(OUT, slug, `${obj.file}_import.json`), JSON.stringify(imp, null, 2));
    expect(imp.sheets[0].valid, `${obj.file}: ${JSON.stringify(imp.sheets[0].issues)}`).toBe(true);
    await page.getByRole("button", { name: /Commit 1 object/ }).click();
    await expect(page.getByText(/Committed 1 object/)).toBeVisible();
  }
  const legacyTemplate = await download(page, () => page.getByRole("button", { name: /Legacy gold template/ }).click());
  const legacyResp = page.waitForResponse((r) => r.url().endsWith("/mart-objects/sttm-import") && r.request().method() === "POST");
  await uploadSttm(page, "gold_legacy_healthcare.xlsx", await legacyWorkbook(legacyTemplate, GOLD_LEGACY_OBJECTS));
  const legacyImport = (await (await legacyResp).json()) as ImportOut;
  writeFileSync(path.join(OUT, slug, "gold_legacy_import.json"), JSON.stringify(legacyImport, null, 2));
  for (const s of legacyImport.sheets) expect(s.valid, `${s.sheetName}: ${JSON.stringify(s.issues)}`).toBe(true);
  await page.getByRole("button", { name: /Commit 2 objects/ }).click();
  await expect(page.getByText(/Committed 2 objects/)).toBeVisible();

  const gold = await backendGet<RegistryOut[]>(`/projects/${E2E_PROJECT.id}/mart-objects`);
  expect(gold.map((g) => g.objectKey).sort()).toEqual(["claims_cost_monthly", "dim_provider", "patient_360", "readmission_30d"]);
  writeFileSync(path.join(OUT, slug, "gold_registry.json"), JSON.stringify(gold, null, 2));
  // Gold's upstream keys point at Silver registry objects.
  expect(gold.find((g) => g.objectKey === "readmission_30d")!.sourceObjectKeys).toEqual(["encounter"]);
  expect(gold.find((g) => g.objectKey === "readmission_30d")!.targetTable).toBe("mart.clinical_quality_readmission_30d");

  // Findings visible from the registry itself.
  const allIssues = [...silverImport.sheets, ...legacyImport.sheets].flatMap((s) => s.issues.map((i) => `${s.sheetName}: [${i.severity}] ${i.message}`));
  writeFileSync(path.join(OUT, slug, "import_issues.txt"), allIssues.join("\n"));
  expect(api.calls.some((c) => c.path.endsWith("/silver-objects/sttm-import"))).toBe(true);
});

// ── 1–10. Build pipelines from the registries ─────────────────────────────────────────────────────

type Files = Map<string, string>;

interface Scenario {
  slug: string;
  title: string;
  name: string;
  layer: "bronze" | "silver" | "gold";
  /** Silver/Gold registry object picked on Basics. */
  registryKey?: string;
  pattern?: string;
  sourceSystem?: string;
  source: ConnectorFill;
  /** The connector the upstream layer actually lives on; noted when Build can't offer it as a source. */
  preferredSource?: string;
  /** Bronze onboarding rows to tick in the Source step's picker (by source_object). */
  bronzePick?: string[];
  /** Objects typed by hand (Gold reads Silver tables, which the Bronze picker never lists). */
  manualObjects?: string[];
  target: ConnectorFill;
  targetPath?: string;
  upsertKeys?: string;
  loading?: "incremental" | "full";
  watermark?: string;
  expectFiles: string[];
  /** Hard checks on the generated files (worker output). */
  verify?: (files: Files) => void;
  /** Soft checks: return findings (strings) for behavior that looks wrong. */
  probe?: (files: Files) => string[];
}

const allText = (files: Files) => [...files.values()].join("\n");
const scripts = (files: Files) =>
  [...allText(files).matchAll(/"text":\s*"((?:[^"\\]|\\.)*)"/g)].map((m) => JSON.parse(`"${m[1]}"`) as string);

const SCENARIOS: Scenario[] = [
  {
    slug: "01-bronze-epic-clarity",
    title: "Bronze: Epic Clarity (5 onboarded objects) → health lake, master/child, mixed full/delta",
    name: "bronze_epic_clarity",
    layer: "bronze",
    sourceSystem: "epic",
    source: CLARITY_SQL,
    bronzePick: ["PATIENT", "PAT_ENC", "CLARITY_SER", "ORDER_RESULTS", "ICD10_CODE"],
    target: HEALTH_LAKE,
    targetPath: "/bronze/epic/clarity/",
    loading: "incremental",
    watermark: "UPDATE_DATE",
    expectFiles: ["pl_master.json", "pl_epic_child.json", "adf_control_table.sql"],
    verify: (files) => {
      const control = files.get("sql/adf_control_table.sql") ?? allText(files);
      for (const wm of ["UPDATE_DATE", "CONTACT_DATE", "RESULT_TIME"]) expect(control, `control table has ${wm}`).toContain(wm);
      expect(control).toContain("PAT_ENC");
    },
  },
  {
    slug: "02-bronze-facets-claims",
    title: "Bronze: Facets claims (Oracle) → Synapse, single metadata-driven pipeline, full",
    name: "bronze_facets_claims",
    layer: "bronze",
    pattern: "single_pipeline",
    sourceSystem: "facets",
    source: FACETS_ORACLE,
    bronzePick: ["CLAIM_HEADER", "CLAIM_LINE", "MEMBER"],
    target: CLAIMS_DW,
    loading: "full",
    expectFiles: ["PL_bronze_facets_claims.json", "adf_control_table.sql"],
    verify: (files) => expect(allText(files)).toContain("CLAIM_LINE"),
    probe: (files) => {
      const out: string[] = [];
      const control = [...files.entries()].find(([n]) => n.endsWith("adf_control_table.sql"))?.[1] ?? "";
      if (/CLAIMS\.CLAIMS\./i.test(control)) out.push("Oracle object onboarded with source_schema CLAIMS is double-qualified as CLAIMS.CLAIMS.<table> in the control table.");
      return out;
    },
  },
  {
    slug: "03-silver-patient",
    title: "Silver: conformed patient master from STTM (filter test patients, initcap/coalesce, dedupe on MRN)",
    name: "silver_patient_conform",
    layer: "silver",
    registryKey: "patient",
    sourceSystem: "epic",
    source: CLARITY_SQL,
    bronzePick: ["PATIENT"],
    target: CURATED_SQL,
    upsertKeys: "PAT_ID",
    expectFiles: ["adf_curated_control_table.sql"],
    verify: (files) => {
      const sql = scripts(files).join("\n");
      expect(sql).toContain("raw.epic_patient");
      expect(sql).toContain("IS_TEST_PATIENT = 'N'");
      expect(sql).toContain("curated.patient");
    },
    probe: (files) => {
      const out: string[] = [];
      if (/initcap/i.test(scripts(files).join("\n"))) out.push("Spark initcap() survives into the T-SQL Script (no T-SQL equivalent; fails at run time).");
      return out;
    },
  },
  {
    slug: "04-silver-encounter",
    title: "Silver: encounters from STTM (ICD-10 lookup join with default, datediff → T-SQL, pass-rate DQ)",
    name: "silver_encounter",
    layer: "silver",
    registryKey: "encounter",
    sourceSystem: "epic",
    source: CLARITY_SQL,
    bronzePick: ["PAT_ENC"],
    target: CURATED_SQL,
    upsertKeys: "PAT_ENC_CSN_ID",
    expectFiles: ["adf_curated_control_table.sql"],
    verify: (files) => {
      const sql = scripts(files).join("\n");
      expect(sql).toContain("raw.epic_pat_enc");
      expect(sql).toMatch(/DATEDIFF\(day/i);
      expect(sql).toContain("COALESCE(b.DESCRIPTION, 'UNKNOWN')");
    },
    probe: (files) => {
      const out: string[] = [];
      const sql = scripts(files).join("\n");
      const join = sql.match(/JOIN\s+([\w.]+)/i)?.[1];
      if (join && !/^raw\./i.test(join)) {
        out.push(`STTM lookup ICD10_CODE joins ${join}, but the onboarded reference lands only as Bronze raw.epic_icd10_code — no Silver object creates ${join}; the join target isn't resolved against the registries.`);
      }
      return out;
    },
  },
  {
    slug: "05-silver-claim-line",
    title: "Silver: claim lines from STTM (void filter, nvl → ISNULL, decimal cast, CPT regex DQ)",
    name: "silver_claim_line",
    layer: "silver",
    registryKey: "claim_line",
    sourceSystem: "facets",
    source: FACETS_ORACLE,
    bronzePick: ["CLAIM_LINE"],
    target: CLAIMS_DW,
    expectFiles: ["adf_curated_control_table.sql"],
    verify: (files) => {
      const sql = scripts(files).join("\n");
      expect(sql).toContain("raw.facets_claim_line");
      expect(sql).toContain("CLAIM_STATUS <> 'VOID'");
      expect(sql).toMatch(/DECIMAL\(18,2\)/i);
    },
    probe: (files) => {
      const out: string[] = [];
      const sql = scripts(files).join("\n");
      if (/\bnvl\s*\(/i.test(sql)) out.push("Spark nvl() survives into the T-SQL Script.");
      if (/\^\[0-9\]\{5\}\$/.test(allText(files))) out.push("CPT REGEX DQ value reached the pipeline as a raw regex instead of a LIKE pattern.");
      return out;
    },
  },
  {
    slug: "06-silver-provider-scd2",
    title: "Silver: provider SCD2 from STTM (credentialing MySQL; template's own history column names)",
    name: "silver_provider_scd2",
    layer: "silver",
    registryKey: "provider",
    sourceSystem: "cred",
    source: CRED_MYSQL,
    bronzePick: ["provider"],
    target: CURATED_SQL,
    upsertKeys: "npi",
    expectFiles: ["adf_curated_control_table.sql"],
    verify: (files) => {
      const sql = scripts(files).join("\n");
      for (const c of ["effective_from", "effective_to", "is_current"]) expect(sql, `SCD2 writes ${c}`).toContain(c);
    },
  },
  {
    slug: "07-gold-readmission",
    title: "Gold: 30-day readmissions mart from STTM (aggregate over Silver encounters)",
    name: "gold_readmission_30d",
    layer: "gold",
    registryKey: "readmission_30d",
    sourceSystem: "curated",
    source: CURATED_SQL,
    manualObjects: ["curated.encounter"],
    target: CLAIMS_DW,
    expectFiles: ["adf_mart_control_table.sql"],
    verify: (files) => {
      const sql = scripts(files).join("\n");
      expect(sql).toContain("mart.clinical_quality_readmission_30d");
      expect(sql).toMatch(/GROUP BY patient_id, drg_code/i);
    },
    probe: (files) => {
      const out: string[] = [];
      const sql = scripts(files).join("\n");
      if (/raw\.curated_encounter/i.test(sql)) {
        out.push("Gold reads its Silver input by first re-copying curated.encounter into a Bronze table raw.curated_encounter and transforming that; the registry's sourceObjectKeys ['encounter'] (a Silver object) is never used to read curated.encounter directly.");
      }
      return out;
    },
  },
  {
    slug: "08-gold-claims-cost",
    title: "Gold: monthly cost of care from STTM (derived grain date_format → FORMAT, SUM/COUNT DISTINCT)",
    name: "gold_claims_cost_monthly",
    layer: "gold",
    registryKey: "claims_cost_monthly",
    sourceSystem: "curated",
    preferredSource: "azure_synapse_analytics",
    source: CURATED_SQL,
    manualObjects: ["curated.claim_line"],
    target: CLAIMS_DW,
    expectFiles: ["adf_mart_control_table.sql"],
    verify: (files) => {
      const sql = scripts(files).join("\n");
      expect(sql).toContain("mart.finance_claims_cost_monthly");
      expect(sql).toMatch(/FORMAT\(service_date, 'yyyy-MM'\) AS service_month/);
      expect(sql).toMatch(/COUNT\(DISTINCT claim_id\)/);
    },
  },
  {
    slug: "09-gold-dim-provider-scd2",
    title: "Gold: provider dimension SCD2 from a legacy-layout STTM workbook (delta on last_credentialed_at)",
    name: "gold_dim_provider",
    layer: "gold",
    registryKey: "dim_provider",
    sourceSystem: "curated",
    source: CURATED_SQL,
    manualObjects: ["curated.provider"],
    target: CURATED_SQL,
    upsertKeys: "npi",
    expectFiles: ["adf_mart_control_table.sql"],
    verify: (files) => expect(scripts(files).join("\n")).toContain("mart.provider_network_dim_provider"),
    probe: (files) => {
      const out: string[] = [];
      const sql = scripts(files).join("\n");
      if (/USING raw\.curated_provider AS src/.test(sql) && !/is_current\s*=\s*1\)?\s*src/i.test(sql)) {
        out.push("Gold SCD2 MERGEs USING raw.curated_provider — a copy of the Silver SCD2 table holding every history version — with no is_current filter, so an npi with more than one Silver version matches the same target row twice and the MERGE fails at run time.");
      }
      return out;
    },
  },
  {
    slug: "10-gold-patient-360",
    title: "Gold: patient 360 from a legacy-layout STTM workbook (join Silver patient, latest encounter, project)",
    name: "gold_patient_360",
    layer: "gold",
    registryKey: "patient_360",
    sourceSystem: "curated",
    source: CURATED_SQL,
    manualObjects: ["curated.encounter"],
    target: CURATED_SQL,
    expectFiles: ["adf_mart_control_table.sql"],
    verify: (files) => {
      const sql = scripts(files).join("\n");
      expect(sql).toContain("mart.population_health_patient_360");
      expect(sql).toMatch(/JOIN curated\.patient/i);
    },
  },
];

/** Checks every scenario gets: where the Copy lands vs. where the chain reads, and RUN-06. */
function commonProbes(s: Scenario, files: Files): string[] {
  const out: string[] = [];
  const control = [...files.entries()].find(([n]) => n.endsWith("adf_control_table.sql"))?.[1] ?? "";
  const targets = [...control.matchAll(/AND Target = N'([^']*)'/g)].map((m) => m[1]);
  const readsRaw = scripts(files).join("\n").match(/FROM (raw\.\w+)/)?.[1];
  if (s.layer !== "bronze" && readsRaw && targets.length && !targets.includes(readsRaw)) {
    out.push(`The Copy lands into ${targets.join(", ")} (control-table Target, from the target dataset's defaults) but the ${s.layer} chain reads ${readsRaw}, which nothing writes — the first Transform fails at run time.`);
  }
  const master = [...files.entries()].find(([n]) => /(_master|pl_master)\.json$/i.test(n))?.[1] ?? "";
  if (targets.length > 1 && master && !/"SchemaName"/.test(master)) {
    out.push(`RUN-06 reproduces: ${targets.length} control-table rows, but the master's ExecutePipeline passes no SchemaName/TableName, so every row's delta Copy reads the child's default table.`);
  }
  return out;
}

// ── Wizard drivers ───────────────────────────────────────────────────────────────────────────────

async function basics(page: Page, s: Scenario) {
  await page.goto("/");
  await page.getByRole("button", { name: /^Build/ }).first().click();
  await expect(page.getByText("Step 1 of 5 · Basics")).toBeVisible();
  await selectProject(page);
  if (s.layer !== "bronze") {
    await page.locator("button.layer-card", { hasText: s.layer === "silver" ? "Silver" : "Gold" }).click();
    const picker = page.locator("main select").filter({ has: page.locator(`option[value="${s.registryKey}"]`) });
    await picker.selectOption(s.registryKey!);
    await expect(page.getByText(`Prefilled from the ${s.layer === "silver" ? "Silver" : "Gold"} registry`)).toBeVisible();
  }
  await labelled(page, "Pipeline name").fill(s.name);
  if (s.pattern) await labelled(page, "Pipeline pattern").selectOption(s.pattern);
  if (s.sourceSystem) {
    const field = labelled(page, "Source system (optional)");
    if (await field.count()) await field.fill(s.sourceSystem);
    else note(s.slug, `"Source system (optional)" is hidden for the ${s.pattern} pattern, but it is also the Bronze landing-table token (bronzeSourceToken → raw.<source>_<object>) for every pattern — this pipeline lands into raw.<connector>_<object> and can't be told apart from another source system on the same connector.`);
  }
}

async function sourceStep(page: Page, s: Scenario) {
  await continueTo(page, "Step 2 of 5 · Source");
  if (s.preferredSource) {
    const offered = await page.locator(`xpath=//div[contains(@class,'field-cell')][div[contains(@class,'field-label')]/span[normalize-space(.)='Connector']]//option[@value='${s.preferredSource}']`).count();
    if (!offered) note(s.slug, `The Silver input for this Gold object lives on ${s.preferredSource}, but that connector isn't offered as a Build source (target-only in every template's supportedSourceTypes) — a Silver table landed on it can't feed a Gold pipeline; this scenario falls back to ${s.source.connector}.`);
  }
  await fillLinkedService(page, s.source);
  const objectsCard = card(page, "Source objects — input files / table names");
  const objectInputs = objectsCard.getByPlaceholder("e.g. customer, ORDERS, or file.csv");
  if (s.bronzePick) {
    const picker = card(page, "Import from Onboarding (Bronze registry)");
    for (const obj of s.bronzePick) {
      await picker.locator("label", { has: page.locator("strong", { hasText: new RegExp(`^${obj}$`) }) }).locator("input[type=checkbox]").check();
    }
    await picker.getByRole("button", { name: new RegExp(`Import ${s.bronzePick.length} selected`) }).click();
    // The import is async (it fetches the objects' DQ rules first) — wait until it has landed.
    const last = s.bronzePick[s.bronzePick.length - 1];
    await expect(picker.locator("label", { has: page.locator("strong", { hasText: new RegExp(`^${last}$`) }) }).getByText("already added")).toBeVisible();
  }
  if (s.manualObjects) {
    for (let i = 0; i < s.manualObjects.length; i++) {
      if (i > 0) await objectsCard.getByRole("button", { name: /Add object/ }).click();
      await objectInputs.nth(i).fill(s.manualObjects[i]);
    }
  }
  // Importing appends after the wizard's default placeholder row; drop it if it's still there.
  if (s.bronzePick && (await objectInputs.first().inputValue()) === "dbo.customer") {
    note(s.slug, "Importing from the Bronze onboarding picker appends after the wizard's default 'dbo.customer' placeholder row instead of replacing it; left in place it would generate a copy for a table nobody onboarded.");
    await objectsCard.getByTitle("Remove object row").first().click();
  }
  await expect(objectInputs).toHaveCount((s.bronzePick ?? s.manualObjects ?? []).length);
}

async function targetStep(page: Page, s: Scenario) {
  await continueTo(page, "Step 3 of 5 · Target");
  await fillLinkedService(page, s.target);
  if (s.targetPath) await labelled(page, "Target path").fill(s.targetPath);
  const writes = labelled(page, "Write behavior (relational target)");
  const keys = labelled(page, "Upsert key column(s)");
  if ((await writes.count()) && (await writes.inputValue()) === "Upsert" && !(await keys.inputValue())) {
    expect(s.upsertKeys, "Write behavior is Upsert with no keys, and the scenario has none to type").toBeTruthy();
    if (s.layer !== "bronze") {
      note(s.slug, `Picking the ${s.layer} registry object set the Bronze copy's write behavior to Upsert (from the object's writeBehavior, which describes the layer-table load) but left its upsert keys empty — the object's businessKeys go to layerKeyColumns only (RUN-14) and the Bronze picker doesn't carry the onboarding row's upsert_keys, so the wizard blocks until source-side keys are typed by hand.`);
    }
    await keys.fill(s.upsertKeys!);
  }
}

async function settingsStep(page: Page, s: Scenario) {
  await continueTo(page, "Step 4 of 5 · Pipeline Settings");
  if (s.loading) await labelled(page, "Loading behavior").selectOption(s.loading);
  if (s.watermark) {
    const wm = card(page, "Watermark & control table").getByPlaceholder("e.g. modified_date").first();
    if (await wm.count()) await wm.fill(s.watermark);
  }
}

function readTree(dir: string, base = dir, out: Files = new Map()): Files {
  if (!existsSync(dir)) return out;
  for (const entry of readdirSync(dir)) {
    const full = path.join(dir, entry);
    if (statSync(full).isDirectory()) readTree(full, base, out);
    else if (!entry.startsWith("_")) out.set(path.relative(base, full).replace(/\\/g, "/"), readFileSync(full, "utf-8"));
  }
  return out;
}

async function reviewAndCreate(page: Page, s: Scenario, api: HealthcareApi) {
  await continueTo(page, "Step 5 of 5 · Review & Generate");
  await page.getByRole("button", { name: /Generate pipeline code/ }).click();
  const names = page.locator(".artifact-select .artifact-name");
  await expect(names.first()).toBeVisible();
  const previewFiles = await names.allInnerTexts();
  for (const expected of s.expectFiles) expect(previewFiles.some((f) => f.endsWith(expected)), `preview has ${expected} (got ${previewFiles.join(", ")})`).toBe(true);

  await page.getByRole("button", { name: /^Create pipeline$/ }).click();
  await expect(page.getByRole("heading", { name: "Validation results" })).toBeVisible();
  const worker = api.worker()!;
  const outDir = path.join(HC_OUT, `medallion-${s.slug}`);
  writeFileSync(
    path.join(outDir, "_summary.json"),
    JSON.stringify({ scenario: s.title, frontendPreviewFiles: previewFiles, workerFiles: worker.artifacts.map((a) => a.fileName), draftIssues: worker.draftIssues, validation: worker.validation }, null, 2),
  );
  expect(worker.draftIssues, "worker accepted the draft").toEqual([]);
  expect((worker.validation?.issues ?? []).filter((i) => i.severity === "error"), "worker validation errors").toEqual([]);
  expect(worker.validation?.state).toBe("passed");
  for (const w of (worker.validation?.issues ?? []).filter((i) => i.severity === "warning")) note(s.slug, `worker warning [${w.ruleId ?? w.layer ?? ""}]: ${w.message}`);

  const norm = (files: string[]) => files.map((f) => f.split("/").pop()!).filter((f) => f !== "manifest.json").sort();
  expect.soft(norm(previewFiles), "frontend vs worker file set").toEqual(norm(worker.artifacts.map((a) => a.fileName)));
  return readTree(outDir);
}

for (const s of SCENARIOS) {
  test(`${s.slug}: ${s.title}`, async ({ page }) => {
    const pageErrors: string[] = [];
    page.on("pageerror", (e) => pageErrors.push(e.message));
    const api = await healthcareApi(page, `medallion-${s.slug}`, { backendUrl: BACKEND });
    await basics(page, s);
    await sourceStep(page, s);
    await targetStep(page, s);
    await settingsStep(page, s);
    const files = await reviewAndCreate(page, s, api);

    const draft = api.draft() as Record<string, unknown>;
    if (s.layer === "silver") expect(draft.silverObjectRef).toBe(s.registryKey);
    if (s.layer === "gold") expect(draft.martObjectRef).toBe(s.registryKey);
    s.verify?.(files);
    for (const f of s.probe?.(files) ?? []) note(s.slug, f);
    for (const f of commonProbes(s, files)) note(s.slug, f);
    expect(pageErrors, "uncaught page errors").toEqual([]);
  });
}

// The disabled (Mapping_Status DRAFT) Silver object must not be offered for a new pipeline.
test("11-registry-picker: a disabled STTM object (Mapping_Status DRAFT) isn't offered in Build", async ({ page }) => {
  await healthcareApi(page, "medallion-11-picker", { backendUrl: BACKEND });
  await page.goto("/");
  await page.getByRole("button", { name: /^Build/ }).first().click();
  await selectProject(page);
  await page.locator("button.layer-card", { hasText: "Silver" }).click();
  await expect(page.locator('main option[value="patient"]')).toHaveCount(1);
  const offered = await page.locator('main option[value="lab_result"]').count();
  if (offered) note("11-registry-picker", "Build's Silver registry picker offers lab_result, which the STTM import disabled (Mapping_Status DRAFT, DMT-10) — a disabled object can still be built into a pipeline.");
});
