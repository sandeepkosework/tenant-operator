"""
Publishes a tenant's URL on the edge HAProxy: one backend file per tenant on the edge host.

The edge host (stg-k8-db01, 192.168.85.205) runs HAProxy in a container. Its shared frontend routes
every request with `use_backend host_<host header>`, so a tenant is reachable at https://<domain> as
soon as a backend named `host_<domain>` exists -- which is one file in /opt/edge/conf.d/. This module
creates and removes that file by SSHing to the edge host and running ONE forced command there:

    edge-apply add <domain> <upstream>     create/replace the tenant's file, validate, reload HAProxy
    edge-apply remove <domain>             delete it, validate, reload
    edge-apply list                        the domains that currently have a file

Why this shape:
  * The operator never sends HAProxy config. It sends two validated values; the file is rendered by a
    template that lives on the edge host (/opt/edge/templates/tenant.cfg.tpl), so a bug or a stolen key
    here cannot put arbitrary config in front of every tenant.
  * The login is locked to `edge-apply` on the edge host (sshd ForceCommand for a password login, or
    authorized_keys restrict,command="..." for a key), so even stolen credentials get no shell on the
    machine that also hosts the databases. A password login on a normal account instead sets
    EDGE_SSH_COMMAND to run `sudo -n .../edge-apply --run '<command>'`: that account HAS a shell, so use it
    only as a stop-gap and keep the password in a Secret.
  * The edge host's key is pinned (EDGE_SSH_HOST_KEY) and an unknown host is rejected.
  * Every operation is idempotent, so a retry or a re-sync is always safe.

The upstream is the spoke's ingress-nginx LoadBalancer (MetalLB) address, from the registry's
`edge_upstream` for the spoke the tenant was placed on -- so a second spoke needs no code change: its
tenants simply get their own address in their own file.

Off by default (EDGE_ENABLED=false). See README "Edge HAProxy routes".
"""
import base64
import io
import json
import logging
import re
import shlex
import time

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.tenant import Tenant, TenantStatus

logger = logging.getLogger("tenant-operator.edge")
settings = get_settings()

# Defence in depth: edge-apply validates again on the edge host (and pins the domain suffix).
_HOST_RE = re.compile(r"^(?=.{4,253}$)[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$")
_UPSTREAM_RE = re.compile(r"^[a-z0-9]([a-z0-9.-]{0,251}[a-z0-9])?:[0-9]{1,5}$")

# Statuses whose tenant should have a route: it is running, or parked (archived keeps its place,
# so restoring never needs the edge). A tenant that was never RUNNING, or is gone, has none.
ROUTED_STATUSES = (
    TenantStatus.RUNNING,
    TenantStatus.UPDATING,
    TenantStatus.ARCHIVING,
    TenantStatus.ARCHIVED,
    TenantStatus.RESTORING,
)


class EdgeError(Exception):
    pass


def is_enabled() -> bool:
    return bool(settings.edge_enabled)


def validate_host(host: str) -> str:
    h = (host or "").strip().lower()
    if not _HOST_RE.match(h):
        raise EdgeError(f"not a valid hostname for an edge route: {host!r}")
    return h


def validate_upstream(upstream: str) -> str:
    u = (upstream or "").strip().lower()
    m = _UPSTREAM_RE.match(u)
    if not m or not (0 < int(u.rsplit(":", 1)[1]) < 65536):
        raise EdgeError(f"not a valid edge upstream (want host:port): {upstream!r}")
    return u


def upstream_for(cluster) -> str:
    """The edge upstream of the spoke a tenant lives on: the registry's edge_upstream, else the
    operator-wide default, else an error (a tenant must never be routed to a guessed address)."""
    up = getattr(cluster, "edge_upstream", None) or settings.edge_default_upstream
    if not up:
        raise EdgeError(
            f"spoke '{getattr(cluster, 'name', '?')}' has no edge_upstream in the cluster registry "
            "and EDGE_DEFAULT_UPSTREAM is not set"
        )
    return validate_upstream(up)


# ---- SSH ---------------------------------------------------------------------------------

def _load_host_key(line: str):
    import paramiko

    parts = (line or "").split()
    for i, tok in enumerate(parts):
        if tok.startswith(("ssh-", "ecdsa-")) and i + 1 < len(parts):
            try:
                return paramiko.PKey.from_type_string(tok, base64.b64decode(parts[i + 1]))
            except Exception as e:  # noqa: BLE001
                raise EdgeError(f"EDGE_SSH_HOST_KEY is not a valid public key: {e}") from e
    raise EdgeError("EDGE_SSH_HOST_KEY must be a public key line such as 'ssh-ed25519 AAAA...'")


def _load_private_key():
    import paramiko

    path = settings.edge_ssh_key_path
    if not path:
        return None                                   # password login only (see _ssh_exec)
    try:
        data = open(path, "r", encoding="utf-8").read()
    except OSError as e:
        raise EdgeError(f"cannot read the edge SSH key: {e}") from e
    for cls in (paramiko.Ed25519Key, paramiko.ECDSAKey, paramiko.RSAKey):
        try:
            return cls.from_private_key(io.StringIO(data))
        except Exception:  # noqa: BLE001 -- try the next key type
            continue
    raise EdgeError("the edge SSH key is not a supported private key (ed25519, ecdsa or rsa, no passphrase)")


