"""HTTP evidence and a standalone report. No imports from the API implementation."""

import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from report import write_report
from history import new_run, save_json, update_history
from progress import bind, emit
from test_catalog import group_for

_current_config = None


def pytest_addoption(parser):
    parser.addoption("--api-url", default="http://127.0.0.1:9000/graphql")
    parser.addoption("--report-dir", default="reports")
    parser.addoption("--run-label", default="Проверка API")
    parser.addoption("--run-folder", help="Existing unique run folder created by the launcher")
    parser.addoption("--managed-coverage", action="store_true")
    parser.addoption("--coverage-backend-dir", default=None)
    parser.addoption("--coverage-url", default=None)
    parser.addoption("--upstream-url", default=None)
    parser.addoption("--live-base-url", default=None)
    parser.addoption("--selection-file", default=None)
    parser.addoption("--catalog-file", default=None)


def pytest_configure(config):
    global _current_config
    _current_config = config
    existing = config.getoption("--run-folder")
    config.run_folder = (Path(existing).resolve() if existing else new_run(config.getoption("--report-dir"), config.getoption("--run-label"), "pytest")) if not config.option.collectonly else None
    config.api_report = {
        "id": config.run_folder.name if config.run_folder else "collection-only",
        "startedAt": datetime.now(timezone.utc).isoformat(),
        "endpoint": config.getoption("--live-base-url") or config.getoption("--api-url"),
        "label": config.getoption("--run-label"),
        "tests": [],
        "exitCode": None,
        "coverageRuns": [],
        "infrastructureErrors": [],
    }
    bind(config)


def pytest_internalerror(excrepr, excinfo):
    if _current_config is not None:
        _current_config.api_report["infrastructureErrors"].append(str(excrepr))


def describe(item):
    marker = item.get_closest_marker("case")
    return {"id": item.nodeid, "title": marker.args[0] if marker else item.name,
            "variant": item.callspec.id if hasattr(item, "callspec") else "",
            "group": group_for(item.path)}


def pytest_collection_modifyitems(config, items):
    selection = config.getoption("--selection-file")
    if selection:
        chosen = set(json.loads(Path(selection).read_text(encoding="utf-8")))
        available = {item.nodeid for item in items}
        unknown = chosen - available
        if unknown:
            raise pytest.UsageError("Unknown selected tests: " + ", ".join(sorted(unknown)))
        kept = [item for item in items if item.nodeid in chosen]
        discarded = [item for item in items if item.nodeid not in chosen]
        items[:] = kept
        config.hook.pytest_deselected(items=discarded)
    config.api_report["plannedTests"] = [describe(item) for item in items]


def pytest_collection_finish(session):
    catalog = session.config.getoption("--catalog-file")
    if catalog:
        save_json(Path(catalog), [describe(item) for item in session.items])
    emit(force=True)


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item):
    folder = item.config.run_folder
    if folder and (folder / "cancel.requested").exists():
        pytest.exit("Cancelled from the test control panel", returncode=2)
    if not hasattr(item, "api_entry"):
        info = describe(item)
        item.api_entry = {**info, "status": "running", "duration": 0, "events": [], "failure": None}
        item.config.api_report["tests"].append(item.api_entry)
    item.config.active_test = item.nodeid
    emit(force=True)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    result = outcome.get_result()
    entry = getattr(item, "api_entry", None)
    if entry is None:
        marker = item.get_closest_marker("case")
        entry = {"id": item.nodeid, "title": marker.args[0] if marker else item.name,
                 "status": "passed", "duration": 0, "events": [], "failure": None}
        item.api_entry = entry
        item.config.api_report["tests"].append(entry)
    entry["duration"] += result.duration
    if result.failed:
        entry["status"] = "failed"
        entry["failure"] = result.longreprtext
    elif result.skipped and entry["status"] != "failed":
        entry["status"] = "skipped"
        entry["failure"] = str(result.longrepr)
    elif result.when == "call" and entry["status"] == "running":
        entry["status"] = "passed"
    emit(force=True)


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session, exitstatus):
    if session.config.option.collectonly:
        return
    report = session.config.api_report
    cancelled = (session.config.run_folder / "cancel.requested").exists()
    report["cancelled"] = cancelled
    report["exitCode"] = 2 if cancelled else int(exitstatus)
    if cancelled:
        session.exitstatus = 2
    report["finishedAt"] = datetime.now(timezone.utc).isoformat()
    folder = session.config.run_folder
    folder.mkdir(parents=True, exist_ok=True)
    save_json(folder / "results.json", report)
    save_json(folder / "field-usage.json", {"testRunId": report["id"], "runs": report["coverageRuns"]})
    write_report(report, folder / "index.html")
    update_history(folder, report)
    session.config.active_test = None
    emit(force=True)
    print(f"\nAPI report: {folder / 'index.html'}")


