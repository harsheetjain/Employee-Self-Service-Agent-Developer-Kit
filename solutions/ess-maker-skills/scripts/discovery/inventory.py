# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""
Inventory data model + IO + intake state machine.

Pure module: no network, no imports of the API clients. Everything here can be
unit-tested by constructing dicts. The orchestrator (``discover_inventory.py``)
and the collectors call into these helpers to assemble and persist the
inventory.

Canonical instance file:  ``workspace/inventory/inventory.json``
Human-readable render:     ``workspace/inventory/summary.md``

The committed JSON Schema for this shape lives at
``src/reference/inventory.schema.json`` and the prose contract at
``src/reference/inventory-contract.md``. Bump ``SCHEMA_VERSION`` (and the schema
file) together when the shape changes in a way old consumers can't tolerate.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCHEMA_VERSION = 1

# Default freshness horizon. Consumers (skills, planner) should warn when
# ``generatedAt`` is older than this; discovery is a snapshot, not live state.
DEFAULT_STALE_AFTER_HOURS = 24

INVENTORY_DIR = os.path.join("workspace", "inventory")
INVENTORY_FILE = os.path.join(INVENTORY_DIR, "inventory.json")
SUMMARY_FILE = os.path.join(INVENTORY_DIR, "summary.md")

# Canonical connector tokens used across the inventory + capability matrix.
CONNECTOR_WORKDAY = "workday"
CONNECTOR_SERVICENOW = "service-now"
CONNECTOR_SHAREPOINT = "sharepoint"
CONNECTOR_SAP = "sap"

# Intake lifecycle states (see docstring of ``upsert_intake`` / ``reconcile``).
INTAKE_PLANNED = "planned"
INTAKE_CONFIGURING = "configuring"
INTAKE_CONFIGURED = "configured"
INTAKE_DECLINED = "declined"
_INTAKE_STATES = (INTAKE_PLANNED, INTAKE_CONFIGURING, INTAKE_CONFIGURED, INTAKE_DECLINED)


