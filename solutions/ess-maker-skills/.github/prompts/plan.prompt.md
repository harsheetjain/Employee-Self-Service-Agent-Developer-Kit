---
mode: agent
description: "Type Enter to get a scenario rollout plan customized to your environment and grounded on Microsoft Learn"
---

# Plan

**Setup-state check.** Read `.local/config.json`.
If it does not exist, OR `setup` is not `"complete"`, show:

> Welcome to the ESS Maker Kit. Before running this command, type `/setup`
> to set up your environment.

and STOP. Otherwise proceed.

You are a script executor. Read `src/skills/plan/SKILL.md` and follow it. It
produces a scenario rollout plan that is (1) **enriched** by your tenant
inventory (`workspace/inventory/inventory.json`, from `/discover`) and (2)
**grounded on Microsoft Learn** via `src/reference/ess-learn-anchors.md`.

Rules:
1. Ground every setup recommendation on a Microsoft Learn page — read the
   vendored `src/reference/ess-docs/` copy first, then do a **live web search /
   fetch** of `learn.microsoft.com/.../employee-self-service/` this turn, and
   **cite the page**. Do NOT plan setup steps from memory.
2. NEVER fabricate or alter a URL. Only cite links you fetched this turn (2xx) or
   that already exist in the vendored docs.
3. Every readiness call must trace to a fact in the inventory (a connector, pack,
   knowledge source, or errored connection) — name the evidence. No generic
   catalogue dumps.
4. NEVER tell the user what files you are reading or what tools you are calling.
   Present the plan and its citations.
