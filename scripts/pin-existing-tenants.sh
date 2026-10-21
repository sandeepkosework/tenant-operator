#!/usr/bin/env bash
# One-time migration: pin every EXISTING tenant to a released chart version by adding
#   chartVersion: <tag>
# to its tenants/<slug>.yaml, in a local clone of the GitOps repo (k8s-infra-setup).
#
#   scripts/pin-existing-tenants.sh /path/to/k8s-infra-setup v0.1.0          # preview only
#   scripts/pin-existing-tenants.sh /path/to/k8s-infra-setup v0.1.0 --apply  # edit + commit
#
# - Run it BEFORE the ApplicationSet is changed to read chartVersion, so no tenant is left
#   without one. Pin them to the release they are running right now (the baseline tag).
# - Safe to run twice: files that already have chartVersion are left alone.
# - It commits locally and never pushes; push when you have looked at the result.
set -euo pipefail

REPO="${1:-}"; VERSION="${2:-}"; MODE="${3:-}"
if [ -z "$REPO" ] || ! [[ "$VERSION" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "usage: $0 <path to k8s-infra-setup clone> <vMAJOR.MINOR.PATCH> [--apply]" >&2; exit 2
fi
DIR="$REPO/tenants"
[ -d "$DIR" ] || { echo "no tenants/ directory in $REPO" >&2; exit 1; }
[ -z "$(git -C "$REPO" status --porcelain)" ] || { echo "refusing: $REPO has uncommitted changes" >&2; exit 1; }

todo=(); done_already=0
for f in "$DIR"/*.yaml; do
  [ -e "$f" ] || continue
  if grep -qE '^chartVersion:' "$f"; then done_already=$((done_already+1)); continue; fi
  if ! grep -qE '^argocdClusterServer:' "$f"; then
    echo "skipping $(basename "$f"): no argocdClusterServer line to anchor on" >&2; continue
  fi
  todo+=("$f")
done

echo "already pinned: $done_already   to pin to $VERSION: ${#todo[@]}"
for f in "${todo[@]}"; do echo "  would pin $(basename "$f" .yaml)"; done
[ "$MODE" = "--apply" ] || { echo "(preview only; add --apply to edit and commit)"; exit 0; }
[ "${#todo[@]}" -gt 0 ] || { echo "nothing to do"; exit 0; }

for f in "${todo[@]}"; do
  # insert right after the argocdClusterServer line, the same place the operator writes it
  sed -i -E "/^argocdClusterServer:/a chartVersion: ${VERSION}" "$f"
done
git -C "$REPO" add tenants
git -C "$REPO" commit -q -m "Pin existing tenants to chart ${VERSION}

Adds chartVersion: ${VERSION} to ${#todo[@]} tenant file(s) that had none. Every one of them runs
that release today, so this changes nothing for the tenants themselves."
echo "committed locally in $REPO. Nothing pushed."
