import socket
from pathlib import Path

import pytest

from coverage_qa.offline_demo import (
    bundle_offline_showcase,
    check_port,
    check_connectivity,
)
from coverage_qa.environment import ROOT


@pytest.mark.case("План Б: генерация автономного офлайн-пакета с метриками и CSV")
def test_offline_bundle_creation(tmp_path):
    output_dir = tmp_path / "offline_showcase"
    bundle_path = bundle_offline_showcase(output_dir)

    assert bundle_path.exists()
    assert (bundle_path / "showcase.html").is_file()

    content = (bundle_path / "showcase.html").read_text(encoding="utf-8")
    assert "GraphQL Coverage Proxy — План Б" in content
    assert "88.57%" in content
    assert "31 / 35" in content
    assert "10 / 10" in content
    assert "coverage.csv" in content


@pytest.mark.case("План Б: диагностика доступности локальных портов")
def test_check_port_utility():
    # Создаём временный слушающий сокет
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        port = s.getsockname()[1]
        assert check_port(port) is True

    # Проверяем заведомо свободный порт
    assert check_port(65432) is False


@pytest.mark.case("План Б: проверка целостности эталонного пакета офлайн-демонстрации")
def test_existing_offline_showcase_integrity():
    showcase_dir = ROOT / "reports" / "offline-showcase"
    assert showcase_dir.exists(), "Каталог reports/offline-showcase должен быть сформирован"
    assert (showcase_dir / "showcase.html").is_file()
    assert (showcase_dir / "coverage.csv").is_file()
    assert (showcase_dir / "index.html").is_file()

    html_text = (showcase_dir / "showcase.html").read_text(encoding="utf-8")
    assert "<!doctype html>" in html_text
    assert "</html>" in html_text
