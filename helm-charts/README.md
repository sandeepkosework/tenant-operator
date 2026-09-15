# tenant-operator (Helm chart)

Helm chart for deploying **tenant-operator** — a FastAPI service that is the
single entry point for onboarding tenants onto a hub-and-spoke Kubernetes
fleet, entirely through GitOps. UI/API/automation talk only to this
service; it is the only thing that writes to the GitOps repo, and it never
calls `kubectl apply` directly against a spoke cluster — Argo CD does the
actual deploying, reacting to files this operator commits.

This document covers **the chart** — how it's structured, where the image
comes from, how it's versioned, and full deployment instructions for the
real, currently-running deployment. For what the *application* actually
does (API, provisioning workflow, Vault schema, etc.), see the repo root
`README.md` — this file deliberately stays scoped to packaging/deployment.

```
POST /api/v1/tenant { tenantId, displayName, email, password, domain }
        │
        ▼
Validate → select spoke (sequential fill; capacity check/scale-out per env)
        │
Create Tenant CR on hub (intent record, logged/echoed -- not a real CRD yet)
        │
Write per-tenant secrets to Vault (+ mirror to MongoDB) → render values.yaml → commit to Git
        │                                                            │
        ▼                                                            ▼
Poll Argo CD + K8s for health                               ApplicationSet detects the file,
        │                                                    creates/syncs the Application
        ▼                                                    automatically
Tenant RUNNING → trigger meta-builder Job (real MSSQL DB/login seeding, when configured)
```

## Where this chart lives, and how it relates to the app

This chart (`helm-charts/`) is **one Helm chart inside the `tenant-operator`
application repository** — application source (the FastAPI service) sits
at the repo root, and this directory is its own deployment chart, versioned
and released independently of the application's own image tags (see
"Versioning" below). It is easy to confuse with a second, entirely separate
chart that this *application* renders per-tenant values for — the
qraie-bridge chart (in a different repo, `helm-chart-bridge`, consumed via
GitOps) — this chart has nothing to do with tenant workloads; it only
deploys the tenant-operator control-plane service itself.

## Image

