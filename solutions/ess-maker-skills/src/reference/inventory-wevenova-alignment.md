# ESS Inventory ↔ Cocreate AgentConfiguration V2 — Alignment & Read-Redaction Spec

**Audience:** Cocreate AgentConfiguration V2 / WeveNova backend owners.
**Purpose:** Align the ADK "tenant inventory" (produced by the ESS Maker Kit
`/discover` skill) with the V2 `InventoryItem` persistence model, and specify the
**one net-new backend requirement**: a role-gated **read-side redaction** so a
non-admin sponsor/owner can consume presence signals without seeing tenant
infrastructure identifiers.

**Source docs:** `Cocreate-AgentConfiguration-V2-design.docx` (InventoryItem, Phase 1);
ESS Maker Kit `src/reference/inventory.schema.json` + `inventory-contract.md`.

---

## 1. Actors & the core flow

| Actor | Role | Action |
|---|---|---|
| **Power Platform / IT Admin** | has the tenant-level verify | Runs the ADK `/discover` skill → it **writes** the full inventory (connector names, connection ids, instance URLs, everything). |
| **Sponsor / Agent Owner** | NOT a PP admin | **Reads** the inventory to plan scenarios — must see only **presence booleans + capability verdicts**, never identifiers. |

This maps cleanly onto V2: **inventory write is already IT-Admin-scoped**
("Writing inventory is an IT-Admin-scoped authorization" — Phase 1). The gap is
purely on the read side (§6).

---

## 2. TL;DR — the delta we need from the backend

1. **Accept ADK auto-discovery as an ingestion source** through the existing
   `upsertInventoryItem(kind, naturalKey, attributes, provenance)` with
   `provenance.assertedVia = "adk-discover"` and `validationStatus = Confirmed`
   (live-read). No new write API — this is the deferred "auto-discovery"
   `assertedVia` the design already anticipates.
2. **Add two net-new kinds** to the kind-schema catalogue: `ExtensionPack` and
   `ScenarioTemplate` (additive; no code change per the hybrid model).
3. **Add a role-gated read projection** to `getInventory` /
   `getInventoryItem` / `resolveInventoryRef`: **Full** vs **Presence**,
   selected server-side by `verify(caller)`. This is the only behavioral change
   to V2 — it currently says *"read-only personas see values"* (full read for
   all). See §6 for the complete requirement.

Everything else below is mapping detail so ingestion is turnkey.

---

## 3. What we reuse from V2 unchanged (no ask)

- **Generic `InventoryItem`**: typed envelope (`inventoryItemId, kind,
  naturalKey, displayName, provenance, validationStatus, externalBaseRef,
  version`) + kind-schema-validated `attributes` JSON bag, on the
  tenant-partitioned `AgentConfigurationPersistenceStamp`.
- **Idempotent upsert** by `(kind, naturalKey)`; per-item OCC via
  `version`/If-Match.
- **Provenance** (`assertedBy`, `assertedVia`, `assertedAt`) and
  **`validationStatus`** (`Unvalidated | Confirmed | Invalid | Retired`) +
  `externalBaseRef` for drift.
- **Retire-on-drift** (soft `Retired`, refs surface invalid, never dangle).
- **`verify(caller, role, target)`** four-primitive authorization enforced in
  the persistence accessor (controllers never bypass it).
- **Read-projection pattern** already exists (task `blocked/blockedBy/rank`
  recomputed each read) — the redaction tier follows the same pattern.

---

## 4. Write path — ADK auto-discovery ingestion

The ADK `/discover` skill runs **as the PP admin** and, per discovered resource,
calls the existing:

```
upsertInventoryItem(
  kind,                       // see §5
  naturalKey,                 // idempotency key, §5
  attributes,                 // kind-specific bag, §5
  provenance = {
    assertedBy:  <admin Entra oid from the ADK sign-in>,
    assertedVia: "adk-discover",
    assertedAt:  <ISO8601>
  },
  validationStatus = "Confirmed",   // read live from BAP/Island Gateway/Dataverse
  externalBaseRef  = <BAP/Dataverse etag or version>,   // for drift
  ifMatch = <version>?              // OCC on re-run
)
```

**`validationStatus` semantics for our source:**

| How the ADK obtained it | validationStatus |
|---|---|
| Live BAP `get_connections` / `get_flows`, Island Gateway KS, Dataverse | `Confirmed` |
| Baseline inference (from agent topic names, no live call) | `Unvalidated` |
| Maker intent captured but not yet configured (`intake`) | `Unvalidated` (or a `Planned` sub-state — decision D4) |

