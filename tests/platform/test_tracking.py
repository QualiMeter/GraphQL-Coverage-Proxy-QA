import csv
import io
import json
from uuid import uuid4

import httpx
import pytest

from coverage_qa import tracking
from coverage_qa.control_panel import Manager, create_app
from coverage_qa.reporting.history import new_run, save_json
from coverage_qa.reporting.test_coverage import server_executions, server_matrix_csv
from coverage_qa.tracking import CoverageTrackingError, TestExecution as Execution
from fastapi.testclient import TestClient


def transport(monkeypatch, handler):
    client = httpx.Client
    monkeypatch.setattr(tracking.httpx, "Client", lambda **options: client(transport=httpx.MockTransport(handler), **options))


def test_metadata_retry_reuses_the_same_execution_id(monkeypatch):
    ids = []
    execution = Execution("http://coverage.test", uuid4(), "private-key", "cart::test", "Cart")

    def handle(request):
        ids.append(json.loads(request.content)["id"])
        if len(ids) == 1:
            raise httpx.ReadTimeout("test-only timeout")
        return httpx.Response(201, json={"id": execution.id, "runId": execution.run_id, "status": "running"})

    transport(monkeypatch, handle)
    execution.start()
    assert ids == [execution.id, execution.id]


def test_sdk_rejects_redirects_without_forwarding_credentials(monkeypatch):
    calls = []

    def handle(request):
        calls.append(str(request.url))
        return httpx.Response(302, headers={"Location": "http://foreign.test/"})

    transport(monkeypatch, handle)
    execution = Execution("http://coverage.test", uuid4(), "private-key", "test", "Test")
    with pytest.raises(CoverageTrackingError, match="HTTP 302"):
        execution.start()
    assert calls == ["http://coverage.test/api/runs/" + execution.run_id + "/tests"]
    assert not execution.started


def test_sdk_does_not_confirm_a_different_execution(monkeypatch):
    transport(monkeypatch, lambda request: httpx.Response(201, json={"id": str(uuid4()), "runId": str(uuid4())}))
    execution = Execution("http://coverage.test", uuid4(), "private-key", "test", "Test")
    with pytest.raises(CoverageTrackingError):
        execution.start()
    assert not execution.started


@pytest.mark.parametrize("status,message", [(201, "not JSON"), (409, "HTTP 409")])
def test_sdk_rejects_non_json_metadata_responses(monkeypatch, status, message):
    transport(monkeypatch, lambda request: httpx.Response(status, text="private upstream text"))
    execution = Execution("http://coverage.test", uuid4(), "private-key", "test", "Test")
    with pytest.raises(CoverageTrackingError, match=message):
        execution.start()
    assert not execution.started


def test_native_matrix_keeps_attempts_results_and_fields_without_local_inference(tmp_path):
    run_id, ident = str(uuid4()), str(uuid4())
    snapshot = {"source": "backend", "fetchedAt": "2026-11-18T00:00:00Z", "execution": {
        "id": ident, "runId": run_id, "testId": "test::cart", "name": "Cart", "framework": "pytest", "attempt": 2,
        "status": "failed", "durationMs": 12, "requestCount": 1}, "fields": [{"typeName": "User", "fieldName": "id",
        "selectedRequestCount": 1, "hits": 1, "observedCount": 1, "notObservedCount": 0,
        "indeterminateCount": 0, "unavailableCount": 0}], "requests": []}
    report = {"tests": [{"id": "local", "status": "passed", "serverTestCoverage": [snapshot]}]}
    rows = list(csv.DictReader(io.StringIO(server_matrix_csv(report))))
    assert rows[0]["status"] == "failed" and rows[0]["attempt"] == "2"
    assert rows[0]["execution_id"] == ident and rows[0]["hits"] == "1"
    folder = new_run(tmp_path, "Native matrix", "test")
    save_json(folder / "results.json", report)
    with TestClient(create_app(Manager(tmp_path))) as client:
        data = client.get(f"/api/runs/{folder.name}/test-coverage").json()
    assert data["source"] == "backend-snapshots" and data["executions"] == server_executions(report)
