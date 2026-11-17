import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tests.support.http_client import redact
from coverage_qa.reporting.cleanup import clean_runs, rebuild_history_index
from coverage_qa.security import run_security_audit, audit_repository_files, audit_reports
from coverage_qa.environment import ROOT


def test_security_audit_respects_explicit_workspace(tmp_path):
    key_header = "-----" + "BEGIN RSA PRIVATE KEY" + "-----"
    (tmp_path / "leaked.txt").write_text(key_header, encoding="utf-8")
    result = run_security_audit(tmp_path, check_reports=False)
    assert not result["passed"]
    assert result["repo_violations"] == 1
    assert result["violations"][0]["file"] == "leaked.txt"


def test_audit_reads_nested_request_and_response_secrets(tmp_path):
    folder = tmp_path / "runs" / "nested"
    folder.mkdir(parents=True)
    payload = {"tests": [{"events": [{"request": {"variables": {"password": "request-secret"}},
                                    "response": {"data": [{"token": "response-secret"}]}}]}]}
    (folder / "results.json").write_text(json.dumps(payload), encoding="utf-8")
    violations = audit_reports(tmp_path)
    assert len(violations) == 2
    assert "request-secret" not in str(violations)
    assert "response-secret" not in str(violations)


def test_cleanup_accepts_legacy_naive_timestamps(tmp_path):
    for name, timestamp in (("old", "2020-01-01T00:00:00"), ("new", "2026-01-01T00:00:00+00:00")):
        folder = tmp_path / "runs" / name
        folder.mkdir(parents=True)
        (folder / "manifest.json").write_text(json.dumps({"id": name, "status": "passed",
                                                       "startedAt": timestamp}), encoding="utf-8")
    result = clean_runs(tmp_path, keep=1, dry_run=True)
    assert result["deleted_runs"] == ["old"]
    assert (tmp_path / "runs" / "old").exists()


def test_cleanup_reports_removal_failure(tmp_path, monkeypatch):
    folder = tmp_path / "runs" / "old"
    folder.mkdir(parents=True)

    def fail(path):
        raise PermissionError("fixture")

    monkeypatch.setattr("coverage_qa.reporting.cleanup.shutil.rmtree", fail)
    with pytest.raises(PermissionError):
        clean_runs(tmp_path, keep=0)
    assert folder.exists()


@pytest.mark.case("Безопасность: redact маскирует все виды секретов в строках, заголовках и объектах")
def test_redact_masks_all_secrets():
    query = 'mutation Login { login(email: "alice@test.test", password: "SuperSecretPassword123!") { token } }'
    masked_query = redact(query)
    assert "SuperSecretPassword123!" not in masked_query
    assert 'password: "<redacted>"' in masked_query

    header_str = "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.secretpayload.sig"
    masked_header = redact(header_str)
    assert "secretpayload" not in masked_header
    assert "Bearer <redacted>" in masked_header

    payload = {
        "username": "alice",
        "PASSWORD": "raw-password",
        "Current_Password": "old-password",
        "New_Password": "new-password",
        "Token": "session-jwt-token",
        "API_KEY": "cov-live-secret-key",
        "nested": {
            "Authorization": "Bearer secret-bearer-token",
            "Cookie": "session=abc; csrf=123",
            "safe_field": 42,
            "idempotencyKey": "uuid-1234-safe",
        },
        "tokens_list": [
            {"token": "tok-1", "user": "bob"},
            {"token": "tok-2", "user": "charlie"},
        ],
    }

    cleaned = redact(payload)

    assert cleaned["PASSWORD"] == "<redacted>"
    assert cleaned["Current_Password"] == "<redacted>"
    assert cleaned["New_Password"] == "<redacted>"
    assert cleaned["Token"] == "<redacted>"
    assert cleaned["API_KEY"] == "<redacted>"
    assert cleaned["nested"]["Authorization"] == "<redacted>"
    assert cleaned["nested"]["Cookie"] == "<redacted>"
    assert cleaned["tokens_list"][0]["token"] == "<redacted>"
    assert cleaned["tokens_list"][1]["token"] == "<redacted>"

    assert cleaned["username"] == "alice"
    assert cleaned["nested"]["safe_field"] == 42
    assert cleaned["nested"]["idempotencyKey"] == "uuid-1234-safe"
    assert cleaned["tokens_list"][0]["user"] == "bob"


@pytest.mark.case("Безопасность: сканирование репозитория на утечки токенов и приватных ключей")
def test_security_audit_clean_and_detection(tmp_path):
    result = run_security_audit(ROOT, check_reports=True)
    assert result["passed"] is True
    assert len(result["violations"]) == 0

    fake_repo = tmp_path / "fake_repo"
    fake_repo.mkdir()
    leak_file = fake_repo / "leaked_credentials.txt"
    fake_key_header = "-----" + "BEGIN RSA PRIVATE KEY" + "-----"
    leak_file.write_text(f"{fake_key_header}\nMIIEowIBAAKCAQEA0...", encoding="utf-8")

    violations = audit_repository_files(fake_repo)
    assert len(violations) == 1
    assert violations[0]["rule"] == "Private key block detected"


@pytest.mark.case("Артефакты: очистка устаревших прогонов, сохранение последних N и пересборка индекса")
def test_artifacts_cleanup_lifecycle(tmp_path):
    report_dir = tmp_path / "reports"
    runs_dir = report_dir / "runs"
    runs_dir.mkdir(parents=True)

    run_ids = []
    for i in range(5):
        run_id = f"20261116T12000{i}-test{i}"
        run_ids.append(run_id)
        r_path = runs_dir / run_id
        r_path.mkdir()
        manifest = {
            "id": run_id,
            "label": f"Run {i}",
            "suite": "e2e",
            "status": "passed",
            "startedAt": f"2026-11-16T12:00:0{i}Z",
            "finishedAt": f"2026-11-16T12:01:0{i}Z",
            "passed": 10,
            "failed": 0,
            "skipped": 0,
        }
        (r_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        (r_path / "dummy.log").write_text("log contents" * 100, encoding="utf-8")

    latest_id = run_ids[-1]
    (report_dir / "latest.json").write_text(json.dumps({"id": latest_id}), encoding="utf-8")

    dry_res = clean_runs(report_dir, keep=2, dry_run=True)
    assert dry_res["found"] == 5
    assert dry_res["retained"] == 2
    assert dry_res["deleted"] == 3
    assert len(list(runs_dir.iterdir())) == 5

    res = clean_runs(report_dir, keep=2, dry_run=False)
    assert res["found"] == 5
    assert res["retained"] == 2
    assert res["deleted"] == 3
    assert res["freed_bytes"] > 0

    remaining_dirs = {p.name for p in runs_dir.iterdir()}
    assert len(remaining_dirs) == 2
    assert latest_id in remaining_dirs

    index_html = (report_dir / "index.html").read_text(encoding="utf-8")
    assert latest_id in index_html
    for deleted_id in res["deleted_runs"]:
        assert deleted_id not in index_html