**Retire-on-drift rule (important):** on each run, items previously asserted
**with `assertedVia = "adk-discover"`** for the same `(tenant, environmentRef,
kind)` that are **not seen this run** → soft `Retired`. Discovery must **only
retire what discovery asserted** — never touch manually-asserted or
other-surface items.

**Batch:** N upserts per run; order-independent; safe to re-run (idempotent).

---

## 5. Kind mapping (field-level)

Attributes align to the Dataverse `connectionreference`/`connector` shape V2
already targets. `naturalKey` must be **unique within the tenant**; for
environment-scoped resources, **compose it with `environmentId`** so identical
names in different environments don't collide.

### 5.1 Environment  (naturalKey = `environmentId`)
| ADK field | V2 attribute |
|---|---|
| `environment.environmentId` | environmentId |
| `environment.region` | region |
| `environment.type` | type (prod/sandbox) |
| `environment.dataverseUrl` | dataverseUrl |

### 5.2 Connection  (naturalKey = `connectionId`; env-scoped → key = `environmentId + connectionId`)
| ADK field | V2 attribute |
|---|---|
| `connections[].connectionId` | connectionId |
| `connections[].connector` → raw apiId (**enrich**, §8-D1) | connectorId (`shared_workday`, `shared_service-now`, `shared_sapsuccessfactors`, `shared_sharepointonline`) |
| — | connectionReferenceLogicalName |
| `connections[].status` | status (Connected/Error/…) |
| `connections[].authType` | auth mode / connectionParametersConfig |
| `connections[].displayName` | displayName |
| (the env) | environmentRef → InventoryRef(Environment) |

Emit a **Connector** item too (naturalKey = `connectorId`) for connector
metadata (connectorType, capabilities) when available.

### 5.3 KnowledgeSource  (naturalKey = `environmentId + botId + sourceId/URL` — KS is agent-scoped, §8-D2)
| ADK field | V2 attribute |
|---|---|
| `knowledgeSources[].name` | displayName |
| `knowledgeSources[].type` | sourceType (SharePoint / ServiceNowGraph / file) |
| `knowledgeSources[].url` | source id / URL |
| `knowledgeSources[].state` | scope/state (`authored`, `active`) → also `validationStatus` |
| — | connectionRef / sharePointSiteRef |

Emit a **SharePointSite** item (naturalKey = site URL) when the KS is a
SharePoint site.

### 5.4 EntraApp  (naturalKey = `appId`)
Not in today's ADK output; add when the discover skill captures the agent's
Entra app (agent identity). Attributes: appId, objectId, displayName,
signInAudience.

### 5.5 New kinds to add to the catalogue

**ExtensionPack** (naturalKey = `environmentId + packName`)
| ADK field | attribute |
|---|---|
| `extensionPacks.<name>.installed` | installed |
| `extensionPacks.<name>.flowCount` | flowCount |
| `extensionPacks.servicenow.hrsd/itsm` | hrsd, itsm |
| `extensionPacks.workday.flavor` | flavor (simplified/legacy) |
| (the env) | environmentRef |

**ScenarioTemplate** (naturalKey = `environmentId + uniqueName`)
| ADK field | attribute |
|---|---|
| `templateConfigs[].uniqueName` | uniqueName |
| `templateConfigs[].connector` | connector |
| `templateConfigs[].operation` | operation (get/list/create/update) |
| `templateConfigs[].status` | status (Active/Inactive) |
| (the env) | environmentRef |

### 5.6 NOT persisted as inventory
- **`agents[]`** → the Project's **AgentIdentity** ConfigFieldValues
  (`agent.agentId`, `agent.entraAppId` → EntraApp ref), not tenant inventory.
- **`intake[]`** → a ConfigFieldValue / plan value with
  `validationStatus = Unvalidated` (maker intent, not a live resource).
- **`capabilityMatrix[]`** → **derived read-projection** (recomputed each read
  from the items above), never stored. It also **doubles as the Presence view**
  (§6) — it exposes readiness with zero identifiers.

---

## 6. Read-side redaction — the net-new backend requirement

### 6.1 Requirement (one sentence)
`getInventory` / `getInventoryItem` / `resolveInventoryRef` MUST return one of
two projection tiers, selected **server-side** by `verify(caller)`; a caller who
does not pass the tenant-admin verify MUST receive **only non-identifying
presence signals + capability verdicts**, with all tenant infrastructure
identifiers redacted.

### 6.2 Projection tiers
| Tier | Who | Contains |
|---|---|---|
| **Full** | `verify(caller, {IT-Admin \| PP-Admin}, tenant)` = allow | full `InventoryItem` (all attributes, provenance, naturalKey, externalBaseRef) |
| **Presence** | everyone else (default) | per-kind presence booleans + `capabilityMatrix` verdicts only |

