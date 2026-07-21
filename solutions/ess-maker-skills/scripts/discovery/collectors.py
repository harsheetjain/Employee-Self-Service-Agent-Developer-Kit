# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""
Read-only discovery collectors.

Each collector answers one question about the environment and reuses an
*existing*, already-verified kit client or on-disk artifact - so no new external
API contract is introduced (nothing new to cassette per
``flightcheck/AGENTS.md``). The orchestrator wires authenticated clients and
calls these; every collector tolerates missing inputs and returns an empty
result rather than raising, so a partial crawl still yields a useful inventory.

Sources:
  - BAP Admin API (``pp_admin``): connections, flows -> installed extension packs.
  - Island Gateway (``pva``): knowledge-source runtime state.
  - On disk: ``.local/config.json`` (agent + captured connections),
    ``.local/connect/*/config.json`` (what ``/connect`` captured: instance URL,
    auth), and the extracted ``workspace/agents/{slug}/`` tree (topics, template
    configs, knowledge-source config).
"""

from __future__ import annotations

import json
import os

# ---------------------------------------------------------------------------
# Connector token mapping (BAP apiId / displayName -> canonical token)
# ---------------------------------------------------------------------------

def connector_token(api_id: str, display_name: str = "") -> str:
    """Map a Power Platform connector apiId/displayName to a canonical token."""
    blob = f"{api_id} {display_name}".lower()
    if "workday" in blob:
        return "workday"
    if "service-now" in blob or "servicenow" in blob:
        return "service-now"
    if "sharepoint" in blob:
        return "sharepoint"
    if "successfactors" in blob or "sapsuccessfactors" in blob or "shared_sap" in blob:
        return "sap"
    return "other"


# Inlined tiny BAP-response helpers (equivalents of
# flightcheck.checks.connections.get_connection_status /
# filter_connections_by_connector) so this package doesn't import FlightCheck's
# CheckResult/runner machinery.

def _bap_connection_status(conn: dict) -> str:
    statuses = conn.get("properties", {}).get("statuses", [])
    if isinstance(statuses, list) and statuses:
        return statuses[0].get("status", "Unknown")
    return "Unknown"


# ---------------------------------------------------------------------------
# 1. Connections (live, via BAP)
# ---------------------------------------------------------------------------

def collect_connections_bap(pp_admin, env_id: str) -> list[dict]:
    """List Power Platform connections for the ESS-relevant connectors.

    Returns normalized rows: ``{connector, displayName, connectionId, status,
    authType, source:"bap"}``. Empty list when the client/env is unavailable.
    """
    if not pp_admin or not env_id:
        return []
    all_conns = pp_admin.get_connections(env_id)
    if isinstance(all_conns, dict) and "_error" in all_conns:
        return []

    rows: list[dict] = []
    for c in all_conns or []:
        props = c.get("properties", {})
        api_id = props.get("apiId", "")
        display = props.get("displayName", "")
        token = connector_token(api_id, display)
        if token == "other":
            continue  # only surface ESS-relevant connectors
        rows.append({
            "connector": token,
            "displayName": display,
            "connectionId": c.get("name", ""),
            "status": _bap_connection_status(c),
            "authType": "",
            "source": "bap",
        })
    return rows


# ---------------------------------------------------------------------------
# 2. Installed extension packs (live, via BAP flows)
# ---------------------------------------------------------------------------

def _flow_names(flows: list) -> list[str]:
    out = []
    for f in flows or []:
        name = f.get("properties", {}).get("displayName", f.get("displayName", ""))
        if name:
            out.append(name)
    return out


def collect_extension_packs(pp_admin, env_id: str) -> dict:
    """Detect installed integration solutions by scanning flow display names.

    Mirrors ``flightcheck.checks.external_systems`` pattern matching. Returns
    ``{workday:{...}, servicenow:{...}, sap:{...}}``.
    """
    packs = {
        "workday": {"installed": False, "flowCount": 0, "flavor": "unknown"},
        "servicenow": {"installed": False, "flowCount": 0, "hrsd": False, "itsm": False},
        "sap": {"installed": False, "flowCount": 0},
    }
    if not pp_admin or not env_id:
        return packs
    all_flows = pp_admin.get_flows(env_id)
    if isinstance(all_flows, dict) and "_error" in all_flows:
        return packs

    names = _flow_names(all_flows)
    _classify_packs_from_names(packs, names)
    return packs


def _classify_packs_from_names(packs: dict, names: list[str]) -> None:
    """Populate the packs dict from a list of flow (or topic) display names.

    Shared by the live (flow) and baseline (local topic) paths so both infer the
    same extension-pack presence.
    """
    for name in names:
        low = name.lower()
        if "workday" in low:
            packs["workday"]["installed"] = True
            packs["workday"]["flowCount"] += 1
        if "servicenow" in low or "service-now" in low or "service now" in low:
            packs["servicenow"]["installed"] = True
            packs["servicenow"]["flowCount"] += 1
            if "hrsd" in low or "hr service" in low or "hr-service" in low:
                packs["servicenow"]["hrsd"] = True
            if "itsm" in low or "incident" in low or "ticket" in low:
                packs["servicenow"]["itsm"] = True
        if "successfactors" in low or "sap" in low:
            packs["sap"]["installed"] = True
            packs["sap"]["flowCount"] += 1


# ---------------------------------------------------------------------------
# 3. Knowledge sources (live runtime state, via Island Gateway)
# ---------------------------------------------------------------------------

def _classify_knowledge_source(comp: dict) -> tuple[str, str]:
    """Return (type, url) for a knowledge-source component. Best-effort."""
    blob = json.dumps(comp).lower()
    url = ""
    conf = comp.get("configuration") or comp.get("settings") or {}
    if isinstance(conf, dict):
        url = conf.get("url") or conf.get("siteUrl") or conf.get("sharePointSiteUrl") or ""
    if "sharepoint" in blob:
        return "SharePoint", url
    if "service-now" in blob or "servicenow" in blob or ("graph" in blob and "connector" in blob):
        return "ServiceNowGraph", url
    return "unknown", url


def collect_knowledge_sources_runtime(pva, bot_id: str) -> list[dict]:
    """Fetch knowledge-source components (runtime state) from Island Gateway."""
    if not pva or not getattr(pva, "is_configured", False) or not bot_id:
        return []
    comps = pva.get_knowledge_sources(bot_id)
    rows = []
    for comp in comps or []:
        ks_type, url = _classify_knowledge_source(comp)
        rows.append({
            "name": comp.get("displayName") or comp.get("schemaName") or "knowledge source",
            "type": ks_type,
            "url": url,
            "state": comp.get("state") or comp.get("status") or "",
            "source": "runtime",
        })
    return rows


# ---------------------------------------------------------------------------
# 4. Connect-state (what /connect captured, on disk)
# ---------------------------------------------------------------------------

def _read_json(path: str) -> dict | None:
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def collect_connect_state(local_state_dir: str = ".local") -> dict:
    """Read what the ``/connect`` skill captured.

    Returns ``{connections:[...], packHints:{...}}`` where connections carry the
    instance URL / auth type / usage the connect flow persisted (status
    ``"captured"``), and packHints reflect installed sub-packs (e.g. ServiceNow
    HRSD/ITSM) recorded in the per-integration config.

    Sources:
      - ``.local/config.json`` -> ``connections`` map (final connect output).
      - ``.local/connect/servicenow/config.json``, ``.../workday/config.json``.
    """
    connections: list[dict] = []
    pack_hints: dict = {}

    # 4a. config.json connections map (ServiceNow/Workday entries)
    cfg = _read_json(os.path.join(local_state_dir, "config.json")) or {}
    for system, entry in (cfg.get("connections") or {}).items():
        if not isinstance(entry, dict):
            continue
        token = connector_token(system, system)
        connections.append({
            "connector": token,
            "displayName": f"{system} (captured by /connect)",
            "status": "captured",
            "authType": entry.get("authType", ""),
            "instanceUrl": entry.get("instanceUrl", ""),
            "usage": entry.get("usage", ""),
            "source": "connect",
        })

    # 4b. per-integration connect config (richer: pack status, install path)
    sn = _read_json(os.path.join(local_state_dir, "connect", "servicenow", "config.json"))
    if sn:
        connections.append({
            "connector": "service-now",
            "displayName": f"ServiceNow {sn.get('instanceName', '')}".strip(),
            "status": "captured" if sn.get("status") == "connected" else "configuring",
            "authType": sn.get("authType", ""),
            "instanceUrl": sn.get("instanceUrl", ""),
            "usage": sn.get("usage", ""),
            "source": "connect",
        })
        packs = sn.get("packs") or {}
        pack_hints["servicenow"] = {
            "hrsd": packs.get("hrsd") == "installed",
            "itsm": packs.get("itsm") == "installed",
        }

    wd = _read_json(os.path.join(local_state_dir, "connect", "workday", "config.json"))
    if wd:
        connections.append({
            "connector": "workday",
            "displayName": f"Workday {wd.get('tenant', '')}".strip(),
            "status": "captured" if wd.get("status") == "connected" else "configuring",
            "authType": "entra-sso",
            "instanceUrl": wd.get("soapBaseUrl") or wd.get("restBaseUrl") or "",
            "source": "connect",
        })
        pack_hints["workday"] = {"flavor": wd.get("installPath", "unknown")}

    return {"connections": connections, "packHints": pack_hints}


def apply_pack_hints(packs: dict, pack_hints: dict) -> dict:
    """Overlay connect-captured pack hints onto detected packs (in place)."""
    for name, hint in (pack_hints or {}).items():
        target = packs.setdefault(name, {"installed": False, "flowCount": 0})
        for k, v in hint.items():
            if v:
                target[k] = v
                if k in ("hrsd", "itsm", "flavor"):
                    target["installed"] = target.get("installed", False) or bool(v) and v != "unknown"
    return packs


# ---------------------------------------------------------------------------
# 5. Agents + topics + template configs + local knowledge (on disk)
# ---------------------------------------------------------------------------

def persona_from_schema(schema_name: str) -> str:
    s = (schema_name or "").lower()
    if "selfservicehr" in s or s.endswith("hr"):
        return "HR"
    if "selfserviceit" in s or s.endswith("it"):
        return "IT"
    return "unknown"


def _list_topic_names(agent_folder: str) -> list[str]:
    topics_dir = os.path.join(agent_folder, "topics")
    if not os.path.isdir(topics_dir):
        return []
    names = []
    for fn in sorted(os.listdir(topics_dir)):
        if fn.endswith(".mcs.yml"):
            names.append(fn[: -len(".mcs.yml")])
    return names


def classify_template_config(unique_name: str, name: str = "") -> tuple[str, str]:
    """Infer (connector, operation) from a template-config name. Best-effort."""
    blob = f"{unique_name} {name}".lower()
    connector = "unknown"
    if "workday" in blob:
        connector = "workday"
    elif "servicenow" in blob or "service-now" in blob or "hrsd" in blob or "itsm" in blob:
        connector = "servicenow"
    elif "successfactors" in blob or "sap" in blob:
        connector = "sap"

    operation = "unknown"
    for op in ("create", "update", "delete", "list", "get", "search"):
        if op in blob:
            operation = op
            break
    return connector, operation


def normalize_template_configs(records: list[dict]) -> list[dict]:
    """Normalize Dataverse template-config records (as fetched by
    fetch_and_setup) into inventory rows."""
    rows = []
    for tc in records or []:
        uname = tc.get("msdyn_uniquename") or ""
        name = tc.get("msdyn_name") or ""
        connector, operation = classify_template_config(uname, name)
        state = tc.get("statecode", 0)
        rows.append({
            "uniqueName": uname,
            "name": name,
            "connector": connector,
            "operation": operation,
            "status": "Active" if state in (0, None) else "Inactive",
        })
    return rows


def collect_template_configs_local(agent_folder: str) -> list[dict]:
    """List template configs from the extracted agent folder when the Dataverse
    records aren't in hand.

    Each config extracts as a value file (``{name}.json`` or ``{name}.xml``) plus
    a ``{name}.meta.json`` sidecar. Enumerate the sidecars - one per config,
    independent of value type - so the total matches Dataverse.
    """
    tc_dir = os.path.join(agent_folder, "template-configs")
    if not os.path.isdir(tc_dir):
        return []
    rows = []
    for fn in sorted(os.listdir(tc_dir)):
        if not fn.endswith(".meta.json"):
            continue
        base = fn[: -len(".meta.json")]
        connector, operation = classify_template_config(base, base)
        rows.append({
            "uniqueName": base,
            "name": base,
            "connector": connector,
            "operation": operation,
            "status": "",
        })
    return rows


def collect_local_knowledge_sources(agent_folder: str) -> list[dict]:
    """List knowledge-source *config* from the extracted agent folder (what the
    developer authored). Runtime index state comes from the PVA collector."""
    ks_dir = os.path.join(agent_folder, "knowledge")
    if not os.path.isdir(ks_dir):
        return []
    rows = []
    for fn in sorted(os.listdir(ks_dir)):
        if fn.endswith(".meta.json"):
            continue
        path = os.path.join(ks_dir, fn)
        if not os.path.isfile(path):
            continue
        base = os.path.splitext(os.path.splitext(fn)[0])[0]  # strip .mcs.yml
        low = fn.lower()
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read().lower()
        except OSError:
            content = ""
        blob = low + " " + content
        if "sharepoint" in blob:
            ks_type = "SharePoint"
        elif "service-now" in blob or "servicenow" in blob or "graph" in blob:
            ks_type = "ServiceNowGraph"
        else:
            ks_type = "unknown"
        rows.append({
            "name": base,
            "type": ks_type,
            "url": "",
            "state": "authored",
            "source": "local",
        })
    return rows


def collect_agents_local(config: dict) -> list[dict]:
    """Build the agents[] block from config.json + the extracted folders."""
    agents_cfg = config.get("agents") or ([config["agent"]] if config.get("agent") else [])
    rows = []
    for a in agents_cfg:
        folder = a.get("folder", "")
        topics = _list_topic_names(folder) if folder else []
        tc_local = collect_template_configs_local(folder) if folder else []
        ks_local = collect_local_knowledge_sources(folder) if folder else []
        # variables / workflows / evaluations counts from folders when present
        counts = {
            "topics": len(topics),
            "templateConfigs": (config.get("templateConfigCount")
                                if len(agents_cfg) == 1 else len(tc_local)),
            "workflows": config.get("workflowCount", 0) if len(agents_cfg) == 1 else _count_dir(folder, "workflows"),
            "variables": _count_dir(folder, "variables"),
            "evaluations": config.get("evaluationCount", 0) if len(agents_cfg) == 1 else 0,
            "knowledgeSources": len(ks_local),
        }
        rows.append({
            "name": a.get("name", ""),
            "botId": a.get("botId", ""),
            "schemaName": a.get("schemaName", ""),
            "slug": a.get("slug", ""),
            "folder": folder,
            "isManaged": a.get("isManaged", False),
            "persona": persona_from_schema(a.get("schemaName", "")),
            "counts": counts,
            "topics": topics,
            "knowledgeSources": ks_local,
        })
    return rows


def _count_dir(folder: str, sub: str) -> int:
    d = os.path.join(folder, sub)
    if not os.path.isdir(d):
        return 0
    return sum(1 for fn in os.listdir(d) if os.path.isfile(os.path.join(d, fn)))
