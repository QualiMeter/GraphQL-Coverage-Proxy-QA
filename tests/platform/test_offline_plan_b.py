import json
import socket

import pytest

from coverage_qa.offline_demo import bundle_offline_showcase, check_port
from tests.platform.test_coverage_metrics import graph


def write_report(folder, data):
    folder.mkdir(parents=True)
    (folder / "index.html").write_text("<!doctype html><p>Saved report</p>", encoding="utf-8")
    (folder / "coverage.csv").write_text("coordinate,hits\nQuery.node,1\n", encoding="utf-8")
    (folder / "coverage.json").write_text(json.dumps(data), encoding="utf-8")


@pytest.mark.case("План Б: метрики совпадают с выбранным сохранённым графом")
def test_offline_bundle_creation(tmp_path):
    source = tmp_path / "saved"
    data = graph()
    data.update({"runId": "real-run", "schemaId": "real-schema"})
    write_report(source, data)
    bundle = bundle_offline_showcase(tmp_path / "offline", source_dir=source)
    content = (bundle / "showcase.html").read_text(encoding="utf-8")
    assert "50.00%" in content
    assert "1 / 2" in content
    assert "real-run" in content
    assert "88.57%" not in content
    assert "Сведения о сценариях отсутствуют" in content
    assert (bundle / "report" / "coverage.csv").read_bytes() == (source / "coverage.csv").read_bytes()
    assert json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))["sourceId"] == "saved"


def test_empty_workspace_has_no_invented_measurements(tmp_path, monkeypatch):
    monkeypatch.setattr("coverage_qa.offline_demo.ROOT", tmp_path)
    bundle = bundle_offline_showcase(tmp_path / "empty")
    content = (bundle / "showcase.html").read_text(encoding="utf-8")
    assert "Нет сохранённого прогона" in content
    assert not (bundle / "report").exists()
    assert 'href="report/' not in content
    assert json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))["coverageRuns"] == []


def test_test_statuses_and_titles_come_from_source(tmp_path):
    source = tmp_path / "saved"
    write_report(source, {})
    data = {"tests": [{"title": "<script>bad</script>", "status": "failed"}],
            "coverageRuns": [{"checkpoints": [{"graph": graph()}]}]}
    (source / "results.json").write_text(json.dumps(data), encoding="utf-8")
    bundle = bundle_offline_showcase(tmp_path / "offline", source_dir=source)
    content = (bundle / "showcase.html").read_text(encoding="utf-8")
    assert "0 / 1" in content
    assert "failed" in content
    assert "&lt;script&gt;bad&lt;/script&gt;" in content


def test_source_cannot_be_replaced_by_output(tmp_path):
    source = tmp_path / "saved"
    write_report(source, graph())
    with pytest.raises(ValueError, match="separate"):
        bundle_offline_showcase(source, source_dir=source)


@pytest.mark.case("План Б: диагностика доступности локального порта")
def test_check_port_utility():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        assert check_port(port)
    assert not check_port(port)
