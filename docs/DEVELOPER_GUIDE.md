# Developer Guide

Practical, task-oriented onboarding for a developer picking up this repo for the first time. This
file complements, and deliberately does not repeat, the other top-level docs:

| File | What it's for |
|---|---|
| `README.md` | Repository reference: full layout, architecture, configuration, CI job list |
| **`docs/DEVELOPER_GUIDE.md`** (this file) | How to actually get set up and make a change, step by step |
| `docs/USER_GUIDE.md` | The end-user counterpart to this file: what the app does (features), the two sample-project reference guides, screen-by-screen behavior, the Build wizard, and governance/reference material. Same content is also built into the app itself as the in-app "User guide" dialog (`frontend/components/shell/user-guide-dialog.tsx`) — update both together when the product changes. |
| `docs/DDL.md` | Table-by-table, column-by-column reference for both stores (`app` = this app's Postgres metastore, `control` = the generated ADF control-table schema on the customer's Azure SQL). Every column has a comment |
| `docs/adf_forge_ddl.sql` | Runnable DDL **generated from `docs/DDL.md`** by `docs/scripts/build_ddl_sql.py` — live tables only, with `COMMENT ON COLUMN` (Postgres) and `MS_Description` (T-SQL). Never hand-edit it |
| `docs/DEPLOYMENT_GUIDE.md` | Building/pushing images and deploying (Container Apps via Bicep, or any Kubernetes via the Helm chart) |
| `infra/README.md`, `infra/helm/adf-accelerator/README.md` | What the Bicep template / Helm chart provision, and the secrets they expect |

If you only read one other file after this one, read `README.md` — its "Repository layout",
"Architecture" and "Product rules that shape the code" sections are the map this guide assumes.

## 1. What you're building

A metadata-driven control plane for Azure Data Factory. A user configures project metadata, fills
a schema-rendered form, the app generates ADF pipeline JSON, validates it, and pushes selected
artifacts to a Git **feature branch** for PR review into `dev`. The product stops there — it never
deploys, releases, or runs a pipeline, and it never touches a customer's live data.

Three runtime pieces, one shared Postgres:

```
frontend (Next.js)  →  api (FastAPI)  →  postgres
                           ↑
                        worker (polls the jobs table: generate, validate, git_push, onboarding_sync,
                                catalog_refresh, catalog_change, live_check, purview_push)
```

Frontend never talks to Postgres, Git, or ADF directly — everything goes through the API's typed
client. The worker is the only thing that talks to Git providers (GitHub, GitLab, and Azure Repos REST
adapters in `worker/app/gitops/`, or an offline `SimulatedAdapter` when no credential is configured)
and to Azure (`worker/app/azure/`: the opt-in layer-6 ARM validate-mode check and the Purview lineage
push — both validate/read-only, never a deployment).

## 2. First-time setup

Prerequisites: Docker Desktop with Compose, Node.js 22+, Python 3.12+.

```
git clone <this repo>
cd adf_accelerator
cp config/platform.env.example config/platform.env    # or: make setup
docker compose up --build                              # or: make up
```

- API health check: http://localhost:8000/health
- Frontend: http://localhost:3000
- The `api` container runs `alembic upgrade head` automatically on every start — you don't run
  migrations by hand for local dev unless the stack is already up and you just pulled a new
  migration (`make migrate`).
- Leave `GIT_TOKEN` blank in `config/platform.env` for local dev — the worker falls back to an
  offline `SimulatedAdapter` instead of a Git provider, so pushes "succeed" without touching a real
  repo. The UI shows a "Simulated — no credential" badge when this happens.
- **Sign-in.** `config/platform.env.example` ships `AUTH_MODE=password`: username (or email) + password,
  server-side cookie sessions, CSRF, lockout. Accounts are created only by a platform admin
  (Administration → Users → Add user). Bootstrap the very first admin once, from the backend:
  `docker compose run --rm api python -m app.manage create-platform-admin --email you@example.com --name "You" --username you`
  (prints a one-time password unless you pass `--password`). Set `AUTH_MODE=mock` for a no-login dev
  user switcher. `oidc`/`saml` are scaffolded only and return 501 (see §7).

