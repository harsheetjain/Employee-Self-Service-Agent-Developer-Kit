# Scenario planner — contract & engine

The planner turns a sponsor's goals into a **tenant-grounded, self-updating
rollout plan**. It sits on top of the discovery inventory and, like discovery, is
built as a deterministic engine (Python) with a reasoning skill (`/plan`) on top.

- **Producer:** `scripts/build_plan.py` (orchestrates the `scripts/planner/` package).
- **Canonical local artifact:** `workspace/plan/plan.json` (+ `summary.md`).
- **Catalogue (data):** `src/reference/ess-scenario-catalogue.json`.
- **Source of truth (target):** the WeveNova Plan MCP; local staging until it lands.

## Pieces

| Module | Job |
|--------|-----|
| `planner/catalogue.py` | Load the scenario catalogue; map goals (focus + named systems) to scenarios. |
| `planner/plan_model.py` | The `Plan` + `Scenarios[]` data model, IO, and summary render. |
| `planner/readiness.py` | Evaluate each `dependencies[].check` against the inventory; roll up the `readiness` triplet. The living-plan core. |
| `planner/wevenova.py` | Config-gated, best-effort sync to the WeveNova Plan MCP (dev tunnel). |
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

## WeveNova sync (dev tunnel)

`wevenova.py` syncs the plan to a **local dev-tunnel MCP with no auth** when
configured, and is a no-op otherwise (the plan stays in `workspace/plan/`).

```
WEVENOVA_MCP_URL    the dev-tunnel MCP endpoint (streamable-HTTP JSON-RPC)
WEVENOVA_PLAN_TOOL  the persist tool (default plan.upsertPlan); the plan is passed as `plan`
```

Expected of the MCP: JSON-RPC 2.0 `initialize` + `tools/call`, and a plan-persist
tool that upserts a `Plan` with an embedded `scenarios[]` list and returns the
stored id/version. It's fail-open: a tunnel that's down or shaped differently
returns `(False, message)` and never crashes the planner.

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
