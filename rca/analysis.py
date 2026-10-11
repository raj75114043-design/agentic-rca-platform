"""Evidence-based hypothesis ranking.

The Analyst agent turns raw evidence into ranked root-cause hypotheses. Scoring is deterministic
and explainable: each hypothesis lists the evidence that supports it and the checks still needed
to confirm it. The Verifier uses those open checks to decide whether to loop back to the Planner.
"""
from __future__ import annotations

from dataclasses import dataclass, field

CONFIRMED, LIKELY, POSSIBLE = 0.85, 0.6, 0.3


@dataclass
class Hypothesis:
    cause: str
    score: float
    key_entity: str | None
    title: str
    supporting: list[str] = field(default_factory=list)
    next_checks: list[dict] = field(default_factory=list)

    @property
    def confidence(self) -> str:
        return "HIGH" if self.score >= CONFIRMED else "MEDIUM" if self.score >= LIKELY else "LOW"

    def as_dict(self) -> dict:
        return {"cause": self.cause, "score": round(self.score, 2), "confidence": self.confidence,
                "key_entity": self.key_entity, "title": self.title, "supporting": self.supporting,
                "next_checks": self.next_checks}


def _by_kind(evidence: dict[str, dict], kind: str) -> list[dict]:
    return [e for e in evidence.values() if e["kind"] == kind]