def _ssh_exec(command: str) -> tuple[int, str]:
    """Runs `command` on the edge host and returns (exit status, stdout). The command string is what
    the forced command receives as SSH_ORIGINAL_COMMAND."""
    import paramiko

    if not settings.edge_ssh_host:
        raise EdgeError("EDGE_SSH_HOST is not set")
    host_key = _load_host_key(settings.edge_ssh_host_key or "")
    pkey = _load_private_key()
    password = settings.edge_ssh_password or None
    if pkey is None and password is None:
        raise EdgeError("set EDGE_SSH_KEY_PATH (a key) or EDGE_SSH_PASSWORD (a password) to log in to the edge host")
    # A login that is not locked to edge-apply on the host runs it through sudo; the command travels as
    # ONE quoted argument (it is already limited to [a-z0-9 .:-] by validate_host/validate_upstream).
    if settings.edge_ssh_command:
        command = f"{settings.edge_ssh_command} {shlex.quote(command)}"

    client = paramiko.SSHClient()
    name = settings.edge_ssh_host if settings.edge_ssh_port == 22 else f"[{settings.edge_ssh_host}]:{settings.edge_ssh_port}"
    client.get_host_keys().add(name, host_key.get_name(), host_key)
    client.set_missing_host_key_policy(paramiko.RejectPolicy())       # an unknown or changed host key = refuse
    try:
        client.connect(
            settings.edge_ssh_host, port=settings.edge_ssh_port, username=settings.edge_ssh_user, pkey=pkey,
            password=password, look_for_keys=False, allow_agent=False,
            timeout=settings.edge_timeout_seconds, banner_timeout=settings.edge_timeout_seconds,
            auth_timeout=settings.edge_timeout_seconds,
        )
        _stdin, stdout, _stderr = client.exec_command(command, timeout=settings.edge_timeout_seconds)
        out = stdout.read().decode("utf-8", "replace")
        return stdout.channel.recv_exit_status(), out
    except paramiko.SSHException as e:
        raise EdgeError(f"ssh to the edge host failed: {e}") from e
    except OSError as e:
        raise EdgeError(f"cannot reach the edge host: {e}") from e
    except EdgeError:
        raise
    except Exception as e:  # noqa: BLE001 -- whatever the SSH library throws must surface as EdgeError, never crash a caller
        raise EdgeError(f"ssh to the edge host failed unexpectedly: {type(e).__name__}: {e}") from e
    finally:
        client.close()


def _run(command: str) -> dict:
    """Runs one edge-apply command, retrying only connection-level failures (never a refusal), and
    returns its JSON answer. Raises EdgeError for a refusal or an answer it cannot understand."""
    last: Exception | None = None
    for attempt in range(1, max(1, settings.edge_attempts) + 1):
        try:
            status, out = _ssh_exec(command)
        except EdgeError as e:
            last = e
            logger.warning("[edge] %s attempt %d/%d failed: %s", command.split()[0], attempt, settings.edge_attempts, e)
            if attempt < settings.edge_attempts:
                time.sleep(settings.edge_retry_delay_seconds)
            continue
        lines = [ln for ln in out.splitlines() if ln.strip()]
        try:
            answer = json.loads(lines[-1])
        except (IndexError, ValueError) as e:
            raise EdgeError(f"unexpected answer from edge-apply (exit {status}): {out.strip()[:200]!r}") from e
        if status != 0 or not answer.get("ok"):
            raise EdgeError(f"edge-apply refused: {answer.get('error') or out.strip()[:200]}")
        return answer
    raise EdgeError(f"edge host unreachable after {settings.edge_attempts} attempts: {last}")


# ---- operations --------------------------------------------------------------------------

def ensure_route(host: str, upstream: str) -> str:
    """Creates (or replaces) the tenant's backend file. Returns "added", "updated" or "unchanged"."""
    host, upstream = validate_host(host), validate_upstream(upstream)
    answer = _run(f"add {host} {upstream}")
    logger.info("[edge] route %s -> %s: %s", host, upstream, answer.get("result"))
    return str(answer.get("result", "added"))


def remove_route(host: str) -> str:
    """Deletes the tenant's backend file. Returns "removed" or "absent" (already gone is success)."""
    host = validate_host(host)
    answer = _run(f"remove {host}")
    logger.info("[edge] route %s: %s", host, answer.get("result"))
    return str(answer.get("result", "removed"))


def list_routes() -> list[str]:
    """The domains that currently have a file on the edge host."""
    answer = _run("list")
    return sorted(str(h) for h in answer.get("hosts", []))


# ---- reconcile ---------------------------------------------------------------------------

def desired_routes(db: Session, clusters) -> tuple[dict[str, str], list[str]]:
    """({domain: upstream} every routed tenant should have, [problems for tenants that cannot be
    worked out]). A tenant is routed while RUNNING, being updated, or archived (see ROUTED_STATUSES)."""
    by_name = {c.name: c for c in clusters}
    want: dict[str, str] = {}
    problems: list[str] = []
    for t in db.query(Tenant).filter(Tenant.status.in_(ROUTED_STATUSES)).all():
        if not t.domain:
            continue
        try:
            want[validate_host(t.domain)] = upstream_for(by_name[t.cluster])
        except KeyError:
            problems.append(f"{t.tenant_name}: placed on unknown cluster '{t.cluster}'")
        except EdgeError as e:
            problems.append(f"{t.tenant_name}: {e}")
    return want, problems
