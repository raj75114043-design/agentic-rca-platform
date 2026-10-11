"""Command-line entry points: `python -m rca.cli seed | ask "question" | serve-mcp`."""
from __future__ import annotations

import argparse
import json

from rca.container import build


def main() -> None:
    ap = argparse.ArgumentParser(prog="rca")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("seed", help="(Re)create the operational database and knowledge graph")
    ask = sub.add_parser("ask", help="Run one investigation and print the report")
    ask.add_argument("question")
    ask.add_argument("--json", action="store_true")
    a = ap.parse_args()
    if a.cmd == "seed":
        c = build(reseed=True)
        print(json.dumps({"seeded": True, "graph": c.graph.stats()}, indent=2))
    elif a.cmd == "ask":
        from rca.agents.graph import run_rca
        r = run_rca(build(), a.question)
        print(json.dumps(r, indent=2, default=str) if a.json else r["answer"])


if __name__ == "__main__":
    main()
