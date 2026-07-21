# Step 2: Capture intent (what the maker wants to add)

Every **Message** block is the exact text to show the user. Copy it verbatim.
Do not rephrase or narrate what files you read.

This step handles the case the user asked about: discovery found no ServiceNow
(or Workday / SAP / SharePoint), so we ask what they want the agent to do, record
that intent, and — where a setup flow exists — offer to configure it now.

---

## 2.1 — Work out what's missing

Read `workspace/inventory/inventory.json`. From `capabilityMatrix[]`, collect the
systems named in the `missing` fields of any category whose `verdict` is
`blocked` or `partial`. Map them to this offer list (only include a system if it
is not already fully connected):

- **ServiceNow** — HR cases / IT tickets, and ServiceNow knowledge search
- **Workday** — employee profile read/write (pay, job, time off)
- **SAP SuccessFactors** — employee profile + manager scenarios
- **SharePoint** — policy/how-to knowledge for deflection

If every category is already `ready`, skip to 2.4 (nothing to add).

---

## 2.2 — Ask what they want to use

Use the `vscode_askQuestions` tool with one multi-select question. Include only
the systems from 2.1 that aren't connected:

```json
[
  {
    "header": "What should your agent do?",
    "question": "Which of these do you want your agent to use? (pick any — you can add more later)",
    "options": [
      { "label": "ServiceNow — HR cases / IT tickets" },
      { "label": "Workday — employee profile & pay" },
      { "label": "SAP SuccessFactors — profile & manager scenarios" },
      { "label": "SharePoint — policy & how-to knowledge" },
      { "label": "Nothing right now — just show me what I have" }
    ],
    "allowFreeformInput": true
  }
]
```

For each system the user picks, record the intent by running (one command per
system, mapping to the token in parentheses):

- ServiceNow (`servicenow`), Workday (`workday`), SAP (`sap`) → `--kind connector`
- SharePoint (`sharepoint`) → `--kind knowledge`

```
python scripts/discover_inventory.py --add-intake <token> --kind <connector|knowledge> --notes "<what they said they want>"
```

If the user picked "Nothing right now," skip to 2.4.

---

## 2.3 — Offer to configure now

Only **ServiceNow** and **Workday** have a guided connection flow in this kit.
For each of those the user chose:

**Message:**

Want to set up **{system}** now? I can walk you through it, then fold the result
back into your inventory. (Or say "later" and I'll keep it on your plan.)

**End message.**

Wait for the user.

- **If they say yes** for ServiceNow or Workday: read `src/skills/connect/SKILL.md`
  and follow it with `PRE_SELECTED_INTEGRATION` set to that system. When the
  connect flow finishes and returns here, continue to Step 3 (it reconciles what
  `/connect` captured).
- **If they say later:** leave the intent as planned and continue.

For **SAP SuccessFactors** and **SharePoint** (no guided flow yet), show:

**Message:**

I've added **{system}** to your plan. It doesn't have a guided setup in the kit
yet, so it'll show up as a prerequisite when you plan scenarios. Your admin can
configure it in the Power Platform / Copilot Studio portal.

**End message.**

---

## 2.4 — Finish the step

Update `.local/discover/tasks.md` — change step 2 from `- [ ]` to `- [x]`.

**Message:**

| # | Task | Status |
|---|------|--------|
| 1 | Environment crawled | ✅ |
| 2 | Intent captured | ✅ |
| 3 | Inventory finalized | ⬜ |

**End message.**

Now read `src/skills/discover/step3.md` and follow it.
