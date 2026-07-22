# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""
Scenario catalogue loader + goal mapping.

Loads the data-only catalogue (``src/reference/ess-scenario-catalogue.json``) and
maps a sponsor's stated goals (focus domains + named systems) onto catalogue
scenarios, converting each into a ``Plan.Scenarios[]`` entry. Deterministic and
offline; the reasoning about which goals map where is the planner skill's job —
this module just does the lookup once the goals are structured.
"""

from __future__ import annotations

import json
import os

CATALOGUE_FILE = os.path.join("src", "reference", "ess-scenario-catalogue.json")

# Sponsor-named system -> canonical token used by the catalogue checks.
_SYSTEM_ALIASES = {
    "workday": "workday",
    "sap": "sap",
    "successfactors": "sap",
    "sap successfactors": "sap",
    "servicenow": "servicenow",
    "service-now": "servicenow",
    "snow": "servicenow",
    "sharepoint": "sharepoint",
    "self-help": "selfhelp",
    "selfhelp": "selfhelp",
    "microsoft self-help": "selfhelp",
}

_DOMAIN_ALIASES = {
    "hr": "HR", "human resources": "HR",
    "it": "IT", "information technology": "IT",
    "cross": "Cross", "both": "*", "all": "*",
}


def load_catalogue(path: str = CATALOGUE_FILE) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def normalize_systems(systems: list[str]) -> set[str]:
    out: set[str] = set()
    for s in systems or []:
        out.add(_SYSTEM_ALIASES.get(s.strip().lower(), s.strip().lower()))
    return out


def normalize_focus(focus: list[str]) -> set[str]:
    out: set[str] = set()
    for f in focus or []:
        d = _DOMAIN_ALIASES.get(f.strip().lower(), f.strip())
        out.add(d)
    return out or {"*"}


def scenario_systems(scenario: dict) -> set[str]:
    """The set of connector tokens a catalogue scenario depends on."""
    tokens: set[str] = set()
    for dep in scenario.get("dependencies", []):
        check = dep.get("check", {})
        kind = check.get("kind")
        if kind == "connector":
            tokens.update(check.get("anyOf", []))
        elif kind == "servicenow":
            tokens.add("servicenow")
        elif kind == "knowledge":
            for t in check.get("anyOf", []):
                tokens.add("sharepoint" if t == "SharePoint" else "servicenow" if t == "ServiceNowGraph" else t.lower())
        elif kind == "builtin":
            tokens.add("selfhelp")
    return tokens


def _domain_of(category_name: str, catalogue: dict) -> str:
    for c in catalogue.get("categories", []):
        if c.get("name") == category_name:
            return c.get("domain", "")
    return ""


def map_goals(
    catalogue: dict,
    *,
    focus: list[str] | None = None,
    systems: list[str] | None = None,
) -> list[dict]:
    """Return catalogue scenarios that match the stated focus + named systems.

    - ``focus``: domains to include (``HR`` / ``IT`` / ``Cross``); empty or ``*``
      means all.
    - ``systems``: connector tokens the sponsor named. When provided, a scenario
      is included only if it depends on one of them (built-in / Self-Help
      scenarios are always eligible). When empty, all in-focus scenarios are
      returned so the plan shows what's blocked.
    """
    want_focus = normalize_focus(focus or [])
    want_systems = normalize_systems(systems or [])
    out = []
    for sc in catalogue.get("scenarios", []):
        domain = _domain_of(sc.get("category", ""), catalogue)
        if "*" not in want_focus and domain not in want_focus and domain != "Cross":
            continue
        if want_systems:
            toks = scenario_systems(sc)
            if not (toks & want_systems) and "selfhelp" not in toks:
                continue
        out.append(sc)
    return out


def to_plan_entry(catalogue_scenario: dict, *, origin: str = "sponsor",
                  enabled: bool = True) -> dict:
    """Convert a catalogue scenario into a ``Plan.Scenarios[]`` entry (via
    ``plan_model.scenario_entry``)."""
    from plan_model import scenario_entry  # local import to avoid cycle
    sc = catalogue_scenario
    return scenario_entry(
        sid=sc.get("id", ""),
        area=sc.get("category", ""),
        jtbd=sc.get("jtbd", "") or sc.get("name", ""),
        persona=sc.get("persona", "Employee"),
        connector=sc.get("connector", ""),
        catalog_range=sc.get("catalogRange", ""),
        tier=sc.get("tier", 0),
        priority=sc.get("tier", 0),
        deflection_type=sc.get("deflectionType", ""),
        origin=origin,
        enabled=enabled,
        dependencies=sc.get("dependencies", []),
        flags=sc.get("flags", {}),
    )


def category_representative(catalogue: dict, category_name: str) -> dict | None:
    """A representative scenario for a category (first by number) — used to seed
    a discovered suggestion for a whole category."""
    best = None
    for sc in catalogue.get("scenarios", []):
        if sc.get("category") == category_name:
            if best is None or sc.get("num", 999) < best.get("num", 999):
                best = sc
    return best
