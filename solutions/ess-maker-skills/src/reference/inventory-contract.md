# Environment Inventory — shared contract

The **inventory** is a reusable, read-only snapshot of an ESS customer's Power
Platform environment: what connectors, extension packs, knowledge sources,
template configs, agents, and topics already exist, plus what the maker *plans*
to add. It is produced once by a discovery crawl and consumed by many surfaces,
so nothing has to re-crawl the environment.

- **Producer:** `scripts/discover_inventory.py` (orchestrates the collectors in
  `scripts/discovery/`).
- **Canonical instance:** `workspace/inventory/inventory.json` (machine-readable).
- **Human render:** `workspace/inventory/summary.md`.
- **JSON Schema:** `src/reference/inventory.schema.json` (`schemaVersion` 1).

Both files live under `workspace/` (git-ignored, per-environment). They are
*derived* state — safe to delete and regenerate.

## Why it exists (decoupling)

Before this, environment discovery was scattered:

- `setup` / `fetch_and_setup` extracted the **agent** (topics, template configs,
  workflows) into `workspace/agents/{slug}/` — but knew nothing about connectors
  or knowledge-source runtime state.
- `flightcheck` *did* discover connections, installed extension packs, and
  knowledge-source state — but only as **ephemeral pass/fail rows** in a
  readiness report, not a reusable artifact.

The inventory unifies these signals into one contract that the maker skills and
an external **scenario planner** can both read. It reuses the kit's existing,
verified API clients (`auth`, `flightcheck.pp_admin_client`,
`flightcheck.pva_client`) — it introduces **no new external API contract**.

## Two build modes

| Mode | Command | Auth | What it captures |
|------|---------|------|------------------|
| `baseline` | `--baseline` (run by `setup`) | none (on-disk only) | agents/topics/template configs, extension packs inferred from topic names, connections captured by `/connect`, local knowledge-source config |
| `full` | `--refresh` (run by `/discover`) | Dataverse + BAP + Island Gateway | everything in baseline **plus** live connection status, installed extension-pack flows, knowledge-source index state |

`baseline` guarantees an inventory always exists after setup without extra
sign-ins; `full` enriches it. A `mode: baseline` inventory carries a note telling
consumers to run `/discover` for live connector status.

## The intake state machine (maker intent)

Discovery is *reflective* (crawl what exists) **and** *intent-driven* (ask what
the maker wants to use that isn't there yet). When a system is absent, the
`/discover` skill records intent in `intake[]`:

```
(absent) --ask "do you want to use X?"--> planned
   planned --launch /connect X--> configuring --/connect captures config--> configured
   planned --maker declines--> declined
```

- `--add-intake <system> --notes "..."` records/advances an item.
- `--reconcile` re-reads what `/connect` persisted
  (`.local/config.json` `connections`, `.local/connect/*/config.json`) and flips
  matching `planned`/`configuring` items to `configured`.

This is how "we discovered no ServiceNow → ask → run `/connect servicenow` →
capture the instance URL/auth → store it back in the inventory" is modeled. The
`/connect` skill is the actuator; discovery only reconciles its captured output —
it never drives connector setup itself.

## The capability matrix (planner bridge)

`capabilityMatrix[]` maps each `ess-catalogue.md` category to a computed
readiness **verdict** from the connectors/packs/knowledge sources found:

| Verdict | Meaning |
|---------|---------|
| `ready` | Every required connector/knowledge source is present and connected. |
| `partial` | Installed but not fully connected (e.g. pack present, connection unverified). |
| `planned` | Not present, but an intake item covers it. |
| `blocked` | A required connector/knowledge source is absent and not planned. |

The catalogue still owns **priority and ordering** — the matrix only answers "is
the plumbing for this category present, planned, or absent?", which is exactly
the *connector readiness* the catalogue delegates to runtime. The category →
requirement map is a data-only mirror of the catalogue in
`scripts/discovery/capability.py`; keep the two in sync.

## Consumers

- **Scenario planner** (Cocreate `planner-execution`): reads `capabilityMatrix`
  for connector readiness and dependency gating, and `templateConfigs` /
  `connections` for what's already reusable.
- **`/create`**: reads `templateConfigs` + `connections` to pattern-match
  existing scenarios instead of re-querying Dataverse.
- **`/connect`**: reads `connections` to skip re-detection; its captured output
  is read back via `--reconcile`.
- **`setup`**: writes the baseline so the inventory exists from the start.

## Freshness

The inventory is a snapshot, not live state. Consumers should compare
`generatedAt` against `staleAfterHours` and prompt for a re-crawl when stale.
Re-run with `/discover` (or `--refresh`) any time the environment changes.

## Roadmap: WeveNova + MCP

Today the inventory is a local structured file the agent reads and refers to. The
intended evolution is to **persist inventory items in WeveNova** and expose
**CRUD over those items via an MCP server**, so the inventory becomes a
first-class, queryable store rather than a file. The schema in
`inventory.schema.json` is the contract that migration should preserve: the item
shapes (connections, extensionPacks, knowledgeSources, templateConfigs, intake,
capabilityMatrix) map directly onto WeveNova records + MCP resources. Keep
producers/consumers reading through this contract so the file → WeveNova/MCP swap
is transparent to them.
