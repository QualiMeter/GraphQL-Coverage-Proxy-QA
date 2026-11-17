from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote_plus
from uuid import UUID
from xml.sax.saxutils import escape

import httpx

from coverage_qa.reporting.metrics import graph_field_hits

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
        return f"Total coverage: {self.coverage_percent:.2f}%"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class GitLabClient:

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
        return quote_plus(str(project_id).strip())

    def trigger_pipeline(
        self,
        project_id: str | int,
        ref: str = "main",
        token: str | None = None,
        variables: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        auth_token = token or self.token
        if not auth_token:
            raise ValueError("GitLab token is required to trigger a pipeline")

        pid = self._normalize_project_id(project_id)
        vars_dict = variables or {}

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

    def __init__(
        self,
        coverage_url: str = "http://localhost:8000",
        api_key: str | None = None,
        timeout: float = 15.0,
    ) -> None:
        self.coverage_url = (coverage_url or "http://localhost:8000").rstrip("/")
        self.api_key = os.environ.get("COVERAGE_API_KEY", "") if api_key is None else api_key
        self.timeout = timeout

    def fetch_coverage(self, run_id: str) -> dict[str, Any]:
        expected_id = str(UUID(run_id))
        url = f"{self.coverage_url}/api/runs/{expected_id}/coverage"
        headers = {}
        if self.api_key:
            headers["X-Coverage-Api-Key"] = self.api_key

        with httpx.Client(timeout=self.timeout, trust_env=False) as client:
            resp = client.get(url, headers=headers)
            resp.raise_for_status()
            graph = resp.json()
            if graph.get("runId", graph.get("run_id")) != expected_id:
                raise ValueError("Coverage response belongs to another run")
            return graph

    def evaluate(self, run_id: str, min_coverage: float = 80.0) -> GateEvaluation:
        data = self.fetch_coverage(run_id)

        return self.evaluate_graph(data, run_id, min_coverage)

    @staticmethod
    def evaluate_graph(data: dict[str, Any], run_id: str, min_coverage: float) -> GateEvaluation:
        if not 0 <= min_coverage <= 100:
            raise ValueError("min-coverage must be between 0 and 100")
        field_hits = graph_field_hits(data)
        covered_fields = [coordinate for coordinate, count in field_hits.items() if count > 0]
        uncovered_fields = [coordinate for coordinate, count in field_hits.items() if count == 0]

        summary = data.get("summary") or {}
        total = summary.get("totalFields", summary.get("total_fields", len(field_hits)))
        covered = summary.get("coveredFields", summary.get("covered_fields", len(covered_fields)))

        if total != len(field_hits) or covered != len(covered_fields):
            raise ValueError("Coverage summary does not match the fields")
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
            service_id=str(data.get("serviceId") or data.get("service_id") or "") or None,
            schema_id=str(data.get("schemaId") or data.get("schema_id") or "") or None,
        )

    @staticmethod
    def generate_junit_xml(evaluation: GateEvaluation, out_path: Path | str) -> Path:
        target = Path(out_path).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)

        failures_count = 0 if evaluation.passed else 1
        uncovered_preview = ", ".join(evaluation.uncovered_fields[:15])
        if len(evaluation.uncovered_fields) > 15:
            uncovered_preview += f" ... (+{len(evaluation.uncovered_fields) - 15} more)"

        lines = [
            '<?xml version="1.0" encoding="utf-8"?>',
            f'<testsuites name="GraphQL Coverage Gate" tests="1" failures="{failures_count}" errors="0" time="0.05">',
            f'  <testsuite name="gitlab.coverage_gate" tests="1" failures="{failures_count}" errors="0" time="0.05">',
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
            '  </testsuite>',
            '</testsuites>',
        ])

        content = "\n".join(lines) + "\n"
        target.write_text(content, encoding="utf-8")
        return target

    @staticmethod
    def generate_csv(evaluation: GateEvaluation, out_path: Path | str) -> Path:
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
        target = Path(out_path).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(evaluation.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        return target


def render_gitlab_ci_template() -> str:
    from sysconfig import get_path
    checkout = Path(__file__).resolve().parents[2] / ".gitlab-ci.yml"
    installed = Path(get_path("data")) / "share/graphql-coverage-qa/.gitlab-ci.yml"
    return (checkout if checkout.is_file() else installed).read_text(encoding="utf-8")


def _cmd_gate(args: argparse.Namespace) -> int:
    gate = CoverageQualityGate(
        coverage_url=args.coverage_url,
        api_key=args.api_key,
    )
    try:
        if args.graph_file:
            graph = json.loads(args.graph_file.read_text(encoding="utf-8"))
            run_id = str(UUID(graph["runId"]))
            eval_result = gate.evaluate_graph(graph, run_id, args.min_coverage)
        else:
            eval_result = gate.evaluate(args.run_id, min_coverage=args.min_coverage)
    except Exception as exc:
        print(f"Error evaluating coverage for run {args.run_id}: {exc}", file=sys.stderr)
        return 1

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
    if args.min_coverage is not None:
        variables["COVERAGE_MIN_PERCENT"] = str(args.min_coverage)

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

    if final_status == "success" and args.run_id and args.coverage_url:
        gate = CoverageQualityGate(coverage_url=args.coverage_url, api_key=args.api_key)
        try:
            res = gate.evaluate(args.run_id, min_coverage=80.0 if args.min_coverage is None else args.min_coverage)
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

    gate_parser = subparsers.add_parser("gate", help="Evaluate coverage gate and export GitLab artifacts")
    source = gate_parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--graph-file", type=Path, help="Saved coverage response for this pipeline")
    source.add_argument("--run-id", help="Test run UUID in Coverage Proxy")
    gate_parser.add_argument("--coverage-url", default=os.environ.get("COVERAGE_URL", "http://localhost:8000"), help="Coverage Proxy base URL")
    gate_parser.add_argument("--api-key", default=os.environ.get("COVERAGE_API_KEY"), help="Coverage Proxy API key")
    gate_parser.add_argument("--min-coverage", type=float, default=80.0, help="Minimum acceptable coverage percent (0-100)")
    gate_parser.add_argument("--junit-out", help="Path to write JUnit XML test report")
    gate_parser.add_argument("--csv-out", help="Path to write CSV coverage report")
    gate_parser.add_argument("--summary-out", help="Path to write JSON summary")

    trigger_parser = subparsers.add_parser("trigger", help="Trigger remote GitLab CI pipeline")
    trigger_parser.add_argument("--project", required=True, help="GitLab Project ID or URL-encoded path")
    trigger_parser.add_argument("--token", required=True, help="GitLab Trigger token or Access token")
    trigger_parser.add_argument("--ref", default="main", help="Target git ref (branch/tag)")
    trigger_parser.add_argument("--host", default=DEFAULT_GITLAB_HOST, help="GitLab server URL")
    trigger_parser.add_argument("--run-id", help="Coverage run UUID to pass as variable")
    trigger_parser.add_argument("--coverage-url", help="Coverage URL for an additional local gate after --wait")
    trigger_parser.add_argument("--api-key", default=os.environ.get("COVERAGE_API_KEY"), help="Coverage Proxy API key")
    trigger_parser.add_argument("--min-coverage", type=float, default=80.0, help="Minimum coverage threshold")
    trigger_parser.add_argument("--variable", "-V", action="append", dest="variables", help="Additional variable in KEY=VALUE format")
    trigger_parser.add_argument("--wait", action="store_true", help="Wait for pipeline completion")
    trigger_parser.add_argument("--timeout", type=float, default=600.0, help="Wait timeout in seconds")
    trigger_parser.add_argument("--poll-interval", type=float, default=5.0, help="Polling interval in seconds")

    status_parser = subparsers.add_parser("status", help="Get status of GitLab pipeline")
    status_parser.add_argument("--project", required=True, help="GitLab Project ID")
    status_parser.add_argument("--pipeline", required=True, help="GitLab Pipeline ID")
    status_parser.add_argument("--token", help="GitLab Access token")
    status_parser.add_argument("--host", default=DEFAULT_GITLAB_HOST, help="GitLab server URL")

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
