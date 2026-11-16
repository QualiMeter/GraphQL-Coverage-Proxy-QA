import argparse
import html
import json
import os
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


def get_dir_size(path: Path) -> int:
    total = 0
    try:
        for entry in os.scandir(path):
            if entry.is_file(follow_symlinks=False):
                total += entry.stat().st_size
            elif entry.is_dir(follow_symlinks=False):
                total += get_dir_size(Path(entry.path))
    except (PermissionError, FileNotFoundError):
        pass
    return total


def rebuild_history_index(root: Path):
    root = Path(root).resolve()
    rows = []
    for path in sorted((root / "runs").glob("*/manifest.json"), reverse=True):
        try:
            m = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        target = f"runs/{path.parent.name}/index.html"
        rows.append(
            f'<tr><td><a href="{target}">{html.escape(m.get("localTime", m["id"]))}</a></td>'
            f'<td>{html.escape(m.get("label", ""))}</td><td>{html.escape(m.get("suite", ""))}</td>'
            f'<td>{html.escape(str(m.get("status", "")))}</td>'
            f'<td>{m.get("passed", 0)} / {m.get("failed", 0)} / {m.get("skipped", 0)}</td></tr>'
        )
    source = (
        '<!doctype html><html lang="ru"><meta charset="utf-8"><title>История API-тестов</title>'
        '<style>body{font:15px system-ui;max-width:1200px;margin:40px auto;padding:20px;background:#f3f5f8;color:#203048}'
        'table{width:100%;border-collapse:collapse;background:white}td,th{padding:14px;text-align:left;border-bottom:1px solid #ddd}'
        'a{color:#315bdb}</style><h1>История API-тестов</h1><p>Время — Екатеринбург (UTC+5). '
        'Результаты: пройдено / ошибок / пропущено.</p><table><tr><th>Запуск</th><th>Название</th>'
        '<th>Набор</th><th>Статус</th><th>Результаты</th></tr>'
        + "".join(rows)
        + "</table></html>"
    )
    (root / "index.html").write_text(source, encoding="utf-8")


def clean_runs(
    report_dir: Path | str = "reports",
    keep: int = 10,
    max_age_days: float | None = None,
    dry_run: bool = False,
) -> dict:
    from coverage_qa.environment import ROOT
    root = Path(report_dir).resolve()
    if report_dir == "reports" and not (Path.cwd() / "src" / "coverage_qa").exists() and (ROOT / "reports" / "runs").exists():
        root = (ROOT / "reports").resolve()
    runs_dir = root / "runs"
    if not runs_dir.exists():
        return {
            "found": 0,
            "retained": 0,
            "deleted": 0,
            "freed_bytes": 0,
            "deleted_runs": [],
            "retained_runs": [],
        }

    # Find latest run ID to protect
    latest_run_id = None
    latest_file = root / "latest.json"
    if latest_file.exists():
        try:
            latest_data = json.loads(latest_file.read_text(encoding="utf-8"))
            latest_run_id = latest_data.get("id")
        except Exception:
            pass

    candidates = []
    now = datetime.now(timezone.utc)

    for run_path in sorted(runs_dir.iterdir()):
        if not run_path.is_dir():
            continue
        manifest_file = run_path / "manifest.json"
        manifest = {}
        if manifest_file.exists():
            try:
                manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
            except Exception:
                pass

        # Protect currently active or running sessions
        if manifest.get("status") in {"starting", "running"}:
            continue
        if (run_path / "cancel.requested").exists() and not manifest.get("finishedAt"):
            continue

        started_dt = None
        started_str = manifest.get("startedAt")
        if started_str:
            try:
                started_dt = datetime.fromisoformat(started_str)
            except ValueError:
                pass
        if started_dt is None:
            started_dt = datetime.fromtimestamp(run_path.stat().st_mtime, tz=timezone.utc)

        size = get_dir_size(run_path)
        candidates.append({
            "path": run_path,
            "id": run_path.name,
            "started_dt": started_dt,
            "size": size,
            "is_latest": (run_path.name == latest_run_id),
        })

    # Sort descending by start time (newest first)
    candidates.sort(key=lambda c: c["started_dt"], reverse=True)

    retained = []
    to_delete = []

    for index, c in enumerate(candidates):
        # Keep latest designated run always
        if c["is_latest"] or index < keep:
            retained.append(c)
            continue

        # If max_age_days given, check age
        if max_age_days is not None:
            age_days = (now - c["started_dt"]).total_seconds() / 86400.0
            if age_days < max_age_days:
                retained.append(c)
                continue

        to_delete.append(c)

    freed_bytes = sum(c["size"] for c in to_delete)
    deleted_ids = []

    for c in to_delete:
        deleted_ids.append(c["id"])
        if not dry_run:
            shutil.rmtree(c["path"], ignore_errors=True)

    if not dry_run and deleted_ids:
        rebuild_history_index(root)

    return {
        "found": len(candidates),
        "retained": len(retained),
        "deleted": len(deleted_ids),
        "freed_bytes": freed_bytes,
        "deleted_runs": deleted_ids,
        "retained_runs": [c["id"] for c in retained],
    }


def main():
    parser = argparse.ArgumentParser(description="Clean up old test run artifacts while preserving history and latest reports.")
    parser.add_argument("--report-dir", default="reports", help="Root report directory (default: reports)")
    parser.add_argument("--keep", type=int, default=10, help="Number of most recent runs to keep (default: 10)")
    parser.add_argument("--max-age-days", type=float, default=None, help="Delete runs older than N days")
    parser.add_argument("--dry-run", action="store_true", help="Simulate cleanup without deleting files")
    parser.add_argument("-q", "--quiet", action="store_true", help="Minimal output")

    args = parser.parse_args()

    result = clean_runs(
        report_dir=args.report_dir,
        keep=args.keep,
        max_age_days=args.max_age_days,
        dry_run=args.dry_run,
    )

    freed_mb = result["freed_bytes"] / (1024 * 1024)

    if not args.quiet:
        prefix = "[DRY-RUN] " if args.dry_run else ""
        print(f"{prefix}Artifacts cleanup finished:")
        print(f"  • Total runs inspected: {result['found']}")
        print(f"  • Runs retained: {result['retained']} (target: keep {args.keep})")
        print(f"  • Runs deleted:  {result['deleted']}")
        print(f"  • Space freed:   {freed_mb:.2f} MB")
        if result["deleted_runs"]:
            print(f"  • Purged: {', '.join(result['deleted_runs'][:5])}{' ...' if len(result['deleted_runs']) > 5 else ''}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
