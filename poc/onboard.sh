#!/usr/bin/env bash
# Bash equivalent of poc/onboard.py -- submits an onboarding request and
# polls the tenant until it reaches a terminal state (RUNNING/FAILED),
# printing every step: status transitions, cluster placement, Tenant CR
# echo fields, the git commit, and the final result.
#
# Requires: curl, jq
#
# Single tenant:
#   ./poc/onboard.sh \
#     --tenant-id hbss --display-name "hbss Corporation" \
#     --email admin@hbss.com --password StrongPassword123 \
#     --domain hbss.example.com
#
# Batch (e.g. to walk a stage spoke to its capacity/notify threshold):
#   ./poc/onboard.sh --batch 3 --prefix loadtest
#
# Options:
#   --base-url URL       default http://localhost:8000
#   --api-prefix PATH    default /api/v1
#   --timeout SECONDS    per-tenant poll timeout, default 60
#   --poll-interval SEC  default 0.5
#   --no-color           disable ANSI colors (auto-disabled if not a TTY)

set -uo pipefail

BASE_URL="http://localhost:8000"
API_PREFIX="/api/v1"
POLL_INTERVAL=0.5
TIMEOUT=60
NO_COLOR=0
BATCH=""
PREFIX="loadtest"

TENANT_ID=""
DISPLAY_NAME=""
EMAIL=""
PASSWORD=""
DOMAIN=""
APPLICATION=""
VERSION=""
TIER=""
USERS=""
DB_SIZE=""

usage() { sed -n '2,26p' "$0"; exit "${1:-0}"; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        --base-url) BASE_URL="$2"; shift 2 ;;
        --api-prefix) API_PREFIX="$2"; shift 2 ;;
        --poll-interval) POLL_INTERVAL="$2"; shift 2 ;;
        --timeout) TIMEOUT="$2"; shift 2 ;;
        --no-color) NO_COLOR=1; shift ;;
        --batch) BATCH="$2"; shift 2 ;;
        --prefix) PREFIX="$2"; shift 2 ;;
        --tenant-id) TENANT_ID="$2"; shift 2 ;;
        --display-name) DISPLAY_NAME="$2"; shift 2 ;;
        --email) EMAIL="$2"; shift 2 ;;
        --password) PASSWORD="$2"; shift 2 ;;
        --domain) DOMAIN="$2"; shift 2 ;;
        --application) APPLICATION="$2"; shift 2 ;;
        --version) VERSION="$2"; shift 2 ;;
        --tier) TIER="$2"; shift 2 ;;
        --users) USERS="$2"; shift 2 ;;
        --db-size) DB_SIZE="$2"; shift 2 ;;
        -h|--help) usage 0 ;;
        *) echo "unknown argument: $1" >&2; usage 1 ;;
    esac
done

command -v curl >/dev/null || { echo "curl is required" >&2; exit 1; }
command -v jq >/dev/null || { echo "jq is required (apt install jq / brew install jq)" >&2; exit 1; }

if [[ -t 1 && "$NO_COLOR" -eq 0 ]]; then
    C_DIM=$'\033[2m'; C_BOLD=$'\033[1m'; C_GREEN=$'\033[32m'
    C_YELLOW=$'\033[33m'; C_RED=$'\033[31m'; C_CYAN=$'\033[36m'
    C_MAGENTA=$'\033[35m'; C_RESET=$'\033[0m'
else
    C_DIM=""; C_BOLD=""; C_GREEN=""; C_YELLOW=""; C_RED=""; C_CYAN=""; C_MAGENTA=""; C_RESET=""
fi

ts() { date +%H:%M:%S; }

log() { echo "${C_DIM}$(ts)${C_RESET} $*"; }

status_color() {
    case "$1" in
        RUNNING) echo "$C_GREEN" ;;
        FAILED) echo "$C_RED" ;;
        SYNCING|DELETING) echo "$C_YELLOW" ;;
        PENDING|DELETED) echo "$C_DIM" ;;
        *) echo "$C_CYAN" ;;
    esac
}

