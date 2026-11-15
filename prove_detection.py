"""Verify that unchanged tests reject an intentionally broken disposable API copy."""

import json
import shutil
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory


ROOT = Path(__file__).resolve().parent


def main():
    temp_root = ROOT / ".tmp"
    temp_root.mkdir(exist_ok=True)
    with TemporaryDirectory(prefix="broken-api-", dir=temp_root) as directory:
        backend = Path(directory) / "backend"
        shutil.copytree(ROOT.parent / "graphql-example" / "backend" / "demo_api", backend / "demo_api",
                        ignore=shutil.ignore_patterns("__pycache__"))
        app = backend / "demo_api" / "app.py"
        source = app.read_text(encoding="utf-8")
        original = 'product["stock"] -= quantity'
        if source.count(original) != 1:
            raise RuntimeError("Stock update changed; fault injection must be reviewed")
        # Only a disposable copy is changed; production source and tests stay intact.
        app.write_text(source.replace(original, 'product["stock"] += quantity'), encoding="utf-8")
        folder = ROOT / "reports" / "fault-check"
        code = subprocess.call([
            sys.executable, str(ROOT / "run_tests.py"), "--suite", "shop", "--local", "--backend-dir", str(backend),
            "--report-dir", str(folder),
            "--label", "Проверка чувствительности: временная копия API увеличивает stock вместо уменьшения",
            "--", "-k", "purchase_lifecycle",
        ])
        latest = json.loads((folder / "latest.json").read_text(encoding="utf-8"))
        report = json.loads((Path(latest["path"]) / "results.json").read_text(encoding="utf-8"))
        failures = [event for test in report["tests"] for event in test["events"]
                    if event["kind"] == "check" and not event["passed"]]
        detected = code == 1 and any(e["title"] == "Остаток изменился в отдельном query" for e in failures)
        if not detected:
            print("Fault was NOT detected as expected. Inspect reports/fault-check.")
            return 1
        print("Sensitivity check passed: unchanged HTTP tests detected the wrong stock update.")
        print(f"Evidence: {folder / 'index.html'}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
