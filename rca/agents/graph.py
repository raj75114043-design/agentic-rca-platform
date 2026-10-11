"""LangGraph multi-agent workflow for network root-cause analysis.

    Router -> Planner -> Executor -> Analyst -> Verifier --(needs more evidence)--> Planner
                                                      \\--(sufficient or budget spent)--> Responder -> Actions

* Router    extracts intent and entities (LLM with rule-based fallback).
* Planner   chooses tool calls from the validated catalog (LLM, constrained to the catalog; falls back to
            the Analyst's suggested next checks).
* Executor  runs tool calls in parallel, deduplicates repeats and records evidence.
* Analyst   ranks root-cause hypotheses from evidence and proposes the next checks.
* Verifier  stops when the top hypothesis is confirmed, or when the iteration budget is spent.
* Responder writes a cited report; citations are validated against the evidence and the report falls back
            to a deterministic template if the LLM cites anything that was not collected.
* Actions   proposes human-in-the-loop actions (for example vendor escalation) that need approval.
"""
from __future__ import annotations

import json
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from rca.agents import prompts
from rca.analysis import rank
from rca.container import Container
from rca.llm import LLMUsage

REGIONS = ["North", "South", "East", "West", "Central"]
RUNBOOK_QUERY = {"VENDOR_DELAY": "material delivery delay purchase order overdue vendor",
                 "PERMIT_PENDING": "permit pending municipal authority civil works",
                 "CREW_SHORTAGE": "crew not mobilised reassign activity",
                 "POWER_OUTAGE": "mains power failure grid outage battery generator",
                 "FIBER_CUT": "backhaul link down fibre break OTDR",
                 "HW_FAULT": "radio hardware fault batch defect replace unit"}


def _citations(text: str) -> set[str]:
    """Evidence IDs cited in square brackets; tolerates several IDs in one bracket and backticks."""
    ids = set()
    for group in re.findall(r"\[([^\[\]]+)\]", text):
        for part in re.split(r"[,;]\s*|\s+", group.replace("`", "").strip()):
            if re.fullmatch(r"[a-z_]+:\S+", part):
                ids.add(part)
    return ids


class RCAState(TypedDict, total=False):
    run_id: str
    question: str
    intent: str
    site_id: str | None
    vendor_id: str | None
    region: str | None
    plan: list[dict]
    calls_made: list[str]
    results: dict[str, Any]
    evidence: dict[str, dict]
    tool_log: list[dict]
    hypotheses: list[dict]
    next_checks: list[dict]
    iterations: int
    done: bool
    answer: str
    answer_mode: str
    actions: list[dict]
    trace: list[dict]


def _sig(step: dict) -> str:
    return step["tool"] + json.dumps(step.get("args", {}), sort_keys=True)


