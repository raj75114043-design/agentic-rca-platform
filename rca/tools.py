"""Validated, read-only tool registry shared by the agents and the MCP server.

Each tool declares a Pydantic input schema. Inputs are validated before execution, every call is
audited, and outputs carry evidence records with stable IDs so the final answer can cite them.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from rca.retrieval import RunbookIndex
from rca.stores.graph import GraphStore
from rca.stores.sql import OpsRepository

log = logging.getLogger("rca.tools")
SITE_PATTERN = r"^SITE-\d{4}$"


class SiteArgs(BaseModel):
    site_id: str = Field(pattern=SITE_PATTERN, description="Site identifier, e.g. SITE-0042")


class ActivityArgs(BaseModel):
    activity_ids: list[str] = Field(min_length=1, max_length=20)


class CrewArgs(BaseModel):
    crew_ids: list[str] = Field(min_length=1, max_length=20)


class VendorArgs(BaseModel):
    vendor_id: str | None = Field(default=None, pattern=r"^VEN-\d{3}$")


class RegionArgs(BaseModel):
    region: Literal["North", "South", "East", "West", "Central"]


class BatchArgs(BaseModel):
    batch_id: str = Field(pattern=r"^B\d{4}$")


class ListSitesArgs(BaseModel):
    status: Literal["ON_AIR", "DOWN", "DEGRADED", "DELAYED"] | None = None
    region: Literal["North", "South", "East", "West", "Central"] | None = None
    limit: int = Field(default=20, ge=1, le=100)


class DependencyArgs(BaseModel):
    site_id: str = Field(pattern=SITE_PATTERN)
    max_hops: int = Field(default=3, ge=1, le=4)


class SearchArgs(BaseModel):
    query: str = Field(min_length=3, max_length=300)


@dataclass
class Tool:
    name: str
    description: str
    args: type[BaseModel]
    fn: Callable[..., tuple[Any, list[dict]]]

    def schema(self) -> dict:
        return {"name": self.name, "description": self.description, "input_schema": self.args.model_json_schema()}


class ToolError(Exception):
    pass


def _ev(kind: str, ident: str, summary: str, data: dict | None = None) -> dict:
    return {"id": f"{kind}:{ident}", "kind": kind, "summary": summary, "data": data or {}}


class ToolRegistry:
    def __init__(self, repo: OpsRepository, graph: GraphStore, runbooks: RunbookIndex):
        self.repo, self.graph, self.runbooks = repo, graph, runbooks
        self.tools: dict[str, Tool] = {}
        self._register()

    def _add(self, name, description, args):
        def deco(fn):
            self.tools[name] = Tool(name, description, args, fn)
            return fn
        return deco

    def catalog(self) -> list[dict]:
        return [t.schema() for t in self.tools.values()]

    def call(self, name: str, args: dict) -> dict:
        """Validate and execute a tool. Returns {ok, data, evidence, error, ms}."""
        t0 = time.perf_counter()
        tool = self.tools.get(name)
        if not tool:
            return {"tool": name, "args": args, "ok": False, "error": f"UNKNOWN_TOOL: {name}", "data": None, "evidence": []}
        try:
            parsed = tool.args(**(args or {}))
        except ValidationError as exc:
            return {"tool": name, "args": args, "ok": False, "error": f"VALIDATION_ERROR: {exc.errors()[0]['msg']}",
                    "data": None, "evidence": []}
        try:
            data, evidence = tool.fn(**parsed.model_dump())
            ok, err = True, None
        except ToolError as exc:
            data, evidence, ok, err = None, [], False, str(exc)
        ms = round((time.perf_counter() - t0) * 1000, 1)
        log.info("tool_call", extra={"tool": name, "tool_args": parsed.model_dump(), "ok": ok, "ms": ms})
        return {"tool": name, "args": parsed.model_dump(), "ok": ok, "error": err, "data": data,
                "evidence": evidence, "ms": ms}

    # --------------------------------------------------------------------------------------- tools
    def _register(self):
        repo, graph = self.repo, self.graph

        @self._add("get_site", "Site master data and current status.", SiteArgs)
        def get_site(site_id):
            s = repo.get_site(site_id)
            if not s:
                raise ToolError(f"NOT_FOUND: {site_id}")
            return s, [_ev("site", site_id, f"{site_id} ({s['city']}, {s['region']}) is {s['status']}; "
                                            f"planned on-air {s['planned_on_air']}", s)]

        @self._add("get_site_activities", "Rollout activities for a site with status, crew and overdue days.", SiteArgs)
        def get_site_activities(site_id):
            acts = repo.site_activities(site_id)
            ev = [_ev("activity", a["activity_id"],
                      f"{a['activity_type']} activity {a['activity_id']} is {a['status']}"
                      + (f", {a['days_overdue']} days overdue" if a["days_overdue"] else "")
                      + (f", blocked: {a['blocked_reason']}" if a["blocked_reason"] else ""), a)
                  for a in acts if a["status"] != "COMPLETED" or a["blocked_reason"]]
            return acts, ev

        @self._add("get_purchase_orders", "Purchase orders for activities with vendor and delivery delay.", ActivityArgs)
        def get_purchase_orders(activity_ids):
            pos = repo.purchase_orders_for(activity_ids)
            ev = [_ev("po", p["po_id"], f"PO {p['po_id']} for {p['material_name']} from {p['vendor_name']} "
                                         f"({p['vendor_id']}) is {p['delivery_status']}"
                      + (f" by {p['delay_days']} days" if p["delay_days"] and p["delay_days"] > 0 else ""), p)
                  for p in pos if p["delivery_status"] in ("OVERDUE", "DELIVERED_LATE")]
            return pos, ev

        @self._add("get_vendor_scorecard", "On-time delivery performance for one vendor or all vendors.", VendorArgs)
        def get_vendor_scorecard(vendor_id=None):
            sc = repo.vendor_scorecard(vendor_id)
            ev = [_ev("vendor", v["vendor_id"], f"{v['vendor_name']} on-time rate {v['on_time_rate']:.0%}, "
                                                f"{v['open_overdue']} open overdue orders", v) for v in sc[:5]]
            return sc, ev

        @self._add("get_permits", "Municipal permits for a site and days past expected approval.", SiteArgs)
        def get_permits(site_id):
            ps = repo.permits_for(site_id)
            ev = [_ev("permit", p["permit_id"], f"Permit {p['permit_id']} from {p['authority']} is {p['status']}"
                      + (f", {p['days_past_expected']} days past expected approval" if p["days_past_expected"] else ""), p)
                  for p in ps if p["status"] != "APPROVED"]
            return ps, ev

        @self._add("get_crew_status", "Availability of crews assigned to activities.", CrewArgs)
        def get_crew_status(crew_ids):
            cs = repo.crews_by_id(crew_ids)
            ev = [_ev("crew", c["crew_id"], f"Crew {c['crew_id']} ({c['team']}, {c['region']}) is unavailable: "
                                            f"{c['unavailable_reason']}", c) for c in cs if not c["available"]]
            return cs, ev

        @self._add("get_incidents", "Network incidents (alarms) raised on a site.", SiteArgs)
        def get_incidents(site_id):
            inc = repo.incidents_for(site_id)
            ev = [_ev("incident", i["incident_id"], f"{i['severity']} {i['alarm_type']} incident {i['incident_id']} "
                                                    f"opened {i['opened_at']}: {i['description']}", i)
                  for i in inc if i["resolved_at"] is None]
            return inc, ev

        @self._add("get_region_events", "Regional events such as grid outages or severe weather.", RegionArgs)
        def get_region_events(region):
            evs = repo.region_events_for(region)
            ev = [_ev("event", e["event_id"], f"{e['event_type']} in {e['region']} since {e['started_at']}"
                      + (" (ongoing)" if not e["ended_at"] else f" until {e['ended_at']}") + f": {e['description']}", e)
                  for e in evs]
            return evs, ev

        @self._add("check_batch_failures", "Other sites with open hardware faults from the same hardware batch.", BatchArgs)
        def check_batch_failures(batch_id):
            rows = repo.batch_failures(batch_id)
            ev = [_ev("batch", batch_id, f"Batch {batch_id} has open HW_FAULT incidents on {len(rows)} sites", {"sites": rows})] \
                if len(rows) > 1 else []
            return rows, ev

        @self._add("list_sites", "List sites filtered by status and/or region.", ListSitesArgs)
        def list_sites(status=None, region=None, limit=20):
            rows = repo.list_sites(status, region, limit)
            return rows, [_ev("site_list", f"{status or 'ALL'}-{region or 'ALL'}", f"{len(rows)} sites match", {"n": len(rows)})]

        @self._add("graph_dependencies", "Multi-hop dependency paths from a site in the knowledge graph.", DependencyArgs)
        def graph_dependencies(site_id, max_hops=3):
            paths = graph.dependencies(site_id, max_hops)
            return paths, []

        @self._add("search_runbooks", "Search operational runbooks for diagnosis and resolution steps.", SearchArgs)
        def search_runbooks(query):
            hits = self.runbooks.search(query, 3)
            return hits, [_ev("runbook", f"{h['runbook_id']}#{h['section']}", f"{h['runbook']} — {h['section']}", h)
                          for h in hits[:2]]
