"""Launch test-only infrastructure and retain an immutable report per invocation."""

import argparse
import json
import os
import subprocess
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

from environment import ROOT, Server, TEAM_BACKEND
from history import new_run, save_json, update_history
from report import write_report
from test_catalog import discover


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=("coverage", "business", "live", "shop", "tooling", "all"), default=None)
    parser.add_argument("--local", action="store_true", help="Use disposable local infrastructure (default)")
    parser.add_argument("--api-url", help="Existing shop endpoint, selects --suite shop by default")
    parser.add_argument("--coverage-url", help="Existing coverage backend; must target the supplied test upstream")
    parser.add_argument("--upstream-url", help="Controlled upstream fixtures/upstream.py address")
    parser.add_argument("--live-base-url", help="Enable GET-only deployment checks against this base URL")
    parser.add_argument("--backend-dir", type=Path, default=ROOT.parent / "graphql-example" / "backend")
    parser.add_argument("--coverage-backend-dir", type=Path, default=TEAM_BACKEND)
    parser.add_argument("--report-dir", type=Path, default=ROOT / "reports")
    parser.add_argument("--label", default="Проверка прокси и покрытия GraphQL")
    parser.add_argument("--run-folder", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--selection-file", type=Path, help=argparse.SUPPRESS)
    args, extra = parser.parse_known_args()
    if extra[:1] == ["--"]:
        extra = extra[1:]
    if any(x.startswith("-n") or x == "--numprocesses" for x in extra):
        parser.error("Parallel pytest workers are not supported by this isolated test stack")
    suite = args.suite or ("shop" if args.api_url else "all")
    if suite == "live" and not args.live_base_url:
        parser.error("--suite live requires --live-base-url")
    if args.coverage_url and not args.upstream_url:
        parser.error("--coverage-url requires --upstream-url")
    folder = args.run_folder.resolve() if args.run_folder else new_run(args.report_dir, args.label, suite)
    if args.run_folder:
        if folder.parent != args.report_dir.resolve() / "runs" or not (folder / "manifest.json").exists():
            parser.error("Invalid panel run folder")
        if json.loads((folder / "manifest.json").read_text(encoding="utf-8"))["status"] != "starting":
            parser.error("A completed run cannot be overwritten")
    print(f"Test run: {folder.name}\nArtifacts: {folder}", flush=True)
    server = None
    code = 2
    try:
        command = [sys.executable, "-m", "pytest", "-c", str(ROOT / "pytest.ini"), "-v",
                   "--run-folder", str(folder), "--run-label", args.label, f"--junitxml={folder / 'junit.xml'}"]
        if args.selection_file:
            command += ["--selection-file", str(args.selection_file.resolve())]
        if suite == "shop":
            url = args.api_url
            if not url:
                server = Server("demo_api.app:app", args.backend_dir, folder / "shop.log")
                server.start()
                url = server.base + "/graphql"
            command += ["--api-url", url]
        if suite in {"coverage", "all"}:
            if args.coverage_url:
                command += ["--coverage-url", args.coverage_url, "--upstream-url", args.upstream_url]
            else:
                command += ["--managed-coverage", "--coverage-backend-dir", str(args.coverage_backend_dir.resolve())]
        if suite in {"business", "all"}:
            command += ["--coverage-backend-dir", str(args.coverage_backend_dir.resolve())]
        if suite == "live" or args.live_base_url:
            command += ["--live-base-url", args.live_base_url]
        command += [str(path) for path in discover(ROOT / "tests", suite, bool(args.live_base_url))]
        env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PLAYWRIGHT_BROWSERS_PATH": str(ROOT / ".browsers")}
        code = subprocess.call([*command, *extra], cwd=ROOT, env=env)
    except (Exception, KeyboardInterrupt) as exc:
        report = {"id": folder.name, "startedAt": json.loads((folder / "manifest.json").read_text())["startedAt"],
                  "finishedAt": datetime.now(timezone.utc).isoformat(), "label": args.label,
                  "endpoint": args.api_url or args.coverage_url or "Изолированное окружение",
                  "exitCode": 2, "tests": [], "coverageRuns": [], "infrastructureErrors": [str(exc)]}
        (folder / "launcher-error.log").write_text(traceback.format_exc(), encoding="utf-8")
        save_json(folder / "results.json", report)
        write_report(report, folder / "index.html")
        update_history(folder, report)
        print(f"Launch failed: {exc}", file=sys.stderr)
    finally:
        if server:
            server.stop()
    print(f"Report: {folder / 'index.html'}", flush=True)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
