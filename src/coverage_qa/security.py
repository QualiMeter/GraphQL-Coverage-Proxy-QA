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
            # Only scan text files under 2 MB
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

    sensitive_key_names = {"password", "token", "accesstoken", "secret", "authorization"}

    for results_file in runs_dir.glob("*/results.json"):
        try:
            data = json.loads(results_file.read_text(encoding="utf-8"))
        except Exception:
            continue

        rel_path = results_file.relative_to(report_root).as_posix()

        # Check events in each test
        for test in data.get("tests", []):
            for event in test.get("events", []):
                # Check headers
                headers = event.get("headers", {})
                for h_name, h_val in headers.items():
                    if any(s in h_name.lower() for s in ("auth", "key", "token", "cookie")) and h_val != "<redacted>":
                        violations.append({
                            "file": rel_path,
                            "rule": f"Unmasked sensitive header '{h_name}' in event '{event.get('title')}'",
                        })

                # Check payload requests
                req = event.get("request")
                if isinstance(req, dict):
                    for k, v in req.items():
                        if k.lower() in sensitive_key_names and v not in ("<redacted>", "<скрыто>"):
                            violations.append({
                                "file": rel_path,
                                "rule": f"Unmasked key '{k}' in request payload of '{event.get('title')}'",
                            })

    return violations


def run_security_audit(workspace_root: Path | str = ".", check_reports: bool = True) -> dict:
    from coverage_qa.environment import ROOT
    workspace = Path(workspace_root).resolve()
    if not (workspace / "src" / "coverage_qa").exists() and (ROOT / "src" / "coverage_qa").exists():
        workspace = ROOT
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
    parser.add_argument("--workspace", default=".", help="Root directory to scan (default: .)")
    parser.add_argument("--no-reports", action="store_true", help="Skip scanning generated reports/runs")
    args = parser.parse_args()

    result = run_security_audit(args.workspace, check_reports=not args.no_reports)

    print("=" * 60)
    print("  SECURITY & SECRETS AUDIT GATE")
    print("=" * 60)

    if result["passed"]:
        print("  ✓ PASS: No unmasked secrets, private keys, or token leaks found.")
        print("=" * 60)
        return 0
    else:
        print(f"  ✗ FAIL: Found {len(result['violations'])} security violation(s):")
        for v in result["violations"]:
            print(f"    • [{v['file']}] {v['rule']}")
        print("=" * 60)
        return 1


if __name__ == "__main__":
    sys.exit(main())
