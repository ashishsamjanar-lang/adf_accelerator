# ADF Pipeline Accelerator

A metadata-driven control plane for Azure Data Factory. Users configure reusable project
metadata, fill a schema-rendered Build form, generate ADF artifacts, validate them, and push
selected artifacts to a Git feature branch for PR review into `dev`. It is a modular monolith:
ADF executes pipelines, Git is the source of truth for artifacts, and Postgres holds application
state (projects, drafts, artifacts, jobs, audit metadata). The product intentionally stops at
"PR into `dev`" — there is no deploy/release UI and the app never runs a pipeline or touches live
data.

**New to this repo? Start with [`docs/DEVELOPER_GUIDE.md`](docs/DEVELOPER_GUIDE.md)** — a
step-by-step walkthrough of setup and the most common tasks (adding an endpoint, a frontend
feature, a migration, a connector) plus the gotchas that aren't obvious from the code.
**Using the app rather than building it? See [`docs/USER_GUIDE.md`](docs/USER_GUIDE.md)** — features,
the two sample-project reference guides, and a screen-by-screen walkthrough (also built into the app
as the in-app "User guide" dialog).

## Repository layout

```
frontend/   Next.js 15 (App Router) + React 19 + TypeScript UI
backend/    FastAPI API: domain contracts, persistence (SQLAlchemy + Alembic), auth + RBAC
worker/     Stateless Python worker: job poll loop, artifact generator, validator, Git adapters,
            connector registry, Git catalog indexer, Azure (live ARM check, Purview) clients
config/     The single runtime parameter file contract (`platform.env`)
infra/      main.bicep (Azure Container Apps) and helm/adf-accelerator/ (any Kubernetes) —
            deploy the app itself; Postgres and Key Vault are bring-your-own
docs/       DEVELOPER_GUIDE.md, USER_GUIDE.md, DEPLOYMENT_GUIDE.md, DDL.md (column-commented
            reference for both stores), adf_forge_ddl.sql (runnable, generated from DDL.md by
            docs/scripts/build_ddl_sql.py)
.github/workflows/   CI + CD entry points (GitHub Actions)
.gitlab-ci.yml        CI + CD entry point (GitLab), mirrors .github/workflows/
Makefile                 Convenience targets that wrap docker compose / pytest
```

### `frontend/`

Next.js 15, React 19, TypeScript, Tailwind 3, a shadcn-style component library.

```
app/                    Routes + globals.css (design tokens — source of truth for the UI palette)
components/shell/       Sidebar, topbar, navigation.ts (single left sidebar)
components/common/      Shared presentational bits: page-header, empty-state, status-pill,
                         lifecycle-stepper
components/{git,validation,rules}/   Domain component groups (rules/ = transformation + DQ
                         rule editors shared by Build and Onboarding)
components/ui/          shadcn-style primitives (button, card, dialog, select, tabs, ...)
features/<domain>/      One folder per nav area: home, teams, projects, pipelines, build, drafts,
                         git, catalog, onboarding, templates, artifacts, administration, samples,
                         auth. The Build wizard is features/build/create-pipeline-wizard.tsx (shell)
                         + use-pipeline-wizard.ts (state) + wizard-*-step.tsx (one per step)
lib/api/                Backend I/O:
                           client.ts  — typed fetch client; all HTTP goes through here
                           hooks.tsx  — useResource(), the hook every resource fetch goes through
                           data.ts    — resource-level fetchers (useAcceleratorData)
                           empty-values.ts — neutral empty/loading-and-error fallback values
                                            (no fabricated sample data)
lib/adf/                The metadata → artifact engine (the reference implementation of the
                         Agent-layer generator; the running port lives in worker/app/generator/):
                           spec.ts             — wizard state → LogicalPipelineSpec
                           pipeline-codegen.ts — spec → ADF JSON
                           connections.ts       — connector schemas for dynamic forms
                           connection-store.ts  — singleton feeding the Build wizard
                           medallion.ts         — Bronze/Silver/Gold layers, transformations, audit columns
                           qualifier.ts         — connector-aware object-name parsing/sanitizing
                           onboarding.ts, sttm.ts, sttm-data-modular.ts, mapping-profiles.ts — Onboarding templates/parsers
                                                  (sttm-data-modular.ts = browser preview of a
                                                  Data Modular STTM workbook)
                           compliance.ts        — Standards compliance scan
                           naming.ts, standalone.ts
lib/{state,format,utils}.ts   Shared leaf helpers (lifecycle/RBAC state, formatters, shadcn cn)
types/*.ts, features/types.ts   Domain types, split by area (core, pipeline, git, onboarding, sttm,
                         catalog, ...) and re-exported from types/index.ts
e2e/                     Playwright browser journeys. Most stub the API in-browser
                         (support/api-stub.ts); the healthcare specs also call the real worker
                         (support/worker_bridge.py) and real backend on SQLite
                         (support/backend_bridge.py) for generate/validate and Onboarding/STTM
middleware.ts            Resolves BACKEND_INTERNAL_URL server-side per request (never baked into
                         the client bundle)
scripts/                 export-generated-artifacts.ts — used by the generator-parity CI job
```

