# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""
Capability matrix - the bridge between the discovered inventory and the ESS
scenario **planner** (Cocreate ``planner-execution`` + ``ess-catalogue.md``).

``ess-catalogue.md`` is explicitly "decision layer only" and delegates
*connector readiness* (its prioritization criterion #3) and per-scenario
specifics to runtime fetch. This module supplies exactly that runtime signal: it
maps the catalogue's category -> connector groupings onto what discovery found
(connections, extension packs, knowledge sources) plus what the maker planned
(intake), and emits a per-category verdict the planner can consume directly.

DATA-ONLY MIRROR: ``CATEGORIES`` below mirrors the category map + connector
groupings from ``ess-catalogue.md``. It does NOT invent categories or connectors
the catalogue doesn't define. If the catalogue's category/connector map changes,
update this table to match - keep them in sync.

The verdicts are computed, not authoritative priority: the catalogue still owns
ordering/priority. This only answers "is the connector plumbing for this
category present, planned, or absent?".
"""

from __future__ import annotations

# Requirement-token states, ordered worst -> best. A category's verdict is the
# minimum group-state across its required groups (a group is satisfied by ANY of
# its tokens - "anyOf").
_ABSENT = 0
_PLANNED = 1
_INSTALLED = 2
_SATISFIED = 3

_STATE_TO_VERDICT = {
    _SATISFIED: "ready",
    _INSTALLED: "partial",
    _PLANNED: "planned",
    _ABSENT: "blocked",
}

# Human labels for requirement tokens (used in ``missing`` / evidence).
_TOKEN_LABEL = {
    "workday": "Workday connector",
    "sap": "SAP SuccessFactors connector",
    "servicenow-hrsd": "ServiceNow HRSD",
    "servicenow-itsm": "ServiceNow ITSM",
    "servicenow-graph": "ServiceNow Graph knowledge source",
    "sharepoint": "SharePoint knowledge source",
    "servicenow-handoff": "ServiceNow live agent / Now Assist",
}

# ---------------------------------------------------------------------------
# Category map (mirror of ess-catalogue.md "Scenario inventory" + "Dependencies")
# ---------------------------------------------------------------------------
# Each category lists requirement GROUPS. A group is {label, anyOf:[tokens]} and
# is satisfied by any one token. Ready requires every group satisfied.

CATEGORIES = [
    {
        "id": "hr-knowledge-profile-read",
        "category": "HR Knowledge & Profile (Read)",
        "domain": "HR",
        "persona": "Employee",
        "scenarioRange": "#1-19",
        "requires": [
            {"label": "Knowledge source", "anyOf": ["sharepoint", "servicenow-graph"]},
            {"label": "Profile connector", "anyOf": ["workday", "sap"]},
        ],
        "note": "Knowledge lookups (#1-3) need a knowledge source; profile reads (#4-19) need Workday or SAP.",
    },
    {
        "id": "hr-profile-write",
        "category": "HR Profile Update (Write)",
        "domain": "HR",
        "persona": "Employee",
        "scenarioRange": "#20-25",
        "requires": [
            {"label": "Profile connector", "anyOf": ["workday", "sap"]},
        ],
        "note": "Enable the category's reads before its writes (catalogue: reads before writes).",
    },
    {
        "id": "manager-scenarios",
        "category": "Manager Scenarios",
        "domain": "HR",
        "persona": "Manager",
        "scenarioRange": "#26-31",
        "requires": [
            {"label": "SAP SuccessFactors", "anyOf": ["sap"]},
        ],
        "note": "Also needs manager context resolved for the signed-in user.",
    },
    {
        "id": "hr-ticketing",
        "category": "HR Ticketing",
        "domain": "HR",
        "persona": "Employee",
        "scenarioRange": "#32-34",
        "requires": [
            {"label": "ServiceNow HRSD", "anyOf": ["servicenow-hrsd"]},
        ],
        "note": "",
    },
    {
        "id": "it-scenarios",
        "category": "IT Scenarios",
        "domain": "IT",
        "persona": "Employee",
        "scenarioRange": "#35-41",
        "requires": [
            {"label": "Knowledge source", "anyOf": ["sharepoint", "servicenow-graph"]},
            {"label": "ServiceNow ITSM", "anyOf": ["servicenow-itsm"]},
        ],
        "note": "Windows/M365 troubleshooting uses Microsoft Self-Help (OOB) and is not connector-gated.",
    },
    {
        "id": "handoff-scenarios",
        "category": "Handoff Scenarios",
        "domain": "Cross",
        "persona": "Employee",
        "scenarioRange": "#42-43",
        "requires": [
            {"label": "Ticketing category", "anyOf": ["servicenow-hrsd", "servicenow-itsm"]},
            {"label": "ServiceNow live agent / Now Assist", "anyOf": ["servicenow-handoff"]},
        ],
        "note": "Live-agent / Now Assist config is verified manually - handoff caps at 'partial' until confirmed.",
    },
]


# ---------------------------------------------------------------------------
# Token resolution against the inventory
# ---------------------------------------------------------------------------

def _connection_state(inv: dict, connector: str) -> str | None:
    """Best connection status for a connector token: 'connected' | 'captured' |
    'error' | None (no such connection)."""
    best = None
    rank = {"connected": 3, "captured": 2, "error": 1}
    for conn in inv.get("connections", []):
        if conn.get("connector") == connector:
            status = (conn.get("status") or "").lower()
            if status in rank and (best is None or rank[status] > rank[best]):
                best = status
    return best


def _pack(inv: dict, name: str) -> dict:
    p = inv.get("extensionPacks", {}).get(name)
    return p if isinstance(p, dict) else {}


def _ks_present(inv: dict, ks_type: str) -> str | None:
    """Return 'active'|'present'|None for a knowledge-source type."""
    found = None
    for ks in inv.get("knowledgeSources", []):
        if ks.get("type") == ks_type:
            state = (ks.get("state") or "").lower()
            if any(k in state for k in ("active", "ready", "published", "indexed")):
                return "active"
            found = "present"
    return found


def _intake_planned(inv: dict, *systems: str) -> bool:
    wanted = set(systems)
    for item in inv.get("intake", []):
        if item.get("system") in wanted and item.get("status") in ("planned", "configuring"):
            return True
    return False


def _token_state(inv: dict, token: str) -> int:
    """Resolve a single requirement token to a state score."""
    if token == "workday":
        cs = _connection_state(inv, "workday")
        if cs in ("connected", "captured"):
            return _SATISFIED
        if _pack(inv, "workday").get("installed") or cs == "error":
            return _INSTALLED
        return _PLANNED if _intake_planned(inv, "workday") else _ABSENT

    if token == "sap":
        cs = _connection_state(inv, "sap")
        if cs in ("connected", "captured"):
            return _SATISFIED
        if _pack(inv, "sap").get("installed") or cs == "error":
            return _INSTALLED
        return _PLANNED if _intake_planned(inv, "sap", "successfactors") else _ABSENT

    if token in ("servicenow-hrsd", "servicenow-itsm"):
        sub = "hrsd" if token.endswith("hrsd") else "itsm"
        cs = _connection_state(inv, "service-now")
        pack = _pack(inv, "servicenow")
        if cs in ("connected", "captured") and pack.get(sub):
            return _SATISFIED
        if pack.get(sub) or cs is not None:
            return _INSTALLED
        return _PLANNED if _intake_planned(inv, "servicenow", "service-now") else _ABSENT

    if token == "servicenow-graph":
        ks = _ks_present(inv, "ServiceNowGraph")
        if ks == "active":
            return _SATISFIED
        if ks == "present":
            return _INSTALLED
        return _PLANNED if _intake_planned(inv, "servicenow", "service-now") else _ABSENT

    if token == "sharepoint":
        ks = _ks_present(inv, "SharePoint")
        if ks == "active":
            return _SATISFIED
        if ks == "present" or _connection_state(inv, "sharepoint") is not None:
            return _INSTALLED
        return _PLANNED if _intake_planned(inv, "sharepoint") else _ABSENT

    if token == "servicenow-handoff":
        # Live-agent / Now Assist configuration is not programmatically
        # verifiable - cap at 'installed' when ServiceNow is present.
        cs = _connection_state(inv, "service-now")
        if cs is not None:
            return _INSTALLED
        return _PLANNED if _intake_planned(inv, "servicenow", "service-now") else _ABSENT

    return _ABSENT


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute(inv: dict) -> list[dict]:
    """Compute the capability matrix for an inventory. Returns a list of
    category verdict dicts (also see ``inventory.render_summary``)."""
    matrix: list[dict] = []
    for cat in CATEGORIES:
        groups_out = []
        missing = []
        evidence = []
        category_state = _SATISFIED  # min across groups

        for group in cat["requires"]:
            token_states = {tok: _token_state(inv, tok) for tok in group["anyOf"]}
            best_tok = max(token_states, key=lambda t: token_states[t])
            group_state = token_states[best_tok]
            category_state = min(category_state, group_state)

            groups_out.append({
                "label": group["label"],
                "anyOf": group["anyOf"],
                "state": _STATE_TO_VERDICT[group_state],
            })
            if group_state >= _INSTALLED:
                evidence.append(_TOKEN_LABEL.get(best_tok, best_tok))
            if group_state < _SATISFIED:
                opts = " or ".join(_TOKEN_LABEL.get(t, t) for t in group["anyOf"])
                missing.append(opts)

        verdict = _STATE_TO_VERDICT[category_state]
        reason = _reason(verdict, missing, cat)
        matrix.append({
            "id": cat["id"],
            "category": cat["category"],
            "domain": cat["domain"],
            "persona": cat["persona"],
            "scenarioRange": cat["scenarioRange"],
            "verdict": verdict,
            "reason": reason,
            "requires": groups_out,
            "missing": missing,
            "evidence": evidence,
            "note": cat.get("note", ""),
        })
    return matrix


def _reason(verdict: str, missing: list[str], cat: dict) -> str:
    if verdict == "ready":
        return "All required connectors/knowledge sources are present."
    if verdict == "blocked":
        return "Missing: " + "; ".join(missing) + "."
    if verdict == "planned":
        return "Planned but not yet configured: " + "; ".join(missing) + "."
    # partial
    return "Partially ready - installed but not fully connected: " + "; ".join(missing) + "."
