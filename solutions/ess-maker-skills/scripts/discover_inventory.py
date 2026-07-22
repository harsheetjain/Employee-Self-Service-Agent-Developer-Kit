# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""
ESS Maker Kit - Environment Discovery orchestrator.

Builds a reusable **inventory** of the customer's ESS environment - what
connectors, extension packs, knowledge sources, template configs, agents and
topics already exist, plus what the maker plans to add - and writes it to
``workspace/inventory/inventory.json`` (+ ``summary.md``). Both the maker skills
and an external scenario planner consume this instead of each re-crawling the
environment.

This decouples "system discovery" from ``setup`` (agent extraction) and
``flightcheck`` (pass/fail readiness). It reuses the kit's existing, verified API
clients - it does not introduce any new external API contract.

Modes
-----
    # Baseline: assemble from data already on disk. No extra sign-in.
    # (setup runs this at the end so an inventory always exists.)
    python scripts/discover_inventory.py --baseline

    # Full crawl: also hit the BAP + Island Gateway APIs for live connection
    # status, installed extension packs, and knowledge-source runtime state.
    python scripts/discover_inventory.py --refresh

    # Record maker intent to use a system discovery didn't find (intake):
    python scripts/discover_inventory.py --add-intake servicenow --notes "IT tickets"

    # After /connect finishes, re-read what it captured and advance intake:
    python scripts/discover_inventory.py --reconcile

    # Print the current inventory (JSON) / run the offline self-test:
    python scripts/discover_inventory.py --print
    python scripts/discover_inventory.py --selftest
