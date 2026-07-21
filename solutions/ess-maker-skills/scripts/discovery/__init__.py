# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""
ESS Maker Kit - Environment Discovery package.

Decouples "system discovery" (a read-only crawl of what already exists in the
customer's Power Platform environment + what the maker plans to use) from
``setup``/``fetch_and_setup`` (which extracts the agent working copy) and from
``flightcheck`` (which produces an ephemeral pass/fail readiness report).

The package produces a single reusable artifact - the **inventory** - written to
``workspace/inventory/inventory.json`` (machine-readable contract) plus
``workspace/inventory/summary.md`` (human-readable). Both the maker skills
(``/create``, ``/connect``) and an external scenario **planner** (Cocreate
``planner-execution`` + ``ess-catalogue.md``) can read the inventory to answer
"what connectors/knowledge sources/template configs are already here that I can
reuse, and what is the maker planning to add?".

Layers:

- ``inventory``  - the data model + IO + the intake state machine (pure, no network).
- ``capability`` - maps the ESS scenario-catalogue connector groupings onto the
  inventory to compute a per-category readiness verdict (the planner bridge).
- ``collectors`` - read-only collectors that reuse the kit's existing API clients
  (``auth``, ``flightcheck.pp_admin_client``, ``flightcheck.pva_client``) and the
  local extracted agent files. No new external API contracts are introduced.

The ``discover_inventory`` CLI (one directory up) orchestrates the collectors and
writes the inventory.
"""
