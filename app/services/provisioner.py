"""
The async workflow engine. This runs in a FastAPI BackgroundTask today;
swap the entrypoints below for Celery tasks with no change to the logic
if/when you need multi-replica workers or retries-with-backoff at scale.

State machine (per the doc):
  PENDING -> VALIDATING -> GIT_COMMITTED -> SYNCING -> RUNNING
  any step -> FAILED (with error_message set)
  RUNNING -> DELETING -> DELETED
  RUNNING -> UPDATING -> SYNCING -> RUNNING
"""
import logging
import time
import uuid

from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import SessionLocal
from app.models.tenant import Tenant, TenantStatus
from app.services import (
    argocd_service,
    cluster_selector,
    git_service,
    helm_values,
    kubernetes_service,
    meta_builder_job,
    mongo_service,
    notifications,
    spoke_cr,
    spoke_scaler,
    status_bus,
    tenant_cr,
    vault_service,
)

logger = logging.getLogger("tenant-operator.provisioner")
settings = get_settings()


def _tenants_dir_for(tenant: Tenant) -> str:
    """Which git directory this tenant's manifest lives under -- see
    helm-chart-bridge-tenants' ApplicationSet generator."""
    return settings.git_tenants_dir


def _render_values_yaml(tenant: Tenant, cluster) -> str:
    """Renders this tenant's values.yaml via the qraie-bridge chart's Jinja
    template -- the only chart tenants are provisioned onto."""
    return helm_values.render_qraie_bridge_values_yaml(
        tenant_name=tenant.tenant_name,
        tenant_slug=tenant.slug,
        environment=tenant.environment,
        namespace=tenant.namespace,
        domain=tenant.domain,
        argocd_cluster_server=cluster.argocd_cluster_server,
        disabled_services=tenant.disabled_services,
        vault_server=cluster.vault_server,
        ingress_class_name=cluster.ingress_class_name,
    )


def _set_status(db: Session, tenant: Tenant, status: TenantStatus, error: str | None = None) -> None:
    tenant.status = status
    tenant.error_message = error
    db.add(tenant)
    db.commit()
    db.refresh(tenant)
    logger.info("tenant=%s status=%s", tenant.tenant_name, status)
    tenant_cr.echo_cr_phase(tenant, status.value)
    status_bus.publish(tenant)


def _wait_for_argocd_and_k8s(db: Session, tenant: Tenant, cluster_context: str) -> None:
    """
    Poll Argo CD + Kubernetes until healthy or timeout. Updates status to
    RUNNING/FAILED. Fails fast (before the timeout) in two cases instead of
    silently retrying for the full provisioning_timeout_seconds:
      - Argo CD reports a DEFINITIVE application failure (Degraded health,
        or the last sync operation itself Failed/Error) -- see
        argocd_service.classify().
      - Argo CD itself is unreachable for argocd_unreachable_fail_after
        consecutive polls.
    """
    deadline = time.monotonic() + settings.provisioning_timeout_seconds
    _set_status(db, tenant, TenantStatus.SYNCING)

    poll = 0
    consecutive_unreachable = 0
    while time.monotonic() < deadline:
        poll += 1
        try:
            argo_status = argocd_service.get_application_status(tenant.slug)
            consecutive_unreachable = 0
            classification = argocd_service.classify(argo_status)

            logger.info(
                "[hub-cr] watching %s/%s (poll #%d): health=%s sync=%s operationPhase=%s -> %s",
                settings.hub_namespace, tenant.hub_cr_name or tenant.tenant_name, poll,
                argo_status.get("health"), argo_status.get("sync"), argo_status.get("operationPhase"),
                classification,
            )

            if classification == "failed":
                reason = argo_status.get("message") or (
                    f"health={argo_status.get('health')} operationPhase={argo_status.get('operationPhase')}"
                )
                _set_status(db, tenant, TenantStatus.FAILED, error=f"Argo CD reported a failure: {reason}")
                return

            if classification == "healthy":
                k8s_ok = kubernetes_service.is_fully_ready(cluster_context, tenant.namespace, tenant.slug)
                if k8s_ok:
                    _set_status(db, tenant, TenantStatus.RUNNING)
                    return
                # Argo says Healthy/Synced but our own k8s check disagrees
                # (e.g. ingress LB not provisioned yet) -- keep polling,
                # this alone isn't a definitive Argo-reported failure.

        except argocd_service.ArgoCDUnreachableError as e:
            consecutive_unreachable += 1
            logger.warning(
                "tenant=%s Argo CD unreachable (%d/%d consecutive): %s",
                tenant.tenant_name, consecutive_unreachable, settings.argocd_unreachable_fail_after, e,
            )
            if consecutive_unreachable == 1:
                notifications.notify(
                    event="argocd_unreachable",
                    message=f"Argo CD appears unreachable while provisioning '{tenant.tenant_name}': {e}",
                    tenant=tenant.tenant_name,
                )
            if consecutive_unreachable >= settings.argocd_unreachable_fail_after:
                _set_status(
                    db, tenant, TenantStatus.FAILED,
                    error=f"Argo CD was unreachable for {consecutive_unreachable} consecutive checks: {e}",
                )
                return
        except Exception as e:  # noqa: BLE001 -- other transient infra errors shouldn't kill the poll loop
            logger.warning("tenant=%s poll error (will retry): %s", tenant.tenant_name, e)

        time.sleep(settings.provisioning_poll_interval_seconds)

    _set_status(
        db, tenant, TenantStatus.FAILED,
        error=f"timed out after {settings.provisioning_timeout_seconds}s waiting for Argo CD/Kubernetes",
    )


