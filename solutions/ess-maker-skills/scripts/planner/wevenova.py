# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""
WeveNova Agent-Configuration MCP client — plan + inventory persistence.

Speaks the ``weve-agentconfig`` MCP (WeveNovaB2) over streamable HTTP / JSON-RPC.
It persists two things:

- **Tenant inventory** — each discovered resource is one ``upsert_tenant_inventory``
  call (idempotent by ``kind`` + ``naturalKey``), mapped to the server's kind
  taxonomy (Environment / EntraApp / Connector / Connection / SharePointSite /
  KnowledgeSource / ExtensionPack / ScenarioTemplate).
- **Scenario plan** — ``create_agent_project`` (get-or-create; name must be a
  supported experience e.g. "Employee Self Serve") → ``create_agent_plan`` with
  ``acceptanceCriteria`` + inline ``tasks`` (created atomically).

Configuration (env, so nothing tenant-specific or ephemeral is committed):
  WEVENOVA_MCP_URL       the MCP endpoint. **Unset by default → local-only**
                         (no sync); set it to a tunnel/prod endpoint to enable.
                         For local dev testing, point it at the dev tunnel, e.g.
                         WEVENOVA_MCP_URL=https://<tunnel>/weveb2/mcp/agentConfiguration
                         The VS Code runtime path uses the `weve-agentconfig`
                         entry in `.vscode/mcp.json` instead of this env var.
  WEVENOVA_PROJECT_NAME  the Cocreate experience/project name
                         (default "Employee Self Serve").

