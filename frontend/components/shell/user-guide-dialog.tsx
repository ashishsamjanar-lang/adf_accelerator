"use client";

import { useState } from "react";
import {
  ArrowRight,
  ArrowUpRight,
  BookOpen,
  CheckCircle2,
  GitPullRequest,
  Hammer,
  HelpCircle,
  KeyRound,
  Layers,
  Sparkles,
} from "lucide-react";
import { Dialog } from "@/components/ui/dialog";
import { cn } from "@/lib/utils";
import { ROLE_DESCRIPTIONS, ROLE_LABELS, ROLES } from "@/lib/state";
import { NAV_GROUPS, ARTIFACT_ICONS, type ViewId } from "./navigation";
import type { ArtifactType } from "@/types";

interface UserGuideDialogProps {
  open: boolean;
  onClose: () => void;
}

/**
 * One-paragraph explanation of what each rail/panel destination actually
 * does, keyed by the same ViewId the sidebar uses — so this guide can never
 * drift into describing a screen that isn't wired to a nav item. Kept in this
 * file (not navigation.ts) since it's help copy, not routing data.
 */
const SCREEN_GUIDE: Partial<Record<ViewId, string>> = {
  home:
    "Your landing dashboard: counts of active pipelines, artifacts ready for a Git push, items that need attention, and drafts in progress — plus one-click shortcuts to every other screen and a snapshot of artifact types and pipeline lifecycle stages.",
  teams:
    "Read-only list of teams in the workspace, each showing its member and project counts. Every project belongs to a team, and team membership is what ownership and access checks are scoped against.",
  projects:
    "Every delivery workspace (project), each under a team, with its pipeline count. A project card jumps straight to that project's Settings or its Pipelines list. \"New project\" needs Team admin or Platform admin.",
  "pipelines.create":
    "Build — the one authoring surface for every artifact type. A type switch at the top chooses Pipeline, Linked service, Dataset or Trigger. Pipeline opens the 5-step metadata-driven wizard (see the Build wizard tab); the other three open a single-artifact form so one linked service, dataset or trigger can be authored and pushed on its own, without running the whole pipeline wizard.",
  "delivery.catalog":
    "Two views. Generated artifacts: everything this app generated, filterable by type and searchable by name — preview a row's code and version/state, select rows (or \"select all\" for the current filter) and Push to Git. Git repository: what's actually in the connected repo on any branch — each file's status (generated and in sync, drifted, or foreign) and what uses it; open it in Build, edit its raw JSON, rename it (optionally rewriting referrers) or delete it. Every change goes to a feature branch with a PR, and is stopped as a conflict if the file changed in Git since you loaded it.",
  "delivery.onboarding":
    "Three tabs per project. Bronze: the source files/tables to ingest — add rows in the grid or upload a CSV, JSON or Excel file (Upload → Map headers, optionally saved as a reusable mapping profile → Validate → Commit; commits are additive and recorded in the import history). Per row: data-quality rules and \"Build pipeline from this object\". Downloads: upload template, control-table SQL, objects.csv, registry-lineage.json. Sync to Git pushes them to a feature branch with a PR and a per-row diff; Import / drift compares the registry with Git; Push to Purview sends lineage. Silver / Gold: curated and mart entities with their transformations and data-quality rules, from the Data Modeling Accelerator's STTM export (one .xlsx per table or a layer-wise .zip) or the legacy multi-tab layout — or one at a time; every object needs a tag. The preview shows Spark SQL translated to T-SQL (untranslatable bits are warned about), lookups as joins, governance cells kept on the object, and unapproved mappings imported as disabled. On commit, a sheet whose upstream objects aren't registered is skipped, with the reason listed.",
  "pipelines.drafts":
    "All pipeline creation that hasn't been pushed yet (states DRAFT / GENERATED / VALIDATED / READY_TO_PUSH). Each row shows the project, spec version, creator and when it was last touched. \"Continue setup\" reopens the wizard with every saved answer. Generate chains straight into Validate and shows the result inline; \"Live ARM check\" (when the project has live validation set up) asks Azure to validate the templates without deploying; \"Review artifacts\" opens Catalog filtered to the pipeline.",
  "pipelines.git":
    "A read-only ledger of everything already committed to Git — pushed pipelines and committed artifacts, each showing its artifact version, project, and commit time, traceable back to the specification version that generated it.",
  "pipelines.connections":
    "Pre-configure a reusable linked service (connector, authentication, integration runtime, Key Vault secret reference, and every connector-specific parameter) plus its dataset and onboarded object list, once per system, tagged as a Source, Target, or both. Saved connections then appear as pick-list choices for Source and Target inside the Build wizard, instead of re-typing the same connection details on every pipeline. For managed-identity auth, pick a registered user-assigned identity (or register one in place) and the ADF credential artifact is generated for you. Editing needs pipeline-create or team-admin permission; others get a read-only view.",
  "delivery.samples":
    "Two enterprise-grade reference implementation guides, each a standalone HTML document: a legacy-system-to-lakehouse migration, and an 8-source enterprise ETL hub (SAP, Salesforce, Workday, Oracle EBS, on-prem SQL Server, SFTP files, a REST API, and a streaming event feed). Each guide defines the problem, lays out the approach — including where ADF Forge fits versus what ADF itself runs — and walks through the implementation: source inventory, chunked/parallel extraction for the highest-volume tables (control-table-driven partitioning with bounded concurrency), orchestration design (master/child and per-source orchestrators, cross-pipeline dependencies, restart-by-chunk/object), standard folder structure, standard metadata columns, standard logging, full/incremental load design across daily/hourly/near-real-time cadences, and the end-to-end dev → test → prod CI/CD pipeline. View inline or open the full document in a new tab.",
  "admin.project_settings":
    "Reusable per-project defaults across four tabs — Identity & naming, ADF defaults, Metadata & parameters, Git defaults — plus the project's managed identities and integrations (each detailed in the Governance & data tab of this guide). The Build wizard reads these as its starting values; editing a pipeline in the wizard never changes them back — only an explicit \"Save to Project Settings\" action does.",
  "admin.standards":
    "Opt-in governance policy for the project: a naming-rules table (prefix, suffix, case, max length per artifact type, with an Off/Warn/Block enforcement level — enforced in Build, validate and push), layer prefixes, standard audit columns per layer, a connector allow-list, required tags (Off/Warn/Block — Block rejects Push to Git when a pipeline lacks a tag key), and the policy categories (Tagging, Structure, Connection, Security, Parameterization, Runtime, Git, Onboarding, Documentation). Compliance scan reports every category against the project's generated artifacts and Onboarding registry; Export downloads policy.json. Naming and required tags are gates today — the rest are reported, not blocking. Secrets-as-Key-Vault-reference and \"base branch must be dev\" are always-on safety rules that can't be turned off.",
  "pipelines.templates":
    "Reference catalog of the five generation patterns (Full Load, Incremental/Delta, Initial Load, Composite Watermark, Metadata-Driven) — each card lists its supported source/target connector families and the fields it contributes to the generated spec. \"Use this template\" jumps into Build with that pattern preselected.",
  "admin.users":
    "Everyone with access to this workspace, their team, and their role — see \"Roles & permissions\" in the Getting started tab for what each role can do. Access itself is enforced server-side. With password sign-in, a Platform admin adds users with a username (prefilled from the email) and either a password they choose or a generated one-time password; the user signs in with that username (or email) and must change the password at first sign-in. A Team admin can edit or reset members of their own team. Active sessions lets an admin end a session; Single sign-on stores identity-provider settings and group → role mappings ahead of SSO sign-in being available.",
  "admin.environments":
    "The ADF instances (subscription ID, resource group, ADF factory name, storage account, SQL server, database, Key Vault name) this workspace's generated artifacts are described against — as many as you need, one card each. Picking one in Build fills only globalParameters.json. Every field here is an identifier or reference — actual secrets live in Key Vault and are resolved only at pipeline execution time, never typed into this app. Editing needs Team admin or Platform admin. This app never deploys to these targets — it only records the target metadata.",
  "admin.audit":
    "The append-only audit trail: who did what to which target, newest first. Filter by action, actor, or target text. Team admins see only their own team's events; platform admins see every team plus global events (e.g. environment changes). Entries can't be edited or deleted from the UI.",
};

