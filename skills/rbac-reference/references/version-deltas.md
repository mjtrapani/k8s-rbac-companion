# Version deltas

**Read this honesty note first.** In command-versioned systems, the *permission grammar itself* shifts between versions, so version filtering is load-bearing. **Kubernetes RBAC is the opposite:** the rule grammar (`apiGroups`/`resources`/`verbs`/`resourceNames`/`nonResourceURLs`), the verb set, and the RBAC objects themselves (`rbac.authorization.k8s.io/v1`) have been **stable and GA since k8s 1.17**. A rule you write today is valid on every supported cluster.

What *is* version-sensitive is narrower:

1. **Which `apiGroup` a resource lives in** (resources graduate from beta groups to GA groups).
2. **Whether a resource exists at all** (some are removed).

So this doc is a *much thinner* concern than version handling is elsewhere. The single best mitigation is to read the live cluster (`list_api_resources`) — it reports the correct, currently-served group for every resource, including CRDs. Use this static table only offline, and **under-include rather than guess a beta group**.

## API group migrations & removals

| Resource | Old group/version | Current (GA) group | Key version | Confidence |
|----------|-------------------|--------------------|-------------|------------|
| **PodSecurityPolicy** | `policy/v1beta1` | — **removed** | removed in **1.25** | HIGH — replaced by Pod Security Admission; the `use` verb on PSP is dead |
| **Ingress** | `extensions/v1beta1`, `networking.k8s.io/v1beta1` | `networking.k8s.io/v1` | GA 1.19; beta removed 1.22 | HIGH |
| **CronJob** | `batch/v1beta1` | `batch/v1` | GA 1.21; beta removed 1.25 | HIGH |
| **Deployment / DaemonSet / ReplicaSet / StatefulSet** | `extensions/v1beta1`, `apps/v1beta1`, `apps/v1beta2` | `apps/v1` | consolidated 1.16 | HIGH |
| **PodDisruptionBudget** | `policy/v1beta1` | `policy/v1` | GA 1.21; beta removed 1.25 | HIGH |
| **HorizontalPodAutoscaler** | `autoscaling/v1` | `autoscaling/v2` (v1 still served) | v2 GA 1.23 | HIGH — prefer v2 features; the apiGroup `autoscaling` is unchanged either way |
| **EndpointSlice** | `discovery.k8s.io/v1beta1` | `discovery.k8s.io/v1` | GA 1.21 | MEDIUM |
| **RBAC objects** (Role/RoleBinding/ClusterRole/ClusterRoleBinding) | — (always v1) | `rbac.authorization.k8s.io/v1` | GA since **1.17** | HIGH — **no migration; stable** |

## How this affects a generated rule

- Put the resource in its **current GA group**: Ingress → `networking.k8s.io` (not `extensions`); Deployment → `apps`; CronJob → `batch`; PDB → `policy`.
- If the codebase imports an old beta type (e.g. `networking.k8s.io/v1beta1` Ingress), the *rule's* `apiGroups` is still just the group name (`networking.k8s.io`) — RBAC rules grant on group, not group/version. The version only matters for whether the resource is served.
- Do **not** emit a rule for a removed resource (PSP) on a modern target. If the code references PSP, flag it rather than granting it.
- When unsure of the group on an unconnected/old cluster, prefer `list_api_resources` (live) over this table.
