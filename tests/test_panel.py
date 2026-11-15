"""Control commands, saved evidence, and real subprocess execution/cancellation."""

import hashlib
import json
import subprocess
import sys
import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from control_panel import Manager, create_app
from history import new_run, save_json, update_history
from report import write_report


NODE = "tests/test_reporting.py::test_credentials_are_redacted_without_altering_runtime_response"
ROOT = Path(__file__).parents[1]


@pytest.fixture
def manager(tmp_path):
    manager = Manager(tmp_path / "reports")
    manager._catalog = [{"id": NODE, "title": "Секреты в отчёте", "group": "tooling", "variant": ""}]
    return manager


@pytest.mark.case("Панель: команды без локального токена и с чужого origin отклоняются")
def test_commands_require_control_token(manager):
    with TestClient(create_app(manager)) as client:
        body = {"node_ids": [NODE]}
        assert client.post("/api/runs", json=body).status_code == 403
        assert client.post("/api/runs", json=body, headers={"X-Panel-Token": "wrong"}).status_code == 403
        assert client.post("/api/runs", json=body, headers={"X-Panel-Token": manager.token, "Origin": "https://foreign.invalid"}).status_code == 403
    assert not list((manager.reports / "runs").glob("*"))


@pytest.mark.case("Панель: неизвестные тесты и произвольные пути запрещены")
def test_unknown_selection_and_paths_are_rejected(manager):
    with TestClient(create_app(manager)) as client:
        response = client.post("/api/runs", json={"node_ids": ["../../arbitrary.py"]}, headers={"X-Panel-Token": manager.token})
        assert response.status_code == 400
        assert client.get("/api/runs/bad!id").status_code == 400
        assert client.get("/api/runs/not-existing").status_code == 404


@pytest.mark.case("Панель: история и подробности берутся из сохранённых доказательств")
def test_saved_evidence_is_served(manager):
    folder = new_run(manager.reports, "Recorded run", "tooling")
    report = {"id": folder.name, "label": "Recorded run", "endpoint": "http://example.invalid", "startedAt": "2026-11-15T10:00:00Z",
              "exitCode": 0, "tests": [{"id": NODE, "title": "Сохранённый тест", "status": "passed", "duration": 1,
                                        "events": [{"kind": "check", "title": "Проверка", "actual": 2, "expected": 2, "passed": True}], "failure": None}]}
    save_json(folder / "results.json", report)
    write_report(report, folder / "index.html")
    update_history(folder, report)
    with TestClient(create_app(manager)) as client:
        snapshot = client.get(f"/api/runs/{folder.name}").json()
        assert snapshot["passed"] == 1
        key = snapshot["tests"][0]["evidenceId"]
        evidence = client.get(f"/api/runs/{folder.name}/tests/{key}").json()
        assert evidence["events"][0]["actual"] == 2
        assert client.get(snapshot["reportUrl"]).status_code == 200


@pytest.mark.case("Панель: остановка создаёт запрос отмены только для активного запуска")
def test_cancel_marks_only_active_run(manager):
    folder = new_run(manager.reports, "Active", "tooling")
    with TestClient(create_app(manager)) as client:
        headers = {"X-Panel-Token": manager.token}
        assert client.post(f"/api/runs/{folder.name}/cancel", headers=headers).status_code == 200
        assert (folder / "cancel.requested").exists()
        manifest = json.loads((folder / "manifest.json").read_text())
        manifest["status"] = "passed"
        save_json(folder / "manifest.json", manifest)
        assert client.post(f"/api/runs/{folder.name}/cancel", headers=headers).status_code == 409


@pytest.mark.case("Панель: выбранный сценарий действительно исполняется pytest и отдаёт live-результат")
def test_selected_test_is_executed(manager):
    id = manager.start([NODE], "Panel execution", False)
    assert manager.process.wait(timeout=40) == 0
    folder = manager.folder(id)
    result = json.loads((folder / "results.json").read_text(encoding="utf-8"))
    assert [t["id"] for t in result["tests"]] == [NODE]
    assert result["tests"][0]["status"] == "passed"
    assert len(result["plannedTests"]) == 1
    assert json.loads((folder / "progress.json").read_text())["passed"] == 1
    assert (folder / "live" / (hashlib.sha256(NODE.encode()).hexdigest()[:16] + ".json")).exists()


@pytest.mark.case("Панель: отменённый запуск не объявляется успешным и не выполняет следующий тест")
def test_cancellation_is_preserved_in_report(manager):
    folder = new_run(manager.reports, "Cancelled", "tooling")
    save_json(folder / "selection.json", [NODE])
    (folder / "cancel.requested").touch()
    result = subprocess.run([
        sys.executable, str(ROOT / "run_tests.py"), "--suite", "tooling", "--report-dir", str(manager.reports),
        "--run-folder", str(folder), "--selection-file", str(folder / "selection.json"),
    ], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=40)
    assert result.returncode == 2, result.stdout.decode(errors="replace")
    report = json.loads((folder / "results.json").read_text(encoding="utf-8"))
    assert report["cancelled"] is True
    assert report["exitCode"] == 2
    assert not report["tests"]
    assert json.loads((folder / "manifest.json").read_text())["status"] == "cancelled"


@pytest.mark.case("Панель: отмена долгого теста ждёт завершения и очистки, следующий тест не запускается")
def test_cancel_waits_for_current_test_and_teardown(manager, tmp_path, monkeypatch):
    marker = tmp_path / "entered"
    cleaned = tmp_path / "cleaned"
    plugin = tmp_path / "delay_review.py"
    plugin.write_text(
        "import time, pytest\nfrom pathlib import Path\n"
        "@pytest.hookimpl(tryfirst=True)\ndef pytest_runtest_call(item):\n"
        f"    Path({str(marker)!r}).touch()\n    time.sleep(16)\n"
        f"def pytest_sessionfinish(session):\n    Path({str(cleaned)!r}).touch()\n", encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path) + os.pathsep + os.environ.get("PYTHONPATH", ""))
    monkeypatch.setenv("PYTEST_PLUGINS", "delay_review")
    other = "tests/test_reporting.py::test_report_escapes_response_and_failure_text"
    manager._catalog.append({"id": other, "title": "Next test", "group": "tooling", "variant": ""})
    id = manager.start([NODE, other], "Long cancellation", False)
    try:
        deadline = time.monotonic() + 20
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert marker.exists()
        manager.cancel(id)
        time.sleep(13)
        assert manager.process.poll() is None, "Launcher must wait for pytest teardown"
        assert manager.process.wait(timeout=30) == 2
        assert cleaned.exists()
        result = json.loads((manager.folder(id) / "results.json").read_text(encoding="utf-8"))
        assert result["cancelled"] is True
        assert len(result["tests"]) == 1
    finally:
        manager.process.wait(timeout=40)


@pytest.mark.case("Панель: аварийный отчёт сохраняет запрошенную отмену")
def test_fallback_report_preserves_cancellation(manager):
    from types import SimpleNamespace
    folder = new_run(manager.reports, "Interrupted", "tooling")
    (folder / "cancel.requested").touch()
    with (folder / "panel.log").open("w") as log:
        manager._monitor(SimpleNamespace(wait=lambda: 1), folder, log)
    manifest = json.loads((folder / "manifest.json").read_text())
    assert manifest["status"] == "cancelled"