const FEATURES: Array<{ title: string; body: string }> = [
  { title: "Unified Build form", body: "One authoring surface for every artifact type (pipeline, linked service, dataset, trigger) — pipeline pattern and load behavior are just fields, not a different screen per connector." },
  { title: "5 generation patterns", body: "Full Load, Incremental/Delta, Initial Load, Composite Watermark, and Metadata-Driven — proven activity shapes you pick, never hand-write." },
  { title: "Dozens of built-in connectors", body: "Relational databases, cloud warehouses, NoSQL/big-data stores, files and SaaS systems, each with its own schema-driven form." },
  { title: "Reusable Connections", body: "Configure a source or target system once; pick it from a list on every later pipeline instead of re-typing connection details." },
  { title: "Source Onboarding", body: "Register Bronze tables/files via CSV/Excel upload or a manual grid, and Silver/Gold entities from an STTM workbook (Data Modeling Accelerator export or legacy layout, Spark SQL translated to T-SQL) — feeds Build's pickers and can generate control-table SQL, sync to Git, or detect drift." },
  { title: "Validation before push", body: "Every artifact is checked in layers — syntax, ADF resource shape, references and expressions, security, policy — each issue with a rule id and fix hint. An optional live check validates against Azure without deploying." },
  { title: "Git-native delivery", body: "Artifacts always go to a feature branch, never straight to dev; a pull request opens from there with reviewers prefilled." },
  { title: "Git-backed Catalog", body: "Browse everything already in the repo on any branch, then reopen, raw-edit, rename or delete a file — each change goes out as a PR, with conflict detection." },
  { title: "Medallion layers", body: "Bronze, Silver and Gold pipelines: Silver/Gold land the source in the Bronze raw table, then chain transformations (conform, cast, dedupe, filter, join, aggregate, select, SCD2) into a computed target with an idempotent final load." },
  { title: "Generated data quality", body: "Not null, unique, length, range, regex, row-count and freshness rules become ADF activities — checked on the source before the copy and on the Silver/Gold target after the load; warn or block, optionally against a pass-rate threshold." },
  { title: "Opt-in Standards & governance", body: "Per-project naming rules and required tags with Off/Warn/Block enforcement (naming live in Build and at validate; both a hard gate on push), plus a compliance scan and policy.json export." },
  { title: "Role-based access", body: "Every action is gated by role and, for non-admins, scoped to their own team. Disallowed buttons stay visible but disabled with a tooltip." },
];

const FLOW_STEPS: Array<{ title: string; body: string }> = [
  {
    title: "1. Onboard your sources (optional but recommended)",
    body: "In Onboarding, register the tables/files you need to ingest — target, format, load behaviour, data-quality rules — and, for Silver/Gold, upload an STTM workbook. These become the objects Build offers you later.",
  },
  {
    title: "2. Save your connections (optional but recommended)",
    body: "In Connections, configure the source and target linked services once — connector, auth, Key Vault reference. Build then offers them as one-click picks.",
  },
  {
    title: "3. Build",
    body: "Open Build → Pipeline and step through Basics → Source → Target → Pipeline Settings → Review & Generate. Generate produces the pipeline as a new DRAFT.",
  },
  {
    title: "4. Track it in Drafts",
    body: "An in-progress pipeline lives in Drafts until it's pushed, moving through GENERATED then VALIDATED as you work. \"Continue setup\" reopens the wizard with every saved answer; pushed pipelines can be edited from Pipelines → Edit.",
  },
  {
    title: "5. Validate",
    body: "Generated artifacts are checked against ADF schema rules and your project's Standards policy before they're allowed to move to READY_TO_PUSH. A failed check is explicit and retryable, never silent.",
  },
  {
    title: "6. Push to Git",
    body: "From Catalog, the Artifacts view, or the wizard's last step, select artifacts and push them to a feature branch, then open a pull request into the base branch (dev). The UI never pushes straight to dev.",
  },
  {
    title: "7. Find it in Git & Catalog",
    body: "Once pushed, the artifact shows up in Git (the commit ledger) and stays browsable — and re-editable via \"Open in Build\" — in Catalog and Pipelines.",
  },
];

