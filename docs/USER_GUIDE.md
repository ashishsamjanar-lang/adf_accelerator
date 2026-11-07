# User Guide

What ADF Forge does, what every screen is for, and how a pipeline goes from idea to a pull
request. This is the end-user counterpart to [`docs/DEVELOPER_GUIDE.md`](DEVELOPER_GUIDE.md)
(that one's for people changing the code; this one's for people using the app). The same content,
in the same words, is also built into the app itself — the **"?" User guide** dialog available from
the bottom of any navigation panel — so you never have to leave the app to look something up.

## 1. Features

ADF Forge is a metadata-driven control plane for Azure Data Factory: you fill a schema-rendered
form, the app generates the ADF artifacts, validates them, and pushes the ones you select to a Git
feature branch for a pull request. **ADF itself still runs the pipelines** — this app never deploys
or executes anything; it stops at the pull request.

| Feature | What it gives you |
|---|---|
| **Unified Build form** | One authoring surface for every artifact type (pipeline, linked service, dataset, trigger) instead of a different screen per connector. Pipeline pattern (master/child, single, independent) and load behavior are just fields — no hunting through template cards. |
| **5 generation patterns** | Full Load, Incremental/Delta (watermark-based), Initial Load, Composite Watermark, and Metadata-Driven — each a proven activity shape you pick, never hand-write. |
| **Dozens of built-in connectors** | Relational databases, cloud warehouses, NoSQL/big-data stores, files and SaaS systems, each with its own schema-driven form — no per-connector custom code to wait on. |
| **Reusable Connections** | Configure a source or target system's linked service, auth, and dataset once; pick it from a list on every later pipeline instead of re-typing connection details. |
| **Source Onboarding** | Register the tables/files you need to ingest (Bronze) — target, format, load behavior, data-quality rules — via CSV/Excel upload or a manual grid, and register Silver/Gold curated and mart entities from an STTM workbook (the Data Modeling Accelerator's export, one workbook per table or a `.zip` of them, or the legacy multi-tab layout). Spark-style expressions in the mapping are translated to T-SQL on import. Feeds Build's object pickers and can generate control-table SQL, sync the registry to Git, or detect drift against what's already in Git. |
| **Validation before push** | Every generated artifact is checked in layers — syntax, ADF resource shape, cross-references and expressions, security posture, policy — before it's allowed to move to `READY_TO_PUSH`. Each issue carries a rule id and a fix hint. An optional live check asks Azure to validate the templates against a sandbox resource group without deploying. |
| **Git-native delivery** | Artifacts always go to a feature branch, never straight to the base branch (`dev`); a pull request is opened from there, with reviewers prefilled from Project Settings. |
| **Git-backed Catalog** | Browse everything already in the repo, on any branch, see which files the app generated and which are foreign, and reopen, raw-edit, rename or delete a file — each change goes out as a feature-branch PR, with conflict detection if someone changed the file first. |
| **Medallion layers** | Bronze (raw copy), Silver (curated) and Gold (mart) pipelines. Silver/Gold land the source in the Bronze `raw.*` table first, then run a chain of transformations (conform, cast, dedupe, filter, join, aggregate, select, SCD2 history) into a computed target table, with an idempotent final load. |
| **Data quality, generated into the pipeline** | Not null, unique, length, range, regex, row-count and freshness rules become ADF activities: checked on the source before the copy and on the Silver/Gold target after the load. Each rule either warns or blocks the run, optionally against a pass-rate threshold. |
| **Opt-in Standards & governance** | Per-project naming rules (prefix/suffix/case/max length) and required pipeline tags, each with Off/Warn/Block enforcement — naming is enforced live in Build, at validate, and both act as a hard gate on push. A compliance scan reports the other policy categories, and the policy can be exported as `policy.json`. Secrets-as-Key-Vault-reference and feature-branch-only are always on and can't be turned off. |
| **Project Settings** | Reusable per-project defaults — naming conventions, ADF environment identifiers, runtime defaults, control-table mapping, Git connectivity/workflow templates, managed identities, and external integrations — so Build starts from sensible values instead of blank fields. |
| **Role-based access** | Every action is gated by role (Platform admin, Team admin, Developer, Viewer) and, for non-admin roles, scoped to their own team. Buttons you can't use stay visible but disabled with a tooltip, so the shape of the product is never a mystery. |
| **Full lifecycle traceability** | Every artifact moves through explicit states (`DRAFT → GENERATED → VALIDATED → READY_TO_PUSH → PUSHING → PUSHED → PR_CREATED`) that show up in Drafts, then Git, then Catalog — you can always tell exactly where something is. |

## 2. Sample projects

The fastest way to see what a real ADF Forge delivery looks like end to end — before or alongside
building your own — is the two worked reference guides shipped with the app:

| Guide | Scenario | Open it |
|---|---|---|
| **Legacy Policy Admin → Enterprise Lakehouse** | One legacy on-premises SQL Server system (plus its nightly flat-file exports) migrated into a governed lake and warehouse. | In-app: **Delivery → Sample Guides**, or directly at [`frontend/public/sample-guides/legacy-migration-guide.html`](../frontend/public/sample-guides/legacy-migration-guide.html) |
| **Enterprise Data Hub — 8 source systems** | SAP, Salesforce, Workday, Oracle EBS, on-prem SQL Server, vendor SFTP files, a marketing REST API, and a clickstream event feed, consolidated into one governed ingestion platform. | In-app: **Delivery → Sample Guides**, or directly at [`frontend/public/sample-guides/multi-source-etl-guide.html`](../frontend/public/sample-guides/multi-source-etl-guide.html) |

Each guide is a standalone HTML document (not just an app screen) so it reads like a real
shareable spec and can be opened on its own, outside the app shell, in any browser. Both walk
through the same structure:

1. **Define the problem** — business/technical context, pain points, success criteria.
2. **Approach** — target architecture, where ADF Forge fits versus what ADF itself executes, and the CI/CD strategy.
3. **Implementation** — source inventory; chunked/parallel extraction for the highest-volume tables; orchestration design (master/child and per-source orchestrators, cross-pipeline dependencies, restart-by-chunk/object); standard folder structure; standard metadata columns and logging; target table/file naming standardization; full/incremental load design across daily/hourly/near-real-time cadences; a Build-wizard walkthrough; and the end-to-end dev → test → prod CI/CD pipeline.

Use these as a template for how to structure your own sources, naming, and cadence decisions —
most of the choices in the guide map directly onto fields you'll fill in Onboarding, Connections,
and Build.

## 3. End-to-end flow

1. **Onboard your sources** *(optional but recommended)* — In **Onboarding**, register the
   tables/files you need to ingest: target, format, load behavior, data-quality rules. For Silver
   and Gold, upload an STTM workbook. These become the objects Build offers you later.
2. **Save your connections** *(optional but recommended)* — In **Connections**, configure the
   source and target linked services once: connector, auth, Key Vault reference. Build then offers
   them as one-click picks.
3. **Build** — Open **Build → Pipeline** and step through Basics → Source → Target → Pipeline
   Settings → Review & Generate. Generate produces the pipeline as a new `DRAFT`.
4. **Track it in Drafts** — An in-progress pipeline lives in **Drafts** until it's pushed, moving
   through `GENERATED` then `VALIDATED` as you work. "Continue setup" reopens the wizard with every
   saved answer. Pushed pipelines can be edited from **Pipelines → Edit**.
5. **Validate** — Generated artifacts are checked against ADF schema rules and your project's
   Standards policy before they're allowed to move to `READY_TO_PUSH`. A failed check is explicit
   and retryable, never silent.
6. **Push to Git** — From Catalog, the Artifacts view, or the wizard's last step, select artifacts
   and push them to a feature branch, then open a pull request into the base branch (`dev`). The UI
   never pushes straight to `dev`. Drafts' "Review artifacts" opens Catalog filtered to that
   pipeline, so "select all" picks up its datasets, linked services, trigger and SQL too.
7. **Find it in Git & Catalog** — Once pushed, the artifact shows up in **Git** (the commit ledger)
   and stays browsable — and re-editable via "Open in Build" — in **Catalog** and **Pipelines**.
8. **Run it in ADF** — merging the PR and running the pipeline happen outside this app. Trigger the master (or single) pipeline from ADF Studio,
   the Azure CLI or a trigger; to restart one control-table row, pass its `TaskId` as the `TaskIdFilter`
   parameter (`0` = every enabled row).

## 4. Roles & permissions

| Role | Can do |
|---|---|
| **Platform admin** | Everything — all teams, all projects, environments, Standards, user roles. |
| **Team admin** | Full control within their own team's projects: Build, Push to Git, Project Settings, Standards, Connections. |
| **Developer** | Build, generate, validate, and push within their own team's projects. Cannot edit Project Settings, Standards, or environments. |
| **Viewer** | Read-only across the screens their team has access to — no Build, no push, no settings edits. |

Buttons you don't have permission for stay visible but disabled (with a tooltip explaining why)
rather than being hidden — so the shape of the product is never a mystery. Access itself is
enforced server-side.

**Signing in.** How you sign in depends on how your administrator configured the deployment:

- **Username and password** (the standard setup) — your platform admin creates your account (there
  is no self-signup) and tells you your **username** and a one-time password (either generated or
  chosen by the admin). Sign in with your username — your email address also works — and set your
  own password at first sign-in. After
  five wrong passwords the account locks for a while. Sessions end after a period of inactivity and
  after an absolute maximum; a banner warns you shortly before an idle timeout, and an expired
  session sends you back to the sign-in page.
- **Development mode** — a user switcher with no password, for demos and local development only.
- **Single sign-on** (Entra ID or another OIDC/SAML provider) is prepared but not available yet:
  provider settings can be stored under Administration → Users, but there is no SSO sign-in button.

## 5. Finding your way around

The narrow icon rail on the far left has one button per group (**Overview**, **Workspace**,
**Delivery**, **Administration**) — hover for its name, click to open that group's panel next to
it. The panel lists every screen in that group with a short hint; the currently open screen is
highlighted. Your avatar, role, and the in-app user guide live at the bottom of that panel.

## 6. Screen guide

### Overview
- **Overview** — Your landing dashboard: counts of active pipelines, artifacts ready for a Git
  push, items that need attention, and drafts in progress — plus one-click shortcuts to every other
  screen and a snapshot of artifact types and pipeline lifecycle stages.

### Workspace
- **Teams** — Read-only list of teams in the workspace, each showing its member and project counts.
  Every project belongs to a team, and team membership is what ownership and access checks are
  scoped against.
- **Projects** — Every delivery workspace (project), each under a team, with its pipeline count. A
  project card jumps straight to that project's Settings or its Pipelines list. "New project" needs
  Team admin or Platform admin.

### Delivery
- **Build** — The one authoring surface for every artifact type. A type switch at the top chooses
  Pipeline, Linked service, Dataset or Trigger. Pipeline opens the 5-step metadata-driven wizard
  (see §7 below); the other three open a single-artifact form so one linked service, dataset or
  trigger can be authored and pushed on its own, without running the whole pipeline wizard.
- **Catalog** — Two views. **Generated artifacts**: everything this app generated, filterable by
  type (with a live count per type) and searchable by name; click a row to preview its code and
  version/state, select rows (or "select all" for the current filter) and Push to Git.
  **Git repository**: what's actually in the connected repo on any branch you choose — refresh the
  index, see each file's status (generated by this app and in sync, drifted, or foreign), which
  other files use it, and then open it in Build, edit its raw JSON, rename it (optionally rewriting
  the files that reference it) or delete it (with a list of what would break). Every change goes to
  a feature branch with a PR; if someone changed the file in Git since you loaded it, the change is
  stopped as a conflict instead of overwriting their work.
- **Onboarding** — Three tabs, one per layer, for the selected project.
  - **Bronze** — the source files/tables you want to ingest: target, file format, load behaviour,
    owner, data classification. Add rows in the grid (add/edit/duplicate/delete, with inline
    validation) or upload a CSV, JSON or Excel file: Upload → Map (match each uploaded header to a
    registry column; save the mapping as a named profile to reuse next time) → Validate → Commit.
    Commits are additive (matched by object key), never a destructive replace, and each one is
    recorded in the import history. Per row you can attach data-quality rules (not null, unique,
    range, row count, freshness) and "Build pipeline from this object". Downloads: the upload
    template, control-table SQL, an `objects.csv` snapshot, and a registry-wide `registry-lineage.json`.
    **Sync to Git** pushes the control-table SQL, `objects.csv` and lineage to a feature branch with a
    PR, showing a per-row diff against the last sync. **Import / drift** compares the registry with
    what's in Git and lets you import rows that only exist there.
  - **Silver / Gold** — curated (Silver) entities and business mart (Gold) objects, with their
    transformations and data-quality rules. Download the multi-tab Excel STTM template (one sheet per
    object, a worked example, and a transformations reference sheet), fill it in, upload it, review
    the per-sheet preview (errors block, warnings don't), and commit — or add objects one at a time.
    Every object needs a `tag`. The upload accepts the Data Modeling Accelerator's STTM export as is
    (one `.xlsx` per target table, or its layer-wise `.zip`; set `Target_Layer` and
    `Target_Subject_Area`/`Target_Domain`) as well as the legacy one-sheet-per-object layout. A Data
    Modular workbook is previewed in the browser first, then the server's preview shows what will be
    imported: Spark SQL expressions translated to T-SQL (anything it can't translate is warned about),
    lookups turned into joins, governance cells (PII/PHI/PCI flags, retention, regulatory tag,
    criticality) kept on the object, and mappings not yet approved imported as disabled. On commit, a
    sheet whose upstream source objects aren't in the Bronze (or, for Gold, Silver) registry is skipped,
    and the summary lists each skipped sheet with its reason. If someone edited an object between your
    preview and your commit, that object is skipped rather than overwritten.
- **Drafts** — All pipeline creation that hasn't been pushed yet (states `DRAFT` / `GENERATED` /
  `VALIDATED` / `READY_TO_PUSH`). Each row shows the project, spec version, who created it and when it
  was last touched. "Continue setup" reopens the wizard with every saved answer. Generate and Validate
  run right from the row; Generate chains straight into Validate, and either action shows its result
  — the pass/fail state and every error/warning — inline under that row. "Live ARM check" (when the
  project has live validation set up) asks Azure to validate the generated templates without
  deploying them. "Review artifacts" opens Catalog filtered to the pipeline.
- **Pipelines** — Every pipeline in a project with its state and creator; "Edit" reopens it in the
  Build wizard. If someone else saved the pipeline after you opened it, your save is refused with a
  message instead of silently overwriting their change.
- **Git** — A read-only ledger of everything already committed to Git — pushed pipelines and
  committed artifacts, each showing its artifact version, project, and commit time, traceable back
  to the specification version that generated it.
- **Connections** — Pre-configure a reusable linked service (connector, authentication, integration
  runtime, Key Vault secret reference, and every connector-specific parameter) plus its dataset and
  onboarded object list, once per system, tagged as a Source, Target, or both. Saved connections
  then appear as pick-list choices for Source and Target inside the Build wizard, instead of
  re-typing the same connection details on every pipeline. The linked-service designer groups fields
  into sections and shows missing required fields inline; for managed-identity auth you pick a
  registered user-assigned identity (or register a new one in place), and the matching ADF
  credential artifact is generated for you. Editing needs pipeline-create or team-admin permission;
  others get a read-only view.
- **Sample Guides** — see §2 above.

### Administration
- **Project Settings** — Reusable per-project defaults across four tabs — Identity & naming, ADF
  defaults, Metadata & parameters, Git defaults — plus the project's managed identities and
  integrations (each detailed in §8 below). The Build wizard reads these as its starting values;
  editing a pipeline in the wizard never changes them back — only an explicit "Save to Project
  Settings" action does.
- **Standards** — Opt-in governance policy for the project: a naming-rules table (prefix, suffix,
  case, max length per artifact type, with an Off/Warn/Block enforcement level — enforced in Build,
  validate and push), custom zone prefixes per medallion layer, the standard audit columns per layer,
  a connector allow-list, **required tags** (tag keys every pipeline's annotations must carry, with
  Off/Warn/Block — Block rejects Push to Git), and the policy categories (Tagging, Structure, Connection, Security,
  Parameterization, Runtime, Git, Onboarding, Documentation). **Compliance scan** checks the project's
  generated artifacts and Onboarding registry against every category and lists what fails; **Export**
  downloads the policy as `policy.json`. Naming and required tags are enforced as gates today — the other
  categories are reported by the scan (and the push dialog warns on the Git branch rule) but don't
  block. Secrets-as-Key-Vault-reference and "base branch must be dev" are always-on safety rules that
  can't be turned off.
- **Patterns** — Reference catalog of the five generation patterns (Full Load, Incremental/Delta,
  Initial Load, Composite Watermark, Metadata-Driven) — each card lists its supported source/target
  connector families and the fields it contributes to the generated spec. "Use this template" jumps
  into Build with that pattern preselected.
- **Users** — Everyone with access to this workspace, their team, and their role — see §4 above for
  what each role can do. With password sign-in, a Platform admin adds users with **Add user**: full name,
  email, **username** (prefilled from the email; lowercase letters, digits, `.`, `_`, `-`), role and
  team. The admin either ticks "Set the initial password myself" or lets the app generate a one-time
  password that is shown once; either way the user must change it at first sign-in. A Team admin can
  change the role/active state of, or reset the
  password for, members of their own team. **Active sessions** lists signed-in sessions and lets an
  admin end one. **Single sign-on** stores identity-provider settings and group → role/team mappings
  ahead of SSO sign-in being available.
- **Environments** — The ADF resource targets (subscription ID, resource group, ADF factory name,
  storage account, SQL server, database, Key Vault name) this workspace's generated artifacts are
  described against, one card per environment with a connection status (not configured / connected
  / approval-gated). Every field here is an identifier or reference — actual secrets live in Key
  Vault and are resolved only at pipeline execution time, never typed into this app. Editing needs
  Team admin or Platform admin. This app never deploys to these targets — it only records the
  target metadata.
- **Audit log** — The append-only activity trail: every create, update, generate, push, role change,
  settings save and sign-in-related admin action, with who did it and when. Search it, filter by
  action, and page through it. Team-scoped roles see only their own team's events.
- **Administration overview** — A landing page for the admin area with the most recent events.

## 7. The Build wizard, step by step

Build → Pipeline is the main way pipelines get created. It's a 5-step wizard; every field is
metadata (nothing is hand-written per connector), so the same wizard works for any connector or
pattern the registry supports. Each step opens with a one-line description of what it's for, right
above its fields, so you don't need this table open to know what you're filling in.

| Step | What it's for | Fields |
|---|---|---|
| **1. Basics** | Name the pipeline, pick its project, and choose the shape of the generated pipeline. | Create new or "Add objects to an existing pipeline" · Pipeline name · Project · Pipeline pattern (master/child, single pipeline, independent full, independent watermark, child only, master only) · Template (Full Load / Incremental / Initial Load / Composite Watermark / Metadata-Driven) · Optional name overrides for the master/child/single pipeline (with a live file-name preview and naming hint) · Source system (optional — switches default names to `pl_master` / `pl_<source_system>_child`) · Child dispatch (route control-table rows to several child pipelines by `TaskGroup`) · ADF instance (optional — fills `globalParameters.json`) · Layer (Bronze / Silver / Gold); for Silver/Gold, pick a registry object (filterable by tag, domain, source, status; disabled objects aren't offered) or enter the curated entity (+ mart domain for Gold) manually. A picked object also fills in its write/load behavior, business keys (used as the layer load's key columns) and watermark |
| **2. Source** | Where data comes from. | Pick a saved connection (tick "already pushed — reference it, don't regenerate" to skip regenerating its linked service/dataset), or create one inline · Source objects — import from the Onboarding registry (copies each object's data-quality rules too) or type them · Per-object column mappings · Source query, filter, partition and isolation options |
| **3. Target** | Where data lands. | Pick a saved connection or create one inline · Target path/table, file name and format · Write behavior (append / overwrite / upsert with key columns), batch size, max rows per file |
| **4. Pipeline Settings** | Runtime, data quality and what gets generated. | Load settings and watermark strategy (up to 5 watermark columns, initial value) · Control / watermark table and optional connection control table · Partition & chunking · Parallel copies, DIU, retries, copy timeout · Staged copy, redirect incompatible rows, schema-drift tolerance · Hard-delete handling (soft-delete flag or SQL Server CDC) · Estimated source rows (an unchunked Full Load over 1,000,000 rows is warned about) · Data-quality rules (warn or block, optional max-violation %) · Transformations (Silver/Gold: conform columns, cast schema, dedupe, filter, join with output columns, aggregate, select columns, SCD2 with tracked columns and optional current-flag/valid-from/valid-to column names) · Logging & monitoring, standard audit columns, custom metadata, pipeline annotations · Notifications · Trigger · Runtime artifacts (turn `globalParameters.json`, `parameters.json`, the trigger, `arm-template-parameters-definition.json`, `lineage.json` on/off) |
| **5. Review & Generate** | A read-only summary of every field you entered across the previous steps. | "Create pipeline" (or "Save" in edit mode) saves the draft, generates the full ADF artifact set and validates it — nothing is written to Git yet |

Fields already set in Project Settings show as a compact "inherited" summary with an **Override**
toggle, so a new pipeline starts from the project's defaults (connectors, linked services, timeout,
watermark type, filter condition, template, tags, audit columns) and you only touch what differs.

**Validation results, shown as soon as you generate** — clicking "Create pipeline" on step 5 persists
the pipeline, generates its artifacts, and validates them, then shows the result right there in the
wizard: a pass/fail badge and the full list of errors/warnings (syntax, naming, secret, and policy
checks), grouped by validation layer, each with its rule id and a fix hint. Fix anything
listed and regenerate from Drafts, or continue straight to Drafts if it's clean. The same
generate-then-validate chain, with the same inline results, also runs from each pipeline's row in
Drafts (§6 above) — you don't have to open a pipeline to see whether it's actually valid.

**Linked service / Dataset / Trigger tabs** — next to Pipeline in Build's type switch. Each opens a
single-artifact form so you can author and push one linked service, dataset, or trigger on its own,
without running the full pipeline wizard.

**Push to Git** — available once artifacts are validated — from the wizard's last step, Catalog, or
the Artifacts view. Confirms a feature-branch name and commit message (prefilled from Project
Settings' Git defaults), pushes, then optionally opens a pull request into the project's base
branch (always `dev`, never pushed to directly) with reviewers prefilled.

## 8. Governance & reusable data

These screens don't sit in the create-a-pipeline path directly — they hold the reusable
configuration and guardrails that Build and Push to Git read from.

**Connections**
- One saved definition per source/target system, tagged Source, Target, or both.
- Captures: connector, authentication type, integration runtime, Key Vault secret reference, every
  connector-specific parameter, its dataset, and the onboarded object list.
- Dozens of built-in connector schemas — relational databases (Azure SQL, SQL Server, Oracle,
  PostgreSQL, MySQL, Synapse, Snowflake, DB2, Teradata…), cloud warehouses (Redshift, BigQuery),
  NoSQL/big-data (Cassandra, HBase, Hive, Cosmos DB…), and file/SaaS connectors.
- Reused across every pipeline that needs the same system — configure once, pick from a list
  afterwards.

**Onboarding outputs**
- **Generate ingestion pipeline** — opens Build with the pattern preselected and every per-object
  setting prefilled from the selected rows.
- **Generate control-table SQL** — DDL plus one seed row per enabled object, the connection control
  table, and an `objects.csv` snapshot, pushed as a PR.
- **Sync to Git** — writes or updates the control-table SQL and metadata/`objects.csv` on a feature
  branch, with a per-row diff against the last synced commit.
- **Import / drift** — compares the registry with the metadata already in Git and imports rows that
  only exist there.
- **Push to Purview** — when the project has a Purview integration, sends the registry's
  lineage to Microsoft Purview.
- For Silver/Gold, generating a pipeline built from a registry-picked object automatically adds its
  row to a curated- or mart-specific control-table artifact — no separate step needed.

**Project Settings — Identity & naming**
- Naming conventions: a pattern per artifact type (pipeline, dataset, linked service, parameter,
  trigger) using tokens like `{project}`, `{source}`, `{object}`, plus a default artifact folder.
- Enforced naming standards: prefix / suffix / case / max length per artifact type, with an
  enforcement level (Off / Warn / Block) — checked live in Build, at validate, and as a hard gate on
  Push to Git when set to Block.
- Tags: applied to every generated pipeline as annotations (prefilled into Build). Standards'
  required tags check these annotations at push time.

**Project Settings — ADF defaults**
- Environment identifiers: subscription ID, resource group, Data Factory name, integration runtime.
- Defaults: source/target connector, source/target linked service (preselects the matching saved
  connection in Build), log level, default copy timeout, and an Azure Monitor metrics toggle.
- Runtime defaults: parallel copies, retry count, retry interval — the starting values Build's
  Pipeline Settings step opens with.

**Project Settings — Metadata & parameters**
- Template & control table: default generation template, watermark type, and the control table's
  schema/name/connection plus its watermark, loading-behavior, source-object, target-path and
  enabled column names.
- A default filter condition and custom metadata columns, prefilled into new pipelines.
- Whether to generate a connection control table by default.

**Project Settings — Git defaults**
- Connectivity: Git provider, connection mode (repository URL, or server + org/project),
  repository/server URL, browse path, authentication method (PAT / OAuth / managed identity / SSH),
  service account, and a Key Vault credential reference — with a Test connection action.
- Workflow templates: organization/project/repository, feature-branch name pattern, base branch
  (always the collaboration branch, e.g. `dev` — a feature branch can never equal it), default
  branch, commit-message convention, PR title/body templates, and default reviewers.
- These prefill the Push to Git dialog everywhere in the app, so an author only confirms a branch
  name and commit message rather than typing Git plumbing each time.

**Project Settings — Managed identities and Integrations**
- **User-assigned managed identities** — the identities (name + Azure resource id) builders can pick
  for managed-identity auth in the linked-service designer. Team admin or above.
- **Integrations** — *Live validation* (a sandbox subscription/resource group the layer-6 check
  validates against) and *Purview* (the account lineage is pushed to). Only identifiers and a Key
  Vault secret *name* are stored — never a secret.

**Standards**
- The same naming-rules table as Project Settings' Identity & naming tab, shared across the
  project — enforced as a gate together with required tags.
- Required tags: the tag keys (`key` or `key:value`) every pipeline must carry, Off/Warn/Block.
- Layer prefixes (replace `raw` / `curated` / `mart` in computed target table names) and the standard
  audit columns added per layer when Build's "Standard audit columns" box is ticked.
- Policy categories reported by the compliance scan: Tagging (required annotations), Structure
  (canonical ADF folder layout), Connection (allowed connectors, auth types), Security (secrets as Key
  Vault reference only, transport, PII classification), Parameterization (no env-specific literals),
  Runtime (retry), Git (base-branch + feature-branch pattern), Onboarding (required object fields,
  watermark for delta objects), Documentation (description required), Audit columns. Rules with no
  evaluator yet are marked "Coming soon".
- Security's Key-Vault-only rule and Git's base-branch rule are always on and can't be disabled,
  regardless of the toggle shown.

**Patterns**
- A read-only reference of what each of the 5 templates generates and which connectors it supports,
  so you know what you're picking in Build's Basics step before committing to it.

**Environments (admin)**
- One card per ADF instance — as many as you need, not just DEV/QA/PROD: storage account, SQL
  server, database, Key Vault name, factory name, subscription ID, resource group. Picking an instance
  in Build fills only `globalParameters.json`; every other artifact stays environment-neutral.
- Every value is an identifier or reference; actual secrets are resolved from Key Vault at pipeline
  execution time and are never entered here.

## 9. Reference

### Pipeline lifecycle

Every pipeline and artifact moves through these states in order. A failure at any step
(`GENERATION_FAILED`, `VALIDATION_FAILED`, `GIT_PUSH_FAILED`, `PR_CREATION_FAILED`) is explicit and
retryable — nothing silently continues to the next state.

| # | State | Meaning |
|---|---|---|
| 1 | `DRAFT` | The wizard's answers are saved as a specification; nothing generated yet. |
| 2 | `GENERATED` | Generate ran and produced the ADF JSON artifacts from the spec. |
| 3 | `VALIDATED` | Artifacts passed ADF schema checks and the project's Standards policy. |
| 4 | `READY_TO_PUSH` | Cleared to go — selectable in Catalog/Artifacts for Push to Git. |
| 5 | `PUSHING` | A push to the chosen feature branch is in progress. |
| 6 | `PUSHED` | Committed to the feature branch in Git. |
| 7 | `PR_CREATED` | A pull request into the base branch (`dev`) was opened. |

### Artifact icon legend

| Type | Meaning |
|---|---|
| Pipeline | The ADF pipeline JSON itself — the activities that move data. |
| Dataset | One object's shape inside a linked service (a table, file, or view). |
| Linked service | A system connection — server/account plus authentication. |
| Parameter | A named, typed input value used instead of a hardcoded literal; also used for generated SQL, `manifest.json` and `lineage.json`. |
| Credential | An ADF credential pointing at a user-assigned managed identity, referenced by a linked service. |
| Trigger | What starts a pipeline run — schedule, tumbling window, or event. |
| Connection | A saved Connections-screen entry bundling a linked service, dataset and onboarded objects. |
| Factory | Factory-wide settings (global parameters) that apply across every pipeline. |

### Tables your pipelines need in your own database

A generated pipeline doesn't just move data — most patterns also **read from and write to a handful
of control tables** that live in *your own* Azure SQL Database or SQL Server, not in ADF Forge. This
app never connects to that database itself — it only generates the `CREATE TABLE` DDL and seed
`INSERT`s as a Git artifact (`adf_control_table.sql`) and pushes it through the normal
Build → Validate → Push to Git flow, exactly like a pipeline or dataset. Get the DDL either from
**Onboarding → Generate control-table SQL** or as part of a pipeline's own generated artifact set in
**Build → Review & Generate**; a DBA runs it against the target database before the first
pipeline run. The script is written for Azure SQL / SQL Server and is safe to run again: each table
is only created if it doesn't already exist. It doesn't create the schema itself (e.g. `control`) —
create that first. Which of the tables below actually get generated depends on what you turned on — most
projects only ever need the first one.

Silver/Gold pipelines also generate `adf_layer_schemas.sql`, which creates the `raw`/`curated`/`mart`
schemas (or your Standards layer prefixes) the layer tables live in. Create the final curated/mart
table yourself: the last step loads it by matching column names, so it may have extra columns (e.g.
audit columns) or a different column order. The intermediate tables each transformation writes are
created and dropped by the pipeline, named per run so two runs never share one.

The schema/table names below are examples. The real name is the Build wizard's **Control / watermark
table** field, prefilled from **Project Settings → Metadata & parameters** ("Control-table schema" /
"Control-table name"), so different teams can point at different databases without editing anything
by hand.

**`control.adf_control_table`** — always generated for the metadata-driven, incremental/delta, and
composite watermark patterns. One row per source object; add, disable, or re-point an object here
without ever regenerating or redeploying the pipeline.

| Column | Description |
|---|---|
| `TaskId` | Auto-incrementing row id — identifies this object to `adf_chunk_plan` and log rows. |
| `SourceType` | The source connector kind (e.g. `AzureSqlTable`, `AzureBlobFS`). |
| `Source` | The qualified source object — schema.table, or a file path, depending on `SourceType`. |
| `SourceQuery` | Optional custom query that overrides the default "select everything" read. |
| `TargetType` | The target connector kind. |
| `Target` | The qualified target object or path the data lands in. |
| `TargetFileName` | Target file name, for file-based targets only. |
| `LoadingBehavior` | `FullLoad` or `DeltaLoad` — which copy strategy this object uses. |
| `WatermarkColumn` | The incremental column (e.g. `ModifiedDate`) delta loads filter on. |
| `WatermarkType` | `single` or `composite` (multiple watermark columns combined). |
| `WatermarkValue` | The current/starting watermark value the next run reads forward from. |
| `Enabled` | Whether the pipeline should process this row at all — the on/off switch per object. |
| `TaskGroup` | Only when the master dispatches to several child pipelines: which child handles this row. |
| `ColumnMapping` | Only when an object has its own column mappings: the per-row copy mapping. |
| `LastRunTimestamp` | When this object last completed successfully. |
| `CreatedAt` | When this row was seeded. |

**`control.adf_execution_log`** — generated when **Azure Monitor logging** ("enable copy log") is
turned on in Project Settings or the Build wizard's Pipeline Settings step. Every generated pipeline
writes one row here per object per run (`RUNNING` on start, then `SUCCEEDED`/`FAILED` on completion),
so run history, row counts, and failures are queryable in one place instead of only in ADF's own run
history.

| Column | Description |
|---|---|
| `LogId` | Auto-incrementing row id. |
| `RunId` | The ADF pipeline run id this log row belongs to. |
| `PipelineName` | Which generated pipeline ran. |
| `SourceObject` | The source object being copied. |
| `TargetObject` | The target object being written to. |
| `LoadType` | `FullLoad`, `DeltaLoad`, or `ChunkedLoad`. |
| `RowsRead` | Rows read from source. |
| `RowsWritten` | Rows written to target. |
| `RowsRejected` | Rows that failed a data-quality check or a copy error and were skipped. |
| `Status` | `RUNNING`, `SUCCEEDED`, or `FAILED`. |
| `ErrorMessage` | Failure detail, when `Status` is `FAILED`. |
| `StartedAt` / `CompletedAt` | Run start/end timestamps. |

**`control.adf_chunk_plan`** — generated when **chunked initial load** is configured on a source
object. `GenerateChunkPlan` seeds one row per chunk; the pipeline updates `Status` per chunk as it
processes them, so a re-run only retries chunks that are `PENDING` or `FAILED` instead of starting
over.

| Column | Description |
|---|---|
| `TaskId` | Which `adf_control_table` object this chunk belongs to. |
| `ChunkId` | This chunk's number within that object's plan. |
| `RangeStart` / `RangeEnd` | The bounds of this chunk on the configured chunk column. |
| `Status` | `PENDING`, `RUNNING`, `SUCCEEDED`, or `FAILED` — drives which chunks a re-run retries. |
| `RowsCopied` | Rows copied for this chunk. |
| `StartedAt` / `CompletedAt` | This chunk's run start/end timestamps. |
| `ErrorMessage` | Failure detail, when `Status` is `FAILED`. |

**`control.adf_connection_control`** — opt-in via Project Settings → Metadata & parameters ("Generate
a connection control table") or the Build wizard's Pipeline Settings step. Decouples a pipeline's
linked services from a hardcoded reference in the pipeline JSON, so ops can repoint a connection per
environment or team by editing this table instead of regenerating artifacts.

| Column | Description |
|---|---|
| `ConnectionId` | Auto-incrementing row id. |
| `ConnectionName` | The linked service's own name (e.g. `LS_sql_server_source`) — unique. |
| `ConnectorType` | The connector kind (e.g. `AzureSqlDatabase`, `Snowflake`, `Sftp`). |
| `LinkedServiceName` | The ADF linked service this row resolves to. |
| `KeyVaultName` | Key Vault name only — never a secret value. |
| `SecretRef` | Key Vault secret name only — never the secret itself. |
| `Properties` | Every other non-secret connector setting (server, database, path, …), as JSON. |
| `Enabled` | Whether this connection is active. |
| `CreatedAt` | When this row was seeded. |

**Watermark table** (default `control.watermarks`, generated as `adf_watermark_table.sql`) — used
only by the single-object incremental pattern (`independent_watermark`), which has no control table.
It holds one row tracking that pipeline's own watermark.

| Column | Description |
|---|---|
| `TaskId` | Always `1` — the one object this pipeline tracks. |
| `WatermarkValue` | The last processed watermark; seeded from the initial watermark value. |
| `LastRunTimestamp` | When the pipeline last completed successfully. |

**`control.adf_curated_control_table`** / **`control.adf_mart_control_table`** — generated only for a
Silver or Gold pipeline built by picking an object from the Silver/Gold registry (Onboarding's Silver/
Gold tabs) rather than typing a curated entity by hand. Publishing that pipeline keeps these tables in
sync automatically — no separate "generate control-table SQL" step needed. Sibling tables to
`adf_control_table`, not an extension of it: Bronze's table is copy-activity-shaped, these are
transformation-shaped.

| Column | In | Description |
|---|---|---|
| `ObjectId` | both | Auto-incrementing row id. |
| `ObjectKey` | both | The registry object's key. |
| `Tag` | both | The object's mandatory classification tag. |
| `CuratedEntity` | curated | The Silver entity name. |
| `MartDomain` | mart | The Gold mart domain. |
| `CuratedEntity` | mart | The Silver entity this mart object is built from. |
| `TargetTable` | both | The computed target table — `curated.<entity>` or `mart.<domain>_<entity>`. |
| `SourceObjectKeys` | curated | Pipe-separated Bronze object keys this Silver entity is built from. |
| `SourceCuratedEntities` | mart | Pipe-separated Silver object keys this Gold object is built from. |
| `TransformationCount` | both | How many transformation rules the object has. |
| `DataQualityRuleCount` | both | How many data-quality rules the object has. |
| `WriteBehavior` | both | `append`, `overwrite`, `upsert`, or `merge`. |
| `LoadingBehavior` | both | `FullLoad` or `DeltaLoad`. |
| `Enabled` | both | Whether this object is active. |
| `CreatedAt` | both | When this row was seeded/last synced. |

**Columns added to your target tables.** Depending on what you tick in Build, generated pipelines
also write these columns into the tables they load: the standard audit columns (`_pipeline_run_id`,
`_pipeline_name`, `_ingestion_ts_utc`, `_medallion_layer`, plus `_trigger_time` on Bronze — or your
project's own list from Standards), `_is_deleted` / `_deleted_at_utc` when hard-delete handling is on,
and `_is_current` / `_valid_from` / `_valid_to` for an SCD2 transformation.

> **Never store secrets in these tables.** Every column that identifies a connection is a *name* or a
> *Key Vault reference* — a linked-service name, a Key Vault name, a secret name. No connection
> string, password, or client secret is ever generated into this SQL. Full column-by-column DDL (and
> this app's own internal Postgres tables, which are unrelated and never touched by ADF) lives in
> `docs/DDL.md`, with a comment on every column; `docs/adf_forge_ddl.sql` is the runnable version.

### Glossary

- **Linked service** — An ADF connection object — connector type, authentication, and how to reach
  a system (server, account, Key Vault reference). Configured once per system, reused by every
  dataset built on it.
- **Dataset** — Points at one object — a table, file, or view — inside a linked service. A
  pipeline's source and target objects are each represented as a dataset.
- **Connector** — The system type behind a linked service — Azure SQL, Snowflake, ADLS Gen2, SFTP,
  and dozens more. The connector registry defines what fields, auth types, and dataset shape each
  one needs, so Build never hand-codes a form per connector.
- **Control table** — A SQL table, in your own database, that the generated pipeline reads to know
  which objects to load, their watermark column, and whether they're enabled. Its schema/table name
  and column mapping are set in Project Settings → Metadata & parameters.
- **Watermark column** — The column (e.g. an `updated_at` timestamp or an incrementing id) an
  incremental/delta pipeline uses to fetch only new or changed rows since the last run. Up to 5 per
  pipeline, each with a type (datetime / int / bigint / string).
- **Feature branch** — A short-lived Git branch created for one push. Artifacts always go here
  first, never to the base branch directly; a pull request then merges it into `dev`.
- **Pipeline pattern** — How activities are organized: master/child (one orchestrator invoking a
  child pipeline per control-table row), single pipeline (one pipeline looping the control table
  inline), independent full / independent watermark (one self-contained pipeline for one object, no
  control table), or child only / master only (generate just one half of a pair). Chosen in Build's
  Basics step.
- **Medallion layer** — Bronze (raw copy), Silver (curated: conform, dedupe, cast, filter, join, SCD2)
  or Gold (mart: join, aggregate, select, filter, dedupe, SCD2). Silver/Gold target table names are
  computed, never typed; on a database target the source first lands in `raw.<source>_<object>`
  (`<source>` is the Source system when set, otherwise the connector).
- **STTM** — Source-to-target mapping: the Excel workbook used to onboard Silver/Gold objects — the
  Data Modeling Accelerator's export (`.xlsx` or `.zip`) or the legacy multi-tab layout.
- **Template (generation pattern)** — One of 5 generation strategies — Full Load, Incremental
  (Delta/watermark) Load, Initial Load, Composite Watermark Load, Metadata-Driven Load. Internal
  keys that decide how the generator assembles activities — you pick one, you never hand-write the
  pipeline JSON.

## 10. Quick answers

| Question | Answer |
|---|---|
| Where do I edit a pipeline that's already been pushed? | Pipelines → Edit, or Catalog → Git repository → find the file → "Open in Build". |
| How do I rename or delete a file that's already in Git? | Catalog → Git repository → select the file → Rename or Delete. It goes out as a PR. |
| How do I restart one table in production? | Trigger the master (or single) pipeline with `TaskIdFilter` set to that row's `TaskId` (`0` = every enabled row). |
| Where do I see who changed what? | Administration → Audit log. |
| Where do I see who pushed what, and when? | Git — every committed artifact, traceable to its spec version. |
| I started a pipeline yesterday, where did it go? | Drafts — anything not yet pushed. "Continue setup" picks up where you left off. |
| How do I stop reusing the same connection details every time? | Save the system once in Connections, then pick it in Build's Source/Target step. |
| How do I make a naming rule mandatory? | Standards → set that artifact type's rule enforcement to Block. |
| How do I change what a new pipeline starts with by default? | Project Settings — but a pipeline's own edits only write back if you explicitly "Save to Project Settings". |
| Where can I see a worked example before building my own? | Delivery → Sample Guides — see §2 above. |
| What SQL tables does my database need before a pipeline can actually run? | See "Tables your pipelines need in your own database" in §9 — get the DDL via Onboarding → Generate control-table SQL, or from Build → Review & Generate. |
