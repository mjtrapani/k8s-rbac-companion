---
description: Use when working with Kubernetes client code (client-go, controller-runtime, the kubernetes Python client, or kubectl), generating or reviewing Kubernetes RBAC (Role/ClusterRole plus RoleBinding/ClusterRoleBinding), or discussing RBAC rule syntax (apiGroups/resources/verbs/resourceNames). Provides the RBAC rule-grammar primer, the Role-vs-ClusterRole scope map, built-in and aggregated ClusterRoles, and pointers to detailed reference docs (API-resource map, version deltas, client-library patterns, resource extraction).
user-invocable: false
---

# rbac-reference

Knowledge base for Kubernetes Role-Based Access Control (RBAC). Model-invocable only — Claude auto-loads this skill when the conversation touches Kubernetes client code, RBAC rule construction, or RBAC syntax discussion. It's also loaded explicitly by the companion `k8s-rbac-companion:rbac-generator` agent at task start.

Why hidden from the user's slash menu: invoking a knowledge base via `/` isn't a meaningful action. When a user asks *"what verbs does `kubectl exec` need?"*, the skill description matches and Claude loads this content automatically — no slash command needed. For *actions* (generating a Role for a specific workload), use `/k8s-rbac-companion:rule <path>` instead.

## What lives here

- **Top-level orientation** (this file): the RBAC rule-grammar primer + the Role-vs-ClusterRole scope map + when to consult `kubectl api-resources` live
- **Reference docs** (`references/`): exhaustive lookup tables and case-handling guides — load on demand, not all at once

---

## RBAC rule grammar — the primitives

A Kubernetes RBAC policy is **two objects**: a **role object** that lists permissions, and a **binding** that grants that role to subjects. There is no inline credential anywhere — a binding references a ServiceAccount (or User/Group) *by name*. (This is the single biggest difference from secret-bearing access systems: the artifact this plugin emits is credential-free by construction.)

### The role object holds `rules`

Each rule is an additive grant (RBAC is **deny-by-default, allow-only** — there are no deny rules; you can only add permissions):

```yaml
rules:
- apiGroups: [""]                      # API group(s). "" (empty string) = the CORE group. NOT "v1".
  resources: ["pods", "pods/log"]      # resource(s) and/or subresource(s) (slash-delimited)
  verbs: ["get", "list", "watch"]      # operation(s) allowed
  resourceNames: ["my-pod"]            # OPTIONAL — restrict to named instances
- nonResourceURLs: ["/healthz"]        # OPTIONAL — cluster HTTP paths (ClusterRole only)
  verbs: ["get"]
```

| Field | Meaning | Hard constraints |
|-------|---------|------------------|
| `apiGroups` | Which API group(s) the rule covers. The **core group is the empty string `""`** (pods, services, configmaps, secrets, serviceaccounts, nodes, namespaces, PVs/PVCs, events…). Named groups: `apps`, `batch`, `networking.k8s.io`, `rbac.authorization.k8s.io`, `storage.k8s.io`, `policy`, `autoscaling`, plus any CRD group. | Required (≥1). `"*"` matches all — almost never least-privilege. |
| `resources` | Resource(s) and/or subresource(s) in those groups. Subresources use slash syntax: `pods/log`, `pods/exec`, `deployments/scale`, `<resource>/status`. | Required unless the rule uses `nonResourceURLs`. **Cannot mix `resources` and `nonResourceURLs` in the same rule.** |
| `verbs` | Operations allowed (see verb table below). | Required. `"*"` matches all. |
| `resourceNames` | Restrict the grant to named instances (e.g. one ConfigMap). | OPTIONAL. **Incompatible with `list`, `watch`, `create`, `deletecollection`** — the API server rejects such rules. Use only with `get`/`update`/`patch`/`delete`. |
| `nonResourceURLs` | Match cluster-level HTTP paths (`/healthz`, `/metrics`, `/version`, `/api/*`). | OPTIONAL. **ClusterRole only** — a namespaced Role rejects this field. Typically paired with `get`. |

### Verbs

| Class | Verbs | Notes |
|-------|-------|-------|
| Read | `get`, `list`, `watch` | `get` = one named object; `list` = a collection; `watch` = a change stream. An informer/cache-backed client needs **both `list` and `watch`**. |
| Write | `create`, `update`, `patch`, `delete`, `deletecollection` | `deletecollection` = bulk delete by selector (cannot be combined with `resourceNames`). |
| Special — auth | `impersonate` | On `users` / `groups` / `serviceaccounts` (core group). Lets the subject act as another identity. High blast radius. |
| Special — RBAC | `bind`, `escalate` | On `roles` / `clusterroles`. Part of privilege-escalation prevention (below). Grant deliberately, never as part of routine access. |
| Special — legacy | `use` | On `podsecuritypolicies` — **PSP was removed in k8s 1.25**; this verb is dead on modern clusters (Pod Security Admission replaced it). |
| Wildcard | `*` | All verbs. Reserved for superuser roles; never least-privilege. |

