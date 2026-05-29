# Resource extraction

How to turn call sites into rule fields. The goal is the difference between a real least-privilege boundary and an over-broad `*` grant. Build a `(apiGroup, resource, verb)` set first, then attach `resourceNames`/namespace/subresource refinements.

## The five fields, and where each comes from

| Rule field | Source in code |
|------------|----------------|
| `apiGroups` | The clientset group (`AppsV1()` → `apps`), the typed object's group, the Python `Api` class, the `kubectl <resource>` group, or a CRD's `spec.group`. **Core = `""`.** |
| `resources` | The receiver/object/method noun (`Pods` → `pods`). Plural, lowercase. |
| `verbs` | The method (see `client-library-patterns.md`). Dedup per resource. |
| `resourceNames` | Inferred (never asked): present only when code targets a **specific named object with a literal name** and the verb permits it. |
| namespace (→ Role vs ClusterRole input) | The `ns` argument / literal / config. Drives the scope question, not a rule field. |

## resourceNames — when to add it (inference rule)

Add `resourceNames: ["<name>"]` to tighten a rule **only** when BOTH hold:

1. The code accesses a **specific named object via a string literal** — e.g. `CoreV1().ConfigMaps(ns).Get(ctx, "my-feature-flags", ...)`, or `kubectl get secret my-tls-cert`.
2. The verb is one of `get`, `update`, `patch`, `delete` (and **collect them into the same rule** as that name).

**Never** add `resourceNames` alongside `list`, `watch`, `create`, or `deletecollection` — the API server rejects the rule (you can't name an object you're enumerating or creating). If the same resource is both `list`ed and `get`-by-name, emit **two rules**: a broad `list`/`watch` rule (no `resourceNames`) and a tight `get`/`update` rule with `resourceNames`.

If the name is dynamic (built from a variable), you cannot scope by name — leave `resourceNames` off and grant the verb on the whole resource type.

## Namespace inference (drives the scope question, not the rule)

- Literal namespace (`Pods("kube-system")`) → suggests a namespaced Role in that namespace; surface it as the default for the scope question.
- Namespace from a variable / `os.Getenv("POD_NAMESPACE")` / downward API → "own namespace" pattern; namespaced Role in the deploy namespace.
- `*_for_all_namespaces` / empty namespace / cluster-scoped resource (`nodes`, `persistentvolumes`) → requires a ClusterRole; flag it.
- Mixed namespaces → ClusterRole + (one or more) RoleBindings, or a ClusterRoleBinding. Ask.

## Subresources

Don't fold subresources into the parent — they're separate `resources` entries:

| Code signal | Rule entry |
|-------------|-----------|
| `kubectl logs`, read pod logs | `pods/log` + `get` |
| `kubectl exec`, exec into pod | `pods/exec` + `create` |
| `kubectl port-forward` | `pods/portforward` + `create` |
| `client.Status().Update/Patch`, `UpdateStatus`, `patch_*_status` | `<resource>/status` + `update`/`patch` |
| `kubectl scale`, HPA on a workload | `<workload>/scale` + `patch`/`update` |

A rule needs the **parent** resource grant too if the code also reads/writes the parent object (e.g. a controller that `Get`s the Deployment AND updates its `/status` needs both `deployments` and `deployments/status`).

## Common implicit needs (look for these even with no direct call)

- **Leader election** → `leases` (`coordination.k8s.io`): `get`/`create`/`update`.
- **Event recording** → `events` (`""`): `create`/`patch`.
- **Informers/caches** → every started informer's resource needs `list`+`watch` (not just `list`).

## Dedup, sort, and the consistency check

1. **Deduplicate** `(group, resource, verb)` tuples; merge verbs for the same `(group, resource[, resourceName])` into one rule's `verbs` list (sorted).
2. **Group rules** by `apiGroups` for readability; sort resources and verbs alphabetically for stable output.
3. **Consistency check (mandatory before emitting):** every verb you grant must have a matching `(apiGroup, resource)`; every resource the code touches must appear in some rule. A classic bug is granting a subresource verb (e.g. `pods/exec` create) while forgetting the parent, or listing a resource in `resources` whose group is wrong (e.g. putting `ingresses` under `""` instead of `networking.k8s.io`). Cross-check each resource's group against `api-resource-map.md` (or live `list_api_resources`).

## Fully-dynamic / undeterminable access — flag, don't guess

If a call uses the dynamic client with a runtime-built `GroupVersionResource`, a reflection-based wrapper, or a resource name assembled from request data, you **cannot** statically derive the grant. Record it as a flagged item for the user (file:line + what's dynamic) instead of falling back to a wildcard. A `*` grant defeats the entire purpose — surfacing the gap honestly is better than a false least-privilege rule.
