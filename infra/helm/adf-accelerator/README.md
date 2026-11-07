# adf-accelerator Helm chart

Cloud-agnostic Kubernetes packaging for the ADF Forge application
(frontend/backend/worker). Where `infra/main.bicep` is the Azure Container
Apps-specific path, this chart is the "deploy to whatever Kubernetes this
client already has" path — EKS, GKE, AKS, OpenShift, on-prem (kubeadm/k3s),
or a local kind/minikube cluster for evaluation. No template in here branches
on a specific cloud; every place a real environment differs is a value.

Same product boundary as `infra/main.bicep` (see `infra/README.md`): this
chart deploys the **application only**. It never provisions PostgreSQL or a
secret store — bring your own and point the chart at them.

## Before you install

You need, reachable from the cluster:

1. **A PostgreSQL database** (any 13+ instance — Azure Database for
   PostgreSQL, RDS, Cloud SQL, a self-hosted instance, whatever this client
   already runs) and its connection string as `DATABASE_URL`
   (`postgresql+psycopg://user:pass@host:5432/dbname`).
2. **A Fernet key** for `GIT_TOKEN_FERNET_KEY` (encrypts a per-push Git token
   at rest between the backend and worker — must be identical for both):
   ```bash
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```
3. **Container images** for `backend`, `worker`, `frontend`, built from this
   repo (see `docs/DEPLOYMENT_GUIDE.md` §9) and pushed to a registry this
   cluster can pull from.

## Bringing your own secrets (the part that makes this "any client environment")

The chart never creates or manages a secret store itself — it only ever
reads a Kubernetes `Secret` by name and key. How that `Secret` gets into the
cluster is entirely up to whatever this client already uses:

| Client already has... | How to get the Secret in |
|---|---|
| Nothing fancy | `kubectl create secret generic adf-secrets --from-literal=DATABASE_URL=... --from-literal=GIT_TOKEN_FERNET_KEY=... --from-literal=GIT_TOKEN=...` |
| External Secrets Operator | An `ExternalSecret` resource syncing from their existing Vault/AWS Secrets Manager/GCP Secret Manager/Azure Key Vault into a `Secret` named e.g. `adf-secrets` |
| HashiCorp Vault | Vault Agent Injector (sidecar template) or the Vault CSI provider, either way landing a `Secret` (or Secret-shaped volume — set `secrets.existingSecret` to the CSI-synced Secret if you use the "Sync as Secret" mode) |
| Azure Key Vault, AWS Secrets Manager, GCP Secret Manager (CSI) | The Secrets Store CSI driver's "Sync as Kubernetes Secret" feature |
| Sealed Secrets | `kubeseal`-encrypted manifest committed to Git, decrypted in-cluster |

Whichever you use, set:

```yaml
secrets:
  create: false
  existingSecret: adf-secrets   # the Secret name you created above
```

