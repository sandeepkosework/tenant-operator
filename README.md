# Tenant Operator

Single entry point for onboarding tenants onto the hub-and-spoke Kubernetes
platform. UI/API/automation talk only to this service; it is the only thing
that writes to the GitOps repo, and it never calls `kubectl apply` directly
against a spoke.

```
POST /api/v1/tenant { tenantId, displayName, email, password, domain }
        │
        ▼
Validate → select spoke (sequential fill; capacity check/scale-out per env)
        │
Create Tenant CR on hub (intent record, continuously updated as status changes)
        │
Render values.yaml → commit to Git ──────► Argo CD ApplicationSet detects file
        │                                             │
        ▼                                             ▼
Poll Argo CD + K8s for health               Application created/synced automatically
        │
Tenant RUNNING → trigger meta-builder Job (Mongo/Postgres seeding)
```

Each spoke also gets a `SpokeCluster` CR on the hub, kept current with the
list of tenants placed there and counts against capacity -- see
`GET /api/v1/cluster`.

## Repo layout

```
tenant-operator/
├── app/                              the deployable application
│   ├── main.py                       FastAPI app, startup, CORS, mounts Socket.IO
│   ├── config.py                     all settings, env-var driven
│   ├── vault_bootstrap.py            loads this operator's OWN config from Vault, pre-Settings
│   ├── socketio_app.py               Socket.IO transport for tenant status push
│   ├── database.py                   SQLAlchemy engine/session
│   ├── api/
│   │   ├── tenant.py                 POST/GET/PUT/DELETE /api/v1/tenant + GET .../{id}/vault
│   │   ├── cluster.py                GET /api/v1/cluster[/{name}] -- SpokeCluster CR view
│   │   ├── vault.py                  PUT/GET /api/v1/vault/common -- shared config across all tenants
│   │   └── health.py                 GET /health
│   ├── models/
│   │   ├── tenant.py                 ORM model + status enum + STATUS_PROGRESS map
│   │   └── schemas.py                Pydantic request/response models
│   └── services/
│       ├── validation.py             request validation (400s)
│       ├── cluster_selector.py       sequential-fill spoke placement + capacity
│       ├── spoke_scaler.py           capacity threshold -> new-spoke job (prod) / notify (stage)
│       ├── crossplane_service.py     prod-only: applies/watches the Crossplane OKE claim on the hub
│       ├── notifications.py          notification sink (log-only for now)
│       ├── status_bus.py             in-process pub/sub feeding the Socket.IO status push
│       ├── vault_service.py          common config (shared) + per-tenant secrets (generated) in Vault
│       ├── tenant_cr.py              builds/echoes the per-tenant Tenant CR on the hub
│       ├── spoke_cr.py               builds/echoes the per-spoke SpokeCluster CR on the hub
│       ├── meta_builder_job.py       triggers the Mongo/Postgres seeding Job once RUNNING
│       ├── helm_values.py            renders tenants/{name}.yaml from Jinja2
│       ├── git_service.py            clone/commit/push to the GitOps repo
│       ├── argocd_service.py         read-only Argo CD status polling + failure classification
│       ├── kubernetes_service.py     read-only namespace/deploy/pvc/ingress checks
│       └── provisioner.py            the async state machine tying it together
├── templates/
│   ├── values.yaml.j2                per-tenant Helm values template
│   └── applicationset.example.yaml   one-time platform setup (see below)
├── clusters.yaml                     default/example registry, baked into the image -- see below
├── Dockerfile                        builds the operator image (app/, templates/, clusters.yaml only)
├── requirements.txt
├── .env.example
├── .gitignore
├── .dockerignore
├── deploy/                           manifests to run the operator itself, on the Hub
│   ├── deployment.yaml               Namespace/Deployment/Service/PVC/ServiceAccount ref
│   ├── crossplane-rbac.yaml          Role/RoleBinding for the Crossplane claim CRD (prod only)
│   ├── clusters-configmap.stage.example.yaml  stage's real spoke registry (mounted, overrides the baked-in one)
│   ├── clusters-configmap.prod.example.yaml   prod's real spoke registry (entirely separate spokes)
│   ├── configmap.example.yaml        non-secret config template
│   └── secret.example.yaml           secret config template (fill in, never commit)
└── poc/                              local dev/testing tooling -- NOT part of the deployment
    ├── run_local_no_cluster.py       run the API with Argo CD/K8s calls faked, no cluster needed
    ├── onboard.py / onboard.sh       CLI to onboard tenants + watch status transitions live
    ├── .env.nocluster.example        config for the above
    ├── clusters.nocluster.yaml       fake 2-spoke registry for local logic testing
    ├── init_local_gitops_repo.sh     seeds a local bare git repo as a GitHub stand-in
    └── POC.md / kind-config.yaml / docker-compose.yml / chart-workplace/
                                      full local walkthrough using kind + real Argo CD
```

