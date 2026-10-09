"""
Central configuration for the Tenant Operator.

Everything is driven by environment variables so the same image can be
promoted across environments without rebuilding. See `.env.example` for
the full list of knobs.
"""
from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.vault_bootstrap import load_env_from_vault

# Must run before Settings() is ever constructed -- fills any config key not
# already set via a real env var/.env from Vault. No-ops entirely if
# VAULT_ADDR isn't set, so this is a pure no-op for local/no-cluster testing.
load_env_from_vault()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- API ---
    app_name: str = "tenant-operator"
    api_prefix: str = "/api/v1"
    log_level: str = "INFO"

    # CORS origins allowed to call this API / consume the SSE status stream
    # from a browser. "*" is fine for local dev/POC; set to your real
    # frontend origin(s) for a real deployment.
    cors_allow_origins: list[str] = ["*"]

    # --- Admin login for the built-in web UI (/ui) and the whole API ---
    # ADMIN_PASSWORD (keep it in the tenant-operator-secrets Secret) and/or
    # API_KEY must be set: every route except /health, /auth/login and the /ui page
    # needs the bearer token from POST /auth/login, and with no password set
    # they all return 503 (fail closed).
    admin_username: str = "admin"
    admin_password: Optional[str] = None
    admin_token_secret: Optional[str] = None   # defaults to admin_password
    # Optional static key for scripts/curl: send `X-API-Key: <value>` instead
    # of logging in. Grants the same access as the admin token.
    api_key: Optional[str] = None
    admin_token_ttl_seconds: int = 8 * 3600

    # --- Which environment THIS operator deployment serves ---
    # One operator deployment == one environment. Set via env var
    # `environment=prod` or `environment=stage` (case-insensitive).
    # Drives per-spoke capacity limits and the scale-out/notify thresholds below.
    environment: str = "stage"

    # --- Spoke capacity planning (tenants per spoke, by environment) ---
    prod_spoke_capacity: int = 1000
    prod_spoke_scale_trigger: int = 995          # kick off a new spoke at this count
    stage_spoke_capacity: int = 3
    stage_spoke_scale_trigger: int = 2           # stage has no autoscaling -> notify instead

    # --- Tenant CR (created on the hub cluster before GitOps takes over) ---
    hub_namespace: str = "platform-mgmt"
    cr_api_version: str = "platform.saas.com/v1alpha1"
    cr_kind: str = "Tenant"
    # Vestigial: every tenant is provisioned onto the qraie-bridge chart,
    # which doesn't take a per-tenant application/version (every service's
    # image tag comes from that chart's own defaults) -- these two just seed
    # the Tenant.application/version NOT NULL DB columns left over from the
    # retired "workplace" chart (see that column's docstring), no longer
    # settable per-request.
    default_application: str = "qraie-bridge"
    default_version: str = "1.0.0"
    default_tier: str = "standard"

    # --- Meta-builder Job (DB seeding, triggered post-provisioning) ---
    # Runs in tenant-operator's own namespace on the hub -- same cluster
    # tenant-operator itself runs on and the MSSQL server it talks to lives
    # on -- so no extra namespace/cross-namespace RBAC is needed.
    meta_builder_job_namespace: str = "tenant-operator"
    # Admin login the Job uses to actually create each tenant's database +
    # login on the real DB server -- the tenant's own DB_HOST/USER/PASSWORD
    # (generated per-tenant into Vault) can't be used for its own creation
    # since that login doesn't exist yet. Only meaningful when a real DB
    # server is configured -- empty/unset means the Job can't run for real.
    mssql_admin_host: Optional[str] = None
    mssql_admin_port: int = 1433
    mssql_admin_user: str = "sa"
    mssql_admin_password: Optional[str] = None

    # --- Bridge meta-builder Job (real schema + SQL default-data inserts +
    # full Mongo seed -- bridgeMetaInfo/auth/users/wfm_*/controlops_actors/
    # iot_*, ported from HBSS's legacy 02_sql_mongo.sh + meta-builder/
    # Node.js scripts, see the meta-builder/ directory at this repo's root
    # for the reference implementation this was adapted from) -- separate
    # from meta_builder_job.py above, which only creates the empty
    # database + login. Triggered at the same point in provisioner.py.
    bridge_meta_builder_job_namespace: str = "tenant-operator"
    bridge_meta_builder_image_repository: str = "sandeepkosework/bridge-meta-builder"
    bridge_meta_builder_image_tag: str = "0.1.11"
    # Base public domain every tenant's hostname is built under (the
    # legacy system's own convention: "<tenant_name>-bridge<stg-suffix>.
    # <this>", e.g. "hbss-bridgestg.qraie.ai" -- see bridge_meta_builder_job.py).
    bridge_base_domain: str = "qraie.ai"
    # Shared platform secrets for the bridgeMetaInfo.dlmObj block this Job
    # seeds -- same for every tenant (an LLM/RAG integration config, not
    # tenant-specific), so plain Settings fields rather than a per-tenant
    # Vault write. Set via env var/Vault like every other secret Setting
    # in this file -- never hardcoded here.
    bridge_dlm_secret_key: Optional[str] = None
    bridge_slm_kb_auth_password: Optional[str] = None

    # --- Vault ---
    # Two separate uses:
    #  1. app.vault_bootstrap.load_env_from_vault() reads VAULT_* directly
    #     from os.environ (before Settings exists) to pull this operator's
    #     OWN config from Vault -- see that module, not these fields.
    #  2. vault_service.py uses these Settings fields at runtime to write
    #     each new tenant's initial secrets (see below) -- disabled by
    #     default so local/no-cluster testing doesn't need a real Vault.
    vault_enabled: bool = False
    vault_addr: Optional[str] = None
    vault_token: Optional[str] = None
    vault_token_file: Optional[str] = None          # e.g. written by a Vault Agent sidecar
    vault_kv_mount: str = "secret"
    vault_config_path: str = "tenant-operator/config"   # where THIS operator's own config lives, if any
    # Where each tenant's secrets live: secret/{prefix}/{slug}/<service> plus
    # secret/{prefix}/{slug}/service-common. helm-chart-bridge's three-layer
    # secrets read secret/k8s/<tenant.id>/..., so this must be "k8s" for the
    # chart and operator to agree (it was "tenants" for the older chart).
    vault_tenant_secret_prefix: str = "k8s"

    # --- MongoDB env-config mirror (qraie-bridge only) ---
    # This is mongo_service.py writing every qraie-bridge tenant's actual resolved
    # env config (same data just written to Vault) into that tenant's own
    # Mongo database, one collection per service group, one document per
    # service. Disabled by default for the same reason vault_enabled is --
    # local/no-cluster testing shouldn't need a real Mongo either.
    mongo_env_config_enabled: bool = False
    mongo_env_config_uri: Optional[str] = None   # full connection string, e.g. mongodb://user:pass@host:27017/?authSource=admin
    # Optional: collapse the per-tenant mirror above into ONE shared database.
    # Unset (default) keeps one database per tenant, named after its slug. When
    # set (e.g. "tenant-env-config"), every tenant's documents go into a single
    # collection `tenant_env_config` of that database instead, _id "<slug>/<service>".
    mongo_env_config_shared_db: Optional[str] = None

    # --- Default eRep ("Noah") creation after a tenant is RUNNING ---
    # Equivalent of infra-runner's step 07. On by default; set false to skip. See
    # services/erep_setup.py for why this calls the tenant's PUBLIC URL.
    erep_setup_enabled: bool = True
    # Placeholders: {domain} (tenant.domain, the ingress host), {tenant}, {slug}.
    erep_setup_url_template: str = "https://{domain}/galaxy/erepapi/api/ereps"
    erep_setup_verify_tls: bool = True
    erep_setup_attempts: int = 12            # x retry delay = how long to wait for the URL to come up
    erep_setup_retry_delay_seconds: int = 10
    erep_setup_timeout_seconds: int = 30
    # False: a failed eRep call is logged and the tenant stays RUNNING.
    # True: it marks the tenant FAILED (infra-runner failed the whole run).
    erep_setup_required: bool = False

    # --- Shared tenant registry (database `qraieai`) ---
    # One small record per tenant in a database shared by ALL tenants, kept
    # current as the tenant's status changes (and marked DELETED, not removed,
    # on delete). Mirrors infra-runner, which wrote one record per tenant into
    # the same database (its bridge_port_allocations collection). Uses the same
    # server/credentials as the mirror (mongo_env_config_uri). Best-effort: a
    # failure here is logged and never fails the tenant. Off by default.
    qraieai_registry_enabled: bool = False
    qraieai_db_name: str = "qraieai"
    # NOT infra-runner's bridge_port_allocations -- that one holds VM port
    # ranges (startPort/endPort) a legacy allocator may read; Kubernetes
    # tenants have no ports to allocate, so they get their own collection.
    qraieai_registry_collection: str = "bridge_tenants"

    # --- Crossplane (prod-only new-spoke OKE provisioning) ---
    # When a prod spoke crosses its scale-out threshold, the operator applies
    # a Crossplane claim on the HUB cluster requesting a new OKE cluster,
    # instead of the log-only simulation used everywhere else in this POC.
    # Disabled by default for local/no-cluster testing (see
    # poc/.env.nocluster.example) so poc/run_local_no_cluster.py keeps
    # working without a real hub cluster or Crossplane installed.
    crossplane_enabled: bool = False
    crossplane_api_version: str = "oke.platform.example.com/v1alpha1"
    crossplane_kind: str = "OKEClusterClaim"
    crossplane_plural: str = "okeclusterclaims"      # CRD plural; Crossplane doesn't let us derive this reliably
    crossplane_claim_namespace: str = "platform-infra"
    crossplane_composition_ref: Optional[str] = None  # only set if your XRD requires pinning a Composition
    crossplane_default_region: str = "us-ashburn-1"
    crossplane_default_node_count: int = 3
    crossplane_provision_timeout_seconds: int = 1800  # OKE cluster creation is slow -- 30 min ceiling
    crossplane_poll_interval_seconds: int = 15

    # --- Hub cluster access (for the Tenant/SpokeCluster CRs and Crossplane
    # claims, all of which live on the hub, not a spoke) ---
    # None => in-cluster config, correct when the operator runs on the hub
    # itself (see deploy/deployment.yaml). Set both for local/dev access to
    # a real hub cluster from outside it.
    hub_kubeconfig_path: Optional[str] = None
    hub_kube_context: Optional[str] = None

    # --- Database (PostgreSQL, per your Database VM) ---
    database_url: str = "postgresql+psycopg2://tenant_operator:tenant_operator@localhost:5432/tenant_operator"

    # --- Git (source of truth for GitOps) ---
    git_repo_url: str = "git@github.com:your-org/tenant-config.git"
    git_branch: str = "main"
    git_local_path: str = "/data/tenant-config"  # persistent volume in prod
    git_author_name: str = "tenant-operator"
    git_author_email: str = "tenant-operator@your-org.com"

    # Auth: choose ONE of the two mechanisms below
    git_ssh_key_path: Optional[str] = None          # e.g. /secrets/git/id_ed25519
    git_https_token: Optional[str] = None           # e.g. GitHub/GitLab PAT
    git_https_username: Optional[str] = "git"

    # Paths inside the git repo (matches the structure in your doc).
    # git_tenants_dir: tenants/{name}.yaml (Helm values) -- every tenant's
    # manifest lives here (see provisioner._tenants_dir_for()).
    # git_bridge_tenants_dir is currently unused by any live code path --
    # git_service.commit_tenant_manifest()/delete_tenant_manifest() accept a
    # tenants_dir override for exactly this kind of split, but nothing calls
    # them with it today. Left in place rather than removed since it's cheap
    # to keep and the parameterization it documents may still be wanted.
    git_tenants_dir: str = "tenants"
    git_bridge_tenants_dir: str = "bridge-tenants"
    git_applicationsets_dir: str = "applicationsets"
    # git_tenant_info_dir: tenant-info/{slug}.yaml -- a complete, secrets-free
    # summary of each tenant (placement, subscription tier, status, etc.),
    # written alongside (never read by) the Helm values.yaml Argo CD deploys
    # from. Purely for humans/tooling that want the full record without a
    # Postgres query -- see app/services/tenant_info.py.
    git_tenant_info_dir: str = "tenant-info"

    # --- Argo CD ---
    argocd_server: str = "https://argocd.hub.internal"
    argocd_token: Optional[str] = None
    argocd_verify_tls: bool = True

    # If Argo CD is unreachable for this many CONSECUTIVE polls during a
    # tenant's own rollout, fail that tenant early instead of silently
    # retrying for the full provisioning_timeout_seconds (see
    # provisioner._wait_for_argocd_and_k8s). Tenants are not monitored after
    # they reach RUNNING -- this only applies during onboarding/updates.
    argocd_unreachable_fail_after: int = 5

    # --- Kubernetes ---
    # kubeconfig containing one context per cluster: hub, spoke-1, spoke-2, ...
    kubeconfig_path: Optional[str] = None            # None => in-cluster config
    kube_context_prefix: str = "spoke-"

    # --- Cluster registry / capacity planning ---
    clusters_config_path: str = "clusters.yaml"
    cluster_max_tenants: int = 1000

    # --- Provisioning behavior ---
    # Pre-provisioning check that a new tenant's SQL database/login, MongoDB
    # database and spoke namespace/PVC/PV don't already exist (see
    # services/preflight.py). Set PREFLIGHT_ENABLED=false to skip it, e.g.
    # when deliberately re-provisioning onto an existing database.
    preflight_enabled: bool = True
    preflight_sql_timeout_seconds: int = 120
    provisioning_poll_interval_seconds: int = 5
    provisioning_timeout_seconds: int = 900          # 15 min ceiling before FAILED
    allowed_environments: list[str] = ["dev", "stage", "prod"]


    def spoke_capacity(self, environment: str) -> int:
        return self.prod_spoke_capacity if environment == "prod" else self.stage_spoke_capacity

    def spoke_scale_trigger(self, environment: str) -> int:
        return self.prod_spoke_scale_trigger if environment == "prod" else self.stage_spoke_scale_trigger

    def autoscale_enabled(self, environment: str) -> bool:
        # Only prod has an automated new-spoke provisioning path; stage is
        # notify-only since there's no autoscaling infra for it yet.
        return environment == "prod"


@lru_cache
def get_settings() -> Settings:
    return Settings()
