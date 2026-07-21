# Discover Script

Every **Message** block is the exact text to show the user. Copy it verbatim.
Do not rephrase, add commentary, or tell the user what files you are reading.
Showing the terminal commands in this skill (the `python scripts/...` lines) is
expected — the kit's setup and connect flows do the same.

This skill builds (or refreshes) the **environment inventory** — a reusable
picture of what connectors, extension packs, knowledge sources, template
configs, and topics already exist in the maker's environment, plus what they
plan to add. The inventory lives at `workspace/inventory/inventory.json`
(+ `summary.md`) and is consumed by `/create`, `/connect`, and the scenario
planner. See `src/reference/inventory-contract.md` for the contract.

---

## Start

Record anonymous usage telemetry (best-effort, non-blocking — no user-facing
message, and it never fails the step): `python scripts/emit_capability.py discover`

**Prerequisite check.** Read `.local/config.json`. If it does not exist or
`setup` is not `"complete"`, show:

**Message:**

Before I can take stock of your environment, we need to finish setup. Type
`/setup` — it only takes a couple of minutes — then run `/discover` again.

**End message.**

Stop here.

Otherwise, read `.local/discover/tasks.md`.

- If it does not exist, copy `src/skills/discover/tasks.md` to
  `.local/discover/tasks.md` and go to Fresh Start.
- If it exists and all items are checked, show the "Already discovered" message
  below, then go to Step 1 (a re-run always does a fresh crawl).
- If it exists with some items unchecked, show the checklist (✅/⬜) followed by
  "Picking up where we left off," and go to the first unchecked step.

**Already discovered — Message:**

I already have an inventory of your environment. Let's refresh it to pick up any
changes.

**End message.**

### Fresh Start

**Message:**

| # | Task | Status |
|---|------|--------|
| 1 | Environment crawled | ⬜ |
| 2 | Intent captured | ⬜ |
| 3 | Inventory finalized | ⬜ |

Let's take stock of your ESS environment — what's already connected, and what
you'd like to add. This takes about 2–3 minutes.

**End message.**

Go to Step 1.

---

## Step 1

Read `src/skills/discover/step1.md` and follow it.

## Step 2

Read `src/skills/discover/step2.md` and follow it.

## Step 3

Read `src/skills/discover/step3.md` and follow it.