`poc/` is guaranteed to never end up in the deployed image or affect a real
deployment: the Dockerfile only ever `COPY`s `app/`, `templates/`,
`clusters.yaml`, and `requirements.txt` by name (never `COPY . .`), and
`.dockerignore` is a second guardrail against that changing by accident.
Use it freely for local testing/demos without worrying about it leaking
into what you ship to the hub cluster.

## One-time platform setup (do this before the operator's first request)

1. **Git repo**: create the `tenant-config` repo with `tenants/`, `charts/`,
   `applicationsets/` directories (matches the layout in the design doc).
2. **ApplicationSet**: apply `templates/applicationset.example.yaml` to Argo CD
   once, by hand or via your platform CI. This is what makes the operator's
   job simple -- it only ever commits/deletes a file under `tenants/`, and
   the ApplicationSet's git-directory generator creates/removes the Argo CD
   `Application` automatically.
3. **Register spoke clusters with Argo CD** (`argocd cluster add <context>`)
   so the ApplicationSet can target them by server URL, and list them in
   your environment's clusters ConfigMap (name/context/region/environments/
   max_tenants) -- see `deploy/clusters-configmap.stage.example.yaml` and
   `deploy/clusters-configmap.prod.example.yaml`. **Stage and prod are
   entirely separate hub-and-spoke fleets and must never share a spoke** --
   each ConfigMap should only ever list that one environment.
4. **Argo CD API token**: create a project-scoped token with permission to
   read Application status (the operator only needs read access).
5. **Kubeconfig**: build one kubeconfig with a context per spoke cluster
   (`spoke-1`, `spoke-2`, ...), scoped to a ServiceAccount with **read-only**
   RBAC on Deployments/Pods/PVCs/Ingresses/Namespaces -- the operator never
   writes to Kubernetes directly.
6. **Git deploy key**: an SSH deploy key (or HTTPS PAT) with write access
   to `tenant-config`, mounted into the operator's pod.
7. **PostgreSQL**: create a database + user on the Database VM
   (`tenant_operator` in the examples).
8. **Meta-builder image**: build/push the `bridge-meta-builder` image
   referenced by `META_BUILDER_JOB_IMAGE`, and make sure the operator's
   ServiceAccount (or whichever identity submits the Job) can create Jobs in
   `META_BUILDER_JOB_NAMESPACE`.
9. **Crossplane (prod only)**: install Crossplane on the hub cluster along
   with an OKE provider and a Composition/XRD that accepts the parameters
   `crossplane_service.build_oke_claim()` sends (region, nodeCount by
   default -- adjust both the claim shape and your XRD to match each
   other). Create the `CROSSPLANE_CLAIM_NAMESPACE` namespace (default
   `platform-infra`), apply `deploy/crossplane-rbac.yaml`, and only then set
   `CROSSPLANE_ENABLED=true` in the prod ConfigMap. Leave it `false` for
   stage.
