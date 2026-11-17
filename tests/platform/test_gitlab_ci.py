import json
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from coverage_qa.control_panel import Manager, create_app
from coverage_qa.environment import ROOT
from coverage_qa.gitlab_ci import (
    GITLAB_COVERAGE_REGEX,
    CoverageQualityGate,
    GateEvaluation,
    GitLabClient,
    main as gitlab_main,
    render_gitlab_ci_template,
)


@pytest.fixture
def sample_coverage_graph_pass():
    return {
        "schema_id": "00000000-0000-0000-0000-000000000001",
        "run_id": "11111111-1111-1111-1111-111111111111",
        "service_id": "22222222-2222-2222-2222-222222222222",
        "summary": {
            "total_fields": 10,
            "covered_fields": 9,
            "coverage_percent": 90.0,
        },
        "types": [
            {
                "name": "Query",
                "fields": [
                    {"name": "user", "hits": 5},
                    {"name": "products", "hits": 3},
                    {"name": "cart", "hits": 1},
                ],
            },
            {
                "name": "User",
                "fields": [
                    {"name": "id", "hits": 5},
                    {"name": "name", "hits": 5},
                    {"name": "email", "hits": 4},
                ],
            },
            {
                "name": "Product",
                "fields": [
                    {"name": "id", "hits": 3},
                    {"name": "title", "hits": 3},
                    {"name": "price", "hits": 3},
                    {"name": "sku", "hits": 0},
                ],
            },
        ],
    }


@pytest.fixture
def sample_coverage_graph_fail():
    return {
        "schema_id": "00000000-0000-0000-0000-000000000001",
        "run_id": "33333333-3333-3333-3333-333333333333",
        "summary": {
            "total_fields": 10,
            "covered_fields": 4,
            "coverage_percent": 40.0,
        },
        "types": [
            {
                "name": "Query",
                "fields": [
                    {"name": "user", "hits": 1},
                    {"name": "products", "hits": 0},
                    {"name": "cart", "hits": 0},
                ],
            },
            {
                "name": "User",
                "fields": [
                    {"name": "id", "hits": 1},
                    {"name": "name", "hits": 1},
                    {"name": "email", "hits": 1},
                ],
            },
            {
                "name": "Product",
                "fields": [
                    {"name": "id", "hits": 0},
                    {"name": "title", "hits": 0},
                    {"name": "price", "hits": 0},
                    {"name": "sku", "hits": 0},
                ],
            },
        ],
    }


@pytest.mark.case("GitLab CI: Quality Gate оценивает успешное покрытие выше порога")
def test_gate_evaluation_passing(sample_coverage_graph_pass):
    gate = CoverageQualityGate("http://mock-proxy")
    with patch.object(gate, "fetch_coverage", return_value=sample_coverage_graph_pass):
        result = gate.evaluate("11111111-1111-1111-1111-111111111111", min_coverage=80.0)

    assert result.passed is True
    assert result.coverage_percent == 90.0
    assert result.covered_fields == 9
    assert result.total_fields == 10
    assert result.uncovered_fields == ["Product.sku"]
    assert "Total coverage: 90.00%" == result.gitlab_metric_line


@pytest.mark.case("GitLab CI: Quality Gate отклоняет прогон с покрытием ниже порога")
def test_gate_evaluation_failing(sample_coverage_graph_fail):
    gate = CoverageQualityGate("http://mock-proxy")
    with patch.object(gate, "fetch_coverage", return_value=sample_coverage_graph_fail):
        result = gate.evaluate("33333333-3333-3333-3333-333333333333", min_coverage=80.0)

    assert result.passed is False
    assert result.coverage_percent == 40.0
    assert len(result.uncovered_fields) == 6
    assert "Product.title" in result.uncovered_fields
    assert "Total coverage: 40.00%" == result.gitlab_metric_line


