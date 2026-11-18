"""Real FORMA browser scenarios against disposable commerce and Coverage databases."""
from collections import Counter
import json
import os
import subprocess
import sys

import pytest
from playwright.sync_api import expect, sync_playwright

from coverage_qa.environment import ROOT, Server
from coverage_qa.reporting.history import new_run
from coverage_qa.tracking import TestExecution as Execution, save_snapshot
from tests.support.browser import browser_options
from tests.support.coverage_client import CoverageRun, field_hits
from tests.support.http_client import API, ensure_entry


CHILD = '''import pytest
from uuid import uuid4
from tests.e2e.test_shop import (
    test_storefront_catalog_and_cart,
    test_storefront_login_error_and_inspector,
)

def test_assertion_failure(storefront):
    storefront.direct('mutation($input:CreateOrderInput!){createOrder(input:$input){id}}',
        {"input": {"items": [{"productId": "cable", "quantity": 1}], "idempotencyKey": str(uuid4())}})
    assert False, "Intentional assertion failure after browser coverage and order creation"

@pytest.fixture
def broken_teardown(storefront):
    yield
    raise AssertionError("Intentional teardown failure after browser coverage")

def test_teardown_failure(storefront, broken_teardown):
    assert storefront.page.title()

@pytest.mark.skip(reason="Intentional skip before storefront setup")
def test_skipped(storefront):
    raise AssertionError("Must never run")
'''

CONFTEST = '''from tests.conftest import *
from tests.support.storefront import storefront
from types import SimpleNamespace

@pytest.fixture(scope="session")
def business_stack():
    # The parent owns this disposable infrastructure; each scenario creates its own run.
    return SimpleNamespace(proxy=SimpleNamespace(base=os.environ["FORMA_TEST_PROXY"]),
        upstream=SimpleNamespace(base=os.environ["FORMA_TEST_UPSTREAM"]), key=os.environ["COVERAGE_API_KEY"], shared_run_id=os.environ.get("FORMA_TEST_SHARED_RUN"))
'''


