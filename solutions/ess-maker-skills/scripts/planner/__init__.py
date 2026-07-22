# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""
ESS Maker Kit - Scenario Planner package.

Turns a sponsor's goals into a tenant-grounded rollout plan. It maps goals onto
the scenario catalogue (``catalogue``), models the rollout as an embedded
``Plan.Scenarios[]`` list (``plan_model``), and enriches each scenario's
readiness from the discovered inventory (``readiness``) so the plan is a *living*
artifact. Persistence stages locally today and syncs to the WeveNova Plan MCP
when configured (``wevenova``).

The ``build_plan`` CLI (one directory up) orchestrates these. The reasoning about
which goals map where, ordering nuance, and Learn-grounded setup detail is the
``/plan`` skill's job; this package is the deterministic engine underneath it.
"""