### Configuration: one file

Every runtime parameter lives in **`config/platform.env`** (git-ignored; copy
`config/platform.env.example`, which documents every key). Nothing else holds app settings — there is no
`frontend/.env*` file.

| Reader | How it loads the file |
|---|---|
| api (`backend/app/config.py`) | pydantic-settings `env_file`, resolved from the repo root (works from any working directory) |
| worker (`worker/app/config.py`) | same |
| frontend | `next.config.ts` loads it into `process.env` for `npm run dev` / `npm run build` |
| docker compose | `env_file: ./config/platform.env` on every service |
| Container Apps / Helm | the same key names as real env vars (`infra/main.bicep` params, Helm `values.yaml`) |

A real environment variable always wins over the file. `PLATFORM_ENV_FILE=/path/x.env` points every
reader at a different file (e.g. one per environment). The backend test suite sets `PLATFORM_ENV_FILE` to
a non-existent path in `tests/conftest.py`, so your local values never leak into tests. To add a new
parameter: add a typed field to the relevant `Settings` class, document it in `platform.env.example`, and
— if it must reach Azure/Kubernetes — add it to `infra/main.bicep` / the Helm chart too.

You rarely need the full stack running just to iterate on one layer:

| Layer | Command |
|---|---|
| Frontend only | `cd frontend && npm install && npm run dev` — shows an empty/error state automatically if the API isn't reachable |
| Backend only | `cd backend && pip install -r requirements-dev.txt && pytest` (tests use in-memory SQLite, no Postgres needed) — or `uvicorn app.main:app --reload` against a real DB |
| Worker only | `cd worker && pip install -r requirements-dev.txt && pytest` |

