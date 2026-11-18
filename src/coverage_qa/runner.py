import argparse
import json
import os
import subprocess
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

from coverage_qa.environment import ROOT, TEAM_BACKEND
from coverage_qa.reporting.history import new_run, save_json, update_history
from coverage_qa.reporting.html import write_report
from coverage_qa.catalog import discover, group_for


def select_paths(extra, parser, suite, live):
    selectors, forwarded = [], []
    skip_value = False
    for value in extra:
        if skip_value:
            forwarded.append(value)
            skip_value = False
        elif value in {"-k", "-m", "--deselect", "--maxfail", "--tb", "--durations"}:
            forwarded.append(value)
            skip_value = True
        elif value.startswith("tests/") or value.startswith("tests\\") or Path(value.split("::")[0]).is_absolute():
            filename, *node = value.split("::")
            target = (ROOT / filename).resolve()
            if not target.is_relative_to(ROOT / "tests") or not target.exists():
                parser.error("Select an existing path inside this checkout's tests/ directory")
            if group_for(target) == "live" and not live:
                parser.error("Selecting tests/live requires --live-base-url")
            selectors.append((target, node))
        else:
            forwarded.append(value)
    paths = discover(ROOT / "tests", suite, live)
    if selectors:
        selected = []
        for target, node in selectors:
            for path in paths:
                if path == target or path.is_relative_to(target):
                    selected.append(str(path) + ("::" + "::".join(node) if node else ""))
        paths = list(dict.fromkeys(selected))
    if not paths:
        parser.error("No scenarios match the selected suite and paths")
    return [str(p) for p in paths], forwarded


def main():
    parser = argparse.ArgumentParser(description='Run QA tests and save reports.')
    parser.add_argument("--suite", choices=("coverage", "business", "e2e", "tooling", "live", "all"), default="all")
    parser.add_argument("--local", action="store_true", help="Disposable infrastructure is the default")
    parser.add_argument("--coverage-url", help="Existing test proxy, configured for the controlled upstream")
    parser.add_argument("--upstream-url", help="Existing controlled upstream address")
    parser.add_argument("--deployed-run-id", help="Existing active FORMA run UUID (server-side credentials)")
    parser.add_argument("--live-base-url", help="Explicitly enable GET-only deployment checks")
    parser.add_argument("--coverage-backend-dir", type=Path, default=TEAM_BACKEND)
    parser.add_argument("--report-dir", type=Path, default=ROOT / "reports")
    parser.add_argument("--label", default="Проверка API и покрытия GraphQL")
    parser.add_argument("--run-folder", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--selection-file", type=Path, help=argparse.SUPPRESS)
    args, extra = parser.parse_known_args()
    if extra[:1] == ["--"]:
        extra = extra[1:]
    if any(x.startswith("-n") or x == "--numprocesses" for x in extra):
        parser.error("Parallel pytest workers are not supported")
    if args.suite == "live" and not args.live_base_url:
        parser.error("--suite live requires --live-base-url")
    if args.coverage_url and not args.upstream_url:
        parser.error("--coverage-url requires --upstream-url")
    if args.deployed_run_id and (args.suite != "e2e" or not args.coverage_url or not os.environ.get("COVERAGE_API_KEY")):
        parser.error("--deployed-run-id requires --suite e2e, --coverage-url and COVERAGE_API_KEY")
    if args.deployed_run_id:
        from fastapi import HTTPException
        from coverage_qa.deployed import validate_deployed_target
        try:
            validate_deployed_target(args.deployed_run_id, args.coverage_url, args.upstream_url)
        except HTTPException as exc:
            parser.error(str(exc.detail))
    paths, extra = select_paths(extra, parser, args.suite, bool(args.live_base_url))
    if "--collect-only" in extra or "--co" in extra:
        return subprocess.call([sys.executable, "-m", "pytest", "-c", str(ROOT / "pyproject.toml"),
                                *paths, *extra], cwd=ROOT,
                               env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    folder = args.run_folder.resolve() if args.run_folder else new_run(args.report_dir, args.label, args.suite)
    if args.run_folder:
        if folder.parent != args.report_dir.resolve() / "runs" or not (folder / "manifest.json").exists():
            parser.error("Invalid panel run folder")
        if json.loads((folder / "manifest.json").read_text(encoding="utf-8"))["status"] != "starting":
            parser.error("A completed run cannot be overwritten")
    print(f"Test run: {folder.name}\nArtifacts: {folder}", flush=True)
    code = 2
    try:
        command = [sys.executable, "-m", "pytest", "-c", str(ROOT / "pyproject.toml"), "-v",
                   f"--run-folder={folder}", f"--run-label={args.label}", f"--junitxml={folder / 'junit.xml'}"]
        if args.selection_file:
            command += [f"--selection-file={args.selection_file.resolve()}"]
        if args.coverage_url:
            command += [f"--coverage-url={args.coverage_url}", f"--upstream-url={args.upstream_url}"]
            if args.deployed_run_id:
                command += [f"--deployed-run-id={args.deployed_run_id}"]
        else:
            command += ["--managed-coverage"]
        command += [f"--coverage-backend-dir={args.coverage_backend_dir.resolve()}"]
        if args.live_base_url:
            command += [f"--live-base-url={args.live_base_url}"]
        env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PLAYWRIGHT_BROWSERS_PATH": str(ROOT / ".browsers")}
        code = subprocess.call([*command, *paths, *extra], cwd=ROOT, env=env)
    except (Exception, KeyboardInterrupt) as exc:
        report = {"id": folder.name, "startedAt": json.loads((folder / "manifest.json").read_text())["startedAt"],
                  "finishedAt": datetime.now(timezone.utc).isoformat(), "label": args.label,
                  "endpoint": args.coverage_url or "Изолированное окружение", "exitCode": 2,
                  "tests": [], "coverageRuns": [], "infrastructureErrors": [str(exc)],
                  "cancelled": (folder / "cancel.requested").exists()}
        (folder / "launcher-error.log").write_text(traceback.format_exc(), encoding="utf-8")
        save_json(folder / "results.json", report)
        write_report(report, folder / "index.html")
        update_history(folder, report)
        print(f"Launch failed: {exc}", file=sys.stderr)
    print(f"Report: {folder / 'index.html'}", flush=True)
    return code
