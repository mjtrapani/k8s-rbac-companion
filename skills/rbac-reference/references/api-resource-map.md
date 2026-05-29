# API-resource map (offline fallback)

The **authoritative** source for `resource → (apiGroup, namespaced?, verbs)` is the live cluster via `list_api_resources` (≈ `kubectl api-resources -o wide`). This table is the **offline fallback** — use it only when no cluster MCP is connected, and emit a *"version drift possible"* note (the apiGroup a resource lives in is version-sensitive — see `version-deltas.md`).

This map is *derivable*: `kubectl api-resources -o wide` on any cluster prints the same `NAME / APIVERSION / NAMESPACED / VERBS` columns this table summarizes. Regenerating it for a specific cluster version is a one-liner — that's the equivalent of pulling from an upstream source-of-truth rather than hand-curating.

**Standard verb set** for most resources: `create, delete, deletecollection, get, list, patch, update, watch`. Exceptions are called out in the Notes column.

## Core group (`apiGroups: [""]`)

The core group is the **empty string**, not `"v1"`.

| Resource | Short | Namespaced | Notes |
|----------|-------|------------|-------|
| `pods` | po | ✅ | subresources: `pods/log`, `pods/exec`, `pods/portforward`, `pods/eviction`, `pods/status` |
| `services` | svc | ✅ | `services/status` |
| `configmaps` | cm | ✅ | |
| `secrets` | | ✅ | **`view` ClusterRole deliberately excludes Secrets** — treat read access as sensitive |
| `serviceaccounts` | sa | ✅ | `serviceaccounts/token` (create) for TokenRequest; `serviceaccounts/impersonate` is the `impersonate` verb target |
| `persistentvolumeclaims` | pvc | ✅ | |
| `endpoints` | ep | ✅ | largely superseded by `endpointslices` (discovery.k8s.io) |
| `events` | ev | ✅ | controllers emitting events need `create` + `patch` here |
| `replicationcontrollers` | rc | ✅ | legacy; `replicationcontrollers/scale` |
| `limitranges`, `resourcequotas`, `podtemplates` | | ✅ | |
| `namespaces` | ns | ❌ (cluster) | verbs: create/delete/get/list/patch/update/watch — **no `deletecollection`** |
| `nodes` | no | ❌ (cluster) | `nodes/status`, `nodes/proxy` |
| `persistentvolumes` | pv | ❌ (cluster) | |

## `apps`

| Resource | Short | Namespaced | Notes |
|----------|-------|------------|-------|
| `deployments` | deploy | ✅ | `deployments/scale`, `deployments/status` |
| `statefulsets` | sts | ✅ | `statefulsets/scale`, `statefulsets/status` |
| `daemonsets` | ds | ✅ | `daemonsets/status` |
| `replicasets` | rs | ✅ | `replicasets/scale`, `replicasets/status` |
| `controllerrevisions` | | ✅ | used by StatefulSet/DaemonSet controllers |

## `batch`

| Resource | Short | Namespaced | Notes |
|----------|-------|------------|-------|
| `jobs` | | ✅ | `jobs/status` |
| `cronjobs` | cj | ✅ | `cronjobs/status` — **was `batch/v1beta1` before GA; see version-deltas** |

## `networking.k8s.io`

| Resource | Short | Namespaced | Notes |
|----------|-------|------------|-------|
| `ingresses` | ing | ✅ | **was `extensions`/`networking.k8s.io/v1beta1`; now v1 — see version-deltas** |
| `networkpolicies` | netpol | ✅ | |
| `ingressclasses` | | ❌ (cluster) | |

## `rbac.authorization.k8s.io`

| Resource | Namespaced | Notes |
|----------|------------|-------|
| `roles`, `rolebindings` | ✅ | granting write here is namespace-admin level; needs `bind`/`escalate` awareness |
| `clusterroles`, `clusterrolebindings` | ❌ (cluster) | |

## Other common groups

| Group | Resources | Namespaced | Notes |
|-------|-----------|------------|-------|
| `policy` | `poddisruptionbudgets` (pdb) | ✅ | **was `policy/v1beta1`; now v1**. (PodSecurityPolicy removed entirely in 1.25.) |
| `autoscaling` | `horizontalpodautoscalers` (hpa) | ✅ | prefer `autoscaling/v2`; `v1` still served |
| `storage.k8s.io` | `storageclasses` (sc), `volumeattachments`, `csidrivers`, `csinodes` | ❌ (cluster) | |
| `coordination.k8s.io` | `leases` | ✅ | **leader election** writes here — `get`/`create`/`update` on `leases` |
| `discovery.k8s.io` | `endpointslices` | ✅ | |
| `authorization.k8s.io` | `subjectaccessreviews`, `localsubjectaccessreviews`, `selfsubjectaccessreviews`, `selfsubjectrulesreviews` | mixed | **`create` only** (they're review requests, not stored objects) |
| `apiextensions.k8s.io` | `customresourcedefinitions` (crd) | ❌ (cluster) | a controller that *manages* CRDs needs this; one that *uses* a CR does not |
| `coordination`, `apiregistration.k8s.io`, `admissionregistration.k8s.io` | (leases, apiservices, *webhookconfigurations) | mixed | |

## Custom Resources (CRDs)

A CRD defines its own `group`, `version`, `plural` (the resource name), and `scope` (Namespaced or Cluster). Read those from the CRD manifest (`spec.group`, `spec.names.plural`, `spec.scope`) or live via `list_api_resources`. A rule for a CR looks exactly like a built-in:

```yaml
- apiGroups: ["example.com"]
  resources: ["widgets", "widgets/status"]
  verbs: ["get", "list", "watch", "update"]
```

## Built-in user-facing ClusterRole rule sets (summary)

From a live v1.x cluster (`kubectl get clusterrole <name> -o yaml`). Use these when the user opts for "bind to a built-in" instead of a custom Role:

- **`view`** — `get`/`list`/`watch` on most namespaced resources across core/apps/batch/networking/policy/autoscaling/discovery, plus `pods/log` and `*/status`. **Excludes Secrets, exec, and all writes.**
- **`edit`** — everything in `view` **plus** `create`/`update`/`patch`/`delete` on workload resources, `pods/exec`, `pods/attach`, `pods/portforward`, `serviceaccounts/impersonate`, and `leases`. Powerful — impersonation + exec.
- **`admin`** — everything in `edit` **plus** managing `roles`/`rolebindings` within the namespace. Namespace administration.
- **`cluster-admin`** — `apiGroups:["*"] resources:["*"] verbs:["*"]` + `nonResourceURLs:["*"]`. Superuser; never for a workload.

These are aggregated — extend them by creating a ClusterRole labeled `rbac.authorization.k8s.io/aggregate-to-{view,edit,admin}: "true"` rather than editing them in place.
