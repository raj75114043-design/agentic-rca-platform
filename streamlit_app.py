"""Operations console for the Agentic RCA Platform (Streamlit)."""
from __future__ import annotations

import os
from pathlib import Path

os.chdir(Path(__file__).resolve().parent)

import streamlit as st  # noqa: E402

# Streamlit Cloud secrets -> environment (e.g. GROQ_API_KEY for the free Llama tier)
try:
    for key in ("GROQ_API_KEY", "LLM_PROVIDER", "LLM_MODEL", "LLM_BASE_URL"):
        if key in st.secrets:
            os.environ[key] = str(st.secrets[key])
except Exception:  # no secrets file locally
    pass

import pandas as pd  # noqa: E402

from rca.agents.graph import run_rca  # noqa: E402
from rca.container import get_container  # noqa: E402

st.set_page_config(page_title="Agentic RCA Platform", page_icon="🛰️", layout="wide")
c = get_container()

COLORS = {"Site": "#FF3B3B", "Activity": "#E76F51", "PurchaseOrder": "#F4A261", "Vendor": "#264653", "Crew": "#2A9D8F",
          "Permit": "#8AB17D", "Region": "#6D597A", "Incident": "#B5179E", "Material": "#999999"}

with st.sidebar:
    st.markdown("### 🛰️ Agentic RCA Platform")
    st.caption("Multi-agent root-cause analysis for 5G rollout and network operations")
    llm_label = f"{c.llm.provider} · {c.llm.model}" if c.llm.available else "deterministic (no LLM key)"
    st.markdown(f"**LLM:** `{llm_label}`")
    st.markdown(f"**Graph:** `{c.graph.stats()['backend']}` · **DB:** `{c.settings.database_url.split(':')[0]}`")
    st.divider()
    st.markdown("**Try asking**")
    examples = {}
    labels = {l["root_cause"]: l["site_id"] for l in c.repo.labels()}
    for cause, verb in [("VENDOR_DELAY", "delayed"), ("HW_FAULT", "down"), ("POWER_OUTAGE", "down"),
                        ("PERMIT_PENDING", "delayed"), ("CREW_SHORTAGE", "delayed")]:
        if cause in labels:
            examples[f"Why is {labels[cause]} {verb}?"] = cause
    for q in list(examples) + ["Which vendors are delaying deliveries?", "Show sites down in the West region"]:
        if st.button(q, width="stretch"):
            st.session_state["q"] = q
    st.divider()
    st.caption("Synthetic data. Set `GROQ_API_KEY` to enable the free Llama 3.3 model for planning and reports.")

tab_inv, tab_ops, tab_appr, tab_eval, tab_arch = st.tabs(
    ["🔎 Investigate", "📊 Operations", "✅ Approvals", "🧪 Evaluation", "🏗️ Architecture"])

with tab_inv:
    q = st.text_input("Ask about a site, vendor or region", key="q", placeholder="Why is SITE-0228 delayed?")
    if q:
        with st.spinner("Agents investigating…"):
            r = run_rca(c, q)
        top = (r["hypotheses"] or [None])[0]
        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("Root cause", r["root_cause"] or "—")
        m2.metric("Confidence", r["confidence"] or "—")
        m3.metric("Agent iterations", r["iterations"])
        m4.metric("Tool calls", len(r["tool_log"]))
        m5.metric("Latency", f"{r['latency_ms']} ms")
        left, right = st.columns([3, 2])
        with left:
            st.markdown(r["answer"])
            st.caption(f"Run `{r['run_id']}` · report: {r['answer_mode']} · LLM calls: {r['llm']['calls']}")
            for a in r["actions"]:
                st.warning(f"**Proposed action — {a['action_type']}** (needs human approval): {a['summary']}  \n"
                           f"Approve it in the Approvals tab · `{a['action_id']}`")
        with right:
            st.markdown("**Agent trace**")
            st.dataframe(pd.DataFrame(r["trace"])[["node", "ms", "llm_calls", "summary"]], hide_index=True,
                         width="stretch", height=300)
        if r["hypotheses"]:
            st.markdown("**Ranked hypotheses**")
            st.dataframe(pd.DataFrame(r["hypotheses"])[["cause", "score", "confidence", "key_entity", "title"]],
                         hide_index=True, width="stretch")
        with st.expander("Tool calls (validated, audited)"):
            st.dataframe(pd.DataFrame(r["tool_log"]), hide_index=True, width="stretch")
        with st.expander("Evidence"):
            st.dataframe(pd.DataFrame([{"id": e["id"], "summary": e["summary"]} for e in r["evidence"]]),
                         hide_index=True, width="stretch")
        if r["site_id"] and r["intent"] == "site_rca":
            ev_ids = {e["id"].split(":", 1)[1] for e in r["evidence"]}
            paths = c.graph.dependencies(r["site_id"], 3)
            keep = [p for p in paths if any(h["id"] in ev_ids for h in p["hops"][1:])] or paths[:8]
            dot = ['digraph G { rankdir=LR; node [shape=box, style="rounded,filled", fontname=Helvetica, fontsize=10];']
            seen = set()
            for p in keep[:20]:
                for i, h in enumerate(p["hops"]):
                    key = f"{h['label']}:{h['id']}"
                    if key not in seen:
                        seen.add(key)
                        hot = h["id"] in ev_ids
                        dot.append(f'"{key}" [label="{h["label"]}\\n{h["id"]}", fillcolor="{COLORS.get(h["label"], "#ccc")}", '
                                   f'fontcolor=white, penwidth={3 if hot else 1}, color="{"#000" if hot else "#fff"}"];')
                    if i:
                        prev = p["hops"][i - 1]
                        dot.append(f'"{prev["label"]}:{prev["id"]}" -> "{key}" [label="{h["rel"]}", fontsize=8];')
            dot.append("}")
            st.markdown("**Dependency graph (evidence nodes outlined)**")
            st.graphviz_chart("\n".join(dict.fromkeys(dot)))

