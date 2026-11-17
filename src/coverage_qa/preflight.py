import argparse
import concurrent.futures
import os
import shutil
import subprocess
import sys
import time

from coverage_qa.environment import ROOT, TEAM_BACKEND

FRONTEND_DIR = ROOT.parent / "GraphQL-Coverage-Proxy-Frontend"
NPM = shutil.which("npm.cmd" if os.name == "nt" else "npm") or "npm"


class PreflightCheck:
    def __init__(self, name, func):
        self.name = name
        self.func = func
        self.passed = False
        self.duration = 0.0
        self.message = ""
        self.details = ""


def check_git_hygiene():
    issues = []
    checked_repositories = 0

    gitignore = ROOT / ".gitignore"
    if gitignore.is_file():
        content = gitignore.read_text(encoding="utf-8")
        if "reports" not in content and "reports/" not in content:
            issues.append("QA .gitignore must exclude 'reports/' directory")
    else:
        issues.append("QA .gitignore is missing")

    for root_dir in (ROOT, TEAM_BACKEND, FRONTEND_DIR):
        if not (root_dir / ".git").exists():
            continue
        try:
            status = subprocess.run(
                ["git", "-C", str(root_dir), "ls-files", "-z"],
                capture_output=True, text=True, timeout=5
            )
            if status.returncode != 0:
                issues.append(f"Git inspection failed for {root_dir.name}")
                continue
            checked_repositories += 1
            for filepath in filter(None, status.stdout.split("\0")):
                if filepath.endswith(".zip") or filepath.endswith(".tar.gz"):
                    issues.append(f"Archive file tracked in git: {root_dir.name}/{filepath}")
                if "credentials" in filepath.lower() or filepath.endswith(".pfx") or filepath.endswith(".pem"):
                    issues.append(f"Potential secret file tracked in git: {root_dir.name}/{filepath}")
        except (OSError, subprocess.TimeoutExpired) as exc:
            issues.append(f"Git inspection failed for {root_dir.name}: {exc}")

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
    return True, f"{checked_repositories} Git repositories and configured secret patterns checked"


def check_backend_units():
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
    if not FRONTEND_DIR.exists():
        return False, f"Frontend directory not found at {FRONTEND_DIR}"

    proc = subprocess.run(
        [NPM, "run", "lint"],
        cwd=FRONTEND_DIR, shell=os.name == "nt", capture_output=True, text=True, timeout=60
    )
    if proc.returncode == 0:
        return True, "Frontend ESLint passed with 0 errors"
    return False, proc.stdout or proc.stderr


def check_frontend_tests():
    if not FRONTEND_DIR.exists():
        return False, f"Frontend directory not found at {FRONTEND_DIR}"

    proc = subprocess.run(
        [NPM, "test"],
        cwd=FRONTEND_DIR, shell=os.name == "nt", capture_output=True, text=True, timeout=60
    )
    if proc.returncode == 0:
        lines = [line for line in proc.stdout.splitlines() if "Tests" in line or "passed" in line]
        summary = lines[-1].strip() if lines else "OK"
        return True, f"Frontend Vitest passed ({summary})"
    return False, proc.stdout or proc.stderr


def check_frontend_build():
    if not FRONTEND_DIR.exists():
        return False, f"Frontend directory not found at {FRONTEND_DIR}"

    proc = subprocess.run(
        [NPM, "run", "build"],
        cwd=FRONTEND_DIR, shell=os.name == "nt", capture_output=True, text=True, timeout=90
    )
    if proc.returncode == 0:
        dist_html = FRONTEND_DIR / "dist" / "index.html"
        if dist_html.is_file():
            return True, "Vite production build and TypeScript check passed"
        return False, "Build succeeded but dist/index.html is missing"
    return False, proc.stdout or proc.stderr


def check_qa_tooling():
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

    print(f"QA checks: {len(checks)}")

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

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(execute_check, check) for check in checks]
        for future in concurrent.futures.as_completed(futures):
            res = future.result()
            if not res.passed:
                all_passed = False

    for check in checks:
        status_tag = "[PASS]" if check.passed else "[FAIL]"
        print(f"{status_tag} {check.name} ({check.duration:.1f}s)")
        print(check.message)

    total_duration = time.perf_counter() - total_start
    passed = sum(check.passed for check in checks)
    print(f"Passed {passed}/{len(checks)} in {total_duration:.1f}s")
    return 0 if all_passed else 1


def main():
    parser = argparse.ArgumentParser(description='Check backend, frontend and QA.')
    parser.add_argument("--full", action="store_true", help="Include full E2E acceptance suite")
    parser.add_argument("--skip-build", action="store_true", help="Skip frontend production build")
    parser.add_argument("--skip-tooling", action="store_true", help="Skip QA tooling tests")
    args = parser.parse_args()

    sys.exit(run_checks(full=args.full, skip_build=args.skip_build, skip_tooling=args.skip_tooling))


if __name__ == "__main__":
    main()
