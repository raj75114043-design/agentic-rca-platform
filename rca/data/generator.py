"""Synthetic telecom 5G rollout and operations dataset with injected, labelled root causes.

The generator builds a consistent operational world (regions, sites, activities, materials,
purchase orders, vendors, crews, permits, incidents and regional power events) and injects
known failure causes into a subset of sites. Every injected cause is recorded as a ground-truth
label so the agent can be evaluated objectively.

Root-cause types
----------------
VENDOR_DELAY   A purchase order for a critical material was delivered late, delaying an activity.
PERMIT_PENDING The site's municipal permit is still pending, blocking civil works.
CREW_SHORTAGE  The crew assigned to a blocking activity is unavailable.
POWER_OUTAGE   A regional grid outage caused POWER_FAIL incidents on an on-air site.
FIBER_CUT      A LINK_DOWN incident after third-party works cut the backhaul fibre.
HW_FAULT       A defective hardware batch from a vendor caused HW_FAULT incidents.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

REGIONS = ["North", "South", "East", "West", "Central"]
CITIES = {
    "North": ["Noida", "Ghaziabad", "Meerut"],
    "South": ["Bengaluru", "Mysuru", "Hosur"],
    "East": ["Kolkata", "Howrah", "Durgapur"],
    "West": ["Pune", "Nashik", "Thane"],
    "Central": ["Bhopal", "Indore", "Jabalpur"],
}
ACTIVITY_TYPES = ["CIVIL", "TOWER", "POWER", "FIBER", "RF_INSTALL", "INTEGRATION"]
MATERIALS = {
    "CIVIL": [("MAT-CEM", "Foundation cement and rebar kit", "CIVIL")],
    "TOWER": [("MAT-TWR", "30m steel lattice tower kit", "STRUCTURE")],
    "POWER": [("MAT-BAT", "48V lithium battery bank", "POWER"), ("MAT-RECT", "Rectifier module", "POWER")],
    "FIBER": [("MAT-OFC", "24-core optical fibre cable", "TRANSPORT")],
    "RF_INSTALL": [("MAT-RRU", "5G remote radio unit", "RADIO"), ("MAT-ANT", "Massive MIMO antenna", "RADIO")],
    "INTEGRATION": [("MAT-BBU", "Baseband unit", "RADIO")],
}
VENDOR_NAMES = [
    "Alpha Infra Supplies", "Beta Power Systems", "Cobalt Networks", "Delta Towers", "Echo RF Solutions",
    "Fusion Fibre Works", "Garuda Civil Contractors", "Helix Electronics", "Indus Telecom Hardware",
    "Jupiter Logistics", "Krypton Radio Systems", "Lotus Energy",
]
ROOT_CAUSES = ["VENDOR_DELAY", "PERMIT_PENDING", "CREW_SHORTAGE", "POWER_OUTAGE", "FIBER_CUT", "HW_FAULT"]
TODAY = date(2026, 10, 1)


@dataclass
class World:
    regions: list[dict] = field(default_factory=list)
    sites: list[dict] = field(default_factory=list)
    activities: list[dict] = field(default_factory=list)
    materials: list[dict] = field(default_factory=list)
    vendors: list[dict] = field(default_factory=list)
    purchase_orders: list[dict] = field(default_factory=list)
    crews: list[dict] = field(default_factory=list)
    permits: list[dict] = field(default_factory=list)
    incidents: list[dict] = field(default_factory=list)
    region_events: list[dict] = field(default_factory=list)
    labels: list[dict] = field(default_factory=list)


def _d(x: date) -> str:
    return x.isoformat()


def generate(n_sites: int = 240, seed: int = 7, fault_rate: float = 0.35) -> World:
    rng = random.Random(seed)
    w = World()
    w.regions = [{"region": r} for r in REGIONS]

    for i, name in enumerate(VENDOR_NAMES):
        w.vendors.append({"vendor_id": f"VEN-{i + 1:03d}", "name": name,
                          "tier": rng.choice(["STRATEGIC", "PREFERRED", "APPROVED"])})
    for acts in MATERIALS.values():
        for mid, mname, cat in acts:
            w.materials.append({"material_id": mid, "name": mname, "category": cat})

    crew_id = 0
    for region in REGIONS:
        for team in ("CIVIL", "TOWER", "ELECTRICAL", "RF", "FIBER"):
            for _ in range(3):
                crew_id += 1
                w.crews.append({"crew_id": f"CRW-{crew_id:03d}", "region": region, "team": team,
                                "available": 1, "unavailable_reason": None})
    crews_by = {}
    for c in w.crews:
        crews_by.setdefault((c["region"], c["team"]), []).append(c)
    team_for = {"CIVIL": "CIVIL", "TOWER": "TOWER", "POWER": "ELECTRICAL", "FIBER": "FIBER",
                "RF_INSTALL": "RF", "INTEGRATION": "RF"}
    vendor_for = {act: rng.sample(w.vendors, 3) for act in ACTIVITY_TYPES}

    # A regional power event and a defective hardware batch that some injected faults reference
    outage_region = rng.choice(REGIONS)
    w.region_events.append({"event_id": "EVT-001", "region": outage_region, "event_type": "GRID_OUTAGE",
                            "started_at": "2026-09-27T22:10:00", "ended_at": None,
                            "description": f"State utility grid failure affecting {outage_region} region feeders"})
    w.region_events.append({"event_id": "EVT-002", "region": rng.choice([r for r in REGIONS if r != outage_region]),
                            "event_type": "HEAVY_RAIN", "started_at": "2026-09-20T06:00:00",
                            "ended_at": "2026-09-22T18:00:00", "description": "Heavy rainfall warning; no outages reported"})
    bad_vendor = rng.choice([v for v in w.vendors])

    act_n = po_n = inc_n = 0
    for s in range(1, n_sites + 1):
        region = REGIONS[(s - 1) % len(REGIONS)]
        site_id = f"SITE-{s:04d}"
        city = rng.choice(CITIES[region])
        planned_on_air = TODAY - timedelta(days=rng.randint(-40, 120))
        site = {"site_id": site_id, "name": f"{city}-{rng.choice(['MG Road', 'Sector', 'Station', 'Market', 'Ring Road'])}-{s}",
                "region": region, "city": city, "site_type": rng.choice(["MACRO", "SMALL_CELL", "ROOFTOP"]),
                "status": "ON_AIR", "planned_on_air": _d(planned_on_air)}
        w.sites.append(site)
        w.permits.append({"permit_id": f"PRM-{s:04d}", "site_id": site_id, "authority": f"{city} Municipal Corporation",
                          "status": "APPROVED", "applied_on": _d(planned_on_air - timedelta(days=150)),
                          "expected_on": _d(planned_on_air - timedelta(days=110))})

        start = planned_on_air - timedelta(days=90)
        site_acts = []
        for k, atype in enumerate(ACTIVITY_TYPES):
            act_n += 1
            p_end = start + timedelta(days=15 * (k + 1))
            crew = rng.choice(crews_by[(region, team_for[atype])])
            a = {"activity_id": f"ACT-{act_n:05d}", "site_id": site_id, "activity_type": atype,
                 "crew_id": crew["crew_id"], "status": "COMPLETED",
                 "planned_end": _d(p_end), "actual_end": _d(p_end + timedelta(days=rng.randint(-2, 3))),
                 "blocked_reason": None}
            site_acts.append(a)
            w.activities.append(a)
            for mid, _, _ in MATERIALS[atype]:
                po_n += 1
                vendor = rng.choice(vendor_for[atype])
                promised = p_end - timedelta(days=10)
                w.purchase_orders.append({"po_id": f"PO-{po_n:06d}", "activity_id": a["activity_id"],
                                          "material_id": mid, "vendor_id": vendor["vendor_id"], "quantity": rng.randint(1, 6),
                                          "promised_date": _d(promised),
                                          "delivered_date": _d(promised + timedelta(days=rng.randint(-3, 2))),
                                          "batch_id": f"B{rng.randint(1000, 9999)}"})

        # Background noise incidents on healthy sites (resolved, minor) so evidence is not trivially clean
        if rng.random() < 0.25:
            inc_n += 1
            w.incidents.append({"incident_id": f"INC-{inc_n:06d}", "site_id": site_id,
                                "opened_at": f"2026-0{rng.randint(6, 9)}-1{rng.randint(0, 9)}T10:00:00",
                                "resolved_at": f"2026-0{rng.randint(6, 9)}-2{rng.randint(0, 8)}T12:00:00",
                                "severity": "MINOR", "alarm_type": rng.choice(["HIGH_TEMP", "DOOR_OPEN", "VSWR"]),
                                "description": "Transient alarm cleared by NOC"})

        if rng.random() >= fault_rate:
            continue

        cause = ROOT_CAUSES[s % len(ROOT_CAUSES)] if rng.random() < 0.85 else rng.choice(ROOT_CAUSES)
        if cause == "POWER_OUTAGE" and region != outage_region:
            cause = "FIBER_CUT"  # grid outages only happen where the regional event is
        rollout = cause in ("VENDOR_DELAY", "PERMIT_PENDING", "CREW_SHORTAGE")
        label = {"site_id": site_id, "root_cause": cause, "key_entity": None}

        if rollout:
            site["status"] = "DELAYED"
            site["planned_on_air"] = _d(TODAY - timedelta(days=rng.randint(5, 40)))
            if cause == "PERMIT_PENDING":
                blocked_idx = 0
            else:
                blocked_idx = rng.randint(1, len(site_acts) - 1)
            for j, a in enumerate(site_acts):
                if j < blocked_idx:
                    continue
                a["status"] = "IN_PROGRESS" if j == blocked_idx else "NOT_STARTED"
                a["actual_end"] = None
            blocked = site_acts[blocked_idx]
            if cause == "VENDOR_DELAY":
                pos = [p for p in w.purchase_orders if p["activity_id"] == blocked["activity_id"]]
                po = rng.choice(pos)
                po["delivered_date"] = None
                po["promised_date"] = _d(TODAY - timedelta(days=rng.randint(12, 45)))
                blocked["blocked_reason"] = "Awaiting material"
                label["key_entity"] = po["vendor_id"]
                label["evidence_id"] = po["po_id"]
            elif cause == "PERMIT_PENDING":
                permit = next(p for p in w.permits if p["site_id"] == site_id)
                permit["status"] = "PENDING"
                permit["expected_on"] = _d(TODAY - timedelta(days=rng.randint(20, 60)))
                blocked["blocked_reason"] = "Site access not permitted"
                label["key_entity"] = permit["permit_id"]
            else:
                crew = next(c for c in w.crews if c["crew_id"] == blocked["crew_id"])
                crew["available"] = 0
                crew["unavailable_reason"] = rng.choice(["Reassigned to emergency restoration", "Crew on leave", "Vehicle breakdown"])
                blocked["blocked_reason"] = "Crew not mobilised"
                label["key_entity"] = crew["crew_id"]
            # distractor: a minor late PO elsewhere on the site that is already delivered
            if cause != "VENDOR_DELAY" and rng.random() < 0.5:
                done = [p for p in w.purchase_orders if p["activity_id"] in {a["activity_id"] for a in site_acts[:blocked_idx]}]
                if done:
                    d = rng.choice(done)
                    d["delivered_date"] = _d(date.fromisoformat(d["promised_date"]) + timedelta(days=rng.randint(3, 6)))
        else:
            site["status"] = "DOWN" if cause != "HW_FAULT" else rng.choice(["DOWN", "DEGRADED"])
            inc_n += 1
            inc = {"incident_id": f"INC-{inc_n:06d}", "site_id": site_id, "opened_at": "2026-09-28T03:15:00",
                   "resolved_at": None, "severity": "CRITICAL" if site["status"] == "DOWN" else "MAJOR"}
            if cause == "POWER_OUTAGE":
                inc.update(alarm_type="POWER_FAIL", description="Mains failure; battery backup exhausted")
                label["key_entity"] = outage_region
            elif cause == "FIBER_CUT":
                inc.update(alarm_type="LINK_DOWN", description="Backhaul link down; OTDR shows break 1.8 km from site")
                fib = next(a for a in site_acts if a["activity_type"] == "FIBER")
                label["key_entity"] = fib["activity_id"]
            else:
                inc.update(alarm_type="HW_FAULT", description="RRU hardware alarm; module reset unsuccessful")
                rru_po = next(p for p in w.purchase_orders
                              if p["material_id"] == "MAT-RRU" and p["activity_id"] in {a["activity_id"] for a in site_acts})
                rru_po["vendor_id"] = bad_vendor["vendor_id"]
                rru_po["batch_id"] = "B0666"
                label["key_entity"] = bad_vendor["vendor_id"]
            w.incidents.append(inc)
        w.labels.append(label)

    return w


def summary(w: World) -> dict:
    from collections import Counter
    return {"sites": len(w.sites), "activities": len(w.activities), "purchase_orders": len(w.purchase_orders),
            "incidents": len(w.incidents), "labelled_faults": len(w.labels),
            "by_cause": dict(Counter(l["root_cause"] for l in w.labels)),
            "by_status": dict(Counter(s["status"] for s in w.sites))}


if __name__ == "__main__":
    import json
    print(json.dumps(summary(generate()), indent=2))
