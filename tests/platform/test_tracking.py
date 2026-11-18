import csv
import io
import json
from types import SimpleNamespace
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


@pytest.mark.parametrize("status,code", [("passed", None), ("failed", "assertion_failed"),
                                        ("failed", "teardown_failed"), ("interrupted", "runner_interrupted")])
def test_finish_retries_busy_with_the_original_result(monkeypatch, status, code):
    execution = Execution("http://coverage.test", uuid4(), "private-key", "test", "Test")
    execution.started = True
    bodies = []

    def handle(request):
        bodies.append(json.loads(request.content))
        if len(bodies) == 1:
            return httpx.Response(409, json={"detail": {"code": "test_requests_in_flight"}})
        return httpx.Response(200, json={"id": execution.id, "runId": execution.run_id, "status": status})

    transport(monkeypatch, handle)
    monkeypatch.setattr(execution, "read", lambda: None)
    monkeypatch.setattr(tracking.time, "sleep", lambda _: None)
    execution.finish(status, 123, code)
    assert execution.finished
    assert bodies == [{"status": status, "durationMs": 123, "failureCode": code}] * 2


def test_busy_cleanup_preserves_the_original_failure_and_saves_snapshot(monkeypatch):
    from coverage_qa.pytest_plugin import publish, pytest_sessionfinish

    execution = Execution("http://coverage.test", uuid4(), "private-key", "test", "Test")
    execution.started = True
    bodies = []
    busy = True

    def handle(request):
        bodies.append(json.loads(request.content))
        if busy:
            return httpx.Response(409, json={"detail": {"code": "test_requests_in_flight"}})
        return httpx.Response(200, json={"id": execution.id, "runId": execution.run_id, "status": "failed"})

    transport(monkeypatch, handle)
    monkeypatch.setattr(tracking.time, "sleep", lambda _: None)
    monkeypatch.setattr(execution, "read", lambda: {"execution": {"id": execution.id}})
    entry = {}
    context = SimpleNamespace(executions={1: execution}, reports=[SimpleNamespace(duration=.123)],
                              item=SimpleNamespace(api_entry=entry))
    with pytest.raises(CoverageTrackingError, match="in flight"):
        publish(context, "failed", "teardown_failed")
    assert len(bodies) == 3
    busy = False
    session = SimpleNamespace(config=SimpleNamespace(_coverage_test_contexts=[context]), exitstatus=0)
    pytest_sessionfinish(session, 0)
    assert execution.finished
    assert len(entry["serverTestCoverage"]) == 1
    assert all(body == {"status": "failed", "durationMs": 123, "failureCode": "teardown_failed"} for body in bodies)


def test_finish_rejects_unrelated_conflicts_without_retry(monkeypatch):
    execution = Execution("http://coverage.test", uuid4(), "private-key", "test", "Test")
    execution.started = True
    calls = []
    transport(monkeypatch, lambda request: (calls.append(request) or httpx.Response(409, json={"detail": "different result"})))
    with pytest.raises(CoverageTrackingError, match="HTTP 409"):
        execution.finish("passed", 10)
    assert len(calls) == 1


def test_failed_snapshot_read_is_retried_before_result_is_considered_published(monkeypatch):
    from coverage_qa.pytest_plugin import publish, pytest_sessionfinish

    execution = Execution("http://coverage.test", uuid4(), "private-key", "test", "Test")
    execution.started = True
    submitted = []

    def handle(request):
        submitted.append(json.loads(request.content))
        return httpx.Response(200, json={"id": execution.id, "runId": execution.run_id, "status": "passed"})

    transport(monkeypatch, handle)
    monkeypatch.setattr(execution, "read", lambda: (_ for _ in ()).throw(CoverageTrackingError("read unavailable")))
    entry = {}
    context = SimpleNamespace(executions={1: execution}, reports=[SimpleNamespace(duration=.123)],
                              item=SimpleNamespace(api_entry=entry))
    with pytest.raises(CoverageTrackingError, match="read unavailable"):
        publish(context, "passed", None)
    assert not execution.finished
    monkeypatch.setattr(execution, "read", lambda: {"execution": {"id": execution.id}})
    session = SimpleNamespace(config=SimpleNamespace(_coverage_test_contexts=[context]), exitstatus=0)
    pytest_sessionfinish(session, 0)
    assert execution.finished and len(entry["serverTestCoverage"]) == 1
    assert submitted == [{"status": "passed", "durationMs": 123, "failureCode": None}] * 2


def test_finish_busy_retry_has_a_deadline(monkeypatch):
    execution = Execution("http://coverage.test", uuid4(), "private-key", "test", "Test")
    execution.started = True
    clock, calls = [0.0], []
    monkeypatch.setattr(tracking.time, "monotonic", lambda: clock[0])

    def handle(request):
        calls.append(request)
        clock[0] += 6
        return httpx.Response(409, json={"detail": {"code": "test_requests_in_flight"}})

    transport(monkeypatch, handle)
    with pytest.raises(CoverageTrackingError, match="in flight"):
        execution.finish("passed", 1)
    assert len(calls) == 1


def test_sessionfinish_can_mark_an_unfinished_test_as_interrupted(monkeypatch):
    from coverage_qa.pytest_plugin import pytest_sessionfinish

    execution = Execution("http://coverage.test", uuid4(), "private-key", "test", "Test")
    execution.started = True
    transport(monkeypatch, lambda request: httpx.Response(
        200, json={"id": execution.id, "runId": execution.run_id, "status": "interrupted"}))
    monkeypatch.setattr(execution, "read", lambda: {"execution": {"id": execution.id}})
    context = SimpleNamespace(executions={1: execution}, reports=[SimpleNamespace(duration=.123)],
                              item=SimpleNamespace(api_entry={}))
    session = SimpleNamespace(config=SimpleNamespace(_coverage_test_contexts=[context]), exitstatus=0)
    pytest_sessionfinish(session, 0)
    assert execution.final_result["status"] == "interrupted" and execution.finished


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


def test_pytest_retry_retains_previous_unconfirmed_result_for_shutdown(monkeypatch):
    from coverage_qa import pytest_plugin
    previous = SimpleNamespace(finished=False, final_result={'status': 'failed', 'durationMs': 3, 'failureCode': 'assertion_failed'})
    context = SimpleNamespace(attempt=1, executions={'old': previous}, reports=[])
    config = SimpleNamespace(_coverage_test_contexts=[context])
    item = SimpleNamespace(execution_count=2, config=config)
    monkeypatch.setattr(pytest_plugin, 'configured_run', lambda config: (None, None, None))
    token = tracking.CURRENT_TEST.set(context)
    try:
        pytest_plugin.pytest_runtest_setup(item)
    finally:
        tracking.CURRENT_TEST.reset(token)
    assert context.attempt == 2 and not context.executions
    pending = [execution for saved in config._coverage_test_contexts for execution in saved.executions.values()]
    assert pending == [previous]
    assert pending[0].final_result['status'] == 'failed'
