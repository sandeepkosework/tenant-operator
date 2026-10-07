#!/usr/bin/env bash
# Copy tenants' Vault secrets from the old layout to the one helm-chart-bridge's
# three-layer secrets read.
#
#   secret/<src>/<slug>/<service>  ->  secret/<dst>/<slug>/<service>
#   secret/<src>/<slug>/common     ->  secret/<dst>/<slug>/service-common
#
# (defaults: src = tenants, dst = k8s, mount = secret, KV v2)
#
# It COPIES: the old paths are never touched, so switching back is just
# pointing the chart and the operator at the old prefix again. Dry-run unless
# --apply is given. A destination path that already exists is skipped unless
# --overwrite is given. Secret values are never printed.
#
#   VAULT_ADDR=http://192.168.85.201:30820 VAULT_TOKEN=... \
#     ./migrate-vault-tenant-secrets.sh --all                 # what would change
#   ... ./migrate-vault-tenant-secrets.sh --tenant hbss-005-39 --apply
#
# Needs: bash, curl, jq.
set -euo pipefail

MOUNT="${VAULT_KV_MOUNT:-secret}"
SRC="tenants"
DST="k8s"
APPLY=0
OVERWRITE=0
ALL=0
TENANTS=()

usage() { sed -n '2,/^set -euo/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --tenant)     TENANTS+=("${2:?--tenant needs a slug}"); shift 2 ;;
    --all)        ALL=1; shift ;;
    --apply)      APPLY=1; shift ;;
    --overwrite)  OVERWRITE=1; shift ;;
    --src-prefix) SRC="${2:?}"; shift 2 ;;
    --dst-prefix) DST="${2:?}"; shift 2 ;;
    --mount)      MOUNT="${2:?}"; shift 2 ;;
    -h|--help)    usage 0 ;;
    *) echo "unknown argument: $1" >&2; usage 1 ;;
  esac
done

: "${VAULT_ADDR:?set VAULT_ADDR}"
: "${VAULT_TOKEN:?set VAULT_TOKEN}"
[[ "$SRC" != "$DST" ]] || { echo "source and destination prefix are the same ($SRC)" >&2; exit 1; }
[[ $ALL -eq 1 || ${#TENANTS[@]} -gt 0 ]] || { echo "give --tenant <slug> (repeatable) or --all" >&2; usage 1; }

BODY="$(mktemp)"
trap 'rm -f "$BODY"' EXIT
STATUS=000

# api METHOD PATH [JSON]  -> sets $STATUS (HTTP code); response body is in $BODY.
# Call it directly, not inside $( ), or $STATUS would be lost with the subshell.
api() {
  local method="$1" path="$2" data="${3:-}"
  local args=(-sS -X "$method" -H "X-Vault-Token: $VAULT_TOKEN" -o "$BODY" -w '%{http_code}')
  [[ -n "$data" ]] && args+=(-H 'Content-Type: application/json' -d "$data")
  STATUS=$(curl "${args[@]}" "$VAULT_ADDR/v1/$path") || STATUS=000
}

# list PATH -> one key per line (directories keep their trailing slash)
list() {
  api LIST "$1"
  [[ "$STATUS" == "200" ]] || return 0
  jq -r '.data.keys[]?' "$BODY"
}

# Fail loudly on a bad address/token instead of reporting "nothing found".
api GET "auth/token/lookup-self"
case "$STATUS" in
  200) ;;
  000) echo "cannot reach Vault at $VAULT_ADDR" >&2; exit 1 ;;
  *)   echo "Vault rejected the token (HTTP $STATUS)" >&2; exit 1 ;;
esac

if [[ $ALL -eq 1 ]]; then
  mapfile -t found < <(list "$MOUNT/metadata/$SRC/" | sed 's#/$##')
  TENANTS+=("${found[@]}")
fi
[[ ${#TENANTS[@]} -gt 0 ]] || { echo "no tenants found under $MOUNT/$SRC/"; exit 0; }

mode="DRY-RUN (nothing is written; add --apply)"; [[ $APPLY -eq 1 ]] && mode="APPLY"
echo "Vault: $VAULT_ADDR   $MOUNT/$SRC/<slug>/*  ->  $MOUNT/$DST/<slug>/*   [$mode]"

copied=0; skipped=0; failed=0; missing=0
for slug in "${TENANTS[@]}"; do
  mapfile -t services < <(list "$MOUNT/metadata/$SRC/$slug" | grep -v '/$' || true)
  if [[ ${#services[@]} -eq 0 ]]; then
    echo "  [$slug] nothing under $SRC/$slug"; missing=$((missing + 1)); continue
  fi
  for svc in "${services[@]}"; do
    target="$svc"; [[ "$svc" == "common" ]] && target="service-common"

    api GET "$MOUNT/data/$SRC/$slug/$svc"
    if [[ "$STATUS" != "200" ]]; then
      echo "  [$slug] $svc: cannot read source (HTTP $STATUS)"; failed=$((failed + 1)); continue
    fi
    payload=$(jq -c '{data: .data.data}' "$BODY")
    nkeys=$(jq -r '.data.data | length' "$BODY")

    api GET "$MOUNT/data/$DST/$slug/$target"
    if [[ "$STATUS" == "200" && $OVERWRITE -eq 0 ]]; then
      echo "  [$slug] $svc -> $target: already exists, skipped"; skipped=$((skipped + 1)); continue
    fi

    if [[ $APPLY -eq 0 ]]; then
      echo "  [$slug] $svc -> $target: would copy $nkeys key(s)"; copied=$((copied + 1)); continue
    fi

    api POST "$MOUNT/data/$DST/$slug/$target" "$payload"
    if [[ "$STATUS" != "200" && "$STATUS" != "204" ]]; then
      echo "  [$slug] $svc -> $target: WRITE FAILED (HTTP $STATUS)"; failed=$((failed + 1)); continue
    fi
    # read it back and compare, so a silent partial write can't pass
    api GET "$MOUNT/data/$DST/$slug/$target"
    back=$(jq -cS '.data.data' "$BODY")
    want=$(jq -cS '.data' <<<"$payload")
    if [[ "$back" == "$want" ]]; then
      echo "  [$slug] $svc -> $target: copied $nkeys key(s), verified"; copied=$((copied + 1))
    else
      echo "  [$slug] $svc -> $target: VERIFY FAILED (read-back differs)"; failed=$((failed + 1))
    fi
  done
done

echo "summary: $copied $([[ $APPLY -eq 1 ]] && echo copied || echo 'would copy'), $skipped skipped (already there), $failed failed, $missing tenant(s) with nothing to copy"
[[ $failed -eq 0 ]]
