"""Operational store (PostgreSQL in production, SQLite in lite mode) via SQLAlchemy Core.

All queries are parameterised and read-only from the agent's point of view; writes are limited to
seeding, run tracing and human-in-the-loop actions.
"""
from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from sqlalchemy import (Column, Integer, MetaData, String, Table, Text, create_engine, func, insert, select,
                        update)
from sqlalchemy.engine import Engine

from rca.data.generator import TODAY, World

metadata = MetaData()

sites = Table("sites", metadata, Column("site_id", String, primary_key=True), Column("name", String),
              Column("region", String, index=True), Column("city", String), Column("site_type", String),
              Column("status", String, index=True), Column("planned_on_air", String))
activities = Table("activities", metadata, Column("activity_id", String, primary_key=True),
                   Column("site_id", String, index=True), Column("activity_type", String), Column("crew_id", String),
                   Column("status", String), Column("planned_end", String), Column("actual_end", String),
                   Column("blocked_reason", String))
materials = Table("materials", metadata, Column("material_id", String, primary_key=True), Column("name", String),
                  Column("category", String))
vendors = Table("vendors", metadata, Column("vendor_id", String, primary_key=True), Column("name", String),
                Column("tier", String))
purchase_orders = Table("purchase_orders", metadata, Column("po_id", String, primary_key=True),
                        Column("activity_id", String, index=True), Column("material_id", String),
                        Column("vendor_id", String, index=True), Column("quantity", Integer),
                        Column("promised_date", String), Column("delivered_date", String), Column("batch_id", String))
crews = Table("crews", metadata, Column("crew_id", String, primary_key=True), Column("region", String),
              Column("team", String), Column("available", Integer), Column("unavailable_reason", String))
permits = Table("permits", metadata, Column("permit_id", String, primary_key=True), Column("site_id", String, index=True),
                Column("authority", String), Column("status", String), Column("applied_on", String),
                Column("expected_on", String))
incidents = Table("incidents", metadata, Column("incident_id", String, primary_key=True),
                  Column("site_id", String, index=True), Column("opened_at", String), Column("resolved_at", String),
                  Column("severity", String), Column("alarm_type", String), Column("description", Text))
region_events = Table("region_events", metadata, Column("event_id", String, primary_key=True),
                      Column("region", String, index=True), Column("event_type", String), Column("started_at", String),
                      Column("ended_at", String), Column("description", Text))
labels = Table("labels", metadata, Column("site_id", String, primary_key=True), Column("root_cause", String),
               Column("key_entity", String))
rca_runs = Table("rca_runs", metadata, Column("run_id", String, primary_key=True), Column("created_at", String),
                 Column("question", Text), Column("intent", String), Column("site_id", String),
                 Column("root_cause", String), Column("confidence", String), Column("llm_provider", String),
                 Column("latency_ms", Integer), Column("answer", Text), Column("trace", Text))
actions = Table("actions", metadata, Column("action_id", String, primary_key=True), Column("run_id", String),
                Column("created_at", String), Column("action_type", String), Column("target", String),
                Column("summary", Text), Column("status", String), Column("decided_by", String),
                Column("decided_at", String))

_TABLES = {"sites": sites, "activities": activities, "materials": materials, "vendors": vendors,
           "purchase_orders": purchase_orders, "crews": crews, "permits": permits, "incidents": incidents,
           "region_events": region_events, "labels": labels}


def make_engine(url: str) -> Engine:
    kwargs = {"connect_args": {"check_same_thread": False}} if url.startswith("sqlite") else {"pool_pre_ping": True}
    return create_engine(url, future=True, **kwargs)


def seed(engine: Engine, world: World) -> None:
    metadata.drop_all(engine, tables=list(_TABLES.values()))
    metadata.create_all(engine)
    with engine.begin() as conn:
        for name, table in _TABLES.items():
            rows = getattr(world, name)
            if rows:
                cols = {c.name for c in table.columns}
                conn.execute(insert(table), [{k: v for k, v in r.items() if k in cols} for r in rows])


