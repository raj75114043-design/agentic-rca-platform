"""Offline evaluation harness with quality gates.

Builds a labelled question set from the injected ground truth (faulty sites) plus negative cases
(healthy sites, where the correct answer is "no root cause"), runs the full agent workflow and reports:

  root_cause_accuracy   predicted cause == labelled cause (faulty sites)
  key_entity_accuracy   predicted vendor/permit/crew/region/activity == labelled entity
  false_alarm_rate      healthy sites wrongly given a MEDIUM/HIGH root cause
  citation_validity     share of answers whose citations all exist in collected evidence
  avg_iterations, latency p50/p95, LLM calls and tokens

Usage:  python -m rca.eval.run [--limit N] [--gate]
With --gate the process exits non-zero if any metric breaches its threshold (used in CI).
"""
from __future__ import annotations

import argparse
import json
import random
import re
import statistics
import sys
from pathlib import Path

from rca.agents.graph import run_rca
from rca.container import build

THRESHOLDS = {"root_cause_accuracy": 0.90, "key_entity_accuracy": 0.85, "false_alarm_rate": 0.10,
              "citation_validity": 0.98}
PHRASINGS = ["Why is {s} {state}?", "What is the root cause for {s}?", "{s} is {state} — what happened?",
             "Can you investigate {s}? It is {state}.", "RCA for {s} please"]


def build_cases(c, limit: int | None, seed: int = 11) -> list[dict]:
    rng = random.Random(seed)
    labels = c.repo.labels()
    cases = []
    for l in labels:
        site = c.repo.get_site(l["site_id"])
        state = "delayed" if site["status"] == "DELAYED" else "down"
        cases.append({"question": rng.choice(PHRASINGS).format(s=l["site_id"], state=state),
                      "site_id": l["site_id"], "expected": l["root_cause"], "key_entity": l["key_entity"]})
    healthy = [s for s in c.repo.list_sites(status="ON_AIR", limit=500)]
    for s in rng.sample(healthy, min(20, len(healthy))):
        cases.append({"question": f"Is anything wrong with {s['site_id']}? Why is it down?", "site_id": s["site_id"],
                      "expected": None, "key_entity": None})
    rng.shuffle(cases)
    return cases[:limit] if limit else cases


def evaluate(limit: int | None = None) -> dict:
    c = build()
    rows = []
    for case in build_cases(c, limit):
        r = run_rca(c, case["question"], persist=False)
        evidence_ids = {e["id"] for e in r["evidence"]}
        cited = set(re.findall(r"\[([a-z_]+:[^\]\s]+)\]", r["answer"]))
        rows.append({**case, "predicted": r["root_cause"], "pred_entity": r["key_entity"],
                     "confidence": r["confidence"], "iterations": r["iterations"], "latency_ms": r["latency_ms"],
                     "citations_valid": cited <= evidence_ids, "llm_calls": r["llm"]["calls"],
                     "tokens": r["llm"]["prompt_tokens"] + r["llm"]["completion_tokens"]})
    faulty = [x for x in rows if x["expected"]]
    healthy = [x for x in rows if not x["expected"]]
    lat = sorted(x["latency_ms"] for x in rows)
    metrics = {
        "cases": len(rows), "faulty_cases": len(faulty), "healthy_cases": len(healthy),
        "root_cause_accuracy": round(sum(x["predicted"] == x["expected"] for x in faulty) / max(1, len(faulty)), 3),
        "key_entity_accuracy": round(sum(x["pred_entity"] == x["key_entity"] for x in faulty) / max(1, len(faulty)), 3),
        "false_alarm_rate": round(sum(x["predicted"] is not None for x in healthy) / max(1, len(healthy)), 3),
        "citation_validity": round(sum(x["citations_valid"] for x in rows) / max(1, len(rows)), 3),
        "avg_iterations": round(statistics.mean(x["iterations"] for x in rows), 2),
        "latency_p50_ms": lat[len(lat) // 2], "latency_p95_ms": lat[int(len(lat) * 0.95) - 1],
        "llm_provider": c.llm.provider, "llm_calls": sum(x["llm_calls"] for x in rows),
        "llm_tokens": sum(x["tokens"] for x in rows),
    }
    per_cause = {}
    for x in faulty:
        d = per_cause.setdefault(x["expected"], {"n": 0, "correct": 0})
        d["n"] += 1
        d["correct"] += x["predicted"] == x["expected"]
    metrics["per_cause"] = {k: f"{v['correct']}/{v['n']}" for k, v in sorted(per_cause.items())}
    failures = [x for x in rows if x["predicted"] != x["expected"]][:20]
    return {"metrics": metrics, "failures": failures}


def gate(metrics: dict) -> list[str]:
    breaches = []
    for k, t in THRESHOLDS.items():
        v = metrics[k]
        if (k == "false_alarm_rate" and v > t) or (k != "false_alarm_rate" and v < t):
            breaches.append(f"{k}={v} (threshold {t})")
    return breaches


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--gate", action="store_true")
    a = ap.parse_args()
    report = evaluate(a.limit)
    Path("reports").mkdir(exist_ok=True)
    Path("reports/eval.json").write_text(json.dumps(report, indent=2, default=str))
    print(json.dumps(report["metrics"], indent=2))
    if a.gate:
        breaches = gate(report["metrics"])
        if breaches:
            print("QUALITY GATE FAILED:", "; ".join(breaches))
            sys.exit(1)
        print("QUALITY GATE PASSED")


if __name__ == "__main__":
    main()
