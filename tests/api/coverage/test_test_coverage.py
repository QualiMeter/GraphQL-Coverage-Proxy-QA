import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import pytest
from playwright.sync_api import expect, sync_playwright

from coverage_qa.environment import ROOT, Server
from coverage_qa.reporting.progress import emit
from coverage_qa.tracking import TestExecution as Execution, save_snapshot
from tests.support.browser import browser_options

pytestmark = pytest.mark.coverage

PYTEST_CHILD = '''import os
import httpx
import pytest

def user(execution):
    response = httpx.post(os.environ["COVERAGE_URL"] + "/graphql",
        headers={**execution.headers, "X-Coverage-Api-Key": os.environ["COVERAGE_API_KEY"]},
        json={"query": '{ user(id: "1") { id name } }'}, trust_env=False)
    assert response.status_code == 200
    return response.json()["data"]["user"]

def test_pass(coverage_test):
    assert user(coverage_test)["name"] == "Anna"

def test_assertion_failure(coverage_test):
    assert user(coverage_test)["name"] == "Different name"

@pytest.fixture
def broken_cleanup():
    yield
    raise AssertionError("Intentional teardown failure")

def test_teardown_failure(coverage_test, broken_cleanup):
    assert user(coverage_test)["id"] == "1"

@pytest.mark.skip(reason="Intentional skipped example")
def test_skipped():
    pass
'''


def child_environment(run):
    return {**os.environ, "PYTHONPATH": str(ROOT / "src"), "PYTHONDONTWRITEBYTECODE": "1",
            "COVERAGE_URL": run.base, "COVERAGE_API_KEY": run.api.key, "COVERAGE_TEST_RUN_ID": run.run_id}


def executions(run, framework):
    rows = run.rest("Серверные результаты " + framework, "GET", f"/api/runs/{run.run_id}/tests?framework={framework}&limit=500")
    for row in rows:
        tracker = Execution(run.base, run.run_id, run.api.key, row["testId"], row["name"],
                            framework=framework, attempt=row["attempt"], execution_id=row["id"])
        save_snapshot(run.api.entry, tracker.read())
    emit(force=True)
    return rows


@pytest.mark.browser
@pytest.mark.case("Test Coverage: реальные passed/failed/teardown/skipped из pytest и матрица в QA-панели")
def test_pytest_result_lifecycle_and_native_panel(coverage_run, request):
    run = coverage_run
    folder = request.config.run_folder / "pytest-result-example"
    folder.mkdir()
    source = folder / "test_external.py"
    source.write_text(PYTEST_CHILD, encoding="utf-8")
    result = subprocess.run([sys.executable, "-m", "pytest", "-p", "coverage_qa.pytest_plugin", "-q", str(source),
                             "-o", "cache_dir=" + str(folder / "cache")], cwd=folder,
                            env=child_environment(run), capture_output=True, text=True, encoding="utf-8", timeout=90)
    (folder / "runner.log").write_text((result.stdout + result.stderr).replace(run.api.key, "<redacted>"), encoding="utf-8")
    run.api.entry["events"].append({"kind": "attachment", "title": "Дочерний pytest: намеренные ошибки", "path": "pytest-result-example/runner.log"})
    run.api.expect("Дочерний runner завершился с реальным падением", result.returncode, 1)
    rows = executions(run, "pytest")
    child = {row["testId"].rsplit("::", 1)[-1]: row for row in rows if "test_external.py" in row["testId"]}
    run.api.expect("Результаты всех четырёх тестов сохранены", set(child),
                   {"test_pass", "test_assertion_failure", "test_teardown_failure", "test_skipped"})
    run.api.expect("Passed пришёл от pytest", child["test_pass"]["status"], "passed")
    run.api.expect("HTTP 200 не превратил провал assertion в passed", child["test_assertion_failure"]["status"], "failed")
    run.api.expect("Ошибка teardown сохранена после тела теста", child["test_teardown_failure"]["failureCode"], "teardown_failed")
    run.api.expect("Skipped сохранён без выдуманного запроса", (child["test_skipped"]["status"], child["test_skipped"]["requestCount"]), ("skipped", 0))
    for name in ("test_pass", "test_assertion_failure", "test_teardown_failure"):
        run.api.expect("Успешные HTTP-ответы связаны с каждым реальным тестом: " + name, child[name]["coveredFieldCount"], 3)
    run.snapshot("Общее покрытие не зависит от исхода assertions")
    if hasattr(run.stack.proxy, "restart"):
        run.stack.proxy.restart()
    persisted = run.rest("Проверка результатов после перезапуска", "GET", f"/api/runs/{run.run_id}/tests?framework=pytest&limit=500")
    run.api.expect("Результаты пережили перезапуск backend", persisted, rows)
    panel = Server("tests.support.panel_app:app", ROOT, request.config.run_folder / "test-coverage-panel.log",
                   {"QA_TEST_REPORTS": str(request.config.run_folder.parent.parent)})
    panel.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(**browser_options())
            try:
                page = browser.new_page(viewport={"width": 1500, "height": 1050})
                page.goto(panel.base)
                page.locator("#nav-history").click()
                page.get_by_role("row").filter(has_text=request.config.run_folder.name).get_by_role("button", name="Открыть →").click()
                page.locator('[data-detail-tab="test-coverage"]').click()
                page.get_by_role("textbox", name="Поле для поиска тестов").fill("User.name")
                page.get_by_role("combobox", name="Результат серверного теста").select_option("failed")
                expect(page.get_by_test_id("server-test-execution")).to_have_count(2)
                expect(page.locator(".server-fields-table").first).to_contain_text("User.name")
                image = "test-coverage-native.png"
                page.screenshot(path=str(request.config.run_folder / image), full_page=True)
                run.api.entry["events"].append({"kind": "attachment", "title": "Test Coverage: два реально упавших теста обращались к User.name", "path": image})
            finally:
                browser.close()
    finally:
        panel.stop()