def _now_iso() -> str:
    """UTC timestamp, second precision, trailing Z (matches config.json style)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------

def new_inventory(mode: str = "baseline") -> dict:
    """Return an empty inventory skeleton at the current schema version.

    ``mode`` is ``"baseline"`` (assembled from data already on disk, no extra
    sign-in) or ``"full"`` (a live crawl that also hit the BAP / Island Gateway
    APIs for connections, extension packs, and knowledge-source runtime state).
    """
    return {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": _now_iso(),
        "generatedBy": "discover_inventory.py",
        "mode": mode,
        "staleAfterHours": DEFAULT_STALE_AFTER_HOURS,
        "environment": {},
        "agents": [],
        "connections": [],
        "extensionPacks": {},
        "knowledgeSources": [],
        "templateConfigs": [],
        "intake": [],
        "capabilityMatrix": [],
        "notes": [],
    }


def touch(inv: dict, mode: str | None = None) -> dict:
    """Refresh ``generatedAt`` (and optionally ``mode``) in place. Returns inv."""
    inv["generatedAt"] = _now_iso()
    if mode is not None:
        inv["mode"] = mode
    return inv


def add_note(inv: dict, note: str) -> None:
    """Append a de-duplicated free-text note (e.g. a scan caveat)."""
    notes = inv.setdefault("notes", [])
    if note not in notes:
        notes.append(note)


# ---------------------------------------------------------------------------
# Section setters (idempotent; collectors call these)
# ---------------------------------------------------------------------------

def set_environment(inv: dict, environment: dict) -> None:
    """Replace the environment block, dropping empty values."""
    inv["environment"] = {k: v for k, v in (environment or {}).items() if v not in (None, "")}


def set_agents(inv: dict, agents: list[dict]) -> None:
    inv["agents"] = agents or []


def set_extension_packs(inv: dict, packs: dict) -> None:
    inv["extensionPacks"] = packs or {}


def set_template_configs(inv: dict, template_configs: list[dict]) -> None:
    inv["templateConfigs"] = template_configs or []


def set_knowledge_sources(inv: dict, sources: list[dict]) -> None:
    """Replace the environment-level knowledge sources, de-duplicating by
    (type, url/name)."""
    seen = set()
    deduped = []
    for s in sources or []:
        key = (s.get("type", ""), s.get("url") or s.get("name", ""))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(s)
    inv["knowledgeSources"] = deduped


def _connection_key(conn: dict) -> tuple:
    """Identity for a connection row. Prefer the BAP connectionId; otherwise
    fall back to (connector, instanceUrl/displayName) so a connect-captured row
    and a BAP-discovered row for the same system collapse into one."""
    cid = conn.get("connectionId")
    if cid:
        return ("id", cid)
    return (
        "logical",
        conn.get("connector", ""),
        (conn.get("instanceUrl") or conn.get("displayName") or "").lower(),
    )


def merge_connections(inv: dict, connections: list[dict]) -> None:
    """Merge connection rows into the inventory, combining rows that describe
    the same connection from different sources (BAP live status vs. the instance
    URL / auth type the ``/connect`` skill captured).

    Later fields win only when non-empty, so a live BAP ``status`` doesn't erase
    a connect-captured ``instanceUrl`` and vice-versa.
    """
    existing = list(inv.get("connections", []))
    index: dict[tuple, dict] = {_connection_key(c): c for c in existing}

    for incoming in connections or []:
        key = _connection_key(incoming)
        if key in index:
            target = index[key]
            for k, v in incoming.items():
                if v not in (None, "", [], {}):
                    # Track provenance additively.
                    if k == "source" and target.get("source") and v not in target["source"].split("+"):
                        target["source"] = target["source"] + "+" + v
                    else:
                        target[k] = v
        else:
            index[key] = incoming
            existing.append(incoming)

    inv["connections"] = existing


# ---------------------------------------------------------------------------
# Intake state machine
# ---------------------------------------------------------------------------
#
# An intake item captures a maker's *intent* to use a system that discovery did
# NOT find already configured. Lifecycle:
#
#   (absent) --ask--> planned --launch /connect--> configuring
#                        |                              |
#                        v                              v
#                     declined                      configured  (reconciled from
#                                                                the connect skill's
#                                                                captured config)
#
# ``upsert_intake`` records/*advances* intent; ``reconcile`` reads what the
# connect skill persisted and flips matching items to ``configured``.

def upsert_intake(
    inv: dict,
    system: str,
    *,
    kind: str = "connector",
    status: str = INTAKE_PLANNED,
    desired: bool = True,
    notes: str = "",
    captured_from: str = "discovery",
) -> dict:
    """Insert or update the intake item for ``system`` (e.g. ``"servicenow"``).

    Idempotent by ``system``. Returns the item. ``status`` must be one of the
    intake lifecycle states.
    """
    if status not in _INTAKE_STATES:
        raise ValueError(f"invalid intake status {status!r}; expected one of {_INTAKE_STATES}")

    system = (system or "").strip().lower()
    intake = inv.setdefault("intake", [])
    for item in intake:
        if item.get("system") == system:
            item["kind"] = kind
            item["status"] = status
            item["desired"] = desired
            if notes:
                item["notes"] = notes
            item["capturedFrom"] = captured_from
            item["updatedAt"] = _now_iso()
            return item

    item = {
        "system": system,
        "kind": kind,
        "status": status,
        "desired": desired,
        "notes": notes,
        "capturedFrom": captured_from,
        "requestedAt": _now_iso(),
        "updatedAt": _now_iso(),
    }
    intake.append(item)
    return item


# Which connector token(s) count as "this system is now configured" when
# reconciling an intake item against discovered connections.
_SYSTEM_CONNECTOR_TOKENS = {
    "servicenow": {CONNECTOR_SERVICENOW},
    "service-now": {CONNECTOR_SERVICENOW},
    "workday": {CONNECTOR_WORKDAY},
    "sap": {CONNECTOR_SAP},
    "successfactors": {CONNECTOR_SAP},
    "sharepoint": {CONNECTOR_SHAREPOINT},
}


def _system_is_configured(inv: dict, system: str) -> bool:
    """True when the inventory now has a usable connection/knowledge source for
    ``system`` (connected connection, or a matching knowledge source)."""
    tokens = _SYSTEM_CONNECTOR_TOKENS.get(system, {system})
    for conn in inv.get("connections", []):
        if conn.get("connector") in tokens:
            status = (conn.get("status") or "").lower()
            if status in ("connected", "captured"):
                return True
    if CONNECTOR_SHAREPOINT in tokens or system == "sharepoint":
        for ks in inv.get("knowledgeSources", []):
            if ks.get("type") == "SharePoint":
                return True
    if CONNECTOR_SERVICENOW in tokens:
        for ks in inv.get("knowledgeSources", []):
            if ks.get("type") == "ServiceNowGraph":
                return True
    return False


def reconcile(inv: dict) -> list[str]:
    """Advance intake items whose system is now configured.

    Reads only the inventory's own ``connections`` / ``knowledgeSources`` (which
    the connect-state collector has already populated from what ``/connect``
    captured). Any ``planned`` / ``configuring`` item whose system is now
    configured flips to ``configured``.

    Returns the list of system names that were advanced (for messaging).
    """
    advanced = []
    for item in inv.get("intake", []):
        if item.get("status") in (INTAKE_PLANNED, INTAKE_CONFIGURING):
            if _system_is_configured(inv, item.get("system", "")):
                item["status"] = INTAKE_CONFIGURED
                item["updatedAt"] = _now_iso()
                advanced.append(item["system"])
    return advanced


# ---------------------------------------------------------------------------
# IO
# ---------------------------------------------------------------------------

def load(path: str = INVENTORY_FILE) -> dict | None:
    """Load an inventory file, or None if it doesn't exist / is unreadable."""
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def save(inv: dict, path: str = INVENTORY_FILE, *, render_md: bool = True) -> str:
    """Write the inventory JSON (and, by default, the summary.md render).

    Returns the JSON path written. Creates ``workspace/inventory/`` if needed.
    """
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(inv, f, indent=2)
        f.write("\n")
    if render_md:
        summary_path = os.path.join(os.path.dirname(path) or ".", "summary.md")
        with open(summary_path, "w", encoding="utf-8") as f:
            f.write(render_summary(inv))
    return path


