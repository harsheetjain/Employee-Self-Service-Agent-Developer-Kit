# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""
WeveNova Plan MCP sync — thin, config-gated, best-effort.

The scenario plan's source of truth is WeveNova (so the sponsor and admins can
share it). Until the production MCP lands, the planner stages the plan locally
(``workspace/plan/plan.json``) and optionally syncs it to a **local dev-tunnel
MCP with no auth** for end-to-end testing.

Configuration (env, so nothing tenant-specific is committed):
  WEVENOVA_MCP_URL    - the dev-tunnel MCP endpoint (streamable-HTTP JSON-RPC).
                        When unset, every function here is a no-op and the plan
                        stays local-only.
  WEVENOVA_PLAN_TOOL  - the tool to call to persist a plan (default
                        ``plan.upsertPlan``). The whole plan is passed as the
                        tool's ``plan`` argument.

Contract expected of the dev-tunnel MCP (aligns with the dev design's
``plan.*`` namespace and the scenario-list one-pager):
  - a JSON-RPC 2.0 endpoint speaking MCP ``initialize`` + ``tools/call``;
  - a plan-persist tool that upserts a ``Plan`` with an embedded ``scenarios[]``
    list (the shape in ``plan_model``) and returns the stored id/version.

Fail-open by design: a tunnel that's down, slow, or shaped differently must
never crash the planner. Errors are returned as ``(False, message)``; the local
plan.json remains the working artifact.
"""

from __future__ import annotations

import json
import os
import uuid

try:
    import requests
except ImportError:  # requests is a kit dependency; degrade to no-op if absent.
    requests = None  # type: ignore

_DEFAULT_PLAN_TOOL = "plan.upsertPlan"
_TIMEOUT = 15


def endpoint() -> str:
    return (os.environ.get("WEVENOVA_MCP_URL") or "").strip()


def plan_tool() -> str:
    return (os.environ.get("WEVENOVA_PLAN_TOOL") or _DEFAULT_PLAN_TOOL).strip()


def is_configured() -> bool:
    """True when a dev-tunnel endpoint is set and HTTP is available."""
    return bool(endpoint()) and requests is not None


def _rpc(method: str, params: dict) -> tuple[bool, object]:
    """Single JSON-RPC 2.0 call over streamable HTTP. Returns (ok, result-or-error).

    Tolerates both a plain JSON body and a text/event-stream ("data: {json}")
    response, which streamable-HTTP MCP servers may return.
    """
    url = endpoint()
    if not url or requests is None:
        return False, "WeveNova MCP not configured"
    payload = {"jsonrpc": "2.0", "id": str(uuid.uuid4()), "method": method, "params": params}
    headers = {
        "Content-Type": "application/json",
        # Accept both so a streamable-HTTP server can pick its framing.
        "Accept": "application/json, text/event-stream",
    }
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=_TIMEOUT)
    except Exception as e:  # noqa: BLE001 - fail-open; the tunnel may be down
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
    text = (text or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # SSE framing: take the last non-empty `data:` line.
    for line in reversed(text.splitlines()):
        line = line.strip()
        if line.startswith("data:"):
            frag = line[len("data:"):].strip()
            try:
                return json.loads(frag)
            except json.JSONDecodeError:
                continue
    return None


def _initialize() -> None:
    """Best-effort MCP handshake. Ignored if the server doesn't require it."""
    _rpc("initialize", {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "ess-maker-kit-planner", "version": "1"},
    })


def call_tool(name: str, arguments: dict) -> tuple[bool, object]:
    """Call an MCP tool by name. Returns (ok, result-or-message)."""
    if not is_configured():
        return False, "WeveNova MCP not configured (set WEVENOVA_MCP_URL)"
    _initialize()
    return _rpc("tools/call", {"name": name, "arguments": arguments})


def sync_plan(plan: dict) -> tuple[bool, str]:
    """Push the plan to the configured dev-tunnel MCP. Best-effort.

    Returns (ok, human-readable message). Never raises.
    """
    if not is_configured():
        return False, ("WeveNova MCP not configured - plan stays local. "
                       "Set WEVENOVA_MCP_URL to sync.")
    ok, result = call_tool(plan_tool(), {"plan": plan})
    if not ok:
        return False, f"sync failed via {plan_tool()}: {result}"
    return True, f"synced via {plan_tool()}: {json.dumps(result)[:200] if result else 'ok'}"
