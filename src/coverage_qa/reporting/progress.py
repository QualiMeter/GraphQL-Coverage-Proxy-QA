import hashlib
import threading
import time

from coverage_qa.reporting.history import save_json


_config = None
_lock = threading.Lock()
_last_emit = 0.0


def bind(config):
    global _config, _last_emit
    _config = config
    _last_emit = 0.0


def evidence_id(node_id):
    return hashlib.sha256(node_id.encode()).hexdigest()[:16]


def emit(*, force=False):
    global _last_emit
    config = _config
    if config is None or config.run_folder is None:
        return
    with _lock:
        now = time.monotonic()
        if not force and now - _last_emit < 0.25:
            return
        _last_emit = now
        report = config.api_report
        live = config.run_folder / "live"
        live.mkdir(exist_ok=True)
        completed = {t["id"]: t for t in report["tests"]}
        tests = []
        requests = checks = failures = 0
        for entry in report["tests"]:
            events = [dict(e) for e in entry["events"]]
            requests += sum(e["kind"] == "request" for e in events)
            checks += sum(e["kind"] == "check" for e in events)
            failures += sum(e["kind"] == "check" and not e["passed"] for e in events)
            try:
                save_json(live / (evidence_id(entry["id"]) + ".json"), {**entry, "events": events})
            except OSError:
                # Live telemetry must not abort functional tests. The complete report is saved separately.
                warning = "Live-снимок временно недоступен; полные доказательства сохраняются в results.json."
                if warning not in report.setdefault("liveWarnings", []):
                    report["liveWarnings"].append(warning)
        for planned in report.get("plannedTests", []):
            entry = completed.get(planned["id"], {})
            tests.append({**planned, "status": entry.get("status", "pending"),
                          "duration": entry.get("duration", 0), "evidenceId": evidence_id(planned["id"])})
        snapshot = {
            "id": report["id"], "activeTest": getattr(config, "active_test", None),
            "tests": tests, "requests": requests, "checks": checks, "failedChecks": failures,
            "passed": sum(t["status"] == "passed" for t in tests),
            "failed": sum(t["status"] == "failed" for t in tests),
            "skipped": sum(t["status"] == "skipped" for t in tests),
            "exitCode": report["exitCode"], "updatedAt": time.time(),
        }
        try:
            save_json(config.run_folder / "progress.json", snapshot)
        except OSError:
            warning = "Обновление live-прогресса задержано файловой блокировкой."
            if warning not in report.setdefault("liveWarnings", []):
                report["liveWarnings"].append(warning)
