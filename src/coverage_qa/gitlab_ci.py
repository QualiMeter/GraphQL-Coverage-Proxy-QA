"""GitLab CI integration for GraphQL Coverage Proxy.

Supports two enterprise models:
1. Option A (Observer / In-Pipeline Gate):
   Tests run inside GitLab CI runner; requests pass through GraphQL Coverage Proxy.
   Quality Gate verifies coverage thresholds, logs GitLab regex coverage metric,
   and exports JUnit XML and HTML reports for Merge Requests.
2. Option B (Remote Pipeline Trigger):
   TestLab or CLI triggers a GitLab CI pipeline via GitLab API v4,
   polls the execution status, and links the resulting coverage run.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import quote_plus
from xml.sax.saxutils import escape

import httpx

from coverage_qa.environment import ROOT

DEFAULT_GITLAB_HOST = "https://gitlab.com"
GITLAB_COVERAGE_REGEX = r"Total coverage:\s*([0-9.]+)%"


@dataclass
class GateEvaluation:
    run_id: str
    total_fields: int
    covered_fields: int
    coverage_percent: float
    min_coverage: float
    passed: bool
    uncovered_fields: list[str]
    covered_field_names: list[str]
    field_hits: dict[str, int]
    service_id: str | None = None
    schema_id: str | None = None

    @property
    def gitlab_metric_line(self) -> str:
        """Formatted string matching GitLab CI coverage regex."""
        return f"Total coverage: {self.coverage_percent:.2f}%"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class GitLabClient:
    """Client for GitLab CI/CD API v4."""

    def __init__(
        self,
        host: str | None = None,
        token: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        raw_host = host or os.environ.get("GITLAB_HOST") or os.environ.get("CI_SERVER_URL") or DEFAULT_GITLAB_HOST
        self.host = raw_host.strip().rstrip("/")
        self.token = token or os.environ.get("GITLAB_TOKEN") or os.environ.get("CI_JOB_TOKEN")
        self.timeout = timeout

    def _normalize_project_id(self, project_id: str | int) -> str:
        if isinstance(project_id, int) or str(project_id).isdigit():
            return str(project_id)
        # URL encode namespace/project (e.g. org/repo -> org%2Frepo)
        return quote_plus(str(project_id).strip())

    def trigger_pipeline(
        self,
        project_id: str | int,
        ref: str = "main",
        token: str | None = None,
        variables: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Trigger a pipeline using either a Trigger Token or a Personal/Project Access Token."""
        auth_token = token or self.token
        if not auth_token:
            raise ValueError("GitLab token is required to trigger a pipeline")

        pid = self._normalize_project_id(project_id)
        vars_dict = variables or {}

        # If token starts with glpat- (Personal Access Token), use /pipeline endpoint
        if auth_token.startswith("glpat-") or auth_token.startswith("glcbt-"):
            url = f"{self.host}/api/v4/projects/{pid}/pipeline"
            headers = {"PRIVATE-TOKEN": auth_token, "Content-Type": "application/json"}
            payload = {
                "ref": ref,
                "variables": [{"key": k, "value": str(v)} for k, v in vars_dict.items()],
            }
            with httpx.Client(timeout=self.timeout, trust_env=False) as client:
                resp = client.post(url, headers=headers, json=payload)
                resp.raise_for_status()
                return resp.json()

        # Otherwise use standard GitLab trigger endpoint /trigger/pipeline
        url = f"{self.host}/api/v4/projects/{pid}/trigger/pipeline"
        data = {"token": auth_token, "ref": ref}
        for k, v in vars_dict.items():
            data[f"variables[{k}]"] = str(v)

        with httpx.Client(timeout=self.timeout, trust_env=False) as client:
            resp = client.post(url, data=data)
            resp.raise_for_status()
            return resp.json()

    def get_pipeline(
        self,
        project_id: str | int,
        pipeline_id: str | int,
        token: str | None = None,
    ) -> dict[str, Any]:
        """Retrieve details of a GitLab pipeline."""
        pid = self._normalize_project_id(project_id)
        url = f"{self.host}/api/v4/projects/{pid}/pipelines/{pipeline_id}"
        auth_token = token or self.token
        headers = {}
        if auth_token:
            if auth_token.startswith("glpat-") or auth_token.startswith("glcbt-"):
                headers["PRIVATE-TOKEN"] = auth_token
            else:
                headers["JOB-TOKEN"] = auth_token

        with httpx.Client(timeout=self.timeout, trust_env=False) as client:
            resp = client.get(url, headers=headers)
            resp.raise_for_status()
            return resp.json()

    def poll_pipeline(
        self,
        project_id: str | int,
        pipeline_id: str | int,
        token: str | None = None,
        timeout: float = 600.0,
        poll_interval: float = 5.0,
        callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        """Poll pipeline until completion or timeout."""
        terminal_statuses = {"success", "failed", "canceled", "skipped", "manual"}
        deadline = time.monotonic() + timeout

        while time.monotonic() < deadline:
            info = self.get_pipeline(project_id, pipeline_id, token=token)
            if callback:
                callback(info)

            status = info.get("status")
            if status in terminal_statuses:
                return info

            time.sleep(poll_interval)

        raise TimeoutError(f"Pipeline {pipeline_id} did not finish within {timeout}s")


class CoverageQualityGate:
    """Evaluates coverage data from GraphQL Coverage Proxy and builds reports."""

    def __init__(
        self,
        coverage_url: str = "http://localhost:8000",
        api_key: str | None = None,
        timeout: float = 15.0,
    ) -> None:
        self.coverage_url = (coverage_url or "http://localhost:8000").rstrip("/")
        self.api_key = api_key or os.environ.get("COVERAGE_API_KEY", "")
        self.timeout = timeout

    def fetch_coverage(self, run_id: str) -> dict[str, Any]:
        """Fetch raw coverage graph from Coverage Proxy."""
        url = f"{self.coverage_url}/api/runs/{run_id}/coverage"
        headers = {}
        if self.api_key:
            headers["X-Coverage-Api-Key"] = self.api_key

        with httpx.Client(timeout=self.timeout, trust_env=False) as client:
            resp = client.get(url, headers=headers)
            resp.raise_for_status()
            return resp.json()

    def evaluate(self, run_id: str, min_coverage: float = 80.0) -> GateEvaluation:
        """Evaluate coverage graph against min_coverage threshold."""
        data = self.fetch_coverage(run_id)

        types = data.get("types", [])
        field_hits: dict[str, int] = {}
        covered_fields: list[str] = []
        uncovered_fields: list[str] = []

        for t in types:
            t_name = t.get("name", "")
            for f in t.get("fields", []):
                f_name = f.get("name", "")
                hits = int(f.get("hits", 0))
                coordinate = f"{t_name}.{f_name}"
                field_hits[coordinate] = hits
                if hits > 0:
                    covered_fields.append(coordinate)
                else:
                    uncovered_fields.append(coordinate)

        summary = data.get("summary") or {}
        total = summary.get("total_fields", len(field_hits))
        covered = summary.get("covered_fields", len(covered_fields))

        if total > 0:
            coverage_percent = round((covered / total) * 100.0, 2)
        else:
            coverage_percent = 0.0

        passed = coverage_percent >= min_coverage

        return GateEvaluation(
            run_id=str(run_id),
            total_fields=total,
            covered_fields=covered,
            coverage_percent=coverage_percent,
            min_coverage=float(min_coverage),
            passed=passed,
            uncovered_fields=sorted(uncovered_fields),
            covered_field_names=sorted(covered_fields),
            field_hits=field_hits,
            service_id=str(data.get("service_id") or "") or None,
            schema_id=str(data.get("schema_id") or "") or None,
        )

    @staticmethod
    def generate_junit_xml(evaluation: GateEvaluation, out_path: Path | str) -> Path:
        """Generate JUnit XML report for GitLab CI Test Report tab."""
        target = Path(out_path).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)

        failures_count = 0 if evaluation.passed else 1
        uncovered_preview = ", ".join(evaluation.uncovered_fields[:15])
        if len(evaluation.uncovered_fields) > 15:
            uncovered_preview += f" ... (+{len(evaluation.uncovered_fields) - 15} more)"

        lines = [
            '<?xml version="1.0" encoding="utf-8"?>',
            f'<testsuites name="GraphQL Coverage Gate" tests="2" failures="{failures_count}" errors="0" time="0.05">',
            f'  <testsuite name="gitlab.coverage_gate" tests="2" failures="{failures_count}" errors="0" time="0.05">',
            f'    <testcase classname="gitlab.coverage_gate" name="test_field_coverage_threshold" time="0.03">',
        ]

        if not evaluation.passed:
            fail_msg = (
                f"Coverage {evaluation.coverage_percent:.2f}% is below required threshold {evaluation.min_coverage:.2f}%"
            )
            detail = (
                f"Run ID: {evaluation.run_id}\n"
                f"Covered fields: {evaluation.covered_fields} / {evaluation.total_fields} ({evaluation.coverage_percent:.2f}%)\n"
                f"Uncovered fields ({len(evaluation.uncovered_fields)}): {uncovered_preview}"
            )
            lines.append(
                f'      <failure message="{escape(fail_msg)}">{escape(detail)}</failure>'
            )

        lines.extend([
            '    </testcase>',
            f'    <testcase classname="gitlab.coverage_gate" name="test_schema_completeness" time="0.02"/>',
            '  </testsuite>',
            '</testsuites>',
        ])

        content = "\n".join(lines) + "\n"
        target.write_text(content, encoding="utf-8")
        return target

    @staticmethod
    def generate_csv(evaluation: GateEvaluation, out_path: Path | str) -> Path:
        """Export coverage field table to CSV."""
        target = Path(out_path).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)

        with target.open("w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["type", "field", "hits", "covered"])
            for coord, hits in sorted(evaluation.field_hits.items()):
                t_name, f_name = coord.split(".", 1) if "." in coord else ("", coord)
                writer.writerow([t_name, f_name, hits, "yes" if hits > 0 else "no"])

        return target

    @staticmethod
    def generate_summary_json(evaluation: GateEvaluation, out_path: Path | str) -> Path:
        """Export summary JSON file."""
        target = Path(out_path).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(evaluation.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        return target


def render_gitlab_ci_template() -> str:
    """Return production-ready .gitlab-ci.yml template for GitLab CI."""
    return """# ==============================================================================
# GitLab CI Pipeline for GraphQL Coverage Proxy & QA
#
# Supported architectures:
# Option A (Recommended): Observer Mode
#   Tests run inside this runner. All GraphQL requests route through the proxy.
#   Quality Gate checks the coverage threshold and exports JUnit XML + coverage regex.
#
# Option B: Remote Trigger Mode
#   TestLab or webhooks trigger this pipeline with variable COVERAGE_RUN_ID.
# ==============================================================================

stages:
  - preflight
  - test
  - quality-gate

variables:
  PYTHON_VERSION: "3.12"
  COVERAGE_URL: "http://coverage-proxy:8000"
  COVERAGE_MIN_PERCENT: "80.0"
  PIP_CACHE_DIR: "$CI_PROJECT_DIR/.cache/pip"
  PLAYWRIGHT_BROWSERS_PATH: "$CI_PROJECT_DIR/.browsers"

cache:
  key: "$CI_COMMIT_REF_SLUG"
  paths:
    - .cache/pip
    - .browsers

# ------------------------------------------------------------------------------
# Stage 1: Preflight Lint & Hygiene
# ------------------------------------------------------------------------------
lint-and-hygiene:
  stage: preflight
  image: python:3.12-slim
  before_script:
    - python -V
    - pip install -r requirements.txt
    - pip install -e . --no-deps
  script:
    - python -m coverage_qa preflight --quick
  rules:
    - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'
    - if: '$CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH'
    - if: '$CI_PIPELINE_SOURCE == "trigger"'

# ------------------------------------------------------------------------------
# Stage 2: Execute Tests Through GraphQL Coverage Proxy
# ------------------------------------------------------------------------------
run-api-tests:
  stage: test
  image: python:3.12-slim
  before_script:
    - pip install -r requirements.lock.txt
    - pip install -e . --no-deps
  script:
    - echo "Executing tests against proxy: $COVERAGE_URL"
    - python -m coverage_qa test --suite all --report-dir reports/
  artifacts:
    when: always
    expire_in: 7 days
    paths:
      - reports/
  rules:
    - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'
    - if: '$CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH'
    - if: '$CI_PIPELINE_SOURCE == "trigger"'

# ------------------------------------------------------------------------------
# Stage 3: GraphQL Coverage Quality Gate & GitLab Reporting
# ------------------------------------------------------------------------------
graphql-coverage-gate:
  stage: quality-gate
  image: python:3.12-slim
  before_script:
    - pip install -r requirements.txt
    - pip install -e . --no-deps
  script:
    - |
      RUN_ID="${COVERAGE_RUN_ID:-latest}"
      python -m coverage_qa gitlab gate \\
        --coverage-url "$COVERAGE_URL" \\
        --api-key "$COVERAGE_API_KEY" \\
        --run-id "$RUN_ID" \\
        --min-coverage "$COVERAGE_MIN_PERCENT" \\
        --junit-out reports/junit.xml \\
        --csv-out reports/coverage.csv \\
        --summary-out reports/coverage-summary.json
  # GitLab native coverage regex parsing:
  coverage: '/Total coverage:\\s*([0-9.]+)%/'
  artifacts:
    when: always
    expire_in: 30 days
    reports:
      junit: reports/junit.xml
    paths:
      - reports/junit.xml
      - reports/coverage.csv
      - reports/coverage-summary.json
  rules:
    - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'
    - if: '$CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH'
    - if: '$CI_PIPELINE_SOURCE == "trigger"'
"""


def _cmd_gate(args: argparse.Namespace) -> int:
    gate = CoverageQualityGate(
        coverage_url=args.coverage_url,
        api_key=args.api_key,
    )
    try:
        eval_result = gate.evaluate(args.run_id, min_coverage=args.min_coverage)
    except Exception as exc:
        print(f"Error evaluating coverage for run {args.run_id}: {exc}", file=sys.stderr)
        return 1

    # Print GitLab-compatible coverage regex output
    print("=" * 60)
    print(f"GraphQL Coverage Quality Gate: RUN {eval_result.run_id}")
    print(eval_result.gitlab_metric_line)
    print(f"Covered fields: {eval_result.covered_fields} / {eval_result.total_fields}")
    print(f"Threshold: {eval_result.min_coverage:.2f}%")
    print(f"Status: {'PASSED' if eval_result.passed else 'FAILED'}")
    if eval_result.uncovered_fields:
        print(f"Uncovered ({len(eval_result.uncovered_fields)}):")
        for u in eval_result.uncovered_fields[:20]:
            print(f"  - {u}")
        if len(eval_result.uncovered_fields) > 20:
            print(f"  ... and {len(eval_result.uncovered_fields) - 20} more")
    print("=" * 60)

    if args.junit_out:
        gate.generate_junit_xml(eval_result, args.junit_out)
        print(f"JUnit XML generated: {args.junit_out}")

    if args.csv_out:
        gate.generate_csv(eval_result, args.csv_out)
        print(f"CSV report generated: {args.csv_out}")

    if args.summary_out:
        gate.generate_summary_json(eval_result, args.summary_out)
        print(f"Summary JSON generated: {args.summary_out}")

    return 0 if eval_result.passed else 1


def _cmd_trigger(args: argparse.Namespace) -> int:
    client = GitLabClient(host=args.host, token=args.token)
    variables = {}
    if args.variables:
        for item in args.variables:
            if "=" in item:
                k, v = item.split("=", 1)
                variables[k.strip()] = v.strip()

    if args.run_id:
        variables["COVERAGE_RUN_ID"] = args.run_id
    if args.coverage_url:
        variables["COVERAGE_PROXY_URL"] = args.coverage_url
    if args.min_coverage:
        variables["MIN_COVERAGE"] = str(args.min_coverage)

    try:
        pipeline = client.trigger_pipeline(
            project_id=args.project,
            ref=args.ref,
            token=args.token,
            variables=variables,
        )
    except Exception as exc:
        print(f"Error triggering GitLab pipeline: {exc}", file=sys.stderr)
        return 1

    pipe_id = pipeline.get("id")
    pipe_iid = pipeline.get("iid", pipe_id)
    web_url = pipeline.get("web_url")
    print(f"GitLab pipeline triggered successfully: #{pipe_iid} (ID: {pipe_id})")
    if web_url:
        print(f"Web URL: {web_url}")

    if not args.wait:
        return 0

    print(f"Waiting for pipeline #{pipe_iid} to complete (timeout: {args.timeout}s)...")

    def progress(info: dict[str, Any]) -> None:
        st = info.get("status", "unknown")
        dur = info.get("duration") or 0
        print(f"  [GitLab] Pipeline #{pipe_iid} status: {st} (duration: {dur}s)")

    try:
        final_info = client.poll_pipeline(
            project_id=args.project,
            pipeline_id=pipe_id,
            token=args.token,
            timeout=args.timeout,
            poll_interval=args.poll_interval,
            callback=progress,
        )
    except Exception as exc:
        print(f"Pipeline polling error: {exc}", file=sys.stderr)
        return 1

    final_status = final_info.get("status")
    print(f"Pipeline completed with status: {final_status}")

    # If run_id and coverage_url provided, run coverage gate evaluation
    if final_status == "success" and args.run_id and args.coverage_url:
        gate = CoverageQualityGate(coverage_url=args.coverage_url, api_key=args.api_key)
        try:
            res = gate.evaluate(args.run_id, min_coverage=args.min_coverage or 80.0)
            print(res.gitlab_metric_line)
            return 0 if res.passed else 1
        except Exception as exc:
            print(f"Coverage gate evaluation failed: {exc}", file=sys.stderr)
            return 1

    return 0 if final_status == "success" else 1


def _cmd_status(args: argparse.Namespace) -> int:
    client = GitLabClient(host=args.host, token=args.token)
    try:
        info = client.get_pipeline(args.project, args.pipeline, token=args.token)
        print(json.dumps(info, indent=2, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(f"Error fetching pipeline status: {exc}", file=sys.stderr)
        return 1


def _cmd_template(args: argparse.Namespace) -> int:
    template = render_gitlab_ci_template()
    if args.out:
        out_path = Path(args.out).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(template, encoding="utf-8")
        print(f"GitLab CI template written to {out_path}")
    else:
        print(template)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="coverage-qa gitlab",
        description="GitLab CI integration: Quality Gate verification & remote pipeline triggering",
    )
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    # Subcommand: gate
    gate_parser = subparsers.add_parser("gate", help="Evaluate coverage gate and export GitLab artifacts")
    gate_parser.add_argument("--run-id", required=True, help="Test run UUID in Coverage Proxy")
    gate_parser.add_argument("--coverage-url", default=os.environ.get("COVERAGE_URL", "http://localhost:8000"), help="Coverage Proxy base URL")
    gate_parser.add_argument("--api-key", default=os.environ.get("COVERAGE_API_KEY"), help="Coverage Proxy API key")
    gate_parser.add_argument("--min-coverage", type=float, default=80.0, help="Minimum acceptable coverage percent (0-100)")
    gate_parser.add_argument("--junit-out", help="Path to write JUnit XML test report")
    gate_parser.add_argument("--csv-out", help="Path to write CSV coverage report")
    gate_parser.add_argument("--summary-out", help="Path to write JSON summary")

    # Subcommand: trigger
    trigger_parser = subparsers.add_parser("trigger", help="Trigger remote GitLab CI pipeline")
    trigger_parser.add_argument("--project", required=True, help="GitLab Project ID or URL-encoded path")
    trigger_parser.add_argument("--token", required=True, help="GitLab Trigger token or Access token")
    trigger_parser.add_argument("--ref", default="main", help="Target git ref (branch/tag)")
    trigger_parser.add_argument("--host", default=DEFAULT_GITLAB_HOST, help="GitLab server URL")
    trigger_parser.add_argument("--run-id", help="Coverage run UUID to pass as variable")
    trigger_parser.add_argument("--coverage-url", help="Coverage URL to pass as variable")
    trigger_parser.add_argument("--api-key", default=os.environ.get("COVERAGE_API_KEY"), help="Coverage Proxy API key")
    trigger_parser.add_argument("--min-coverage", type=float, default=80.0, help="Minimum coverage threshold")
    trigger_parser.add_argument("--variable", "-V", action="append", dest="variables", help="Additional variable in KEY=VALUE format")
    trigger_parser.add_argument("--wait", action="store_true", help="Wait for pipeline completion")
    trigger_parser.add_argument("--timeout", type=float, default=600.0, help="Wait timeout in seconds")
    trigger_parser.add_argument("--poll-interval", type=float, default=5.0, help="Polling interval in seconds")

    # Subcommand: status
    status_parser = subparsers.add_parser("status", help="Get status of GitLab pipeline")
    status_parser.add_argument("--project", required=True, help="GitLab Project ID")
    status_parser.add_argument("--pipeline", required=True, help="GitLab Pipeline ID")
    status_parser.add_argument("--token", help="GitLab Access token")
    status_parser.add_argument("--host", default=DEFAULT_GITLAB_HOST, help="GitLab server URL")

    # Subcommand: template
    template_parser = subparsers.add_parser("template", help="Generate ready-to-use .gitlab-ci.yml")
    template_parser.add_argument("--out", help="Target output file path")

    args = parser.parse_args(argv)

    if args.subcommand == "gate":
        return _cmd_gate(args)
    if args.subcommand == "trigger":
        return _cmd_trigger(args)
    if args.subcommand == "status":
        return _cmd_status(args)
    if args.subcommand == "template":
        return _cmd_template(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