@pytest.mark.case("Test Coverage: Playwright reporter, параллельные тесты, retries и ошибка teardown")
def test_playwright_result_retry_and_parallel_attribution(coverage_run, request):
    run = coverage_run
    folder = request.config.run_folder / "playwright-result-example"
    folder.mkdir()
    package = ROOT / "acceptance/mvp/node_modules/@playwright/test"
    if not package.is_dir():
        pytest.fail("Install acceptance/mvp dependencies before running the Playwright Test Coverage scenario")
    sdk = ROOT / "integrations/playwright/index.cjs"
    source = '''const {test: base, expect} = require(PACKAGE);
const {createCoverageTest} = require(SDK);
const test = createCoverageTest(base);
async function user(request, coverage) {
  const execution = await coverage.trackRun(process.env.COVERAGE_TEST_RUN_ID);
  const response = await execution.post(request, {query: '{ user(id: "1") { id name } }'});
  expect(response.status()).toBe(200);
  return (await response.json()).data.user;
}
test('passes', async ({request, coverage}) => { expect((await user(request, coverage)).name).toBe('Anna'); });
test('assertion fails', async ({request, coverage}) => { expect((await user(request, coverage)).name).toBe('Different name'); });
test('retry succeeds', async ({request, coverage}, info) => { const data = await user(request, coverage); expect(data.name).toBe(info.retry ? 'Anna' : 'Different name'); });
const cleanup = test.extend({broken: [async ({}, use) => { await use(); throw new Error('Intentional teardown failure'); }, {auto: true}]});
cleanup('teardown fails', async ({request, coverage}) => { expect((await user(request, coverage)).id).toBe('1'); });
test.skip('skipped', async () => {});
'''.replace("PACKAGE", json.dumps(str(package))).replace("SDK", json.dumps(str(sdk)))
    (folder / "example.spec.cjs").write_text(source, encoding="utf-8")
    (folder / "playwright.config.cjs").write_text("module.exports=" + json.dumps({"testDir": str(folder), "workers": 2,
        "fullyParallel": True, "retries": 1, "reporter": [["list"], [str(sdk)]], "outputDir": str(folder / "results")}), encoding="utf-8")
    result = subprocess.run(["node", str(package / "cli.js"), "test", "--config", str(folder / "playwright.config.cjs")],
                            cwd=folder, env=child_environment(run), capture_output=True, text=True, encoding="utf-8", timeout=120)
    (folder / "runner.log").write_text((result.stdout + result.stderr).replace(run.api.key, "<redacted>"), encoding="utf-8")
    run.api.entry["events"].append({"kind": "attachment", "title": "Дочерний Playwright: реальные ошибки и retries", "path": "playwright-result-example/runner.log"})
    run.api.expect("Playwright вернул ошибку из-за намеренно падающих тестов", result.returncode, 1)
    rows = executions(run, "playwright")
    run.api.expect("Все попытки Playwright сохранены", len(rows), 8)
    retried = [row for row in rows if "retry succeeds" in row["name"]]
    run.api.expect("Retry сохранён двумя отдельными попытками", {(r["attempt"], r["status"]) for r in retried}, {(1, "failed"), (2, "passed")})
    run.api.expect("Stable testId один на обе попытки", len({r["testId"] for r in retried}), 1)
    run.api.expect("У каждой попытки свой UUID", len({r["id"] for r in retried}), 2)
    skipped = [row for row in rows if row["status"] == "skipped"]
    run.api.expect("Playwright skipped сохранён без запросов", [(r["status"], r["requestCount"]) for r in skipped], [("skipped", 0)])
    failed = [row for row in rows if row["status"] == "failed"]
    run.api.expect("Ошибки assertions и teardown действительно сохранены", len(failed), 5)
    for row in rows:
        if row["status"] != "skipped":
            run.api.expect("Запрос приписан своей попытке, а не соседнему worker", (row["requestCount"], row["coveredFieldCount"]), (1, 3))
