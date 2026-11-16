"""Preflight quality gate: verify linting, unit tests, builds, and hygiene across repositories."""
import argparse
import concurrent.futures
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from coverage_qa.environment import ROOT, TEAM_BACKEND

FRONTEND_DIR = ROOT.parent / "GraphQL-Coverage-Proxy-Frontend"


class PreflightCheck:
    def __init__(self, name, func):
        self.name = name
        self.func = func
        self.passed = False
        self.duration = 0.0
        self.message = ""
        self.details = ""


def check_git_hygiene():
    """Verify repository hygiene: gitignore, untracked artifacts, and secrets scan."""
    issues = []

    # 1. Check QA .gitignore for reports/ exclusion
    gitignore = ROOT / ".gitignore"
    if gitignore.is_file():
        content = gitignore.read_text(encoding="utf-8")
        if "reports" not in content and "reports/" not in content:
            issues.append("QA .gitignore must exclude 'reports/' directory")
    else:
        issues.append("QA .gitignore is missing")

    # 2. Check for unwanted zip traces or binaries in QA
    for root_dir in (ROOT, TEAM_BACKEND, FRONTEND_DIR):
        if not root_dir.exists():
            continue
        try:
            status = subprocess.run(
                ["git", "-C", str(root_dir), "status", "--porcelain"],
                capture_output=True, text=True, timeout=5
            )
            for line in status.stdout.splitlines():
                filepath = line[3:].strip()
                if filepath.endswith(".zip") or filepath.endswith(".tar.gz"):
                    issues.append(f"Archive file tracked in git: {root_dir.name}/{filepath}")
                if "credentials" in filepath.lower() or filepath.endswith(".pfx") or filepath.endswith(".pem"):
                    issues.append(f"Potential secret file tracked in git: {root_dir.name}/{filepath}")
        except Exception:
            pass

    # 3. Deep security & secrets audit across repository files and reports
    try:
        from coverage_qa.security import run_security_audit
        audit_res = run_security_audit(ROOT, check_reports=True)
        if not audit_res["passed"]:
            for v in audit_res["violations"]:
                issues.append(f"{v['file']}: {v['rule']}")
    except Exception as exc:
        issues.append(f"Security audit runner error: {exc}")

    if issues:
        return False, "; ".join(issues)
    return True, "All repositories clean, reports excluded, no secrets found"


def check_backend_units():
    """Run core unit tests in backend (AST, schema, diagnostic, safe doc)."""
    if not TEAM_BACKEND.exists():
        return False, f"Backend directory not found at {TEAM_BACKEND}"

    test_files = [
        "tests/test_query_analyzer.py",
        "tests/test_schema_parser.py",
        "tests/test_schema_metadata.py",
        "tests/test_safe_document.py",
    ]
    existing = [str(TEAM_BACKEND / f) for f in test_files if (TEAM_BACKEND / f).is_file()]
    if not existing:
        return False, "No core backend unit test files found"

    env = {**os.environ, "PYTHONPATH": str(TEAM_BACKEND), "PYTHONDONTWRITEBYTECODE": "1"}
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", *existing, "-q"],
        cwd=TEAM_BACKEND, env=env, capture_output=True, text=True, timeout=30
    )
    if proc.returncode == 0:
        lines = [line for line in proc.stdout.splitlines() if line.strip()]
        summary = lines[-1] if lines else "OK"
        return True, f"Core unit tests passed ({summary})"
    return False, proc.stdout or proc.stderr


def check_frontend_lint():
    """Run ESLint on frontend code."""
    if not FRONTEND_DIR.exists():
        return False, f"Frontend directory not found at {FRONTEND_DIR}"

    proc = subprocess.run(
        ["npm", "run", "lint"],
        cwd=FRONTEND_DIR, shell=True, capture_output=True, text=True, timeout=60
    )
    if proc.returncode == 0:
        return True, "Frontend ESLint passed with 0 errors"
    return False, proc.stdout or proc.stderr


def check_frontend_tests():
    """Run Vitest tests on frontend."""
    if not FRONTEND_DIR.exists():
        return False, f"Frontend directory not found at {FRONTEND_DIR}"

    proc = subprocess.run(
        ["npm", "test"],
        cwd=FRONTEND_DIR, shell=True, capture_output=True, text=True, timeout=60
    )
    if proc.returncode == 0:
        lines = [line for line in proc.stdout.splitlines() if "Tests" in line or "passed" in line]
        summary = lines[-1].strip() if lines else "OK"
        return True, f"Frontend Vitest passed ({summary})"
    return False, proc.stdout or proc.stderr


