# Tenant Operator — Local POC

Proves out the whole loop — `POST /tenant` → Postgres row → git commit →
Argo CD sync → pod running → status flips to `RUNNING` — using:

- **kind** instead of your hub-and-spoke fleet (one cluster plays both roles)
- **a local bare git repo** instead of GitHub (no deploy keys needed)
- **docker-compose Postgres** instead of the Database VM
- **a toy Helm chart** (`poc/chart-workplace`, deploys `nginxdemos/hello`) instead of your real app

Nothing here talks to your real infra. Everything below runs on a laptop.

## Prerequisites

```bash
# macOS
brew install kind kubectl helm argocd

# Linux — see https://kind.sigs.k8s.io/docs/user/quick-start/ for kind,
# https://kubernetes.io/docs/tasks/tools/ for kubectl,
# https://helm.sh/docs/intro/install/ for helm,
# https://argo-cd.readthedocs.io/en/stable/cli_installation/ for argocd CLI
```

You also need Docker running (kind runs clusters as containers) and Python 3.12 for the operator itself.

## 1. Bring up the kind cluster

```bash
cd tenant-operator
kind create cluster --config poc/kind-config.yaml
kubectl config use-context kind-tenant-poc
```

## 2. Install Argo CD into it

```bash
kubectl create namespace argocd
kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml

# wait for it to come up
kubectl -n argocd rollout status deployment/argocd-server

# port-forward the API/UI
kubectl -n argocd port-forward svc/argocd-server 8080:443 &

# get the auto-generated admin password
kubectl -n argocd get secret argocd-initial-admin-secret \
  -o jsonpath='{.data.password}' | base64 -d; echo
```

Log in with the CLI (skip TLS verify since it's a local self-signed cert):

```bash
argocd login localhost:8080 --username admin --password <password-from-above> --insecure
```

Generate an API token the operator will use (read-only is enough, but the
default admin token is fine for a POC):

```bash
argocd account generate-token
```

Save that token — it's `ARGOCD_TOKEN` in the next step.

## 3. Start Postgres

```bash
docker compose -f poc/docker-compose.yml up -d
```

## 4. Seed the local "GitHub" repo

```bash
bash poc/init_local_gitops_repo.sh
# prints the bare repo path, e.g.
#   /Users/you/tenant-operator-poc/tenant-config-bare.git
```

Edit the seeded ApplicationSet to point at that real path, then push it and apply it to Argo CD:

```bash
SEED=~/tenant-operator-poc/tenant-config-seed
BARE=~/tenant-operator-poc/tenant-config-bare.git

sed -i.bak "s#REPLACE_WITH_BARE_REPO_PATH#$BARE#g" "$SEED/applicationsets/workplace.yaml"
cd "$SEED" && git add -A && git commit -m "poc: point ApplicationSet at real repo path" && git push
cd -

kubectl apply -f "$SEED/applicationsets/workplace.yaml"
kubectl -n argocd get applicationsets   # should show "workplace"
```

## 5. Get a kubeconfig for the operator to use

```bash
kind get kubeconfig --name tenant-poc > /tmp/poc-kubeconfig
```

(In the real fleet this would be a multi-context kubeconfig, one context per
spoke. For the POC there's just one: `kind-tenant-poc`, referenced as
`spoke-1`'s context in `poc/clusters.poc.yaml`.)

## 6. Configure and run the operator

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp poc/.env.poc.example .env
# edit .env: fill in GIT_REPO_URL (the bare repo path from step 4),
# ARGOCD_TOKEN (from step 2), and confirm KUBECONFIG_PATH matches step 5

uvicorn app.main:app --reload
```

Check it's alive:

```bash
curl localhost:8000/health
```

## 7. Create a tenant

```bash
curl -s -X POST localhost:8000/api/v1/tenant \
  -H 'Content-Type: application/json' \
  -d '{
        "tenantName": "company-a",
        "environment": "dev",
        "application": "workplace",
        "version": "1.0.0",
        "users": 10,
        "database": { "size": "1Gi" }
      }' | jq
```

You'll get back `{"tenantId": "...", "status": "PENDING"}` immediately. Poll it:

```bash
TENANT_ID=<paste from above>
watch -n2 "curl -s localhost:8000/api/v1/tenant/$TENANT_ID | jq"
```

Expect to see `status` walk through
`VALIDATING → GIT_COMMITTED → SYNCING → RUNNING` over roughly 10–30 seconds.

## 8. Verify independently of the operator

```bash
# the commit actually landed in "GitHub"
git -C ~/tenant-operator-poc/tenant-config-seed pull
cat ~/tenant-operator-poc/tenant-config-seed/tenants/company-a.yaml

# Argo CD picked it up
argocd app get company-a

# the pod is really running
kubectl -n company-a get pods
kubectl -n company-a port-forward svc/company-a 8081:80 &
curl localhost:8081   # nginxdemos/hello response
```

## 9. Try the failure/edge paths worth seeing once

```bash
# duplicate tenant name -> 400
curl -s -X POST localhost:8000/api/v1/tenant -d '{"tenantName":"company-a", ...}'

# capacity exhaustion -> FAILED with a clear error, no silent cluster creation
# (poc/clusters.poc.yaml caps spoke-1 at 5 tenants -- create 6 to see it)

# delete
curl -s -X DELETE localhost:8000/api/v1/tenant/$TENANT_ID | jq
# poll again -- status goes DELETING -> DELETED, namespace disappears:
kubectl get ns company-a   # should eventually 404
```

## Teardown

```bash
kind delete cluster --name tenant-poc
docker compose -f poc/docker-compose.yml down -v
```

## What this does and doesn't prove

**Proves:** the operator's actual state machine, git write path, Argo CD
polling, and Kubernetes health-check logic all work correctly together —
this is the real code, not a mock.

**Doesn't prove:** multi-cluster placement logic beyond the algorithm itself
(only one context is under test), real git auth (SSH/token, since the local
repo needs neither), or behavior under concurrent load — see the README's
"Operational notes" section for what changes before a production rollout.