# ---------------------------------------------------------------------------
# Human-readable render
# ---------------------------------------------------------------------------

_VERDICT_ICON = {
    "ready": "\u2705",       # check
    "partial": "\U0001f7e1",  # yellow circle
    "planned": "\U0001f6e0\ufe0f",  # hammer+wrench
    "blocked": "\u26d4",      # no entry
}


def render_summary(inv: dict) -> str:
    """Render the inventory as a scannable markdown summary (workspace file)."""
    lines: list[str] = []
    env = inv.get("environment", {})
    lines.append("# Environment Inventory")
    lines.append("")
    lines.append(f"Generated: {inv.get('generatedAt', '?')} (mode: {inv.get('mode', '?')})")
    if env.get("dataverseUrl"):
        lines.append(f"Environment: {env.get('displayName') or ''} {env['dataverseUrl']}".rstrip())
    lines.append("")

    # Agents
    agents = inv.get("agents", [])
    if agents:
        lines.append("## Agents")
        lines.append("")
        lines.append("| Name | Persona | Schema | Topics | Template Configs | Workflows |")
        lines.append("|------|---------|--------|-------:|-----------------:|----------:|")
        for a in agents:
            c = a.get("counts", {})
            lines.append(
                f"| {a.get('name', '')} | {a.get('persona', '')} | "
                f"{a.get('schemaName', '')} | {c.get('topics', 0)} | "
                f"{c.get('templateConfigs', 0)} | {c.get('workflows', 0)} |"
            )
        lines.append("")

    # Connections
    conns = inv.get("connections", [])
    lines.append("## Connections")
    lines.append("")
    if conns:
        lines.append("| Connector | Display Name | Status | Auth | Instance | Source |")
        lines.append("|-----------|--------------|--------|------|----------|--------|")
        for c in conns:
            lines.append(
                f"| {c.get('connector', '')} | {c.get('displayName', '')} | "
                f"{c.get('status', '')} | {c.get('authType', '')} | "
                f"{c.get('instanceUrl', '')} | {c.get('source', '')} |"
            )
    else:
        lines.append("_None discovered._"
                     + (" Run `/discover` for a full crawl." if inv.get("mode") == "baseline" else ""))
    lines.append("")

    # Extension packs
    packs = inv.get("extensionPacks", {})
    if packs:
        lines.append("## Installed extension packs")
        lines.append("")
        for name, info in packs.items():
            if not isinstance(info, dict):
                continue
            installed = info.get("installed")
            mark = "\u2705" if installed else "\u2014"
            extra = []
            if name == "servicenow":
                if info.get("hrsd"):
                    extra.append("HRSD")
                if info.get("itsm"):
                    extra.append("ITSM")
            if info.get("flavor") and info.get("flavor") != "unknown":
                extra.append(info["flavor"])
            suffix = f" ({', '.join(extra)})" if extra else ""
            lines.append(f"- {mark} **{name}**{suffix} \u2014 {info.get('flowCount', 0)} flow(s)")
        lines.append("")

    # Knowledge sources
    ks = inv.get("knowledgeSources", [])
    if ks:
        lines.append("## Knowledge sources")
        lines.append("")
        lines.append("| Name | Type | Location | State |")
        lines.append("|------|------|----------|-------|")
        for s in ks:
            lines.append(
                f"| {s.get('name', '')} | {s.get('type', '')} | "
                f"{s.get('url', '')} | {s.get('state', '')} |"
            )
        lines.append("")

    # Intake (planned / in-progress systems)
    intake = [i for i in inv.get("intake", []) if i.get("status") != INTAKE_DECLINED]
    if intake:
        lines.append("## Planned integrations (maker intent)")
        lines.append("")
        lines.append("| System | Kind | Status | Notes |")
        lines.append("|--------|------|--------|-------|")
        for i in intake:
            lines.append(
                f"| {i.get('system', '')} | {i.get('kind', '')} | "
                f"{i.get('status', '')} | {i.get('notes', '')} |"
            )
        lines.append("")

    # Capability matrix (planner bridge)
    matrix = inv.get("capabilityMatrix", [])
    if matrix:
        lines.append("## Scenario readiness (capability matrix)")
        lines.append("")
        lines.append("| Category | Domain | Verdict | Missing |")
        lines.append("|----------|--------|---------|---------|")
        for m in matrix:
            icon = _VERDICT_ICON.get(m.get("verdict", ""), "")
            missing = ", ".join(m.get("missing", []) or []) or "\u2014"
            lines.append(
                f"| {m.get('category', '')} | {m.get('domain', '')} | "
                f"{icon} {m.get('verdict', '')} | {missing} |"
            )
        lines.append("")

    notes = inv.get("notes", [])
    if notes:
        lines.append("## Notes")
        lines.append("")
        for n in notes:
            lines.append(f"- {n}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"