Install from `requirements-dev.txt`, not `pyproject.toml` ranges directly — see [Dependency
pinning](#dependency-pinning-gotcha) below before you change a Python dependency.

## 3. Before you touch code

1. Skim `README.md`'s "Architecture" and "Product rules that shape the code" — the eight rules
   there (no deploy UI, no fabricated values, feature-branch-only Git, etc.) are enforced by
   convention across every layer, and a PR that violates one will be rejected in review regardless
   of how well-tested it is.
2. Read the code you're about to change and its tests first — the test files under
   `backend/tests/`, `worker/tests/` and `frontend/lib/**/*.test.ts` document the expected behavior.

## 4. Common tasks, step by step

### Add a new backend API endpoint

1. Add the route in the right `backend/app/api/*.py` router (one file per resource: `auth.py`,
   `teams.py`, `users.py`, `projects.py`, `templates.py`, `pipelines.py`, `artifacts.py`, `git.py`,
   `connections.py`, `connectors.py`, `managed_identities.py`, `onboarding.py` (Bronze registry, DQ
   rules, imports, mapping profiles, sync-to-Git, Git drift), `registry.py` (Silver/Gold STTM),
   `catalog.py` (Git catalog), `integrations.py` (live check / Purview), `sso.py`, `admin.py`). Keep
   the handler thin — request/response shapes go in `schemas.py`, business logic in `services.py`
   (or `catalog_services.py` / `integration_services.py`), data access in `repository.py`.
2. Gate it with `require_permission(resource, action)` from `app/rbac.py` unless there's a
   documented reason not to (`connectors.py` is deliberately ungated — see the comment at its top).
   Add the `(resource, action) → roles` row to `PERMISSIONS` in `rbac.py` if it doesn't exist yet,
   following the existing role tiers in that file. Add `require_scope`/`scoped_team_id` too if the endpoint
   should be confined to the caller's own team for non-admin roles.
3. If the change needs a new column/table, edit `app/models.py` then generate a migration:
   ```
   docker compose run --rm api alembic revision --autogenerate -m "short_description"
   ```
   Check the generated file — autogenerate misses some constraint/index changes. The CI
   `migrations` job fails the build if `models.py` and the migrations directory drift apart, so
   don't skip this step even for a "trivial" model change. Then document the column in
   `docs/DDL.md` (with an inline comment) and regenerate `docs/adf_forge_ddl.sql` — see
   [Add a database migration](#add-a-database-migration).
4. Add a `pytest` case in the matching `backend/tests/test_*.py` file — at minimum one happy path
   and one per role in `ROLE_USERS` (`backend/tests/conftest.py`) if you added an RBAC gate. Use the
   `as_user` fixture to exercise a role other than the default `PLATFORM_ADMIN`.
5. Run `pytest` in `backend/` (or `docker compose run --rm api pytest`). `tests/test_endpoint_contract.py`
   walks every `/api/v1` route and fails the full run if your new route (a) has no `require_permission`
   dependency and isn't listed in its `_OPEN_ROUTES` with a reason, or (b) is never driven to a 2xx
   response by some test (a `conftest.py` middleware records them; routes that can't succeed offline go in
   `_NO_HAPPY_PATH`). It also calls each gated route as every disallowed role and expects a 403.

### Add or change a frontend feature

1. Find the right `features/<domain>/` folder — one per nav area (`home`, `teams`, `projects`,
   `pipelines`, `build`, `drafts`, `git`, `catalog`, `onboarding`, `templates`, `artifacts`,
   `administration`, `samples`, `auth`). The Build wizard (`ViewId` `pipelines.create`) is split:
   `create-pipeline-wizard.tsx` is a thin shell, state and handlers live in `use-pipeline-wizard.ts`,
   each step is a `wizard-*-step.tsx`, and shared bits are in `wizard-shared.tsx`. Rule editors
   shared with Onboarding live in `components/rules/rule-editors.tsx`.
2. All HTTP goes through `frontend/lib/api/client.ts` — never call `fetch()` directly in a
   component. Reads go through `useResource()` (`frontend/lib/api/hooks.tsx`), which keeps the
   resource at a neutral empty value (`frontend/lib/api/empty-values.ts`) if the API call throws;
   make sure any new required field still has a sensible empty default there.
3. Reuse `components/ui/` primitives (shadcn-style: button, card, dialog, select, tabs, ...) and
   `components/common/` shared bits (`page-header`, `empty-state`, `status-pill`,
   `lifecycle-stepper`) before writing new markup.
4. Connector-specific form fields are driven by schemas in `frontend/lib/adf/connections.ts` —
   never hand-write a form per connector. If you're adding a connector, add its schema there (and
   the matching entry in `worker/app/connectors/`, kept in parity — see below).
5. Add explicit TypeScript types in `types/<area>.ts` (split by domain — `types/git.ts`,
   `types/pipeline.ts`, etc. — each re-exported from `types/index.ts`, so `@/types` imports
   elsewhere are unaffected) or `features/types.ts` for feature-layer-only types; avoid `any`.
6. Run `npm run typecheck` after every TypeScript change, then `npm run lint` and `npm run test`.
   Component tests sit next to the component (`*.test.tsx`, Vitest + jsdom); browser journeys are
   Playwright specs in `frontend/e2e/` with the API stubbed in-browser (`e2e/support/api-stub.ts`). The
   healthcare specs also call the real worker (`e2e/support/worker_bridge.py`) and, for Onboarding/STTM,
   the real backend on a throwaway SQLite file (`e2e/support/backend_bridge.py`), so they need Python with
   the backend/worker requirements installed.

### Change the artifact generator (spec → ADF JSON)

The generator is a pure function of `(LogicalPipelineSpec, TemplateMetadata)` and **exists twice on
purpose**:

- `frontend/lib/adf/{spec,pipeline-codegen,connections}.ts` — the reference implementation
- `worker/app/generator/` — the Python port the worker actually runs

Change both, in the same PR, and keep them in lockstep — the `generator-parity` CI job
(`worker/tests/test_generator_parity.py`) bridges Node and Python to prove they produce matching
output, and it will fail the build if they diverge. Run it locally before pushing:
```
cd worker && pytest tests/test_generator_parity.py
```
(needs Node available on PATH, since it shells out to run the frontend codegen). Fixtures are
`worker/tests/fixtures/spec_draft_*.json`; add one when you add a generator option.

The validator rejects shapes ADF won't accept (`validate_resource_schema` in
`worker/app/validator/rules.py` — Switch `on`/`defaultActivities`, ForEach `output.value`, Copy
`inputs`/`outputs`, Lookup `sqlReaderQuery`, dataset `typeProperties`, and so on). If you change an activity or dataset shape, check it there too, or every generated pipeline will
start failing Validate. `validate_sql_dialect` warns when generated SQL contains Spark/ANSI syntax — keep
generated SQL T-SQL.