class API:
    def __init__(self, url, entry, key=None):
        self.url = url
        self.entry = entry
        self.client = httpx.Client(timeout=45, trust_env=False)
        self.key = key

    def raw(self, title, *, payload=None, content=None, headers=None):
        return self.http(title, "POST", self.url, payload=payload, content=content, headers=headers)

    def http(self, title, method, url, *, payload=None, content=None, headers=None, auth=True):
        outgoing = {**({"X-Coverage-Api-Key": self.key} if self.key and auth else {}), **(headers or {})}
        event = {"kind": "request", "title": title,
                 "request": redact(payload) if content is None else content,
                 "method": method, "url": url,
                 "headers": {k: "<redacted>" if any(x in k.lower() for x in ("key", "authorization", "cookie", "csrf")) else v for k, v in outgoing.items()},
                 "httpStatus": None, "response": None, "error": None}
        self.entry["events"].append(event)
        emit()
        start = time.perf_counter()
        try:
            kwargs = ({"json": payload} if payload is not None else {}) if content is None else {
                "content": content, "headers": {"Content-Type": "application/json"},
            }
            if content is not None:
                outgoing = {"Content-Type": "application/json", **outgoing}
                kwargs.pop("headers", None)
            response = self.client.request(method, url, headers=outgoing, **kwargs)
            event["httpStatus"] = response.status_code
            try:
                body = response.json()
            except ValueError:
                body = response.text
            event["response"] = redact(body)
            event["responseHeaders"] = {k: v for k, v in response.headers.items() if k.lower() in {"content-type", "content-encoding", "cache-control"}}
            return response.status_code, body
        except httpx.HTTPError as exc:
            event["error"] = str(exc)
            raise
        finally:
            event["durationMs"] = round((time.perf_counter() - start) * 1000, 2)
            emit()

    def gql(self, title, query, variables=None, operation=None, *, errors=False):
        status, body = self.raw(title, payload={"query": query, "variables": variables,
                                               "operationName": operation})
        self.expect("HTTP-статус GraphQL-запроса", status, 200)
        self.expect("Ответ — JSON-объект", isinstance(body, dict), True)
        if not errors:
            self.expect("GraphQL errors отсутствуют", body.get("errors", []), [])
        return body

    def expect(self, title, actual, expected):
        passed = actual == expected
        self.entry["events"].append({"kind": "check", "title": title,
                                      "actual": redact(actual), "expected": redact(expected), "passed": passed})
        emit(force=not passed)
        assert passed, f"{title}: expected={expected!r}, actual={actual!r}"


INLINE_SECRET_RE = re.compile(
    r'(?i)\b(password|token|accesstoken|refreshtoken|secret|apikey)\s*:\s*("(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\')'
)


