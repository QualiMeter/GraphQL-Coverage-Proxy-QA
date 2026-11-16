"""Deployed FORMA mode may only reuse a verified active demo run."""
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from coverage_qa.control_panel import Manager, create_app, validate_deployed_run


def test_deployed_mode_requires_server_side_key(tmp_path, monkeypatch):
    for key in ("QA_DEPLOYED_COVERAGE_URL", "QA_DEPLOYED_SHOP_API_URL", "COVERAGE_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    manager = Manager(tmp_path / "reports")
    manager._catalog = [{"id": "tests/e2e/test_shop.py::test_one", "group": "e2e"}]
    with TestClient(create_app(manager)) as client:
        state = client.get("/api/state").json()
        assert state["deployedAvailable"] is False
        assert "COVERAGE_API_KEY" not in str(state)
        response = client.post("/api/runs", headers={"X-Panel-Token": manager.token},
                               json={"node_ids": [manager._catalog[0]["id"]],
                                     "deployed_run_id": str(uuid4())})
        assert response.status_code == 503


def test_deployed_mode_rejects_non_browser_tests_before_network(tmp_path, monkeypatch):
    monkeypatch.setenv("COVERAGE_API_KEY", "server-only")
    monkeypatch.setenv("QA_DEPLOYED_COVERAGE_URL", "https://graphql.rebyte.fun")
    monkeypatch.setenv("QA_DEPLOYED_SHOP_API_URL", "http://127.0.0.1:9100")
    manager = Manager(tmp_path / "reports")
    manager._catalog = [{"id": "t-business", "group": "business"}, {"id": "t-e2e", "group": "e2e"}]
    with pytest.raises(HTTPException) as error:
        manager.start(["t-business", "t-e2e"], "Test", False, str(uuid4()))
    assert error.value.status_code == 400
    assert manager.process is None


def test_deployed_mode_verifies_service_and_active_run(monkeypatch):
    run_id = str(uuid4())
    secret = "server-side-test-key"
    monkeypatch.setenv("COVERAGE_API_KEY", secret)
    monkeypatch.setenv("QA_DEPLOYED_COVERAGE_URL", "https://graphql.rebyte.fun")
    monkeypatch.setenv("QA_DEPLOYED_SHOP_API_URL", "http://127.0.0.1:9100")
    original_client = httpx.Client
    observed = []

    def handler(request):
        observed.append((request.url.path, request.headers.get("X-Coverage-Api-Key")))
        if request.url.path == "/api/services":
            return httpx.Response(200, json=[{"id": "service-demo", "key": "forma-demo-shop"},
                                              {"id": "other", "key": "not-forma"}])
        if request.url.path == "/api/runs":
            assert request.url.params["serviceId"] == "service-demo"
            assert request.url.params["status"] == "running"
            return httpx.Response(200, json=[{"id": run_id, "status": "running"}])
        if request.url.path == "/graphql":
            return httpx.Response(200, json={"data": {"__typename": "Query"}})
        return httpx.Response(404)

    monkeypatch.setattr(httpx, "Client", lambda **kwargs: original_client(
        transport=httpx.MockTransport(handler), **kwargs))
    assert validate_deployed_run(run_id) == (
        run_id, "https://graphql.rebyte.fun", "http://127.0.0.1:9100")
    assert observed == [("/api/services", secret), ("/api/runs", secret), ("/graphql", None)]

    with pytest.raises(HTTPException) as error:
        validate_deployed_run(str(uuid4()))
    assert error.value.status_code == 409


def test_deployed_mode_rejects_invalid_uuid_before_network():
    with pytest.raises(HTTPException) as error:
        validate_deployed_run("../other")
    assert error.value.status_code == 400