### Subresources

Subresources are granted **separately** from their parent — granting `pods` does NOT grant `pods/log`. Common ones and the verb they need:

| Subresource | Verb | Triggered by |
|-------------|------|--------------|
| `pods/log` | `get` | `kubectl logs`, reading container logs via API |
| `pods/exec` | `create` | `kubectl exec` (note: **create**, not get) |
| `pods/portforward` | `create` | `kubectl port-forward` |
| `pods/eviction` | `create` | voluntary pod eviction (drain) |
| `<workload>/scale` | `patch` / `update` | `kubectl scale`, HPA targets (`deployments/scale`, `statefulsets/scale`, `replicasets/scale`, `replicationcontrollers/scale`) |
| `<resource>/status` | `update` / `patch` | controllers writing `.status` via `client.Status().Update()/.Patch()` or clientset `UpdateStatus()` |

### Subjects (who the binding grants to)

```yaml
subjects:
- kind: ServiceAccount
  name: my-workload
  namespace: my-namespace
```

A ServiceAccount's internal username is `system:serviceaccount:<namespace>:<name>` — that's the form you pass to `kubectl auth can-i ... --as=system:serviceaccount:ns:name` to verify a rule. (Subjects can also be `User`/`Group`, but a workload authenticates as its ServiceAccount, which is this plugin's focus.)

### Privilege-escalation prevention (why `bind`/`escalate` exist)

The API server refuses to let a subject grant permissions it doesn't already hold:

- You can't create/modify a **Role/ClusterRole** containing permissions you lack unless you have `escalate` on roles/clusterroles.
- You can't create a **binding** to a role unless you hold the role's permissions, or have `bind` on it.

This is why a generated rule should never casually include `bind`/`escalate` — they're the keys that disable the guardrail.

---

## Role vs ClusterRole — the scope map

This is the structural decision the user must make (the analog of "which edition" in other domains): **namespaced or cluster-wide**, and it determines which two objects get emitted.

| Combination | Role object | Binding | Use when |
|-------------|-------------|---------|----------|
| **Namespaced** (default, most least-privilege) | `Role` (in namespace N) | `RoleBinding` (in N) | The workload accesses only namespaced resources within one (or a few) namespaces. Prefer this. |
| **Cluster-wide** | `ClusterRole` | `ClusterRoleBinding` | The workload needs cluster-scoped resources (`nodes`, `persistentvolumes`, `namespaces`, CRDs that are cluster-scoped), `nonResourceURLs`, or genuinely all-namespace access (e.g. a cluster-wide controller). |
| **Reuse cluster role, scope to one namespace** | `ClusterRole` | `RoleBinding` (in N) | A shared permission set defined once as a ClusterRole, but granted only within namespace N. Common for operators deployed per-namespace. |

Binding rules: a `RoleBinding` can bind **either** a `Role` or a `ClusterRole`; a `ClusterRoleBinding` can bind **only** a `ClusterRole`. Cluster-scoped resources and `nonResourceURLs` can only be granted via a `ClusterRole`.

### Built-in and aggregated ClusterRoles — the "custom vs built-in" decision

Every cluster ships four user-facing ClusterRoles. Binding to one of these instead of authoring a custom Role is the K8s replacement for "category-collapse" — it trades precision for brevity and standardization:

| Built-in | Grants | Trade-off |
|----------|--------|-----------|
| `view` | read-only (`get`/`list`/`watch`) on most namespaced resources (excludes Secrets, excludes exec/logs-write) | Broad read. Over-grants if the workload reads only a few resource types. |
| `edit` | `view` + create/update/patch/delete on most workload resources + `pods/exec`, `pods/portforward`, `serviceaccounts/impersonate` | Powerful; impersonation + exec are real risks. |
| `admin` | `edit` + manage `roles`/`rolebindings` in the namespace | Namespace administration; rarely what a workload needs. |
| `cluster-admin` | `*/*/*` + all `nonResourceURLs` | Superuser. Never bind a workload to this. |

These are **aggregated**: a ClusterRole with an `aggregationRule` auto-absorbs the rules of any ClusterRole labeled `rbac.authorization.k8s.io/aggregate-to-{view,edit,admin}: "true"`. That's how you *extend* a built-in (e.g. make CRD access show up in `view`) — you don't edit the built-in, you add a labeled ClusterRole.

**Default stance for this plugin: author a custom least-privilege Role** scoped to exactly the `(apiGroup, resource, verb)` tuples the code uses. Offer binding-to-a-built-in only when the user explicitly wants standardization over precision — and say plainly what it over-grants.

---

## `kubectl api-resources` — the live source of truth

When a cluster is connected via MCP, prefer **live introspection** over any baked-in table:

- `list_api_resources` (≈ `kubectl api-resources -o wide`) returns, *for this cluster's exact version*: each resource's `NAME`, `APIVERSION` (so you get the correct `apiGroup`), `NAMESPACED` (Role vs ClusterRole eligibility), and `VERBS` (the verbs that resource actually supports). This is authoritative — it includes installed CRDs and reflects version-specific group moves.
- `explain_resource` resolves a resource's group/subresource details when a method or kind is ambiguous.
- Read-only `kubectl auth can-i --list --as=system:serviceaccount:<ns>:<sa>` verifies what a subject can do *after* a rule is applied.

Fall back to the static `api-resource-map.md` reference (path below) only when no MCP connection is available, or for offline reasoning. When falling back, surface a *"version drift possible — connect a cluster for live verification"* note: the apiGroup a resource lives in is version-sensitive (see `version-deltas.md`), even though the rule grammar and verb set are stable.

---

## Reference docs — absolute paths, what's in each, when to consult

**Important for agents:** these reference docs ship inside this plugin and live in the plugin's installed cache directory. Use the **absolute paths below** when calling `Read`. Do NOT use relative paths like `references/api-resource-map.md` — those resolve against your current working directory (the user's repo), not the plugin bundle, and would fail or read the wrong file.

