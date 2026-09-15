#!/usr/bin/env python3
"""
CLI wrapper around the tenant-operator API for local testing. Submits an
onboarding request, then polls the tenant until it reaches a terminal state
(RUNNING/FAILED), printing every step -- status transitions, cluster
placement, Tenant CR echo fields, the git commit, and the final result --
as they happen so you don't have to tail the server log by hand.

Single tenant:
    python poc/onboard.py \\
        --tenant-id hbss --display-name "hbss Corporation" \\
        --email admin@hbss.com --password StrongPassword123 \\
        --domain hbss.example.com

Batch (e.g. to walk a stage spoke to its capacity/notify threshold):
    python poc/onboard.py --batch 3 --prefix loadtest

Options:
    --base-url URL       default http://localhost:8000
    --timeout SECONDS    per-tenant poll timeout, default 60
    --poll-interval SEC  default 0.5
    --no-color           disable ANSI colors (auto-disabled if not a TTY)
"""
import argparse
import sys
import time
from datetime import datetime

import httpx

TERMINAL_STATUSES = {"RUNNING", "FAILED", "DELETED"}


class Palette:
    def __init__(self, enabled: bool):
        self.enabled = enabled

    def _wrap(self, code: str, text: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.enabled else text

    def bold(self, t): return self._wrap("1", t)
    def dim(self, t): return self._wrap("2", t)
    def green(self, t): return self._wrap("32", t)
    def yellow(self, t): return self._wrap("33", t)
    def red(self, t): return self._wrap("31", t)
    def cyan(self, t): return self._wrap("36", t)
    def magenta(self, t): return self._wrap("35", t)


STATUS_COLOR = {
    "PENDING": "dim",
    "VALIDATING": "cyan",
    "GIT_COMMITTED": "cyan",
    "SYNCING": "yellow",
    "RUNNING": "green",
    "FAILED": "red",
    "DELETING": "yellow",
    "DELETED": "dim",
}


def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


def log(pal: Palette, msg: str) -> None:
    print(f"{pal.dim(_ts())} {msg}")


def status_line(pal: Palette, status: str, msg: str) -> str:
    color = STATUS_COLOR.get(status, "cyan")
    colored = getattr(pal, color)(status)
    return f"{pal.dim(_ts())} [{colored}] {msg}"


def build_payload(args) -> dict:
    payload = {
        "tenantId": args.tenant_id,
        "displayName": args.display_name,
        "email": args.email,
        "password": args.password,
        "domain": args.domain,
    }
    if args.application:
        payload["application"] = args.application
    if args.version:
        payload["version"] = args.version
    if args.tier:
        payload["tier"] = args.tier
    if args.users is not None:
        payload["users"] = args.users
    if args.db_size:
        payload["database"] = {"size": args.db_size}
    return payload


def onboard_one(client: httpx.Client, base_url: str, api_prefix: str, payload: dict,
                 poll_interval: float, timeout: float, pal: Palette) -> bool:
    tenant_id = payload["tenantId"]
    safe_payload = {**payload, "password": "***REDACTED***"}

    print()
    print(pal.bold(f"=== onboarding '{tenant_id}' ==="))
    log(pal, f"POST {api_prefix}/tenant  {safe_payload}")

    try:
        resp = client.post(f"{api_prefix}/tenant", json=payload)
    except httpx.RequestError as e:
        log(pal, pal.red(f"could not reach {base_url} -- is the server running? ({e})"))
        return False

    if resp.status_code == 400:
        log(pal, pal.red(f"rejected (400): {resp.json().get('detail')}"))
        return False
    if resp.status_code != 202:
        log(pal, pal.red(f"unexpected response {resp.status_code}: {resp.text}"))
        return False

    body = resp.json()
    row_id = body["tenantId"]
    log(pal, f"accepted -- row id={pal.magenta(row_id)} initial status={body['status']}")

    seen_status = None
    seen_fields = set()
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        r = client.get(f"{api_prefix}/tenant/{row_id}")
        r.raise_for_status()
        t = r.json()
        status = t["status"]

        if status != seen_status:
            print(status_line(pal, status, f"tenant={tenant_id}"))
            seen_status = status

        for field, label in (
            ("cluster", "placed on cluster"),
            ("namespace", "namespace"),
            ("hubCrName", "hub CR name"),
            ("hubCrUid", "hub CR uid"),
            ("gitCommit", "git commit"),
        ):
            value = t.get(field)
            if value and field not in seen_fields:
                log(pal, f"  {pal.dim(label + ':')} {value}")
                seen_fields.add(field)

        if status in TERMINAL_STATUSES:
            if status == "RUNNING":
                print(pal.green(pal.bold(f"✔ '{tenant_id}' RUNNING")))
                return True
            else:
                err = t.get("errorMessage") or "(no error message)"
                print(pal.red(pal.bold(f"✘ '{tenant_id}' {status}: {err}")))
                return False

        time.sleep(poll_interval)

    print(pal.red(pal.bold(f"✘ '{tenant_id}' timed out after {timeout}s waiting for a terminal status")))
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--api-prefix", default="/api/v1")
    parser.add_argument("--poll-interval", type=float, default=0.5)
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--no-color", action="store_true")

    single = parser.add_argument_group("single tenant")
    single.add_argument("--tenant-id")
    single.add_argument("--display-name")
    single.add_argument("--email")
    single.add_argument("--password")
    single.add_argument("--domain")
    single.add_argument("--application")
    single.add_argument("--version")
    single.add_argument("--tier")
    single.add_argument("--users", type=int)
    single.add_argument("--db-size")

    batch = parser.add_argument_group("batch (generates N sequential tenants)")
    batch.add_argument("--batch", type=int, metavar="N", help="create N sequential tenants instead of one")
    batch.add_argument("--prefix", default="loadtest", help="tenantId prefix for --batch, default 'loadtest'")

    args = parser.parse_args()
    pal = Palette(enabled=not args.no_color and sys.stdout.isatty())

    if args.batch:
        payloads = []
        for i in range(args.batch):
            tid = f"{args.prefix}{i}"
            payloads.append({
                "tenantId": tid,
                "displayName": args.display_name or f"{tid} Corp",
                "email": args.email or f"admin@{tid}.example.com",
                "password": args.password or "StrongPassword123",
                "domain": args.domain or f"{tid}.example.com",
            })
    else:
        missing = [f for f in ("tenant_id", "display_name", "email", "password", "domain")
                   if getattr(args, f) is None]
        if missing:
            parser.error(f"missing required field(s) for single-tenant mode: {', '.join(missing)} "
                         f"(or use --batch N instead)")
        payloads = [build_payload(args)]

    with httpx.Client(base_url=args.base_url, timeout=10.0) as client:
        results = [
            onboard_one(client, args.base_url, args.api_prefix, p, args.poll_interval, args.timeout, pal)
            for p in payloads
        ]

    print()
    ok = sum(results)
    print(pal.bold(f"=== {ok}/{len(results)} tenant(s) reached RUNNING ==="))
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
