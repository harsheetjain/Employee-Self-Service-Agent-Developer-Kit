# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""
Readiness engine — the "living plan" core.

Evaluates each scenario's ``dependencies[].check`` against the discovered
inventory, sets a per-dependency ``status`` (``met`` / ``at-risk`` / ``unmet`` /
``planned`` / ``pending``), and rolls it up into the scenario's ``readiness``
triplet (``config`` / ``pilot`` / ``production``) and a display ``verdict``.

This is what makes the stored plan self-update: re-run ``enrich`` after each
``/discover`` and the same scenario entries move from ``unmet`` to ``at-risk`` to
``ready`` with no re-scoping. Pure/deterministic — no network.

Status meaning:
  met      — the required resource is present and healthy
  at-risk  — present but unhealthy/unverified (e.g. an erroring connection, or a
             knowledge source that's authored but not indexed) → configured, not
             pilot-safe
  planned  — not present, but the maker recorded intent (inventory ``intake``)
  unmet    — not present
  pending  — a manual gate (governance / manager context) the kit can't verify
"""

from __future__ import annotations

MET, AT_RISK, UNMET, PLANNED, PENDING, ADVISORY = (
    "met", "at-risk", "unmet", "planned", "pending", "advisory")

_HARD_KINDS = {"connector", "servicenow", "knowledge", "builtin", "capability", "scenario-edge"}


# ---------------------------------------------------------------------------
# Inventory probes
# ---------------------------------------------------------------------------

def _conns(inventory: dict, *tokens: str) -> list[dict]:
    want = set(tokens)
    return [c for c in inventory.get("connections", []) if c.get("connector") in want]


def _any_status(conns: list[dict], *statuses: str) -> bool:
    want = {s.lower() for s in statuses}
    return any((c.get("status") or "").lower() in want for c in conns)


def _pack(inventory: dict, name: str) -> dict:
    p = inventory.get("extensionPacks", {}).get(name)
    return p if isinstance(p, dict) else {}


def _intake_planned(inventory: dict, *systems: str) -> bool:
    want = set(systems)
    for item in inventory.get("intake", []):
        if item.get("system") in want and item.get("status") in ("planned", "configuring"):
            return True
    return False


def _connector_status(inventory: dict, any_of: list[str]) -> str:
    conns = _conns(inventory, *any_of)
    if _any_status(conns, "connected", "captured"):
        return MET
    if _any_status(conns, "error"):
        return AT_RISK
    if any(_pack(inventory, t).get("installed") for t in any_of):
        return AT_RISK
    if _intake_planned(inventory, *any_of):
        return PLANNED
    return UNMET


def _servicenow_status(inventory: dict, module: str) -> str:
    conns = _conns(inventory, "service-now")
    connected = _any_status(conns, "connected", "captured")
    errored = _any_status(conns, "error")
    pack = _pack(inventory, "servicenow")

    if module == "handoff":
        # Live-agent / Now Assist config is not programmatically verifiable.
        if connected:
            return AT_RISK
        if errored:
            return AT_RISK
        return PLANNED if _intake_planned(inventory, "servicenow") else UNMET

    sub = bool(pack.get(module))  # hrsd | itsm
    if sub and connected:
        return MET
    if sub and errored:
        return AT_RISK
    if sub or connected:
        return AT_RISK
    return PLANNED if _intake_planned(inventory, "servicenow") else UNMET


def _knowledge_status(inventory: dict, any_of: list[str]) -> str:
    want = set(any_of)
    best = UNMET
    for ks in inventory.get("knowledgeSources", []):
        if ks.get("type") in want:
            state = (ks.get("state") or "").lower()
            if any(k in state for k in ("active", "ready", "published", "indexed")):
                return MET
            best = AT_RISK
    if best == UNMET and ("SharePoint" in want and _intake_planned(inventory, "sharepoint")):
        return PLANNED
    if best == UNMET and ("ServiceNowGraph" in want and _intake_planned(inventory, "servicenow")):
        return PLANNED
    return best


def _capability_status(inventory: dict, category: str) -> str:
    for m in inventory.get("capabilityMatrix", []):
        if m.get("category") == category:
            return {"ready": MET, "partial": AT_RISK, "planned": PLANNED,
                    "blocked": UNMET}.get(m.get("verdict"), PENDING)
    return PENDING


def evaluate_check(check: dict, inventory: dict) -> str | None:
    """Evaluate one inventory-derivable check. Returns a status, or None for
    checks that need plan context / manual confirmation (handled by ``enrich``)."""
    kind = check.get("kind")
    if kind == "connector":
        return _connector_status(inventory, check.get("anyOf", []))
    if kind == "servicenow":
        return _servicenow_status(inventory, check.get("module", ""))
    if kind == "knowledge":
        return _knowledge_status(inventory, check.get("anyOf", []))
    if kind == "builtin":
        return MET  # out-of-the-box (e.g. Microsoft Self-Help)
    if kind == "capability":
        return _capability_status(inventory, check.get("category", ""))
    return None  # scenario-edge / manual


# ---------------------------------------------------------------------------
# Rollup
# ---------------------------------------------------------------------------

def _rollup(scenario: dict) -> None:
    deps = scenario.get("dependencies", [])
    hard = [d for d in deps if d.get("kind") in _HARD_KINDS]
    hard_status = [d.get("status") for d in hard]

    config = bool(hard) and not any(s in (UNMET, PLANNED, PENDING) for s in hard_status)
    if not hard:
        config = True
    has_at_risk = any(s == AT_RISK for s in hard_status)
    context_ok = all(d.get("status") == MET for d in deps if d.get("kind") == "context")
    gov_ok = all(d.get("status") == MET for d in deps if d.get("kind") == "governance")

    pilot = config and not has_at_risk and context_ok and gov_ok
    # production is a manual go-live sign-off set by the owner - preserve it
    # across re-enrich rather than auto-deriving it.
    production = bool(scenario.get("readiness", {}).get("production"))
    scenario["readiness"] = {"config": config, "pilot": pilot, "production": production}

    if pilot:
        verdict = "ready"
    elif config and has_at_risk:
        verdict = "at-risk"
    elif config:
        verdict = "partial"  # configured but a manual gate (context/governance) is open
    elif any(s == PLANNED for s in hard_status):
        verdict = "planned"
    else:
        verdict = "blocked"
    scenario["verdict"] = verdict


def _evaluate_one(scenario: dict, inventory: dict) -> None:
    for dep in scenario.get("dependencies", []):
        kind = dep.get("kind")
        if kind == "ordering":
            dep["status"] = ADVISORY
            continue
        if kind in ("governance", "context"):
            # Manual gate — leave as-is if already confirmed, else pending.
            if dep.get("status") != MET:
                dep["status"] = PENDING
            continue
        if kind == "scenario-edge":
            continue  # second pass
        status = evaluate_check(dep.get("check", {}), inventory)
        if status is not None:
            dep["status"] = status
    _rollup(scenario)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def enrich(plan: dict, inventory: dict) -> dict:
    """Evaluate every scenario against the inventory and stamp
    ``evaluatedAgainst``. Two passes so ``scenario-edge`` deps can see other
    scenarios' readiness."""
    scenarios = plan.get("scenarios", [])

    # Pass 1 — inventory-derivable + manual.
    for s in scenarios:
        _evaluate_one(s, inventory)

    # Pass 2 — scenario-edge (depends on other enabled scenarios being config-ready).
    ready_categories = {
        s.get("area") for s in scenarios
        if s.get("enabled") and s.get("readiness", {}).get("config")
    }
    for s in scenarios:
        edges = [d for d in s.get("dependencies", []) if d.get("kind") == "scenario-edge"]
        if not edges:
            continue
        for dep in edges:
            need = set(dep.get("check", {}).get("needAnyCategory", []))
            dep["status"] = MET if (need & ready_categories) else UNMET
        _rollup(s)

    env = inventory.get("environment", {})
    plan["evaluatedAgainst"] = {
        "environmentRef": env.get("environmentId", "") or env.get("dataverseUrl", ""),
        "inventoryVersion": inventory.get("generatedAt", ""),
        "inventoryMode": inventory.get("mode", ""),
    }
    return plan


def suggest_latent(plan: dict, inventory: dict, catalogue: dict) -> list[str]:
    """Append discovered suggestions: categories whose connector is present in
    the tenant but that the sponsor didn't scope. Returns the list of added
    scenario ids (for messaging)."""
    import catalogue as _cat

    existing_areas = {s.get("area") for s in plan.get("scenarios", [])}
    added: list[str] = []
    for cat in catalogue.get("categories", []):
        name = cat.get("name")
        if name in existing_areas:
            continue
        verdict = _capability_status(inventory, name)
        if verdict not in (MET, AT_RISK):
            continue  # only suggest when the connector is actually present
        rep = _cat.category_representative(catalogue, name)
        if not rep:
            continue
        entry = _cat.to_plan_entry(rep, origin="discovered", enabled=False)
        _evaluate_one(entry, inventory)
        plan.setdefault("scenarios", []).append(entry)
        existing_areas.add(name)
        added.append(entry["id"])
    return added
