import html
import json
import shutil
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4


LOCAL_TZ = timezone(timedelta(hours=5))


def save_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    # Windows can temporarily deny replacing a destination being read by the panel.
    for attempt in range(12):
        try:
            temporary.replace(path)
            break
        except PermissionError:
            if attempt == 11:
                raise
            time.sleep(min(0.005 * (2 ** attempt), 0.05))


def new_run(root, label, suite):
    root = Path(root).resolve()
    now = datetime.now(timezone.utc)
    run_id = f"{now:%Y%m%dT%H%M%S}-{uuid4().hex[:10]}"
    folder = root / "runs" / run_id
    folder.mkdir(parents=True, exist_ok=False)
    manifest = {"id": run_id, "label": label, "suite": suite, "status": "starting",
                "startedAt": now.isoformat(), "localTime": now.astimezone(LOCAL_TZ).isoformat(),
                "exitCode": None}
    save_json(folder / "manifest.json", manifest)
    return folder


def update_history(folder, report):
    folder = Path(folder)
    manifest_path = folder / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {"id": folder.name}
    manifest.update({"status": "cancelled" if report.get("cancelled") else "passed" if report["exitCode"] == 0 else "failed",
                     "exitCode": report["exitCode"], "finishedAt": report.get("finishedAt"),
                     "passed": sum(t["status"] == "passed" for t in report["tests"]),
                     "failed": sum(t["status"] == "failed" for t in report["tests"]),
                     "skipped": sum(t["status"] == "skipped" for t in report["tests"]),
                     "coverageRuns": report.get("coverageRuns", []), "backend": report.get("backend")})
    save_json(manifest_path, manifest)
    root = folder.parent.parent
    from coverage_qa.reporting.cleanup import rebuild_history_index
    rebuild_history_index(root)
    latest = root / "latest"
    latest.mkdir(exist_ok=True)
    # Preserve the pre-history implementation's report before replacing its entry page.
    if (latest / "results.json").exists() and not (root / "legacy-latest").exists():
        shutil.copytree(latest, root / "legacy-latest")
    target = f"../runs/{folder.name}/index.html"
    (latest / "index.html").write_text(f'<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="0;url={target}"><a href="{target}">Последний отчёт</a>', encoding="utf-8")
    save_json(root / "latest.json", {"id": folder.name, "path": str(folder), "exitCode": report["exitCode"]})