const SAMPLE_PROJECTS: Array<{ title: string; body: string }> = [
  {
    title: "Legacy Policy Admin → Enterprise Lakehouse",
    body: "One legacy on-premises SQL Server system (plus its nightly flat-file exports) migrated into a governed lake and warehouse.",
  },
  {
    title: "Enterprise Data Hub — 8 source systems",
    body: "SAP, Salesforce, Workday, Oracle EBS, on-prem SQL Server, vendor SFTP files, a marketing REST API and a clickstream event feed, consolidated into one governed ingestion platform.",
  },
];

const LIFECYCLE_STATES: Array<{ state: string; body: string }> = [
  { state: "DRAFT", body: "The wizard's answers are saved as a specification; nothing generated yet." },
  { state: "GENERATED", body: "Generate ran and produced the ADF JSON artifacts from the spec." },
  { state: "VALIDATED", body: "Artifacts passed ADF schema checks and the project's Standards policy." },
  { state: "READY_TO_PUSH", body: "Cleared to go — selectable in Catalog/Artifacts for Push to Git." },
  { state: "PUSHING", body: "A push to the chosen feature branch is in progress." },
  { state: "PUSHED", body: "Committed to the feature branch in Git." },
  { state: "PR_CREATED", body: "A pull request into the base branch (dev) was opened." },
];

const CONTROL_TABLES: Array<{ title: string; intro: string; columns: Array<{ name: string; body: string }> }> = [
  {
    title: "control.adf_control_table — always, for metadata-driven / incremental / composite watermark patterns",
    intro: "One row per source object; add, disable, or re-point an object here without ever regenerating or redeploying the pipeline.",
    columns: [
      { name: "TaskId", body: "Auto-incrementing row id — identifies this object to adf_chunk_plan and log rows." },
      { name: "SourceType", body: "The source connector kind (e.g. AzureSqlTable, AzureBlobFS)." },
      { name: "Source", body: "The qualified source object — schema.table, or a file path, depending on SourceType." },
      { name: "SourceQuery", body: "Optional custom query that overrides the default \"select everything\" read." },
      { name: "TargetType", body: "The target connector kind." },
      { name: "Target", body: "The qualified target object or path the data lands in." },
      { name: "TargetFileName", body: "Target file name, for file-based targets only." },
      { name: "LoadingBehavior", body: "FullLoad or DeltaLoad — which copy strategy this object uses." },
      { name: "WatermarkColumn", body: "The incremental column (e.g. ModifiedDate) delta loads filter on." },
      { name: "WatermarkType", body: "single or composite (multiple watermark columns combined)." },
      { name: "WatermarkValue", body: "The current/starting watermark value the next run reads forward from." },
      { name: "Enabled", body: "Whether the pipeline should process this row at all — the on/off switch per object." },
      { name: "TaskGroup", body: "Only when the master dispatches to several child pipelines: which child handles this row." },
      { name: "ColumnMapping", body: "Only when an object has its own column mappings: the per-row copy mapping." },
      { name: "LastRunTimestamp", body: "When this object last completed successfully." },
      { name: "CreatedAt", body: "When this row was seeded." },
    ],
  },
  {
    title: "control.adf_execution_log — opt-in, \"enable copy log\"",
    intro: "Every generated pipeline writes one row here per object per run (RUNNING on start, then SUCCEEDED/FAILED on completion), so run history, row counts, and failures are queryable in one place instead of only in ADF's own run history.",
    columns: [
      { name: "LogId", body: "Auto-incrementing row id." },
      { name: "RunId", body: "The ADF pipeline run id this log row belongs to." },
      { name: "PipelineName", body: "Which generated pipeline ran." },
      { name: "SourceObject / TargetObject", body: "The source object being copied, and the target object being written to." },
      { name: "LoadType", body: "FullLoad, DeltaLoad, or ChunkedLoad." },
      { name: "RowsRead / RowsWritten / RowsRejected", body: "Rows read from source, written to target, or skipped for a failed check/copy error." },
      { name: "Status", body: "RUNNING, SUCCEEDED, or FAILED." },
      { name: "ErrorMessage", body: "Failure detail, when Status is FAILED." },
      { name: "StartedAt / CompletedAt", body: "Run start/end timestamps." },
    ],
  },
  {
    title: "control.adf_chunk_plan — opt-in, chunked initial load",
    intro: "GenerateChunkPlan seeds one row per chunk; the pipeline updates Status per chunk as it processes them, so a re-run only retries chunks that are PENDING or FAILED instead of starting over.",
    columns: [
      { name: "TaskId", body: "Which adf_control_table object this chunk belongs to." },
      { name: "ChunkId", body: "This chunk's number within that object's plan." },
      { name: "RangeStart / RangeEnd", body: "The bounds of this chunk on the configured chunk column." },
      { name: "Status", body: "PENDING, RUNNING, SUCCEEDED, or FAILED — drives which chunks a re-run retries." },
      { name: "RowsCopied", body: "Rows copied for this chunk." },
      { name: "StartedAt / CompletedAt", body: "This chunk's run start/end timestamps." },
      { name: "ErrorMessage", body: "Failure detail, when Status is FAILED." },
    ],
  },
  {
    title: "control.adf_connection_control — opt-in, \"generate a connection control table\"",
    intro: "Decouples a pipeline's linked services from a hardcoded reference in the pipeline JSON, so ops can repoint a connection per environment or team by editing this table instead of regenerating artifacts.",
    columns: [
      { name: "ConnectionId", body: "Auto-incrementing row id." },
      { name: "ConnectionName", body: "The linked service's own name (e.g. LS_sql_server_source) — unique." },
      { name: "ConnectorType", body: "The connector kind (e.g. AzureSqlDatabase, Snowflake, Sftp)." },
      { name: "LinkedServiceName", body: "The ADF linked service this row resolves to." },
      { name: "KeyVaultName / SecretRef", body: "Key Vault name and secret name only — never a secret value." },
      { name: "Properties", body: "Every other non-secret connector setting (server, database, path, …), as JSON." },
      { name: "Enabled", body: "Whether this connection is active." },
      { name: "CreatedAt", body: "When this row was seeded." },
    ],
  },
  {
    title: "Watermark table (default control.watermarks) — the independent_watermark pattern only",
    intro: "That pattern has no control table; it tracks its own watermark in a one-row table, generated as adf_watermark_table.sql.",
    columns: [
      { name: "TaskId", body: "Always 1 — the one object this pipeline tracks." },
      { name: "WatermarkValue", body: "The last processed watermark; seeded from the initial watermark value." },
      { name: "LastRunTimestamp", body: "When the pipeline last completed successfully." },
    ],
  },
  {
    title: "control.adf_curated_control_table / control.adf_mart_control_table — Silver/Gold pipelines built from the registry only",
    intro: "Generated only for a Silver or Gold pipeline built by picking an object from the Silver/Gold registry (Onboarding's Silver/Gold tabs) rather than typing a curated entity by hand. Publishing that pipeline keeps these tables in sync automatically. Sibling tables to adf_control_table, not an extension of it — Bronze's table is copy-activity-shaped, these are transformation-shaped.",
    columns: [
      { name: "ObjectId", body: "Auto-incrementing row id." },
      { name: "ObjectKey", body: "The registry object's key." },
      { name: "Tag", body: "The object's mandatory classification tag." },
      { name: "CuratedEntity", body: "The Silver entity name (curated table); on the mart table, the Silver entity a Gold object is built from." },
      { name: "MartDomain", body: "The Gold mart domain (mart table only)." },
      { name: "TargetTable", body: "The computed target table — curated.<entity> or mart.<domain>_<entity>." },
      { name: "SourceObjectKeys / SourceCuratedEntities", body: "Pipe-separated Bronze object keys (curated table) or Silver object keys (mart table) this object is built from." },
      { name: "TransformationCount / DataQualityRuleCount", body: "How many transformation and data-quality rules the object has." },
      { name: "WriteBehavior", body: "append, overwrite, upsert, or merge." },
      { name: "LoadingBehavior", body: "FullLoad or DeltaLoad." },
      { name: "Enabled", body: "Whether this object is active." },
      { name: "CreatedAt", body: "When this row was seeded/last synced." },
    ],
  },
];

