# Tenant Operator

Single entry point for onboarding tenants onto a hub-and-spoke Kubernetes
platform, entirely through GitOps. UI/API/automation talk only to this
FastAPI service; it is the only thing that writes to the GitOps repo, and it
never calls `kubectl apply`/`create`/`patch` against a **spoke** cluster
directly — Argo CD does the actual deploying, reacting to files this
operator commits. (The one deliberate write exception, and the one real,
non-echoed Kubernetes Job submission, are both called out explicitly below.)

```
POST /api/v1/tenant { tenantId, displayName, email, password, domain, services?, onlyListedServices? }
        │
        ▼
Validate → select spoke (sequential fill; capacity check/scale-out per env)
        │
Create Tenant CR on hub (intent record, echoed/logged -- see "Known limitations")
        │
Write per-tenant secrets to Vault (+ mirror to MongoDB) → render values.yaml → commit to Git
        │                                                            │
        ▼                                                            ▼
Poll Argo CD + Kubernetes for health                       ApplicationSet detects the file,
        │                                                   creates/syncs the Application
        ▼                                                   automatically
Tenant RUNNING → trigger meta-builder Job (real MSSQL DB/login seeding, when configured)
```

Each spoke also gets a `SpokeCluster` CR on the hub, kept current with the
list of tenants placed there and counts against capacity — see
`GET /api/v1/cluster`.

## What this service actually does (and does not do)

- It is a **control-plane orchestrator**, not a workload. It never runs
  tenant application code itself.
- It writes exactly one artifact per tenant into a separate GitOps repo
  (`{GIT_REPO_URL}`, e.g. `k8s-infra-setup-testing`): a rendered
  `values.yaml` for the **qraie-bridge** Helm chart, under
  `{GIT_TENANTS_DIR}/{tenant.slug}.yaml` (default directory `tenants/`).
  An Argo CD `ApplicationSet` (applied once, by hand — see
  `templates/qraie-bridge-applicationset.example.yaml`) watches that
  directory and creates/syncs/prunes the per-tenant Argo CD `Application`
  automatically. This operator never talks to that `Application` object
  except to *read* its status back.
- Every tenant is provisioned onto **one chart**: qraie-bridge, a ~32-service
  conversion of a prototype docker-compose stack (redis, several API
  gateways/backends/UIs, IoT broker services, a scheduler, a calendar
  server, etc.). There is no second chart choice any more — see "The
  retired `workplace` chart" below.
- It owns its own small bookkeeping database (`tenants` table — see
  `app/models/tenant.py`), a Vault KV tree (`secret/tenants/<slug>/<service>`),
  and — optionally — a read-only-for-humans MongoDB mirror of that same
  Vault data, one database per tenant.

## Repo layout

```
tenant-operator/
├── app/                              the deployable FastAPI application
│   ├── main.py                       FastAPI app, CORS, mounts Socket.IO, creates DB tables on startup
│   ├── config.py                     all settings (pydantic-settings), env-var driven
│   ├── vault_bootstrap.py            loads this operator's OWN config from Vault, pre-Settings
│   ├── socketio_app.py               Socket.IO transport for tenant status push
│   ├── database.py                   SQLAlchemy engine/session (works against SQLite or Postgres)
│   ├── api/
│   │   ├── tenant.py                 POST/GET/PUT/DELETE /api/v1/tenant + GET .../{id}/vault
│   │   ├── cluster.py                GET /api/v1/cluster[/{name}] -- SpokeCluster CR view
│   │   ├── vault.py                  PUT/GET /api/v1/vault/qraie-bridge-defaults -- shared per-service config
│   │   └── health.py                 GET /health (checks DB connectivity)
│   ├── models/
│   │   ├── tenant.py                 ORM model + TenantStatus enum + STATUS_PROGRESS map
│   │   └── schemas.py                Pydantic request/response models (TenantCreateRequest, etc.)
│   └── services/
│       ├── validation.py             request validation (400s) -- duplicate tenantId, environment sanity
│       ├── cluster_selector.py       sequential-fill spoke placement + capacity accounting
│       ├── spoke_scaler.py           capacity threshold -> new-spoke job (prod) / notify (stage)
│       ├── crossplane_service.py     prod-only: applies/watches a Crossplane OKE claim on the hub
│       ├── notifications.py          notification sink (log-only for now)
│       ├── status_bus.py             in-process pub/sub feeding the Socket.IO status push
│       ├── vault_service.py          qraie-bridge Vault schema: platform defaults + per-tenant secrets
│       ├── mongo_service.py          mirrors the same resolved env config into a per-tenant MongoDB database
│       ├── tenant_cr.py              builds/echoes the per-tenant Tenant CR on the hub (NOT applied for real)
│       ├── spoke_cr.py               builds/echoes the per-spoke SpokeCluster CR on the hub (NOT applied for real)
│       ├── meta_builder_job.py       submits the REAL MSSQL DB/login-seeding Job once a tenant is RUNNING
│       ├── helm_values.py            renders tenants/{slug}.yaml from the Jinja2 template + service allow/deny logic
│       ├── git_service.py            clone/commit/push to the GitOps repo (process-wide lock, single replica)
│       ├── argocd_service.py         read-only Argo CD status polling + failure classification
│       ├── kubernetes_service.py     read-only namespace/deployment/pod/pvc/ingress checks on spokes
│       └── provisioner.py            the async state machine tying all of the above together
├── templates/
│   ├── qraie_bridge_values.yaml.j2              per-tenant Helm values template (the ONLY tenant chart now)
│   └── qraie-bridge-applicationset.example.yaml one-time Argo CD ApplicationSet, applied by hand/CI
├── clusters.yaml                     default/example spoke registry, baked into the image -- see below
├── Dockerfile                        builds the operator image (app/, templates/, clusters.yaml, requirements.txt only)
├── requirements.txt
├── .env.example                      local-dev config template (not fully in sync with app/config.py -- see below)
├── .gitignore / .dockerignore
├── deploy/                           raw Kubernetes manifests to run the operator itself (secondary path -- see below)
│   ├── deployment.yaml                Namespace/Deployment/Service/PVC
│   ├── spoke-readonly-rbac.yaml       baseline ServiceAccount/ClusterRole (every deployment)
│   ├── crossplane-rbac.yaml           Role/RoleBinding for the Crossplane claim CRD (prod only)
│   ├── clusters-configmap.stage.example.yaml  stage's real spoke registry
│   ├── clusters-configmap.prod.example.yaml   prod's real spoke registry (entirely separate spokes)
│   ├── configmap.example.yaml         non-secret config template
│   └── secret.example.yaml            secret config template (fill in, never commit)
└── helm-charts/                      THE deployment Helm chart actually used in production -- see helm-charts/README.md
```

