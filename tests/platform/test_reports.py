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