const GLOSSARY: Array<{ term: string; body: string }> = [
  {
    term: "Linked service",
    body: "An ADF connection object — connector type, authentication, and how to reach a system (server, account, Key Vault reference). Configured once per system, reused by every dataset built on it.",
  },
  {
    term: "Dataset",
    body: "Points at one object — a table, file, or view — inside a linked service. A pipeline's source and target objects are each represented as a dataset.",
  },
  {
    term: "Connector",
    body: "The system type behind a linked service — Azure SQL, Snowflake, ADLS Gen2, SFTP, and dozens more. The connector registry defines what fields, auth types, and dataset shape each one needs, so Build never hand-codes a form per connector.",
  },
  {
    term: "Control table",
    body: "A SQL table, in your own database, that the generated pipeline reads to know which objects to load, their watermark column, and whether they're enabled. Its schema/table name and column mapping are set in Project Settings → Metadata & parameters.",
  },
  {
    term: "Watermark column",
    body: "The column (e.g. an updated_at timestamp or an incrementing id) an incremental/delta pipeline uses to fetch only new or changed rows since the last run. Up to 5 per pipeline, each with a type (datetime / int / bigint / string).",
  },
  {
    term: "Feature branch",
    body: "A short-lived Git branch created for one push. Artifacts always go here first, never to the base branch directly; a pull request then merges it into dev.",
  },
  {
    term: "Pipeline pattern",
    body: "How activities are organized: master/child (one orchestrator invoking a child pipeline per control-table row), single pipeline (one pipeline looping the control table inline), independent full / independent watermark (one self-contained pipeline for one object), or child only / master only. Chosen in Build's Basics step.",
  },
  {
    term: "Template (generation pattern)",
    body: "One of 5 generation strategies — Full Load, Incremental (Delta/watermark) Load, Initial Load, Composite Watermark Load, Metadata-Driven Load. Internal keys that decide how the generator assembles activities — you pick one, you never hand-write the pipeline JSON.",
  },
];

const WIZARD_STEPS: Array<{ title: string; body: string; fields: string[] }> = [
  {
    title: "Basics",
    body: "Name the pipeline, pick its project, and choose the shape of the generated pipeline.",
    fields: ["Create new, or add objects to an existing pipeline", "Pipeline name · Project", "Pipeline pattern (master/child, single pipeline, independent full/watermark, child only, master only)", "Template (Full Load / Incremental / Initial Load / Composite Watermark / Metadata-Driven)", "Optional name overrides, source system, child dispatch, ADF instance", "Layer (Bronze / Silver / Gold) — for Silver/Gold, pick a registry object (which also fills its load behavior, business keys and watermark) or enter the entity manually"],
  },
  {
    title: "Source",
    body: "Where data comes from.",
    fields: ["Pick a saved connection (optionally reference it instead of regenerating it), or create one inline", "Source objects — import from Onboarding (brings their data-quality rules) or type them", "Per-object column mappings · query, filter, partition and isolation options"],
  },
  {
    title: "Target",
    body: "Where data lands.",
    fields: ["Pick a saved connection, or create one inline", "Target path/table, file name and format", "Write behavior (append / overwrite / upsert with keys), batch size, max rows per file"],
  },
  {
    title: "Pipeline Settings",
    body: "Runtime, data quality and what gets generated.",
    fields: ["Load settings and watermark strategy (up to 5 watermark columns)", "Control / watermark table, connection control table", "Partition & chunking · parallel copies, DIU, retries, copy timeout", "Staged copy, redirect incompatible rows, schema-drift tolerance, hard-delete handling", "Estimated source rows · data-quality rules (warn or block, optional max-violation %) · transformations (Silver/Gold: conform, cast, dedupe, filter, join, aggregate, select, SCD2)", "Logging, standard audit columns, custom metadata, annotations, notifications, trigger", "Runtime artifacts: turn globalParameters.json, parameters.json, trigger, ARM parameters definition, lineage.json on/off"],
  },
  {
    title: "Review & Generate",
    body: "A read-only summary of every field you entered across the previous steps.",
    fields: ["\"Create pipeline\" (or \"Save\" in edit mode) saves the draft, generates the full ADF artifact set and validates it — nothing is written to Git yet"],
  },
];

