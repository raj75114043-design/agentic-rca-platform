"""Parity test between the Neo4j and NetworkX graph backends. Runs only when NEO4J_TEST_URI is set (CI service)."""
import os

import pytest

from rca.data.generator import generate
from rca.stores.graph import Neo4jGraph, NetworkXGraph

URI = os.getenv("NEO4J_TEST_URI")
pytestmark = pytest.mark.skipif(not URI, reason="NEO4J_TEST_URI not set")


def test_neo4j_matches_networkx():
    world = generate(n_sites=40)
    nx_g, neo = NetworkXGraph(), Neo4jGraph(URI, "neo4j", os.getenv("NEO4J_TEST_PASSWORD", "rca-password"))
    nx_g.load(world)
    neo.load(world)
    vendor = world.vendors[0]["vendor_id"]
    assert neo.vendor_impact(vendor) == nx_g.vendor_impact(vendor)
    site = world.labels[0]["site_id"]
    ids = lambda paths: {h["id"] for p in paths for h in p["hops"]}  # noqa: E731
    assert ids(neo.dependencies(site, 3)) == ids(nx_g.dependencies(site, 3))