Auth: the endpoint accepts ``initialize`` / ``tools/list`` unauthenticated, but
``tools/call`` requires an authorized caller. Headless calls without a bearer
token get "caller is not authorized"; the VS Code MCP runtime supplies auth
(devtunnel / OAuth). This client is **fail-open** — any transport error, SSE
quirk, or authorization failure returns ``(False, message)`` and never raises, so
the plan/inventory always remain in their local ``workspace/`` mirrors.
"""

from __future__ import annotations

import json
import os
import uuid

try:
    import requests
except ImportError:  # requests is a kit dependency; degrade to no-op if absent.
    requests = None  # type: ignore

# No hardcoded endpoint: an ephemeral dev-tunnel URL must never ship as a default
# (it would 404 for everyone once the tunnel closes). Unset => local-only; the
# operator supplies the endpoint via WEVENOVA_MCP_URL or .vscode/mcp.json.
_DEFAULT_PROJECT = "Employee Self Serve"
_TIMEOUT = 20


def endpoint() -> str:
    val = (os.environ.get("WEVENOVA_MCP_URL") or "").strip()
    if val.lower() in ("", "off", "none", "0", "false"):
        return ""
    return val


def project_name() -> str:
    return (os.environ.get("WEVENOVA_PROJECT_NAME") or _DEFAULT_PROJECT).strip()


def is_configured() -> bool:
    return bool(endpoint()) and requests is not None


# ---------------------------------------------------------------------------
# JSON-RPC / MCP transport (SSE-aware)
# ---------------------------------------------------------------------------

def _post(method: str, params: dict) -> tuple[bool, object]:
    url = endpoint()
    if not url or requests is None:
        return False, "WeveNova MCP not configured"
    payload = {"jsonrpc": "2.0", "id": str(uuid.uuid4()), "method": method, "params": params}
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=_TIMEOUT)
    except Exception as e:  # noqa: BLE001 - fail-open
        return False, f"request failed: {e}"
    if resp.status_code >= 400:
        return False, f"HTTP {resp.status_code}: {resp.text[:200]}"
    body = _parse_body(resp.text)
    if body is None:
        return False, f"unparseable response: {resp.text[:200]}"
    if "error" in body:
        return False, body["error"]
    return True, body.get("result")


def _parse_body(text: str) -> dict | None:
    """Parse a plain-JSON or SSE (``event:``/``data:``) JSON-RPC response body."""
    text = (text or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    for line in reversed(text.splitlines()):
        line = line.strip()
        if line.startswith("data:"):
            frag = line[len("data:"):].strip()
            try:
                return json.loads(frag)
            except json.JSONDecodeError:
                continue
    return None





def call_tool(name: str, arguments: dict) -> tuple[bool, object]:
    """Call an MCP tool. Returns (ok, result). ``ok`` is False on transport
    failure, JSON-RPC error, OR a tool result flagged ``isError`` (e.g. the
    'caller is not authorized' authorization failure).

    The server dispatches ``tools/call`` without requiring a prior
    ``initialize`` on the same connection (stateless HTTP), so we don't pay for
    a handshake on every call.
    """
    if not is_configured():
        return False, "WeveNova MCP not configured (set WEVENOVA_MCP_URL)"
    ok, result = _post("tools/call", {"name": name, "arguments": arguments})
    if not ok:
        return False, result
    if isinstance(result, dict) and result.get("isError"):
        return False, _content_text(result) or "tool error"
    return True, result


def _content_text(result: dict) -> str:
    parts = []
    for c in (result or {}).get("content", []) or []:
        if isinstance(c, dict) and c.get("type") == "text":
            parts.append(c.get("text", ""))
    return " ".join(parts).strip()


# ---------------------------------------------------------------------------
# Inventory mapping (inventory.json -> tenant inventory kinds)
# ---------------------------------------------------------------------------

# Canonical connector token -> Dataverse-style connectorId used in attributes.
_CONNECTOR_ID = {
    "workday": "shared_workdaysoap",
    "service-now": "shared_service-now",
    "sap": "shared_sapsuccessfactors",
    "sharepoint": "shared_sharepointonline",
}


def _item(kind: str, natural_key: str, display_name: str, attributes: dict,
          source: str = "discovered") -> dict:
    return {
        "inventoryItem": {
            "kind": kind,
            "naturalKey": natural_key,
            "displayName": display_name,
            "source": source,
            # attributes is a JSON-object *string* per the server schema.
            "attributes": json.dumps(attributes, separators=(",", ":")),
        }
    }


def inventory_to_items(inv: dict) -> list[dict]:
    """Map a discovery inventory into ``upsert_tenant_inventory`` argument dicts.

    Environment-scoped items compose the environmentId into the naturalKey so
    the same resource in a different environment doesn't collide (per the
    WeveNova alignment spec).
    """
    items: list[dict] = []
    env = inv.get("environment", {})
    env_id = env.get("environmentId") or env.get("dataverseUrl") or "env"

    if env.get("environmentId") or env.get("dataverseUrl"):
        items.append(_item(
            "Environment", env_id, env.get("displayName") or env_id,
            {"environmentId": env.get("environmentId", ""),
             "dataverseUrl": env.get("dataverseUrl", ""),
             "region": env.get("region", ""), "type": env.get("type", "")},
        ))

    for c in inv.get("connections", []):
        connector = c.get("connector", "")
        cid = c.get("connectionId") or (c.get("instanceUrl") or c.get("displayName") or connector)
        nk = f"{env_id}:{cid}"
        items.append(_item(
            "Connection", nk, c.get("displayName") or connector,
            {"connectorId": _CONNECTOR_ID.get(connector, connector),
             "connectionId": c.get("connectionId", ""),
             "status": c.get("status", ""), "authType": c.get("authType", ""),
             "instanceUrl": c.get("instanceUrl", ""), "environmentRef": env_id,
             "source": c.get("source", "")},
        ))

    for name, info in (inv.get("extensionPacks", {}) or {}).items():
        if not isinstance(info, dict) or not info.get("installed"):
            continue
        attrs = {"installed": True, "flowCount": info.get("flowCount", 0),
                 "environmentRef": env_id}
        for k in ("hrsd", "itsm", "flavor"):
            if k in info:
                attrs[k] = info[k]
        items.append(_item("ExtensionPack", f"{env_id}:{name}", name, attrs))

    for ks in inv.get("knowledgeSources", []):
        key = ks.get("url") or ks.get("name") or "ks"
        items.append(_item(
            "KnowledgeSource", f"{env_id}:{key}", ks.get("name") or key,
            {"sourceType": ks.get("type", ""), "url": ks.get("url", ""),
             "state": ks.get("state", ""), "environmentRef": env_id},
        ))

    for tc in inv.get("templateConfigs", []):
        uname = tc.get("uniqueName") or ""
        if not uname:
            continue
        items.append(_item(
            "ScenarioTemplate", f"{env_id}:{uname}", tc.get("name") or uname,
            {"uniqueName": uname, "connector": tc.get("connector", ""),
             "operation": tc.get("operation", ""), "status": tc.get("status", ""),
             "environmentRef": env_id},
        ))

    return items


# ---------------------------------------------------------------------------
# Plan mapping (plan.json -> project + plan + tasks)
# ---------------------------------------------------------------------------

def _plan_acceptance_criteria(plan: dict) -> list[str]:
    """Encode the scenario scope as acceptance-criteria lines (the current
    WeveNova plan carries acceptanceCriteria + tasks, not a Scenarios[] field)."""
    lines = []
    for g in plan.get("goals", []):
        lines.append(f"Goal: {g}")
    for s in plan.get("scenarios", []):
        if not s.get("enabled"):
            continue
        r = s.get("readiness", {})
        state = "ready" if r.get("pilot") else "config" if r.get("config") else s.get("verdict", "")
        lines.append(f"[{state}] {s.get('area')}: {s.get('jtbd') or s.get('id')} "
                     f"({s.get('connector')})")
    return lines


def _plan_tasks(plan: dict) -> list[dict]:
    """Derive gap tasks from not-yet-ready scenarios. Each becomes a WeveNova
    task ({title, description}); the owning role is noted in the description
    until the server models requiredRole / assignment natively."""
    tasks: list[dict] = []
    seen: set[str] = set()
    for s in plan.get("scenarios", []):
        if not s.get("enabled") or s.get("verdict") == "ready":
            continue
        for dep in s.get("dependencies", []):
            if dep.get("status") in ("met", "advisory"):
                continue
            need = dep.get("need", "")
            if not need or need in seen:
                continue
            seen.add(need)
            tasks.append({
                "title": f"Provide: {need}",
                "description": (f"Required by **{s.get('area')}** — {s.get('jtbd') or s.get('id')}. "
                                f"Dependency kind: {dep.get('kind')}, status: {dep.get('status')}."),
            })
    return tasks


# ---------------------------------------------------------------------------
# Public sync API
# ---------------------------------------------------------------------------

def sync_inventory(inv: dict) -> tuple[bool, str]:
    """Upsert every inventory item into the tenant inventory. Best-effort.

    Fails fast: if the very first upsert fails (typically a systemic problem —
    the tunnel is down, or the caller isn't authorized), it aborts immediately
    instead of grinding through a hundred+ items that will all fail the same way.
    """
    if not is_configured():
        return False, "WeveNova MCP not configured - inventory stays local."
    items = inventory_to_items(inv)
    if not items:
        return True, "inventory: nothing to upsert"

    ok_n, fail_n, first_err = 0, 0, ""
    for idx, it in enumerate(items):
        ok, res = call_tool("upsert_tenant_inventory", it)
        if ok:
            ok_n += 1
        else:
            fail_n += 1
            if not first_err:
                first_err = str(res)
            # Abort on the first failure — a systemic error (auth/tunnel) would
            # otherwise repeat across every remaining item, slowly.
            if idx == 0:
                return False, (f"inventory sync aborted on first item "
                               f"({len(items)} total): {first_err}")
    msg = f"inventory: {ok_n}/{len(items)} upserted"
    if fail_n:
        msg += f", {fail_n} failed ({first_err})"
    return (fail_n == 0), msg


def sync_plan(plan: dict) -> tuple[bool, str]:
    """Persist the plan as project + plan(+tasks) in WeveNova. Best-effort."""
    if not is_configured():
        return False, "WeveNova MCP not configured - plan stays local."

    ok, proj = call_tool("create_agent_project", {"project": {"name": project_name()}})
    if not ok:
        return False, f"create_agent_project failed: {proj}"
    project_id = _extract_id(proj, "projectId", "project")
    if not project_id:
        return False, f"no projectId in response: {json.dumps(proj)[:200]}"

    plan_arg = {
        "plan": {
            "acceptanceCriteria": _plan_acceptance_criteria(plan),
            "tasks": _plan_tasks(plan),
        },
        "projectId": project_id,
    }
    ok, res = call_tool("create_agent_plan", plan_arg)
    if not ok:
        return False, f"create_agent_plan failed: {res}"
    plan_id = _extract_id(res, "planId", "plan")
    return True, (f"synced: project={project_id} plan={plan_id or '?'} "
                  f"({len(plan_arg['plan']['tasks'])} tasks)")


def _extract_id(result: object, *keys: str) -> str:
    """Pull an id out of a tool result. Handles the MCP text-content envelope
    (a JSON string inside content[].text) and plain dict results."""
    obj = result
    if isinstance(result, dict) and "content" in result:
        text = _content_text(result)
        try:
            obj = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            obj = {}
    if not isinstance(obj, dict):
        return ""
    for k in keys:
        if isinstance(obj.get(k), str):
            return obj[k]
        if isinstance(obj.get(k), dict):
            for idk in ("id", f"{k}Id", "projectId", "planId"):
                if isinstance(obj[k].get(idk), str):
                    return obj[k][idk]
    for idk in ("id", "projectId", "planId"):
        if isinstance(obj.get(idk), str):
            return obj[idk]
    return ""
