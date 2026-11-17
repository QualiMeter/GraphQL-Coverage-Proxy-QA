import pytest

from coverage_qa.gitlab_ci import CoverageQualityGate
from coverage_qa.reporting.diff import compare_runs, extract_field_hits
from coverage_qa.reporting.html import final_measurement


def graph():
    return {
        "summary": {"totalFields": 2, "coveredFields": 1, "coveragePercent": 50.0},
        "types": [
            {"name": "Query", "kind": "OBJECT", "fields": [{"name": "node", "hits": 1}]},
            {"name": "Node", "kind": "INTERFACE", "fields": [{"name": "id", "hits": 1}]},
            {"name": "User", "kind": "OBJECT", "fields": [{"name": "id", "hits": 0}]},
        ],
    }


def test_gate_and_diff_agree_with_object_summary():
    source = graph()
    gate = CoverageQualityGate.evaluate_graph(source, "run", 50)
    diff = compare_runs(source, source)
    assert gate.passed
    assert gate.coverage_percent == diff["summary"]["afterPercent"] == 50
    assert gate.field_hits == {"Query.node": 1, "User.id": 0}
    assert final_measurement({"checkpoints": [{"graph": source}]}) == (gate.field_hits, 50.0)


def test_diff_uses_latest_checkpoint_before_legacy_flat_hits():
    source = {"finalFieldHits": {"Node.id": 1}, "checkpoints": [{"graph": graph()}]}
    assert extract_field_hits(source) == {"Query.node": 1, "User.id": 0}


def test_schema_changes_preserve_each_snapshot_percentage():
    before = {"fieldHits": {"Query.a": 1, "Query.b": 0}}
    after = {"fieldHits": {"Query.a": 1, "Query.c": 1, "Query.d": 0}}
    diff = compare_runs(before, after)
    assert diff["summary"]["beforePercent"] == 50
    assert diff["summary"]["afterPercent"] == 66.67
    assert diff["summary"]["beforeTotalFields"] == 2
    assert diff["summary"]["afterTotalFields"] == 3
    assert diff["addedFields"] == ["Query.c", "Query.d"]
    assert diff["removedFields"] == ["Query.b"]


@pytest.mark.parametrize("value", [-1, True, None, "1"])
def test_invalid_object_hits_are_rejected(value):
    source = graph()
    source["types"][0]["fields"][0]["hits"] = value
    with pytest.raises(ValueError, match="hit count"):
        CoverageQualityGate.evaluate_graph(source, "run", 50)
