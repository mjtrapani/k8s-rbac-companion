# sample-controller

A representative `client-go` workload, used to demonstrate **k8s-rbac-companion**. It's a **read target**: point the plugin at this directory and it infers the least-privilege RBAC its ServiceAccount needs from the API calls in `main.go`.

> Building/running it would need `go mod tidy` + an in-cluster context. That's **not** required to generate or validate the rule — the plugin reads the source, and `kubectl auth can-i` verifies the ServiceAccount's permissions without the workload running.

## Generate the rule

```text
/k8s-rbac-companion:rule examples/sample-controller
```

When asked, answer: scope **Namespaced Role + RoleBinding in `sample-system`**; ServiceAccount **`sample-controller` / `sample-system`**; role style **custom least-privilege**; and **leave out** the speculation TODO.

## What it should discover

| apiGroup | resource(s) | verb(s) | from |
|----------|-------------|---------|------|
| `""` (core) | `pods` | list, watch | `Pods(ns).List` / `.Watch` |
| `""` | `pods/log` | get | `Pods(ns).GetLogs(...)` |
| `""` | `configmaps` | get | `ConfigMaps(ns).Get("sample-controller-config")` → `resourceNames` |
| `""` | `secrets` | get | `Secrets(ns).Get("sample-controller-tls")` → `resourceNames` |
| `""` | `events` | create | `Events(ns).Create(...)` (event recording) |
| `apps` | `deployments` | list | `Deployments(ns).List` |
| `apps` | `deployments/scale` | get, update | `GetScale` / `UpdateScale("web")` |
| `coordination.k8s.io` | `leases` | get, create, update | leader-election lock in `acquireLease` |

Plus a `// TODO: also watch ConfigMaps` near the pod watch — the plugin should surface it as a **speculation candidate** and (by default) leave it out.

Note the two `resourceNames`-scoped grants: the ConfigMap and Secret reads target literal object names, so the rule can restrict them to exactly `sample-controller-config` / `sample-controller-tls` instead of granting read on every ConfigMap/Secret.

## Apply and verify end-to-end

```bash
# 1. Create the namespace + ServiceAccount the rule binds to
kubectl apply -f examples/sample-controller/deploy.yaml

# 2. Validate, then apply the generated rule (written to your cwd)
kubectl apply -f ./rbac-sample-controller.yaml --dry-run=client
kubectl apply -f ./rbac-sample-controller.yaml

# 3. Verify the ServiceAccount can do what the code needs ...
kubectl auth can-i list pods     --as=system:serviceaccount:sample-system:sample-controller -n sample-system   # yes
kubectl auth can-i create events --as=system:serviceaccount:sample-system:sample-controller -n sample-system   # yes

# 4. ... and nothing else
kubectl auth can-i delete pods   --as=system:serviceaccount:sample-system:sample-controller -n sample-system   # no
kubectl auth can-i get nodes     --as=system:serviceaccount:sample-system:sample-controller                    # no (cluster-scoped, never granted)
```

The ConfigMap/Secret grants are `resourceNames`-scoped, so a blanket `kubectl auth can-i get secrets` returns `no` — access is limited to the named objects only.
