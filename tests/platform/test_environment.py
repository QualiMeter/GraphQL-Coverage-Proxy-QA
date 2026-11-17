import subprocess

import pytest

from coverage_qa.environment import CoverageStack, find_backend, source_identity


def test_source_copy_does_not_inherit_parent_repository_commit(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "tracked").write_text("fixture", encoding="utf-8")
    subprocess.run(["git", "add", "tracked"], cwd=tmp_path, check=True)
    subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.test",
                    "-c", "commit.gpgsign=false", "commit", "-qm", "fixture"], cwd=tmp_path, check=True)
    nested = tmp_path / "backend-copy"
    nested.mkdir()
    assert source_identity(tmp_path)["commit"] is not None
    assert source_identity(nested)["commit"] is None


@pytest.mark.case("Установка: backend находится в соседнем Git checkout")
@pytest.mark.parametrize("name", ["GraphQL-Coverage-Proxy-Beckend", "coverage-backend"])
def test_find_sibling_backend(tmp_path, monkeypatch, name):
    monkeypatch.delenv("COVERAGE_BACKEND_DIR", raising=False)
    backend = tmp_path / name
    backend.mkdir()
    (backend / "alembic.ini").touch()
    assert find_backend(tmp_path) == backend


@pytest.mark.case("Установка: путь backend переопределяется настройкой")
def test_backend_environment_override(tmp_path, monkeypatch):
    monkeypatch.setenv("COVERAGE_BACKEND_DIR", str(tmp_path))
    assert find_backend() == tmp_path.resolve()


@pytest.mark.case("Установка: отсутствующий backend обнаруживается до запуска Docker")
def test_missing_backend_is_actionable(tmp_path):
    stack = CoverageStack(tmp_path, tmp_path / "missing")
    with pytest.raises(RuntimeError, match="COVERAGE_BACKEND_DIR"):
        stack.start()
    assert stack.compose_started is False