@pytest.mark.coverage
@pytest.mark.browser
@pytest.mark.parametrize("deployed", [False, True], ids=["isolated", "existing-forma-run"])
@pytest.mark.case("FORMA: реальные браузерные сценарии, Test Execution, ошибки и изоляция общего прогона")
def test_forma_browser_execution_lifecycle(business_stack, request, deployed):
    stack = business_stack
    entry = ensure_entry(request)
    api = API(stack.proxy.base + "/graphql", entry, stack.key)
    owner = CoverageRun(api, stack, {"id": request.config.api_report["id"], "coverageRuns": []}, total_fields=35)
    reports = request.config.run_folder / ("forma-shared" if deployed else "forma-isolated")
    folder = new_run(reports, "FORMA controlled lifecycle", "e2e")
    (folder / "test_forma_child.py").write_text(CHILD, encoding="utf-8")
    (folder / "conftest.py").write_text(CONFTEST, encoding="utf-8")
    headers = {"X-Coverage-Api-Key": stack.key}

    def get(path):
        response = api.client.get(stack.proxy.base + path, headers=headers)
        response.raise_for_status()
        return response.json()

    def commerce(query):
        response = api.client.post(stack.upstream.base + "/graphql", json={"query": query})
        response.raise_for_status()
        assert not response.json().get("errors")
        return response.json()["data"]

    def all_run_ids():
        return {r["id"] for service in get("/api/services")
                for r in get(f"/api/runs?serviceId={service['id']}")}

    before_stock = commerce("{products{id stock}}")
    try:
        if deployed:
            owner.prepare(request.node.nodeid)
        runs_before = all_run_ids()
        env = {**os.environ, "FORMA_TEST_PROXY": stack.proxy.base, "FORMA_TEST_UPSTREAM": stack.upstream.base,
               "COVERAGE_API_KEY": stack.key, "PYTHONDONTWRITEBYTECODE": "1"}
        for name in ("COVERAGE_TEST_RUN_ID", "COVERAGE_URL"):
            env.pop(name, None)
        command = [sys.executable, "-m", "pytest", "-c", str(ROOT / "pyproject.toml"), "-q", str(folder),
                   "--run-folder", str(folder), "--confcutdir", str(folder)]
        if deployed:
            # This parent owns disposable commerce + Coverage databases. Exercise
            # shared-run attribution without using the production deployed mode.
            env.update(FORMA_TEST_SHARED_RUN=owner.run_id, COVERAGE_TEST_RUN_ID=owner.run_id,
                       COVERAGE_URL=stack.proxy.base)
        result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, timeout=180)
        (folder / "runner.log").write_text((result.stdout + result.stderr).replace(stack.key, "<redacted>"), encoding="utf-8")
        assert result.returncode == 1, result.stdout + result.stderr
        report = json.loads((folder / "results.json").read_text(encoding="utf-8"))
        assert not report["infrastructureErrors"]
        assert Counter(t["status"] for t in report["tests"]) == {"passed": 2, "failed": 2, "skipped": 1}
        run_ids = {r["runId"] for r in report["coverageRuns"]}
        runs_after = all_run_ids()
        if deployed:
            assert run_ids == {owner.run_id}
            assert runs_after == runs_before, "FORMA must not create another run"
            assert get(f"/api/runs/{owner.run_id}/test-coverage")["collectionStatus"] == "running"
        else:
            assert len(run_ids) == 4
            assert runs_after - runs_before == run_ids
            assert all(get(f"/api/runs/{ident}/test-coverage")["collectionStatus"] == "completed" for ident in run_ids)

        snapshots = [s for t in report["tests"] for s in t.get("serverTestCoverage", [])]
        for snapshot in snapshots:
            save_snapshot(entry, snapshot)
        entry["events"].append({"kind": "attachment", "title": "FORMA: дочерний pytest с намеренными ошибками",
                                "path": str((folder / "runner.log").relative_to(request.config.run_folder))})
        assert len(snapshots) == (5 if deployed else 4)
        rows = [s["execution"] for s in snapshots]
        assert len({r["id"] for r in rows}) == len(rows)
        assert all(r["status"] != "running" and r["finishedAt"] and not r["resultMissing"] for r in rows)
        for record in report["coverageRuns"]:
            row = next(r for r in rows if r["testId"] == record["testId"])
            catalog = [e for e in record["exchanges"] if e["operationName"] == "GetProducts"]
            assert len(catalog) == 1
            assert len(catalog[0]["observedRequestIds"]) == 1
            detail = record["requestDetails"][catalog[0]["observedRequestIds"][0]]
            assert detail["testExecutionId"] == row["id"]
        seen_requests = set()
        for snapshot in snapshots:
            row = snapshot["execution"]
            assert row["runId"] in run_ids
            own = snapshot["requests"]
            assert len(own) == row["requestCount"]
            ids = {r["id"] for r in own}
            assert not ids & seen_requests
            seen_requests |= ids
            assert all(r["testExecutionId"] == row["id"] and r["runId"] == row["runId"] for r in own)
            if row["testId"].endswith("test_skipped"):
                assert row["status"] == "skipped" and not own and not snapshot["fields"]
            else:
                assert own and row["coveredFieldCount"] > 0
                assert any(f["typeName"] == "Query" and f["fieldName"] == "products" and f["hits"] > 0
                           for f in snapshot["fields"])
            if row["testId"].endswith("test_assertion_failure"):
                assert (row["status"], row["failureCode"]) == ("failed", "assertion_failed")
            elif row["testId"].endswith("test_teardown_failure"):
                assert (row["status"], row["failureCode"]) == ("failed", "teardown_failed")
            elif "test_storefront_" in row["testId"]:
                assert row["status"] == "passed"
            # Repeat start and finish against the real API: metadata must remain idempotent.
            tracker = Execution(stack.proxy.base, row["runId"], stack.key, row["testId"], row["name"], execution_id=row["id"], attempt=row["attempt"])
            tracker.start()
            tracker.finish(row["status"], row["durationMs"], row["failureCode"])

        for ident in run_ids:
            all_rows = get(f"/api/runs/{ident}/tests?limit=500")
            expected = [s for s in snapshots if s["execution"]["runId"] == ident]
            # The parent tracker in the shared run has no browser requests.
            child_ids = {s["execution"]["id"] for s in expected}
            assert {r["id"] for r in all_rows if "test_forma_child.py" in r["testId"]} == child_ids
            requests = [row for snapshot in expected for row in
                        Execution(stack.proxy.base, ident, stack.key, snapshot["execution"]["testId"],
                                  snapshot["execution"]["name"], execution_id=snapshot["execution"]["id"]).read()["requests"]]
            assert {r["id"] for r in requests} == {r["id"] for s in expected for r in s["requests"]}
            hits = Counter()
            for snapshot in expected:
                hits.update({f"{f['typeName']}.{f['fieldName']}": f["hits"] for f in snapshot["fields"]})
            aggregate = field_hits(get(f"/api/runs/{ident}/coverage"))
            assert all(aggregate.get(k, 0) >= v for k, v in hits.items())
            if not deployed:
                assert {k: v for k, v in aggregate.items() if v} == {k: v for k, v in hits.items() if v}
            summary = get(f"/api/runs/{ident}/test-coverage")
            assert summary["totalRequests"] >= summary["attributedRequests"] >= len(requests)
            assert summary["missingResults"] == summary["inFlightRequests"] == 0

        assert commerce("{products{id stock}}") == before_stock, "Cleanup must restore test order stock even after assertion failure"
        # The same server snapshots must be available through the existing TestLab tab.
        panel = Server("tests.support.panel_app:app", ROOT, folder / "panel.log", {"QA_TEST_REPORTS": str(reports)})
        try:
            panel.start()
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(**browser_options())
                try:
                    page = browser.new_page()
                    page.goto(panel.base)
                    page.locator("#nav-history").click()
                    page.get_by_role("row").filter(has_text=folder.name).get_by_role("button", name="Открыть →").click()
                    page.locator('[data-detail-tab="test-coverage"]').click()
                    expect(page.get_by_test_id("server-test-execution")).to_have_count(len(snapshots))
                    expect(page.locator(".server-fields-table").first).to_contain_text("Query.products")
                finally:
                    browser.close()
        finally:
            panel.stop()
    finally:
        try:
            if owner.run_id:
                owner.finish()  # Only the parent owns this disposable run.
        finally:
            api.client.close()
