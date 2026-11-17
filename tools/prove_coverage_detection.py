import json
import shutil
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from coverage_qa.environment import ROOT, TEAM_BACKEND


def main():
    temp_root = ROOT / ".tmp"
    temp_root.mkdir(exist_ok=True)
    with TemporaryDirectory(prefix="broken-coverage-", dir=temp_root) as directory:
        backend = Path(directory) / "backend"
        for name in ("app", "alembic"):
            shutil.copytree(TEAM_BACKEND / name, backend / name, ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copy2(TEAM_BACKEND / "alembic.ini", backend / "alembic.ini")
        services = backend / "app" / "services.py"
        source = services.read_text(encoding="utf-8")
        needle = "covered = hits > 0"
        if source.count(needle) != 1:
            raise RuntimeError("Coverage implementation changed; review fault injection")
        services.write_text(source.replace(needle, "covered = hits >= 0"), encoding="utf-8")
        root = ROOT / "reports" / "coverage-fault-check"
        code = subprocess.call([
            sys.executable, "-m", "coverage_qa", "test", "--suite", "coverage", "--local",
            "--coverage-backend-dir", str(backend), "--report-dir", str(root),
            "--label", "Намеренный дефект во временной копии: поля с hits=0 объявлены покрытыми",
            "--", "-k", "empty_and_schema_deduplication",
        ])
        latest = json.loads((root / "latest.json").read_text(encoding="utf-8"))
        report = json.loads((Path(latest["path"]) / "results.json").read_text(encoding="utf-8"))
        failures = [event for test in report["tests"] for event in test["events"]
                    if event["kind"] == "check" and not event["passed"]]
        caught = code == 1 and any(e["title"] == "Число покрытых полей" and e["actual"] == 10 and e["expected"] == 0 for e in failures)
        print("Injected coverage defect detected." if caught else "Expected coverage defect was NOT detected.")
        print(Path(latest["path"]) / "index.html")
        return 0 if caught else 1


if __name__ == "__main__":
    raise SystemExit(main())