| Reference (absolute path) | When to consult |
|---------------------------|-----------------|
| `${CLAUDE_SKILL_DIR}/references/api-resource-map.md` | You need an offline `resource → (apiGroup, namespaced?, verbs)` lookup, or the built-in ClusterRole rule sets. The static fallback for `list_api_resources`. Common core/apps/batch/networking/rbac/policy/storage/autoscaling resources, each with its group and whether it's namespaced. |
| `${CLAUDE_SKILL_DIR}/references/version-deltas.md` | The target cluster version affects which `apiGroup` a resource lives in. Covers removed/migrated APIs (PSP removed 1.25, Ingress → `networking.k8s.io/v1`, CronJob → `batch/v1`, apps consolidation in 1.16, HPA `autoscaling/v2`). Notes honestly that RBAC's grammar/verbs are stable, so this matters far less than version deltas do in command-versioned systems. |
| `${CLAUDE_SKILL_DIR}/references/client-library-patterns.md` | You're reading source and need to map a client call to `(apiGroup, resource, verb)` — `client-go` clientset, `controller-runtime`, the kubernetes Python client, and `kubectl`. Includes the non-obvious cases: informer `List()` needs `list`+`watch`, `Status().Update()` → `<resource>/status`, `exec` → `create pods/exec`. |
| `${CLAUDE_SKILL_DIR}/references/resource-extraction.md` | You need to derive the rule fields from code: which resources/verbs, when to add `resourceNames`, which namespace(s), and how to handle subresources and fully-dynamic access. The difference between a real least-privilege boundary and an over-broad `*` grant. |

`${CLAUDE_SKILL_DIR}` is substituted to the absolute install path of this skill at load time (e.g., `~/.claude-personal/plugins/cache/k8s-rbac-companion/k8s-rbac-companion/<version>/skills/rbac-reference`).

---

## Companion: the `k8s-rbac-companion:rbac-generator` agent

If the user wants to **generate a complete RBAC manifest for a specific workload** (rather than discuss syntax in the abstract), invoke the `k8s-rbac-companion:rbac-generator` agent (plugin agents are dispatched via the fully-qualified namespaced name; the unqualified `rbac-generator` will not resolve). It:

- Scans the target codebase for Kubernetes API usage (client-go, controller-runtime, Python client, kubectl, Helm/Kustomize)
- Maps each call to `(apiGroup, resource, verb)` tuples, including subresources
- Asks the user for scope (Role vs ClusterRole + namespace), the target ServiceAccount, and custom-vs-built-in
- Synthesizes a least-privilege `Role`/`ClusterRole` + matching binding as annotated YAML
- (Optional, cluster MCP connected) Reads `list_api_resources` for live group/verb verification and validates with `--dry-run`

Invocation paths:
- Natural language: *"scope an RBAC role for ./my-controller"*
- Slash command: `/k8s-rbac-companion:rule <path>`
- Agent picker: `/agents` → `k8s-rbac-companion:rbac-generator`