class RCAWorkflow:
    def __init__(self, c: Container):
        self.c = c
        self.usage = LLMUsage()
        g = StateGraph(RCAState)
        for name in ("router", "planner", "executor", "analyst", "verifier", "responder", "actions"):
            g.add_node(name, self._traced(name, getattr(self, name)))
        g.set_entry_point("router")
        g.add_conditional_edges("router", lambda s: "planner" if s["intent"] != "out_of_scope" else "responder",
                                {"planner": "planner", "responder": "responder"})
        g.add_edge("planner", "executor")
        g.add_edge("executor", "analyst")
        g.add_edge("analyst", "verifier")
        g.add_conditional_edges("verifier", lambda s: "responder" if s["done"] else "planner",
                                {"planner": "planner", "responder": "responder"})
        g.add_edge("responder", "actions")
        g.add_edge("actions", END)
        self.graph = g.compile()

    # ------------------------------------------------------------------------------------ tracing
    def _traced(self, name, fn):
        def wrapper(state: RCAState) -> dict:
            t0 = time.perf_counter()
            calls0 = self.usage.calls
            update = fn(state)
            span = {"node": name, "ms": round((time.perf_counter() - t0) * 1000, 1),
                    "llm_calls": self.usage.calls - calls0, "summary": update.pop("_summary", "")}
            update["trace"] = state.get("trace", []) + [span]
            return update
        return wrapper

    # ------------------------------------------------------------------------------------- nodes
    def router(self, s: RCAState) -> dict:
        q = s["question"]
        site = re.search(r"SITE-?\s?(\d{1,4})", q, re.I)
        site_id = f"SITE-{int(site.group(1)):04d}" if site else None
        vendor = re.search(r"VEN-?(\d{1,3})", q, re.I)
        vendor_id = f"VEN-{int(vendor.group(1)):03d}" if vendor else None
        region = next((r for r in REGIONS if re.search(rf"\b{r}\b", q, re.I)), None)
        ql = q.lower()
        if site_id and re.search(r"\b(why|cause|reason|down|delay|delayed|late|not on.?air|outage|degraded|stuck|rca)\b", ql):
            intent = "site_rca"
        elif site_id:
            intent = "site_status"
        elif vendor_id or "vendor" in ql or "supplier" in ql:
            intent = "vendor_performance"
        elif region or re.search(r"\b(sites?|network|region)\b", ql):
            intent = "region_overview"
        else:
            intent = "out_of_scope"
        mode = "rules"
        if self.c.llm.available:
            out = self.c.llm.chat(prompts.ROUTER_SYSTEM, q, json_mode=True, usage=self.usage, max_tokens=120)
            if isinstance(out, dict) and out.get("intent") in ("site_rca", "site_status", "vendor_performance",
                                                                 "region_overview", "out_of_scope"):
                # Entities extracted by regex are authoritative; the LLM only refines intent
                intent, mode = out["intent"], "llm"
                if intent in ("site_rca", "site_status") and not site_id:
                    intent = "region_overview"
                region = region or (out.get("region") if out.get("region") in REGIONS else None)
        return {"intent": intent, "site_id": site_id, "vendor_id": vendor_id, "region": region, "iterations": 0,
                "calls_made": [], "results": {}, "evidence": {}, "tool_log": [], "next_checks": [], "actions": [],
                "_summary": f"intent={intent} site={site_id} vendor={vendor_id} region={region} ({mode})"}

    def _initial_plan(self, s: RCAState) -> list[dict]:
        sid = s.get("site_id")
        if s["intent"] == "site_rca":
            return [{"tool": "get_site", "args": {"site_id": sid}, "why": "Current status and region"},
                    {"tool": "get_site_activities", "args": {"site_id": sid}, "why": "Find blocked or overdue work"},
                    {"tool": "get_incidents", "args": {"site_id": sid}, "why": "Find open network alarms"},
                    {"tool": "get_permits", "args": {"site_id": sid}, "why": "Check site access permit"},
                    {"tool": "graph_dependencies", "args": {"site_id": sid, "max_hops": 3}, "why": "Map dependencies"}]
        if s["intent"] == "site_status":
            return [{"tool": "get_site", "args": {"site_id": sid}, "why": "Current status"},
                    {"tool": "get_incidents", "args": {"site_id": sid}, "why": "Open alarms"}]
        if s["intent"] == "vendor_performance":
            return [{"tool": "get_vendor_scorecard", "args": {"vendor_id": s.get("vendor_id")}, "why": "Delivery performance"}]
        steps = [{"tool": "list_sites", "args": {"status": st, "region": s.get("region"), "limit": 100},
                  "why": f"Sites {st}"} for st in ("DOWN", "DEGRADED", "DELAYED")]
        if s.get("region"):
            steps.append({"tool": "get_region_events", "args": {"region": s["region"]}, "why": "Regional events"})
        return steps

    def planner(self, s: RCAState) -> dict:
        made = set(s.get("calls_made", []))
        fallback = self._initial_plan(s) if s.get("iterations", 0) == 0 else s.get("next_checks", [])
        steps, mode = fallback, "rules"
        if self.c.llm.available and s["intent"] == "site_rca" and s.get("iterations", 0) > 0:
            ctx = {"question": s["question"], "site_id": s.get("site_id"),
                   "evidence_so_far": [e["summary"] for e in s.get("evidence", {}).values()][:15],
                   "suggested_next_checks": s.get("next_checks", []), "already_called": sorted(made),
                   "tool_catalog": [{"name": t["name"], "description": t["description"]} for t in self.c.tools.catalog()]}
            out = self.c.llm.chat(prompts.PLANNER_SYSTEM, json.dumps(ctx), json_mode=True, usage=self.usage, max_tokens=400)
            if isinstance(out, dict) and isinstance(out.get("steps"), list):
                valid = [st for st in out["steps"] if isinstance(st, dict) and st.get("tool") in self.c.tools.tools]
                if valid:
                    steps, mode = valid[:5], "llm"
        steps = [st for st in steps if _sig(st) not in made]
        return {"plan": steps, "_summary": f"{len(steps)} steps ({mode}): " + ", ".join(st["tool"] for st in steps)}

    def executor(self, s: RCAState) -> dict:
        plan = s.get("plan", [])
        with ThreadPoolExecutor(max_workers=6) as pool:
            outs = list(pool.map(lambda st: self.c.tools.call(st["tool"], st.get("args", {})), plan))
        results, evidence = dict(s.get("results", {})), dict(s.get("evidence", {}))
        log = list(s.get("tool_log", []))
        for st, out in zip(plan, outs, strict=True):
            log.append({k: out.get(k) for k in ("tool", "args", "ok", "error", "ms")} | {"why": st.get("why", "")})
            if out["ok"]:
                if st["tool"] == "list_sites" and "list_sites" in results:
                    results["list_sites"] = results["list_sites"] + out["data"]
                else:
                    results[st["tool"]] = out["data"]
                for e in out["evidence"]:
                    evidence[e["id"]] = e
        failed = sum(1 for o in outs if not o["ok"])
        return {"results": results, "evidence": evidence, "tool_log": log,
                "calls_made": s.get("calls_made", []) + [_sig(st) for st in plan],
                "iterations": s.get("iterations", 0) + 1,
                "_summary": f"{len(plan)} calls, {failed} failed, {len(evidence)} evidence items"}

    def analyst(self, s: RCAState) -> dict:
        if s["intent"] != "site_rca":
            return {"hypotheses": [], "next_checks": [], "_summary": "not an RCA question"}
        hyps = rank(s["results"].get("get_site"), s["results"], s["evidence"])
        made = set(s.get("calls_made", []))
        nxt = [c for h in hyps[:2] for c in h.next_checks if _sig(c) not in made]
        top = hyps[0] if hyps else None
        return {"hypotheses": [h.as_dict() for h in hyps], "next_checks": nxt,
                "_summary": (f"top={top.cause} ({top.score:.2f})" if top else "no hypothesis") + f", {len(nxt)} next checks"}

    def verifier(self, s: RCAState) -> dict:
        if s["intent"] != "site_rca":
            return {"done": True, "_summary": "single pass"}
        top = (s.get("hypotheses") or [None])[0]
        budget_left = s.get("iterations", 0) < self.c.settings.max_iterations
        if top and top["score"] >= 0.85 and not top["next_checks"]:
            return {"done": True, "_summary": f"confirmed {top['cause']}"}
        if s.get("next_checks") and budget_left:
            return {"done": False, "_summary": f"need more evidence: {[c['tool'] for c in s['next_checks']]}"}
        return {"done": True, "_summary": "stopping: " + ("budget spent" if not budget_left else "no further checks")}

    def responder(self, s: RCAState) -> dict:
        if s["intent"] == "out_of_scope":
            return {"answer": "I can investigate network sites, vendors and regions. Ask, for example, "
                              "\"Why is SITE-0007 delayed?\" or \"Which vendors are delaying deliveries?\"",
                    "answer_mode": "template", "_summary": "out of scope"}
        if s["intent"] != "site_rca":
            return {"answer": self._summary_answer(s), "answer_mode": "template", "_summary": "summary answer"}
        top = (s.get("hypotheses") or [None])[0]
        evidence = dict(s["evidence"])
        if top:  # attach the matching runbook's Resolution section as evidence for recommended actions
            rb = self.c.tools.call("search_runbooks", {"query": RUNBOOK_QUERY[top["cause"]] + " resolution"})
            hits = rb["data"] or []
            best = next((h for h in hits if h["section"] == "Resolution"), hits[0] if hits else None)
            if best:
                eid = f"runbook:{best['runbook_id']}#{best['section']}"
                evidence[eid] = {"id": eid, "kind": "runbook", "summary": f"{best['runbook']} — {best['section']}",
                                 "data": best}
        template = self._template_answer(s, top, evidence)
        if not self.c.llm.available or not top:
            return {"answer": template, "answer_mode": "template", "evidence": evidence, "_summary": "template report"}
        ctx = {"question": s["question"], "top_hypothesis": top, "alternatives": s["hypotheses"][1:3],
               "evidence": [{"id": i, "summary": e["summary"]} for i, e in evidence.items()]}
        text = self.c.llm.chat(prompts.RESPONDER_SYSTEM, json.dumps(ctx), usage=self.usage)
        for attempt in range(2):
            if not isinstance(text, str):
                break
            cited = _citations(text)
            bad = sorted(cited - set(evidence))
            if cited and not bad:
                return {"answer": text, "answer_mode": "llm", "evidence": evidence,
                        "_summary": f"LLM report, {len(cited)} citations verified" + (" after correction" if attempt else "")}
            if attempt == 0:  # one corrective retry with explicit feedback
                fix = {**ctx, "previous_report": text,
                       "problem": (f"These citations are not evidence IDs: {bad}" if bad else "The report has no citations")
                                  + ". Rewrite citing only IDs from the evidence list, one ID per bracket."}
                text = self.c.llm.chat(prompts.RESPONDER_SYSTEM, json.dumps(fix), usage=self.usage)
        if isinstance(text, str):
            return {"answer": template, "answer_mode": "template", "evidence": evidence,
                    "_summary": "LLM report rejected (unverifiable citations); template used"}
        return {"answer": template, "answer_mode": "template", "evidence": evidence, "_summary": "LLM unavailable"}

    def actions(self, s: RCAState) -> dict:
        top = (s.get("hypotheses") or [None])[0]
        if not top or top["confidence"] != "HIGH":
            return {"actions": [], "_summary": "no action proposed"}
        act = None
        if top["cause"] == "VENDOR_DELAY":
            po = next((e["data"] for e in s["evidence"].values() if e["kind"] == "po"
                       and e["data"]["delivery_status"] == "OVERDUE"), None)
            if po and po["delay_days"] >= self.c.settings.escalation_delay_days:
                act = ("ESCALATE_VENDOR", po["vendor_id"],
                       f"Escalate {po['po_id']} ({po['material_name']}) to {po['vendor_name']}: {po['delay_days']} days overdue, "
                       f"blocking {s['site_id']}")
        elif top["cause"] == "HW_FAULT" and "batch" in " ".join(top["supporting"]):
            act = ("QUARANTINE_BATCH", top["key_entity"], f"Quarantine failing hardware batch and raise vendor quality incident: {top['title']}")
        elif top["cause"] == "PERMIT_PENDING":
            act = ("ESCALATE_PERMIT", top["key_entity"], f"Escalate permit {top['key_entity']} via regional liaison for {s['site_id']}")
        elif top["cause"] == "POWER_OUTAGE":
            act = ("DISPATCH_GENERATOR", s["site_id"], f"Dispatch portable generator to {s['site_id']} during regional grid outage")
        if not act:
            return {"actions": [], "_summary": "no action proposed"}
        action = {"action_id": f"ACT-{uuid.uuid4().hex[:8].upper()}", "run_id": s["run_id"],
                  "created_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds"), "action_type": act[0],
                  "target": act[1], "summary": act[2], "status": "PENDING_APPROVAL", "decided_by": None, "decided_at": None}
        self.c.repo.create_action(action)
        return {"actions": [action], "_summary": f"{act[0]} awaiting human approval"}

    # --------------------------------------------------------------------------------- templates
    @staticmethod
    def _template_answer(s: RCAState, top: dict | None, evidence: dict) -> str:
        site = s["results"].get("get_site")
        if not site:
            return f"I could not find {s.get('site_id')} in the network inventory."
        if not top:
            return (f"**{site['site_id']}** is **{site['status']}**. No blocking activity, open incident or pending permit "
                    f"was found, so there is no root cause to report [site:{site['site_id']}].")
        lines = [f"**Root cause** ({top['confidence']} confidence): {top['title']}.",
                 "", "**Evidence**"]
        for eid in [f"site:{site['site_id']}"] + top["supporting"]:
            if eid in evidence:
                lines.append(f"- {evidence[eid]['summary']} [{eid}]")
        lines += ["", "**Impact**", f"- {site['site_id']} ({site['city']}, {site['region']}) is {site['status']}; "
                                      f"planned on-air {site['planned_on_air']} [site:{site['site_id']}]."]
        rbs = sorted((e for e in evidence.values() if e["kind"] == "runbook"),
                     key=lambda e: e["data"].get("section") != "Resolution")
        if rbs:
            steps = [l.strip("- ").strip() for l in rbs[0]["data"]["text"].splitlines() if l.strip().startswith("-")]
            lines += ["", "**Recommended actions**"] + [f"- {x} [{rbs[0]['id']}]" for x in steps[:3]]
        alts = [h for h in s["hypotheses"][1:3] if h["score"] >= 0.3]
        if alts:
            lines += ["", "_Ruled lower:_ " + "; ".join(f"{h['cause']} ({h['score']:.2f})" for h in alts)]
        return "\n".join(lines)

    @staticmethod
    def _summary_answer(s: RCAState) -> str:
        r = s["results"]
        if s["intent"] == "site_status":
            site = r.get("get_site")
            if not site:
                return f"{s.get('site_id')} was not found in the network inventory."
            inc = [i for i in (r.get("get_incidents") or []) if not i["resolved_at"]]
            return (f"**{site['site_id']}** ({site['city']}, {site['region']}) is **{site['status']}**, planned on-air "
                    f"{site['planned_on_air']}. Open incidents: {len(inc)} [site:{site['site_id']}].")
        if s["intent"] == "vendor_performance":
            sc = r.get("get_vendor_scorecard") or []
            rows = "\n".join(f"| {v['vendor_name']} ({v['vendor_id']}) | {v['on_time_rate']:.0%} | {v['open_overdue']} | "
                             f"{v['total_delay_days']} |" for v in sc[:8])
            return "**Vendor delivery performance (worst first)**\n\n| Vendor | On-time | Open overdue | Delay days |\n" \
                   "|---|---|---|---|\n" + rows
        sites = r.get("list_sites") or []
        by = {}
        for x in sites:
            by.setdefault(x["status"], []).append(x["site_id"])
        scope = s.get("region") or "all regions"
        out = [f"**Network overview — {scope}**"] + [f"- {k}: {len(v)} sites (e.g. {', '.join(v[:5])})" for k, v in by.items()]
        ev = [e for e in s["evidence"].values() if e["kind"] == "event"]
        out += [f"- Regional event: {e['summary']} [{e['id']}]" for e in ev]
        return "\n".join(out)