**The caller does not choose the tier** — the accessor decides from `verify`. A
caller cannot request Full. Return `projectionTier: "full" | "presence"` in the
envelope so the client knows what it received.

### 6.3 The verify gate
- Full requires a **tenant-level** verify (`verify(caller, IT-Admin, tenant)` —
  inventory is tenant-global, so the target is the tenant, not an agent). This
  is the **same** verify already used for inventory **write**.
- **Default-deny:** unknown / absent / partial role → **Presence**.

### 6.4 Per-kind exposure allow-list (allow-list, not deny-list)

Anything **not** on the Presence allow-list is redacted. New attributes added to
a kind schema default to **redacted** until explicitly allow-listed (regression
gate — §9).

| Kind | Presence EXPOSES | Presence REDACTS (Full-only) |
|---|---|---|
| Connection | `connector` **family** as a boolean bucket only (see 6.5); `healthy` (any Connected); `validationStatus` | connectionId, connectorId, connectionReferenceLogicalName, displayName, auth mode, instanceUrl, naturalKey, externalBaseRef, provenance.assertedBy, environmentRef |
| Connector | (nothing beyond the boolean bucket) | connectorId, displayName, capabilities |
| KnowledgeSource | `type` (SharePoint/ServiceNowGraph), `present`, `indexed` | url/siteUrl, siteId/webId/driveId, connectionRef, naturalKey, provenance |
| SharePointSite | `present` | site URL, siteId, driveId |
| Environment | `type` (prod/sandbox) — optional (D3) | environmentId, dataverseUrl, region, naturalKey |
| ExtensionPack | `installed` (bool), `hrsd`/`itsm` (bool), `flavor` | flowCount (D3), naturalKey, environmentRef |
| ScenarioTemplate | per-connector `hasTemplates` (bool) | uniqueName, operation, counts (D3), naturalKey |
| *(all kinds)* | — | `inventoryItemId`, `naturalKey`, `externalBaseRef`, entire `provenance` |

