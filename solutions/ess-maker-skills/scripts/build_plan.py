# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""
ESS Maker Kit - Scenario Planner orchestrator.

Turns a sponsor's goals into a tenant-grounded, self-updating rollout plan:
scope scenarios from the catalogue, enrich each scenario's readiness from the
discovered inventory, surface discovered suggestions, and stage the plan to
``workspace/plan/plan.json`` (syncing to the WeveNova Plan MCP when configured).

Modes
-----
    # Scope + enrich a new plan from goals (focus domains + named systems):
    python scripts/build_plan.py --build --name "HR-first launch" \
        --focus hr --systems workday,sharepoint,servicenow

    # Re-evaluate readiness after a /discover re-run (the living update):
    python scripts/build_plan.py --enrich

    # Add discovered suggestions (connectors present but not scoped):
    python scripts/build_plan.py --suggest

    # Push the plan to the WeveNova dev-tunnel MCP (if WEVENOVA_MCP_URL set):
    python scripts/build_plan.py --sync

    # Inspect / offline self-test:
    python scripts/build_plan.py --print
    python scripts/build_plan.py --selftest
"""

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))  # scripts/
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "planner"))

import plan_model  # noqa: E402
import catalogue as catalogue_mod  # noqa: E402
import readiness  # noqa: E402
import wevenova  # noqa: E402
from discovery import inventory as inv_mod  # noqa: E402


def _csv(v: str | None) -> list[str]:
    return [x.strip() for x in (v or "").split(",") if x.strip()]


def _load_inventory() -> dict | None:
    return inv_mod.load()


def _emit_telemetry() -> None:
    try:
        import adk_telemetry
        adk_telemetry.emit_capability_use("plan", block=True)
    except Exception:  # noqa: BLE001 - telemetry must never break the planner
        pass


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_build(args) -> int:
    _emit_telemetry()
    cat = catalogue_mod.load_catalogue()
    inventory = _load_inventory()

    plan = plan_model.new_plan(args.name or "ESS rollout")
    if args.goals:
        plan_model.set_goals(plan, _csv(args.goals))

    scoped = catalogue_mod.map_goals(cat, focus=_csv(args.focus), systems=_csv(args.systems))
    for sc in scoped:
        plan_model.upsert_scenario(plan, catalogue_mod.to_plan_entry(sc))

    if inventory is None:
        plan_model.add_note(plan, "No inventory found - run /discover so readiness can be "
                                   "computed. Every scenario currently reads as blocked.")
        inventory = {}

    readiness.enrich(plan, inventory)
    readiness.suggest_latent(plan, inventory, cat)
    readiness.enrich(plan, inventory)  # re-roll after suggestions added

    path = plan_model.save(plan)
    print(f"Plan written to {path}")
    _print_headline(plan)
    if args.sync:
        _do_sync(plan)
    return 0


def cmd_enrich(args) -> int:
    plan = plan_model.load()
    if plan is None:
        print("No plan yet - run --build first.")
        return 1
    inventory = _load_inventory()
    if inventory is None:
        print("No inventory - run /discover first.")
        return 1
    cat = catalogue_mod.load_catalogue()
    readiness.enrich(plan, inventory)
    readiness.suggest_latent(plan, inventory, cat)
    readiness.enrich(plan, inventory)
    path = plan_model.save(plan)
    print(f"Plan re-enriched at {path}")
    _print_headline(plan)
    if args.sync:
        _do_sync(plan)
    return 0


def cmd_suggest(args) -> int:
    plan = plan_model.load()
    inventory = _load_inventory()
    if plan is None or inventory is None:
        print("Need both a plan (--build) and an inventory (/discover).")
        return 1
    cat = catalogue_mod.load_catalogue()
    added = readiness.suggest_latent(plan, inventory, cat)
    readiness.enrich(plan, inventory)
    plan_model.save(plan)
    print(f"Added {len(added)} suggestion(s): {', '.join(added) or '(none)'}")
    return 0


def cmd_sync(args) -> int:
    plan = plan_model.load()
    if plan is None:
        print("No plan yet - run --build first.")
        return 1
    return 0 if _do_sync(plan) else 2


def cmd_print(args) -> int:
    plan = plan_model.load()
    if plan is None:
        print("No plan yet - run --build first.")
        return 1
    print(json.dumps(plan, indent=2))
    return 0


def _do_sync(plan: dict) -> bool:
    ok, msg = wevenova.sync_plan(plan)
    print(("WeveNova: " if ok else "WeveNova (skipped): ") + msg)
    return ok


def _print_headline(plan: dict) -> None:
    active = plan_model.enabled_scenarios(plan)
    by_verdict: dict[str, int] = {}
    for s in active:
        by_verdict[s.get("verdict", "?")] = by_verdict.get(s.get("verdict", "?"), 0) + 1
    suggestions = [s for s in plan.get("scenarios", [])
                   if s.get("origin") == "discovered" and not s.get("enabled")]
    print(f"  Scenarios: {len(active)} in scope | "
          + " ".join(f"{k}={v}" for k, v in sorted(by_verdict.items()))
          + f" | suggestions: {len(suggestions)} | EvalState: {plan.get('evalState')}")


# ---------------------------------------------------------------------------
# Offline self-test
# ---------------------------------------------------------------------------

def cmd_selftest(args) -> int:
    from discovery import capability
    failures: list[str] = []

    def check(cond, msg):
        if not cond:
            failures.append(msg)

    cat = catalogue_mod.load_catalogue()
    check(len(cat.get("scenarios", [])) == 43, "catalogue has 43 scenarios")
    check(len(cat.get("categories", [])) == 6, "catalogue has 6 categories")

    # Synthetic tenant: Workday + SAP + ServiceNow (HRSD+ITSM) connected, one
    # ServiceNow connection erroring, ServiceNow Graph KB authored, no SharePoint.
    inv = inv_mod.new_inventory(mode="full")
    inv["environment"] = {"environmentId": "env-test", "dataverseUrl": "https://x.crm.dynamics.com"}
    inv["connections"] = [
        {"connector": "workday", "status": "Connected", "source": "bap"},
        {"connector": "sap", "status": "Connected", "source": "bap"},
        {"connector": "service-now", "status": "Connected", "source": "bap"},
        {"connector": "service-now", "status": "Error", "source": "bap"},
    ]
    inv["extensionPacks"] = {
        "workday": {"installed": True, "flowCount": 10},
        "servicenow": {"installed": True, "hrsd": True, "itsm": True, "flowCount": 10},
        "sap": {"installed": True, "flowCount": 5},
    }
    inv["knowledgeSources"] = [{"name": "sn-kb", "type": "ServiceNowGraph", "state": "authored", "source": "local"}]
    inv["capabilityMatrix"] = capability.compute(inv)

    # Build a plan WITHOUT sap in the named systems, so SAP-only categories are
    # left for suggest_latent to surface.
    plan = plan_model.new_plan("selftest")
    for sc in catalogue_mod.map_goals(cat, focus=["hr", "it"], systems=["workday", "servicenow", "sharepoint"]):
        plan_model.upsert_scenario(plan, catalogue_mod.to_plan_entry(sc))
    readiness.enrich(plan, inv)

    def verdict(sid):
        for s in plan["scenarios"]:
            if s["id"] == sid:
                return s.get("verdict")
        return None

    check(verdict("view-job-details") == "ready", "Workday read scenario is ready")
    check(verdict("create-hr-ticket") == "ready", "HR ticketing ready (ServiceNow HRSD connected)")
    check(verdict("hr-policy-lookup") == "blocked", "HR policy lookup blocked (no SharePoint)")
    check(verdict("hr-knowledge-sn-kb") == "at-risk", "SN KB at-risk (authored, not indexed)")
    check(verdict("windows-troubleshooting") == "ready", "Self-Help troubleshooting ready (OOB)")

    # readiness triplet on the ready scenario
    for s in plan["scenarios"]:
        if s["id"] == "view-job-details":
            check(s["readiness"]["config"] and s["readiness"]["pilot"], "ready scenario config+pilot true")

    # suggest_latent surfaces SAP-backed Manager Scenarios (not in scope, SAP connected)
    added = readiness.suggest_latent(plan, inv, cat)
    readiness.enrich(plan, inv)
    mgr = [s for s in plan["scenarios"] if s["area"] == "Manager Scenarios"]
    check(mgr and mgr[0]["origin"] == "discovered" and not mgr[0]["enabled"],
          "Manager Scenarios surfaced as a discovered suggestion")

    # EvalState + render + round-trip
    plan_model.touch(plan)
    md = plan_model.render_summary(plan)
    check("Scenario plan" in md and "Scenarios in scope" in md, "summary renders")
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "plan.json")
        plan_model.save(plan, p)
        loaded = plan_model.load(p)
        check(loaded is not None and loaded["schemaVersion"] == plan_model.SCHEMA_VERSION, "save/load round-trip")
        check(os.path.exists(os.path.join(d, "summary.md")), "summary.md written")

    # wevenova is a no-op when unconfigured (must never raise)
    ok, msg = wevenova.sync_plan(plan)
    check(ok is False and "not configured" in msg, "WeveNova sync no-ops when unconfigured")

    if failures:
        print("SELFTEST: FAIL")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("SELFTEST: PASS")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Build the ESS scenario rollout plan.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--build", action="store_true", help="Scope + enrich a new plan from goals.")
    group.add_argument("--enrich", action="store_true", help="Re-evaluate readiness from the inventory.")
    group.add_argument("--suggest", action="store_true", help="Add discovered suggestions.")
    group.add_argument("--sync", dest="sync_only", action="store_true", help="Push the plan to the WeveNova MCP.")
    group.add_argument("--print", dest="do_print", action="store_true", help="Print the current plan JSON.")
    group.add_argument("--selftest", action="store_true", help="Run the offline self-test.")

    parser.add_argument("--name", default="", help="Plan name (with --build).")
    parser.add_argument("--focus", default="", help="Focus domains, csv: hr,it (with --build).")
    parser.add_argument("--systems", default="", help="Named systems, csv: workday,servicenow,sharepoint,sap (with --build).")
    parser.add_argument("--goals", default="", help="Goal statements, csv (with --build).")
    parser.add_argument("--do-sync", dest="sync", action="store_true", help="Also sync to WeveNova after build/enrich.")
    args = parser.parse_args()

    if args.selftest:
        return cmd_selftest(args)
    if args.do_print:
        return cmd_print(args)
    if args.sync_only:
        return cmd_sync(args)
    if args.suggest:
        return cmd_suggest(args)
    if args.enrich:
        return cmd_enrich(args)
    return cmd_build(args)


if __name__ == "__main__":
    raise SystemExit(main())
