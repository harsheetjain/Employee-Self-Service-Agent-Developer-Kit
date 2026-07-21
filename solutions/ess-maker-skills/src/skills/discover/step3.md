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

## 3.2 — Show the finished inventory

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
| `/create` | Build a topic or workflow — I'll reuse what's already here |
| `/connect` | Connect another system (ServiceNow or Workday) |
| `/menu` | See everything I can help with |

**End message.**

Stop here.