const GOVERNANCE_SECTIONS: Array<{ title: string; intro?: string; bullets: string[] }> = [
  {
    title: "Connections",
    bullets: [
      "One saved definition per source/target system, tagged Source, Target, or both.",
      "Captures: connector, authentication type, integration runtime, Key Vault secret reference, every connector-specific parameter, its dataset, and the onboarded object list.",
      "Dozens of built-in connector schemas — relational databases (Azure SQL, SQL Server, Oracle, PostgreSQL, MySQL, Synapse, Snowflake, DB2, Teradata…), cloud warehouses (Redshift, BigQuery), NoSQL/big-data (Cassandra, HBase, Hive, Cosmos DB…), and file/SaaS connectors.",
      "Reused across every pipeline that needs the same system — configure once, pick from a list afterwards.",
    ],
  },
  {
    title: "Onboarding outputs",
    bullets: [
      "Generate ingestion pipeline — opens Build with the pattern preselected and every per-object setting prefilled from the selected rows.",
      "Generate control-table SQL — DDL plus one seed row per enabled object, the connection control table, and an objects.csv snapshot, pushed as a PR.",
      "Sync to Git — writes or updates the control-table SQL and metadata/objects.csv on a feature branch, with a per-row diff against the last synced commit.",
      "Import / drift — compares the registry with the metadata already in Git and imports rows that only exist there.",
      "Silver/Gold — a pipeline built from a registry-picked object automatically adds its row to the curated or mart control-table artifact.",
    ],
  },
  {
    title: "Project Settings — Identity & naming",
    bullets: [
      "Naming conventions: a pattern per artifact type (pipeline, dataset, linked service, parameter, trigger) using tokens like {project}, {source}, {object}, plus a default artifact folder.",
      "Enforced naming standards: prefix / suffix / case / max length per artifact type, with an enforcement level (Off / Warn / Block) — checked live in Build, at validate, and as a hard gate on Push to Git when set to Block.",
      "Tags: applied to every generated pipeline as annotations (prefilled into Build).",
    ],
  },
  {
    title: "Project Settings — ADF defaults",
    bullets: [
      "Environment identifiers: subscription ID, resource group, Data Factory name, integration runtime.",
      "Defaults: source/target connector, source/target linked service (preselects the matching saved connection in Build), log level, default copy timeout, and an Azure Monitor metrics toggle.",
      "Runtime defaults: parallel copies, retry count, retry interval — the starting values Build's Pipeline Settings step opens with.",
    ],
  },
  {
    title: "Project Settings — Metadata & parameters",
    bullets: [
      "Template & control table: default generation template, watermark type, and the control table's schema/name/connection plus its watermark, loading-behavior, source-object, target-path and enabled column names.",
      "A default filter condition and custom metadata columns, prefilled into new pipelines.",
      "Whether to generate a connection control table by default.",
    ],
  },
  {
    title: "Project Settings — Managed identities & integrations",
    bullets: [
      "User-assigned managed identities: the identities (name + Azure resource id) builders pick for managed-identity auth. Team admin or above.",
      "Integrations: live validation (the sandbox the layer-6 ARM check validates against) and Purview (where lineage is pushed). Identifiers and a Key Vault secret name only — never a secret.",
    ],
  },
  {
    title: "Project Settings — Git defaults",
    bullets: [
      "Connectivity: Git provider, connection mode (repository URL, or server + org/project), repository/server URL, browse path, authentication method (PAT / OAuth / managed identity / SSH), service account, and a Key Vault credential reference — with a Test connection action.",
      "Workflow templates: organization/project/repository, feature-branch name pattern, base branch (always the collaboration branch, e.g. dev — a feature branch can never equal it), default branch, commit-message convention, PR title/body templates, and default reviewers.",
      "These prefill the Push to Git dialog everywhere in the app, so an author only confirms a branch name and commit message rather than typing Git plumbing each time.",
    ],
  },
  {
    title: "Standards",
    bullets: [
      "The same naming-rules table as Project Settings' Identity & naming tab, shared across the project.",
      "Required tags: the tag keys (key or key:value) every pipeline's annotations must carry, Off/Warn/Block.",
      "Layer prefixes (replace raw / curated / mart in computed table names) and the standard audit columns added per layer.",
      "Policy categories reported by the compliance scan: Tagging, Structure, Connection (allowed connectors, auth types), Security (Key Vault references, transport, PII classification), Parameterization, Runtime (retry), Git (branch rules), Onboarding (required fields, watermark for delta objects), Documentation, Audit columns. Naming and required tags block today; rules without an evaluator are marked \"Coming soon\".",
      "Security's Key-Vault-only rule and Git's base-branch rule are always on and can't be disabled, regardless of the toggle shown.",
    ],
  },
  {
    title: "Patterns",
    bullets: [
      "A read-only reference of what each of the 5 templates generates and which connectors it supports, so you know what you're picking in Build's Basics step before committing to it.",
    ],
  },
  {
    title: "Environments (admin)",
    bullets: [
      "One card per ADF instance — any number, not just DEV/QA/PROD: storage account, SQL server, database, Key Vault name, factory name, subscription ID, resource group. Picking one in Build fills only globalParameters.json.",
      "Every value is an identifier or reference; actual secrets are resolved from Key Vault at pipeline execution time and are never entered here.",
    ],
  },
];