### `backend/`

FastAPI, Pydantic settings, SQLAlchemy 2, Alembic, psycopg3. All endpoints are served under
`/api/v1/*` (no unversioned alias).

```
app/main.py           FastAPI app, health endpoint, router registration
app/api/               One APIRouter per resource, mounted under /api/v1: auth, teams, users,
                       projects, templates, pipelines, artifacts, git, connections, connectors,
                       managed_identities, onboarding, registry (Silver/Gold STTM), catalog,
                       integrations, sso, admin — plus deps.py, models.py, schemas.py
app/models.py          SQLAlchemy ORM models (the metastore schema — see docs/DDL.md)
app/repository.py      Data-access layer
app/services.py        Application/business logic (+ catalog_services.py, integration_services.py)
app/seed.py            Local/dev seed data
app/manage.py          CLI: create-platform-admin, purge-audit-log
app/rbac.py            §11 permission registry + require_permission / require_scope — on every
                       router except connectors.py (deliberately ungated)
app/security.py        Password hashing, sessions, CSRF (AUTH_MODE=password)
app/identity/           SSO scaffolding only (oidc/saml stubs, JIT provisioning); /auth/sso/* 501
app/standards.py        Naming-rule evaluator (vendored in parity with the frontend and worker)
app/onboarding/         STTM workbook parser + validator, incl. sttm_data_modular.py (imports the
                       Data Modeling Accelerator's STTM template, single .xlsx or layer-wise .zip,
                       and translates its Spark SQL to T-SQL)
app/connectors/         Connector schema + serialization shared with the worker's registry
app/crypto.py           Fernet-based encryption helpers (Git token at rest)
app/db/                 Engine/session setup
alembic/                Migrations 0001_initial_metastore … 0022_registry_governance_fields
tests/                  pytest suite, one file per router/concern
docker-entrypoint.sh     Runs `alembic upgrade head` on every container start
```

### `worker/`

Stateless Python worker with a real job-poll loop.

```
app/jobs/loop.py        Claims `Job` rows FOR UPDATE SKIP LOCKED
app/jobs/runner.py      Dispatches claimed jobs: generate, validate, git_push, onboarding_sync,
                         live_check, purview_push, catalog_refresh, catalog_change (catalog.py)
app/generator/           spec.py, codegen.py, manifest.py, medallion.py, qualifier.py — a Python
                         port of frontend/lib/adf, kept in parity with it (generator-parity CI job)
app/validator/           rules.py, naming.py — layered validation (syntax, resource schema,
                         cross-references, expressions, security posture, policy)
app/gitops/               adapter.py (interface), github.py, gitlab.py, azure_repos.py (REST
                         adapters), simulated.py (offline SimulatedAdapter), credential_vault.py
app/azure/                arm_validate.py (layer-6 validate-mode check, never deploys), purview.py
app/connectors/           dataset.py, linked_service.py, schemas.py, serialize.py, validation.py,
                         drafts.py — the connector registry engine
app/db/                   Models/session shared with the backend's schema
tests/                    pytest suite incl. test_generator_parity.py (bridges the frontend
                         codegen via Node to prove worker/frontend generator output matches)
```

### `config/`

