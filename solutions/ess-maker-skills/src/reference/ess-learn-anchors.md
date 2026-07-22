# ESS Microsoft Learn anchors (grounding for planning & setup)

Grounding data for the planner/setup flows. Use it to **ground scenario plans
and setup guidance on Microsoft Learn**, not on memory.

This mirrors the "Grounding Priority" in `.github/copilot-instructions.md`. The
seed links below are **illustrative, not exhaustive** — they are entry points to
*search* from, not a fixed allow-list. Search Learn for the right page for the
scenario/connector at hand; don't assume these are the only pages.

## Grounding order (highest → lowest)

1. **Vendored snapshot** `src/reference/ess-docs/` — the kit's canonical copy
   (snapshot 2026-04-29). Read this FIRST; it's offline, stable, and trimmed for
   the kit. See the mapping table below.
2. **Live Microsoft Learn** — fetch/search `learn.microsoft.com/.../employee-self-service/`
   for anything not vendored, or to confirm the latest guidance. **Fetch the page
   THIS turn before citing a deep link.**
3. **General knowledge** — only when neither above answers, and say so.

If the vendored copy and the open web disagree, the vendored copy wins for this
kit — but **cite which one you used** so the maker can verify.

## Hard rules (match the kit's "No fabricated URLs" rule)

- **Never invent, guess, or alter a URL.** Only cite a deep link you have
  **fetched this turn** (2xx) or that already exists in the repo/vendored docs.
- The seed list is **illustrative** — when a scenario needs a page not listed,
  **search** `site:learn.microsoft.com` scoped to the ESS path and fetch the hit.
- **Canonical vs legacy:** `workday-simplified-setup` is the **current** default
  for new deployments; `workday` (ISU + RaaS) is **legacy / existing deployments
  only**. Ground Workday setup on simplified unless the tenant is already legacy.
- Cite the exact page you grounded each recommendation on (per phase / per step).

## Seed anchors → vendored copy

The URLs are as provided for illustration; verify by fetching this turn.

| Learn anchor | URL | Vendored copy (read first) |
|---|---|---|
| overview | https://learn.microsoft.com/en-us/microsoft-365/copilot/employee-self-service/overview | `ess-docs/overview.md` |
| deployment-checklist | https://learn.microsoft.com/en-us/microsoft-365/copilot/employee-self-service/deployment-checklist | `ess-docs/deployment/deployment-checklist.md` |
| prerequisites | https://learn.microsoft.com/en-us/microsoft-365/copilot/employee-self-service/prerequisites | `ess-docs/deployment/prerequisites.md` |
| prepare | https://learn.microsoft.com/en-us/microsoft-365/copilot/employee-self-service/prepare | `ess-docs/deployment/prepare.md` |
| install | https://learn.microsoft.com/en-us/microsoft-365/copilot/employee-self-service/install | `ess-docs/deployment/install.md` |
| customize | https://learn.microsoft.com/en-us/microsoft-365/copilot/employee-self-service/customize | `ess-docs/customization/customize.md` |
| design-best-practices | https://learn.microsoft.com/en-us/microsoft-365/copilot/employee-self-service/design-best-practices | `ess-docs/customization/design-best-practices.md` |
| servicenow | https://learn.microsoft.com/en-us/microsoft-365/copilot/employee-self-service/servicenow | `ess-docs/integrations/servicenow.md` (+ `servicenow-hrsd-itsm.md`, `servicenow-knowledge-deployment.md`, `servicenow-live-agent.md`) |
| workday (simplified — current) | https://learn.microsoft.com/en-us/microsoft-365/copilot/employee-self-service/workday-simplified-setup | `ess-docs/integrations/workday.md` (+ `workday-extensibility.md`) |
| workday-legacy (existing only) | https://learn.microsoft.com/en-us/microsoft-365/copilot/employee-self-service/workday | `ess-docs/integrations/workday.md` |
| deploy-overview-alm | https://learn.microsoft.com/en-us/microsoft-365/copilot/employee-self-service/deploy-overview-alm | `ess-docs/deployment/deploy-overview-alm.md` |

Additional vendored pages with no 1:1 seed anchor (search Learn for the live
equivalent when needed): SAP SuccessFactors
(`ess-docs/integrations/sapsuccessfactors.md`, `sap-employee-read-write-scenarios.md`,
`sap-manager-read-write-scenarios.md`), SharePoint knowledge
(`ess-docs/customization/optimization-sharepoint.md`,
`ess-docs/integrations/sharepoint-filtering.md`), multilingual
(`ess-docs/customization/employee-self-service-multilingual.md`), agent handoff
(`ess-docs/customization/agent-handoff.md`).

## How to search Learn (when a page isn't vendored)

- Scope the query: `site:learn.microsoft.com employee-self-service <topic>` (e.g.
  `... employee-self-service SharePoint knowledge source`).
- Fetch the top ESS-path hit, confirm it's under
  `/microsoft-365/copilot/employee-self-service/`, then cite it.
- Prefer the `/employee-self-service/` path over generic Copilot Studio pages.
