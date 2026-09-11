# tenant-operator

Helm chart for deploying **tenant-operator** — a FastAPI service that is the
single entry point for onboarding tenants onto a hub-and-spoke Kubernetes
fleet, entirely through GitOps. UI/API/automation talk only to this service;
it is the only thing that writes to the GitOps repo, and it never calls
`kubectl apply` directly against a spoke — Argo CD does the actual deploying,
reacting to files this operator commits.

```
POST /api/v1/tenant { tenantId, displayName, email, password, domain }
        │
        ▼
Validate → select spoke (sequential fill; capacity check/scale-out per env)
        │
Create Tenant CR on hub (intent record, continuously updated as status changes)
        │
Write per-tenant secrets to Vault → render values.yaml → commit to Git
        │                                             │
        ▼                                             ▼
Poll Argo CD + K8s for health               ApplicationSet detects the file,
        │                                    creates/syncs the Application
        ▼                                    automatically
Tenant RUNNING → trigger meta-builder Job (Mongo/Postgres seeding)
```

Image: `sandeepkosework/tenant-operator` (Docker Hub). This repo is the
chart only — the application's own source lives in a separate repository.

## Install

```bash
helm install tenant-operator . \
  --namespace tenant-operator --create-namespace \
  -f my-values.yaml
```

One release serves exactly one environment (`stage` or `prod`) and exactly
one hub-and-spoke fleet — run a second, separate release (its own
namespace, its own config/secrets, its own `clustersConfig`) for the other
environment. They must never share a spoke cluster or a Vault path prefix.

## Configuration

Almost every setting is a plain environment variable on the application
(`Settings` in its own `app/config.py`) — the chart just decides whether a
given key ends up in a ConfigMap (`config:`) or a Secret (`secret:`):

| Values key | Purpose |
|---|---|
| `image.repository` / `image.tag` | Defaults to the public `sandeepkosework/tenant-operator` image |
| `environment` | `stage` or `prod` |
| `config` | Any non-secret `Settings` field (`GIT_REPO_URL`, `ARGOCD_SERVER`, `VAULT_ADDR`, `DATABASE_URL`, `VAULT_TENANT_SECRET_PREFIX`, ...), `UPPER_SNAKE` → ConfigMap → `envFrom` |
| `secret` / `existingSecretName` | Secret-shaped config (Git/GHCR/Vault/Argo CD tokens) → Secret → `envFrom`. Prefer `existingSecretName` for anything real — inline `secret:` values end up in this release's Helm history in plaintext |
| `vault.enabled` / `vault.role` | Vault Agent Injector sidecar for the operator's **own** config bootstrap (`secret/tenant-operator/config`) — separate from its runtime Vault writes for tenant secrets, which always go through `VAULT_TOKEN`/`VAULT_TOKEN_FILE` in `config`/`secret` regardless of this setting |
| `git.sshKey.existingSecretName` | SSH deploy key, if using `git@...` instead of an HTTPS PAT (`secret.GIT_HTTPS_TOKEN`) |
| `kubeconfig.existingSecretName` | Only needed when the tenant workload cluster(s) aren't the cluster the operator itself runs on. Mounted at `/secrets/kube` — also set `config.KUBECONFIG_PATH` to the actual file path inside that Secret |
| `clustersConfig.inline` / `.existingConfigMapName` | Overrides the `clusters.yaml` baked into the image, so one image serves multiple environments without a rebuild |
| `persistence.*` | Backs `GIT_LOCAL_PATH` — the operator's local clone of the GitOps repo |
| `sqlitePersistence.*` | Backs `DATABASE_URL` when using `sqlite:////data/db/...` (set that in `config` too) — not needed with a real external Postgres |
| `rbac.create` | Baseline cluster-scoped `ClusterRole` (see "RBAC" below) |
| `rbac.crossplane.*` | Prod-only, off by default: namespaced `Role` for applying Crossplane OKE claims |

## API

```
POST   /api/v1/tenant            -> 202 Accepted, { tenantId, status: PENDING }
GET    /api/v1/tenant/{id}       -> current tenant record incl. status + progress
GET    /api/v1/tenant            -> list, filterable by ?environment=&status_filter=
PUT    /api/v1/tenant/{id}       -> update version/users/db size (only when RUNNING/FAILED)
DELETE /api/v1/tenant/{id}       -> 202 Accepted, tears down via git delete + Argo CD prune
GET    /api/v1/tenant/{id}/vault -> this tenant's current Vault secrets
GET    /api/v1/cluster           -> SpokeCluster status for every registered spoke
GET    /api/v1/cluster/{name}    -> SpokeCluster status for one spoke
GET    /api/v1/vault/common                          -> shared config across every tenant
PUT    /api/v1/vault/common                          -> overwrite it
GET    /api/v1/vault/qraie-bridge-defaults            -> shared config for every qraie-bridge service
PUT    /api/v1/vault/qraie-bridge-defaults/{service}  -> overwrite one qraie-bridge service's shared config
GET    /health                   -> liveness/readiness (checked by the chart's probes)
/socket.io                       -> live status push, as an alternative to polling GET /api/v1/tenant/{id}
```