@pytest.mark.case("GitLab CI: Генерация JUnit XML отчёта для вкладки тестов GitLab")
def test_junit_xml_report_generation(tmp_path, sample_coverage_graph_pass, sample_coverage_graph_fail):
    gate = CoverageQualityGate()

    with patch.object(gate, "fetch_coverage", return_value=sample_coverage_graph_pass):
        pass_res = gate.evaluate("run-pass", min_coverage=80.0)
    pass_xml = tmp_path / "pass_junit.xml"
    gate.generate_junit_xml(pass_res, pass_xml)
    assert pass_xml.exists()
    content_pass = pass_xml.read_text(encoding="utf-8")
    assert 'failures="0"' in content_pass
    assert "<failure" not in content_pass
    assert "test_field_coverage_threshold" in content_pass

    with patch.object(gate, "fetch_coverage", return_value=sample_coverage_graph_fail):
        fail_res = gate.evaluate("run-fail", min_coverage=80.0)
    fail_xml = tmp_path / "fail_junit.xml"
    gate.generate_junit_xml(fail_res, fail_xml)
    assert fail_xml.exists()
    content_fail = fail_xml.read_text(encoding="utf-8")
    assert 'failures="1"' in content_fail
    assert "<failure" in content_fail
    assert "Coverage 40.00% is below required threshold 80.00%" in content_fail


@pytest.mark.case("GitLab CI: Экспорт CSV и JSON артефактов")
def test_csv_and_summary_export(tmp_path, sample_coverage_graph_pass):
    gate = CoverageQualityGate()
    with patch.object(gate, "fetch_coverage", return_value=sample_coverage_graph_pass):
        res = gate.evaluate("run-pass", min_coverage=80.0)

    csv_path = tmp_path / "coverage.csv"
    gate.generate_csv(res, csv_path)
    assert csv_path.exists()
    csv_text = csv_path.read_text(encoding="utf-8")
    assert "type,field,hits,covered" in csv_text
    assert "Product,sku,0,no" in csv_text
    assert "Query,user,5,yes" in csv_text

    json_path = tmp_path / "summary.json"
    gate.generate_summary_json(res, json_path)
    assert json_path.exists()
    summary_data = json.loads(json_path.read_text(encoding="utf-8"))
    assert summary_data["coverage_percent"] == 90.0
    assert summary_data["passed"] is True


@pytest.mark.case("GitLab Client: Запуск пайплайна через Trigger Token")
def test_gitlab_trigger_pipeline_trigger_token():
    client = GitLabClient(host="https://gitlab.example.com", token="trigger-token-abc")

    mock_resp = MagicMock()
    mock_resp.status_code = 201
    mock_resp.json.return_value = {
        "id": 10101,
        "iid": 42,
        "status": "pending",
        "web_url": "https://gitlab.example.com/org/repo/-/pipelines/10101",
    }

    with patch("httpx.Client.post", return_value=mock_resp) as mock_post:
        result = client.trigger_pipeline(
            project_id="myorg/backend-tests",
            ref="feat/branch",
            variables={"COVERAGE_RUN_ID": "test-uuid-123"},
        )

    assert result["id"] == 10101
    assert result["iid"] == 42
    mock_post.assert_called_once()
    call_args = mock_post.call_args
    assert "org%2Frepo" in call_args[0][0] or "myorg%2Fbackend-tests" in call_args[0][0]
    assert call_args[1]["data"]["token"] == "trigger-token-abc"
    assert call_args[1]["data"]["variables[COVERAGE_RUN_ID]"] == "test-uuid-123"


@pytest.mark.case("GitLab Client: Запуск пайплайна через Personal Access Token (glpat-...)")
def test_gitlab_trigger_pipeline_pat():
    client = GitLabClient(host="https://gitlab.example.com", token="glpat-secret-token")

    mock_resp = MagicMock()
    mock_resp.status_code = 201
    mock_resp.json.return_value = {"id": 20202, "status": "created"}

    with patch("httpx.Client.post", return_value=mock_resp) as mock_post:
        result = client.trigger_pipeline(
            project_id=456,
            ref="main",
            variables={"MIN_COVERAGE": "85.0"},
        )

    assert result["id"] == 20202
    call_args = mock_post.call_args
    assert "/api/v4/projects/456/pipeline" in call_args[0][0]
    assert call_args[1]["headers"]["PRIVATE-TOKEN"] == "glpat-secret-token"
    assert call_args[1]["json"]["variables"] == [{"key": "MIN_COVERAGE", "value": "85.0"}]