"""

import argparse
import json
import os
import sys

# Make sibling modules importable whether run as `python scripts/discover_inventory.py`
# (cwd = repo root) or imported from within scripts/.
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from discovery import inventory as inv_mod  # noqa: E402
from discovery import capability  # noqa: E402
from discovery import collectors  # noqa: E402

LOCAL_STATE_DIR = ".local"


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def _read_config(local_state_dir: str = LOCAL_STATE_DIR) -> dict:
    """Read .local/config.json tolerantly (no configVersion gate / no exit)."""
    path = os.path.join(local_state_dir, "config.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _all_topic_names(agents: list[dict]) -> list[str]:
    names: list[str] = []
    for a in agents:
        names.extend(a.get("topics", []))
    return names


def _aggregate_template_configs(agents: list[dict]) -> list[dict]:
    seen = set()
    out = []
    for a in agents:
        folder = a.get("folder", "")
        for row in collectors.collect_template_configs_local(folder):
            key = row.get("uniqueName")
            if key and key not in seen:
                seen.add(key)
                out.append(row)
    return out


def _aggregate_local_knowledge(agents: list[dict]) -> list[dict]:
    out = []
    for a in agents:
        out.extend(a.get("knowledgeSources", []))
    return out


# ---------------------------------------------------------------------------
# Baseline build (no network)
# ---------------------------------------------------------------------------

def build_baseline(local_state_dir: str = LOCAL_STATE_DIR) -> dict:
    """Assemble an inventory from artifacts already on disk. No sign-in."""
    config = _read_config(local_state_dir)
    inv = inv_mod.new_inventory(mode="baseline")

    inv_mod.set_environment(inv, {
        "dataverseUrl": config.get("dataverseEndpoint", ""),
    })

    agents = collectors.collect_agents_local(config)
    inv_mod.set_agents(inv, agents)

    # Extension packs inferred from local topic names (the vanilla ESS agent
    # ships ServiceNow/Workday topics OOB, so this is meaningful even pre-crawl).
    packs = {
        "workday": {"installed": False, "flowCount": 0, "flavor": "unknown"},
        "servicenow": {"installed": False, "flowCount": 0, "hrsd": False, "itsm": False},
        "sap": {"installed": False, "flowCount": 0},
    }
    collectors._classify_packs_from_names(packs, _all_topic_names(agents))

    # Connect-captured state (instance URLs / auth / pack hints).
    connect = collectors.collect_connect_state(local_state_dir)
    collectors.apply_pack_hints(packs, connect.get("packHints", {}))
    inv_mod.set_extension_packs(inv, packs)
    inv_mod.merge_connections(inv, connect.get("connections", []))

    inv_mod.set_template_configs(inv, _aggregate_template_configs(agents))
    inv_mod.set_knowledge_sources(inv, _aggregate_local_knowledge(agents))

    # Preserve maker intent (intake) across re-crawls: a fresh crawl must not
    # forget systems the maker already said they want. reconcile() later advances
    # any of these that are now configured.
    prior = inv_mod.load()
    if prior and prior.get("intake"):
        inv["intake"] = prior["intake"]

    inv_mod.add_note(
        inv,
        "Baseline build: live connection status, installed extension packs, and "
        "knowledge-source index state were NOT scanned. Run `/discover` for a "
        "full crawl.",
    )

    inv["capabilityMatrix"] = capability.compute(inv)
    return inv


# ---------------------------------------------------------------------------
# Full crawl (network)
# ---------------------------------------------------------------------------

def build_full(local_state_dir: str = LOCAL_STATE_DIR) -> dict:
    """Full live crawl: baseline + BAP connections/packs + Island Gateway KS."""
    inv = build_baseline(local_state_dir)
    config = _read_config(local_state_dir)
    env_url = config.get("dataverseEndpoint", "")
    if not env_url:
        inv_mod.add_note(inv, "No dataverseEndpoint in config.json - run /setup first.")
        return inv

    # --- Authenticate (reuse the kit's shared MSAL cache) ---
    from auth import authenticate, discover_tenant
    from flightcheck.pp_admin_client import PPAdminClient, derive_environment_id
    from flightcheck.pva_client import PVAClient

    print("Authenticating to Dataverse...")
    dv_token = authenticate(env_url)
    tenant_id = discover_tenant(env_url)
    print(f"  Tenant: {tenant_id}")

    print("Authenticating to Power Platform Admin API...")
    pp_admin = None
    env_id = None
    try:
        pp_admin = PPAdminClient(tenant_id)
        pp_admin.authenticate()
        env_id = derive_environment_id(env_url, dv_token, pp_admin=pp_admin)
        print(f"  Power Platform: OK (env {env_id})")
    except Exception as e:  # noqa: BLE001 - degrade gracefully to baseline data
        print(f"  Power Platform: unavailable ({e}) - keeping baseline data")
        inv_mod.add_note(inv, "Power Platform Admin API unavailable; connections/packs not live-scanned.")

    if pp_admin and env_id:
        inv["environment"]["environmentId"] = env_id
        bap_conns = collectors.collect_connections_bap(pp_admin, env_id)
        inv_mod.merge_connections(inv, bap_conns)
        live_packs = collectors.collect_extension_packs(pp_admin, env_id)
        # Overlay live pack detection on top of the baseline/connect-hint packs
        # (live 'installed'/flowCount win; baseline hrsd/itsm/flavor preserved).
        inv_mod.set_extension_packs(inv, _merge_packs(inv.get("extensionPacks", {}), live_packs))

    print("Authenticating to Copilot Studio (Island Gateway)...")
    try:
        pva = PVAClient(tenant_id, env_url)
        pva.authenticate()
        if pva.is_configured:
            print("  Copilot Studio: OK")
            all_ks = list(inv.get("knowledgeSources", []))
            for agent in inv.get("agents", []):
                bot_id = agent.get("botId")
                if bot_id:
                    all_ks.extend(collectors.collect_knowledge_sources_runtime(pva, bot_id))
            inv_mod.set_knowledge_sources(inv, all_ks)
        else:
            print("  Copilot Studio: gateway not resolved - skipping knowledge-source runtime state")
            inv_mod.add_note(inv, "Island Gateway not resolved; knowledge-source index state not scanned.")
    except Exception as e:  # noqa: BLE001
        print(f"  Copilot Studio: unavailable ({e})")
        inv_mod.add_note(inv, "Island Gateway unavailable; knowledge-source index state not scanned.")

    # Drop the baseline caveat now that we did a live crawl, and recompute.
    inv["notes"] = [n for n in inv.get("notes", []) if not n.startswith("Baseline build:")]
    inv_mod.touch(inv, mode="full")
    inv["capabilityMatrix"] = capability.compute(inv)
    return inv


def _merge_packs(base: dict, live: dict) -> dict:
    """Merge two extension-pack dicts, preferring live 'installed'/flowCount but
    keeping baseline sub-flags (hrsd/itsm/flavor) when live didn't set them."""
    out = dict(base)
    for name, info in live.items():
        target = dict(out.get(name, {}))
        for k, v in info.items():
            if k == "installed":
                target["installed"] = target.get("installed", False) or v
            elif k == "flowCount":
                target["flowCount"] = max(target.get("flowCount", 0), v)
            elif v and v != "unknown":
                target[k] = v
        out[name] = target
    return out


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_baseline(args) -> int:
    inv = build_baseline()
    path = inv_mod.save(inv)
    print(f"Baseline inventory written to {path}")
    _print_headline(inv)
    if getattr(args, "sync", False):
        _do_sync(inv)
    return 0