def provision_tenant(tenant_id: uuid.UUID, admin_password: str) -> None:
    """
    Entry point for a fresh tenant creation. Run as a background task.
    `admin_password` is passed through only for the meta-builder Job trigger
    at the end of this flow -- it is never written to the database.
    """
    db = SessionLocal()
    try:
        tenant = db.get(Tenant, tenant_id)
        if tenant is None:
            logger.error("tenant_id=%s not found -- cannot provision", tenant_id)
            return

        try:
            logger.info("[step] tenant=%s provisioning started", tenant.tenant_name)
            _set_status(db, tenant, TenantStatus.VALIDATING)

            cluster, projected_count = cluster_selector.select_cluster(tenant.environment, db)
            tenant.cluster = cluster.name
            # tenant.slug already carries the sequential id (see
            # Tenant.slug) -- "tenant-" here is just so this namespace
            # reads unambiguously as a tenant's own among argocd/vault/
            # kube-system/etc. in `kubectl get ns`, e.g. "tenant-00042-acme-corp".
            tenant.namespace = f"tenant-{tenant.slug}"
            db.add(tenant)
            db.commit()
            db.refresh(tenant)
            logger.info(
                "[step] tenant=%s placed on cluster=%s (region=%s, %d/%d on that spoke)",
                tenant.tenant_name, cluster.name, cluster.region, projected_count,
                settings.spoke_capacity(tenant.environment),
            )

            # Capacity check for the spoke we just placed this tenant on. Fires a
            # parallel/non-blocking new-spoke provisioning job in prod, or a
            # notification in stage (no autoscaling there) -- see spoke_scaler.
            spoke_scaler.maybe_trigger_spoke_scale(cluster.name, tenant.environment, projected_count)

            # Keep the spoke's SpokeCluster CR (tenant list + count) current.
            spoke_cr.upsert_spoke_cluster_cr(cluster, db)
            logger.info("[step] tenant=%s SpokeCluster CR for '%s' updated", tenant.tenant_name, cluster.name)

            # Create the Tenant CR on the hub cluster before handing off to GitOps.
            logger.info("[step] tenant=%s creating Tenant CR on hub", tenant.tenant_name)
            cr = tenant_cr.build_tenant_cr(tenant, cluster)
            cr_name, cr_uid = tenant_cr.create_tenant_cr(cr)
            tenant.hub_cr_name = cr_name
            tenant.hub_cr_uid = cr_uid
            db.add(tenant)
            db.commit()
            db.refresh(tenant)
            logger.info("[step] tenant=%s Tenant CR created (uid=%s)", tenant.tenant_name, cr_uid)

            # Write Vault secrets BEFORE the GitOps handoff, not after RUNNING:
            # the tenant workload's own Vault Agent/VSO sidecar blocks its
            # pod from starting until these paths exist, so if we waited
            # until RUNNING to write them, the pod could never become Ready
            # in the first place -- a chicken-and-egg deadlock. The
            # meta-builder Job trigger still waits for RUNNING further down;
            # it just reads the same secrets written here.
            logger.info("[step] tenant=%s writing initial secrets to Vault", tenant.tenant_name)
            service_data = vault_service.write_initial_qraie_bridge_tenant_secrets(tenant.slug, tenant.domain)
            mongo_service.write_tenant_env_config(tenant.slug, service_data)

            logger.info("[step] tenant=%s rendering Helm values", tenant.tenant_name)
            values_yaml = _render_values_yaml(tenant, cluster)

            logger.info("[step] tenant=%s committing Helm values to GitOps repo", tenant.tenant_name)
            commit_sha = git_service.commit_tenant_manifest(
                tenant.slug,
                values_yaml,
                message=f"tenant-operator: create {tenant.tenant_name} ({tenant.environment})",
                tenants_dir=_tenants_dir_for(tenant),
            )
            tenant.git_commit = commit_sha
            db.add(tenant)
            db.commit()
            db.refresh(tenant)
            logger.info("[step] tenant=%s git commit created: %s", tenant.tenant_name, commit_sha)
            _set_status(db, tenant, TenantStatus.GIT_COMMITTED)

            logger.info("[step] tenant=%s waiting for Argo CD sync + Kubernetes readiness", tenant.tenant_name)
            _wait_for_argocd_and_k8s(db, tenant, cluster.context)

            if tenant.status == TenantStatus.RUNNING:
                # DB + workload are ready. Vault secrets were already written
                # above (before the GitOps handoff); the seeding Job reads
                # those same DB/redis/mongo/jwt secrets via safeVault() and
                # fails hard if they're missing.
                logger.info("[step] tenant=%s triggering DB seeding job", tenant.tenant_name)
                meta_builder_job.trigger_meta_builder_job(tenant, admin_password)

                logger.info("[step] tenant=%s provisioning complete", tenant.tenant_name)

        except cluster_selector.NoAvailableClusterError as e:
            _set_status(db, tenant, TenantStatus.FAILED, error=str(e))
        except git_service.GitServiceError as e:
            _set_status(db, tenant, TenantStatus.FAILED, error=str(e))
        except Exception as e:  # noqa: BLE001
            logger.exception("tenant=%s unexpected provisioning failure", tenant.tenant_name)
            _set_status(db, tenant, TenantStatus.FAILED, error=f"unexpected error: {e}")
    finally:
        db.close()