### 6.5 Presence payload shape (what the sponsor gets)
```jsonc
{
  "projectionTier": "presence",
  "generatedAt": "...",
  "systems": {
    "workday":    { "configured": true,  "healthy": true,  "validationStatus": "Confirmed" },
    "servicenow": { "configured": true,  "healthy": false, "validationStatus": "Confirmed" },  // an errored conn → healthy:false
    "sap":        { "configured": true,  "healthy": true,  "validationStatus": "Confirmed" },
    "sharepoint": { "configured": false, "healthy": false, "validationStatus": "Unvalidated" }
  },
  "knowledge": { "sharepoint": false, "serviceNowGraph": true },
  "extensionPacks": { "workday": true, "servicenow": { "hrsd": true, "itsm": true }, "sap": true },
  "capabilityMatrix": [
    { "category": "HR Ticketing", "verdict": "ready",   "missing": [] },
    { "category": "IT Scenarios", "verdict": "partial", "missing": ["SharePoint knowledge source or ServiceNow Graph knowledge source"] }
  ]
}
```
Notes:
- `capabilityMatrix.missing` names **generic capability types** ("SharePoint
  knowledge source"), never tenant instance names — safe to expose.
- `configured`/`healthy` are booleans; **no counts** in the default Presence
  shape (a count is mildly identifying — D3 if you want bucketed counts like
  `none|one|several`).

### 6.6 Enforcement & security invariants (non-negotiable)
1. **Server-side only**, in the persistence accessor. Never client-side — a
   sponsor must not be able to reach the raw store/ItemClass and reconstruct
   Full. (V2 already mandates "controllers never bypass the accessor.")
2. **Allow-list, not deny-list** (see 6.4) — fail closed on new fields.
3. **Redaction applies to every read entry point:** `getInventory`,
   `getInventoryItem`, and **`resolveInventoryRef`** (a ConfigFieldValue
   resolving an InventoryRef must NOT leak the item's attributes to a Presence
   caller — resolve to the presence/opaque form or a boolean).
4. **Constrain `$filter` for Presence callers.** Promoted scalars include
   `naturalKey`/`validationStatus`; a Presence caller must not be able to
   `$filter` by `naturalKey`/`connectionId` to confirm a specific value.
   Allow `$filter` on `kind` only; deny (400) or ignore filters on redacted
   scalars.
5. **No side-channel leaks:** identifiers must not appear in error messages,
   `@odata.count`, ordering, ETags, or `externalBaseRef` for Presence callers.
6. **Retired/Invalid items:** excluded from the Presence `configured` roll-up
   the same way absent items are (a Retired Workday connection ⇒
   `workday.configured` reflects only live/Confirmed items).

### 6.7 API surface (recommended)
- Keep the existing operations; the tier is implicit from `verify`. Add
  `projectionTier` to the response envelope.
- OR expose a dedicated `getInventoryPresence()` that is *only ever* the
  Presence shape (defense in depth: the sponsor UI calls the presence endpoint;
  the admin UI calls `getInventory`). Either is acceptable; the invariant is that
  **no Presence-authenticated call can return Full**.

### 6.8 Edge cases
| Case | Behavior |
|---|---|
| Admin verify passes, item `Retired`/`Invalid` | Full (admin sees state) |
| Presence caller, empty tenant | all booleans `false`, capabilityMatrix all `blocked` (reveals nothing) |
| Presence caller `$filter=kind eq 'Connection'` | allowed |
| Presence caller `$filter=naturalKey eq '<connId>'` | denied (400) or ignored |
| Presence caller `resolveInventoryRef(ref)` | presence/opaque form, never attributes |
| Caller role indeterminate / verify errors | **Presence** (default-deny) |

### 6.9 Audit
Log per read: caller oid, `verify` decision, `projectionTier` served, kind/filter.
(Enables security review that no Presence caller ever received Full.)

---

## 7. Scope reconciliation (per-environment discovery → tenant-global store)

The ADK inventory is **per Power Platform environment** (one `dataverseUrl` /
`environmentId` per crawl). V2's inventory is **tenant-global**, with each
env-scoped item carrying `environmentRef`. So cross-environment aggregation
**emerges at the WeveNova layer**: running `/discover` in env A upserts items
tagged `environmentRef=A`; env B upserts `environmentRef=B`; the tenant
partition holds both. No change needed — just ensure every env-scoped item
carries `environmentRef` and composes it into `naturalKey` (§5). (An optional
ADK `--all-environments` mode would let one admin populate several envs per run.)

---

## 8. Open decisions for the backend team

- **D1 — Connector identity granularity.** ADK currently normalizes to a
  connector *token* (`workday`); V2's Connection wants the raw `connectorId`
  (`shared_workday`). We'll enrich the collector to capture apiId — confirm the
  exact `connectorId` values you want as the canonical set.
- **D2 — KnowledgeSource naturalKey.** KS is agent-scoped; propose
  `environmentId + botId + sourceId`. Confirm whether KS should reference the
  agent (AgentIdentity) or stay a standalone tenant item.
- **D3 — Presence granularity.** Booleans only (safest, matches the ask), or
  bucketed counts (`none|one|several`) + environment `type`/`region`? Counts and
  region are mildly identifying — your call.
- **D4 — `intake`/Planned state.** Model maker intent as a ConfigFieldValue
  (`Unvalidated`) or add a `Planned` validationStatus sub-state on InventoryItem?
- **D5 — New kinds.** Confirm `ExtensionPack` and `ScenarioTemplate` as
  catalogue kinds vs deriving them (packs from flows, templates from Dataverse)
  at read.
- **D6 — Retention.** Inventory is durable (excluded from 30-day); confirm the
  split-stamp vs app-sweep decision covers `adk-discover` items.
- **D7 — Write auth target.** Confirm the tenant-level verify role name the ADK
  admin must hold to write (IT-Admin? PP-Admin?), and the same role gate for
  Full read.

---

## 9. Acceptance criteria

**Write / ingestion**
- [ ] ADK `/discover` upserts each discovered resource via `upsertInventoryItem`
      with `assertedVia="adk-discover"`, `validationStatus="Confirmed"`, correct
      `naturalKey` + `environmentRef`; re-run is idempotent (no duplicates).
- [ ] Items dropped from the environment between runs go `Retired`, and **only**
      `adk-discover`-asserted items are retired.

**Read — Full (admin)**
- [ ] `verify`-passing caller gets full attributes for every kind.

**Read — Presence (non-admin) — the redaction gate**
- [ ] Non-admin read returns **only** presence booleans + `capabilityMatrix`;
      payload (incl. nested, `$filter` results, and `resolveInventoryRef`)
      contains **no** `connectionId`, `connectorId`,
      `connectionReferenceLogicalName`, `displayName`, `instanceUrl`/site URL,
      `naturalKey`, `externalBaseRef`, `environmentId`, `dataverseUrl`, Entra
      `appId/objectId`, or provenance oid.
- [ ] Unknown/absent role ⇒ Presence (default-deny).
- [ ] A new attribute added to any kind schema is **absent** from Presence until
      explicitly allow-listed (fail-closed regression test).
- [ ] Presence caller cannot `$filter` by a redacted scalar (naturalKey/…).
- [ ] `projectionTier` is reported in the response; audit logs the tier served.
