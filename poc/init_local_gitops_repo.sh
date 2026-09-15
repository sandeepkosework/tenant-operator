#!/usr/bin/env bash
# Sets up a local bare git repo to stand in for a real GitHub/GitLab repo,
# so you can POC the whole flow without any external git hosting.
#
# Usage: ./poc/init_local_gitops_repo.sh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BARE_REPO="${1:-$HOME/tenant-operator-poc/tenant-config-bare.git}"
WORKING_CLONE="${2:-$HOME/tenant-operator-poc/tenant-config-seed}"

echo "Bare repo:     $BARE_REPO"
echo "Working clone: $WORKING_CLONE"

rm -rf "$BARE_REPO" "$WORKING_CLONE"
mkdir -p "$(dirname "$BARE_REPO")"

# 1. The "remote" -- a bare repo on local disk, playing the role of GitHub.
git init --bare "$BARE_REPO"
git -C "$BARE_REPO" symbolic-ref HEAD refs/heads/main

# 2. A working clone to seed the initial structure.
git clone "$BARE_REPO" "$WORKING_CLONE"
cd "$WORKING_CLONE"
git config user.name "poc-seed"
git config user.email "poc-seed@example.com"

mkdir -p tenants applicationsets clusters/hub
cp -r "$ROOT_DIR/poc/chart-workplace" "$WORKING_CLONE/charts-workplace-tmp"
mkdir -p charts
mv "$WORKING_CLONE/charts-workplace-tmp" charts/workplace

touch tenants/.gitkeep
cat > applicationsets/workplace.yaml <<'EOF'
# Applied to Argo CD once, by hand, in step 4 of poc/POC.md.
# Kept here too so it's version-controlled alongside the rest of the platform.
apiVersion: argoproj.io/v1alpha1
kind: ApplicationSet
metadata:
  name: workplace
  namespace: argocd
spec:
  generators:
    - git:
        repoURL: REPLACE_WITH_BARE_REPO_PATH
        revision: main
        files:
          - path: "tenants/*.yaml"
  template:
    metadata:
      # NOT {{path.basenameNormalized}} -- for the git FILES generator that
      # resolves to the containing DIRECTORY name ("tenants"), identical for
      # every file, which would collide every tenant onto one Application.
      # tenant.id comes from the matched file's own content instead.
      name: "{{tenant.id}}"
    spec:
      project: default
      source:
        repoURL: REPLACE_WITH_BARE_REPO_PATH
        targetRevision: main
        path: charts/workplace
        helm:
          valueFiles:
            # {{path.filename}} is the actual matched filename (e.g.
            # "acme.yaml") -- {{path.basename}} is also just the directory
            # name, same bug as the Application name above.
            - "../../{{path}}/{{path.filename}}"
      destination:
        server: https://kubernetes.default.svc   # single-cluster POC: hub == spoke
        namespace: "{{tenant.namespace}}"
      syncPolicy:
        automated:
          prune: true
          selfHeal: true
        syncOptions:
          - CreateNamespace=true
EOF

git checkout -b main
git add -A
git commit -m "poc: seed repo structure + toy workplace chart"
git push origin main

echo ""
echo "Done. Bare repo path to use as GIT_REPO_URL:"
echo "  $BARE_REPO"
echo ""
echo "Next: edit $WORKING_CLONE/applicationsets/workplace.yaml, replace"
echo "REPLACE_WITH_BARE_REPO_PATH with '$BARE_REPO', commit+push it, then"
echo "kubectl apply -f it against your kind cluster's argocd namespace."
