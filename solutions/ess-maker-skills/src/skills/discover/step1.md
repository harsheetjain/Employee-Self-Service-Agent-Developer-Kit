# Step 1: Crawl the environment

Every **Message** block is the exact text to show the user. Copy it verbatim.
Do not rephrase or narrate what files you read.

---

## 1.1 — Run the crawl

**Message:**

Taking stock of your environment now. A sign-in window may appear so I can read
your connections and knowledge sources — pick the same account you used during
setup. This takes about a minute...

**End message.**

Run this command in the terminal:

```
python scripts/discover_inventory.py --refresh
```

If the command reports that the Power Platform Admin API or Copilot Studio was
unavailable, that's fine — continue with whatever it did discover. (The inventory
still captures agents, topics, template configs, and anything `/connect` has
already recorded.)

---

## 1.2 — Present the findings

Read `workspace/inventory/inventory.json`. Present a summary to the user built
**only** from its fields. Use these sections (skip a section if it has no data):

1. **Connected systems** — from `connections[]`. One row per connection:
   connector, status, and instance URL when present. If empty, say
   "No external connections are set up yet."
2. **Installed capabilities** — from `extensionPacks`. For each pack with
   `installed: true`, name it (ServiceNow, include HRSD/ITSM; Workday; SAP).
3. **Knowledge sources** — from `knowledgeSources[]` (name + type). If empty,
   say "No knowledge sources are configured yet."
4. **What your agent can do today** — from `capabilityMatrix[]`. Render as a
   table with the category and a plain-language readiness:
   - `ready` → "✅ Ready"
   - `partial` → "🟡 Installed — connection not verified"
   - `planned` → "🛠️ Planned"
   - `blocked` → "⛔ Not set up"
   Include the `missing` items for anything not ready.

Present it in a **Message** block you compose from those fields (this is the one
place you compose text rather than copy a canned message — because the content is
the user's own data). Keep it scannable. Do **not** mention file names, JSON, or
tools.

After presenting, update `.local/discover/tasks.md` — change step 1 from `- [ ]`
to `- [x]`.

Then show:

**Message:**

| # | Task | Status |
|---|------|--------|
| 1 | Environment crawled | ✅ |
| 2 | Intent captured | ⬜ |
| 3 | Inventory finalized | ⬜ |

**End message.**

Now read `src/skills/discover/step2.md` and follow it.
