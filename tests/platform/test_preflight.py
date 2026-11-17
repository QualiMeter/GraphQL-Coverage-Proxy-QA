import os
import subprocess
from types import SimpleNamespace

from coverage_qa import preflight


def test_preflight_reports_git_inspection_failure(tmp_path, monkeypatch):
    (tmp_path / ".gitignore").write_text("reports/\n", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(preflight, "ROOT", tmp_path)
    monkeypatch.setattr(preflight, "TEAM_BACKEND", tmp_path / "missing-backend")
    monkeypatch.setattr(preflight, "FRONTEND_DIR", tmp_path / "missing-frontend")
    passed, message = preflight.check_git_hygiene()
    assert not passed
    assert "Git inspection failed" in message


def test_preflight_finds_committed_archives(tmp_path, monkeypatch):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text("reports/\n", encoding="utf-8")
    (tmp_path / "archive.zip").write_bytes(b"fixture")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.test",
                    "-c", "commit.gpgsign=false", "commit", "-qm", "fixture"], cwd=tmp_path, check=True)
    for attribute in ("ROOT", "TEAM_BACKEND", "FRONTEND_DIR"):
        monkeypatch.setattr(preflight, attribute, tmp_path)
    passed, message = preflight.check_git_hygiene()
    assert not passed
    assert "archive.zip" in message


def test_preflight_passes_npm_arguments_on_posix(tmp_path, monkeypatch):
    monkeypatch.setattr(preflight, "FRONTEND_DIR", tmp_path)
    monkeypatch.setattr(preflight, "os", SimpleNamespace(name="posix", environ=os.environ))
    calls = []

    def run(command, **options):
        calls.append((command, options["shell"]))
        return SimpleNamespace(returncode=0, stdout="passed", stderr="")

    monkeypatch.setattr(preflight.subprocess, "run", run)
    assert preflight.check_frontend_lint()[0]
    assert preflight.check_frontend_tests()[0]
    assert calls == [([preflight.NPM, "run", "lint"], False), ([preflight.NPM, "test"], False)]