10. **Vault**: create a Vault token (or Agent-injected token file) with
    write access to `VAULT_KV_MOUNT`/`VAULT_TENANT_SECRET_PREFIX`
    (default `secret/tenants/*`), set `VAULT_ADDR`/`VAULT_TOKEN` in the
    Secret and `VAULT_ENABLED=true` in the ConfigMap. Also confirm
    `TENANT_POSTGRES_HOST`/`TENANT_MONGO_HOST`/`TENANT_REDIS_HOST` point at
    your real shared infra endpoints -- these are written into every new
    tenant's Vault secrets alongside a generated credential. Optional: if
    you'd rather manage this operator's own config in Vault too (instead of
    the ConfigMap/Secret), put it at `VAULT_CONFIG_PATH`
    (default `secret/tenant-operator/config`) -- see `app/vault_bootstrap.py`.

## Local development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in real values
uvicorn app.main:app --reload
```

To test the full onboarding/capacity logic **without any of the above
infra** (no cluster, no Argo CD, no kind), see `poc/README` usage in
`poc/POC.md` for the kind-based walkthrough, or for a zero-infra option:

```bash
cp poc/.env.nocluster.example .env
python poc/run_local_no_cluster.py       # fakes Argo CD/K8s health checks only
./poc/onboard.sh --tenant-id acme --display-name "Acme Corp" \
  --email admin@acme.com --password StrongPassword123 --domain acme.example.com
```

`Base.metadata.create_all()` runs on startup for convenience. For anything
beyond local dev, switch to Alembic migrations:

```bash
pip install alembic
alembic init migrations
# point migrations/env.py at app.database.Base.metadata and DATABASE_URL,
# then: alembic revision --autogenerate -m "init" && alembic upgrade head
```

## API

```
POST   /api/v1/tenant            -> 202 Accepted, { tenantId, status: PENDING }
GET    /api/v1/tenant/{id}       -> current tenant record incl. status + progress
GET    /api/v1/tenant            -> list, filterable by ?environment=&status_filter=
PUT    /api/v1/tenant/{id}       -> update version/users/db size (only when RUNNING/FAILED)
DELETE /api/v1/tenant/{id}       -> 202 Accepted, tears down via git delete + Argo CD prune
GET    /api/v1/tenant/{id}/vault -> this tenant's current Vault secrets (redis/database/mongo/jwt)
GET    /api/v1/cluster           -> SpokeCluster CR for every registered spoke
GET    /api/v1/cluster/{name}    -> SpokeCluster CR for one spoke
GET    /api/v1/vault/common      -> current common config (shared across every tenant)
PUT    /api/v1/vault/common      -> overwrite the common config
GET    /api/v1/vault/qraie-bridge-defaults           -> current shared config for every qraie-bridge service
PUT    /api/v1/vault/qraie-bridge-defaults/{service} -> overwrite one qraie-bridge service's shared config
GET    /health                   -> liveness/readiness (checks DB connectivity)
/socket.io                       -> Socket.IO status push (see below)
```

`POST /api/v1/tenant`'s `appType` field selects which chart/Vault schema a
tenant uses: `"workplace"` (default) is the original single-image chart,
using the generic Vault shape described above. `"qraie-bridge"` is the
~30-service [`helm-chart-bridge`](../helm-chart-bridge) chart, which has its
own, much wider Vault schema — see "qraie-bridge Vault integration" below.
For `appType="qraie-bridge"`, the request's `services` map (service name ->
`true`/`false`) and `onlyListedServices` flag control which of the chart's
services get disabled (`app/services/helm_values.py::compute_disabled_services()`),
rendered into the tenant's values file as `disabledServices:` — never as a
`services:` override, since Helm replaces lists wholesale rather than
merging them (see that chart's README for the full reasoning).

Which environment (`prod`/`stage`) a tenant lands in is **not** part of the
request -- it's set once per operator deployment via the `ENVIRONMENT` env
var (see `deploy/configmap.example.yaml`). Run one Deployment per
environment.

Example create request:

```json
POST /api/v1/tenant
{
  "tenantId": "acme",
  "displayName": "Acme Corporation",
  "email": "admin@acme.com",
  "password": "StrongPassword123",
  "domain": "acme.example.com"
}
```

Poll `GET /api/v1/tenant/{id}` for status transitions:
`PENDING → VALIDATING → GIT_COMMITTED → SYNCING → RUNNING` (or `FAILED`).
Tenants are not monitored after reaching `RUNNING` -- see "Argo CD failure
monitoring" below for exactly what's covered. Every response also includes
a `progress` field (0-100, see `STATUS_PROGRESS` in `app/models/tenant.py`)
for a simple loading bar even without using the Socket.IO push below.

**For a live loading bar without polling**, use Socket.IO instead
(`app/socketio_app.py`, mounted at `/socket.io`, same host/port as the REST
API):

```js
import { io } from "socket.io-client";

