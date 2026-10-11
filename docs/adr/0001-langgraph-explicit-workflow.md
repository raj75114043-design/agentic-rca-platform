# ADR-0001: Explicit LangGraph state machine instead of a free-running agent

**Status:** Accepted

## Context
A single ReAct-style agent decides its own next step indefinitely. In operations work this makes runs hard to
bound, hard to test and hard to explain to auditors.

## Decision
Model the investigation as an explicit LangGraph graph with typed shared state: Router, Planner, Executor, Analyst,
Verifier, Responder and Actions. The only loop is Verifier → Planner, bounded by `MAX_ITERATIONS`.

## Consequences
- Every run has a predictable shape and a per-node trace (duration, LLM calls, summary) that is persisted.
- Each node can be unit-tested and swapped (for example a smaller model for routing).
- Less flexible than an open agent for unforeseen question types; out-of-scope questions are declined.
