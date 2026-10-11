"""Relationship store for multi-hop dependency traversal.

Neo4j is used in the full deployment; an in-process NetworkX implementation with the same interface
backs lite mode, tests and the hosted demo.

Ontology
  (Site)-[:IN_REGION]->(Region)          (Site)-[:REQUIRES]->(Permit)
  (Site)-[:HAS_ACTIVITY]->(Activity)     (Activity)-[:ASSIGNED_TO]->(Crew)
  (Activity)-[:NEEDS]->(PurchaseOrder)   (PurchaseOrder)-[:FOR_MATERIAL]->(Material)
  (PurchaseOrder)-[:SUPPLIED_BY]->(Vendor)
  (Incident)-[:AFFECTS]->(Site)          (RegionEvent)-[:IMPACTS]->(Region)
"""
from __future__ import annotations

from abc import ABC, abstractmethod

import networkx as nx

from rca.data.generator import World

ONTOLOGY = [("Site", "IN_REGION", "Region"), ("Site", "REQUIRES", "Permit"), ("Site", "HAS_ACTIVITY", "Activity"),
            ("Activity", "ASSIGNED_TO", "Crew"), ("Activity", "NEEDS", "PurchaseOrder"),
            ("PurchaseOrder", "FOR_MATERIAL", "Material"), ("PurchaseOrder", "SUPPLIED_BY", "Vendor"),
            ("Incident", "AFFECTS", "Site"), ("RegionEvent", "IMPACTS", "Region")]


def _edges(w: World):
    for r in w.regions:
        yield ("Region", r["region"], {}), None
    for s in w.sites:
        yield ("Site", s["site_id"], {"status": s["status"]}), ("IN_REGION", "Region", s["region"])
    for p in w.permits:
        yield ("Permit", p["permit_id"], {"status": p["status"]}), None
        yield ("Site", p["site_id"], None), ("REQUIRES", "Permit", p["permit_id"])
    for c in w.crews:
        yield ("Crew", c["crew_id"], {"available": c["available"]}), None
    for v in w.vendors:
        yield ("Vendor", v["vendor_id"], {"name": v["name"]}), None
    for m in w.materials:
        yield ("Material", m["material_id"], {"name": m["name"]}), None
    for a in w.activities:
        yield ("Activity", a["activity_id"], {"type": a["activity_type"], "status": a["status"]}), \
            ("ASSIGNED_TO", "Crew", a["crew_id"])
        yield ("Site", a["site_id"], None), ("HAS_ACTIVITY", "Activity", a["activity_id"])
    for p in w.purchase_orders:
        yield ("PurchaseOrder", p["po_id"], {"delivered": p["delivered_date"] is not None}), \
            ("SUPPLIED_BY", "Vendor", p["vendor_id"])
        yield ("PurchaseOrder", p["po_id"], None), ("FOR_MATERIAL", "Material", p["material_id"])
        yield ("Activity", p["activity_id"], None), ("NEEDS", "PurchaseOrder", p["po_id"])
    for i in w.incidents:
        yield ("Incident", i["incident_id"], {"alarm": i["alarm_type"], "open": i["resolved_at"] is None}), \
            ("AFFECTS", "Site", i["site_id"])
    for e in w.region_events:
        yield ("RegionEvent", e["event_id"], {"type": e["event_type"]}), ("IMPACTS", "Region", e["region"])


class GraphStore(ABC):
    @abstractmethod
    def load(self, world: World) -> None: ...

    @abstractmethod
    def dependencies(self, site_id: str, max_hops: int = 3) -> list[dict]:
        """Return dependency paths starting at a site, each as a list of {label, id, rel} hops."""

    @abstractmethod
    def vendor_impact(self, vendor_id: str) -> list[str]:
        """Sites that depend on at least one purchase order from the vendor."""

    @abstractmethod
    def stats(self) -> dict: ...