const socket = io("http://localhost:8000");
socket.emit("subscribe", { tenantId: id });
socket.on("status", ({ status, progress, errorMessage }) => {
  updateLoadingBar(progress);
  if (status === "RUNNING" || status === "FAILED") socket.disconnect();
});
socket.on("error", ({ message }) => console.error(message));
```

It emits the current status immediately after `subscribe`, then one event
per subsequent transition, and stops emitting once a terminal status
(`RUNNING`/`FAILED`/`DELETED`) is reached (the subscription is cleaned up
server-side at that point, or on disconnect, whichever comes first).

## Argo CD failure monitoring

Scoped entirely to **a tenant's own rollout** (`provisioner._wait_for_argocd_and_k8s`,
runs for new onboarding and for `PUT` updates) -- tenants are not monitored
after they reach `RUNNING`. Built on `argocd_service.classify()`, this fails
fast instead of silently retrying for the full `PROVISIONING_TIMEOUT_SECONDS`:
- Argo CD reports a **definitive failure** (Degraded health, or the last
  sync operation itself Failed/Error) → the tenant goes straight to
  `FAILED` with Argo's own message as the error, on the very next poll.
- Argo CD itself is **unreachable** for `ARGOCD_UNREACHABLE_FAIL_AFTER`
  (default 5) consecutive polls → `FAILED`, and a notification fires on the
  *first* unreachable poll (so you find out immediately, not just when the
  tenant eventually gives up).
- Anything else (Progressing, Suspended, a transient blip) → keeps polling
  as before.

Calls the same `notifications.notify()` sink used elsewhere (log-only for
now -- see `app/services/notifications.py` to wire in Slack/PagerDuty/email)
and pushes through `status_bus`/Socket.IO, so anything watching that
tenant live sees the failure too.

## Vault: common vs. tenant-specific variables

Every tenant's Vault secrets are two kinds of variable merged together
(`vault_service.build_initial_tenant_secrets()`):

- **Common** -- shared infra config, identical for every tenant in this
  environment: Redis/Postgres/Mongo hosts+ports, JWT signing config. Lives
  at `secret/{VAULT_COMMON_SECRET_PATH}` (default `common/config`), a
  single object, not per-tenant. Managed via:
  ```
  GET /api/v1/vault/common
  PUT /api/v1/vault/common   { "redis_host": "...", "postgres_host": "...", "jwt_issuer": "...", ... }
  ```
  Falls back to the `TENANT_POSTGRES_HOST`/etc. Settings fields if Vault is
  disabled or nothing's been pushed yet -- those are seed/fallback values,
  not the source of truth once you're actually using this.

- **Tenant-specific** -- generated fresh per tenant at onboarding time:
  `db_username`/`root_username` (the tenant's own id), and freshly random
  `password`/`jwt_secret` values. These can never come from the common
  config since they must be unique per tenant. Written to
  `secret/{VAULT_TENANT_SECRET_PREFIX}/{tenantId}/{redis,database,mongo,jwt}`
  -- the exact paths/fields `bridge-meta-builder`'s `safeVault()` calls
  expect. Inspect what's currently stored for a tenant via:
  ```
  GET /api/v1/tenant/{id}/vault
  ```

Both only write for real when `VAULT_ENABLED=true`; otherwise every call
logs/echoes what it would have done (same pattern as `crossplane_service.py`)
and `read_common_config()` just returns the Settings-derived fallback.

**Local/dev Vault**: `poc/vault-dev.yaml` runs Vault in dev mode (in-memory,
auto-unsealed, fixed root token `"root"`) for testing this against a real
Vault server without any production setup -- `kubectl apply -f
poc/vault-dev.yaml`, then point `VAULT_ADDR` at
`http://vault.vault.svc.cluster.local:8200` and `VAULT_TOKEN=root`. **Not**
for anything resembling production -- see that file's header comment for
what a real deployment needs instead (persistent raft storage, auto-unseal,
real auth).

