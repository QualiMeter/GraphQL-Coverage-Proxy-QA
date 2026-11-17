import argparse
import json
import os
import re
import sys
from pathlib import Path


SUSPICIOUS_PATTERNS = [
    (re.compile(r"-----BEGIN (?:[A-Z0-9_-]+\s+)?(?:PRIVATE KEY|CERTIFICATE)-----"), "Private key block detected"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "Hardcoded AWS Access Key ID detected"),
    (re.compile(r"\bghp_[A-Za-z0-9]{36}\b"), "Hardcoded GitHub Personal Access Token detected"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{20,}\.eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\b"), "Unmasked production-length JWT detected"),
]

ALLOWED_FILES = {
    ".gitignore",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
}

IGNORE_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "dist",
    ".pytest_cache",
    "__pycache__",
    ".turbo",
    ".panel",
    "reports",
}


def audit_repository_files(repo_root: Path) -> list[dict]:
    repo_root = Path(repo_root).resolve()
    violations = []

    for root, dirs, files in os.walk(repo_root):
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS and not d.startswith(".")]
        for file in files:
            if file in ALLOWED_FILES:
                continue
            file_path = Path(root) / file
            try:
                if file_path.stat().st_size > 2 * 1024 * 1024:
                    continue
                content = file_path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue

            for pattern, desc in SUSPICIOUS_PATTERNS:
                matches = pattern.findall(content)
                if matches:
                    rel_path = file_path.relative_to(repo_root).as_posix()
                    violations.append({
                        "file": rel_path,
                        "rule": desc,
                        "match_count": len(matches),
                    })

    return violations


def audit_reports(report_root: Path) -> list[dict]:
    report_root = Path(report_root).resolve()
    violations = []
    runs_dir = report_root / "runs"
    if not runs_dir.exists():
        return violations

    sensitive_key_names = {
        "password", "token", "accesstoken", "refreshtoken", "secret", "apikey",
        "authorization", "cookie", "setcookie", "xcoverageapikey", "xcsrftoken",
        "csrftoken", "currentpassword", "newpassword", "sessiontoken",
    }

    def unmasked_keys(value):
        if isinstance(value, dict):
            for name, child in value.items():
                normalized = name.lower().replace("_", "").replace("-", "")
                if normalized in sensitive_key_names:
                    if child not in (None, "<redacted>", "<скрыто>"):
                        yield name
                else:
                    yield from unmasked_keys(child)
        elif isinstance(value, list):
            for child in value:
                yield from unmasked_keys(child)

    for results_file in runs_dir.glob("*/results.json"):
        try:
            data = json.loads(results_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            violations.append({"file": results_file.relative_to(report_root).as_posix(),
                               "rule": "Report could not be read as JSON"})
            continue

        rel_path = results_file.relative_to(report_root).as_posix()

        for test in data.get("tests", []):
            for event in test.get("events", []):
                headers = event.get("headers", {})
                for h_name, h_val in headers.items():
                    if any(s in h_name.lower() for s in ("auth", "key", "token", "cookie")) and h_val != "<redacted>":
                        violations.append({
                            "file": rel_path,
                            "rule": f"Unmasked sensitive header '{h_name}' in event '{event.get('title')}'",
                        })

                for section in ("request", "response"):
                    for key in unmasked_keys(event.get(section)):
                        violations.append({"file": rel_path,
                                           "rule": f"Unmasked key '{key}' in {section} payload"})

    return violations


def run_security_audit(workspace_root: Path | str | None = None, check_reports: bool = True) -> dict:
    from coverage_qa.environment import ROOT
    workspace = Path(workspace_root).resolve() if workspace_root is not None else ROOT
    repo_violations = audit_repository_files(workspace)
    report_violations = audit_reports(workspace / "reports") if check_reports else []

    all_violations = repo_violations + report_violations

    return {
        "passed": len(all_violations) == 0,
        "violations": all_violations,
        "repo_violations": len(repo_violations),
        "report_violations": len(report_violations),
    }


def main():
    parser = argparse.ArgumentParser(description="Security audit: scan for unmasked secrets, credentials, and token leaks.")
    parser.add_argument("--workspace", help="Root directory to scan (default: QA project)")
    parser.add_argument("--no-reports", action="store_true", help="Skip scanning generated reports/runs")
    args = parser.parse_args()

    result = run_security_audit(args.workspace, check_reports=not args.no_reports)

    if result["passed"]:
        print("Configured security checks passed.")
        return 0
    print(f"Security findings: {len(result['violations'])}")
    for violation in result["violations"]:
        print(f"[{violation['file']}] {violation['rule']}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
