# Client-library patterns

Map a source-code call to `(apiGroup, resource, verb)`. The convention "method ≈ verb, receiver ≈ resource" holds for ~90% of calls; the **caveats** section is where the other 10% lives — read it before finalizing a rule.

> Coverage honesty: `client-go` and `kubectl` mappings are HIGH confidence (verified against a live cluster + upstream docs). `controller-runtime`'s `list`+`watch` coupling and the Python client's method naming are documented but were not exercised end-to-end on a live controller — flag, don't silently bake, any call that depends on the MEDIUM-confidence rows.

## client-go (Go clientset) — HIGH

Call shape: `clientset.<Group><Version>().<Resource>(namespace).<Method>(...)`.

- Receiver → resource + group: `CoreV1().Pods(...)` → (`""`, `pods`); `AppsV1().Deployments(...)` → (`apps`, `deployments`); `BatchV1().Jobs(...)` → (`batch`, `jobs`); `RbacV1().Roles(...)` → (`rbac.authorization.k8s.io`, `roles`).
- Cluster-scoped receivers omit the namespace arg: `CoreV1().Nodes()`, `StorageV1().StorageClasses()`.

| Method | Verb |
|--------|------|
| `Get` | `get` |
| `List` | `list` |
| `Watch` | `watch` |
| `Create` | `create` |
| `Update` | `update` |
| `UpdateStatus` | `update` on **`<resource>/status`** |
| `Patch` | `patch` |
| `Delete` | `delete` |
| `DeleteCollection` | `deletecollection` |

## controller-runtime (Kubebuilder) — verb logic HIGH, list+watch coupling MEDIUM

Generic typed client `c`; the resource is inferred from the typed object passed (`&corev1.PodList{}` → `pods`, `&appsv1.Deployment{}` → `deployments`).

| Method | Verb(s) |
|--------|---------|
| `c.Get(...)` | `get` |
| `c.List(...)` | `list` **+ `watch`** ⚠️ (caches/informers establish a watch; granting only `list` causes a silent cache-never-syncs failure) |
| `c.Create(...)` | `create` |
| `c.Update(...)` | `update` |
| `c.Patch(...)` | `patch` |
| `c.Delete(...)` | `delete` |
| `c.DeleteAllOf(...)` | `deletecollection` |
| `c.Status().Update(...)` / `c.Status().Patch(...)` | `update`/`patch` on **`<resource>/status`** |

Controller-specific signals:
- `Owns(&Foo{})` / `Watches(&Foo{})` / `For(&Foo{})` in `SetupWithManager` → the controller establishes informers → needs `get`+`list`+`watch` on `foos`.
- `controllerutil.SetControllerReference(...)` and `AddFinalizer(...)` modify `.metadata` → `update`/`patch` on the **main** resource. Some controllers also need `<resource>/finalizers` when the API server enforces finalizer permissions on owner refs (MEDIUM — flag if you see explicit finalizer subresource usage).
- **Kubebuilder RBAC markers** are a first-party signal: `// +kubebuilder:rbac:groups=apps,resources=deployments,verbs=get;list;watch;create;update;patch;delete`. If present, treat them as the author's declared intent and reconcile your inferred rule against them (they're generated into `config/rbac/role.yaml`).

## kubernetes Python client — MEDIUM

Method naming: `<verb>_[namespaced_]<resource>` on an `Api` object (`CoreV1Api`, `AppsV1Api`, `BatchV1Api`, …).

| Method prefix | Verb |
|---------------|------|
| `read_*` | `get` |
| `list_*` (incl. `list_*_for_all_namespaces`) | `list` |
| `create_*` | `create` |
| `replace_*` | `update` |
| `patch_*` | `patch` |
| `delete_*` | `delete` |
| `delete_collection_*` | `deletecollection` |
| `*_status` suffix (e.g. `patch_namespaced_pod_status`) | on `<resource>/status` |
| `watch.Watch().stream(api.list_*)` | adds `watch` |

Group from the Api class: `CoreV1Api` → `""`, `AppsV1Api` → `apps`, `BatchV1Api` → `batch`, `NetworkingV1Api` → `networking.k8s.io`, `CustomObjectsApi` → group is a runtime argument (flag — see resource-extraction).

## kubectl (scripts, Makefiles, entrypoints) — HIGH

| Command | Verbs (resource / subresource) |
|---------|-------------------------------|
| `kubectl get` / `describe` | `get`, `list` |
| `kubectl watch` / `get -w` | `list`, `watch` |
| `kubectl create -f` | `create` |
| `kubectl apply -f` | `create`, `get`, `patch` (server-side merge needs read+patch for idempotence) |
| `kubectl delete` | `delete` |
| `kubectl edit` | `get`, `update` |
| `kubectl patch` | `patch` |
| `kubectl logs` | `get` on `pods/log` |
| `kubectl exec` | **`create`** on `pods/exec` |
| `kubectl port-forward` | `create` on `pods/portforward` |
| `kubectl scale` | `patch`/`update` on `<resource>/scale` |
| `kubectl auth can-i` | none needed by the subject (it's a self-check via `selfsubjectaccessreviews`, `create`) |

## Caveats (the 10%)

- **Dynamic / unstructured client** (`dynamic.Interface`, `CustomObjectsApi`, `unstructured.Unstructured`): the resource/group is often a runtime value (a `schema.GroupVersionResource` built from variables). You usually can't statically resolve it — **flag for the user** rather than guessing.
- **Leader election** (`leaderelection` / `resourcelock`): needs `get`/`create`/`update` on `leases` (`coordination.k8s.io`), historically also `configmaps`/`endpoints`. Very common in controllers — look for `leaderElection: true` or a `LeaseLock`.
- **Event recording** (`record.EventRecorder`, `Eventf`): needs `create` + `patch` on `events` (core group). Easy to miss because it's indirect.
- **TokenRequest / SA tokens**: `serviceaccounts/token` (`create`) — distinct from reading the SA.
- **`SubjectAccessReview` self-checks**: a workload that calls `SelfSubjectAccessReview` needs `create` on `selfsubjectaccessreviews` (`authorization.k8s.io`). Rarely intended; confirm.
- **Sharded/aggregated API access via a proxy**: `nodes/proxy`, `services/proxy` are separate subresource grants.
- **Informer factories** (`informers.NewSharedInformerFactory`): every informer started implies `list`+`watch` on that resource — trace which informers are actually started, not just constructed.