def redact(value):
    if isinstance(value, str):
        return INLINE_SECRET_RE.sub(r'\1: "<redacted>"', value)
    if isinstance(value, dict):
        return {k: "<redacted>" if k.lower() in {"password", "token", "accesstoken", "refreshtoken", "secret", "apikey"} else redact(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    return value


def ensure_entry(request):
    if not hasattr(request.node, "api_entry"):
        marker = request.node.get_closest_marker("case")
        request.node.api_entry = {"id": request.node.nodeid, "title": marker.args[0] if marker else request.node.name,
                                  "status": "passed", "duration": 0, "events": [], "failure": None}
        request.config.api_report["tests"].append(request.node.api_entry)
    return request.node.api_entry


@pytest.fixture
def api(request):
    if not hasattr(request.node, "api_entry"):
        marker = request.node.get_closest_marker("case")
        request.node.api_entry = {
            "id": request.node.nodeid,
            "title": marker.args[0] if marker else request.node.name,
            "status": "passed", "duration": 0, "events": [], "failure": None,
        }
        request.config.api_report["tests"].append(request.node.api_entry)
    client = API(request.config.getoption("--api-url"), request.node.api_entry)
    yield client
    client.client.close()


@pytest.fixture(scope="session")
def coverage_stack(request):
    from environment import CoverageStack, TEAM_BACKEND
    from types import SimpleNamespace
    config = request.config
    if config.getoption("--managed-coverage"):
        stack = CoverageStack(config.run_folder, config.getoption("--coverage-backend-dir") or TEAM_BACKEND)
        try:
            stack.start()
            config.api_report["endpoint"] = stack.proxy.base + "/graphql"
            yield stack
        finally:
            errors = stack.close()
            config.api_report["infrastructureErrors"].extend(errors)
            if errors:
                pytest.fail("; ".join(errors))
    else:
        base, upstream = config.getoption("--coverage-url"), config.getoption("--upstream-url")
        if not base or not upstream or not os.environ.get("COVERAGE_API_KEY"):
            pytest.skip("Coverage tests require --managed-coverage or --coverage-url + --upstream-url + COVERAGE_API_KEY")
        config.api_report["endpoint"] = base.rstrip("/") + "/graphql"
        yield SimpleNamespace(proxy=SimpleNamespace(base=base.rstrip("/")), upstream=SimpleNamespace(base=upstream.rstrip("/")), key=os.environ["COVERAGE_API_KEY"], managed=False)


@pytest.fixture
def coverage_run(request, coverage_stack):
    from coverage_client import CoverageRun
    if not hasattr(request.node, "api_entry"):
        marker = request.node.get_closest_marker("case")
        request.node.api_entry = {"id": request.node.nodeid, "title": marker.args[0] if marker else request.node.name,
                                  "status": "passed", "duration": 0, "events": [], "failure": None}
        request.config.api_report["tests"].append(request.node.api_entry)
    api = API(coverage_stack.proxy.base + "/graphql", request.node.api_entry, coverage_stack.key)
    run = CoverageRun(api, coverage_stack, request.config.api_report)
    try:
        run.prepare(request.node.nodeid)
        yield run
    finally:
        try:
            if run.run_id:
                run.finish()
                run.snapshot("Итог прогона")
        finally:
            api.client.close()


@pytest.fixture(scope="session")
def business_stack(request):
    from environment import CoverageStack, TEAM_BACKEND
    folder = request.config.run_folder / "business-infrastructure"
    folder.mkdir(exist_ok=True)
    stack = CoverageStack(folder, request.config.getoption("--coverage-backend-dir") or TEAM_BACKEND, commerce=True)
    try:
        stack.start()
        request.config.api_report["endpoint"] = f"{stack.upstream.base}/graphql (business direct); {stack.proxy.base}/graphql (business proxy)"
        yield stack
    finally:
        errors = stack.close()
        request.config.api_report["infrastructureErrors"].extend(errors)
        if errors:
            pytest.fail("; ".join(errors))


@pytest.fixture(params=["direct", "proxy"])
def business(request, business_stack):
    from business_client import BusinessClient
    entry = ensure_entry(request)
    api = API(business_stack.proxy.base + "/graphql", entry, business_stack.key)
    client = BusinessClient(api, business_stack, request.config.api_report, request.param)
    try:
        client.prepare(request.node.nodeid)
        yield client
    finally:
        try:
            client.cleanup()
        finally:
            api.client.close()


@pytest.fixture
def live_api(request):
    base = request.config.getoption("--live-base-url")
    if not base:
        pytest.skip("Use --live-base-url to enable GET-only deployment checks")
    client = API(base.rstrip("/"), ensure_entry(request))
    yield client
    client.client.close()
