# Scenario planner — contract & engine

The planner turns a sponsor's goals into a **tenant-grounded, self-updating
rollout plan**. It sits on top of the discovery inventory and, like discovery, is
built as a deterministic engine (Python) with a reasoning skill (`/plan`) on top.

- **Producer:** `scripts/build_plan.py` (orchestrates the `scripts/planner/` package).
- **Canonical local artifact:** `workspace/plan/plan.json` (+ `summary.md`).
- **Catalogue (data):** `src/reference/ess-scenario-catalogue.json`.
- **Source of truth (target):** the WeveNova agent-configuration MCP; local staging until it lands.

## Pieces

| Module | Job |
|--------|-----|
| `planner/catalogue.py` | Load the scenario catalogue; map goals (focus + named systems) to scenarios. |
| `planner/plan_model.py` | The `Plan` + `Scenarios[]` data model, IO, and summary render. |
| `planner/readiness.py` | Evaluate each `dependencies[].check` against the inventory; roll up the `readiness` triplet. The living-plan core. |
| `planner/wevenova.py` | Config-gated, best-effort, fail-open sync to the WeveNova agent-configuration MCP (plan + tenant inventory). |
| `build_plan.py` | CLI: `--build` / `--enrich` / `--suggest` / `--sync` / `--print` / `--selftest`. |

## The plan (matches the scenario-list one-pager)

`workspace/plan/plan.json` is a `Plan` with an embedded `scenarios[]` list. Each
scenario entry:

```jsonc
{
  "id": "create-hr-ticket",
  "area": "HR Ticketing", "catalogRange": "#33",
  "jtbd": "Create HR support ticket", "persona": "Employee",
  "connector": "ServiceNow HRSD",
  "tier": 2, "priority": 2, "confidence": "Med", "deflectionType": "pre-ticket resolution",
  "origin": "sponsor",              // sponsor | discovered
  "enabled": true,                   // disable != delete
  "dependencies": [                  // each has a machine-checkable `check`
    { "kind": "servicenow", "need": "ServiceNow HRSD",
      "check": { "kind": "servicenow", "module": "hrsd" }, "status": "met" }
  ],
  "readiness": { "config": true, "pilot": true, "production": false },
  "verdict": "ready"                 // derived: ready | at-risk | partial | planned | blocked
}
```

The Plan also carries `goals`, `phase`, `roles[]`, `knowledgeSources[]`,
`evalState` (`Draft → ScenariosDefined → Configured → Ready-to-run`), and
`evaluatedAgainst` (`{environmentRef, inventoryVersion}` — what the readiness
reflects, for staleness).

## Dependency checks (what the engine evaluates)

`readiness.py` resolves each `check` against the inventory:

| `check.kind` | Shape | Resolves from |
|---|---|---|
| `connector` | `{anyOf:["workday","sap","sharepoint"]}` | connections + extension packs |
| `servicenow` | `{module:"hrsd"\|"itsm"\|"handoff"}` | ServiceNow connections + pack sub-flags |
| `knowledge` | `{anyOf:["SharePoint","ServiceNowGraph"]}` | knowledge sources (+ index state) |
| `builtin` | `{name:"Microsoft Self-Help"}` | always met (OOB) |
| `capability` | `{category:"HR Ticketing"}` | the inventory `capabilityMatrix` verdict |
| `scenario-edge` | `{needAnyCategory:[...]}` | another in-scope scenario being config-ready |
| `governance` / `context` | `{kind:"manual"}` | manual gate — `pending` until confirmed |

Status values: `met`, `at-risk` (present but unhealthy/unverified), `planned`
(maker intent recorded), `unmet`, `pending` (manual gate). Rollup:

- `config` = no hard dependency is `unmet` / `planned` / `pending`.
- `pilot` = `config` and nothing `at-risk` and all manual gates (governance,
  manager context) met.
- `production` = a manual go-live flag the owner sets (default false).

## WeveNova sync (agent-configuration MCP)

`wevenova.py` is a **config-gated, fail-open** client for the WeveNova
agent-configuration MCP (`WeveNovaB2`). It syncs when `WEVENOVA_MCP_URL` is set
and is a pure no-op otherwise — the plan and inventory always keep working from
their local `workspace/` mirrors.

```
WEVENOVA_MCP_URL       the MCP endpoint (streamable-HTTP JSON-RPC 2.0, SSE-framed).
                       Unset / "off" => local-only. For local dev, point it at the
                       dev tunnel; the VS Code runtime uses the `weve-agentconfig`
                       entry in `.vscode/mcp.json` instead (which also supplies auth).
WEVENOVA_PROJECT_NAME  the Cocreate project/experience name (default "Employee Self Serve").
```

**Transport.** JSON-RPC 2.0 over streamable-HTTP. Responses may arrive as an SSE
stream (`event: message\ndata: {json}`); `_parse_body` handles both raw JSON and
SSE. `initialize` / `tools/list` are reachable unauthenticated, but `tools/call`
requires an authorized caller — headless calls without a bearer token get
`"The caller is not authorized to perform this request"`. The VS Code MCP runtime
supplies that auth; the client is fail-open, so an unauthorized/absent/reshaped
endpoint returns `(False, message)` and never crashes the planner.

**Real tool contract (verified against the tunnel).**

| Purpose | Tool | Args (shape) |
|---------|------|-------------|
| Plan → project | `create_agent_project` | `{project:{name}}` — name is the project (`Employee Self Serve`). |
| Plan → plan+tasks | `create_agent_plan` | `{projectId, plan:{acceptanceCriteria[], tasks[{title,description,assignedToId}]}}`. |
| Inventory upsert | `upsert_tenant_inventory` | `{inventoryItem:{kind, naturalKey, displayName, source, attributes}}` — `attributes` is a JSON-**string**. |
| Inventory read (redacted) | `get_tenant_inventory_presence` | `{}` |
| Inventory list | `list_tenant_inventory` | OData `$filter`; item id is `{kind}:{naturalKey}`. |
| Inventory retire | `retire_tenant_inventory` | by `{kind, naturalKey}`. |

> **Impedance note.** The current WeveNova `Plan` has **no `Scenarios[]` field** —
> only `acceptanceCriteria[]` + `tasks[]`. `wevenova.py` therefore *encodes* the
> local `scenarios[]` into acceptance criteria + role-assigned tasks
> (`_plan_acceptance_criteria` / `_plan_tasks`) rather than a 1:1 upsert. Inventory
> `kind`s map to: Environment / EntraApp / Connector / Connection / SharePointSite /
> KnowledgeSource / ExtensionPack / ScenarioTemplate.

## Living-plan loop

```
/discover (updates inventory) → build_plan --enrich (re-scores checks) →
readiness rolls up → gaps become role-tagged tasks (Execution Plan capability) →
admin completes a task → inventory updates → re-enrich
```

Re-running `--enrich` after a `/discover` moves scenarios `unmet → at-risk →
ready` on the same persisted plan, with no re-scoping.

## Boundary

The planner owns the **scenario plan** only. Eval generation/persistence and the
role-routed **task** execution (`requiredRole`, assignment, inbox) are separate
capabilities (see the dev design and the scenario-list one-pager). This engine
stops at scope + readiness.

## Verify

```
python scripts/build_plan.py --selftest        # offline, deterministic
python scripts/build_plan.py --build --focus hr,it --systems workday,servicenow,sharepoint,sap
```