def cmd_refresh(args) -> int:
    inv = build_full()
    path = inv_mod.save(inv)
    print(f"Inventory written to {path}")
    _print_headline(inv)
    if getattr(args, "sync", False):
        _do_sync(inv)
    return 0


def cmd_add_intake(args) -> int:
    inv = inv_mod.load() or build_baseline()
    inv_mod.upsert_intake(
        inv, args.add_intake,
        kind=args.kind, status=args.status,
        notes=args.notes or "", captured_from="user",
    )
    inv["capabilityMatrix"] = capability.compute(inv)
    inv_mod.touch(inv)
    path = inv_mod.save(inv)
    print(f"Recorded intake '{args.add_intake}' ({args.status}) in {path}")
    return 0


def cmd_reconcile(args) -> int:
    inv = inv_mod.load()
    if inv is None:
        print("No inventory yet - run --baseline or --refresh first.")
        return 1
    connect = collectors.collect_connect_state()
    inv_mod.merge_connections(inv, connect.get("connections", []))
    collectors.apply_pack_hints(
        inv.setdefault("extensionPacks", {}), connect.get("packHints", {}))
    advanced = inv_mod.reconcile(inv)
    inv["capabilityMatrix"] = capability.compute(inv)
    inv_mod.touch(inv)
    path = inv_mod.save(inv)
    if advanced:
        print(f"Reconciled - now configured: {', '.join(advanced)}")
    else:
        print("Reconciled - no intake items advanced.")
    print(f"Inventory updated at {path}")
    return 0


def cmd_print(args) -> int:
    inv = inv_mod.load()
    if inv is None:
        print("No inventory yet - run --baseline or --refresh first.")
        return 1
    print(json.dumps(inv, indent=2))
    return 0


def cmd_sync(args) -> int:
    inv = inv_mod.load()
    if inv is None:
        print("No inventory yet - run --baseline or --refresh first.")
        return 1
    ok, configured = _do_sync(inv)
    if ok:
        return 0
    # Not being wired up to WeveNova is a benign, expected local-only state
    # (the kit works fully offline); only a real attempt that failed is exit 2.
    return 0 if not configured else 2


def _do_sync(inv: dict) -> tuple[bool, bool]:
    """Push the inventory to the WeveNova tenant-inventory MCP. Best-effort.

    Returns ``(ok, configured)`` so callers can tell a benign "not wired up"
    state apart from a real sync failure.
    """
    try:
        sys.path.insert(0, os.path.join(HERE, "planner"))
        import wevenova
        configured = wevenova.is_configured()
        ok, msg = wevenova.sync_inventory(inv)
        print(("WeveNova: " if ok else "WeveNova (skipped): ") + msg)
        return ok, configured
    except Exception as e:  # noqa: BLE001 - sync must never break discovery
        print(f"WeveNova (skipped): {e}")
        return False, False


def _print_headline(inv: dict) -> None:
    conns = inv.get("connections", [])
    ready = [m["category"] for m in inv.get("capabilityMatrix", []) if m["verdict"] == "ready"]
    print(f"  Agents: {len(inv.get('agents', []))} | "
          f"Connections: {len(conns)} | "
          f"Template configs: {len(inv.get('templateConfigs', []))} | "
          f"Ready categories: {len(ready)}")


# ---------------------------------------------------------------------------
# Offline self-test (no network) - the kit's verification convention
# ---------------------------------------------------------------------------