def run_rca(c: Container, question: str, persist: bool = True) -> dict:
    wf = RCAWorkflow(c)
    run_id = f"RUN-{uuid.uuid4().hex[:10].upper()}"
    t0 = time.perf_counter()
    state = wf.graph.invoke({"run_id": run_id, "question": question, "trace": []},
                            config={"recursion_limit": 30})
    latency = round((time.perf_counter() - t0) * 1000)
    top = (state.get("hypotheses") or [None])[0]
    if top and top["confidence"] == "LOW":  # weak signals are shown as hypotheses but not reported as the root cause
        top = None
    result = {"run_id": run_id, "question": question, "intent": state["intent"], "site_id": state.get("site_id"),
              "answer": state["answer"], "answer_mode": state.get("answer_mode"), "hypotheses": state.get("hypotheses", []),
              "root_cause": top["cause"] if top else None, "key_entity": top["key_entity"] if top else None,
              "confidence": top["confidence"] if top else None, "evidence": list(state.get("evidence", {}).values()),
              "tool_log": state.get("tool_log", []), "trace": state.get("trace", []), "actions": state.get("actions", []),
              "iterations": state.get("iterations", 0), "latency_ms": latency,
              "llm": {"provider": c.llm.provider, "model": c.llm.model, "calls": wf.usage.calls,
                      "failures": wf.usage.failures, "prompt_tokens": wf.usage.prompt_tokens,
                      "completion_tokens": wf.usage.completion_tokens,
                      "errors": sorted({f"{e['error']}({e.get('status')})" for e in wf.usage.events if not e["ok"]})}}
    if persist:
        c.repo.save_run({"run_id": run_id, "created_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds"),
                         "question": question, "intent": result["intent"], "site_id": result["site_id"],
                         "root_cause": result["root_cause"], "confidence": result["confidence"],
                         "llm_provider": c.llm.provider, "latency_ms": latency, "answer": result["answer"],
                         "trace": {k: result[k] for k in ("trace", "tool_log", "hypotheses", "llm")}})
    return result
