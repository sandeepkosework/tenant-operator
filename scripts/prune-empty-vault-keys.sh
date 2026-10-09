#!/usr/bin/env bash
# Removes keys whose value is an empty string from a tenant's Vault secrets
# (secret/<prefix>/<tenant>/<service>), so values set once in
# secret/k8s/tenant-common (or service-common) are no longer overridden by an empty
# key in a service's own secret. Older tenants were created with those empty keys;
# the operator no longer writes them. Dry run unless --apply is given.
#
#   scripts/prune-empty-vault-keys.sh <tenant-slug>            # show what would change
#   scripts/prune-empty-vault-keys.sh <tenant-slug> --apply    # rewrite the secrets
#
# Needs the vault CLI (logged in, read+write on the tenant's paths) and jq.
# Afterwards restart the tenant's pods (kubectl rollout restart deploy -n <ns>); they
# only read secrets at start, and the chart refreshes them from Vault hourly.
set -euo pipefail

TENANT="${1:?usage: $0 <tenant-slug> [--apply]}"
APPLY="${2:-}"
MOUNT="${VAULT_KV_MOUNT:-secret}"
PREFIX="${VAULT_TENANT_SECRET_PREFIX:-k8s}"
BASE="$MOUNT/$PREFIX/$TENANT"

for svc in $(vault kv list -format=json "$BASE" | jq -r '.[]'); do
  path="$BASE/${svc%/}"
  current=$(vault kv get -format=json "$path" | jq -c '.data.data')
  kept=$(jq -c 'with_entries(select(.value != ""))' <<<"$current")
  removed=$(jq -nr --argjson a "$current" --argjson b "$kept" '($a | keys) - ($b | keys) | join(",")')
  if [ -z "$removed" ]; then echo "ok      $path"; continue; fi
  if [ "$kept" = "{}" ]; then echo "SKIP    $path (every key is empty; the path must keep at least one key)"; continue; fi
  echo "prune   $path: $removed"
  if [ "$APPLY" = "--apply" ]; then vault kv put "$path" - <<<"$kept" >/dev/null; fi
done
[ "$APPLY" = "--apply" ] || echo "(dry run -- re-run with --apply to rewrite)"