`poc/` (a local no-cluster test harness) and the old generic `workplace`
tenant chart's own values template/ApplicationSet example
(`templates/values.yaml.j2`, `templates/applicationset.example.yaml`) have
been removed as part of this repo's dead-code cleanup — see "The retired
`workplace` chart" below.

## How it's written

- **FastAPI** (`app/main.py`) for the REST API, with **python-socketio**
  mounted alongside it at `/socket.io` (same host/port, no separate
  process) for push-based status updates — see "Live status" below.
- **SQLAlchemy 2.x**, engine URL fully driven by `DATABASE_URL` — works
  against either SQLite (`sqlite:////data/db/tenant_operator.db`, what the
  current hub deployment actually runs — see `helm-charts/README.md`) or
  PostgreSQL (`postgresql+psycopg2://...`, the config default and what
  `requirements.txt`'s `psycopg2-binary` is there for). `Base.metadata.create_all()`
  runs on every startup (`app/main.py`'s `on_startup` hook) — fine for this
  scale of bookkeeping table; swap for real Alembic migrations if the schema
  starts changing under live data (`alembic` is already a pinned dependency,
  unused so far).
- **Pydantic v2 + pydantic-settings** (`app/config.py`) for typed,
  env-var-driven configuration — one `Settings` class, ~50 fields, every one
  overridable by a real environment variable, `.env` file, or (see below) a
  value pulled from Vault at process startup.
- **Jinja2** (`app/services/helm_values.py`) to render each tenant's
  `values.yaml` from `templates/qraie_bridge_values.yaml.j2` — a small
  template; qraie-bridge's own chart `values.yaml` already carries every
  service's image/env/volume defaults, so the per-tenant override only needs
  tenant identity, placement, and which services are disabled.
- **GitPython** (`app/services/git_service.py`) for all writes to the
  separate GitOps repo — clone-once, hard-reset-to-origin before every
  write (never a real merge, since this operator is the only writer),
  commit, push, all behind a process-wide `threading.Lock`.
- **The official `kubernetes` Python client**, used in three distinct,
  differently-scoped ways: read-only health checks against spoke clusters
  (`kubernetes_service.py`), a real `BatchV1Api.create_namespaced_job` call
  against the operator's own (hub) namespace (`meta_builder_job.py`), and a
  real `CustomObjectsApi` call against the hub for Crossplane OKE claims
  (`crossplane_service.py`, prod-only, opt-in).
- **httpx** for read-only Argo CD REST API polling (`argocd_service.py`).
- **hvac** for Vault KV v2 reads/writes (`vault_service.py`,
  `vault_bootstrap.py`) — both optional; the whole app runs with Vault fully
  disabled (the default) for local development.
- **pymongo** for the optional per-tenant env-config mirror
  (`mongo_service.py`) — also disabled by default.
- Non-root container: `Dockerfile` builds on `python:3.12-slim`, creates a
  `tenantop` user (uid 1000; not `operator`, which collides with a system
  group already baked into the base image), and only ever `COPY`s `app/`,
  `templates/`, `clusters.yaml`, and `requirements.txt` — `.dockerignore` is
  a second, redundant guardrail against `poc/`, `deploy/`, and this
  `README.md` ever leaking into the image even if the `Dockerfile` is later
  changed to `COPY . .`.

## Data model / state machine

One row per tenant in the operator's own database (`app/models/tenant.py`).
Key fields:

- `tenant_name` — the caller-supplied `tenantId`, human-chosen, **not**
  DB-unique (a `DELETED` tenant's name is reusable — soft delete via
  `deleted_at`, not a hard delete).
- `tenant_seq` — an operator-allocated sequential integer (`SELECT MAX()+1`
  at creation), combined with `tenant_name` to form `slug` (the
  `@property`, e.g. `acme-corp-42`) — the **one** identifier used for the
  Kubernetes namespace (`tenant-<slug>`), the Vault path prefix, the git
  filename, the Argo CD Application/Helm release name, the Tenant CR name,
  and the meta-builder Job name. `slug` is deliberately never used for
  anything customer-facing (the tenant's own domain, its seeded database's
  display name) — a customer never sees an internal sequence number.
- `app_type` — always `"qraie-bridge"` now (`server_default="qraie-bridge"`);
  kept as a real column only for API/DB back-compat, not settable per
  request any more. See "The retired `workplace` chart".
- `application` / `version` — vestigial NOT NULL columns seeded from
  `settings.default_application`/`default_version` at creation; not read
  anywhere in the qraie-bridge path (every service's image tag comes from
  that chart's own defaults).
- `disabled_services` — a JSON list of qraie-bridge service names this
  tenant does **not** get, computed from the request's `services`/
  `onlyListedServices` fields (see "Selecting which services a tenant
  gets" below) and rendered into the tenant's `values.yaml` as
  `disabledServices:`.
- `status` — a `TenantStatus` enum driving the whole workflow:

  ```
  PENDING -> VALIDATING -> GIT_COMMITTED -> SYNCING -> RUNNING
  any step -> FAILED (error_message set)
  RUNNING -> DELETING -> DELETED
  RUNNING -> UPDATING -> SYNCING -> RUNNING
  ```

  `STATUS_PROGRESS` maps each status to a rough 0–100 heuristic used by both
  the plain `GET` response's `progress` field and every status-push event
  (not meant to be precise — `SYNCING` alone can take anywhere from seconds
  to the full provisioning timeout).

## API

```
POST   /api/v1/tenant            -> 202 Accepted, { tenantId, status: PENDING }
GET    /api/v1/tenant/{id}       -> current tenant record incl. status + progress
GET    /api/v1/tenant            -> list, filterable by ?environment=&status_filter=
PUT    /api/v1/tenant/{id}       -> update version/users/db size/services (only when RUNNING/FAILED)
DELETE /api/v1/tenant/{id}       -> 202 Accepted, tears down via git delete + Argo CD prune + namespace delete
GET    /api/v1/tenant/{id}/vault -> this tenant's current Vault secrets (one path per qraie-bridge service)
GET    /api/v1/cluster           -> SpokeCluster CR content for every registered spoke
GET    /api/v1/cluster/{name}    -> SpokeCluster CR content for one spoke
GET    /api/v1/vault/qraie-bridge-defaults           -> current shared config for every qraie-bridge service
PUT    /api/v1/vault/qraie-bridge-defaults/{service} -> overwrite one qraie-bridge service's shared config
GET    /health                   -> liveness/readiness (checks DB connectivity via `SELECT 1`)
/socket.io                       -> Socket.IO status push (see "Live status" below)
```

There is **no** `GET /api/v1/tenant/{id}/events` SSE endpoint any more —
some in-repo comments are stale on this point (leftover from before the
Socket.IO migration); Socket.IO at `/socket.io` is the only push transport
today.

### Creating a tenant

```json
POST /api/v1/tenant
{
  "tenantId": "acme",
  "displayName": "Acme Corporation",
  "email": "admin@acme.com",
  "password": "StrongPassword123",
  "domain": "acme.example.com",
  "services": {"voxflow": false, "mcp-server": false},
  "onlyListedServices": false
}
```

Notes on the request shape (`app/models/schemas.py::TenantCreateRequest`):

- **No `appType`, `application`, or `version` fields exist any more.**
  Every tenant is provisioned onto qraie-bridge; there is nothing left to
  select. (See "The retired `workplace` chart".)
- **No `environment` field** — which environment (`prod`/`stage`) a tenant
  lands in is a deployment-time fact (`ENVIRONMENT` env var on *this*
  operator instance), not a per-request choice. One operator
  Deployment/release == one environment.
- `tenantId` becomes a Kubernetes namespace suffix — lowercase alphanumeric
  with optional hyphens (RFC-1123 label), max 50 chars (so
  `tenant-<tenantId>-<seq>` stays under Kubernetes' 63-char namespace cap).
- `services` (`dict[str, bool]`) + `onlyListedServices` (`bool`) control
  which of qraie-bridge's ~32 services this tenant gets — see next section.
- `password` is used only in-memory, to pass an admin credential through to
  the meta-builder DB-seeding Job trigger later in the flow. **It is never
  persisted** to the tenant row.

### Selecting which services a tenant gets

Two modes, both validated against the fixed set of ~32 known service names
(`helm_values.QRAIE_BRIDGE_SERVICE_NAMES` — an unknown name in `services`
is a 400, not a silent no-op):

- **Opt-out (default, `onlyListedServices=false`)** — `services` is sparse;
  only entries set to `false` do anything. `{"voxflow": false}` disables
  just voxflow; everything else stays enabled.
- **Opt-in / allowlist (`onlyListedServices=true`)** — every service **not**
  explicitly set to `true` is disabled. `{"services": {"bridge": true,
  "qraie-redis-shared": true}, "onlyListedServices": true}` runs only those
  two, without spelling out the other ~30 as `false`. Setting
  `onlyListedServices=true` with no service set to `true` is rejected (it
  would disable every service).

`helm_values.compute_disabled_services()` turns this into the flat list
rendered as the chart's `disabledServices:` key — **never** as a `services:`
override, because Helm replaces an entire list wholesale rather than
merging it; a per-tenant `services:` override would have to repeat the
chart's whole ~32-entry array just to skip a couple.

### Polling and live status

Poll `GET /api/v1/tenant/{id}` for status transitions — every response
includes a `progress` field (0–100) even without using the push transport.
For a live loading bar without polling, use Socket.IO
(`app/socketio_app.py`, mounted at `/socket.io`):

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
per subsequent transition, and stops once a terminal status
(`RUNNING`/`FAILED`/`DELETED`) is reached — the server-side subscription
(`status_bus.py`'s in-process pub/sub) is cleaned up at that point, or on
client disconnect, whichever comes first. This is **single-replica, in-memory
only** — see "Known limitations".

## The provisioning workflow (`app/services/provisioner.py`)

Runs as a plain FastAPI `BackgroundTask` today (swap-in point for a real
task queue is called out below). For a fresh `POST /api/v1/tenant`:

1. **VALIDATING** — `validation.py` rejects a duplicate active `tenantId`
   and confirms this deployment's own `ENVIRONMENT` is in
   `allowed_environments`.
2. **Cluster selection** — `cluster_selector.select_cluster()` picks a
   spoke via **sequential fill**: clusters are tried in the order they
   appear in `clusters.yaml`/`clustersConfig`, and a tenant lands on the
   first one not yet at capacity for its environment — spoke-1 fills
   completely before spoke-2 ever gets a tenant. Live counts come from this
   operator's own database (`ACTIVE_STATUSES` — everything except
   `DELETED`/`FAILED`), not from Kubernetes.
3. **Capacity check** (`spoke_scaler.maybe_trigger_spoke_scale`) — fires a
   parallel, non-blocking new-spoke job in prod once a spoke crosses its
   scale-out threshold, or a notification in stage (no autoscaling there).
4. **SpokeCluster CR upsert** (`spoke_cr.py`) — echoed/logged, not applied
   for real (see "Known limitations").
5. **Tenant CR creation** (`tenant_cr.py`) — echoed/logged, not applied for
   real either; returns a synthetic `(name, uid)` pair the way a real API
   server would.
6. **Vault secrets written** (`vault_service.write_initial_qraie_bridge_tenant_secrets`)
   — deliberately **before** the GitOps handoff, not after `RUNNING`: the
   tenant workload's own Vault Agent/VSO sidecar blocks its pod from
   starting until these paths exist, so writing them late would be a
   chicken-and-egg deadlock. The same resolved data is mirrored into
   MongoDB right after (`mongo_service.write_tenant_env_config`).
7. **Render + commit `values.yaml`** (`helm_values.py` + `git_service.py`)
   → **GIT_COMMITTED**.
8. **SYNCING** — poll Argo CD + Kubernetes until healthy or timeout (see
   "Argo CD failure monitoring" below) → **RUNNING** or **FAILED**.
9. **Meta-builder Job** — only on reaching `RUNNING`: submits the real
   MSSQL DB/login-seeding Job (see below).

`PUT /api/v1/tenant/{id}` (only accepted from `RUNNING`/`FAILED`) re-renders
and re-commits `values.yaml` with the updated version/users/db size/service
selection, then re-runs the same Argo CD/Kubernetes wait loop.
`DELETE /api/v1/tenant/{id}` removes the tenant's manifest from git, deletes
its namespace directly (the one deliberate write exception — see below),
and polls until the namespace is gone.

## Argo CD failure monitoring

Scoped entirely to **a tenant's own rollout**
(`provisioner._wait_for_argocd_and_k8s`, runs for both onboarding and
`PUT` updates) — tenants are **not** monitored after reaching `RUNNING`.
Built on `argocd_service.classify()`, which fails fast instead of silently
retrying for the full `PROVISIONING_TIMEOUT_SECONDS` (default 900s):

- Argo CD reports a **definitive failure** (the last sync operation itself
  `Failed`/`Error`) → straight to `FAILED` with Argo's own message, on the
  very next poll. Note: **Degraded health alone is *not* treated as
  definitive** — Argo CD commonly reports a resource as transiently
  Degraded while an already-succeeded sync is still settling (e.g. an
  `ExternalSecret` not yet caught up); only a failed/errored *operation*
  short-circuits the wait.
- Argo CD itself is **unreachable** (network error, 5xx, timeout) for
  `ARGOCD_UNREACHABLE_FAIL_AFTER` (default 5) consecutive polls → `FAILED`,
  with a notification firing on the *first* unreachable poll so an operator
  finds out immediately rather than only once the tenant gives up.
- Anything else (Progressing, Suspended, a transient blip, or Argo reporting
  Healthy+Synced while this operator's own `kubernetes_service.is_fully_ready`
  check still disagrees — e.g. an ingress LB not provisioned yet) → keeps
  polling.

## qraie-bridge Vault integration

`app/services/vault_service.py` writes one Vault KV v2 path per chart
service: `secret/tenants/<tenant-slug>/<service>` (plus one extra
tenant-wide `secret/tenants/<tenant-slug>/common` path for shared
Redis/gateway config), consumed by the qraie-bridge chart's per-service
`envFrom`. `QRAIE_BRIDGE_SERVICE_KEYS` (in code, not just ad-hoc `vault kv
put` commands) lists, per service, exactly which env keys its Vault path
holds — currently ~32 services' worth, each expanded and cross-checked
against the original docker-compose stack this chart was converted from.
**Read that dict directly for the authoritative, current key list per
service** — it's too large and too likely to drift to usefully duplicate
here; below is the mechanism, not the full schema.

Every key falls into exactly one of three buckets when a new tenant's
secrets are written (`write_initial_qraie_bridge_tenant_secrets`):

1. **`QRAIE_BRIDGE_TENANT_DERIVED_KEYS`** (`TENANT_ID`, `TENANT_KEY`,
   `TENANT_IDS`, `DB_USER`, `DB_NAME`, `DB_SCHEMA`) — set to the tenant's
   own `slug`. Must be unique per tenant: a shared value here would mean
   every tenant's seeding Job operates on the *same* database/login,
   silently colliding.
2. **`QRAIE_BRIDGE_GENERATED_KEYS`** (`REDIS_PASSWORD`, `DB_PASSWORD`,
   `WORKPLACE_DM_PASSWORD`, `RADICALE_AGENT_PASS`, `JWT_SECRET`,
   `G_JWT_SECRETKEY`, `G_RT_SECRETKEY`) — a fresh, random,
   complexity-guaranteed value generated per tenant
   (`_generate_secret()` guarantees at least one upper/lower/digit/symbol
   character, since a uniform draw can otherwise land on a password MSSQL's
   default complexity policy rejects). *`WORKPLACE_DM_*` here is an env key
   name inherited from the `bridge-cp-conductor` service's own third-party
   integration naming — it has nothing to do with the retired
   `appType="workplace"` tenant chart discussed below; it just happens to
   share the word.*
3. **Two keys with their own dedicated derivation, not a platform default**:
   - `VIRTUAL_HOST` → the tenant's own `domain` from the request, verbatim.
     Safe to derive automatically (unlike every other self-referencing URL
     key) because it never has a service-specific path suffix.
   - `MONGODB_URI` → built from `mongo_env_config_uri` (the same shared
     Mongo instance credentials `mongo_service.py` already uses) with the
     tenant's own `slug` inserted as the database-name path segment
     (`vault_service._tenant_mongodb_uri()`). This gives every service that
     needs Mongo (`controlops-server`, `erep-server`, `tranops-backend`,
     `iot-broker-config`, `iot-broker-data`) that tenant's own isolated
     database rather than a value from platform defaults — the source
     reference data this schema was built from showed some services
     sharing one fixed database name across every tenant, which defeats
     tenant isolation, so this was deliberately changed to derive per
     tenant instead.
4. **Everything else** — copied from that service's current
   `secret/qraie-bridge/platform-defaults/<service>` entry (or `""` if
   never configured). This is genuinely shared business/integration config
   (external API URLs/credentials, hosts, flags) — the same for every
   tenant in *this* environment, set once via:

   ```
   PUT /api/v1/vault/qraie-bridge-defaults/tranops-backend
   {"SLM_API_URL": "...", "SLM_PASSWORD": "..."}
   ```

   This only affects tenants provisioned **after** the call — an existing
   tenant's `secret/tenants/<slug>/<service>` was written once at its own
   creation and is not kept in sync with later platform-default changes.

Inspect what's currently stored for one tenant via
`GET /api/v1/tenant/{id}/vault`. With `VAULT_ENABLED=false` (the default),
every write above still runs, just logged/echoed instead of actually
reaching a Vault server — this lets the whole onboarding flow be exercised
locally with zero Vault setup.

**Local/dev Vault**: Vault's own `--dev` mode (in-memory, auto-unsealed,
fixed root token `"root"`) is enough to test this against a real Vault
server — point `VAULT_ADDR` at it and `VAULT_TOKEN=root`. Not for anything
resembling production (no persistence, no real auth).

## MongoDB env-config mirror (`app/services/mongo_service.py`)

A **read-only-for-humans mirror**, not a source of truth for anything — the
qraie-bridge chart's own `envFrom` (reading Vault) is what pods actually
consume at runtime. Disabled by default (`mongo_env_config_enabled=false`);
when enabled, `write_tenant_env_config()` mirrors the *exact same* resolved
env data `vault_service.write_initial_qraie_bridge_tenant_secrets()` just
wrote (passed straight through, never re-derived or re-classified) into:

- **One MongoDB database per tenant, named after the tenant's `slug`**
  (e.g. `hbss-14`) — shared by every service belonging to that tenant, not
  one database per service.
- **One collection per "service group"** within that database
  (`QRAIE_BRIDGE_SERVICE_GROUPS` — mirrors the chart's own ingress-path
  prefixes where a service has one, e.g. `controlops`, `galaxy`,
  `workplace` — this `workplace` is a service-group label for the
  `qraie-api-gateway`/`qraie-ui`/`admin-panel`/`microservice-qraie` family,
  also unrelated to the retired tenant chart of the same name — or grouped
  by service-name family for internal services with no ingress path of
  their own).
- **One document per service**, `_id` = service name, `replace_one(...,
  upsert=True)` so a later tenant update replaces rather than duplicates
  that service's document.

## The retired `workplace` chart

Earlier, tenants could be provisioned onto **either** qraie-bridge, or a
second, generic single-image "workplace" chart (`poc/chart-workplace`),
selected via an `appType` request field, with `application`/`version`
request fields mapping to that chart's `image.repository`/`image.tag`, and
its own generic `redis`/`database`/`mongo`/`jwt` Vault shape
(`PUT`/`GET /api/v1/vault/common`). That chart was only ever a demo/POC —
never used for a real tenant — and has been fully retired as of this
repo's dead-code cleanup:

- `appType`, `application`, and `version` no longer exist as
  `TenantCreateRequest` fields — every tenant is qraie-bridge, full stop.
- `poc/` (the entire local no-cluster test harness, including
  `poc/chart-workplace/`) has been deleted from the working tree.
- `templates/values.yaml.j2` (the generic chart's values template) and
  `templates/applicationset.example.yaml` (its ApplicationSet) have been
  deleted — only `templates/qraie_bridge_values.yaml.j2` and
  `templates/qraie-bridge-applicationset.example.yaml` remain.
- `Tenant.app_type`/`.application`/`.version` remain as real, `NOT NULL`
  DB columns (dropping them would need a migration this codebase doesn't
  have yet) — always `"qraie-bridge"`/`settings.default_application`/
  `settings.default_version` now, and still surfaced on `TenantResponse`
  for API back-compat.
- A stale reference survives in
  `templates/qraie-bridge-applicationset.example.yaml`'s own comments
  (mentions "the two appTypes'" and a counterpart
  `templates/applicationset.example.yaml` that no longer exists) — cosmetic
  only, doesn't affect behavior, but worth cleaning up next time that file
  is touched.
- `helm-charts/templates/NOTES.txt`'s example `curl` still includes
  `"appType": "qraie-bridge"` in its sample request body — also stale;
  `TenantCreateRequest` will simply ignore that unknown field today (Pydantic
  models here don't reject extra fields by default), but it should be
  removed from the example.

## Configuration reference

Full source of truth: `app/config.py`'s `Settings` class (~50 fields,
grouped and commented by subsystem). `.env.example` is a **local-dev
template and is not fully in sync with it** — e.g. it references
`META_BUILDER_JOB_IMAGE`, which is not a real `Settings` field any more
(the seeding Job's image is now hardcoded to
`mcr.microsoft.com/mssql/server:2022-latest` in `meta_builder_job.py`, not
configurable), and it doesn't mention `MSSQL_ADMIN_HOST`/`MSSQL_ADMIN_PORT`/
`MSSQL_ADMIN_USER`/`MSSQL_ADMIN_PASSWORD` or the `MONGO_ENV_CONFIG_*` keys
at all. Treat `app/config.py` as authoritative; `.env.example` as a rough
starting point.

Selected settings worth knowing about explicitly:

| Setting | Default | Notes |
|---|---|---|
| `ENVIRONMENT` | `stage` | Which environment *this* deployment serves — drives spoke capacity thresholds. One deployment = one environment. |
| `DATABASE_URL` | `postgresql+psycopg2://...` | Also works as `sqlite:////data/db/tenant_operator.db` — what the current hub deployment actually runs (see `helm-charts/README.md`). |
| `VAULT_ENABLED` | `false` | Gates every real Vault write in `vault_service.py`; `false` means log/echo only. |
| `MONGO_ENV_CONFIG_ENABLED` | `false` | Gates the read-only MongoDB mirror in `mongo_service.py`. |
| `MONGO_ENV_CONFIG_URI` | unset | Full Mongo connection string, also the base for each tenant's derived `MONGODB_URI` (see above). |
| `GIT_REPO_URL` / `GIT_BRANCH` / `GIT_TENANTS_DIR` | — | The GitOps repo and directory tenant manifests are committed into. |
| `GIT_BRIDGE_TENANTS_DIR` | `bridge-tenants` | **Currently unused by any live code path** — `_tenants_dir_for()` in `provisioner.py` always returns `GIT_TENANTS_DIR` now that there's only one chart/one tenant flow. Kept because `git_service.py`'s functions already accept a `tenants_dir` override and nothing currently calls them with it; harmless to leave set. |
| `MSSQL_ADMIN_HOST` / `..._PORT` / `..._USER` / `..._PASSWORD` | unset | Admin login the meta-builder Job uses to create each tenant's real MSSQL database + login. Both host and password must be set for the Job to actually run — otherwise it's skipped (logged, not failed). |
| `ARGOCD_UNREACHABLE_FAIL_AFTER` | `5` | Consecutive unreachable polls before a rollout fails fast — see "Argo CD failure monitoring". |
| `CROSSPLANE_ENABLED` | `false` | Prod-only; gates whether `spoke_scaler.py` applies a real Crossplane claim vs. a log-only simulation. |
| `KUBECONFIG_PATH` | unset (in-cluster) | kubeconfig with one context per spoke, used **read-only** against every spoke. |
| `HUB_KUBECONFIG_PATH` / `HUB_KUBE_CONTEXT` | unset (in-cluster) | Only for local/dev access to the hub from outside it — the real hub deployment leaves these unset since the operator already runs *on* the hub. |

No `ARGOCD_APPSET_NAME` setting exists — it was found to be fully unused
dead code and removed from `Settings` as part of this cleanup; if you see
it referenced anywhere else, that reference is stale.

## Local development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in real values (see caveats above)
uvicorn app.main:app --reload
```

With `VAULT_ENABLED=false`, `MONGO_ENV_CONFIG_ENABLED=false`,
`CROSSPLANE_ENABLED=false` (all defaults) and `DATABASE_URL` pointed at a
local SQLite file, the entire onboarding flow runs end-to-end with nothing
external except a reachable git remote and (if you want the Argo CD wait
loop to resolve) a reachable Argo CD/Kubernetes spoke. Every optional
integration echoes/logs what it would have done instead of failing outright
when disabled.

`Base.metadata.create_all()` runs on startup for convenience. For anything
beyond local dev, switch to Alembic migrations (already a pinned
dependency, not yet wired up):

```bash
pip install alembic
alembic init migrations
# point migrations/env.py at app.database.Base.metadata and DATABASE_URL,
# then: alembic revision --autogenerate -m "init" && alembic upgrade head
```

## Deploying the operator itself

Two paths exist in this repo; **the Helm chart under `helm-charts/` is the
primary, currently-used one** — it's what's actually deployed as the Argo CD
`Application` named `tenant-operator` on the hub cluster. Full instructions,
image versioning, and the live-deployment operational notes (including a
real gotcha around `existingSecretName` and Secret patches) are in
**`helm-charts/README.md`** — read that for the real deployment story.

`deploy/*.yaml` is a secondary, plain-manifest path — the same
Namespace/Deployment/Service/PVC/RBAC objects the Helm chart also renders,
written out by hand instead of templated. It's kept as a manual/reference
alternative for anyone who'd rather not use Helm, but it is **not** what's
currently running, and it has drifted slightly from the current code —
e.g. `deploy/configmap.example.yaml`'s `META_BUILDER_JOB_IMAGE` key isn't
consumed by any current `Settings` field (see "Configuration reference"
above). If you use `deploy/` for a real deployment, double-check its
ConfigMap/Secret keys against `app/config.py` first.

```bash
docker build -t your-registry/tenant-operator:latest .
docker push your-registry/tenant-operator:latest

cp deploy/configmap.example.yaml deploy/tenant-operator-config.yaml   # fill in, don't commit
cp deploy/secret.example.yaml deploy/tenant-operator-secrets.secret.yaml
cp deploy/clusters-configmap.stage.example.yaml deploy/tenant-operator-clusters.yaml   # or the prod one

kubectl apply -f deploy/tenant-operator-config.yaml
kubectl apply -f deploy/tenant-operator-secrets.secret.yaml
kubectl apply -f deploy/tenant-operator-clusters.yaml
kubectl apply -f deploy/spoke-readonly-rbac.yaml
kubectl apply -f deploy/crossplane-rbac.yaml   # prod only
kubectl apply -f deploy/deployment.yaml
```

You'll also need to separately create `tenant-operator-git-ssh-key` and
`tenant-operator-kubeconfig` Secrets — not templated here since their
contents are environment-specific credentials.

## One-time platform setup (before the operator's first request)

1. **Git repo**: a separate GitOps repo (e.g. `k8s-infra-setup-testing`)
   with a `tenants/` (or your configured `GIT_TENANTS_DIR`) directory and a
   copy of the qraie-bridge chart under `charts/qraie-bridge`.
2. **ApplicationSet**: apply
   `templates/qraie-bridge-applicationset.example.yaml` to Argo CD once, by
   hand or via platform CI — its git-directory generator creates/removes
   the per-tenant Argo CD `Application` automatically whenever this
   operator commits or deletes a file.
3. **Register spoke clusters with Argo CD** (`argocd cluster add <context>`)
   and list them in the environment's clusters registry (name/context/
   region/environments/max_tenants — see `clusters.yaml` and
   `deploy/clusters-configmap.*.example.yaml`). **Stage and prod must never
   share a spoke.**
4. **Argo CD API token** — read-only access to Application status is
   sufficient.
5. **Kubeconfig** with a context per spoke, scoped to **read-only** RBAC
   (Deployments/Pods/PVCs/Ingresses/Namespaces) — the operator never writes
   to a spoke directly.
6. **Git deploy key** (SSH) or PAT (HTTPS) with write access to the GitOps
   repo.
7. **Database**: a Postgres database + user, or a persistent volume for a
   SQLite file — either way, wired via `DATABASE_URL`.
8. **MSSQL admin login** (optional but needed for real DB seeding): set
   `MSSQL_ADMIN_HOST`/`..._PORT`/`..._USER`/`..._PASSWORD` and make sure the
   operator's ServiceAccount can create `batch/v1` Jobs in its own
   namespace (`meta_builder_job_namespace`).
9. **Crossplane** (prod only, optional): install Crossplane + an OKE
   provider/Composition on the hub, matching the shape
   `crossplane_service.build_oke_claim()` sends, then set
   `CROSSPLANE_ENABLED=true`.
10. **Vault** (optional): a token (or Agent-injected token file) with write
    access under `VAULT_KV_MOUNT`/`VAULT_TENANT_SECRET_PREFIX`, then
    `VAULT_ENABLED=true`. Set the shared qraie-bridge platform defaults via
    `PUT /api/v1/vault/qraie-bridge-defaults/{service}` before onboarding
    tenants that need real values there.

## Known limitations (read this before relying on anything below in prod)

- **Single replica by design.** `git_service.py`'s write lock and
  `status_bus.py`/Socket.IO's pub/sub are both plain in-process state —
  correct for exactly one pod. `BackgroundTasks` also only runs in the
  process that received the HTTP request, so there's no crash recovery: if
  the pod restarts mid-provision, that tenant is stuck in whatever status
  it was last in. `provisioner.py`'s functions are already plain functions,
  so wrapping them as Celery tasks (with the git lock moved to a Postgres
  advisory lock, and the status bus to Redis pub/sub) is the natural next
  step, not a rewrite.
- **Tenant CR and SpokeCluster CR are intent records, not real CRDs —
  still true.** `tenant_cr.py` and `spoke_cr.py` build the objects and
  `logger.info`/`print()` the equivalent `kubectl apply` rather than
  calling the Kubernetes API. No CRDs are installed anywhere for this.
- **The meta-builder Job is REAL now — this is a correction to older
  documentation.** `meta_builder_job.py` submits an actual
  `BatchV1Api.create_namespaced_job` call (in-cluster config, since the
  operator runs on the hub where the Job also runs) once a tenant reaches
  `RUNNING`, *if* `MSSQL_ADMIN_HOST`/`MSSQL_ADMIN_PASSWORD` are configured
  and the tenant has a `controlops-server` Vault secret with a `DB_NAME`
  (the one qraie-bridge service whose schema models a real DB connection).
  It reuses the platform's own `mcr.microsoft.com/mssql/server` image
  purely for its bundled `sqlcmd` — no separate image is built. If either
  precondition is missing, it's skipped and logged, not treated as a
  failure (a missing DB integration shouldn't fail an otherwise-successful
  onboarding).
- **Vault write is secrets-only for most services; MSSQL is the one
  exception with real account provisioning.** `vault_service.py` generates
  and writes credentials for every qraie-bridge service, but for most of
  them (Redis passwords, JWT secrets, etc.) nothing in this codebase
  creates the underlying account those credentials describe — the target
  infra must already accept whatever lands in Vault, or you provision those
  accounts separately. MSSQL is the exception: the meta-builder Job above
  does create the real database + login matching `DB_NAME`/`DB_USER`/
  `DB_PASSWORD`, closing that gap for the one service it's wired up for.
- **New-spoke provisioning: Crossplane for prod, notify-only for stage** —
  and even the Crossplane path stops at "cluster exists and is Ready";
  registering it with Argo CD and adding it to the clusters registry are
  still separate, deliberate steps, logged clearly when the claim goes
  Ready.
- **Placement is sequential-fill, not load-balanced** — spoke-1 always
  fills before spoke-2 receives a tenant. This is what makes a scale-out
  threshold a meaningful, single-spoke event.
- **RBAC**: the kubeconfig used against each *spoke* should be read-only —
  writes there only ever happen via Argo CD reading git. The *hub* is the
  exception, with two narrowly-scoped real write paths: `batch/v1` Jobs in
  the operator's own namespace (meta-builder), and one Crossplane claim CRD
  in one namespace (prod only). The one write against a *spoke* directly is
  namespace `delete` — Argo CD's `CreateNamespace=true` creates a tenant's
  namespace but never tracks/prunes it, so `delete_tenant()` deletes it
  directly or it would stay `Active` forever.
- **Namespace-per-tenant assumes single-tenant-per-namespace charts**
  labelled `app.kubernetes.io/instance=<tenant.slug>` — adjust the label
  selector in `kubernetes_service.py` if a chart labels differently.