def ensure_schema(engine: Engine) -> None:
    metadata.create_all(engine)


def _rows(result) -> list[dict]:
    return [dict(r._mapping) for r in result]


def _days_between(a: str | None, b: str | date) -> int | None:
    if not a:
        return None
    b = b if isinstance(b, date) else date.fromisoformat(b)
    return (b - date.fromisoformat(a[:10])).days


class OpsRepository:
    """Read access used by the agent tools. Every method returns JSON-serialisable dicts."""

    def __init__(self, engine: Engine):
        self.engine = engine

    def _q(self, stmt) -> list[dict]:
        with self.engine.connect() as conn:
            return _rows(conn.execute(stmt))

    def get_site(self, site_id: str) -> dict | None:
        r = self._q(select(sites).where(sites.c.site_id == site_id))
        return r[0] if r else None

    def list_sites(self, status: str | None = None, region: str | None = None, limit: int = 20) -> list[dict]:
        stmt = select(sites)
        if status:
            stmt = stmt.where(sites.c.status == status)
        if region:
            stmt = stmt.where(sites.c.region == region)
        return self._q(stmt.order_by(sites.c.site_id).limit(limit))

    def status_counts(self) -> list[dict]:
        return self._q(select(sites.c.region, sites.c.status, func.count().label("n"))
                       .group_by(sites.c.region, sites.c.status))

    def site_activities(self, site_id: str) -> list[dict]:
        rows = self._q(select(activities).where(activities.c.site_id == site_id).order_by(activities.c.planned_end))
        for r in rows:
            r["days_overdue"] = (max(0, _days_between(r["planned_end"], TODAY))
                                 if r["status"] != "COMPLETED" else 0)
        return rows

    def purchase_orders_for(self, activity_ids: list[str]) -> list[dict]:
        if not activity_ids:
            return []
        stmt = (select(purchase_orders, vendors.c.name.label("vendor_name"), materials.c.name.label("material_name"))
                .join(vendors, vendors.c.vendor_id == purchase_orders.c.vendor_id)
                .join(materials, materials.c.material_id == purchase_orders.c.material_id)
                .where(purchase_orders.c.activity_id.in_(activity_ids)))
        rows = self._q(stmt)
        for r in rows:
            if r["delivered_date"]:
                r["delay_days"] = _days_between(r["promised_date"], r["delivered_date"])
                r["delivery_status"] = "DELIVERED_LATE" if r["delay_days"] > 0 else "DELIVERED"
            else:
                r["delay_days"] = _days_between(r["promised_date"], TODAY)
                r["delivery_status"] = "OVERDUE" if r["delay_days"] > 0 else "IN_TRANSIT"
        return rows

    def vendor_scorecard(self, vendor_id: str | None = None) -> list[dict]:
        stmt = (select(purchase_orders, vendors.c.name.label("vendor_name"))
                .join(vendors, vendors.c.vendor_id == purchase_orders.c.vendor_id))
        if vendor_id:
            stmt = stmt.where(purchase_orders.c.vendor_id == vendor_id)
        agg: dict[str, dict] = {}
        for r in self._q(stmt):
            a = agg.setdefault(r["vendor_id"], {"vendor_id": r["vendor_id"], "vendor_name": r["vendor_name"],
                                                "orders": 0, "late_or_overdue": 0, "open_overdue": 0, "total_delay_days": 0})
            a["orders"] += 1
            delay = (_days_between(r["promised_date"], r["delivered_date"]) if r["delivered_date"]
                     else _days_between(r["promised_date"], TODAY))
            if delay and delay > 0:
                a["late_or_overdue"] += 1
                a["total_delay_days"] += delay
                if not r["delivered_date"]:
                    a["open_overdue"] += 1
        out = []
        for a in agg.values():
            a["on_time_rate"] = round(1 - a["late_or_overdue"] / a["orders"], 3)
            out.append(a)
        return sorted(out, key=lambda x: (x["on_time_rate"], -x["open_overdue"]))

    def permits_for(self, site_id: str) -> list[dict]:
        rows = self._q(select(permits).where(permits.c.site_id == site_id))
        for r in rows:
            r["days_past_expected"] = (max(0, _days_between(r["expected_on"], TODAY)) if r["status"] != "APPROVED" else 0)
        return rows

    def crews_by_id(self, crew_ids: list[str]) -> list[dict]:
        return self._q(select(crews).where(crews.c.crew_id.in_(crew_ids))) if crew_ids else []

    def incidents_for(self, site_id: str, open_only: bool = False) -> list[dict]:
        stmt = select(incidents).where(incidents.c.site_id == site_id)
        if open_only:
            stmt = stmt.where(incidents.c.resolved_at.is_(None))
        return self._q(stmt.order_by(incidents.c.opened_at.desc()))

    def region_events_for(self, region: str) -> list[dict]:
        return self._q(select(region_events).where(region_events.c.region == region))

    def batch_failures(self, batch_id: str) -> list[dict]:
        """Sites with open HW_FAULT incidents whose hardware came from the given batch."""
        stmt = (select(activities.c.site_id, purchase_orders.c.vendor_id, purchase_orders.c.po_id)
                .join(activities, activities.c.activity_id == purchase_orders.c.activity_id)
                .where(purchase_orders.c.batch_id == batch_id))
        site_ids = {r["site_id"]: r for r in self._q(stmt)}
        faulty = self._q(select(incidents.c.site_id).where(incidents.c.alarm_type == "HW_FAULT")
                         .where(incidents.c.resolved_at.is_(None)))
        return [site_ids[f["site_id"]] for f in faulty if f["site_id"] in site_ids]

    def labels(self) -> list[dict]:
        return self._q(select(labels))

    # ----------------------------------------------------------------- runs and actions (write side)
    def save_run(self, run: dict) -> None:
        with self.engine.begin() as conn:
            conn.execute(insert(rca_runs), [{**run, "trace": json.dumps(run["trace"], default=str)}])

    def get_run(self, run_id: str) -> dict | None:
        r = self._q(select(rca_runs).where(rca_runs.c.run_id == run_id))
        if not r:
            return None
        r[0]["trace"] = json.loads(r[0]["trace"])
        return r[0]

    def recent_runs(self, limit: int = 20) -> list[dict]:
        return self._q(select(rca_runs.c.run_id, rca_runs.c.created_at, rca_runs.c.question, rca_runs.c.root_cause,
                              rca_runs.c.confidence, rca_runs.c.latency_ms)
                       .order_by(rca_runs.c.created_at.desc()).limit(limit))

    def create_action(self, action: dict) -> None:
        with self.engine.begin() as conn:
            conn.execute(insert(actions), [action])

    def list_actions(self, status: str | None = None) -> list[dict]:
        stmt = select(actions).order_by(actions.c.created_at.desc())
        if status:
            stmt = stmt.where(actions.c.status == status)
        return self._q(stmt)

    def decide_action(self, action_id: str, approve: bool, user: str) -> dict | None:
        with self.engine.begin() as conn:
            res = conn.execute(update(actions).where(actions.c.action_id == action_id)
                               .where(actions.c.status == "PENDING_APPROVAL")
                               .values(status="APPROVED" if approve else "REJECTED", decided_by=user,
                                       decided_at=datetime.utcnow().isoformat(timespec="seconds")))
            if res.rowcount == 0:
                return None
        return self._q(select(actions).where(actions.c.action_id == action_id))[0]


def as_dict(v: Any) -> Any:  # small helper for callers that need plain JSON
    return json.loads(json.dumps(v, default=str))