Poll (or subscribe via Socket.IO to) `GET /api/v1/tenant/{id}` for status
transitions: `PENDING → VALIDATING → GIT_COMMITTED → SYNCING → RUNNING` (or
`FAILED`). Tenants are not monitored after reaching `RUNNING`.

`appType` on the create request selects which chart/Vault schema a tenant
uses. `"workplace"` (default) is a simple single-image chart using a generic
Vault shape (`redis`/`database`/`mongo`/`jwt`). `"qraie-bridge"` targets the
[`helm-chart-bridge`](https://github.com/sandeepkosework/helm-chart-bridge)
multi-service chart and has its own, much wider per-service Vault schema —
see "Vault integration" below. For `appType="qraie-bridge"`, the request's
`services` map and `onlyListedServices` flag control which of that chart's
~30 services are disabled for this tenant.

## Vault integration

Two kinds of variable, kept deliberately separate:

- **Common / platform defaults** — shared infra config that's the same for
  every tenant in this environment (hosts, ports, external API
  credentials, business config). Set once via `PUT /api/v1/vault/common`
  (generic schema) or `PUT /api/v1/vault/qraie-bridge-defaults/{service}`
  (per-service, qraie-bridge schema) — copied into each new tenant's
  secrets at creation time, not kept in sync with later changes to
  already-provisioned tenants.
- **Tenant-specific** — generated fresh per tenant at onboarding: database
  usernames/passwords, JWT secrets, Redis passwords, and identity fields
  (`TENANT_ID` and friends) derived from the tenant's own slug. These can
  never come from the shared config, since they must be unique per tenant —
  a shared value here would mean every tenant's seeding job or Redis
  instance collides on the same login.

For `appType="qraie-bridge"` specifically, secrets land at
`secret/tenants/<tenant-slug>/<service>` (one path per chart service) and
`secret/tenants/<tenant-slug>/common` (shared across that tenant's own
services only) — consumed by `helm-chart-bridge`'s 3-layer `envFrom`, not
Vault Agent injection. A fourth path, `secret/k8s/tenant-common`, is
genuinely global across every tenant and every environment; it's managed
manually, not written by this operator, and read live by the chart rather
than copied per tenant.

Inspect what's currently stored for one tenant via
`GET /api/v1/tenant/{id}/vault`.

## RBAC

Two identities, deliberately scoped as narrowly as each job allows:

- **Cluster-scoped, mostly read-only** (`rbac.create`) — `get`/`list`/`watch`
  on namespaces/pods/PVCs/deployments/ingresses across every spoke, plus
  exactly one write verb: `delete` on namespaces. That one exception exists
  because Argo CD's `CreateNamespace=true` sync option creates a tenant's
  namespace but never tracks or prunes it — this operator deletes it
  directly on tenant teardown. Every other write happens by committing to
  git and letting Argo CD apply it, never via a direct API write from this
  service.
- **Namespaced, on the operator's own namespace** — `create`/`get`/`list`/
  `watch` on `batch/v1` Jobs only, for submitting and polling the
  meta-builder DB-seeding Job it creates per tenant. Never broadened beyond
  that resource type.
- **Crossplane** (`rbac.crossplane.enabled`, prod-only, off by default) — a
  namespaced `Role` scoped to exactly one Crossplane claim CRD, for
  requesting new spoke clusters once a spoke crosses its capacity
  threshold.

## Persistence

Two independent PVCs, both `ReadWriteOnce`:

- `persistence` — the operator's local clone of the GitOps repo
  (`GIT_LOCAL_PATH`). Needs to survive pod restarts; a fresh clone on every
  restart works too, just slower on the first request after a restart.
- `sqlitePersistence` — the operator's own bookkeeping database, when
  `DATABASE_URL` points at `sqlite:////data/db/...` rather than an external
  Postgres.

## Operational notes

- **Single replica by design.** The application's git write lock and its
  Socket.IO status bus are both in-process only — safe for exactly one
  replica. Don't raise `replicaCount` without first moving the git critical
  section to a Postgres advisory lock and the status bus to Redis pub/sub
  (both are small, contained changes in the application, not this chart).
- **`strategy: Recreate`, not `RollingUpdate`**, deliberately — this chart's
  PVCs are `ReadWriteOnce`, and a surge pod briefly coexisting with the old
  one during a rolling update can deadlock on volume attachment, especially
  on a single-worker-node cluster. Expect a short window of downtime on
  every upgrade, not a rolling one.
- **`clustersConfig` lets one image serve multiple environments.** The
  `clusters.yaml` baked into the image at build time is only a default —
  mount a real per-environment registry (spoke names, Argo CD cluster
  server URLs, per-environment capacity limits) via
  `clustersConfig.existingConfigMapName` instead of rebuilding the image per
  environment.
- **Stage and prod must never share a spoke or a clusters registry.** Each
  release's `clustersConfig` should list only that one environment's spokes.
