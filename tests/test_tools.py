def test_valid_call_returns_evidence(container, labels):
    site = labels["PERMIT_PENDING"]["site_id"]
    r = container.tools.call("get_permits", {"site_id": site})
    assert r["ok"] and r["evidence"][0]["id"].startswith("permit:")


def test_invalid_site_id_rejected(container):
    r = container.tools.call("get_site", {"site_id": "SITE-1; DROP TABLE sites"})
    assert not r["ok"] and r["error"].startswith("VALIDATION_ERROR")


def test_unknown_tool_rejected(container):
    assert container.tools.call("delete_everything", {})["error"].startswith("UNKNOWN_TOOL")


def test_not_found_is_a_tool_error_not_a_crash(container):
    r = container.tools.call("get_site", {"site_id": "SITE-9999"})
    assert not r["ok"] and "NOT_FOUND" in r["error"]


def test_catalog_has_json_schemas(container):
    names = {t["name"] for t in container.tools.catalog()}
    assert {"get_site", "get_purchase_orders", "search_runbooks"} <= names


def test_graph_dependencies_reach_vendor(container, labels):
    site = labels["VENDOR_DELAY"]["site_id"]
    paths = container.graph.dependencies(site, 3)
    labels_seen = {h["label"] for p in paths for h in p["hops"]}
    assert {"Activity", "PurchaseOrder", "Vendor"} <= labels_seen