def update_tenant(tenant_id: uuid.UUID, new_version: str | None, new_users: int | None,
                   new_db_size: str | None, new_disabled_services: list[str] | None = None) -> None:
    """Entry point for PUT /tenants/{id}. Re-renders values.yaml and re-commits."""
    db = SessionLocal()
    try:
        tenant = db.get(Tenant, tenant_id)
        if tenant is None:
            logger.error("tenant_id=%s not found -- cannot update", tenant_id)
            return

        try:
            logger.info("[step] tenant=%s update started (version=%s users=%s db_size=%s services=%s)",
                        tenant.tenant_name, new_version, new_users, new_db_size, new_disabled_services)
            _set_status(db, tenant, TenantStatus.UPDATING)

            if new_version:
                tenant.version = new_version
            if new_users is not None:
                tenant.users = new_users
            if new_db_size:
                tenant.database_size = new_db_size
            if new_disabled_services is not None:
                tenant.disabled_services = new_disabled_services
            db.add(tenant)
            db.commit()
            db.refresh(tenant)

            cluster = next(c for c in cluster_selector.load_cluster_registry() if c.name == tenant.cluster)

            logger.info("[step] tenant=%s rendering Helm values", tenant.tenant_name)
            values_yaml = _render_values_yaml(tenant, cluster)

            logger.info("[step] tenant=%s committing Helm values to GitOps repo", tenant.tenant_name)
            commit_sha = git_service.commit_tenant_manifest(
                tenant.slug,
                values_yaml,
                message=f"tenant-operator: update {tenant.tenant_name} -> version={tenant.version}",
                tenants_dir=_tenants_dir_for(tenant),
            )
            tenant.git_commit = commit_sha
            db.add(tenant)
            db.commit()
            db.refresh(tenant)
            logger.info("[step] tenant=%s git commit created: %s", tenant.tenant_name, commit_sha)

            logger.info("[step] tenant=%s waiting for Argo CD sync + Kubernetes readiness", tenant.tenant_name)
            _wait_for_argocd_and_k8s(db, tenant, cluster.context)

            if tenant.status == TenantStatus.RUNNING:
                logger.info("[step] tenant=%s update complete", tenant.tenant_name)

        except Exception as e:  # noqa: BLE001
            logger.exception("tenant=%s unexpected update failure", tenant.tenant_name)
            _set_status(db, tenant, TenantStatus.FAILED, error=f"unexpected error during update: {e}")
    finally:
        db.close()