If the change affects what a *valid* artifact looks like (a name, a required field, a secret-bearing
property), also run `worker/tests/test_generator_validation_integration.py` — it feeds real fixtures
through the generator and then the validator, so it catches a generator change that accidentally
produces something the validator now rejects (or stops rejecting). Its frontend counterpart is
`frontend/lib/adf/artifact-validation.test.ts` (`npm run test` in `frontend/`).

### Add a Silver/Gold transformation type (medallion layer + STTM registry)

The Silver/Gold transformation vocabulary (`conform_columns`/`dedupe`/`cast_schema`/`scd2`/`filter`/
`join`/`aggregate`/`select_columns`; which layer allows which is `SILVER_TRANSFORM_TYPES`/
`GOLD_TRANSFORM_TYPES`) is deliberately a small, finite, mirrored enum — both the Build wizard's
hand-authored `TransformationsEditor` and the STTM Excel registry (§33) produce the exact same
`TransformationRule` shape, so a new type only needs to be added in one place per side:

1. `frontend/lib/adf/medallion.ts` — add the new literal to `SilverTransformType`/`GoldTransformType`
   (or both, if it applies to either layer — see §33.3's note on why `join`/`scd2` deliberately aren't
   split by layer), list it in the relevant `MEDALLION_LAYER_INFO[layer].transformTypes` array, add its
   required-field checks to `validateTransformationRules`, and add its T-SQL shape to
   `transformationRuleSql`.
2. Mirror all four changes in `worker/app/generator/medallion.py` — this is the code path pipeline
   generation actually runs through; the frontend module is the reference copy (§23.12 #2's parity
   convention applies here exactly like the rest of the generator).