# onboard_one tenantId displayName email password domain [application version tier users dbSize]
onboard_one() {
    local tenant_id="$1" display_name="$2" email="$3" password="$4" domain="$5"
    local application="${6:-}" version="${7:-}" tier="${8:-}" users="${9:-}" db_size="${10:-}"

    echo
    echo "${C_BOLD}=== onboarding '$tenant_id' ===${C_RESET}"

    local payload
    payload=$(jq -n \
        --arg tenantId "$tenant_id" \
        --arg displayName "$display_name" \
        --arg email "$email" \
        --arg password "$password" \
        --arg domain "$domain" \
        --arg application "$application" \
        --arg version "$version" \
        --arg tier "$tier" \
        --arg users "$users" \
        --arg dbSize "$db_size" \
        '{tenantId:$tenantId, displayName:$displayName, email:$email, password:$password, domain:$domain}
         + (if $application != "" then {application:$application} else {} end)
         + (if $version != "" then {version:$version} else {} end)
         + (if $tier != "" then {tier:$tier} else {} end)
         + (if $users != "" then {users:($users|tonumber)} else {} end)
         + (if $dbSize != "" then {database:{size:$dbSize}} else {} end)')

    local safe_payload
    safe_payload=$(echo "$payload" | jq -c '.password = "***REDACTED***"')
    log "POST ${API_PREFIX}/tenant  ${safe_payload}"

    local http_code body
    body=$(curl -s -w '\n%{http_code}' -X POST "${BASE_URL}${API_PREFIX}/tenant" \
        -H 'Content-Type: application/json' -d "$payload")
    http_code=$(echo "$body" | tail -n1)
    body=$(echo "$body" | sed '$d')

    if [[ -z "$http_code" || "$http_code" == "000" ]]; then
        log "${C_RED}could not reach ${BASE_URL} -- is the server running?${C_RESET}"
        return 1
    fi
    if [[ "$http_code" == "400" ]]; then
        log "${C_RED}rejected (400): $(echo "$body" | jq -r '.detail')${C_RESET}"
        return 1
    fi
    if [[ "$http_code" != "202" ]]; then
        log "${C_RED}unexpected response ${http_code}: ${body}${C_RESET}"
        return 1
    fi

    local row_id init_status
    row_id=$(echo "$body" | jq -r '.tenantId')
    init_status=$(echo "$body" | jq -r '.status')
    log "accepted -- row id=${C_MAGENTA}${row_id}${C_RESET} initial status=${init_status}"

    local seen_status="" seen_cluster=0 seen_namespace=0 seen_cr_name=0 seen_cr_uid=0 seen_commit=0
    local deadline
    deadline=$(($(date +%s) + ${TIMEOUT%.*}))

    while [[ $(date +%s) -lt $deadline ]]; do
        local t status color
        t=$(curl -s "${BASE_URL}${API_PREFIX}/tenant/${row_id}")
        status=$(echo "$t" | jq -r '.status')

        if [[ "$status" != "$seen_status" ]]; then
            color=$(status_color "$status")
            echo "${C_DIM}$(ts)${C_RESET} [${color}${status}${C_RESET}] tenant=${tenant_id}"
            seen_status="$status"
        fi

        _maybe_print_field() {
            local field="$1" label="$2" flag_var="$3"
            local value
            value=$(echo "$t" | jq -r --arg f "$field" '.[$f] // empty')
            if [[ -n "$value" && "${!flag_var}" -eq 0 ]]; then
                log "  ${C_DIM}${label}:${C_RESET} ${value}"
                printf -v "$flag_var" '1'
            fi
        }
        _maybe_print_field cluster "placed on cluster" seen_cluster
        _maybe_print_field namespace "namespace" seen_namespace
        _maybe_print_field hubCrName "hub CR name" seen_cr_name
        _maybe_print_field hubCrUid "hub CR uid" seen_cr_uid
        _maybe_print_field gitCommit "git commit" seen_commit

        case "$status" in
            RUNNING)
                echo "${C_GREEN}${C_BOLD}✔ '${tenant_id}' RUNNING${C_RESET}"
                return 0
                ;;
            FAILED|DELETED)
                local err
                err=$(echo "$t" | jq -r '.errorMessage // "(no error message)"')
                echo "${C_RED}${C_BOLD}✘ '${tenant_id}' ${status}: ${err}${C_RESET}"
                return 1
                ;;
        esac

        sleep "$POLL_INTERVAL"
    done

    echo "${C_RED}${C_BOLD}✘ '${tenant_id}' timed out after ${TIMEOUT}s waiting for a terminal status${C_RESET}"
    return 1
}

total=0
ok=0

if [[ -n "$BATCH" ]]; then
    for ((i = 0; i < BATCH; i++)); do
        tid="${PREFIX}${i}"
        total=$((total + 1))
        if onboard_one "$tid" "${DISPLAY_NAME:-$tid Corp}" "${EMAIL:-admin@${tid}.example.com}" \
            "${PASSWORD:-StrongPassword123}" "${DOMAIN:-${tid}.example.com}"; then
            ok=$((ok + 1))
        fi
    done
else
    missing=()
    [[ -z "$TENANT_ID" ]] && missing+=(--tenant-id)
    [[ -z "$DISPLAY_NAME" ]] && missing+=(--display-name)
    [[ -z "$EMAIL" ]] && missing+=(--email)
    [[ -z "$PASSWORD" ]] && missing+=(--password)
    [[ -z "$DOMAIN" ]] && missing+=(--domain)
    if [[ ${#missing[@]} -gt 0 ]]; then
        echo "missing required argument(s): ${missing[*]} (or use --batch N instead)" >&2
        usage 1
    fi
    total=1
    if onboard_one "$TENANT_ID" "$DISPLAY_NAME" "$EMAIL" "$PASSWORD" "$DOMAIN" \
        "$APPLICATION" "$VERSION" "$TIER" "$USERS" "$DB_SIZE"; then
        ok=1
    fi
fi

echo
echo "${C_BOLD}=== ${ok}/${total} tenant(s) reached RUNNING ===${C_RESET}"
[[ "$ok" -eq "$total" ]]