with tab_ops:
    counts = pd.DataFrame(c.repo.status_counts())
    pivot = counts.pivot_table(index="region", columns="status", values="n", fill_value=0)
    a, b, d, e = st.columns(4)
    tot = counts.groupby("status")["n"].sum()
    a.metric("On air", int(tot.get("ON_AIR", 0)))
    b.metric("Down", int(tot.get("DOWN", 0)))
    d.metric("Degraded", int(tot.get("DEGRADED", 0)))
    e.metric("Delayed", int(tot.get("DELAYED", 0)))
    st.markdown("**Site status by region**")
    st.bar_chart(pivot)
    st.markdown("**Vendor delivery performance (worst first)**")
    st.dataframe(pd.DataFrame(c.repo.vendor_scorecard())[["vendor_name", "vendor_id", "orders", "on_time_rate",
                                                             "open_overdue", "total_delay_days"]],
                 hide_index=True, width="stretch")
    st.markdown("**Recent investigations**")
    st.dataframe(pd.DataFrame(c.repo.recent_runs(15)), hide_index=True, width="stretch")

with tab_appr:
    pend = c.repo.list_actions("PENDING_APPROVAL")
    if not pend:
        st.info("No actions awaiting approval. Run an investigation that proposes an action first.")
    for a in pend:
        with st.container(border=True):
            st.markdown(f"**{a['action_type']}** → `{a['target']}`  \n{a['summary']}")
            x, y, _ = st.columns([1, 1, 4])
            if x.button("Approve", key=f"ok-{a['action_id']}", type="primary"):
                c.repo.decide_action(a["action_id"], True, "ops.console")
                st.rerun()
            if y.button("Reject", key=f"no-{a['action_id']}"):
                c.repo.decide_action(a["action_id"], False, "ops.console")
                st.rerun()
    done = [a for a in c.repo.list_actions() if a["status"] != "PENDING_APPROVAL"]
    if done:
        st.markdown("**Decision log**")
        st.dataframe(pd.DataFrame(done)[["action_type", "target", "status", "decided_by", "decided_at"]], hide_index=True)

with tab_eval:
    st.markdown("Runs the full multi-agent workflow on labelled faulty sites and healthy sites, then checks the "
                "quality gates used in CI.")
    n = st.slider("Cases", 20, 99, 40, step=10)
    if st.button("Run evaluation", type="primary"):
        from rca.eval.run import THRESHOLDS, evaluate, gate
        with st.spinner("Evaluating…"):
            rep = evaluate(n)
        m = rep["metrics"]
        cols = st.columns(4)
        for col, k in zip(cols, ["root_cause_accuracy", "key_entity_accuracy", "false_alarm_rate", "citation_validity"],
                          strict=True):
            col.metric(k.replace("_", " ").title(), f"{m[k]:.0%}", help=f"Gate: {THRESHOLDS[k]}")
        breaches = gate(m)
        (st.error if breaches else st.success)("Quality gate " + ("FAILED: " + "; ".join(breaches) if breaches else "PASSED"))
        st.json(m)

with tab_arch:
    st.graphviz_chart("""
    digraph A { rankdir=LR; node [shape=box, style="rounded,filled", fillcolor="#f4f4f4", fontname=Helvetica];
      UI [label="Streamlit console"]; API [label="FastAPI\\n(auth, metrics)"];
      subgraph cluster_g { label="LangGraph workflow"; style=dashed;
        R [label="Router"]; P [label="Planner\\n(LLM, catalog-constrained)"]; X [label="Executor\\n(parallel tools)"];
        AN [label="Analyst\\n(hypothesis ranking)"]; V [label="Verifier"]; RS [label="Responder\\n(cited report)"]; AC [label="Actions\\n(HITL)"];
        R -> P -> X -> AN -> V; V -> P [label="need evidence"]; V -> RS -> AC; }
      T [label="Tool registry\\n(Pydantic validation, audit)"]; MCP [label="MCP server"];
      DB [label="PostgreSQL / SQLite"]; G [label="Neo4j / NetworkX"]; RB [label="Runbooks (BM25)"]; LLM [label="Groq Llama / Ollama"];
      UI -> R; API -> R; X -> T; MCP -> T; T -> DB; T -> G; T -> RB; P -> LLM; RS -> LLM; }""")
    st.markdown("Source code, ADRs and runbook: see the GitHub repository README.")
