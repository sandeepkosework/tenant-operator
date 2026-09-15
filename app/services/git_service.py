"""
All writes to the GitOps repo go through this module. Nothing else in the
codebase should touch `git` or `kubectl apply` directly — see the doc's
core rule: "the Tenant Operator writes YAML into Git, never K8s directly."

Concurrency: git working-tree operations are not safe to run in parallel
from multiple threads/requests against the same clone, so all writes take
a process-wide lock. In production, back this with an actual lock service
(e.g. Postgres advisory lock) if you run more than one operator replica.
"""
import os
import threading
from pathlib import Path

import git
from git import Repo, GitCommandError

from app.config import get_settings

settings = get_settings()
_lock = threading.Lock()


class GitServiceError(Exception):
    pass


def _env_for_auth() -> dict:
    """Build the env vars git needs for non-interactive auth."""
    env = os.environ.copy()
    if settings.git_ssh_key_path:
        env["GIT_SSH_COMMAND"] = (
            f"ssh -i {settings.git_ssh_key_path} -o StrictHostKeyChecking=accept-new"
        )
    return env


def _authed_url() -> str:
    """If using an HTTPS token, inject it into the remote URL."""
    if settings.git_https_token and settings.git_repo_url.startswith("https://"):
        scheme_stripped = settings.git_repo_url[len("https://"):]
        return f"https://{settings.git_https_username}:{settings.git_https_token}@{scheme_stripped}"
    return settings.git_repo_url


def _get_repo() -> Repo:
    local_path = Path(settings.git_local_path)
    env = _env_for_auth()

    # git's "dubious ownership" check compares the clone's file-owner uid
    # against the running process's uid -- fails here whenever
    # GIT_LOCAL_PATH is a fresh PersistentVolume (provisioned owned by
    # root, since podSecurityContext.fsGroup only affects group
    # ownership/writability, not the owner uid) mounted into a container
    # that runs as a non-root user (this image's "tenantop", uid 1000).
    # Whitelisting it here means this always works regardless of what
    # provisioned the volume, instead of depending on a one-time `git
    # config` a human might forget to run against a fresh PVC.
    git.Git().execute(["git", "config", "--global", "--add", "safe.directory", str(local_path)])

    if local_path.exists() and (local_path / ".git").exists():
        repo = Repo(str(local_path))
        with repo.git.custom_environment(**env):
            repo.remotes.origin.set_url(_authed_url())
            repo.remotes.origin.fetch()
            repo.git.checkout(settings.git_branch)
            # Hard reset instead of `pull()`: this local clone is never a
            # source of truth (the bare/remote repo is) and only this
            # process ever commits to it, so there's nothing local worth
            # merging. A plain `pull()` fails on any git version that
            # requires an explicit reconcile strategy (rebase vs merge) once
            # history has diverged for any reason -- reset sidesteps that
            # entirely and guarantees the clone exactly mirrors origin
            # before each write.
            repo.git.reset("--hard", f"origin/{settings.git_branch}")
        return repo

    local_path.parent.mkdir(parents=True, exist_ok=True)
    repo = Repo.clone_from(_authed_url(), str(local_path), branch=settings.git_branch, env=env)
    # git can sanitize embedded credentials out of the URL it stores in
    # .git/config on clone (varies by git version/credential-helper config)
    # -- re-set it explicitly so a later push() always has them, same as
    # the existing-repo branch above already does on every open.
    repo.remotes.origin.set_url(_authed_url())
    return repo


def _tenant_file_path(repo: Repo, tenant_slug: str, tenants_dir: str) -> Path:
    return Path(repo.working_tree_dir) / tenants_dir / f"{tenant_slug}.yaml"


def commit_tenant_manifest(tenant_slug: str, content: str, message: str, tenants_dir: str = None) -> str:
    """Write/overwrite {tenants_dir}/{slug}.yaml and push. Returns the commit SHA.

    Filename is the tenant's slug (name + sequence number, e.g.
    "acme-corp-42"), not the bare tenant name -- tenant_name has no DB
    uniqueness constraint, so two tenants sharing a name would otherwise
    silently overwrite each other's file.

    `tenants_dir` defaults to settings.git_tenants_dir ("tenants") -- the
    original single-chart layout. Pass a different directory (e.g.
    settings.git_bridge_tenants_dir) for tenants on a different chart/
    ApplicationSet, so the two flows never collide on the same files.
    """
    tenants_dir = tenants_dir or settings.git_tenants_dir
    with _lock:
        try:
            repo = _get_repo()
            file_path = _tenant_file_path(repo, tenant_slug, tenants_dir)
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content)

            repo.index.add([str(file_path)])
            if not repo.index.diff("HEAD") and not repo.untracked_files:
                # nothing changed (e.g. identical re-apply) -- no-op commit avoided
                return repo.head.commit.hexsha

            commit = repo.index.commit(
                message,
                author=git.Actor(settings.git_author_name, settings.git_author_email),
            )
            with repo.git.custom_environment(**_env_for_auth()):
                repo.remotes.origin.push()
            return commit.hexsha
        except GitCommandError as e:
            raise GitServiceError(f"git operation failed: {e}") from e


def delete_tenant_manifest(tenant_slug: str, message: str, tenants_dir: str = None) -> str:
    """Remove {tenants_dir}/{slug}.yaml and push. Returns the commit SHA (or current HEAD if already gone)."""
    tenants_dir = tenants_dir or settings.git_tenants_dir
    with _lock:
        try:
            repo = _get_repo()
            file_path = _tenant_file_path(repo, tenant_slug, tenants_dir)
            if not file_path.exists():
                return repo.head.commit.hexsha

            repo.index.remove([str(file_path)], working_tree=True)
            commit = repo.index.commit(
                message,
                author=git.Actor(settings.git_author_name, settings.git_author_email),
            )
            with repo.git.custom_environment(**_env_for_auth()):
                repo.remotes.origin.push()
            return commit.hexsha
        except GitCommandError as e:
            raise GitServiceError(f"git operation failed: {e}") from e
