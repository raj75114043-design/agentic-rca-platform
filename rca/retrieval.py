"""Section-level BM25 retrieval over operational runbooks."""
from __future__ import annotations

import re
from pathlib import Path

from rank_bm25 import BM25Okapi

RUNBOOK_DIR = Path(__file__).resolve().parent / "data" / "runbooks"
_STOP = set("a an the is are to of in on for and or with by from at as be it this that its".split())


def _tok(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9_]+", text.lower()) if t not in _STOP]


class RunbookIndex:
    def __init__(self, directory: Path = RUNBOOK_DIR):
        self.chunks: list[dict] = []
        for path in sorted(directory.glob("*.md")):
            text = path.read_text(encoding="utf-8")
            title = text.splitlines()[0].lstrip("# ").strip()
            for sec in re.split(r"\n(?=## )", text)[1:]:
                head, _, body = sec.partition("\n")
                self.chunks.append({"runbook": title, "runbook_id": title.split()[0], "section": head.strip("# ").strip(),
                                    "text": body.strip()})
        self.bm25 = BM25Okapi([_tok(f"{c['runbook']} {c['section']} {c['text']}") for c in self.chunks])

    def search(self, query: str, k: int = 3) -> list[dict]:
        scores = self.bm25.get_scores(_tok(query))
        order = sorted(range(len(scores)), key=lambda i: -scores[i])[:k]
        return [{**self.chunks[i], "score": round(float(scores[i]), 2)} for i in order if scores[i] > 0]
