"""Model Context Protocol server exposing the same validated tools to any MCP client.

Run with:  python -m rca.mcp_server            (stdio transport)
Each MCP tool delegates to the ToolRegistry, so validation, auditing and evidence are identical
whether the tool is called by this platform's agents or by an external client such as Claude Desktop.
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from rca.container import get_container
from rca.stores.graph import ONTOLOGY

mcp = FastMCP("network-rca")


def _call(name: str, **args) -> dict:
    res = get_container().tools.call(name, args)
    return {k: res[k] for k in ("ok", "error", "data", "evidence")}


@mcp.tool()
def get_site(site_id: str) -> dict:
    """Site master data and current status (site_id format SITE-0001)."""
    return _call("get_site", site_id=site_id)


@mcp.tool()
def get_site_activities(site_id: str) -> dict:
    """Rollout activities for a site with status, crew and overdue days."""
    return _call("get_site_activities", site_id=site_id)


@mcp.tool()
def get_purchase_orders(activity_ids: list[str]) -> dict:
    """Purchase orders for activities, with vendor and delivery delay."""
    return _call("get_purchase_orders", activity_ids=activity_ids)


@mcp.tool()
def get_vendor_scorecard(vendor_id: str | None = None) -> dict:
    """On-time delivery performance for one vendor (VEN-001) or all vendors."""
    return _call("get_vendor_scorecard", vendor_id=vendor_id)


@mcp.tool()
def get_incidents(site_id: str) -> dict:
    """Network incidents raised on a site."""
    return _call("get_incidents", site_id=site_id)


@mcp.tool()
def get_region_events(region: str) -> dict:
    """Regional events such as grid outages (North, South, East, West, Central)."""
    return _call("get_region_events", region=region)


@mcp.tool()
def graph_dependencies(site_id: str, max_hops: int = 3) -> dict:
    """Multi-hop dependency paths from a site in the knowledge graph."""
    return _call("graph_dependencies", site_id=site_id, max_hops=max_hops)


@mcp.tool()
def search_runbooks(query: str) -> dict:
    """Search operational runbooks for diagnosis and resolution steps."""
    return _call("search_runbooks", query=query)


@mcp.resource("schema://ontology")
def ontology() -> str:
    """Knowledge-graph ontology as readable context."""
    return "\n".join(f"({a})-[:{r}]->({b})" for a, r, b in ONTOLOGY)


if __name__ == "__main__":
    mcp.run()
