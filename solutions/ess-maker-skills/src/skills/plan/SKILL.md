# Plan Script

Produces a **scenario rollout plan for the maker's ESS agent** that is (1)
**enriched** by the tenant inventory (from `/discover`), (2) **grounded on
Microsoft Learn** for setup detail, and (3) **persisted** as a living
`Plan.Scenarios[]` list (locally, and to the WeveNova Plan MCP when configured).

A deterministic engine does the scoping and readiness math; your job is the
reasoning around it — gather goals, run the engine, ground the setup steps on
Learn, present the plan, and refine it with the maker. Do **not** hand-compute
readiness or hardcode a phase list; the engine and the inventory decide that.

Do not narrate what files you read. Show the plan and cite Learn pages. Showing
the `python scripts/...` commands is fine (setup and connect do the same).

---

## Rules

- **Ground, don't guess.** Read `src/reference/ess-learn-anchors.md` and follow
  its order: vendored `src/reference/ess-docs/` first, then a **live web
  search/fetch** of `learn.microsoft.com/.../employee-self-service/` this turn.
  **Cite** every setup recommendation. Never fabricate or alter a URL.
- **Enriched, not generic.** Every readiness call comes from the engine, which
  traces it to a fact in the inventory. Don't restate scope as a generic
  catalogue dump.
- **Reason, don't script.** The plan's shape (unblock-first, reuse, pilot-gating,
  latent capability) emerges from the enriched plan — read it and explain it.

---

## Start

Record anonymous usage telemetry (best-effort, non-blocking — no user-facing
message, never fails the step): `python scripts/emit_capability.py plan`

**Prerequisite: the inventory.** Read `workspace/inventory/inventory.json`.

- If it does not exist, show:

  **Message:**

  I need to take stock of your environment first so the plan reflects what you
  actually have connected. Type `/discover` (about 2 minutes), then run `/plan`.

  **End message.**

  Stop here.

- If it exists but `mode` is `"baseline"`, tell the user the plan is based on a
  quick offline snapshot and offer to run `/discover` for a full crawl of live
  connections before planning. Continue if they want.

---

## Step 1 — Gather goals

Use the `vscode_askQuestions` tool to capture the rollout scope in one call:

```json
[
  { "header": "Focus", "question": "What should your agent focus on first?",
    "options": [ { "label": "HR" }, { "label": "IT" }, { "label": "Both" } ],
    "allowFreeformInput": false },
  { "header": "Systems", "question": "Which systems do you use? (pick any)",
    "options": [ { "label": "Workday" }, { "label": "ServiceNow" }, { "label": "SharePoint" }, { "label": "SAP SuccessFactors" } ],
    "allowFreeformInput": true },
  { "header": "Off the table", "question": "Anything to exclude for now? (e.g. profile writes, manager scenarios, handoff)",
    "allowFreeformInput": true }
]
```

Map the answers to CLI arguments:
- Focus → `--focus hr` / `--focus it` / `--focus hr,it`.
- Systems → `--systems` csv of `workday,servicenow,sharepoint,sap` (map SAP SuccessFactors → `sap`).

---

## Step 2 — Build and enrich the plan

Run (substitute a short plan name and the mapped args):

```
python scripts/build_plan.py --build --name "{plan name}" --focus {focus} --systems {systems}
```

The engine scopes scenarios from the catalogue, evaluates each one's
dependencies against your inventory, sets `readiness` (config / pilot /
production), and surfaces discovered suggestions. It writes
`workspace/plan/plan.json` and `workspace/plan/summary.md`.

If the maker named systems to exclude, note them — you'll present those scenarios
as deferred (they stay in the plan, disabled, so they can be re-added later).

---

## Step 3 — Ground the gaps on Learn

Read `workspace/plan/plan.json`. For each scenario whose `verdict` is `blocked`,
`at-risk`, or `partial`, and for each `dependencies[].need` that isn't met:

1. Read the vendored `ess-docs/` page first (mapping in `ess-learn-anchors.md`).
2. Then **do a live web search/fetch** of the `/employee-self-service/` Learn
   page for the fix — especially the Phase-0 blockers (add a SharePoint
   knowledge source, deploy ServiceNow Graph knowledge, re-auth a connection,
   configure Now Assist). Fetch it this turn; capture the URL to cite.

---

## Step 4 — Present the plan

Compose a **Message** from `plan.json` (this is your own composed output — the
content is the maker's plan). Structure it:

1. **What's ready now** — scenarios with `verdict: "ready"`, grouped by category.
2. **Phase 0 — unblock first** — the `blocked` / `at-risk` scenarios and the
   single fixes that unlock the most (missing SharePoint, an erroring connection,
   an unindexed knowledge source). Give each fix a **Learn citation you fetched
   this turn** and the owner role.
3. **Reuse wins** — where the connector + template configs already exist so it's
   topic-authoring only.
4. **You could also add** — the discovered suggestions (`origin: "discovered"`,
   `enabled: false`).
5. **Deferred / gated** — `partial` scenarios waiting on a manual gate (governance
   sign-off, manager context).
6. **Readiness at a glance** — the counts (ready / at-risk / partial / blocked)
   and the `evalState`.

Keep it scannable. Do not expose file names, JSON, or tool names in the prose.

---

## Step 5 — Persist and refine

- **Persist to WeveNova** if the Plan MCP is available (a dev tunnel sets
  `WEVENOVA_MCP_URL`): run `python scripts/build_plan.py --sync`, or call the
  WeveNova plan tool directly. If it isn't configured, the plan stays in
  `workspace/plan/` — say so plainly.
- **Refine on request.** If the maker changes scope, re-run `--build` (or patch
  and `--enrich`). Disabling a scenario keeps it (disabled), so "add it back
  later" is a re-enable.
- **Living update.** Any time the environment changes, re-run `/discover` then
  `python scripts/build_plan.py --enrich` — the same plan re-scores itself
  (`unmet → at-risk → ready`) with no re-scoping.

End by offering to go deeper on a phase, accept a suggestion, or run `/create`
to start building the first ready scenario.