`platform.env` (git-ignored) is the **only** settings file — there is no `frontend/.env*`. The api
and worker resolve it from the repo root, `frontend/next.config.ts` loads it, and every compose
service reads it; set `PLATFORM_ENV_FILE=/path/to/other.env` to point all three at a different file.
Deployment tooling is expected to map the same keys into its target environment. `platform.env.example` documents every key (see [Configuration](#configuration)
below).

### `infra/`

`main.bicep` deploys the app itself (frontend, api, worker) to Azure Container Apps;
`helm/adf-accelerator/` is the cloud-agnostic equivalent for any Kubernetes (EKS/GKE/AKS/on-prem),
with secrets from a plain Kubernetes Secret you populate however you like. Postgres and Key Vault are
bring-your-own in both — never provisioned by the app. See `infra/README.md` and
`infra/helm/adf-accelerator/README.md`.

## Architecture

Four Docker Compose services: `frontend`, `api`, `worker`, `postgres`.

- **Frontend** renders the UI and talks to the API through a typed client
  (`frontend/lib/api/client.ts`); it never calls ADF, Git, or Postgres directly. When the API is
  unreachable, `useResource()` (`frontend/lib/api/hooks.tsx`) keeps each resource at a neutral
  empty value (`lib/api/empty-values.ts` — no fabricated sample data) and the shell shows a
  "backend unreachable" error banner (`components/shell/api-error-banner.tsx`).
- **API** (`backend/`) owns the metastore (projects, teams, templates, pipeline drafts, artifacts,
  Git state, jobs, environments) and exposes it under `/api/v1/*`.
- **Worker** (`worker/`) polls the `jobs` table, and for each job runs the generator (spec → ADF
  artifact set + `manifest.json`), the validator (layered: syntax, resource schema, references,
  expressions, security, policy), and — on push — a Git adapter (GitHub, GitLab or Azure Repos REST,
  or an offline `SimulatedAdapter` for local/dev) that opens a feature branch and a PR into `dev`.
  It also indexes the Git repo for the Catalog, syncs the Onboarding registry to Git, and runs the
  opt-in Azure live check / Purview push. The worker never pushes to the protected base branch.
- **Postgres** is the single source of application state; ADF and Git remain the systems of record
  for pipeline execution and artifact content respectively.

The generator is a pure function of `(LogicalPipelineSpec, TemplateMetadata)`. It exists twice by
design: `frontend/lib/adf/{spec,pipeline-codegen,connections}.ts` is the reference implementation
and `worker/app/generator/` is the running port that the worker actually executes; a CI job
(`generator-parity`) proves the two stay in lockstep.

See [`docs/DEVELOPER_GUIDE.md`](docs/DEVELOPER_GUIDE.md) for how these layers fit together when
making a change.

## Local setup

Prerequisites: Docker Desktop with Compose, Node.js 22+ for direct frontend work, and Python
3.12+ for direct backend/worker work.

1. Copy the example env file:

   ```
   cp config/platform.env.example config/platform.env
   ```

   (or `make setup`, which does the same via PowerShell `Copy-Item`).

2. Adjust values in `config/platform.env` only — see [Configuration](#configuration). Never place
   real credentials in this file; use Key Vault references for deployed environments.
3. Start the local stack:

   ```
   docker compose up --build
   ```

   (or `make up`). The `api` container runs `alembic upgrade head` automatically on every start.

4. Check the API at `http://localhost:8000/health` and the frontend at `http://localhost:3000`.

### Working on one layer at a time

You don't need the full stack running to iterate on a single layer:

- **Frontend only**: `cd frontend && npm install && npm run dev` (http://localhost:3000). If
  `http://localhost:8000` isn't running it shows empty states and a "backend unreachable" banner —
  there is no mock data.
- **Backend only**: `cd backend && pip install -r requirements-dev.txt`, then run the app with
  `uvicorn app.main:app --reload` against a local/dev Postgres (or just run `pytest`, which uses
  in-memory SQLite via `backend/tests/conftest.py`; set `TEST_DATABASE_URL` to run it against a
  real Postgres instead).
- **Worker only**: `cd worker && pip install -r requirements-dev.txt`, then `pytest`.

### Building images locally

`docker compose up --build` already builds all three images for the compose stack. To build one
in isolation (e.g. to inspect it, push it by hand, or test the frontend's `production` target
rather than compose's `dev` target), or to build in the cloud with `az acr build` instead of a
local Docker daemon, see [`docs/DEPLOYMENT_GUIDE.md` §10](docs/DEPLOYMENT_GUIDE.md#10-generating-build-artifacts-locally).

### Makefile targets

| Target | What it does |
|---|---|
| `make setup` | Copies `config/platform.env.example` to `config/platform.env` |
| `make up` | `docker compose up --build` |
| `make down` | `docker compose down` |
| `make logs` | `docker compose logs -f` |
| `make test` | `docker compose run --rm api pytest` |
| `make worker-test` | `docker compose run --rm worker pytest` |
| `make migrate` | `docker compose run --rm api alembic upgrade head` (apply pending migrations to a stack that's already up, without restarting it) |
| `make lock` | Regenerate `backend/` and `worker/` pinned `requirements*.txt` from `requirements*.in` (see [Dependency pinning](#dependency-pinning)) |

## Dependency pinning

`backend/` and `worker/` each carry four Python dependency files, split the same way on both:

| File | Purpose |
|---|---|
| `requirements.in` | Direct runtime deps, as ranges — must mirror `pyproject.toml`'s `[project] dependencies` |
| `requirements-dev.in` | The above `-r` included, plus the `test` extra (pytest, httpx) |
| `requirements.txt` | Exact-pinned lock of `requirements.in` — what `Dockerfile` and `deploy.yml` actually install |
| `requirements-dev.txt` | Exact-pinned lock of `requirements-dev.in` — what CI and local dev install |

`pyproject.toml` still declares the real ranges (it's what `setuptools` reads for packaging), but a
range like `fastapi>=0.115,<1` resolves to whatever the newest matching release is *on the day you
build* — a Docker rebuild next month can silently pick up a different version than the one CI last
tested, which is exactly the kind of drift that shows up as a deployment surprise instead of a local
one. The pinned `requirements.txt` files remove that: the same versions install today, next month,
and in the image `deploy.yml` ships to Container Apps.

After changing a dependency range in `pyproject.toml`, update the matching `requirements*.in` file
the same way, then run `make lock` (needs Docker Desktop running) to regenerate both pinned files.
CI's `backend-requirements-lock-check` / `worker-requirements-lock-check` jobs then install the new
lock from a clean environment and import the app against it — a bad or incompatible pin fails there,
before it ever reaches a build or deploy step.

## Commands reference

Run frontend commands from `frontend/`, backend/worker commands from their respective directories.

| Task | Command |
|---|---|
| Frontend dev server | `npm run dev` (http://localhost:3000) |
| Frontend typecheck | `npm run typecheck` — run after every TypeScript change |
| Frontend lint | `npm run lint` |
| Frontend unit/component tests | `npm run test` (Vitest + jsdom) |
| Frontend browser E2E | `npm run test:e2e` (Playwright; `PW_CHANNEL=chrome` or `PW_CHANNEL=msedge` to use an installed browser) |
| Frontend build | `npm run build` |
| Frontend generator-parity export | `npm run parity:export` (used by the `generator-parity` CI job) |
| Backend tests | `docker compose run --rm api pytest` (or `pytest` in `backend/` with Python 3.12) |
| Worker tests | `docker compose run --rm worker pytest` (or `pytest` in `worker/`) |
| Apply DB migrations | `docker compose run --rm api alembic upgrade head` |
| Regenerate the runnable DDL | `python docs/scripts/build_ddl_sql.py` (after editing `docs/DDL.md`) |
| Purge old audit entries | `docker compose run --rm api python -m app.manage purge-audit-log` (uses `AUDIT_RETENTION_DAYS`) |
| Full stack | `docker compose up --build` / `make up` |

## Configuration

`config/platform.env` (copied from `config/platform.env.example`, git-ignored) is the single
input surface for local runtime parameters. Compose passes it to every service via `env_file`, and
deployment tooling is expected to map the same keys into its target environment. Secrets must
remain in Azure Key Vault (or an equivalent local secret mechanism) and must never be committed.

Key groups in `platform.env.example`:

- **App**: `APP_ENV`, `APP_NAME`, `LOG_LEVEL`
- **Database**: `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `DATABASE_URL`,
  `DB_POOL_SIZE`, `DB_MAX_OVERFLOW`
- **API/frontend wiring**: `API_HOST`, `API_PORT`, `BACKEND_INTERNAL_URL` (server-only — read at
  request time by `frontend/middleware.ts` so a built frontend image can be promoted across
  environments unmodified) and `NEXT_PUBLIC_API_BASE_URL` (only needed if the browser must call the
  API origin directly instead of through the same-origin proxy; baked into the client bundle at
  build time if set, so a build carrying this is pinned to one environment — leave it unset in the
  normal case)
- **Auth**: `AUTH_MODE` (the example file ships `password` — username-or-email + password with
  cookie sessions; the code default if unset is still `mock`, a dev user switcher; `oidc`/`saml`
  real SSO isn't implemented yet), `AUTH_IDLE_TIMEOUT_MINUTES`/`AUTH_ABSOLUTE_TIMEOUT_HOURS`/
  `AUTH_MAX_FAILED_LOGINS`/`AUTH_LOCKOUT_MINUTES`/`AUTH_MIN_PASSWORD_LENGTH` (all
  `AUTH_MODE=password` only), `SSO_CALLBACK_BASE_URL` (reserved for the SSO scaffold)
- **Worker**: `WORKER_POLL_INTERVAL`, `WORKER_MAX_ATTEMPTS`
- **ADF/Git targets**: `KEY_VAULT_NAME`, `ADF_SUBSCRIPTION_ID`, `ADF_RESOURCE_GROUP`, `ADF_FACTORY_NAME`,
  `SOURCE_REPOSITORY_URL`
- **Git push credentials**: `GIT_TOKEN` (fallback credential for the worker when a push carries no
  per-project token and no Key Vault reference resolves; leave blank to use the offline
  `SimulatedAdapter`; never commit a real token) and `GIT_TOKEN_FERNET_KEY` (encrypts a per-push
  Git token at rest between the API and worker — must be identical in both services; generate with
  `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`;
  left unset in local dev, the API/worker fall back to a shared, publicly-known dev key and log a
  warning)
- **Audit**: `AUDIT_RETENTION_DAYS` (read by `python -m app.manage purge-audit-log`; no API route
  purges)

### Bootstrapping the platform admin (`AUTH_MODE=password`)

There is no sign-up page — the first (and every) `PLATFORM_ADMIN` account is provisioned from the
backend, then that admin onboards everyone else via **Administration → Users** (`POST /admin/users`).
With `AUTH_MODE=password` set:

```
docker compose run --rm api python -m app.manage create-platform-admin \
  --email you@example.com --name "Your Name" [--username you] [--password '...']
```

Idempotent (safe to re-run — promotes an existing row by email rather than erroring). `--username`
is the login name (derived from the email if omitted); users can sign in with either username or
email. Without `--password` it prints a one-time temporary password that must be changed on first
login. When adding users, an admin can set a username and optionally choose the initial password.
Under `AUTH_MODE=mock` none of this is needed.

## Backend-unreachable behavior

There is no demo/fixture-data mode. `frontend/lib/api/hooks.tsx`'s `useResource` keeps each
resource at a neutral empty value (`frontend/lib/api/empty-values.ts`) whenever a live API call
throws, so a failed fetch renders a real loading/empty state — never fabricated sample content —
and the shell surfaces a single "backend unreachable" error banner
(`components/shell/api-error-banner.tsx`). Every feature is expected to work both live and empty.

## Product rules that shape the code

These are non-negotiable and enforced by convention across all layers:

1. No Release/Deployment UI — no deploy-to-DEV/QA/PROD, no release states, no deployment wizard.
2. Metadata-driven, schema-rendered forms only — no fabricated values, no hand-written per-connector
   JSX. Pipeline pattern and load behavior are form fields, not template cards.
3. The header shows only the authenticated user's name — no greetings.
4. Git flow: push to a feature branch, PR into `dev`. Never push to `dev` from the UI; a feature
   branch equal to the protected base branch is rejected.
5. No fabricated business values (server/db/schema/table/container/branch/repo names, retry counts,
   parallelism, filters, watermark columns) — everything comes from user input, a saved project
   default, a template default, or a visibly-derived value.
6. Secrets are references only (Key Vault name / secret ref) — never stored or logged as a value.
7. Pipeline form edits never mutate project defaults; only an explicit "Save to Project Settings"
   does.
8. Artifact/Git states are limited to `DRAFT → GENERATED → VALIDATED → READY_TO_PUSH → PUSHING →
   PUSHED → PR_CREATED` (+ `*_FAILED`) — never a deployment state like `DEV_DEPLOYED`.

## CI

`.github/workflows/ci.yml` runs on every push/PR:

| Job | What it checks |
|---|---|
| `foundation` | `docker compose config` validity + builds the backend, worker, and frontend images |
| `backend-tests` | `pytest` against the backend package (Python 3.12) |
| `worker-tests` | `pytest` against the worker package (Python 3.12) |
| `generator-parity` | Runs `worker/tests/test_generator_parity.py`, which bridges Node (frontend codegen) and Python (worker codegen) to prove the two generators produce matching output |
| `migrations` | Spins up a real Postgres service container, runs `alembic upgrade head`, then runs `alembic revision --autogenerate` and fails if it produces a non-empty diff — i.e. catches a model change with no matching migration |
| `backend-requirements-lock-check` / `worker-requirements-lock-check` | Installs the pinned `requirements-dev.txt` from a clean environment, runs `pip check`, and imports the app — catches a bad/stale pin (see [Dependency pinning](#dependency-pinning)) before it reaches a build or deploy |
| `frontend-checks` | `npm ci && npm run typecheck && npm run lint && npm run test && npm run build` |
| `frontend-e2e` | Playwright journeys (`npm run test:e2e`), no Postgres service. The job sets up Node only, but the healthcare specs need Python with the backend/worker requirements installed, so they will fail there until the job adds it |
| `helm-chart` | `helm lint` + `helm template` on `infra/helm/adf-accelerator/` |

Run `npm run typecheck && npm run lint` and `pytest` locally before considering work done — these
jobs are the actual gate.

## Deployment

`.github/workflows/deploy.yml` (GitHub Actions) and `.gitlab-ci.yml` (GitLab CI/CD) both build and
push the three images and deploy `infra/main.bicep` to Azure Container Apps. For any other
Kubernetes cluster, use the Helm chart in `infra/helm/adf-accelerator/`. For the full guide —
one-time setup per CI system, which Azure services you need, the container-registry choice, and
what changes if you target App Service, Azure Functions, AKS or another Kubernetes — see
**[`docs/DEPLOYMENT_GUIDE.md`](docs/DEPLOYMENT_GUIDE.md)**. `infra/README.md` documents
`main.bicep`'s parameters and required Key Vault secrets specifically.

The database schema is created by Alembic on API start (`alembic upgrade head`). The generated control
tables (`adf_control_table.sql` and siblings) are *not* part of this app's deployment — they reach the
customer's Azure SQL through the normal PR flow, and a DBA applies them. `docs/DDL.md` documents both.

## Current status / known gaps

The end-to-end flow — Onboarding (Bronze registry + Silver/Gold STTM, including the Data Modeling
Accelerator's STTM template), Build, generate, layered
validation, push to a feature branch, PR into `dev`, and the Git-backed Catalog (browse, raw edit,
rename, delete) — is implemented, with RBAC and team scoping on every router and `AUTH_MODE=password`
available. Notable items still open:

- **SSO sign-in** (`oidc`/`saml`) — configurations can be stored and `backend/app/identity/` holds
  the adapter scaffold, but `/auth/sso/*` still returns 501 (§27).
- **Standards enforcement beyond naming and required tags** — naming and `requiredTags` block at push;
  the other categories are reported by the compliance scan but don't block (§25).
- **Medallion runtime gaps** — Gold still reads Bronze rather than Silver directly (E2E-07), and a
  multi-object Silver/Gold pipeline doesn't yet land each object separately.
- **Unified Build remainder** — edit locks, a 3-way merge UI and the single-scroll Build form (§24).
- **AI-assisted capabilities** (§28) — target design only.


## Contributing / working conventions

- TypeScript everywhere on the frontend; add explicit domain types, avoid `any`.
- Match the surrounding file's style, imports, and naming; reuse `components/ui/` primitives and
  `lib/` helpers before adding new ones.
- Connector-specific forms are driven by `frontend/lib/adf/connections.ts` schemas — never
  duplicate a form per connector.
- API routes stay versioned under `/api/v1/`; provider-specific Git calls live behind an adapter
  (`worker/app/gitops/`), never inline in components.
- Backend: keep endpoints thin, types in Pydantic models, DB access via SQLAlchemy; add a `pytest`
  case per endpoint.
- Don't read `node_modules/`, `.next/`, or lockfiles; scope searches with a path or glob.

See [`docs/DEVELOPER_GUIDE.md`](docs/DEVELOPER_GUIDE.md) for the step-by-step workflow and the
gotchas that aren't obvious from the code.
