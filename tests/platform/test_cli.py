import json
import subprocess
import sys

import pytest

from coverage_qa.catalog import discover, group_for
from coverage_qa.environment import ROOT


def run_cli(tmp_path, *args):
    return subprocess.run([sys.executable, "-m", "coverage_qa", "test", *args], cwd=tmp_path,
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=45)


@pytest.mark.case("CLI: выбор одного сценария работает из другого текущего каталога")
def test_node_selection_from_other_directory(tmp_path):
    node = "tests/platform/test_reports.py::test_credentials_are_redacted_without_altering_runtime_response"
    reports = tmp_path / "reports"
    result = run_cli(tmp_path, "--report-dir", str(reports), "--", node)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(next(reports.glob("runs/*/results.json")).read_text(encoding="utf-8"))
    assert [t["id"] for t in report["tests"]] == [node]


@pytest.mark.case("CLI: сбор списка тестов не создаёт историю выполнения")
def test_collection_does_not_create_run(tmp_path):
    reports = tmp_path / "reports"
    result = run_cli(tmp_path, "--report-dir", str(reports), "--", "tests/api/coverage", "--collect-only", "-q")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "test_empty_and_schema_deduplication" in result.stdout
    assert not reports.exists()


@pytest.mark.case("CLI: live-папка требует явного адреса сервера")
def test_live_path_requires_explicit_address(tmp_path):
    result = run_cli(tmp_path, "--", "tests/live")
    assert result.returncode == 2
    assert "--live-base-url" in result.stderr


@pytest.mark.case("Каталог: новые имена тестов группируются по папке, live не входит в обычный набор")
def test_discovery_uses_directory_not_filename(tmp_path):
    folder = tmp_path / "tests"
    for relative in ["api/coverage/test_anything.py", "e2e/test_anything.py", "live/test_anything.py"]:
        path = folder / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    selected = discover(folder, "all")
    assert len(selected) == 2
    assert {group_for(p) for p in selected} == {"coverage", "e2e"}
