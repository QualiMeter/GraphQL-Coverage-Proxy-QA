import pytest

from coverage_qa.environment import CoverageStack, find_backend


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
