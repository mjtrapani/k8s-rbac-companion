---
description: Generate a least-privilege Kubernetes RBAC manifest (Role/ClusterRole + matching binding) for the codebase at the given path. Use when invoked via `/k8s-rbac-companion:rule <path>` or when the user explicitly asks to scope or generate RBAC / a Role / a ServiceAccount's permissions for a workload with a path argument. Orchestrates the `k8s-rbac-companion:rbac-generator` agent across two phases (discovery, synthesis) and gathers user input between them via `AskUserQuestion`.
---

# Generate a Kubernetes RBAC manifest for a workload

The user requested a rule for the path: `$ARGUMENTS`

## If `$ARGUMENTS` is empty or missing

Respond exactly with:

> The `/k8s-rbac-companion:rule` command needs a path argument.
>
> **Usage:** `/k8s-rbac-companion:rule <path>`
>
> **Example:** `/k8s-rbac-companion:rule ./my-controller`
>
> If you want a more conversational entry point, just say something like *"scope an RBAC role for ./my-controller"* and Claude will route you to the `k8s-rbac-companion:rbac-generator` agent.

Then stop. Do not proceed without a path.

---

## If `$ARGUMENTS` contains a path — the three-phase orchestration

**First, emit a brief greeting to the user (exactly one short line — no headers, no bullets):**

> 👋 Hi! I'm **k8s-rbac-companion**. Scanning `$ARGUMENTS` for Kubernetes API usage — I'll ask you a few questions, then emit a least-privilege Role/ClusterRole + binding as annotated YAML.

Then proceed to the three phases below — discovery (sub-agent), batched ask (`AskUserQuestion`), and synthesis (sub-agent). Do not deviate from the order. Do not skip phases. Do not attempt the analysis yourself outside these phase boundaries.

Why this structure exists: Claude Code sub-agents run single-shot — they can't pause mid-response to ask the user a question. So the interactive step lives in the skill (here, in the main conversation), via `AskUserQuestion`, between two stateless sub-agent dispatches.

### Phase 1 — Discovery (sub-agent)

Spawn the `k8s-rbac-companion:rbac-generator` sub-agent via the Task tool — pass `k8s-rbac-companion:rbac-generator` as the `subagent_type` (plugin agents require the fully-qualified namespaced name; the unqualified `rbac-generator` will not resolve). Use this prompt:

> **Mode: DISCOVERY ONLY.**
>
> Analyze the codebase at `$ARGUMENTS` for Kubernetes API usage. Run steps D1–D9 from your DISCOVERY mode. Then **return a structured discovery summary and stop. Do not ask any questions. Do not synthesize a manifest.**
>
> **Read only source files, package manifests, and deploy manifests (Helm/Kustomize).** Do NOT read service-internal prose docs (`README.md`, `CHANGELOG.md`, `LICENSE`, `CONTRIBUTING.md`) — they describe what the service does for end users, not how it talks to the Kubernetes API.
>
> **Lazy-load the `rbac-reference` skill** — only invoke it if you hit a non-obvious mapping (dynamic client with a runtime-built GroupVersionResource, controller-runtime informer/owner-ref subresource questions, leader-election/event-recorder implicit needs, an unfamiliar client library, or a subresource you're unsure of). For standard client-go / controller-runtime / Python-client / kubectl calls, your training data plus the mapping tables you already know are sufficient.
>
> Your structured summary must include:
> - **Client / framework** (name + how detected, e.g. `controller-runtime` from `sigs.k8s.io/controller-runtime` in go.mod + `client.Get(` call sites)
> - **API calls** (table: call site `file:line` → method → `(apiGroup, resource, verb)`). Include subresources as their own rows (`pods/log`, `<resource>/status`, etc.).
> - **resourceNames candidates** (table: resource → literal object name → call site, for accesses to a *specifically-named* object via a verb in {get,update,patch,delete}). These tighten the rule; you infer them, the skill does NOT ask about them.
> - **Namespaces touched** (literal namespaces, "own namespace" via downward API/env, all-namespaces, or cluster-scoped). This informs the scope question.
> - **Cluster-scoped signals** (any access to cluster-scoped resources like `nodes`/`persistentvolumes`/CRDs-cluster-scoped, `nonResourceURLs`, or all-namespace list/watch) — these force a ClusterRole.
> - **Implicit needs** (leader election → `leases`; event recording → `events`; informers started → `list`+`watch` on their resources).
> - **Speculation candidates** (TODO/FIXME near K8s calls implying a future verb/resource — e.g. `// TODO: also watch secrets`). For each: file:line, comment text, implied grant. Do NOT bake in.
> - **Cluster version** from `list_api_resources` / `kubectl version` if a cluster MCP is connected (else: "MCP not connected").
> - **Mapping notes** (dynamic/unstructured access you can't statically resolve, or any ambiguous method→grant mapping you flagged).
>
> Permitted tools: `Read`, `Grep`, `Glob`, `Skill` (to load `rbac-reference` once, if needed), `mcp__plugin_k8s-rbac-companion_kubernetes__list_api_resources` and `__explain_resource` (read-only, once each), `WebFetch` (one-shot, for an ambiguous client-library mapping). Forbidden: `Bash`, and every MUTATING Kubernetes MCP tool (`kubectl_apply`, `kubectl_create`, `kubectl_delete`, `kubectl_patch`, `kubectl_scale`, helm/cleanup, `port_forward`).

Wait for the agent's return. Read the discovery summary carefully — you'll use it to set the Phase 2 question defaults and pass it back to the agent in Phase 3.

### Phase 2 — Batched ask (AskUserQuestion)

Use the `AskUserQuestion` tool to ask the user the **three baseline questions at once, plus a 4th speculation question only if Phase 1 surfaced speculation candidates**. This is the only Claude Code primitive that actually pauses the conversation for structured input — natural-language "wait for the user" instructions don't enforce a pause.

**Rule for option ordering: the recommended / safest / most-common option ALWAYS goes first** (the UI cursor defaults to the first option). Set the recommendation from the discovery findings, as noted per-question.

**Q1 — Scope (Role vs ClusterRole):**
- header: "Scope"
- question: "What scope should this RBAC have?"
- **If discovery found ANY cluster-scoped signal, the ClusterRole option MUST be first and the question text should say a namespaced Role can't grant the cluster-scoped resources found.** Otherwise the namespaced Role is first (the least-privilege default).
- options (order per the rule above):
  - label: "Namespaced Role + RoleBinding" — description: "A `Role` + `RoleBinding` in a single namespace. Tightest scope. Correct when the workload only touches namespaced resources in one namespace."
  - label: "ClusterRole + ClusterRoleBinding" — description: "Cluster-wide. Required for cluster-scoped resources (nodes, PVs, cluster CRDs), `nonResourceURLs`, or genuine all-namespace access."
  - label: "ClusterRole + RoleBinding (reuse in one namespace)" — description: "Define the permission set once as a ClusterRole, grant it only within one namespace. Common for operators deployed per-namespace."

**Q2 — Target ServiceAccount (name + namespace):**
- header: "ServiceAccount"
- question: "Which ServiceAccount should the binding grant to? (the workload authenticates as this SA)"
- options:
  - label: "`<basename>` in `<namespace>`" — description: "Suggested name = the repo directory basename `<basename>`; namespace = the one discovery saw (or `default`). Pick **Other** to type a different `name` / `namespace`."  *(Set `<basename>` to the basename of `$ARGUMENTS`; set `<namespace>` to the discovered namespace or `default`.)*
  - label: "`default` in `<namespace>`" — description: "Bind the namespace's `default` ServiceAccount. Usually a smell — prefer a dedicated SA — but offered for quick local testing."
- The user will typically use **Other** to enter the real `name`/`namespace`. Treat their answer as authoritative.

**Q3 — Custom Role vs built-in ClusterRole:**
- header: "Role style"
- question: "Author a custom least-privilege Role, or bind to a built-in ClusterRole?"
- options:
  - label: "Custom least-privilege Role (recommended)" — description: "Grants exactly the `(apiGroup, resource, verb)` tuples discovery found — nothing more. Tightest, and the rule explains itself."
  - label: "Bind to a built-in ClusterRole" — description: "Bind to `view`/`edit`/`admin` instead of a custom role. Standardized, but over-grants — `view` reads everything, `edit` includes exec + impersonation. I'll ask which one and call out what it over-grants."

If the user picks "Bind to a built-in", fire a **follow-up** `AskUserQuestion` for which built-in (`view` / `edit` / `admin`; order `view` first as least-privilege), and skip the custom-role synthesis path — Phase 3 emits only the binding to that built-in.

**Q4 (conditional — only if Phase 1 surfaced speculation candidates):**
For each candidate, add a question. **Leave out first — it's the safer default.** Example for a `// TODO: also watch secrets` at controller.go:88:
- header: "Speculation"
- question: "I noticed `// TODO: also watch secrets` at controller.go:88, implying a future `watch secrets` grant. Include it now or leave it out?"
- options:
  - label: "Leave out (recommended)" — description: "Stricter least-privilege. Re-run me when it's actually wired up."
  - label: "Include now" — description: "Grant `get`/`list`/`watch` on `secrets`. Covers the planned addition without re-running."

**Calling `AskUserQuestion`:** the platform limit is 4 questions per call. Plan:
- **No speculation candidates (common):** one call = Q1, Q2, Q3. Then a follow-up call ONLY if Q3 = built-in (the which-built-in question).
- **Speculation candidates present:** one call = Q1, Q2, Q3, Q4 (or batch multiple Q4s across a follow-up if there are several). Then the built-in follow-up if applicable.

Do not narrate before/after the calls — `AskUserQuestion` renders its own UI; wrapping it in "I'll now ask…" / "thanks!" just adds noise.

### Phase 3 — Synthesis (sub-agent)

Spawn the `k8s-rbac-companion:rbac-generator` agent again (same dispatch rule as Phase 1 — fully-qualified `subagent_type`) with this prompt:

> **Mode: SYNTHESIS.**
>
> Here is the discovery summary from Phase 1:
>
> ```
> <paste the full Phase 1 return verbatim>
> ```
>
> The user answered:
> - Scope: <Q1 answer — Role+RoleBinding in ns / ClusterRole+ClusterRoleBinding / ClusterRole+RoleBinding in ns>
> - ServiceAccount: <name> in namespace <namespace>
> - Role style: <custom least-privilege | bind to built-in `<view|edit|admin>`>
> - Speculation: <left out | included: …> (per candidate, if any)
>
> Run S1 (resolve every `(apiGroup, resource, verb)` against `api-resource-map.md` / live `list_api_resources`, fixing groups per `version-deltas.md`), S2 (decide custom-vs-built-in per the user's answer — if built-in, emit only the binding), and S3 (compose the manifest). Emit the manifest for the user's chosen scope. Use the ServiceAccount name/namespace the user gave; name the role/binding after the SA (`<sa>-role` / `<sa>-rolebinding`, or `<sa>-clusterrole` / `<sa>-clusterrolebinding`).
>
> Required output sections, in order:
> 1. The complete manifest — `Role`/`ClusterRole` + matching binding — as one fenced ```yaml block on `rbac.authorization.k8s.io/v1`, with a `#` comment on each rule citing the source `file:line`(s) that justify it. (For a built-in binding: just the `RoleBinding`/`ClusterRoleBinding` referencing `view`/`edit`/`admin`.)
> 2. A per-rule annotation table (rule → grants → justified by file:line).
> 3. "Detected context" block (client/framework, scope, ServiceAccount, role style, cluster version + how known, MCP status, mapping/flag notes).
> 4. How to apply (`kubectl apply -f`, with a `--dry-run=client` step first) and how to verify (`kubectl auth can-i --list --as=system:serviceaccount:<ns>:<sa>`) and a negative test.
> 5. Do NOT auto-apply. The agent never runs `kubectl_apply`/mutating MCP tools — apply is the user's explicit action.
>
> Synthesis is **text-only composition** from the inputs above — no MCP calls. The agent's tool allowlist grants only read-only `list_api_resources` / `explain_resource`, so it cannot apply *or* dry-run; manifest validation is the user's job via the condensed prompt's `--dry-run=client` step.
>
> Forbidden tools: `Write`, `Edit`, `Bash`, and every mutating Kubernetes MCP tool — the agent is read-only by allowlist.

### After Phase 3 returns — write the `.yaml` AND emit a CONDENSED message (CRITICAL UX)

**Design note (domain adaptation):** redis-companion wrote its artifact to a `.md` and had the user `grep` the single-line rule out, because a long ACL line gets mangled by terminal copy-paste. A Kubernetes manifest is **multi-line YAML that `kubectl apply -f` consumes from a file natively** — so here the artifact IS the file, written as a directly-appliable `.yaml` with the annotations carried **inline as `#` comments** (kubectl ignores them). No grep extraction, no copy-paste of the manifest body. Same dual-output spirit (full artifact in a file + short apply command in the prompt), correct file format for the domain.

**Step 1 — Write `./rbac-<sa>.yaml` to the current working directory.**

Save the agent's complete manifest (verbatim, including the inline `#` comments) to `./rbac-<sa>.yaml` in cwd, where `<sa>` is the ServiceAccount name.

**Overwrite-safe procedure (mandatory order):**
1. Use `Glob` (pattern: `rbac-<sa>.yaml`) or `Read` on the path to check whether the file already exists from a prior run.
2. **If it exists**, call `Read` on it first (this satisfies the `Write` tool's same-session read-before-overwrite guard — without it, `Write` errors out and the user sees a noisy retry).
3. Call `Write` with the agent's verbatim manifest.

Critical: the file must be a clean, appliable manifest — valid YAML, documents separated by `---`, comments only on `#` lines. The agent's output template already produces this; preserve it exactly.

If `Write` fails or the user denies the permission prompt, fall back: tell the user the file write didn't happen and surface the manifest inline.

**Step 2 — Emit a CONDENSED user-facing message.** Do NOT re-emit the agent's full output. Keep it tight:

```markdown
✅ RBAC generated for ServiceAccount `<ns>/<sa>` (<scope>, <N> rules, least-privilege)

**Manifest:** `./rbac-<sa>.yaml` — open it for the per-rule rationale (inline comments) and detected context.

**Validate (no changes), then apply:**

\`\`\`
! kubectl apply -f ./rbac-<sa>.yaml --dry-run=client
\`\`\`
\`\`\`
! kubectl apply -f ./rbac-<sa>.yaml
\`\`\`

**Verify what the SA can now do:**

\`\`\`
! kubectl auth can-i --list --as=system:serviceaccount:<ns>:<sa>
\`\`\`

**Negative test — confirm an out-of-scope action is denied** (should print `no`):

\`\`\`
! kubectl auth can-i delete secrets --as=system:serviceaccount:<ns>:<sa> -n <ns>
\`\`\`
```

Each one-liner starts with `!` so Claude Code runs it directly on paste. Substitute `<ns>`, `<sa>`, `<scope>`, `<N>` from the answers/manifest, and pick a genuinely out-of-scope verb/resource for the negative test (one the rule does NOT grant).

---

## Important constraints

- **Never skip Phase 2.** Even if discovery seems to imply the answers, you MUST call `AskUserQuestion`. The user's answer is authoritative; your inference is not.
- **Never bake speculation candidates into the manifest** without asking Q4.
- **Never auto-apply.** The agent and skill never run `kubectl apply` (or any mutating MCP tool). Apply is the user's explicit action via the condensed prompt's commands.
- **Never widen to `*`.** If discovery flagged fully-dynamic access it couldn't resolve, surface it as a note for the user — do not paper over it with a wildcard grant.
- **Don't dump the Phase 1 summary to the user verbatim.** They see the questions (Phase 2) and the final manifest (Phase 3). Discovery is internal context.
- **Always re-emit (write + condense) the Phase 3 output** — the Task tool's return is collapsed in the user's UI by default.
