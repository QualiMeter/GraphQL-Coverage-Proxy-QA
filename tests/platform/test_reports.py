"""Evidence/history invariants: reports must survive the next execution."""

import json
import pytest
import threading

from coverage_qa.reporting.history import new_run, save_json, update_history
from coverage_qa.reporting.html import write_report


def result(folder, label):
    return {"id": folder.name, "label": label, "endpoint": "http://test.invalid/graphql",
            "startedAt": "2026-11-15T10:00:00+00:00", "finishedAt": "2026-11-15T10:00:01+00:00",
            "exitCode": 0, "tests": [], "coverageRuns": []}


@pytest.mark.case("История: следующий запуск не перезаписывает предыдущий")
def test_previous_run_survives_next_execution(tmp_path):
    first = new_run(tmp_path, "first", "tooling")
    one = result(first, "first")
    save_json(first / "results.json", one)
    write_report(one, first / "index.html")
    update_history(first, one)
    original = {name: (first / name).read_bytes() for name in ("results.json", "index.html", "manifest.json")}
    second = new_run(tmp_path, "second", "tooling")
    two = result(second, "second")
    save_json(second / "results.json", two)
    write_report(two, second / "index.html")
    update_history(second, two)
    assert first != second
    for name, data in original.items():
        assert (first / name).read_bytes() == data
    assert json.loads((tmp_path / "latest.json").read_text())["id"] == second.name
    index = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert first.name in index and second.name in index
    # A Windows reader can hold a non-delete-sharing handle briefly during polling.
    shared = tmp_path / "shared.json"
    save_json(shared, {"version": 1})
    opened, release = threading.Event(), threading.Event()
    def reader():
        with shared.open(encoding="utf-8") as stream:
            stream.read()
            opened.set()
            release.wait(timeout=2)
    thread = threading.Thread(target=reader)
    thread.start()
    assert opened.wait(timeout=2)
    timer = threading.Timer(0.04, release.set)
    timer.start()
    try:
        save_json(shared, {"version": 2})
    finally:
        release.set()
        thread.join(timeout=2)
        timer.cancel()
    assert json.loads(shared.read_text()) == {"version": 2}


@pytest.mark.case("Отчёт: ответы API экранируются и не исполняются как HTML")
def test_report_escapes_response_and_failure_text(tmp_path):
    folder = new_run(tmp_path, "<untrusted>", "tooling")
    report = result(folder, "<untrusted>")
    report["tests"] = [{"id": "test", "title": "<script>alert('title')</script>", "status": "failed",
                        "duration": 1, "failure": "</pre><script>alert('failure')</script>",
                        "events": [{"kind": "request", "title": "HTTP", "request": {},
                                    "response": {"value": "</pre><script>alert('body')</script>"},
                                    "httpStatus": 200, "durationMs": 1, "error": None}]}]
    write_report(report, folder / "index.html")
    html = (folder / "index.html").read_text(encoding="utf-8")
    assert "<untrusted>" not in html
    assert "<script>alert(" not in html
    assert "&lt;script&gt;alert(" in html


@pytest.mark.case("Секреты: пароль и токен скрыты в отчёте, исходный ответ сохранён")
def test_credentials_are_redacted_without_altering_runtime_response():
    from tests.support.http_client import redact
    body = {"data": {"login": {"token": "sensitive-token", "user": {"id": "alice"}}},
            "variables": {"password": "sensitive-password"}}
    masked = redact(body)
    assert masked["data"]["login"]["token"] == "<redacted>"
    assert masked["variables"]["password"] == "<redacted>"
    assert masked["data"]["login"]["user"]["id"] == "alice"
    assert body["data"]["login"]["token"] == "sensitive-token"
    inline = {"query": 'mutation { login(email: "a@b.c", password: "inline-secret-pass") { token } }'}
    masked_inline = redact(inline)
    assert "inline-secret-pass" not in masked_inline["query"]
    assert '<redacted>' in masked_inline["query"]
    assert redact({"csrfToken": "csrf-secret"})["csrfToken"] == "<redacted>"


@pytest.mark.case("Экспорт: отчёт создаёт coverage.csv и test-matrix.csv рядом с HTML")
def test_csv_exports_created_with_report(tmp_path):
    folder = new_run(tmp_path, "csv-test", "tooling")
    report = result(folder, "csv-test")
    report["tests"] = [{
        "id": "tests/test_catalog.py::test_case",
        "title": "Тестовый сценарий каталога",
        "status": "passed",
        "duration": 0.123,
        "events": [],
        "failure": None,
    }]
    report["coverageRuns"] = [{
        "testId": "tests/test_catalog.py::test_case",
        "runId": "run-uuid-1",
        "name": "Основной",
        "finalFieldHits": {"Query.user": 2, "User.id": 2, "User.email": 0},
        "summary": {"totalFields": 3, "coveredFields": 2, "coveragePercent": 66.67},
    }]
    write_report(report, folder / "index.html")
    csv_file = folder / "coverage.csv"
    matrix_file = folder / "test-matrix.csv"
    assert csv_file.exists()
    assert matrix_file.exists()

    csv_content = csv_file.read_text(encoding="utf-8")
    assert "run_id,test_id,run_name,type_name,field_name,coordinate,hits,covered,coverage_percent" in csv_content
    assert "run-uuid-1,tests/test_catalog.py::test_case,Основной,Query,user,Query.user,2,True,66.67" in csv_content
    assert "run-uuid-1,tests/test_catalog.py::test_case,Основной,User,email,User.email,0,False,66.67" in csv_content

    matrix_content = matrix_file.read_text(encoding="utf-8")
    assert "test_id,test_title,status,duration_sec,run_id,covered_fields_count,covered_coordinates" in matrix_content
    assert "tests/test_catalog.py::test_case,Тестовый сценарий каталога,passed,0.123,run-uuid-1,2,Query.user; User.id" in matrix_content


@pytest.mark.case("Сравнение: diff между прогонами корректно вычисляет дельту полей и обращений")
def test_compare_runs_coverage_diff():
    from coverage_qa.reporting.diff import compare_runs, format_diff_markdown, format_diff_csv
    run_before = {
        "finalFieldHits": {"Query.user": 1, "User.id": 1, "User.email": 0, "Mutation.createOrder": 0},
        "summary": {"totalFields": 4, "coveredFields": 2, "coveragePercent": 50.0},
    }
    run_after = {
        "finalFieldHits": {"Query.user": 2, "User.id": 3, "User.email": 0, "Mutation.createOrder": 1},
        "summary": {"totalFields": 4, "coveredFields": 3, "coveragePercent": 75.0},
    }
    diff = compare_runs(run_before, run_after)
    assert diff["summary"]["totalFields"] == 4
    assert diff["summary"]["beforeCovered"] == 2
    assert diff["summary"]["afterCovered"] == 3
    assert diff["summary"]["deltaCovered"] == 1
    assert diff["summary"]["deltaPercent"] == 25.0
    assert diff["newlyCovered"] == ["Mutation.createOrder"]
    assert diff["hitsDeltas"]["Query.user"] == 1
    assert diff["hitsDeltas"]["User.id"] == 2
    assert diff["hitsDeltas"]["Mutation.createOrder"] == 1
    assert diff["hitsDeltas"]["User.email"] == 0

    md = format_diff_markdown(diff)
    assert "Сравнение покрытия (Coverage Diff)" in md
    assert "`Mutation.createOrder`" in md
    assert "Впервые покрыто" in md

    csv_data = format_diff_csv(diff)
    assert "Mutation.createOrder,0,1,1,newly_covered" in csv_data

