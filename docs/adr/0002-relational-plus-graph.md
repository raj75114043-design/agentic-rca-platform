# ADR-0002: PostgreSQL for records, Neo4j for dependencies

**Status:** Accepted

## Context
Investigations need both filtering/aggregation (overdue purchase orders, vendor on-time rates) and multi-hop
traversal (site → activity → purchase order → vendor; incident → site → region).

## Decision
Keep operational records in PostgreSQL and the dependency graph in Neo4j, behind `OpsRepository` and `GraphStore`
interfaces. Lite mode swaps in SQLite and NetworkX with the same interfaces for tests and the hosted demo.

## Consequences
- Each query runs on the store suited to it; graph traversal stays readable in Cypher.
- Two stores must be kept in sync; seeding loads both from the same source.
- A parity test in CI checks that Neo4j and NetworkX return the same dependencies.