3. If the `TransformationsEditor` needs new input fields for the type (e.g. a new parameter
   `TransformationRule` doesn't already have a slot for), add them to `TRANSFORM_FIELD_CONFIG` in
   `components/rules/rule-editors.tsx` (shared by the Build wizard and the Onboarding registry drawer).
4. Update the STTM Excel template — `lib/adf/sttm.ts`'s column defs and the
   `Available_Transformations` reference-sheet row — and the backend's vendored validator
   (`backend/app/onboarding/sttm.py`) plus its authoritative parser (`sttm_parser.py`), in the same PR.
5. New cases in `medallion.test.ts` + `test_layers.py`, and a `test_generator_parity.py` fixture if the
   change affects generated SQL output (it almost always does).

### Change the Data Modular STTM import

Onboarding's Silver/Gold tabs accept data_moduler's `STTM_Data_Modular_Template_v2b.xlsx` (one workbook
per table, or the layer-wise `.zip`) as well as the legacy per-sheet layout. The pieces:

- `backend/app/onboarding/sttm_data_modular.py` — the adapter. It reads Section 1/Section 2 of a sheet and
  turns it into the same registry body the legacy parser produces (transformations, DQ rules, load policy,
  governance fields). `to_tsql` rewrites Spark/ANSI expressions to T-SQL (innermost call first, literals
  protected); `untranslated_spark` lists what's left so the import can warn; `regex_to_like` turns a regex
  DQ rule into a LIKE/PATINDEX pattern only when that's exact.
- `frontend/lib/adf/sttm-data-modular.ts` — a browser preview of the same workbook. The backend parse is
  authoritative; keep the two consistent when you change a field.
- `frontend/public/templates/STTM_Data_Modular_Template_v2b.xlsx` is the downloadable template.
  `test_shipped_template_copies_are_identical` fails if it and the test fixture in
  `backend/tests/fixtures/data_modular/` drift apart. The fixtures were made by data_moduler's own exporter.
- Tests: `backend/tests/test_sttm_data_modular.py`. Known limits are reported as warnings in the adapter's import output.

### Target path standardization (canonical ADF Git folder layout)

Every artifact type has one fixed destination folder inside the target Git repo — this is what
makes the pushed repo something ADF Studio's own Git integration can actually import, instead of a
flat dump of JSON files. The layout is:

```
<artifactRoot>/
  factory/            <factoryName>.json
  integrationRuntime/ IR_*.json
  managedVirtualNetwork/  managedPrivateEndpoint/
  credential/         *.json
  linkedService/      LS_*.json
  dataset/            DS_*.json
  dataflow/           DF_*.json
  pipeline/           PL_*.json
  trigger/            TR_*.json
```

`<artifactRoot>` itself is a Project Settings Git default (`artifactRoot`, e.g. `adf/artifacts/`) —
editable per project, not hard-coded.

The important part for how the code is actually organized:

- **The generator never bakes the folder into the file path.** `frontend/lib/adf/pipeline-codegen.ts`
  and `worker/app/generator/` only ever produce a bare `fileName` (e.g. `PL_foo_master.json`) plus an
  `artifactType`. Folder placement is applied exactly once, at push time, in
  `worker/app/jobs/runner.py::_artifact_blobs`, via the `_ADF_FOLDER_BY_ARTIFACT_TYPE` lookup table —
  `{artifactRoot}/{folder}/{fileName}`.
- Artifact types with no entry in that table (`parameter`/runtime-parameters snapshot, the
  control-table SQL, `manifest.json`, `lineage.json`) are **not** real ADF Git resources, so they
  intentionally stay at the artifact root instead of getting a folder. Onboarding's Sync to Git
  writes its files under `<artifactRoot>/metadata/`.
- If you add a new artifact type (e.g. `dataflow`, `integrationRuntime`), you must add its folder to
  `_ADF_FOLDER_BY_ARTIFACT_TYPE` in `runner.py` (and to the layout above) — adding
  the type to the generator alone will silently push it to the artifact root with no folder.
- Commit order is also standardized, not incidental: `integrationRuntime → managedVirtualNetwork →
  managedPrivateEndpoint → credential → linkedService → dataset → dataflow → pipeline (children before
  the master that `ExecutePipeline`s them) → trigger → factory/globalParameters`. This is the same
  order `manifest.json` records (§23.9/§23.4a) — don't reorder the push logic without updating both.

### Add a database migration

Always via Alembic, never a hand-edited schema:
```
docker compose run --rm api alembic revision --autogenerate -m "add_foo_column"
docker compose run --rm api alembic upgrade head    # or: make migrate
```
Review the autogenerated file before committing — check index/constraint changes especially, since
`--autogenerate` doesn't always catch them correctly. If you declare an index, give it an explicit
name in the model's `__table_args__` matching the migration, or the CI drift check will propose
dropping it.

Then update the docs in the same PR:

1. Add the table/column to `docs/DDL.md` §1 **with an inline `--` comment** (what it holds, allowed
   values, where it is written from), note the migration number above the table, and add any index to
   the index list. Mark anything not yet migrated `-- (planned)`.
2. Regenerate the runnable script: `python docs/scripts/build_ddl_sql.py` (writes
   `docs/adf_forge_ddl.sql`, dropping planned items and emitting `COMMENT ON COLUMN`).
3. If the worker reads the table, mirror the model in `worker/app/db/models.py`.

`docs/DDL.md` documents both stores this app touches: `app` (this app's own Postgres metastore) and
`control` (the customer's ADF control-table schema on Azure SQL, which this app only ever *generates
as a Git artifact* and never queries). Don't confuse the two when adding a table. If you change the
control-table SQL a generator emits, update DDL.md §2 and regenerate the script too.

## 5. Testing

| Suite | Command | Notes |
|---|---|---|
| Frontend typecheck | `npm run typecheck` (from `frontend/`) | Run after every TS change |
| Frontend lint | `npm run lint` | |
| Frontend unit/component tests | `npm run test` | Vitest + jsdom: `lib/adf/*.test.ts`, `lib/*.test.ts`, and `*.test.tsx` next to components |
| Frontend browser E2E | `npm run test:e2e` | Playwright, `frontend/e2e/*.spec.ts` — 8 specs: Build → Generate → Validate, Onboarding CSV import, Push to Git, STTM import, Build registry picker, and the healthcare scenarios / medallion / button-sweep specs. API stubbed in-browser; the healthcare specs call the real worker and backend through `e2e/support/*_bridge.py` (needs Python). `PW_CHANNEL=chrome` or `msedge` uses an installed browser instead of downloading Chromium |
| Frontend build | `npm run build` | Also run by CI |
| Backend tests | `pytest` (from `backend/`) or `docker compose run --rm api pytest` | In-memory SQLite, no external DB needed. Set `TEST_DATABASE_URL` to run the same suite against another database (e.g. a throwaway Postgres) — not yet proven on Postgres. Skipped tests are documented scenario placeholders |
| Endpoint contract audit | `cd backend && pytest tests/test_endpoint_contract.py` | Every route gated or listed open, 403 for each disallowed role. The "every route has a 2xx test" check only runs in a full-suite run |
| Worker tests | `pytest` (from `worker/`) or `docker compose run --rm worker pytest` | Includes `test_generator_parity.py` and `test_generator_validation_integration.py` |
| Generator parity only | `cd worker && pytest tests/test_generator_parity.py` | Proves frontend/worker codegen match |
| Generator → validator integration only | `cd worker && pytest tests/test_generator_validation_integration.py` | Feeds real `SpecDraft` fixtures through the real generator, then the real validator — proves a clean pipeline passes and a tampered/bad one is caught, for every artifact type the generator emits (pipeline, dataset, linked service, trigger, factory, control-table SQL), not just hand-written fixtures. `frontend/lib/adf/artifact-validation.test.ts` (run via `npm run test` in `frontend/`) is its frontend-generator counterpart. |
| Concurrency only | `cd backend && pytest tests/test_concurrency.py` | 25 simulated people across every §11 role, logged in under `AUTH_MODE=password` and hitting the API **at the same time** via `ThreadPoolExecutor` — proves per-request session identity never bleeds across threads and §27.4 scope/§11 role checks hold under concurrent load, plus a 25-way concurrent-failed-login race on one account. Runs its own file-backed SQLite engine with a real connection pool (`NullPool`) instead of the shared `conftest.py` engine — see the file's module docstring for why the shared one can't safely take concurrent requests from multiple threads. |

Run `npm run typecheck && npm run lint` and both `pytest` suites locally before opening a PR — these
are the same checks CI gates on (`.github/workflows/ci.yml`: `foundation`, `backend-tests`,
`worker-tests`, `generator-parity`, `migrations`, `backend-requirements-lock-check` /
`worker-requirements-lock-check`, `frontend-checks` (typecheck, lint, test, build), `frontend-e2e`,
`helm-chart` (`helm lint` + `helm template`)). CI hasn't been proven against every possible
change path, so don't treat a green push as a substitute for running these locally first.

## 6. Git workflow for *this* repo

(This is about contributing to the accelerator's own codebase — not to be confused with the
feature-branch-into-`dev` flow the *product* implements for a customer's ADF Git repo.)

- `main` is the default and protected branch here. Branch off `main`, open a PR back into `main`.
- Keep PRs small — one logical change per PR.
- If your change touches a model, run the migration step (§4 above) in the same PR — don't leave
  the schema and the migration out of sync.
- If your change touches the generator, update both copies (frontend + worker) in the same PR.

## 7. Things that will surprise you

- **`password` is the shipped auth mode; `mock` is still the code default.** `config/platform.env.example`
  sets `AUTH_MODE=password` (username or email + password, cookie sessions, CSRF, lockout, admin-created
  accounts only — no self-signup; `users.username` since migration 0021). If the key is missing entirely,
  `Settings` falls back to `mock` (a no-login user switcher), and the API logs a startup warning when that
  happens outside `APP_ENV=local`. `oidc`/`saml` fail closed with 501.
- **SSO is scaffolded, not implemented.** `backend/app/identity/` holds the `IdentityProvider` contract
  (`base.py`), `OidcIdentityProvider`/`SamlIdentityProvider` stubs, a `provision_user` JIT stub and a
  `get_identity_provider` factory; `GET /api/v1/auth/sso/{configurationId}/login` and `.../callback` exist
  and answer 501 `sso_not_implemented`. Each stub's docstring lists the exact steps to implement. Admin →
  Users can already store SSO configurations and group→role mappings (`app.sso_configurations`), and
  `SSO_CALLBACK_BASE_URL` is the one app-wide setting.
- **There is no demo/fixture-data mode.** `useResource()` falls back to a neutral empty value
  (`lib/api/empty-values.ts`) automatically, per-call, whenever the API throws — never fabricated
  sample data. If you add a required field to a resource type, add a sensible empty default there
  too, or the type won't compile.
- **RBAC is enforced on every router except `connectors.py`**, which is deliberately left ungated
  (see the comment at the top of that file) — don't assume an unguarded endpoint is a bug without
  checking first.
- **Secrets are references only, everywhere.** Never store or log a literal secret value — Key
  Vault name / secret reference only. This is an always-on `block` policy, not configurable.
- **Never push to `dev` from the UI**, and a feature branch equal to the protected base branch must
  be rejected — this is a hard rule, not a lint suggestion.
- **Pipeline form edits never mutate project defaults** — only an explicit "Save to Project
  Settings" action does. If you're wiring a form and it looks like it should autosave to project
  settings, it shouldn't.
- **The generator exists twice.** If you only change `frontend/lib/adf/`, the worker will generate
  different output than the UI previewed — `generator-parity` in CI exists specifically to catch
  this.
- **Some logic exists three times.** Naming rules and STTM/transformation validation are also
  vendored into the backend (`backend/app/standards.py`, `backend/app/onboarding/sttm.py`), because
  the services can't import each other. Change all copies together.
- **The Standards compliance scan runs in the browser** (`frontend/lib/adf/compliance.ts`); only two
  rules are enforced server-side: naming (validate + a `422 policy_violation` push gate on `block`) and the
  project's `requiredTags` policy (push gate on `block`, checked against pipeline annotations).
- **Generated file names and their Git folder are two separate concerns.** The generator only
  produces a bare file name; the canonical `linkedService/ dataset/ pipeline/ trigger/ factory/`
  folder per artifact type is applied later, only in
  `worker/app/jobs/runner.py::_artifact_blobs`. Don't go looking for folder logic in the generator —
  it isn't there, and it isn't previewed in the frontend either. See [Target path
  standardization](#target-path-standardization-canonical-adf-git-folder-layout) above.

### Dependency pinning gotcha

`backend/` and `worker/` each have four Python dependency files:

| File | Purpose |
|---|---|
| `requirements.in` | Direct runtime deps as ranges — must mirror `pyproject.toml` |
| `requirements-dev.in` | Same, plus test extras |
| `requirements.txt` | Exact-pinned lock — what the Docker image and deploy actually install |
| `requirements-dev.txt` | Exact-pinned lock — what CI and local dev install |

If you change a dependency range in `pyproject.toml`, update the matching `requirements*.in` file
the same way, then run `make lock` (needs Docker Desktop running) to regenerate both pinned files.
Don't hand-edit the pinned `.txt` files. CI's `*-requirements-lock-check` jobs install the pinned
lock fresh and import the app — a bad pin fails there, not at deploy time.

## 8. Where to look when something's confusing

- Unsure what a screen does or how the product hangs together from a user's point of view (not the
  code's)? `docs/USER_GUIDE.md` — or the in-app "User guide" dialog, same content.
- Unsure if a feature is implemented, stubbed, or target design? Check `README.md`'s known-gaps list,
  then the code and its tests.
- Unsure which of two similar-looking files is canonical (e.g. the two copies of the generator)?
  `frontend/lib/adf/` is the reference implementation; `worker/app/generator/` is the running port.
- Unsure about a table's purpose, a column's meaning, or which schema it lives in? `docs/DDL.md` —
  every column is commented.
- Need to trigger or restart a generated pipeline in production? See `docs/DDL.md`'s control-table section (`TaskIdFilter`).
- Unsure about a config key? `config/platform.env.example` documents every key inline; `README.md`
  groups them by concern.
- Need to build/tag/push an image, deploy via GitHub or GitLab, or move to App Service/Functions/AKS
  instead of Container Apps? `docs/DEPLOYMENT_GUIDE.md`.