@pytest.mark.case("GitLab Client: Опрос статуса пайплайна до завершения")
def test_gitlab_poll_pipeline_lifecycle():
    client = GitLabClient(host="https://gitlab.example.com", token="token")

    responses = [
        {"id": 55, "status": "pending", "duration": 0},
        {"id": 55, "status": "running", "duration": 10},
        {"id": 55, "status": "success", "duration": 25},
    ]

    with patch.object(client, "get_pipeline", side_effect=responses):
        observed_statuses = []
        final = client.poll_pipeline(
            project_id=12,
            pipeline_id=55,
            poll_interval=0.01,
            timeout=5.0,
            callback=lambda info: observed_statuses.append(info["status"]),
        )

    assert final["status"] == "success"
    assert observed_statuses == ["pending", "running", "success"]


@pytest.mark.case("CLI: coverage-qa gitlab template выводит корректный .gitlab-ci.yml")
def test_cli_gitlab_template(tmp_path, capsys):
    out_file = tmp_path / "generated-ci.yml"
    code = gitlab_main(["template", "--out", str(out_file)])
    assert code == 0
    assert out_file.exists()
    text = out_file.read_text(encoding="utf-8")
    assert "quality-gate" in text
    assert "gitlab gate" in text
    assert text == (ROOT / ".gitlab-ci.yml").read_text(encoding="utf-8")


@pytest.mark.case("CLI: coverage-qa gitlab gate возвращает 0 при успехе и 1 при падении")
def test_cli_gitlab_gate_exit_codes(capsys, sample_coverage_graph_pass, sample_coverage_graph_fail):
    with patch("coverage_qa.gitlab_ci.CoverageQualityGate.fetch_coverage", return_value=sample_coverage_graph_pass):
        code_pass = gitlab_main(["gate", "--run-id", "pass-id", "--min-coverage", "80"])
    out_pass = capsys.readouterr().out
    assert code_pass == 0
    assert "Total coverage: 90.00%" in out_pass
    assert "Status: PASSED" in out_pass

    with patch("coverage_qa.gitlab_ci.CoverageQualityGate.fetch_coverage", return_value=sample_coverage_graph_fail):
        code_fail = gitlab_main(["gate", "--run-id", "fail-id", "--min-coverage", "80"])
    out_fail = capsys.readouterr().out
    assert code_fail == 1
    assert "Total coverage: 40.00%" in out_fail
    assert "Status: FAILED" in out_fail


@pytest.mark.case("GitLab CI: Корневой .gitlab-ci.yml валиден и содержит все обязательные стадии")
def test_root_gitlab_ci_yaml_validity():
    gitlab_ci_file = ROOT / ".gitlab-ci.yml"
    assert gitlab_ci_file.is_file(), "Файл .gitlab-ci.yml должен существовать в корне репозитория"

    text = gitlab_ci_file.read_text(encoding="utf-8")
    assert "stages:" in text
    assert "preflight" in text
    assert "quality-gate" in text
    assert "graphql-coverage-gate:" in text
    assert "coverage:" in text

    match_regex = re.search(r"coverage:\s*['\"](/.*?/)['\"]", text)
    assert match_regex is not None, "В job graphql-coverage-gate должно быть указано поле coverage с регулярным выражением"
    regex_pattern = match_regex.group(1).strip("/")

    sample_line = "Total coverage: 85.50%"
    match = re.search(regex_pattern, sample_line)
    assert match is not None
    assert match.group(1) == "85.50"


@pytest.fixture
def panel_client(tmp_path):
    manager = Manager(tmp_path / "reports")
    manager._catalog = []
    app = create_app(manager)
    return TestClient(app), manager