def cmd_selftest(args) -> int:
    """Exercise the pure model + capability + intake logic without network."""
    failures = []

    def check(cond, msg):
        if not cond:
            failures.append(msg)

    inv = inv_mod.new_inventory(mode="full")
    inv_mod.set_agents(inv, [{
        "name": "ESS HR", "botId": "b1", "schemaName": "msdyn_copilotforemployeeselfservicehr",
        "slug": "ess-hr", "folder": "", "isManaged": True, "counts": {}, "topics": [], "knowledgeSources": [],
    }])
    check(collectors.persona_from_schema("msdyn_copilotforemployeeselfservicehr") == "HR", "persona HR")
    check(collectors.persona_from_schema("msdyn_copilotforemployeeselfserviceit") == "IT", "persona IT")

    # Blocked when nothing present.
    matrix = capability.compute(inv)
    check(len(matrix) == len(capability.CATEGORIES), "matrix covers all categories")
    tick = {m["id"]: m["verdict"] for m in matrix}
    check(tick["hr-ticketing"] == "blocked", "hr-ticketing blocked when empty")

    # Plan ServiceNow -> hr-ticketing becomes 'planned'.
    inv_mod.upsert_intake(inv, "servicenow", notes="IT tickets")
    matrix = capability.compute(inv)
    tick = {m["id"]: m["verdict"] for m in matrix}
    check(tick["hr-ticketing"] == "planned", "hr-ticketing planned after intake")

    # Connect ServiceNow HRSD -> hr-ticketing ready + intake reconciles.
    inv_mod.merge_connections(inv, [{
        "connector": "service-now", "displayName": "ServiceNow acme",
        "status": "connected", "instanceUrl": "https://acme.service-now.com", "source": "connect",
    }])
    inv["extensionPacks"] = {"servicenow": {"installed": True, "hrsd": True, "itsm": False, "flowCount": 3}}
    matrix = capability.compute(inv)
    tick = {m["id"]: m["verdict"] for m in matrix}
    check(tick["hr-ticketing"] == "ready", "hr-ticketing ready after connect")

    advanced = inv_mod.reconcile(inv)
    check("servicenow" in advanced, "intake reconciled to configured")
    check(any(i["system"] == "servicenow" and i["status"] == "configured"
              for i in inv["intake"]), "intake status configured")

    # Merge dedup: a BAP row for the same connector should not double-count
    # beyond identity rules.
    before = len(inv["connections"])
    inv_mod.merge_connections(inv, [{
        "connector": "service-now", "displayName": "ServiceNow acme",
        "status": "Connected", "instanceUrl": "https://acme.service-now.com", "source": "bap",
    }])
    check(len(inv["connections"]) == before, "same-instance connection merged, not duplicated")

    # Render must succeed and include the matrix section.
    inv["capabilityMatrix"] = matrix
    md = inv_mod.render_summary(inv)
    check("Scenario readiness" in md, "summary renders capability matrix")

    # Round-trip save/load into a temp dir.
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "inventory.json")
        inv_mod.save(inv, p)
        loaded = inv_mod.load(p)
        check(loaded is not None and loaded["schemaVersion"] == inv_mod.SCHEMA_VERSION, "save/load round-trip")
        check(os.path.exists(os.path.join(d, "summary.md")), "summary.md written")

    if failures:
        print("SELFTEST: FAIL")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("SELFTEST: PASS")
    return 0


# ---------------------------------------------------------------------------
# Public helper for setup.py
# ---------------------------------------------------------------------------

def build_and_write_baseline() -> str | None:
    """Best-effort baseline write for setup.py. Never raises. Returns path or None."""
    try:
        inv = build_baseline()
        return inv_mod.save(inv)
    except Exception:  # noqa: BLE001 - discovery must never break setup
        return None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build the ESS environment inventory (workspace/inventory/).")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--baseline", action="store_true",
                       help="Assemble from on-disk data (no sign-in). Used by setup.")
    group.add_argument("--from-config", dest="baseline", action="store_true",
                       help="Alias for --baseline.")
    group.add_argument("--refresh", action="store_true",
                       help="Full live crawl (BAP + Island Gateway).")
    group.add_argument("--add-intake", metavar="SYSTEM",
                       help="Record maker intent to use SYSTEM (e.g. servicenow).")
    group.add_argument("--reconcile", action="store_true",
                       help="Re-read /connect output and advance intake items.")
    group.add_argument("--print", dest="do_print", action="store_true",
                       help="Print the current inventory JSON.")
    group.add_argument("--sync", dest="sync_only", action="store_true",
                       help="Push the current inventory to the WeveNova tenant inventory MCP.")
    group.add_argument("--selftest", action="store_true",
                       help="Run the offline self-test.")

    parser.add_argument("--kind", default="connector",
                        help="Intake kind: connector | knowledge (with --add-intake).")
    parser.add_argument("--status", default=inv_mod.INTAKE_PLANNED,
                        help="Intake status (with --add-intake).")
    parser.add_argument("--notes", default="",
                        help="Intake notes (with --add-intake).")
    parser.add_argument("--do-sync", dest="sync", action="store_true",
                        help="Also push to WeveNova after --refresh/--baseline.")
    args = parser.parse_args()

    if args.selftest:
        return cmd_selftest(args)
    if args.do_print:
        return cmd_print(args)
    if args.sync_only:
        return cmd_sync(args)
    if args.reconcile:
        return cmd_reconcile(args)
    if args.add_intake:
        return cmd_add_intake(args)
    if args.refresh:
        return cmd_refresh(args)
    # baseline / from-config
    return cmd_baseline(args)


if __name__ == "__main__":
    raise SystemExit(main())