def check_frontend_build():
    """Verify TypeScript types and production Vite build."""
    if not FRONTEND_DIR.exists():
        return False, f"Frontend directory not found at {FRONTEND_DIR}"

    proc = subprocess.run(
        ["npm", "run", "build"],
        cwd=FRONTEND_DIR, shell=True, capture_output=True, text=True, timeout=90
    )
    if proc.returncode == 0:
        dist_html = FRONTEND_DIR / "dist" / "index.html"
        if dist_html.is_file():
            return True, "Vite production build and TypeScript check passed"
        return False, "Build succeeded but dist/index.html is missing"
    return False, proc.stdout or proc.stderr


def check_qa_tooling():
    """Run QA tooling and platform test suite."""
    proc = subprocess.run(
        [sys.executable, "-m", "coverage_qa", "test", "--suite", "tooling"],
        cwd=ROOT, capture_output=True, text=True, timeout=120
    )
    if proc.returncode == 0:
        lines = [line for line in proc.stdout.splitlines() if "passed" in line]
        summary = lines[-1].strip() if lines else "OK"
        return True, f"QA tooling suite passed ({summary})"
    return False, proc.stdout or proc.stderr


def check_qa_e2e():
    """Run full QA E2E acceptance suite."""
    proc = subprocess.run(
        [sys.executable, "-m", "coverage_qa", "test", "--suite", "e2e"],
        cwd=ROOT, capture_output=True, text=True, timeout=300
    )
    if proc.returncode == 0:
        lines = [line for line in proc.stdout.splitlines() if "passed" in line]
        summary = lines[-1].strip() if lines else "OK"
        return True, f"QA E2E suite passed ({summary})"
    return False, proc.stdout or proc.stderr


def run_checks(full=False, skip_build=False, skip_tooling=False):
    checks = [
        PreflightCheck("Git & Secret Hygiene Audit", check_git_hygiene),
        PreflightCheck("Backend Core Unit Tests", check_backend_units),
        PreflightCheck("Frontend ESLint", check_frontend_lint),
        PreflightCheck("Frontend Vitest Unit Suite", check_frontend_tests),
    ]
    if not skip_build:
        checks.append(PreflightCheck("Frontend TypeScript & Vite Build", check_frontend_build))
    if not skip_tooling:
        checks.append(PreflightCheck("QA Platform & Tooling Suite", check_qa_tooling))
    if full:
        checks.append(PreflightCheck("QA E2E Acceptance Suite", check_qa_e2e))

    print("=" * 72)
    print("  PREFLIGHT VERIFICATION GATE")
    print(f"  Mode: {'FULL (including E2E)' if full else 'QUICK (lint + units + build + tooling)'}")
    print("=" * 72)

    total_start = time.perf_counter()
    all_passed = True

    def execute_check(c):
        t0 = time.perf_counter()
        try:
            passed, message = c.func()
        except Exception as exc:
            passed, message = False, str(exc)
        c.duration = time.perf_counter() - t0
        c.passed = passed
        c.message = message
        return c

    # Run independent fast checks in parallel when possible
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(execute_check, check) for check in checks]
        for future in concurrent.futures.as_completed(futures):
            res = future.result()
            if not res.passed:
                all_passed = False

    # Display results sorted by order of declaration
    for check in checks:
        status_tag = "[PASS]" if check.passed else "[FAIL]"
        print(f"  {status_tag} ({check.duration:5.1f}s)  {check.name}")
        if check.passed:
            print(f"         → {check.message}")
        else:
            print(f"         ✗ ERROR: {check.message}")

    total_duration = time.perf_counter() - total_start
    print("-" * 72)

    if all_passed:
        print(f"  ✓ ALL {len(checks)} PREFLIGHT CHECKS PASSED in {total_duration:.1f}s")
        print("  ✓ Workspace is green and safe to commit / push.")
        print("=" * 72)
        return 0
    else:
        print(f"  ✗ PREFLIGHT FAILED in {total_duration:.1f}s")
        print("  ✗ Resolve the failed checks before pushing.")
        print("=" * 72)
        return 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--full", action="store_true", help="Include full E2E acceptance suite")
    parser.add_argument("--skip-build", action="store_true", help="Skip frontend production build")
    parser.add_argument("--skip-tooling", action="store_true", help="Skip QA tooling tests")
    args = parser.parse_args()

    sys.exit(run_checks(full=args.full, skip_build=args.skip_build, skip_tooling=args.skip_tooling))


if __name__ == "__main__":
    main()
