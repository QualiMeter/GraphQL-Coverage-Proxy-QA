"""Run from the QA checkout with python -m acceptance.mvp.run."""
import json
import os
import subprocess
from pathlib import Path
from uuid import uuid4

from .report import write_summary

import httpx
from coverage_qa.environment import CoverageStack, ROOT


def main():
    folder = ROOT / "reports" / "mvp" / uuid4().hex
    folder.mkdir(parents=True)
    print(f"MVP report: {folder}", flush=True)
    stack = CoverageStack(folder, upstream_module="acceptance.mvp.demo:app")
    try:
        stack.start()
        directory = Path(__file__).resolve().parent
        subprocess.run(["node", "node_modules/@playwright/test/cli.js", "test"],
                       cwd=directory, check=True, env={**os.environ, "MVP_PROXY": stack.proxy.base,
                       "MVP_UPSTREAM": stack.upstream.base, "MVP_KEY": stack.key, "MVP_REPORT": str(folder)})
        saved = json.loads((folder / "coverage.json").read_text(encoding="utf-8"))
        stack.proxy.restart()
        with httpx.Client(base_url=stack.proxy.base, headers={"X-Coverage-Api-Key": stack.key},
                          trust_env=False) as client:
            response = client.get(f"/api/runs/{saved['runId']}/coverage")
            response.raise_for_status()
            if response.json() != saved:
                raise AssertionError("Coverage changed after backend restart")
        write_summary(folder, saved)
        print("PostgreSQL: coverage and all field hits survived backend restart.")
    finally:
        errors = stack.close()
        if errors:
            raise RuntimeError(errors)


if __name__ == "__main__":
    main()