Published to Docker Hub as **[`sandeepkosework/tenant-operator`](https://hub.docker.com/r/sandeepkosework/tenant-operator)**
— public, no registry auth needed to pull. Built from the repo-root
`Dockerfile` (`python:3.12-slim`, non-root `tenantop` user, uid 1000).

The chart's own `values.yaml` defaults (`image.repository:
sandeepkosework/tenant-operator`, `image.tag: "0.1.3"`) and `Chart.yaml`
(`appVersion: "0.1.8"`) are **stale relative to what's actually pushed and
deployed** — treat them as placeholders, not as the real current version.
Check Docker Hub directly for the true latest tag:

```bash
curl -s "https://hub.docker.com/v2/repositories/sandeepkosework/tenant-operator/tags?page_size=5" \
  | python -c "import json,sys; [print(t['name'], t['tag_last_pushed']) for t in json.load(sys.stdin)['results']]"
```

As of this writing, the latest published tag is **`0.2.6`**, and that is
also what the real, live Argo CD `Application` (see below) is actually
running — not the chart's own default. Always verify rather than trust
either the chart default or a README's hardcoded number.

## Chart structure

```
helm-charts/
├── Chart.yaml              apiVersion v2, name tenant-operator, chart version + appVersion (see "Versioning")
├── values.yaml              defaults for every templated value below
├── README.md                 this file
└── templates/
    ├── _helpers.tpl          name/fullname/labels/secretName template helpers
    ├── deployment.yaml        the operator's own Deployment (strategy: Recreate -- see "Operational notes")
    ├── service.yaml            ClusterIP Service, port 80 -> containerPort 8000
    ├── serviceaccount.yaml     ServiceAccount the Deployment runs as
    ├── configmap.yaml          non-secret config (envFrom) + a second ConfigMap for clusters.yaml (file mount)
    ├── secret.yaml             secret-shaped config (envFrom) -- SKIPPED ENTIRELY when existingSecretName is set
    ├── pvc.yaml                two independent PVCs: git-cache and (optional) sqlite db-data
    ├── rbac.yaml               ClusterRole (spoke read-only + namespace delete), Role (Jobs), Role (Crossplane, optional)
    └── NOTES.txt               post-install hints (has a stale `appType` field in its example -- see below)
```

Every templated object name is `{{ include "tenant-operator.fullname" . }}`
(`<chart-name>` if the release is named exactly `tenant-operator`,
otherwise `<release-name>-tenant-operator`, truncated to 63 chars) — so a
release literally named `tenant-operator` (the one actually running — see
"Live deployment" below) produces plain, unprefixed object names:
`Deployment/tenant-operator`, `Secret/tenant-operator-secrets`,
`ConfigMap/tenant-operator-config`, etc.

**Known stale spot**: `templates/NOTES.txt`'s example `curl -X POST
.../api/v1/tenant` still includes `"appType": "qraie-bridge"` in its sample
JSON body. The application's `TenantCreateRequest` no longer has an
`appType` field at all (every tenant is qraie-bridge unconditionally now —
see the root README's "The retired `workplace` chart" section); the extra
field is silently ignored by Pydantic today rather than rejected, but the
example should be updated to drop it next time this file is touched.

## Versioning

**Two independent version numbers, easy to conflate:**

- **`Chart.yaml`'s `version`** (currently `0.1.3`) — the Helm chart's own
  package version. Bump this whenever `helm-charts/templates/*` or `values.yaml`'s
  *shape* changes (a new key, a new template, changed defaults) — this is
  what `helm package`/a chart registry tracks, independent of the
  application inside it.
- **`Chart.yaml`'s `appVersion`** (currently `"0.1.8"`) and
  **`values.yaml`'s `image.tag`** (currently `"0.1.3"`) — informational/
  default-only. **Neither is what determines the image actually running in
  the real deployment** — see "How the image tag is actually managed"
  below. Don't infer the live version from either of these fields.

Given the app image is already at `0.2.6` while the chart still defaults to
`0.1.3`/`0.1.8`, these have clearly drifted apart — normal for a chart that
was scaffolded once and has had its `values.yaml` default left alone since,
while the application inside it has shipped many image releases. This is
harmless as long as every real deployment overrides `image.tag` explicitly
(which the live one does — see below), but don't take the chart defaults as
a signal of what's actually running.

## How the image tag is actually managed (live deployment)

The real deployment is an **Argo CD `Application`** named `tenant-operator`
in namespace `tenant-operator` on the hub cluster, using
**`spec.source`** (singular) — not `spec.sources[]`, which is a different
field used by the unrelated *tenant* `ApplicationSet` (the one described in
the root README, that watches `bridge-tenants/*.yaml` and creates one
`Application` per tenant). Don't confuse the two — same Argo CD instance,
same `Application` CRD kind, entirely different objects and entirely
different meaning of "source".

The image tag is patched directly onto that `Application`'s
`spec.source.helm.valuesObject` — **not** by editing this chart's
`values.yaml` and re-pushing, and not via a normal `helm upgrade`:

```bash
kubectl patch application tenant-operator -n argocd --type json \
  -p '[{"op":"replace","path":"/spec/source/helm/valuesObject/image/tag","value":"0.2.6"}]'
```

Argo CD's automated sync then reconciles the running `Deployment` to that
tag. Bump `X.Y.Z` to whatever tag was just pushed to Docker Hub (check with
the `curl` command above — don't assume `0.2.6` stays current).

## Live gotcha: `existingSecretName` makes Secret patches silently no-op

**This is the single most important operational fact about the live
deployment, and it is easy to get wrong.**

The live `Application` sets **`existingSecretName: tenant-operator-secrets`**.
Look at `templates/secret.yaml`:

```yaml
{{- if not .Values.existingSecretName }}
apiVersion: v1
kind: Secret
...
{{- end }}
```

When `existingSecretName` is set, **this template renders nothing at
all** — Helm/Argo CD never creates, owns, or touches any `Secret` object.
The real `tenant-operator-secrets` Secret was **hand-created once** via a
plain `kubectl apply` (or equivalent), entirely outside Helm/Argo CD's
awareness, and the Deployment's `envFrom.secretRef` simply points at
whatever Secret object already exists under that name in the namespace.

**The consequence**: patching `spec.source.helm.valuesObject.secret.*` on
the `Application` does **nothing** to real secret values, even though Argo
CD will happily report the `Application` as `Synced` — because from Helm's
perspective, there is nothing to sync; the (non-existent, because
`existingSecretName` is set) `secret.yaml` template renders to nothing,
successfully. A `valuesObject.secret.SOME_KEY` patch is not an error, not a
no-op-with-a-warning — it just has no effect on anything real, silently.

**The correct way to actually change a secret value on the live
deployment** is to patch the real Secret directly, then force a pod
restart so the new value is actually read (env vars sourced from
`envFrom` are only read once, at container startup — they are **not**
live-reloaded):

```bash
kubectl patch secret tenant-operator-secrets -n tenant-operator --type merge \
  -p '{"stringData":{"SOME_KEY":"new-value"}}'

kubectl rollout restart deployment/tenant-operator -n tenant-operator
```

**`valuesObject.config.*` (the ConfigMap) does NOT have this problem.**
`templates/configmap.yaml` has no `existingConfigMapName`-style escape
hatch for the main config map — it always renders and is always
Helm/Argo-CD-managed, so a `valuesObject.config.SOME_KEY` patch on the
`Application` really does take effect on the next sync (still requires a
pod restart to be read, same `envFrom`-is-startup-only caveat as above —
Argo CD's automated sync triggers that restart for you via the Deployment
spec changing, since the ConfigMap's content itself isn't part of the pod
spec hash unless you've added a checksum annotation, which this chart does
not).

## Configuration

Almost every setting is a plain environment variable consumed by the
application's own `Settings` class (`app/config.py`, repo root) — this
chart's only job is deciding whether a given key ends up in the ConfigMap
(`config:`, non-secret) or the Secret (`secret:`/`existingSecretName:`).

| Values key | Purpose |
|---|---|
| `image.repository` / `image.tag` | See "Versioning" — defaults are stale; the live deployment overrides both via the Argo CD `Application`'s `valuesObject`, not this file. |
| `environment` | `stage` or `prod`. One release == one environment == one `ENVIRONMENT` ConfigMap value. |
| `config` | Any non-secret `Settings` field, `UPPER_SNAKE` key → ConfigMap → `envFrom`. Real live example values observed on the hub: `DATABASE_URL=sqlite:////data/db/tenant_operator.db` (SQLite, not Postgres, for this deployment's own bookkeeping — see "Persistence"), `VAULT_ENABLED=true`, `GIT_REPO_URL=https://github.com/sandeepkosework/k8s-infra-setup-testing.git`, `GIT_BRIDGE_TENANTS_DIR=bridge-tenants` (set, but currently unused by any live code path — see root README), `KUBECONFIG_PATH=/secrets/kube/config`, `MSSQL_ADMIN_HOST=192.168.85.203`, `MSSQL_ADMIN_PORT=30143`. These are illustrative of what this specific deployment runs, not universal defaults — every deployment's real values depend on its own infra. |
| `secret` / `existingSecretName` | Secret-shaped config (Git/Vault/Argo CD tokens, DB password if inline in `DATABASE_URL`, MSSQL admin password). **The live deployment uses `existingSecretName: tenant-operator-secrets`** — see the gotcha above; `secret:` inline values are ignored entirely in that mode (the template that would render them doesn't run). |
| `vault.enabled` / `vault.role` | Vault Agent Injector sidecar for the operator's **own** config bootstrap (`secret/tenant-operator/config`, read by `app/vault_bootstrap.py` before `Settings` is even constructed). Entirely separate from the application's runtime Vault writes for tenant secrets (`vault_service.py`), which always go through `VAULT_ADDR`/`VAULT_TOKEN`/`VAULT_TOKEN_FILE` in `config`/`secret` regardless of this setting. |
| `git.sshKey.existingSecretName` | SSH deploy key, if using `git@...` instead of an HTTPS PAT (`secret.GIT_HTTPS_TOKEN`/`config.GIT_REPO_URL` with an `https://` URL — the live deployment uses the HTTPS form). |
| `kubeconfig.existingSecretName` | Only needed when the tenant workload cluster(s) aren't the cluster the operator itself runs on. Mounted at `/secrets/kube` — also set `config.KUBECONFIG_PATH` to the actual file path inside that Secret. |
| `clustersConfig.inline` / `.existingConfigMapName` | Overrides the `clusters.yaml` baked into the image, so one image serves multiple environments/spoke registries without a rebuild. |
| `persistence.*` | Backs `GIT_LOCAL_PATH` — the operator's local clone of the GitOps repo. `ReadWriteOnce`. |
| `sqlitePersistence.*` | Backs `DATABASE_URL` when using `sqlite:////data/db/...` (the live deployment's actual setup) — not needed with a real external Postgres. |
| `rbac.create` | Baseline cluster-scoped `ClusterRole` (see "RBAC" below). |
| `rbac.crossplane.*` | Prod-only, off by default: namespaced `Role` for applying Crossplane OKE claims. |

`values.yaml`'s own inline comments are accurate and worth reading directly
for anything not covered above — they're kept in sync with the templates
(unlike the chart-version/appVersion drift noted above).

## Install

```bash
helm install tenant-operator . \
  --namespace tenant-operator --create-namespace \
  -f my-values.yaml
```

One release serves exactly one environment (`stage` or `prod`) and exactly
one hub-and-spoke fleet — run a second, entirely separate release (its own
namespace, its own `config`/`secret`, its own `clustersConfig`) for the
other environment. They must never share a spoke cluster or a Vault path
prefix.

**The real, currently-running deployment does not use `helm install`
directly** — it's managed as an Argo CD `Application` (`spec.source`,
singular) pointed at this chart path in this repo, with `valuesObject`
overriding the defaults above (image tag, `environment`, `config.*`, and
`existingSecretName`). A plain `helm install`/`helm upgrade` is the right
tool for a fresh/manual/non-Argo-CD deployment of this chart; the live one
is entirely Argo-CD-managed and should be changed via `kubectl patch
application ...` (as above) or by editing the `Application` object's own
spec, not by running Helm commands directly against the cluster.

## API surface (for reference — full detail is in the root README)

```
POST   /api/v1/tenant            -> 202 Accepted, { tenantId, status: PENDING }
GET    /api/v1/tenant/{id}       -> current tenant record incl. status + progress
GET    /api/v1/tenant            -> list, filterable by ?environment=&status_filter=
PUT    /api/v1/tenant/{id}       -> update version/users/db size/services (only when RUNNING/FAILED)
DELETE /api/v1/tenant/{id}       -> 202 Accepted, tears down via git delete + Argo CD prune
GET    /api/v1/tenant/{id}/vault -> this tenant's current Vault secrets
GET    /api/v1/cluster           -> SpokeCluster status for every registered spoke
GET    /api/v1/cluster/{name}    -> SpokeCluster status for one spoke
GET    /api/v1/vault/qraie-bridge-defaults            -> shared config for every qraie-bridge service
PUT    /api/v1/vault/qraie-bridge-defaults/{service}  -> overwrite one qraie-bridge service's shared config
GET    /health                   -> liveness/readiness (checked by the chart's probes -- deployment.yaml)
/socket.io                       -> live status push, as an alternative to polling GET /api/v1/tenant/{id}
```

Poll (or subscribe via Socket.IO to) `GET /api/v1/tenant/{id}` for status
transitions: `PENDING → VALIDATING → GIT_COMMITTED → SYNCING → RUNNING` (or
`FAILED`). Tenants are not monitored after reaching `RUNNING`. There is no
`appType` request field any more — every tenant is provisioned onto the
qraie-bridge chart unconditionally (see the root README for the full
history of the retired generic "workplace" chart this replaced, and for
the complete Vault schema/provisioning-workflow narrative — this document
deliberately doesn't repeat that here).

## Vault integration (chart-level summary)

Shared infra config that's the same for every tenant in this environment
is set once via `PUT /api/v1/vault/qraie-bridge-defaults/{service}` and
copied into each new tenant's secrets at creation time. Tenant-specific
values (database usernames/passwords, JWT secrets, Redis passwords, and
identity fields derived from the tenant's own slug) are generated fresh per
tenant instead. Secrets land at `secret/tenants/<tenant-slug>/<service>`
(one path per chart service, ~32 of them) plus
`secret/tenants/<tenant-slug>/common` — consumed by the qraie-bridge
chart's own per-service `envFrom`, not Vault Agent injection for tenant
workloads (Vault Agent injection in *this* chart is only for
tenant-operator's own config bootstrap — see `vault.enabled` above).

This chart's own responsibility here is narrow: it wires
`VAULT_ADDR`/`VAULT_ENABLED`/`VAULT_TOKEN_FILE`/`VAULT_KV_MOUNT`/
`VAULT_TENANT_SECRET_PREFIX` into the ConfigMap/Secret, and optionally
enables the Vault Agent Injector sidecar for the operator's own separate
config-bootstrap use case. The actual Vault schema and write logic live
entirely in the application (`app/services/vault_service.py`) — see the
root README's "qraie-bridge Vault integration" section for the full
mechanism (platform-default vs. generated vs. derived keys, the
`MONGODB_URI`/`VIRTUAL_HOST` special cases, etc.).

## RBAC

Two identities' worth of rules, deliberately scoped as narrowly as each job
allows (`templates/rbac.yaml`):

- **Cluster-scoped, mostly read-only** (`rbac.create`, default `true`) —
  `get`/`list`/`watch` on namespaces/pods/PVCs/deployments/ingresses across
  every spoke, plus exactly one write verb: `delete` on namespaces. That one
  exception exists because Argo CD's `CreateNamespace=true` sync option
  creates a tenant's namespace but never tracks or prunes it — this
  operator deletes it directly on tenant teardown. Every other write
  happens by committing to git and letting Argo CD apply it, never via a
  direct API write from this service against a spoke.
- **Namespaced, on the operator's own namespace** — `create`/`get`/`list`/
  `watch` on `batch/v1` Jobs only, for submitting and polling the
  meta-builder DB-seeding Job (this Job submission is real, not simulated —
  see the root README's "Known limitations"). Never broadened beyond that
  resource type. Always rendered, regardless of `rbac.create`.
- **Crossplane** (`rbac.crossplane.enabled`, prod-only, off by default) — a
  namespaced `Role` scoped to exactly one Crossplane claim CRD
  (`rbac.crossplane.apiGroup`/`.resource`/`.namespace`), for requesting new
  spoke clusters once a spoke crosses its capacity threshold. Also a real,
  non-simulated write when enabled.

## Persistence

Two independent PVCs, both `ReadWriteOnce` (`templates/pvc.yaml`):

- **`persistence`** — the operator's local clone of the GitOps repo
  (`GIT_LOCAL_PATH`, mounted at `/data/tenant-config`). Needs to survive
  pod restarts for a warm cache; a fresh clone on every restart also works,
  just slower on the first request after a restart.
- **`sqlitePersistence`** — the operator's own bookkeeping database, when
  `DATABASE_URL` points at `sqlite:////data/db/...` rather than an external
  Postgres. **This is what the live deployment actually uses** — its
  `DATABASE_URL` is `sqlite:////data/db/tenant_operator.db`, and
  `sqlitePersistence.mountPath` defaults to `/data/db` to match. If you
  switch a deployment to a real external Postgres instead, set
  `sqlitePersistence.enabled: false` to skip provisioning an unused PVC.

Both support `existingClaimName` to bring your own PVC instead of letting
the chart create one.

## Deployment instructions (full walkthrough)

```bash
# 1. Confirm the real current image tag (don't trust values.yaml's default)
curl -s "https://hub.docker.com/v2/repositories/sandeepkosework/tenant-operator/tags?page_size=5"

# 2a. Fresh install with plain Helm (manual/non-Argo-CD path)
cat > my-values.yaml <<'EOF'
image:
  tag: "0.2.6"           # use whatever the curl above actually returned
environment: stage
config:
  GIT_REPO_URL: "https://github.com/your-org/tenant-config.git"
  ARGOCD_SERVER: "https://argocd.hub.internal"
  KUBECONFIG_PATH: "/secrets/kube/config"
  VAULT_ENABLED: "true"
  VAULT_ADDR: "http://vault.vault.svc.cluster.local:8200"
existingSecretName: tenant-operator-secrets   # created separately, see below
git:
  sshKey:
    existingSecretName: tenant-operator-git-ssh-key
kubeconfig:
  existingSecretName: tenant-operator-kubeconfig
EOF

# The Secret referenced by existingSecretName must exist BEFORE install/upgrade
# (this chart will not create or manage it while existingSecretName is set --
# see "Live gotcha" above):
kubectl create namespace tenant-operator
kubectl -n tenant-operator create secret generic tenant-operator-secrets \
  --from-literal=GIT_HTTPS_TOKEN=ghp_xxx \
  --from-literal=ARGOCD_TOKEN=xxx \
  --from-literal=VAULT_TOKEN=xxx \
  --from-literal=MSSQL_ADMIN_PASSWORD=xxx

helm install tenant-operator . --namespace tenant-operator -f my-values.yaml

# 2b. Or, for the real Argo-CD-managed path this deployment actually uses:
# create/edit an Argo CD Application (spec.source, singular) pointed at this
# chart directory, with valuesObject carrying the same keys as my-values.yaml
# above, then let Argo CD sync it. Subsequent image bumps go through
# `kubectl patch application ...` (see "How the image tag is actually
# managed"), not helm upgrade.
```

```bash
# Verify
kubectl -n tenant-operator get pods -l app.kubernetes.io/name=tenant-operator
kubectl -n tenant-operator port-forward svc/tenant-operator 8000:80
curl http://localhost:8000/health
```

```bash
# Upgrade (plain Helm path)
helm upgrade tenant-operator . --namespace tenant-operator -f my-values.yaml
# Expect a short window of downtime -- strategy: Recreate, not RollingUpdate
# (see "Operational notes" below), and a full pod restart is required for
# any envFrom (ConfigMap/Secret) change to actually be read regardless.
```

```bash
# Uninstall
helm uninstall tenant-operator --namespace tenant-operator
# PVCs are NOT deleted automatically by Helm -- clean up
# tenant-operator-git-cache / tenant-operator-db manually if you actually
# want the data gone, otherwise they're just orphaned and safe to leave
# (e.g. before a reinstall that should keep the same git cache/DB).
```

## Operational notes

- **Single replica by design.** The application's git write lock
  (`git_service.py`) and its Socket.IO status bus (`status_bus.py`) are
  both in-process only — safe for exactly one replica. Don't raise
  `replicaCount` without first moving the git critical section to a
  Postgres advisory lock and the status bus to Redis pub/sub (both are
  small, contained changes in the application, not this chart).
- **`strategy: Recreate`, not `RollingUpdate`**, deliberately — this
  chart's PVCs are `ReadWriteOnce`, and a surge pod briefly coexisting with
  the old one during a rolling update can deadlock on volume attachment,
  especially on a single-worker-node cluster. Expect a short window of
  downtime on every upgrade/image-tag patch, not a rolling one.
- **`existingSecretName` silently detaches Helm/Argo CD from real secret
  management** — see "Live gotcha" above. This is the single most
  important thing to know before touching this deployment's config; a
  `valuesObject.secret.*` patch that looks like it succeeded (Argo reports
  `Synced`) can be a complete no-op.
- **`envFrom` (both ConfigMap and Secret) is read once, at pod startup.**
  Neither a ConfigMap edit nor a Secret edit takes effect until the pod
  restarts — `kubectl rollout restart deployment/tenant-operator -n
  tenant-operator` after any real config or secret change, always.
- **`clustersConfig` lets one image serve multiple environments.** The
  `clusters.yaml` baked into the image at build time is only a default —
  mount a real per-environment registry (spoke names, Argo CD cluster
  server URLs, per-environment capacity limits) via
  `clustersConfig.existingConfigMapName` instead of rebuilding the image
  per environment.
- **Stage and prod must never share a spoke or a clusters registry.** Each
  release's `clustersConfig` should list only that one environment's
  spokes, and each release's Vault paths (`VAULT_TENANT_SECRET_PREFIX`,
  typically left at the shared default `tenants` but distinguished by
  environment at a higher path segment if you run both against the same
  Vault) should never collide either.
- **The meta-builder Job and (if enabled) the Crossplane claim are real
  Kubernetes API calls from this pod** — not GitOps, not echoed/simulated.
  Both are scoped by the RBAC above; neither writes anywhere outside its
  one narrow resource type.