For a **quick local/eval install only** (never production — the values then
sit in plaintext in your values file and Helm's release history), you can
instead let the chart create the Secret itself:

```yaml
secrets:
  create: true
  databaseUrl: "postgresql+psycopg://..."
  gitTokenFernetKey: "..."
  gitToken: ""
```

## Cloud identity (optional, additive)

If this client's cluster supports workload identity federation, annotate the
chart's ServiceAccount instead of managing a static credential at all — the
chart passes `serviceAccount.annotations` straight through, no cloud-specific
template logic:

```yaml
# AWS (IRSA)
serviceAccount:
  annotations:
    eks.amazonaws.com/role-arn: arn:aws:iam::123456789012:role/adf-accelerator

# GCP (Workload Identity)
serviceAccount:
  annotations:
    iam.gke.io/gcp-service-account: adf-accelerator@my-project.iam.gserviceaccount.com

# Azure (Workload Identity, the AKS-native alternative to infra/main.bicep's
# managed-identity + Key Vault reference wiring)
serviceAccount:
  annotations:
    azure.workload.identity/client-id: <uuid>
```

Leave `serviceAccount.annotations` empty if you're using the plain
`existingSecret` route above — no cloud identity binding is needed for that
path at all.

## Ingress

Fronts the frontend Service only. `frontend/middleware.ts` proxies
`/api/:path*` to the backend Service in-cluster (same same-origin-proxy
design used against Azure Container Apps in `infra/main.bicep`), so there's
no separate backend path to route. Set `ingress.className` and
`ingress.annotations` for whichever controller this cluster runs:

```yaml
# ingress-nginx (most common on-prem / any cloud)
ingress:
  className: nginx
  host: adf-accelerator.example.com
  tls:
    - secretName: adf-accelerator-tls
      hosts: ["adf-accelerator.example.com"]

# AWS ALB Ingress Controller (EKS)
ingress:
  className: alb
  annotations:
    alb.ingress.kubernetes.io/scheme: internal
    alb.ingress.kubernetes.io/target-type: ip

# GKE (native GCE ingress)
ingress:
  className: gce
```

No ingress controller yet? Set `ingress.enabled: false` and
`kubectl port-forward` to reach the frontend/backend Services directly (see
`helm install`'s `NOTES.txt` output for the exact commands).

## Installing

```bash
helm upgrade --install adf-accelerator ./infra/helm/adf-accelerator \
  --namespace adf-accelerator --create-namespace \
  --set image.backend.repository=ghcr.io/yourorg/adf-accelerator-backend \
  --set image.worker.repository=ghcr.io/yourorg/adf-accelerator-worker \
  --set image.frontend.repository=ghcr.io/yourorg/adf-accelerator-frontend \
  --set image.tag=<git-sha-or-release-tag> \
  --set secrets.existingSecret=adf-secrets \
  --set ingress.host=adf-accelerator.example.com \
  -f my-client-values.yaml
```

Keep client-specific overrides (registry, ingress host/class, resource
sizing, `secrets.existingSecret` name) in a `my-client-values.yaml` you don't
commit alongside this chart, the same way `config/platform.env` is git-ignored
for local dev — this chart ships no client's specifics baked in.

**Validate before trusting a real rollout**, same caveat this repo's other
infra carries (`infra/main.bicep` has never been run against a real
subscription as shipped — see `infra/README.md`): this chart has not been
run against a real cluster as part of authoring it.

```bash
helm lint ./infra/helm/adf-accelerator
helm template adf-accelerator ./infra/helm/adf-accelerator -f my-client-values.yaml | less
helm upgrade --install --dry-run --debug adf-accelerator ./infra/helm/adf-accelerator -f my-client-values.yaml
```

## Bootstrapping the first admin

Once `config.authMode: password` and the app is up:

```bash
kubectl exec -n adf-accelerator deploy/adf-accelerator-backend -- \
  python -m app.manage create-platform-admin --email you@example.com --name "Your Name" --username you
```

It prints a one-time password unless you pass `--password`, and the admin must change it at first
sign-in (with username or email). Never via a signup UI.

## What this chart deliberately does not do

- **No Postgres.** Bring your own, same as `infra/main.bicep`.
- **No secret store.** Bring your own via `secrets.existingSecret`.
- **No SSO broker.** `config.authMode: oidc`/`saml` will 501 until §27's
  `IdentityProvider` adapter is implemented — `password` mode is the real
  option today for a client that isn't ready to wait on that.
- **No multi-cluster/multi-region topology.** One `helm install` = one
  environment (dev/qa/prod are separate releases/namespaces/clusters, your
  choice — the chart doesn't presume which).

## Values reference

See `values.yaml` — every field is commented inline. The `global`,
`secrets`, `serviceAccount`, and `ingress` blocks are the ones that change
per client; `image`, `config`, `backend`/`worker`/`frontend` resource sizing
are reasonable defaults you'll likely leave alone for a first install.
