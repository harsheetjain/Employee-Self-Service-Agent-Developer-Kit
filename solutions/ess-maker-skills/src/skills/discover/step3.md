# Step 3: Reconcile and finalize

Every **Message** block is the exact text to show the user. Copy it verbatim.
Do not rephrase or narrate what files you read.

---

## 3.1 — Reconcile

Pull in anything a connection flow captured and advance the plan:

```
python scripts/discover_inventory.py --reconcile
```

This re-reads what `/connect` recorded (instance URLs, auth, installed packs) and
flips any planned systems that are now connected to "configured."

---

## 3.2 — Persist to WeveNova (optional)

The inventory is always saved locally. When the WeveNova agent-configuration MCP
is wired up, also push it to the tenant so the planner and Cocreate can reuse it:

- **VS Code runtime (preferred):** the authorized `weve-agentconfig` MCP server
  can upsert each resource with `upsert_tenant_inventory` (one call per item:
  connectors, connections, extension packs, knowledge sources, template configs).
- **CLI:** with `WEVENOVA_MCP_URL` set, run `python scripts/discover_inventory.py --sync`.
  Headless CLI writes are unauthenticated and may return "not authorized" — that's
  expected; the inventory still stays intact in `workspace/inventory/`.

If WeveNova isn't configured, skip this silently — the inventory stays local and
everything downstream still works.

---

## 3.3 — Show the finished inventory

Read `workspace/inventory/inventory.json` and present a short wrap-up composed
from its fields:

1. **Ready now** — categories in `capabilityMatrix[]` with `verdict: "ready"`.
2. **On your plan** — `intake[]` items with status `planned` or `configuring`
   (name the system + their note).
3. If any category is still `blocked`, name what's missing.

Then update `.local/discover/tasks.md` — change step 3 from `- [ ]` to `- [x]`.

**Message:**

| # | Task | Status |
|---|------|--------|
| 1 | Environment crawled | ✅ |
| 2 | Intent captured | ✅ |
| 3 | Inventory finalized | ✅ |

Your environment inventory is ready. Here's what you can do next:

| Command | What it does |
|---------|-------------|
| `/plan` | Get a rollout plan for your agent, customized to what you have and grounded on Microsoft Learn |
| `/create` | Build a topic or workflow — I'll reuse what's already here |
| `/connect` | Connect another system (ServiceNow or Workday) |
| `/menu` | See everything I can help with |

**End message.**

Stop here.
