# Infrastructure

`main.bicep` deploys the **ADF Forge application** (frontend/backend/worker) to Azure Container Apps —
Consumption-plan, scale-to-zero by default — the lowest-cost option that still fits three independent
services. It is invoked once per environment by
`.github/workflows/deploy.yml`.

This has nothing to do with, and never touches, a customer's Azure Data Factory factory or the Generated
Artifacts repo — those are reached only through this app's own Git adapter (§20/§23.7). This template is
about hosting the *application*, not deploying ADF pipelines.

## What this does NOT provision

- **PostgreSQL.** Bring your own reachable Postgres (Azure Database for PostgreSQL Flexible Server —
  Burstable B1ms is the cheapest tier that fits this workload — or otherwise) and put its connection
  string in Key Vault as `DATABASE_URL` before the first deploy.
- **The Key Vault itself.** Referenced as `existing`; provisioning it and populating its secrets is a
  one-time manual/out-of-band step per environment (§1.6/§20 — this app never writes a literal secret
  into Bicep or generated artifacts).

## Required Key Vault secrets, per environment

| Secret name (default) | Used for |
|---|---|
| `database-url` | `DATABASE_URL` for both `api` and `worker` |
| `git-token-fernet-key` | Encrypts/decrypts a per-push Git token at rest (§23.12 gap #17) — must be identical across `api` and `worker`. Generate with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `git-token` | Optional fallback Git credential the worker uses when a push carries none of its own and no per-project Key Vault reference resolves |
| `container-registry-password` | Pulls images from the registry (a GitHub PAT/token with `read:packages`, for the default `ghcr.io` registry) |

Names are parameterized (`keyVault*SecretName` params) if your Key Vault uses different naming.

## Deploying

`az deployment group create` is run by the CD workflow with per-environment parameters (backend/worker/
frontend image repository, the tag it just built, the environment's Key Vault name, and the registry
username). This template has not been deployed against a real subscription as part of authoring it —
run `az deployment group what-if` before the first real deploy in each environment and adjust SKUs/scale
limits to the actual traffic expected.

`authMode` (default `password`) sets the apps' `AUTH_MODE`. Leave it at `password` for any real
environment. `mock` disables sign-in, and `oidc`/`saml` are scaffolded only (every sign-in returns 501).
After the first deploy, bootstrap the first platform admin once with
`python -m app.manage create-platform-admin --email ... --name ... --username ...` in the api container.

Keep environment values aligned with `config/platform.env`; secret values must be Key Vault references,
never Bicep literals, matching how this app's own generated ADF artifacts treat secrets (§1.6/§5/§20).

## Deploying to any other Kubernetes (not Azure-specific)

`main.bicep` is Azure Container Apps only. For AKS, EKS, GKE, OpenShift, or an
on-prem cluster, use [`helm/adf-accelerator/`](helm/adf-accelerator/) instead — a
real, checked-in Helm chart with no cloud-specific assumptions (secrets are
bring-your-own via a plain Kubernetes `Secret`, not Azure Key Vault
specifically; cloud workload identity is opt-in via ServiceAccount
annotations, not baked in). See that chart's own `README.md` for the full
"any client environment" installation guide.

## Deploying via GitLab, or to App Service / Functions / AKS instead

This file documents `main.bicep` (Azure Container Apps) and its GitHub Actions caller
specifically. For deploying via **GitLab CI/CD** (`.gitlab-ci.yml` at the repo root), the
**container registry** decision (`ghcr.io` vs. ACR vs. GitLab's own registry), the full list of
**Azure services** this app needs to run efficiently, and the concrete changes required to target
**Azure App Service for Containers, Azure Functions, or AKS** instead of Container Apps, see
[`docs/DEPLOYMENT_GUIDE.md`](../docs/DEPLOYMENT_GUIDE.md).