def rank(site: dict | None, results: dict[str, dict], evidence: dict[str, dict]) -> list[Hypothesis]:
    """results: last successful tool output keyed by tool name; evidence: all evidence keyed by id."""
    if not site:
        return []
    hyps: list[Hypothesis] = []
    acts = results.get("get_site_activities") or []
    blocked = [a for a in acts if a.get("blocked_reason")]
    open_incidents = [e["data"] for e in _by_kind(evidence, "incident")]
    have = set(results)

    # --- rollout delays --------------------------------------------------------------------------
    for a in blocked:
        reason = a["blocked_reason"]
        if reason == "Awaiting material":
            h = Hypothesis("VENDOR_DELAY", 0.45, None, f"Material for {a['activity_type']} activity {a['activity_id']} not delivered",
                           [f"activity:{a['activity_id']}"])
            overdue = [e for e in _by_kind(evidence, "po")
                       if e["data"]["activity_id"] == a["activity_id"] and e["data"]["delivery_status"] == "OVERDUE"]
            if overdue:
                po = max(overdue, key=lambda e: e["data"]["delay_days"])
                h.score, h.key_entity = 0.9, po["data"]["vendor_id"]
                h.title = (f"{po['data']['vendor_name']} has not delivered {po['data']['material_name']} "
                           f"({po['data']['po_id']}), {po['data']['delay_days']} days overdue")
                h.supporting.append(po["id"])
                if "get_vendor_scorecard" not in have:
                    h.next_checks.append({"tool": "get_vendor_scorecard", "args": {"vendor_id": po["data"]["vendor_id"]},
                                          "why": "Check whether the vendor is chronically late"})
                else:
                    h.score = 0.95
                    h.supporting += [e["id"] for e in _by_kind(evidence, "vendor")
                                     if e["data"]["vendor_id"] == po["data"]["vendor_id"]]
            elif "get_purchase_orders" not in have:
                h.next_checks.append({"tool": "get_purchase_orders", "args": {"activity_ids": [a["activity_id"]]},
                                      "why": "Find the undelivered purchase order for the blocked activity"})
            hyps.append(h)
        elif reason == "Site access not permitted":
            h = Hypothesis("PERMIT_PENDING", 0.45, None, f"{a['activity_type']} works cannot start without site access",
                           [f"activity:{a['activity_id']}"])
            pend = _by_kind(evidence, "permit")
            if pend:
                p = pend[0]["data"]
                h.score, h.key_entity = 0.92, p["permit_id"]
                h.title = f"Permit {p['permit_id']} from {p['authority']} pending, {p['days_past_expected']} days past expected approval"
                h.supporting.append(pend[0]["id"])
            elif "get_permits" not in have:
                h.next_checks.append({"tool": "get_permits", "args": {"site_id": site["site_id"]},
                                      "why": "Confirm the municipal permit status"})
            hyps.append(h)
        elif reason == "Crew not mobilised":
            h = Hypothesis("CREW_SHORTAGE", 0.45, None, f"Crew for {a['activity_type']} activity {a['activity_id']} not mobilised",
                           [f"activity:{a['activity_id']}"])
            crew_ev = [e for e in _by_kind(evidence, "crew") if e["data"]["crew_id"] == a["crew_id"]]
            if crew_ev:
                c = crew_ev[0]["data"]
                h.score, h.key_entity = 0.9, c["crew_id"]
                h.title = f"Assigned crew {c['crew_id']} ({c['team']}) unavailable: {c['unavailable_reason']}"
                h.supporting.append(crew_ev[0]["id"])
            elif "get_crew_status" not in have:
                h.next_checks.append({"tool": "get_crew_status", "args": {"crew_ids": [a["crew_id"]]},
                                      "why": "Check availability of the assigned crew"})
            hyps.append(h)

    # Late but delivered purchase orders are a weak, competing signal (distractor handling)
    late = [e for e in _by_kind(evidence, "po") if e["data"]["delivery_status"] == "DELIVERED_LATE"]
    if late and not any(h.cause == "VENDOR_DELAY" for h in hyps):
        hyps.append(Hypothesis("VENDOR_DELAY", 0.2, late[0]["data"]["vendor_id"],
                               "Earlier material was delivered late but has since arrived", [late[0]["id"]]))

    # --- network outages -------------------------------------------------------------------------
    for inc in open_incidents:
        ev_id = f"incident:{inc['incident_id']}"
        if inc["alarm_type"] == "POWER_FAIL":
            h = Hypothesis("POWER_OUTAGE", 0.55, site["region"], "Mains power failure with battery backup exhausted", [ev_id])
            grid = [e for e in _by_kind(evidence, "event")
                    if e["data"]["event_type"] == "GRID_OUTAGE" and not e["data"]["ended_at"]]
            if grid:
                h.score = 0.93
                h.title = f"Regional grid outage in {site['region']} ({grid[0]['data']['event_id']}) caused mains failure"
                h.supporting.append(grid[0]["id"])
            elif "get_region_events" not in have:
                h.next_checks.append({"tool": "get_region_events", "args": {"region": site["region"]},
                                      "why": "Check for a regional grid outage"})
            hyps.append(h)
        elif inc["alarm_type"] == "LINK_DOWN":
            fiber = next((a for a in acts if a["activity_type"] == "FIBER"), None)
            h = Hypothesis("FIBER_CUT", 0.88, fiber["activity_id"] if fiber else None,
                           "Backhaul fibre cut; OTDR shows a break along the route", [ev_id])
            if not fiber and "get_site_activities" not in have:
                h.score = 0.7
                h.next_checks.append({"tool": "get_site_activities", "args": {"site_id": site["site_id"]},
                                      "why": "Identify the fibre route activity"})
            hyps.append(h)
        elif inc["alarm_type"] == "HW_FAULT":
            h = Hypothesis("HW_FAULT", 0.55, None, "Radio unit hardware fault", [ev_id])
            rru = [p for p in (results.get("get_purchase_orders") or []) if p["material_id"] == "MAT-RRU"]
            if rru:
                p = rru[0]
                h.key_entity = p["vendor_id"]
                h.title = f"Faulty {p['material_name']} from {p['vendor_name']} (batch {p['batch_id']})"
                h.score = 0.75
                batch = [e for e in _by_kind(evidence, "batch") if e["id"] == f"batch:{p['batch_id']}"]
                if batch:
                    h.score = 0.94
                    h.title += f"; same batch failing on {len(batch[0]['data']['sites'])} sites"
                    h.supporting.append(batch[0]["id"])
                elif "check_batch_failures" not in have:
                    h.next_checks.append({"tool": "check_batch_failures", "args": {"batch_id": p["batch_id"]},
                                          "why": "Check for a batch-level defect"})
            elif acts and "get_purchase_orders" not in have:
                rf = [a["activity_id"] for a in acts if a["activity_type"] == "RF_INSTALL"]
                h.next_checks.append({"tool": "get_purchase_orders", "args": {"activity_ids": rf},
                                      "why": "Identify vendor and batch of the installed radio unit"})
            elif not acts and "get_site_activities" not in have:
                h.next_checks.append({"tool": "get_site_activities", "args": {"site_id": site["site_id"]},
                                      "why": "Find the RF installation activity"})
            hyps.append(h)

    return sorted(hyps, key=lambda h: -h.score)