@pytest.mark.case("Панель TestLab: POST /api/gitlab/trigger запускает пайплайн")
def test_panel_gitlab_trigger_api(panel_client):
    client, manager = panel_client
    mock_pipeline = {"id": 888, "iid": 1, "status": "created", "web_url": "https://gitlab.com/test"}

    with patch("coverage_qa.gitlab_ci.GitLabClient.trigger_pipeline", return_value=mock_pipeline):
        response = client.post(
            "/api/gitlab/trigger",
            headers={"X-Panel-Token": manager.token},
            json={
                "project_id": "123",
                "token": "secret-token",
                "ref": "main",
                "coverage_run_id": "test-run",
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "triggered"
    assert data["pipeline"]["id"] == 888


@pytest.mark.case("Панель TestLab: POST /api/gitlab/gate оценивает качество прогона")
def test_panel_gitlab_gate_api(panel_client, sample_coverage_graph_pass):
    client, manager = panel_client

    with patch("coverage_qa.gitlab_ci.CoverageQualityGate.fetch_coverage", return_value=sample_coverage_graph_pass):
        response = client.post(
            "/api/gitlab/gate",
            headers={"X-Panel-Token": manager.token},
            json={
                "run_id": "11111111-1111-1111-1111-111111111111",
                "min_coverage": 80.0,
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["passed"] is True
    assert data["coverage_percent"] == 90.0
    assert data["covered_fields"] == 9

def test_pipeline_artifact_gate(tmp_path, sample_coverage_graph_pass):
    graph = sample_coverage_graph_pass
    graph["runId"] = graph.pop("run_id")
    graph["schemaId"] = graph.pop("schema_id")
    graph["summary"] = {"totalFields": 10, "coveredFields": 9}
    artifact = tmp_path / "graph.json"
    artifact.write_text(json.dumps(graph), encoding="utf-8")
    summary = tmp_path / "summary.json"
    args = ["gate", "--graph-file", str(artifact), "--summary-out", str(summary)]
    with patch.object(CoverageQualityGate, "fetch_coverage", side_effect=AssertionError):
        assert gitlab_main([*args, "--min-coverage", "90"]) == 0
        actual = json.loads(summary.read_text(encoding="utf-8"))
        assert actual["run_id"] == graph["runId"]
        assert actual["schema_id"] == graph["schemaId"]
        assert gitlab_main([*args, "--min-coverage", "91"]) == 1
        graph["summary"]["coveredFields"] = 10
        artifact.write_text(json.dumps(graph), encoding="utf-8")
        assert gitlab_main(args) == 1


def test_panel_status_requires_header(panel_client):
    client, _ = panel_client
    path = "/api/gitlab/pipelines/123/456"
    assert client.get(path).status_code == 401
    with patch.object(GitLabClient, "get_pipeline", return_value={"status": "success"}) as get:
        assert client.get(path, headers={"X-GitLab-Token": "test-token"}).json()["status"] == "success"
    get.assert_called_once_with(project_id="123", pipeline_id=456, token="test-token")

def test_panel_uses_server_key_only_for_configured_proxy(panel_client, monkeypatch, sample_coverage_graph_pass):
    client, manager = panel_client
    monkeypatch.setenv("COVERAGE_URL", "https://team.example.test")
    monkeypatch.setenv("COVERAGE_API_KEY", "test-server-key")
    observed = []

    def fetch(gate, run_id):
        observed.append((gate.coverage_url, gate.api_key))
        return sample_coverage_graph_pass

    with patch.object(CoverageQualityGate, "fetch_coverage", fetch):
        for url in ("https://team.example.test", "https://other.example.test"):
            response = client.post("/api/gitlab/gate", headers={"X-Panel-Token": manager.token},
                                   json={"run_id": sample_coverage_graph_pass["run_id"], "coverage_url": url})
            assert response.status_code == 200
    assert observed == [("https://team.example.test", "test-server-key"), ("https://other.example.test", "")]
