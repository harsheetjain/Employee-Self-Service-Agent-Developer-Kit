# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""
Scenario-plan data model + IO + rendering.

Implements the ``Plan.Scenarios[]`` shape from the scenario-list one-pager: an
embedded, iterated list of scenario entries, each carrying ``origin``,
machine-checkable ``dependencies[].check``, a ``readiness`` triplet
(``config`` / ``pilot`` / ``production``), and ``evaluatedAgainst`` so the plan
can be a *living* artifact rather than a static snapshot.

Pure module: no network, no inventory evaluation (that's ``readiness.py``). The
canonical local artifact is ``workspace/plan/plan.json`` (the contract mirror the
planner stages until the WeveNova Plan MCP is wired in), with a human-readable
``workspace/plan/summary.md`` alongside.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

SCHEMA_VERSION = 1

PLAN_DIR = os.path.join("workspace", "plan")
PLAN_FILE = os.path.join(PLAN_DIR, "plan.json")
SUMMARY_FILE = os.path.join(PLAN_DIR, "summary.md")

# Scenario origin.
ORIGIN_SPONSOR = "sponsor"
ORIGIN_DISCOVERED = "discovered"

# Plan lifecycle (EvalState rollup).
EVAL_DRAFT = "Draft"
EVAL_SCENARIOS_DEFINED = "ScenariosDefined"
EVAL_CONFIGURED = "Configured"
EVAL_READY_TO_RUN = "Ready-to-run"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------

def new_plan(name: str, project_id: str = "", kind: str = "Buildout") -> dict:
    """Return an empty Draft plan skeleton at the current schema version."""
    return {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": _now_iso(),
        "planId": "",
        "projectId": project_id,
        "name": name or "ESS rollout",
        "kind": kind,
        "status": "Draft",
        "phase": "Discovery",
        "goals": [],
        "knowledgeSources": [],
        "roles": [],
        "evalState": EVAL_DRAFT,
        "evaluatedAgainst": {},
        "scenarios": [],
        "acceptanceCriteria": [],
        "notes": [],
    }


def scenario_entry(
    *,
    sid: str,
    area: str,
    jtbd: str = "",
    persona: str = "Employee",
    connector: str = "",
    catalog_range: str = "",
    tier: int = 0,
    priority: int = 0,
    confidence: str = "Med",
    deflection_type: str = "",
    origin: str = ORIGIN_SPONSOR,
    enabled: bool = True,
    dependencies: list[dict] | None = None,
    flags: dict | None = None,
) -> dict:
    """Build a single ``Plan.Scenarios[]`` entry.

    ``readiness`` starts unknown (all False); ``readiness.py`` fills it from the
    inventory. Each dependency is ``{kind, need, check, status}`` with ``status``
    seeded ``"pending"`` until evaluated.
    """
    deps = []
    for d in dependencies or []:
        deps.append({
            "kind": d.get("kind", ""),
            "need": d.get("need", ""),
            "check": d.get("check", {"kind": "manual"}),
            "status": d.get("status", "pending"),
        })
    return {
        "id": sid,
        "area": area,
        "jtbd": jtbd,
        "persona": persona,
        "connector": connector,
        "catalogRange": catalog_range,
        "tier": tier,
        "priority": priority,
        "confidence": confidence,
        "deflectionType": deflection_type,
        "origin": origin,
        "enabled": enabled,
        "dependencies": deps,
        "readiness": {"config": False, "pilot": False, "production": False},
        "verdict": "unknown",
        "flags": flags or {},
    }


# ---------------------------------------------------------------------------
# Mutation helpers (idempotent by scenario id)
# ---------------------------------------------------------------------------

def upsert_scenario(plan: dict, entry: dict) -> dict:
    """Insert or replace a scenario by ``id``. Preserves ``enabled`` and
    ``origin`` on an existing sponsor entry so a re-scope doesn't silently
    re-enable a deferred scenario."""
    scenarios = plan.setdefault("scenarios", [])
    for i, s in enumerate(scenarios):
        if s.get("id") == entry.get("id"):
            # Keep the maker's enable/disable + accepted-suggestion decisions.
            entry["enabled"] = s.get("enabled", entry.get("enabled", True))
            entry["origin"] = s.get("origin", entry.get("origin"))
            if s.get("deferReason"):
                entry["deferReason"] = s["deferReason"]
            scenarios[i] = entry
            return entry
    scenarios.append(entry)
    return entry


def set_goals(plan: dict, goals: list[str]) -> None:
    plan["goals"] = [g for g in (goals or []) if g]


def set_roles(plan: dict, roles: list[dict]) -> None:
    plan["roles"] = roles or []


def add_note(plan: dict, note: str) -> None:
    notes = plan.setdefault("notes", [])
    if note not in notes:
        notes.append(note)


def enabled_scenarios(plan: dict) -> list[dict]:
    return [s for s in plan.get("scenarios", []) if s.get("enabled")]


def compute_eval_state(plan: dict) -> str:
    """Roll the plan's EvalState up from its scenarios' readiness.

    Draft (no scenarios) -> ScenariosDefined (some enabled) -> Configured (every
    enabled scenario is config-ready) -> Ready-to-run (every enabled scenario is
    pilot-ready). Discovered-but-unaccepted suggestions never gate the rollup.
    """
    enabled = enabled_scenarios(plan)
    if not enabled:
        return EVAL_DRAFT
    if all(s.get("readiness", {}).get("pilot") for s in enabled):
        return EVAL_READY_TO_RUN
    if all(s.get("readiness", {}).get("config") for s in enabled):
        return EVAL_CONFIGURED
    return EVAL_SCENARIOS_DEFINED


def touch(plan: dict) -> dict:
    plan["generatedAt"] = _now_iso()
    plan["evalState"] = compute_eval_state(plan)
    return plan


# ---------------------------------------------------------------------------
# IO
# ---------------------------------------------------------------------------

def load(path: str = PLAN_FILE) -> dict | None:
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def save(plan: dict, path: str = PLAN_FILE, *, render_md: bool = True) -> str:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    touch(plan)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(plan, f, indent=2)
        f.write("\n")
    if render_md:
        summary_path = os.path.join(os.path.dirname(path) or ".", "summary.md")
        with open(summary_path, "w", encoding="utf-8") as f:
            f.write(render_summary(plan))
    return path


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------

_VERDICT_ICON = {
    "ready": "\u2705",       # green check
    "at-risk": "\U0001f7e0",  # orange circle
    "planned": "\U0001f6e0\ufe0f",
    "blocked": "\u26d4",
    "unknown": "\u2754",
}


def _readiness_label(s: dict) -> str:
    r = s.get("readiness", {})
    if r.get("pilot"):
        return "pilot-ready"
    if r.get("config"):
        return "authored (not pilot-ready)"
    return "not configured"


def render_summary(plan: dict) -> str:
    lines: list[str] = []
    lines.append(f"# Scenario plan: {plan.get('name', '')}")
    lines.append("")
    lines.append(f"Generated: {plan.get('generatedAt', '?')}  |  "
                 f"Phase: {plan.get('phase', '?')}  |  EvalState: {plan.get('evalState', '?')}")
    ea = plan.get("evaluatedAgainst", {})
    if ea:
        lines.append(f"Evaluated against inventory: {ea.get('inventoryVersion', '?')} "
                     f"(env {ea.get('environmentRef', '?')})")
    lines.append("")

    if plan.get("goals"):
        lines.append("## Goals")
        lines.append("")
        for g in plan["goals"]:
            lines.append(f"- {g}")
        lines.append("")

    active = [s for s in plan.get("scenarios", []) if s.get("enabled")]
    if active:
        lines.append("## Scenarios in scope")
        lines.append("")
        lines.append("| Scenario | Category | Connector | Readiness | Verdict | Missing |")
        lines.append("|----------|----------|-----------|-----------|---------|---------|")
        for s in sorted(active, key=lambda x: (x.get("tier", 9), x.get("priority", 9), x.get("id", ""))):
            missing = ", ".join(
                d.get("need", "") for d in s.get("dependencies", [])
                if d.get("status") in ("unmet", "at-risk", "planned", "pending")
            ) or "\u2014"
            icon = _VERDICT_ICON.get(s.get("verdict", "unknown"), "")
            lines.append(
                f"| {s.get('jtbd') or s.get('id')} | {s.get('area','')} | "
                f"{s.get('connector','')} | {_readiness_label(s)} | "
                f"{icon} {s.get('verdict','')} | {missing} |"
            )
        lines.append("")

    suggestions = [s for s in plan.get("scenarios", [])
                   if s.get("origin") == ORIGIN_DISCOVERED and not s.get("enabled")]
    if suggestions:
        lines.append("## Suggested (discovered — not yet accepted)")
        lines.append("")
        for s in suggestions:
            lines.append(f"- **{s.get('area')}** ({s.get('connector')}) \u2014 "
                         f"you have the connector; accept to add it.")
        lines.append("")

    if plan.get("roles"):
        lines.append("## Roles")
        lines.append("")
        for r in plan["roles"]:
            who = (r.get("assignee") or {}).get("displayName", "TBD") if isinstance(r.get("assignee"), dict) else (r.get("assignee") or "TBD")
            lines.append(f"- **{r.get('role','')}** \u2014 {r.get('responsibility','')} ({who})")
        lines.append("")

    if plan.get("notes"):
        lines.append("## Notes")
        lines.append("")
        for n in plan["notes"]:
            lines.append(f"- {n}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"