class NetworkXGraph(GraphStore):
    def __init__(self):
        self.g = nx.MultiDiGraph()

    def load(self, world: World) -> None:
        self.g.clear()
        for (label, nid, props), rel in _edges(world):
            key = f"{label}:{nid}"
            if not self.g.has_node(key):
                self.g.add_node(key, label=label, id=nid, **(props or {}))
            elif props:
                self.g.nodes[key].update(props)
            if rel:
                rtype, tlabel, tid = rel
                tkey = f"{tlabel}:{tid}"
                if not self.g.has_node(tkey):
                    self.g.add_node(tkey, label=tlabel, id=tid)
                self.g.add_edge(key, tkey, type=rtype)

    def _hop(self, key: str, rel: str | None) -> dict:
        n = self.g.nodes[key]
        return {"label": n["label"], "id": n["id"], "rel": rel,
                **{k: v for k, v in n.items() if k not in ("label", "id")}}

    def dependencies(self, site_id: str, max_hops: int = 3) -> list[dict]:
        start = f"Site:{site_id}"
        if start not in self.g:
            return []
        paths: list[list[dict]] = []

        def walk(node: str, path: list[dict], depth: int):
            extended = False
            if depth < max_hops:
                for _, nxt, data in self.g.out_edges(node, data=True):
                    if data["type"] == "FOR_MATERIAL":
                        continue
                    walk(nxt, path + [self._hop(nxt, data["type"])], depth + 1)
                    extended = True
            if not extended and len(path) > 1:
                paths.append(path)

        walk(start, [self._hop(start, None)], 0)
        # Incidents point *to* the site, so add them as reverse one-hop paths
        for src, _ in self.g.in_edges(start):
            paths.append([self._hop(start, None), self._hop(src, "AFFECTED_BY")])
        return [{"hops": p} for p in paths]

    def vendor_impact(self, vendor_id: str) -> list[str]:
        v = f"Vendor:{vendor_id}"
        if v not in self.g:
            return []
        sites = set()
        for po, _ in self.g.in_edges(v):
            for act, _ in self.g.in_edges(po):
                for site, _ in self.g.in_edges(act):
                    if self.g.nodes[site]["label"] == "Site":
                        sites.add(self.g.nodes[site]["id"])
        return sorted(sites)

    def stats(self) -> dict:
        return {"backend": "networkx", "nodes": self.g.number_of_nodes(), "edges": self.g.number_of_edges()}


class Neo4jGraph(GraphStore):  # pragma: no cover - exercised in docker-compose deployment
    def __init__(self, uri: str, user: str, password: str):
        from neo4j import GraphDatabase
        self.driver = GraphDatabase.driver(uri, auth=(user, password))

    def load(self, world: World) -> None:
        nodes, rels = {}, []
        for (label, nid, props), rel in _edges(world):
            nodes.setdefault((label, nid), {}).update(props or {})
            if rel:
                rels.append((label, nid, rel[0], rel[1], rel[2]))
        with self.driver.session() as s:
            s.run("MATCH (n) DETACH DELETE n")
            for label in {l for l, _ in nodes}:
                s.run(f"CREATE CONSTRAINT IF NOT EXISTS FOR (n:{label}) REQUIRE n.id IS UNIQUE")
                batch = [{"id": nid, **p} for (l, nid), p in nodes.items() if l == label]
                s.run(f"UNWIND $rows AS r MERGE (n:{label} {{id: r.id}}) SET n += r", rows=batch)
            by_type: dict[tuple, list] = {}
            for sl, sid, rt, tl, tid in rels:
                by_type.setdefault((sl, rt, tl), []).append({"s": sid, "t": tid})
            for (sl, rt, tl), rows in by_type.items():
                s.run(f"UNWIND $rows AS r MATCH (a:{sl} {{id: r.s}}), (b:{tl} {{id: r.t}}) MERGE (a)-[:{rt}]->(b)",
                      rows=rows)

    def dependencies(self, site_id: str, max_hops: int = 3) -> list[dict]:
        q = (f"MATCH p=(s:Site {{id: $sid}})-[:HAS_ACTIVITY|ASSIGNED_TO|NEEDS|SUPPLIED_BY|REQUIRES|IN_REGION*1..{max_hops}]->(x) "
             "WHERE NOT (x)-->() OR length(p) = $hops "
             "RETURN [n IN nodes(p) | {label: labels(n)[0], id: n.id}] AS ns, [r IN relationships(p) | type(r)] AS rs")
        out = []
        with self.driver.session() as s:
            for rec in s.run(q, sid=site_id, hops=max_hops):
                hops = [{**n, "rel": None if i == 0 else rec["rs"][i - 1]} for i, n in enumerate(rec["ns"])]
                out.append({"hops": hops})
            for rec in s.run("MATCH (i:Incident)-[:AFFECTS]->(s:Site {id: $sid}) RETURN i.id AS id, i.alarm AS alarm",
                             sid=site_id):
                out.append({"hops": [{"label": "Site", "id": site_id, "rel": None},
                                     {"label": "Incident", "id": rec["id"], "rel": "AFFECTED_BY", "alarm": rec["alarm"]}]})
        return out

    def vendor_impact(self, vendor_id: str) -> list[str]:
        q = ("MATCH (s:Site)-[:HAS_ACTIVITY]->(:Activity)-[:NEEDS]->(:PurchaseOrder)-[:SUPPLIED_BY]->(v:Vendor {id: $vid}) "
             "RETURN DISTINCT s.id AS sid ORDER BY sid")
        with self.driver.session() as s:
            return [r["sid"] for r in s.run(q, vid=vendor_id)]

    def stats(self) -> dict:
        with self.driver.session() as s:
            n = s.run("MATCH (n) RETURN count(n) AS c").single()["c"]
            e = s.run("MATCH ()-[r]->() RETURN count(r) AS c").single()["c"]
        return {"backend": "neo4j", "nodes": n, "edges": e}