## qraie-bridge Vault integration

`appType="qraie-bridge"` tenants use a completely different Vault layout
from the generic `redis/database/mongo/jwt` shape above -- one path per
chart service, since [`helm-chart-bridge`](../helm-chart-bridge) delivers
secrets via a 3-layer `envFrom` (see that chart's README), not a handful of
Vault-Agent-injected files. All of this lives in
`app/services/vault_service.py`, below the generic functions.

**Path taxonomy** (four distinct kinds of Vault data, easy to mix up):

| Path | Scope | Written by | Consumed via |
|---|---|---|---|
| `secret/k8s/tenant-common` | Every tenant, every environment | Manually (`vault kv put`), not this operator | Chart's `commonSecrets` -- read live, not copied |
| `secret/qraie-bridge/platform-defaults/<service>` | Every tenant, this environment | `PUT /api/v1/vault/qraie-bridge-defaults/{service}` | Copied into new tenants' per-service secrets at creation time |
| `secret/tenants/<slug>/common` | This tenant only, all its services | `write_initial_qraie_bridge_tenant_secrets()` (once, at creation) | Chart's `tenantSecrets` |
| `secret/tenants/<slug>/<service>` | This tenant, this service only | `write_initial_qraie_bridge_tenant_secrets()` (once, at creation) | Chart's per-service `envFrom` (always wins) |

**Per-service key classification** (`QRAIE_BRIDGE_SERVICE_KEYS`, one entry
per chart service, keeps the full schema in code instead of only existing as
ad-hoc `vault kv put` commands) splits every key three ways when a new
tenant's secrets are written:

- **`QRAIE_BRIDGE_TENANT_DERIVED_KEYS`** (`TENANT_ID`, `TENANT_KEY`,
  `TENANT_IDS`, `DB_USER`, `DB_NAME`) -- set to the tenant's own slug. Must
  be unique per tenant; a shared platform default here would mean every
  tenant's seeding Job operates on the same database/login.
- **`QRAIE_BRIDGE_GENERATED_KEYS`** (`REDIS_PASSWORD`, `DB_PASSWORD`,
  `WORKPLACE_DM_PASSWORD`, `RADICALE_AGENT_PASS`, `JWT_SECRET`) -- a fresh
  random value per tenant, per service.
- **Everything else** -- copied from that service's current
  `secret/qraie-bridge/platform-defaults/<service>` entry (or `""` if never
  configured yet). This is shared business/integration config (external API
  URLs/creds, hosts, flags) that's genuinely the same for every tenant in
  this environment, not a credential.

Set the shared half once per environment, before onboarding tenants that
need it:
```
PUT /api/v1/vault/qraie-bridge-defaults/tranops-backend
{"SLM_API_URL": "...", "SLM_PASSWORD": "..."}
```
This only affects tenants provisioned *after* the call -- an existing
tenant's `secret/tenants/<slug>/<service>` was already written once at its
own creation time and is not kept in sync with later platform-default
changes.

**Filename in the GitOps repo**: `commit_tenant_manifest()`/
`delete_tenant_manifest()` (`app/services/git_service.py`) key the file on
`tenant.slug`, not `tenant.tenant_name` -- the latter has no DB uniqueness
constraint, so two tenants sharing a display name would otherwise silently
overwrite each other's file in
[`k8s-infra-setup-testing`](../k8s-infra-setup-testing)/`tenants/`.

## Deploying the operator itself

One image, two Deployments -- one per environment, each in its own
namespace/ConfigMaps/Secrets, on entirely separate hub-and-spoke fleets:

```bash
docker build -t your-registry/tenant-operator:latest .
docker push your-registry/tenant-operator:latest

# fill in real values, then apply (never commit the filled-in copies):
cp deploy/configmap.example.yaml deploy/tenant-operator-config.yaml
cp deploy/secret.example.yaml deploy/tenant-operator-secrets.secret.yaml

# pick ONE, matching which environment this Deployment serves:
cp deploy/clusters-configmap.stage.example.yaml deploy/tenant-operator-clusters.yaml
# cp deploy/clusters-configmap.prod.example.yaml deploy/tenant-operator-clusters.yaml

kubectl apply -f deploy/tenant-operator-config.yaml
kubectl apply -f deploy/tenant-operator-secrets.secret.yaml
kubectl apply -f deploy/tenant-operator-clusters.yaml
kubectl apply -f deploy/crossplane-rbac.yaml   # prod only -- needs the CROSSPLANE_CLAIM_NAMESPACE to already exist
kubectl apply -f deploy/deployment.yaml   # against the Hub cluster
```

Repeat against the *other* hub-and-spoke fleet's cluster for the other
environment (own namespace, own Secret, own `ENVIRONMENT` value in the
ConfigMap, own clusters ConfigMap) -- same image, no rebuild needed.

You'll also need to create `tenant-operator-git-ssh-key` and
`tenant-operator-kubeconfig` as Kubernetes Secrets -- not templated here
since their contents are environment-specific credentials, not config.

## Operational notes / known limits worth knowing before you go to prod

- **Single replica by design right now.** `git_service.py` uses an in-process
  lock around the working tree, which only protects against concurrent
  requests *within one pod*. Running >1 replica as-is can race on git
  pushes. To scale out: either keep 1 replica (fine for most tenant-creation
  volumes -- it's I/O-bound, not CPU-bound), or move the git critical section
  behind a Postgres advisory lock (`pg_advisory_lock`) so multiple replicas
  serialize correctly. `BackgroundTasks` also only runs in the process that
  received the HTTP request, so it doesn't provide crash-recovery -- if the
  pod restarts mid-provision, that tenant is stuck in whatever status it was
  last in. Moving to Celery + Redis/RabbitMQ is the natural next step, and
  `provisioner.py`'s functions are already structured as plain functions so
  wrapping them as `@celery_app.task` is a small change.
- **Socket.IO status push is also single-replica/in-memory.** `status_bus.py`'s
  pub/sub (what `socketio_app.py` relays over the wire) is a plain
  in-process `dict` of `queue.Queue`s -- a client connected to one pod won't
  see events published by provisioning work running on another, and
  `socketio.AsyncServer`'s own client<->sid session tracking is in-memory
  too (pass a Redis-backed `client_manager` if you run >1 replica, so
  clients can be routed to whichever pod they're actually connected to).
  Fine at 1 replica; revisit alongside the Celery move above if you scale
  out. A disconnected/never-connected client loses nothing permanently --
  `GET /api/v1/tenant/{id}` (with its `progress` field) is always there as
  a fallback, it just requires polling instead of being pushed to.
- **Vault write is secrets-only, not account provisioning.** `vault_service.py`
  generates and writes credentials to `secret/tenants/{tenantId}/{redis,database,mongo,jwt}`
  so bridge-meta-builder's `safeVault()` calls succeed, but it does **not**
  create the underlying Postgres/Mongo/Redis accounts those credentials
  describe -- that's a separate provisioning step (either the meta-builder
  Job itself, via its `ensureDatabaseExists()`, or your own infra
  automation) that must actually honor whatever lands in Vault. If your
  real setup uses a shared service account instead of a dedicated
  per-tenant credential, replace the generated password in
  `vault_service.build_initial_tenant_secrets()` accordingly.
- **New-spoke provisioning: Crossplane for prod, notify-only for stage.**
  When a prod spoke crosses its scale-out threshold, `spoke_scaler.py`
  applies a Crossplane claim (`crossplane_service.py`, kind configurable via
  `CROSSPLANE_KIND`/`CROSSPLANE_API_VERSION`/`CROSSPLANE_PLURAL`) on the hub
  cluster requesting a new OKE cluster, then polls it until Crossplane
  reports the claim Ready -- all on a background thread so it never blocks
  the tenant currently being created. This only fires when
  `CROSSPLANE_ENABLED=true` (see `deploy/configmap.example.yaml`); with it
  false (the default, and what stage and local/no-cluster testing use) the
  old log-only simulation runs instead. Either way, this stops at
  "cluster exists and is Ready" -- registering it with Argo CD
  (`argocd cluster add <context>`) and adding it to the prod clusters
  ConfigMap are still separate, deliberate steps, logged clearly when the
  claim goes Ready. New prod spokes are named `spoke-prod-<N>`
  (`spoke_scaler._next_spoke_name()`), numbered independently from stage's
  `spoke-<N>` spokes -- the two environments never share a name any more
  than they share a cluster. The Crossplane claim's `spec.parameters` shape in
  `crossplane_service.build_oke_claim()` is a generic placeholder -- adjust
  it to match your actual OKE Composition/XRD schema before enabling this
  in prod. Stage has no automation at all by design (per your requirement
  that Crossplane is prod-only) -- it only emits a notification
  (`notifications.py`) for a human to provision the next spoke manually. If
  every spoke for an environment is genuinely full,
  `NoAvailableClusterError` surfaces as a `FAILED` tenant with a clear error
  message.
- **Placement is sequential-fill, not load-balanced.** `cluster_selector.py`
  fills spokes in the order they appear in the mounted clusters registry, so
  spoke-1 is always full before spoke-2 receives a tenant. This is what
  makes "spoke hits its capacity threshold" a meaningful, single-spoke
  event. Combined with stage and prod each having their own registry, a
  spoke is never shared across environments either.
- **RBAC**: the kubeconfig the operator uses against each *spoke* should be
  read-only (`kubernetes_service.py`) -- writes there only ever happen via
  Argo CD reading git. The *hub* is the one exception: `crossplane_service.py`
  needs write RBAC there (create/get on the Crossplane claim CRD) to apply
  and poll claims. It defaults to in-cluster config (`HUB_KUBECONFIG_PATH`/
  `HUB_KUBE_CONTEXT` unset) since the operator runs on the hub itself --
  give its ServiceAccount a Role scoped to just that CRD, not cluster-admin.
- **Namespace-per-tenant assumes single-tenant-per-namespace Helm charts**
  labelled with `app.kubernetes.io/instance=<tenant_name>` -- adjust the
  label selector in `kubernetes_service.py` if your charts label differently.
- **Tenant CR / SpokeCluster CR are intent records, not real CRDs (yet).**
  `tenant_cr.py` and `spoke_cr.py` build the objects and log/echo the
  equivalent `kubectl apply` rather than calling the Kubernetes API --
  install the actual CRDs on the hub and swap in a real API call in those
  two modules when you're ready to make them authoritative instead of
  advisory.
- **Meta-builder Job is echoed too.** `meta_builder_job.py` builds the Job
  manifest and logs/echoes it rather than submitting it -- swap in a real
  `BatchV1Api.create_namespaced_job` call once the image and RBAC are ready.
