from fastapi.testclient import TestClient

from rca.api import app

client = TestClient(app)
H = {"X-API-Key": "test-key"}


def test_health_and_ready():
    assert client.get("/health").json()["status"] == "ok"
    assert client.get("/ready").json()["graph"]["backend"] == "networkx"


def test_auth_required():
    assert client.post("/v1/rca", json={"question": "Why is SITE-0001 down?"}).status_code == 401


def test_input_validation():
    assert client.post("/v1/rca", json={"question": "x"}, headers=H).status_code == 422


def test_rca_run_is_persisted_and_traceable():
    r = client.post("/v1/rca", json={"question": "Which vendors are delaying deliveries?"}, headers=H)
    assert r.status_code == 200 and "X-Request-ID" in r.headers
    run = client.get(f"/v1/runs/{r.json()['run_id']}", headers=H).json()
    assert run["trace"]["trace"][0]["node"] == "router"


def test_hitl_action_approval_flow():
    from rca.container import get_container
    labels = {l["root_cause"]: l for l in get_container().repo.labels()}
    site = labels["PERMIT_PENDING"]["site_id"]
    res = client.post("/v1/rca", json={"question": f"Why is {site} delayed?"}, headers=H).json()
    action_id = res["actions"][0]["action_id"]
    ok = client.post(f"/v1/actions/{action_id}/decision", json={"approve": True, "user": "ops.lead"}, headers=H)
    assert ok.json()["status"] == "APPROVED"
    again = client.post(f"/v1/actions/{action_id}/decision", json={"approve": False, "user": "ops.lead"}, headers=H)
    assert again.status_code == 409


def test_metrics_exposed():
    assert "rca_runs_total" in client.get("/metrics").text
