# tenant-operator (Helm chart)

Deploys [tenant-operator](https://hub.docker.com/r/sandeepkosework/tenant-operator) —
a FastAPI service that automates tenant onboarding onto a hub-and-spoke
Kubernetes fleet entirely through GitOps: it writes a values.yaml file to a
git repo and lets Argo CD do the actual deploying.

## Install

```bash
helm install tenant-operator . \
  --namespace tenant-operator --create-namespace \
  -f my-values.yaml
```

## Key values

| Key | Purpose |
|---|---|
| `image.repository` / `image.tag` | Defaults to the public Docker Hub image `sandeepkosework/tenant-operator` |
| `environment` | `stage` or `prod` — one release serves exactly one |
| `config` | Any non-secret `Settings` field (`GIT_REPO_URL`, `ARGOCD_SERVER`, `VAULT_ADDR`, `DATABASE_URL`, ...), `UPPER_SNAKE` → ConfigMap → `envFrom` |
| `secret` / `existingSecretName` | Secret-shaped config (tokens) → Secret → `envFrom`. Prefer `existingSecretName` for anything real |
| `git.sshKey.existingSecretName` | SSH deploy key, if using `git@...` instead of an HTTPS token in `secret.GIT_HTTPS_TOKEN` |
| `kubeconfig.existingSecretName` | Only needed when the operator's target cluster(s) for tenant workloads aren't the cluster it runs on itself. Mounted at `/secrets/kube` — also set `config.KUBECONFIG_PATH` to the full path of the actual key inside that Secret (e.g. `/secrets/kube/config`) |
| `clustersConfig.inline` / `.existingConfigMapName` | Overrides the `clusters.yaml` baked into the image |
| `persistence.*` | Backs `GIT_LOCAL_PATH` (the operator's local git clone) |
| `sqlitePersistence.*` | Backs `DATABASE_URL` when using `sqlite:////data/db/...` (set accordingly in `config`) |
| `rbac.create` | Baseline cluster-scoped, read-only ClusterRole (see `templates/rbac.yaml` for the one deliberate `delete` verb on namespaces) |

See [TENANT_OPERATOR_REFERENCE.md](https://github.com/sandeepkosework) (companion doc) for the full config reference.

## Notes

- **Single replica by design** — `git_service.py`'s write lock and the
  Socket.IO status bus are both in-process only.
- `strategy: Recreate` is used deliberately (not `RollingUpdate`) — this
  operator's PVCs are `ReadWriteOnce`, and a surge pod briefly coexisting
  with the old one during a rolling update can deadlock on volume
  attachment, especially on constrained/single-worker-node clusters.
