# Deployment Guide

> Last reviewed 2026-10-31: `infra/`, `config/platform.env.example` and the workflows haven't changed
> since 2026-09-27, so this guide is current. Every config key is documented in `README.md` →
> Configuration.

How to ship the ADF Forge **application** (frontend/backend/worker) to Azure — via GitHub Actions
or GitLab CI/CD, onto Azure Container Apps (the default), Azure App Service for Containers, Azure
Functions, or AKS — plus which Azure services you need around it and how to build the same images
locally before you push them anywhere.

Same scope boundary as `infra/README.md`: this is about hosting the *application*. It has nothing
to do with, and never touches, a customer's Azure Data Factory factory or the Generated Artifacts
repo — those are reached only through this app's own Git adapter.

None of the CI/CD-to-Azure paths below (GitHub Actions, GitLab CI, App Service, Functions, AKS)
have been run against a real subscription as part of writing this guide, other than the
already-existing `.github/workflows/deploy.yml` + `infra/main.bicep` pair, which itself carries the
same caveat (see `infra/README.md`). Run `az deployment group what-if` (or the AKS/Functions
equivalent below) before trusting any of this in a real environment, and adjust SKUs/scale limits to
your actual traffic.

## Contents

1. [Which image repository to use](#1-which-image-repository-to-use)
2. [Azure services you need](#2-azure-services-you-need)
3. [Deploying via GitHub Actions](#3-deploying-via-github-actions)
4. [Deploying via GitLab CI/CD](#4-deploying-via-gitlab-cicd)
5. [Compute target: Azure Container Apps (default)](#5-compute-target-azure-container-apps-default)
6. [Compute target: Azure App Service for Containers](#6-compute-target-azure-app-service-for-containers)
7. [Compute target: Azure Functions](#7-compute-target-azure-functions)
8. [Compute target: Azure Kubernetes Service (AKS)](#8-compute-target-azure-kubernetes-service-aks)
9. [Compute target: any other Kubernetes — the Helm chart (cloud-agnostic)](#9-compute-target-any-other-kubernetes--the-helm-chart-cloud-agnostic)
10. [Generating build artifacts locally](#10-generating-build-artifacts-locally)
11. [Running the full stack locally without Docker (native Postgres)](#11-running-the-full-stack-locally-without-docker-native-postgres)

---

## 1. Which image repository to use

| Registry | Use when | Notes |
|---|---|---|
| **GitHub Container Registry (`ghcr.io`)** — current default | You deploy via GitHub Actions and your compute target is Container Apps or App Service | Free for public/private repos at this scale, no separate resource to provision, auth via the workflow's own `GITHUB_TOKEN`. This is what `.github/workflows/deploy.yml` and `infra/main.bicep` use today. |
| **Azure Container Registry (ACR)** — recommended for AKS, and a reasonable choice for any Azure-only pipeline | You deploy to **AKS** (needed either way — AKS pulls via `az aks update --attach-acr`, which grants the cluster's kubelet identity `AcrPull` with no registry password to store at all), or you want Azure-native image scanning (Microsoft Defender for Cloud), geo-replication, or a private endpoint so images never cross the public internet | One extra resource + its own cost (Basic SKU is a few $/month). Works from either GitHub Actions (`azure/docker-login` or `az acr login`) or GitLab CI (`az acr login` after OIDC login). |
| **GitLab Container Registry** | You deploy via GitLab CI/CD and don't want a second registry to manage | Bundled with the GitLab project (`$CI_REGISTRY_IMAGE`), free on GitLab.com within storage quota, auth via the pipeline's own `CI_REGISTRY_PASSWORD`/OIDC job token — same "no separate resource" argument `ghcr.io` makes for GitHub. |

**Recommendation:** keep `ghcr.io` if you stay on GitHub Actions + Container Apps/App Service (no
change needed — it's already wired up). Switch to **ACR** the moment you adopt AKS. If you move CI
to GitLab, use the GitLab Container Registry unless you specifically want ACR's scanning/geo-replication.

Whichever you pick, the registry is only ever referenced by fully-qualified repository (server +
path), never hardcoded elsewhere — `infra/main.bicep`'s `containerRegistryServer` /
`backendImage`/`workerImage`/`frontendImage` params are how the registry choice reaches the compute
layer, and are the only place you need to point at a different registry.

## 2. Azure services you need

**Baseline — every compute target needs these:**

| Service | Purpose | Sizing note |
|---|---|---|
| **Azure Database for PostgreSQL – Flexible Server** | The metastore (projects, teams, templates, drafts, artifacts, jobs). Bring-your-own; never provisioned by `infra/main.bicep` (see `infra/README.md`). | Burstable B1ms is the cheapest tier that fits this workload; move to General Purpose once you have real concurrent load. |
| **Azure Key Vault** | Holds `DATABASE_URL`, `GIT_TOKEN_FERNET_KEY`, `GIT_TOKEN`, the registry password — never a literal secret in Bicep/manifests/env files. Referenced as `existing`; provisioning and populating it is a one-time manual step per environment. | Standard tier is enough; enable soft-delete + purge protection. |
| **Log Analytics workspace** | Container/App/pod logs and the metrics every compute target below wires up. | `PerGB2018`, 30-day retention is what `infra/main.bicep` uses. |
| **Application Insights** (not yet wired into `infra/main.bicep` — add if you need it) | Request tracing, dependency maps, exceptions from the FastAPI backend. Cheap add-on to the Log Analytics workspace above (`workspace-based` mode). | Optional but recommended before your first production incident. |
| **Managed identity** (system- or user-assigned, per compute target below) | Every app authenticates to Key Vault (and ACR, if used) via identity — never a stored credential where the platform supports it. | Free; already used by `infra/main.bicep` (`SystemAssigned` on the backend/worker Container Apps). |

**Add per compute target:**

| Compute target | Extra services |
|---|---|
| Container Apps (default) | Container Apps managed environment (created by `infra/main.bicep`) — nothing else. |
| App Service for Containers | An App Service Plan (Linux) sized for 3 web apps' worth of load. |
| Azure Functions | A Function App + its required Storage account (Functions' own runtime state, not application data) + a Premium (or App Service) plan if you need VNET integration to reach Postgres privately — see §7 for why Consumption is a poor fit here. |
| AKS | The AKS cluster itself, **Azure Container Registry** (§1), an ingress controller (`ingress-nginx` via Helm, or Application Gateway Ingress Controller if you want an Azure-managed one), and — if you use Key Vault via the CSI driver — the AKS "Azure Key Vault Secrets Provider" add-on. |

**Optional, regardless of compute target:** Azure Front Door or a CDN in front of the frontend if
you serve users across regions; Azure Monitor alert rules + a budget/cost alert (this stack is
meant to run cheaply — a budget alert catches a misconfigured `minReplicas`/node pool before it
doesn't).

## 3. Deploying via GitHub Actions

This is what's already implemented and is the path of least resistance.

- **`.github/workflows/ci.yml`** — runs on every push/PR: image builds, backend/worker/frontend
  tests, the generator-parity check, a migration-drift check, and dependency-lock checks. Nothing
  here talks to Azure.
- **`.github/workflows/deploy.yml`** — runs on push to `main` or manual dispatch. Builds and pushes
  the three images to `ghcr.io`, then runs `az deployment group what-if` followed by the real
  `az deployment group create` against `infra/main.bicep`, authenticating to Azure via **OIDC**
  (`azure/login`) — no stored client secret to rotate or leak.

**One-time setup per environment** (`dev`/`qa`/`prod`), before the workflow can deploy:

1. Create a GitHub *Environment* (repo Settings → Environments) named `dev`/`qa`/`prod`, and add
   qa/prod approval gates there if you want a human in the loop before those environments deploy.
2. Add environment **variables**: `AZURE_RESOURCE_GROUP`, `AZURE_KEY_VAULT_NAME`,
   `AZURE_CONTAINER_REGISTRY_USERNAME`.
3. Add environment **secrets**: `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID` — these
   identify an Azure AD App Registration (or user-assigned managed identity) with a **federated
   credential** trusting this GitHub repo/environment, so no client secret is stored at all:

   ```bash
   az ad app federated-credential create --id <app-object-id> --parameters '{
     "name": "github-deploy-dev",
     "issuer": "https://token.actions.githubusercontent.com",
     "subject": "repo:<org>/<repo>:environment:dev",
     "audiences": ["api://AzureADTokenExchange"]
   }'
   ```

   Repeat per environment (the `subject` changes to `environment:qa` / `environment:prod`), and
   grant that app/identity `Contributor` on the target resource group plus `Key Vault Secrets
   Officer`/appropriate role if it needs to read secrets directly (it doesn't for the Bicep deploy —
   the *apps* read Key Vault via their own managed identity, granted inside `main.bicep`).
4. Populate the Key Vault's secrets (see `infra/README.md`'s table) — a one-time, out-of-band step,
   never done by the workflow.

Nothing else changes — push to `main` (or run the workflow manually) and it builds, pushes, and
deploys.

## 4. Deploying via GitLab CI/CD

A `.gitlab-ci.yml` mirroring the same build → test → build-and-push → deploy shape as the GitHub
workflows is included at the repo root. It targets the **same `infra/main.bicep`** (Container
Apps) — switch to §6/§7/§8 below if you want a different compute target — and pushes images to the
**GitLab Container Registry** (`$CI_REGISTRY_IMAGE`) instead of `ghcr.io`. Swap in ACR by changing
the `docker login`/tag steps if you'd rather keep one registry across both CI systems.

**One-time setup:**

1. In your Azure AD tenant, create an App Registration (or reuse the one from §3) and add a
   **federated identity credential** trusting your GitLab project's OIDC issuer:

   ```bash
   az ad app federated-credential create --id <app-object-id> --parameters '{
     "name": "gitlab-deploy-dev",
     "issuer": "https://gitlab.com",
     "subject": "project_path:<group>/<project>:ref_type:branch:ref:main",
     "audiences": ["api://AzureADTokenExchange"]
   }'
   ```

   (Self-managed GitLab: use your instance's URL as `issuer`. Adjust `subject` for protected
   environments/tags if you deploy from something other than a `main` branch push — GitLab's OIDC
   docs list the exact claim shapes for environment-scoped and merge-request pipelines.)
2. In GitLab, set CI/CD variables (Settings → CI/CD → Variables) per environment: `AZURE_CLIENT_ID`,
   `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`, `AZURE_RESOURCE_GROUP`, `AZURE_KEY_VAULT_NAME`. Mark
   them "Protected" so only protected branches/environments can read them.
3. Populate the Key Vault's secrets, same as §3 step 4.

The pipeline authenticates with `az login --service-principal --federated-token` using the
short-lived OIDC ID token GitLab issues per job (the `id_tokens:` block in `.gitlab-ci.yml`) — no
client secret stored in GitLab either.

This has not been run against a real GitLab pipeline or Azure subscription; validate the `what-if`
stage's output before trusting the `deploy` stage.

## 5. Compute target: Azure Container Apps (default)

Already implemented — `infra/main.bicep` + `.github/workflows/deploy.yml`. See `infra/README.md`
for the full parameter/secret reference. No changes needed to use this path from either CI system;
§3/§4 above are the only setup required.

Key shape worth knowing if you're comparing it to the alternatives below: **frontend and backend
scale to zero** (`minReplicas: 0`) since both are pure request-driven HTTP apps; the **worker never
scales to zero** (`minReplicas: 1`) because it's a poll loop, not something Container Apps' HTTP
traffic-based autoscaler can wake up on demand.

## 6. Compute target: Azure App Service for Containers

A simpler PaaS alternative if you'd rather not run the (still fairly new) Container Apps product,
at the cost of losing scale-to-zero.

**What changes vs. Container Apps:**

- **One App Service Plan (Linux), three Web Apps for Containers** — one per image (`api`, `worker`,
  `web`), each pointed at its own image tag via the Web App's container settings
  (`az webapp config container set --docker-custom-image-name <registry>/<image>:<tag>`). There is
  no Bicep template for this in `infra/` yet; the shape is a straight port of `main.bicep`'s
  `containerApps` resources to `Microsoft.Web/sites` (`kind: 'app,linux,container'`) sharing one
  `Microsoft.Web/serverfarms` plan.
- **No scale-to-zero.** App Service bills for the plan whether or not it's serving traffic; pick the
  smallest Linux plan (B1) that fits three containers' worth of CPU/memory, or put `worker` on its
  own smaller plan since it never needs a public endpoint.
- **The worker needs "Always On" enabled** on its Web App — without it, App Service can idle-shut-down
  an app with no inbound HTTP traffic, which would kill the poll loop. A Web App with `alwaysOn: true`
  and a container that never exits is enough (no need for the WebJobs SDK — the container's own
  `CMD ["python", "-m", "app.main"]` just keeps running).
- **Secrets via Key Vault references in App Settings**, not the Container Apps `secrets:` block:
  `@Microsoft.KeyVault(SecretUri=https://<vault>.vault.azure.net/secrets/database-url/)` as the value
  of the `DATABASE_URL` app setting, with the Web App's system-assigned identity granted "Key Vault
  Secrets User" on the vault (same role `main.bicep` grants Container Apps today).
- **Frontend → backend wiring**: App Service doesn't have Container Apps' internal DNS between
  sibling apps, so `BACKEND_INTERNAL_URL` must be the backend Web App's public (or VNET-integrated
  private) hostname, e.g. `https://<platform>-api.azurewebsites.net`, instead of the
  Container-Apps-environment-local name `main.bicep` uses today.
- **Registry auth**: same idea as Container Apps — `DOCKER_REGISTRY_SERVER_URL` /
  `_USERNAME`/`_PASSWORD` app settings (or a managed identity with `AcrPull` if you're on ACR,
  which avoids a stored password entirely).

Everything else (Dockerfiles, `docker-entrypoint.sh` running `alembic upgrade head` on start, env
var contract in `config/platform.env.example`) is unchanged — the app itself has no idea which
compute product is hosting its container.

## 7. Compute target: Azure Functions

The biggest-change option, because this app's backend is a long-running ASGI server and its worker
is a true infinite poll loop — neither is what Functions is built for. Only worth it if you have a
specific reason to standardize on Functions; Container Apps already gives you scale-to-zero without
this rework.

**Backend (FastAPI → Functions):** FastAPI is ASGI, and Azure Functions' Python worker supports ASGI
apps via `azure.functions.AsgiMiddleware`. Concretely:

- Add `azure-functions` to `backend/requirements.in`/`.txt`.
- Add a `function_app.py` at the backend root:

  ```python
  import azure.functions as func
  from app.main import app as fastapi_app

  app = func.AsgiFunctionApp(app=fastapi_app, http_auth_level=func.AuthLevel.ANONYMOUS)
  ```

- Add `host.json` (Functions runtime config) and switch the Dockerfile's `CMD` for a Functions
  custom-container image (`mcr.microsoft.com/azure-functions/python:4-python3.12` as the base
  instead of plain `python:3.12-slim`, or use `func azure functionapp publish` for a non-container
  deploy).
- **Use a Premium plan, not Consumption.** Consumption's cold starts (a fresh worker process per
  scale-out, sometimes per idle period) fight directly with this app's SQLAlchemy connection pool
  (`DB_POOL_SIZE`/`DB_MAX_OVERFLOW` in `config/platform.env.example`) and with `lifespan`'s
  `Base.metadata.create_all` / seed check running on every cold start. Premium also gets you VNET
  integration, which you need anyway to reach a Postgres Flexible Server that isn't publicly
  exposed.

**Worker (poll loop → Functions) — the part that actually changes behavior, not just packaging:**
`worker/app/jobs/loop.py`'s `run_forever()` is an infinite loop with no natural request/timer
boundary; Functions has no "long-running background process" trigger. The realistic port is a
**Timer Trigger** that runs the *body* of one poll iteration (claim-and-process whatever jobs are
currently queued, then return) on a schedule:

```python
@app.timer_trigger(schedule="*/30 * * * * *", arg_name="timer")
def poll_jobs(timer: func.TimerRequest) -> None:
    from app.jobs.runner import process_available_jobs  # one batch, not run_forever()
    process_available_jobs()
```

Trade-offs to weigh before doing this: jobs now wait up to the timer interval instead of being
picked up the moment they're enqueued (30s is a reasonable floor); a run that takes longer than the
interval can overlap with the next firing unless you add your own lock/guard (Postgres's `FOR UPDATE
SKIP LOCKED`, already used by the real poll loop, actually helps here — two overlapping invocations
just can't claim the same row); and you lose the one long-lived process's connector-registry
startup banner/log, which becomes a per-invocation cost instead.

**Secrets/identity**: same Key Vault-reference-in-App-Settings pattern as §6, with the Function
App's system-assigned identity granted "Key Vault Secrets User".

**Recommendation**: if you want Functions for the backend's HTTP surface, that part is a
straightforward wrap. Keep the worker on Container Apps (`minReplicas: 1`, as today) rather than
force it into a timer-trigger shape unless you have a concrete reason — it's the one piece of this
stack that Functions' execution model doesn't fit naturally.

## 8. Compute target: Azure Kubernetes Service (AKS)

Worth it once you need more control than Container Apps gives you (custom networking, multiple
node pools, a service mesh, workloads beyond this app sharing the cluster) — otherwise it's more to
operate for the same three services.

**What changes vs. Container Apps:**

- **Kubernetes manifests (or a Helm chart) replace `main.bicep`'s `containerApps` resources** — one
  `Deployment` + `Service` per app (`api`, `worker`, `web`), plus an `Ingress` for `api`/`web`
  (`worker` needs no `Service`/`Ingress`, same as it needs no `ingress:` block in Container Apps
  today). Shape, per app:

  ```yaml
  apiVersion: apps/v1
  kind: Deployment
  metadata:
    name: api
  spec:
    replicas: 2
    selector: { matchLabels: { app: api } }
    template:
      metadata: { labels: { app: api } }
      spec:
        containers:
          - name: api
            image: <acr-name>.azurecr.io/adf-accelerator-backend:<tag>
            ports: [{ containerPort: 8000 }]
            envFrom:
              - secretRef: { name: adf-accelerator-secrets }
            env:
              - { name: APP_ENV, value: "prod" }
  ---
  apiVersion: v1
  kind: Service
  metadata: { name: api }
  spec:
    selector: { app: api }
    ports: [{ port: 80, targetPort: 8000 }]
  ```

  `worker`'s `Deployment` keeps `replicas: 1` for the same reason `main.bicep` pins
  `minReplicas: 1` — it's a poll loop, and running >1 replica means jobs get double-claimed unless
  you're relying on the `FOR UPDATE SKIP LOCKED` claim (which is safe, so `replicas: 2+` is fine
  too if you want the redundancy — unlike Container Apps, AKS has no scale-to-zero pressure pushing
  you toward exactly 1).
- **No built-in scale-to-zero.** `api`/`web` get a `HorizontalPodAutoscaler` (CPU or request-rate
  based) instead of Container Apps' `minReplicas: 0`; the cluster's node pool itself keeps running
  regardless, so factor that into cost vs. Container Apps' Consumption plan.
- **Registry access**: `az aks update -n <cluster> -g <rg> --attach-acr <acr-name>` grants the
  cluster's kubelet identity `AcrPull` — no registry secret in the manifests at all. This is the
  main reason §1 recommends ACR once you're on AKS.
- **Secrets from Key Vault**: install the "Azure Key Vault Secrets Provider" AKS add-on
  (`az aks enable-addons -a azure-keyvault-secrets-provider`) and use **workload identity**
  (`az aks update --enable-oidc-issuer --enable-workload-identity`) — a `ServiceAccount` per app
  federated to an Azure AD identity, exactly the same trust-relationship idea as the GitHub/GitLab
  OIDC setup in §3/§4, just federating a Kubernetes service account instead of a CI pipeline. A
  `SecretProviderClass` per app mounts the Key Vault secrets as files or syncs them to a native k8s
  `Secret` that `envFrom.secretRef` above reads.
- **Ingress + TLS**: `ingress-nginx` (via Helm) or the Application Gateway Ingress Controller
  (AGIC) fronting `api`/`web`, with `cert-manager` for automatic Let's Encrypt certificates if you
  don't already terminate TLS at Front Door.
- **`BACKEND_INTERNAL_URL`** becomes the in-cluster DNS name Kubernetes gives the `api` Service —
  `http://api.<namespace>.svc.cluster.local` — playing the same role
  `main.bicep`'s Container-Apps-environment-local name does today.
- **Cluster sizing to start**: a 2–3 node `Standard_B2s`/`Standard_D2s_v5` pool comfortably fits
  this stack's three low-traffic services; enable the cluster autoscaler (`--enable-cluster-autoscaler
  --min-count 2 --max-count 4`) rather than hand-sizing for peak.

No Bicep for AKS specifically is checked into `infra/` — if you want to stay Azure-native
(managed identity + Key Vault CSI provider, as described above), the manifests/Helm chart shape
above is what to build against. If you don't need Azure-specific integration, prefer §9 below
instead: a real, cloud-agnostic Helm chart already checked in that runs unmodified on AKS too.

## 9. Compute target: any other Kubernetes — the Helm chart (cloud-agnostic)

Everything in §8 is Azure-flavored (Key Vault CSI, workload identity federated to Azure AD). For
**EKS, GKE, OpenShift, on-prem (kubeadm/k3s), or a client who hasn't decided on a cloud yet**, use
[`infra/helm/adf-accelerator/`](../infra/helm/adf-accelerator/) instead — a checked-in Helm chart
with zero cloud-specific assumptions in its templates:

- **Secrets are bring-your-own via a plain Kubernetes `Secret`** (`secrets.existingSecret`), not
  Azure Key Vault specifically — populate it however this client already manages secrets
  (External Secrets Operator, Vault Agent Injector/CSI, a cloud Secrets Store CSI driver synced to
  a k8s Secret, Sealed Secrets, or a plain `kubectl create secret generic`). The chart never knows
  or cares which.
- **Cloud workload identity is opt-in and generic** — `serviceAccount.annotations` passes straight
  through to the ServiceAccount, so AWS IRSA, GKE Workload Identity, and Azure AD Workload Identity
  all work with the same chart, no template branching per cloud.
- **Ingress works with any controller** (`ingress-nginx`, ALB, GKE-native, on-prem) via
  `ingress.className`/`ingress.annotations` — same same-origin-proxy design as §5's Container Apps
  path (frontend `middleware.ts` proxies `/api/*` to the backend Service in-cluster, so there's no
  separate backend ingress rule to configure).

```bash
helm upgrade --install adf-accelerator ./infra/helm/adf-accelerator \
  --namespace adf-accelerator --create-namespace \
  -f my-client-values.yaml
```

See the chart's own `README.md` for the full secrets/ingress/identity walkthrough and the exact
`helm lint`/`helm template --dry-run` validation steps to run before a real rollout — this chart
carries the same "not yet run against a real cluster" caveat every other unverified path in this
guide does.

## 10. Generating build artifacts locally

"Build artifacts" here means the three Docker images (and the frontend's compiled output) — not
the product's own generated ADF pipeline artifacts (that's a running-app feature; see
`docs/USER_GUIDE.md`'s Build/Generate walkthrough instead).

**Build each image locally**, same context Docker Compose and CI both use:

```bash
docker build -t adf-accelerator-backend:local  ./backend
docker build -t adf-accelerator-worker:local   ./worker
docker build --target production -t adf-accelerator-frontend:local ./frontend
```

(`--target production` matters for the frontend — its Dockerfile's default target is `dev`, wired
for docker-compose's bind-mount hot reload; `production` is the real standalone build CI/CD ships.)

**Tag the same way `deploy.yml` does**, so a locally-built image is directly comparable to what a
pipeline would produce: it tags with the first 12 characters of the commit SHA
(`${GITHUB_SHA::12}`) — reproduce that locally with:

```bash
git rev-parse --short=12 HEAD
```

**Run the stack against locally-built images** (instead of Compose rebuilding them itself) by
overriding the `image:` in a `docker-compose.override.yml` you keep untracked:

```yaml
services:
  api:      { image: adf-accelerator-backend:local }
  worker:   { image: adf-accelerator-worker:local }
  frontend: { image: adf-accelerator-frontend:local }
```

then `docker compose up` (no `--build`) picks up the pre-built images.

**Frontend-only build artifact, no Docker needed**: `cd frontend && npm run build` produces
`.next/standalone` + `.next/static` directly — the same output the `production` Docker stage
copies out, useful for inspecting bundle size or testing the standalone server
(`node .next/standalone/server.js`) without a container at all.

**Build in the cloud instead of locally** (no Docker daemon required, e.g. from a machine without
Docker Desktop) with ACR Tasks:

```bash
az acr build --registry <acr-name> --image adf-accelerator-backend:local ./backend
```

**Inspect or transfer a built image** without a registry: `docker image inspect
adf-accelerator-backend:local` to check layers/size, or `docker save adf-accelerator-backend:local
-o backend.tar` / `docker load -i backend.tar` to move an image between machines directly.

None of the above pushes anywhere — pushing to a registry (whether `ghcr.io`, ACR, or the GitLab
registry) is `docker push`, done here only by CI/CD (§3/§4), never as part of routine local
iteration.

## 11. Running the full stack locally without Docker (native Postgres)

For testing the app's own artifact-generation feature (Build → Generate) end to end without Docker
Desktop at all — e.g. Postgres installed natively on your machine instead of the `postgres`
Compose service. ADF and a real Git provider are **not** needed for this: the generator never
calls ADF, and leaving `GIT_TOKEN` blank makes the worker fall back to an offline
`SimulatedAdapter` so Push "succeeds" without a real repo.

**1. Install Postgres locally** (any version compatible with the `postgres:16-alpine` image
Compose uses) and create the app's database/user:

```sql
CREATE USER accelerator WITH PASSWORD 'change-me-local-only';
CREATE DATABASE accelerator OWNER accelerator;
```

**2. Point `config/platform.env` at it.** `cp config/platform.env.example config/platform.env` if
you haven't already, then change `DATABASE_URL`'s host from the Compose-network hostname
`postgres` to `localhost`:

```
DATABASE_URL=postgresql+psycopg://accelerator:change-me-local-only@localhost:5432/accelerator
```

Also set `BACKEND_INTERNAL_URL=http://localhost:8000` so the frontend's `/api` proxy finds the bare
backend, and leave `GIT_TOKEN` blank. Keep `AUTH_MODE=password` (then bootstrap an admin with `python -m
app.manage create-platform-admin ...` from `backend/`) or switch to `AUTH_MODE=mock` for no login.
`backend/app/config.py`, `worker/app/config.py` and `frontend/next.config.ts` all load
`config/platform.env` by path from the repo root regardless of how they're started, so no extra env
wiring is required to run them bare. Don't run `docker compose up` against this same
file at the same time — the `postgres` hostname only resolves inside the Compose network, so you'd
need to flip it back first.

**3. Run migrations + the backend** (from `backend/`):

```bash
pip install -r requirements-dev.txt
alembic upgrade head
uvicorn app.main:app --reload
```

**4. Run the worker** (from `worker/`, separate terminal) — this is the poll loop that claims `Job`
rows and actually performs Generate/Validate/Push:

```bash
pip install -r requirements-dev.txt
python -m app.main
```

**5. Run the frontend** (from `frontend/`, separate terminal):

```bash
npm install
npm run dev
```

**6. Exercise it**: http://localhost:3000 → create a team/project → Build wizard → fill the form →
Generate. The worker picks up the job and generated artifacts land under Drafts/Artifacts. You can
stop there and skip Validate/Push until you're ready to test Git integration too.