const TABS = [
  { id: "start" as const, label: "Getting started", icon: Sparkles },
  { id: "nav" as const, label: "Screen guide", icon: Layers },
  { id: "build" as const, label: "Build wizard", icon: Hammer },
  { id: "govern" as const, label: "Governance & data", icon: KeyRound },
  { id: "reference" as const, label: "Reference", icon: BookOpen },
];

export function UserGuideDialog({ open, onClose }: UserGuideDialogProps) {
  const [tab, setTab] = useState<(typeof TABS)[number]["id"]>("start");

  return (
    <Dialog
      open={open}
      onClose={onClose}
      eyebrow="HELP"
      title="User guide"
      description="What every icon and screen does, and how a pipeline goes from idea to a pull request."
      wide
    >
      <div className="mb-4 flex gap-1 overflow-x-auto border-b border-line">
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            onClick={() => setTab(t.id)}
            className={cn(
              "-mb-px flex shrink-0 items-center gap-1.5 whitespace-nowrap border-b-2 px-3 py-2 text-xs font-bold transition-colors",
              tab === t.id ? "border-teal text-teal" : "border-transparent text-muted hover:text-ink",
            )}
          >
            <t.icon className="h-3.5 w-3.5" />
            {t.label}
          </button>
        ))}
      </div>

      {tab === "start" ? (
        <div className="space-y-5">
          <p className="text-xs leading-relaxed text-ink-2">
            ADF Forge is a metadata-driven control plane for Azure Data Factory: you fill a
            schema-rendered form, the app generates the ADF artifacts (pipelines, datasets, linked
            services, triggers), validates them, and pushes the ones you select to a Git feature
            branch for a pull request into <code className="rounded bg-surface-2 px-1 py-0.5 font-mono text-2xs">dev</code>.
            ADF itself still runs the pipelines — this app never deploys or executes anything.
          </p>

          <div>
            <h3 className="mb-2 flex items-center gap-1.5 text-xs font-extrabold uppercase tracking-wide text-ink">
              <Sparkles className="h-3.5 w-3.5 text-teal" />
              Features
            </h3>
            <div className="grid gap-2 sm:grid-cols-2">
              {FEATURES.map((feature) => (
                <div key={feature.title} className="rounded-md border border-line px-3.5 py-2.5">
                  <strong className="block text-xs text-ink">{feature.title}</strong>
                  <span className="text-2xs text-muted">{feature.body}</span>
                </div>
              ))}
            </div>
          </div>

          <div className="flex gap-3 rounded-md border border-line bg-surface-2 px-3.5 py-2.5">
            <BookOpen className="mt-0.5 h-3.5 w-3.5 shrink-0 text-teal" />
            <div>
              <strong className="flex items-center gap-1 text-xs text-ink">
                See it in a real project
                <ArrowUpRight className="h-3 w-3 text-muted" />
              </strong>
              <span className="text-xs text-muted">
                Two enterprise-grade reference guides — a legacy-to-lakehouse migration and an
                8-source ETL hub — walk through a full delivery end to end (source inventory,
                orchestration, naming, CI/CD) using this app. Open them from{" "}
                <strong className="text-ink">Delivery → Sample Guides</strong>, or see the
                "Reference" tab of this guide for a summary of each.
              </span>
            </div>
          </div>

          <div>
            <h3 className="mb-2 flex items-center gap-1.5 text-xs font-extrabold uppercase tracking-wide text-ink">
              <Sparkles className="h-3.5 w-3.5 text-teal" />
              End-to-end flow
            </h3>
            <div className="space-y-2.5">
              {FLOW_STEPS.map((step) => (
                <div key={step.title} className="flex gap-3 rounded-md border border-line bg-surface-2 px-3.5 py-2.5">
                  <ArrowRight className="mt-0.5 h-3.5 w-3.5 shrink-0 text-teal" />
                  <div>
                    <strong className="block text-xs text-ink">{step.title}</strong>
                    <span className="text-xs text-muted">{step.body}</span>
                  </div>
                </div>
              ))}
            </div>
          </div>

          <div>
            <h3 className="mb-2 text-xs font-extrabold uppercase tracking-wide text-ink">Roles & permissions</h3>
            <div className="grid gap-2 sm:grid-cols-2">
              {ROLES.map((role) => (
                <div key={role} className="rounded-md border border-line px-3 py-2">
                  <strong className="block text-xs text-ink">{ROLE_LABELS[role]}</strong>
                  <span className="text-2xs text-muted">{ROLE_DESCRIPTIONS[role]}</span>
                </div>
              ))}
            </div>
            <p className="mt-2 text-2xs text-muted">
              Buttons you don't have permission for stay visible but disabled (with a tooltip
              explaining why) rather than being hidden — so the shape of the product is never a mystery.
            </p>
          </div>

          <div>
            <h3 className="mb-2 text-xs font-extrabold uppercase tracking-wide text-ink">Finding your way around</h3>
            <p className="text-xs text-muted">
              The narrow icon rail on the far left has one button per group (Overview, Workspace,
              Delivery, Administration) — hover for its name, click to open that group's panel next
              to it. The panel lists every screen in that group with a short hint; the currently open
              screen is highlighted. Your avatar, role, and this guide live at the bottom of that panel.
            </p>
          </div>

          <div>
            <h3 className="mb-2 flex items-center gap-1.5 text-xs font-extrabold uppercase tracking-wide text-ink">
              <HelpCircle className="h-3.5 w-3.5 text-teal" />
              Quick answers
            </h3>
            <div className="space-y-2">
              {[
                { q: "Where do I edit a pipeline that's already been pushed?", a: "Catalog → find the artifact → \"Open in Build\"." },
                { q: "Where do I see who pushed what, and when?", a: "Git — every committed artifact, traceable to its spec version." },
                { q: "I started a pipeline yesterday, where did it go?", a: "Drafts — anything not yet pushed. \"Continue setup\" picks up where you left off." },
                { q: "How do I stop reusing the same connection details every time?", a: "Save the system once in Connections, then pick it in Build's Source/Target step." },
                { q: "How do I make a naming rule mandatory?", a: "Standards → set that artifact type's rule enforcement to Block." },
                { q: "How do I change what a new pipeline starts with by default?", a: "Project Settings — but a pipeline's own edits only write back if you explicitly \"Save to Project Settings\"." },
              ].map((item) => (
                <div key={item.q} className="rounded-md border border-line px-3.5 py-2.5">
                  <strong className="block text-xs text-ink">{item.q}</strong>
                  <span className="text-xs text-muted">{item.a}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      ) : null}

      {tab === "nav" ? (
        <div className="space-y-5">
          {NAV_GROUPS.map((group) => {
            const GroupIcon = group.icon;
            return (
              <div key={group.id}>
                <h3 className="mb-2 flex items-center gap-1.5 text-xs font-extrabold uppercase tracking-wide text-ink">
                  <GroupIcon className="h-3.5 w-3.5 text-teal" />
                  {group.label}
                </h3>
                <div className="space-y-2">
                  {group.items.map((item) => {
                    const ItemIcon = item.icon;
                    return (
                      <div key={item.id} className="flex gap-3 rounded-md border border-line px-3.5 py-2.5">
                        <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded bg-teal-50 text-teal">
                          <ItemIcon className="h-3.5 w-3.5" />
                        </span>
                        <div>
                          <strong className="block text-xs text-ink">{item.label}</strong>
                          <span className="text-xs text-muted">
                            {SCREEN_GUIDE[item.id] ?? item.hint ?? "—"}
                          </span>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            );
          })}

          <div>
            <h3 className="mb-2 text-xs font-extrabold uppercase tracking-wide text-ink">Also in Administration</h3>
            <div className="flex gap-3 rounded-md border border-line px-3.5 py-2.5">
              <div>
                <strong className="block text-xs text-ink">Administration overview</strong>
                <span className="text-xs text-muted">
                  A landing page for the admin area with a recent-activity feed — every administrative
                  action (role change, environment edit, settings save) is logged here for traceability.
                </span>
              </div>
            </div>
          </div>
        </div>
      ) : null}

      {tab === "build" ? (
        <div className="space-y-5">
          <p className="text-xs text-muted">
            Build → Pipeline is the main way pipelines get created. It's a 5-step wizard; every
            field is metadata (nothing is hand-written JSX per connector), so the same wizard works
            for any connector or pattern the registry supports.
          </p>
          <div className="space-y-2.5">
            {WIZARD_STEPS.map((step, index) => (
              <div key={step.title} className="rounded-md border border-line bg-surface-2 px-3.5 py-2.5">
                <div className="flex gap-3">
                  <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-teal text-2xs font-extrabold text-white">
                    {index + 1}
                  </span>
                  <div className="min-w-0 flex-1">
                    <strong className="block text-xs text-ink">{step.title}</strong>
                    <span className="text-xs text-muted">{step.body}</span>
                    <ul className="m-0 mt-1.5 list-disc space-y-0.5 pl-4">
                      {step.fields.map((field) => (
                        <li key={field} className="text-2xs text-muted">{field}</li>
                      ))}
                    </ul>
                  </div>
                </div>
              </div>
            ))}
          </div>
          <div className="flex gap-3 rounded-md border border-line px-3.5 py-2.5">
            <Hammer className="mt-0.5 h-3.5 w-3.5 shrink-0 text-teal" />
            <div>
              <strong className="block text-xs text-ink">Linked service / Dataset / Trigger tabs</strong>
              <span className="text-xs text-muted">
                Next to Pipeline in Build's type switch. Each opens a single-artifact form so you can
                author and push one linked service, dataset, or trigger on its own, without running the
                full pipeline wizard.
              </span>
            </div>
          </div>
          <div className="flex gap-3 rounded-md border border-line px-3.5 py-2.5">
            <GitPullRequest className="mt-0.5 h-3.5 w-3.5 shrink-0 text-teal" />
            <div>
              <strong className="block text-xs text-ink">Push to Git</strong>
              <span className="text-xs text-muted">
                Available once artifacts are validated — from the wizard's last step, Catalog, or the
                Artifacts view. Confirms a feature-branch name and commit message (prefilled from
                Project Settings' Git defaults), pushes, then optionally opens a pull request into the
                project's base branch (always dev, never pushed to directly) with reviewers prefilled.
              </span>
            </div>
          </div>
        </div>
      ) : null}

      {tab === "govern" ? (
        <div className="space-y-3">
          <p className="text-xs text-muted">
            These screens don't sit in the create-a-pipeline path directly — they hold the reusable
            configuration and guardrails that Build and Push to Git read from.
          </p>
          {GOVERNANCE_SECTIONS.map((section) => (
            <div key={section.title} className="rounded-md border border-line px-3.5 py-3">
              <strong className="mb-1.5 flex items-center gap-1.5 text-xs text-ink">
                <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-teal" />
                {section.title}
              </strong>
              {section.intro ? <p className="mb-1.5 text-xs text-muted">{section.intro}</p> : null}
              <ul className="m-0 list-disc space-y-1 pl-4">
                {section.bullets.map((bullet) => (
                  <li key={bullet} className="text-xs text-muted">{bullet}</li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      ) : null}

      {tab === "reference" ? (
        <div className="space-y-5">
          <div>
            <h3 className="mb-2 flex items-center gap-1.5 text-xs font-extrabold uppercase tracking-wide text-ink">
              <BookOpen className="h-3.5 w-3.5 text-teal" />
              Sample projects
            </h3>
            <p className="mb-2 text-xs text-muted">
              Two enterprise-grade reference implementation guides, each a standalone HTML document
              under <code className="rounded bg-surface-2 px-1 py-0.5 font-mono text-2xs">Delivery → Sample Guides</code>,
              built with this app end to end and shipped through a real dev → test → prod CI/CD
              pipeline. Open either one in-app or as a standalone file to see source inventory,
              orchestration design, standard naming/metadata/logging, and a full Build-wizard
              walkthrough.
            </p>
            <div className="space-y-2">
              {SAMPLE_PROJECTS.map((sample) => (
                <div key={sample.title} className="rounded-md border border-line px-3.5 py-2.5">
                  <strong className="block text-xs text-ink">{sample.title}</strong>
                  <span className="text-xs text-muted">{sample.body}</span>
                </div>
              ))}
            </div>
          </div>

          <div>
            <h3 className="mb-2 text-xs font-extrabold uppercase tracking-wide text-ink">Pipeline lifecycle</h3>
            <p className="mb-2 text-xs text-muted">
              Every pipeline and artifact moves through these states in order. A failure at any step
              (GENERATION_FAILED, VALIDATION_FAILED, GIT_PUSH_FAILED, PR_CREATION_FAILED) is explicit
              and retryable — nothing silently continues to the next state.
            </p>
            <div className="space-y-1.5">
              {LIFECYCLE_STATES.map((item, index) => (
                <div key={item.state} className="flex items-center gap-3 rounded-md border border-line px-3.5 py-2">
                  <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-teal-50 text-2xs font-extrabold text-teal">
                    {index + 1}
                  </span>
                  <code className="shrink-0 font-mono text-2xs font-bold text-ink">{item.state}</code>
                  <span className="text-xs text-muted">{item.body}</span>
                </div>
              ))}
            </div>
          </div>

          <div>
            <h3 className="mb-2 text-xs font-extrabold uppercase tracking-wide text-ink">Artifact icon legend</h3>
            <div className="grid gap-2 sm:grid-cols-2">
              {(Object.entries(ARTIFACT_ICONS) as Array<[ArtifactType | "connection", (typeof ARTIFACT_ICONS)[keyof typeof ARTIFACT_ICONS]]>).map(
                ([type, Icon]) => (
                  <div key={type} className="flex items-center gap-2.5 rounded-md border border-line px-3 py-2">
                    <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded bg-teal-50 text-teal">
                      <Icon className="h-3.5 w-3.5" />
                    </span>
                    <div>
                      <strong className="block text-2xs capitalize text-ink">{type.replace(/_/g, " ")}</strong>
                      <span className="text-2xs text-muted">{ARTIFACT_ICON_MEANING[type] ?? ""}</span>
                    </div>
                  </div>
                ),
              )}
            </div>
          </div>

          <div>
            <h3 className="mb-2 text-xs font-extrabold uppercase tracking-wide text-ink">
              Tables your pipelines need in your own database
            </h3>
            <p className="mb-2 text-xs text-muted">
              Several generation patterns (metadata-driven, incremental/delta, composite watermark,
              chunked initial load) read from and write to control tables in{" "}
              <em>your own</em> Azure SQL Database or SQL Server — never in ADF Forge itself. This app
              only generates the DDL/seed SQL as a Git artifact (get it from{" "}
              <strong className="text-ink">Onboarding → Generate control-table SQL</strong>, or as part
              of a pipeline's own artifacts in{" "}
              <strong className="text-ink">Build → Review &amp; Generate</strong>) — a DBA runs it once
              before the first pipeline run. Which tables actually get generated depends on what's
              turned on; most projects only ever need the first one. Names below are only the
              out-of-the-box default — every pipeline actually reads the schema/table name from{" "}
              <strong className="text-ink">Project Settings → Metadata &amp; parameters</strong>, or its
              override in the Build wizard.
            </p>
            <div className="space-y-2.5">
              {CONTROL_TABLES.map((table) => (
                <div key={table.title} className="rounded-md border border-line px-3.5 py-2.5">
                  <strong className="block text-xs text-ink">{table.title}</strong>
                  <span className="mb-1.5 mt-0.5 block text-2xs text-muted">{table.intro}</span>
                  <ul className="m-0 list-disc space-y-0.5 pl-4">
                    {table.columns.map((col) => (
                      <li key={col.name} className="text-2xs text-muted">
                        <code className="rounded bg-surface-2 px-1 py-0.5 font-mono text-2xs text-ink">{col.name}</code>
                        {" — "}
                        {col.body}
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>
            <p className="mt-2 text-2xs text-muted">
              Never store secrets in these tables — every column that identifies a connection is a
              name or a Key Vault reference, never a connection string, password, or client secret.
              Full column-by-column DDL lives in <code className="rounded bg-surface-2 px-1 py-0.5 font-mono text-2xs">docs/DDL.md</code>.
            </p>
          </div>

          <div>
            <h3 className="mb-2 text-xs font-extrabold uppercase tracking-wide text-ink">Glossary</h3>
            <div className="space-y-2">
              {GLOSSARY.map((entry) => (
                <div key={entry.term} className="rounded-md border border-line px-3.5 py-2.5">
                  <strong className="block text-xs text-ink">{entry.term}</strong>
                  <span className="text-xs text-muted">{entry.body}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      ) : null}
    </Dialog>
  );
}

const ARTIFACT_ICON_MEANING: Record<string, string> = {
  pipeline: "The ADF pipeline JSON itself — the activities that move data.",
  dataset: "One object's shape inside a linked service (a table, file, or view).",
  linked_service: "A system connection — server/account plus authentication.",
  parameter: "A named, typed input value used instead of a hardcoded literal.",
  trigger: "What starts a pipeline run — schedule, tumbling window, or event.",
  connection: "A saved Connections-screen entry bundling a linked service, dataset and onboarded objects.",
  factory: "Factory-wide settings (global parameters) that apply across every pipeline.",
};