def delete_tenant(tenant_id: uuid.UUID) -> None:
    """Entry point for DELETE /tenants/{id}."""
    db = SessionLocal()
    try:
        tenant = db.get(Tenant, tenant_id)
        if tenant is None:
            logger.error("tenant_id=%s not found -- cannot delete", tenant_id)
            return

        try:
            logger.info("[step] tenant=%s deletion started", tenant.tenant_name)
            _set_status(db, tenant, TenantStatus.DELETING)

            logger.info("[step] tenant=%s removing manifest from GitOps repo", tenant.tenant_name)
            git_service.delete_tenant_manifest(
                tenant.slug,
                message=f"tenant-operator: delete {tenant.tenant_name}",
                tenants_dir=_tenants_dir_for(tenant),
            )

            cluster = next(c for c in cluster_selector.load_cluster_registry() if c.name == tenant.cluster)

            # Argo CD prunes the Application (and everything it tracks) once it
            # sees the manifest gone, but never tracks the namespace itself --
            # delete it directly or it stays Active forever. See
            # kubernetes_service.delete_namespace().
            logger.info("[step] tenant=%s deleting namespace '%s' on '%s'",
                        tenant.tenant_name, tenant.namespace, cluster.name)
            kubernetes_service.delete_namespace(cluster.context, tenant.namespace)

            logger.info("[step] tenant=%s waiting for namespace '%s' to terminate on '%s'",
                        tenant.tenant_name, tenant.namespace, cluster.name)
            deadline = time.monotonic() + settings.provisioning_timeout_seconds
            while time.monotonic() < deadline:
                if kubernetes_service.namespace_fully_deleted(cluster.context, tenant.namespace):
                    from datetime import datetime, timezone
                    tenant.deleted_at = datetime.now(timezone.utc)
                    _set_status(db, tenant, TenantStatus.DELETED)
                    # Tenant no longer counts toward this spoke -- refresh its CR.
                    spoke_cr.upsert_spoke_cluster_cr(cluster, db)
                    logger.info("[step] tenant=%s deletion complete", tenant.tenant_name)
                    return
                time.sleep(settings.provisioning_poll_interval_seconds)

            _set_status(
                db, tenant, TenantStatus.FAILED,
                error=f"namespace '{tenant.namespace}' did not terminate within timeout",
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("tenant=%s unexpected deletion failure", tenant.tenant_name)
            _set_status(db, tenant, TenantStatus.FAILED, error=f"unexpected error during deletion: {e}")
    finally:
        db.close()
