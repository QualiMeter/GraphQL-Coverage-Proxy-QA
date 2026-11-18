import asyncio
import hashlib
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from examples.shop.web.server import app
from tests.support.coverage_client import CoverageRun
from tests.support.http_client import API


def test_storefront_keeps_credentials_and_attribution_on_the_server(monkeypatch):
    secret = "test-only-server-secret"
    for name, value in {"SHOP_COVERAGE_API_KEY": secret, "SHOP_COVERAGE_RUN_ID": "selected-run",
                        "SHOP_COVERAGE_TEST_EXECUTION_ID": "this-execution"}.items():
        monkeypatch.setenv(name, value)
    observed = []

    async def upstream(request):
        observed.append(request)
        return httpx.Response(200, json={"data": {"__typename": "Query"}}, headers={
            "X-Coverage-Api-Key": secret, "X-Upstream-Debug": secret})

    with TestClient(app) as client:
        original = app.state.client
        app.state.client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        try:
            responses = [client.get(path) for path in ("/", "/api/config", "/assets/shop.js")]
            responses.append(client.post("/api/graphql", json={"query": "{__typename}"}, headers={
                "Authorization": "Bearer customer-token", "X-Coverage-Api-Key": "forged-key",
                "X-Coverage-Run-Id": "foreign-run", "X-Coverage-Test-Execution-Id": "foreign-test"}))
            assert all(response.status_code == 200 for response in responses)
            assert all(secret not in response.text and secret not in str(response.headers) for response in responses)
            headers = observed[0].headers
            assert headers["X-Coverage-Api-Key"] == secret
            assert headers["X-Coverage-Run-Id"] == "selected-run"
            assert headers["X-Coverage-Test-Execution-Id"] == "this-execution"
            assert headers["Authorization"] == "Bearer customer-token"
        finally:
            asyncio.run(app.state.client.aclose())
            app.state.client = original


@pytest.mark.parametrize("owner", ["this-execution", "foreign-execution", None])
def test_browser_exchange_is_only_matched_with_its_own_execution(monkeypatch, owner):
    entry = {"id": "test", "events": [{}]}
    api = API("http://coverage.test/graphql", entry, "server-key")
    run = CoverageRun(api, SimpleNamespace(proxy=SimpleNamespace(base="http://coverage.test")), {})
    run.run_id = "run"
    run.execution = SimpleNamespace(id="this-execution", read=lambda: {"execution": {"id": "this-execution"}})
    query_hash = hashlib.sha256(b"{products{id}}").hexdigest()
    operation_key = hashlib.sha256(json.dumps([query_hash, None, "query"], separators=(",", ":")).encode()).hexdigest()
    exchange = {"runId": "run", "queryHash": query_hash, "operationName": None,
                "attribution": "browser-operation-aggregate", "eventIndex": 0}
    run.record = {"exchanges": [exchange], "checkpoints": [], "requestDetails": {}}
    detail = {"id": "request", "operationKey": operation_key, "testExecutionId": owner,
              "selectedFields": [], "countedInCoverage": True}
    replies = {"/api/runs/run/coverage": {"types": [], "summary": {}},
               "/api/runs/run/operations": [{"operationKey": operation_key, "operationType": "query",
                   "requestCount": 1, "countedRequestCount": 1, "fields": []}],
               "/api/runs/run/requests?limit=500": [{"id": "request"}]}
    monkeypatch.setattr(run, "rest", lambda title, method, path: replies[path])
    monkeypatch.setattr(run, "request_detail", lambda ident: detail)
    try:
        run.snapshot("Shared FORMA run")
        assert exchange.get("observedRequestIds", []) == (["request"] if owner == "this-execution" else [])
    finally:
        api.client.close()
