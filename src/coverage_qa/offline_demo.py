import argparse
import html
import json
import os
import shutil
import socket
import sys
from pathlib import Path

import httpx

from coverage_qa.environment import ROOT


VPS_ENDPOINTS = [
    ("Healthcheck", "https://graphql.rebyte.fun/health", 200),
    ("Coverage Proxy UI", "https://graphql.rebyte.fun/coverage/", 200),
    ("TestLab QA Panel", "https://graphql.rebyte.fun/qa/", 401),
    ("FORMA Demo Shop", "https://graphql.rebyte.fun/shop/", 200),
]

LOCAL_SERVICES = [
    ("TestLab QA Panel", 9020),
    ("FORMA Demo Shop", 9110),
    ("Commerce API", 9100),
    ("Coverage Proxy", 9000),
]


def check_port(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


def check_connectivity() -> dict:
    vps_results = []
    with httpx.Client(timeout=3.0, trust_env=False) as client:
        for name, url, expected_status in VPS_ENDPOINTS:
            try:
                resp = client.get(url)
                ok = (resp.status_code == expected_status)
                vps_results.append({
                    "name": name, "url": url, "status": resp.status_code, "ok": ok, "error": None
                })
            except Exception as exc:
                vps_results.append({
                    "name": name, "url": url, "status": None, "ok": False, "error": str(exc)
                })

    local_results = []
    for name, port in LOCAL_SERVICES:
        open_ = check_port(port)
        local_results.append({"name": name, "port": port, "open": open_})

    vps_alive = sum(r["ok"] for r in vps_results) >= 2
    return {
        "vps_alive": vps_alive,
        "vps": vps_results,
        "local": local_results,
    }


def bundle_offline_showcase(output_dir: Path | str = None, *, source_dir: Path | str = None) -> Path:
    from coverage_qa.reporting.metrics import graph_field_hits

    target = Path(output_dir).resolve() if output_dir else ROOT / "reports" / "offline-showcase"
    if source_dir is None:
        candidates = [
            path for pattern in ("mvp/*/coverage.json", "runs/*/results.json")
            for path in (ROOT / "reports").glob(pattern)
            if (path.parent / "index.html").is_file()
        ]
        source = max(candidates, key=lambda path: path.stat().st_mtime).parent if candidates else None
    else:
        source = Path(source_dir).resolve()
    if source and (target == source or target.is_relative_to(source) or source.is_relative_to(target)):
        raise ValueError("Source report and bundle directory must be separate")

    data = {}
    if source:
        source_file = next((source / name for name in ("results.json", "coverage.json")
                            if (source / name).is_file()), None)
        if source_file is None or not (source / "index.html").is_file():
            raise ValueError("Source must contain index.html and results.json or coverage.json")
        data = json.loads(source_file.read_text(encoding="utf-8"))

    target.mkdir(parents=True, exist_ok=True)
    if source:
        destination = target / "report"
        if destination.is_symlink():
            raise ValueError("Report destination must not be a symbolic link")
        if destination.exists():
            resolved = destination.resolve()
            if resolved.parent != target or resolved.name != "report":
                raise ValueError("Invalid report destination")
            shutil.rmtree(resolved)
        shutil.copytree(source, destination, ignore=shutil.ignore_patterns("*.log", "*.zip"),
                        symlinks=False)

        copied_index = destination / "index.html"
        copied_index.write_text(copied_index.read_text(encoding="utf-8").replace(
            'href="../../index.html"', 'href="../showcase.html"'), encoding="utf-8")

    graphs = []
    if "types" in data:
        graphs.append(data)
    for run in data.get("coverageRuns", []):
        checkpoints = run.get("checkpoints") or []
        graph = checkpoints[-1].get("graph") if checkpoints else run.get("graph")
        if graph and "types" in graph:
            graphs.append(graph)

    rows = []
    metrics = []
    for graph in graphs:
        hits = graph_field_hits(graph)
        total = len(hits)
        covered = sum(count > 0 for count in hits.values())
        percent = round(covered * 100 / total, 2) if total else 0.0
        item = {"runId": graph.get("runId"), "schemaId": graph.get("schemaId"),
                "coveredFields": covered, "totalFields": total, "coveragePercent": percent}
        metrics.append(item)
        rows.append(f"<tr><td>{html.escape(str(item['runId'] or '—'))}</td>"
                    f"<td>{html.escape(str(item['schemaId'] or '—'))}</td>"
                    f"<td>{covered} / {total}</td><td>{percent:.2f}%</td></tr>")

    tests = data.get("tests", [])
    test_rows = "".join(
        f"<tr><td>{html.escape(str(test.get('title', test.get('id', '—'))))}</td>"
        f"<td>{html.escape(str(test.get('status', 'unknown')))}</td></tr>" for test in tests
    )
    passed = sum(test.get("status") == "passed" for test in tests)
    result = f"Сценариев пройдено {passed} / {len(tests)}." if tests else "Сведения о сценариях отсутствуют."
    links = []
    if source:
        for filename, label in (("index.html", "Отчёт"), ("coverage.csv", "Coverage CSV"),
                                ("test-matrix.csv", "Матрица тестов"),
                                ("server-test-coverage.json", "Test Coverage JSON")):
            if (target / "report" / filename).is_file():
                links.append(f'<a href="report/{filename}">{label}</a>')
    identity = source.name if source else None
    manifest = {"sourceId": identity, "startedAt": data.get("startedAt"),
                "finishedAt": data.get("finishedAt"), "backend": data.get("backend"),
                "coverageRuns": metrics, "tests": tests, "exitCode": data.get("exitCode")}
    (target / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                                        encoding="utf-8")
    state = f"Источник {html.escape(identity)}" if identity else "Нет сохранённого прогона"
    timing = html.escape(str(data.get("finishedAt") or data.get("startedAt") or "Время в источнике не указано"))
    source_html = f"""<!doctype html>
<html lang="ru"><meta charset="utf-8"><title>GraphQL Coverage Proxy — План Б</title>
<style>body{{font:16px system-ui;max-width:1100px;margin:40px auto;padding:24px;color:#203048}}
table{{width:100%;border-collapse:collapse;margin:24px 0}}td,th{{text-align:left;padding:12px;border-bottom:1px solid #ccc}}
a{{display:inline-block;margin-right:24px}}code{{overflow-wrap:anywhere}}</style>
<h1>GraphQL Coverage Proxy — План Б</h1><p>{state}</p><p>{timing}</p>
<p>{' '.join(links)}</p><h2>Покрытие сохранённых прогонов</h2>
<table><thead><tr><th>Прогон</th><th>Схема</th><th>Покрыто полей</th><th>Покрытие</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table>
<p>{'Метрики покрытия в источнике отсутствуют.' if not rows else ''}</p>
<h2>Результаты сценариев</h2><p>{result}</p>
<table><thead><tr><th>Сценарий</th><th>Статус</th></tr></thead><tbody>{test_rows}</tbody></table>
</html>"""
    (target / "showcase.html").write_text(source_html, encoding="utf-8")
    return target


def print_status(status: dict):
    print("=" * 64)
    print("  GRAPHQL COVERAGE PROXY · CONNECTIVITY & PLAN B CHECK")
    print("=" * 64)

    print("\n[УДАЛЁННЫЙ VPS СТЕНД: graphql.rebyte.fun]")
    for item in status["vps"]:
        symbol = "✓" if item["ok"] else "✗"
        status_text = f"HTTP {item['status']}" if item["status"] else f"FAIL ({item['error']})"
        print(f"  {symbol} {item['name']:24} : {status_text}")

    print("\n[ЛОКАЛЬНЫЙ СТЕНД (ПЛАН Б: 127.0.0.1)]")
    for item in status["local"]:
        symbol = "✓" if item["open"] else "○"
        state_text = f"OPEN (http://127.0.0.1:{item['port']}/)" if item["open"] else "STOPPED"
        print(f"  {symbol} {item['name']:24} : {state_text}")

    print("\n" + "-" * 64)
    if status["vps_alive"]:
        print("  ✓ VPS СТЕНД ДОСТУПЕН: можно проводить онлайн-демонстрацию.")
    else:
        print("  ⚠ ВНИМАНИЕ: VPS СТЕНД НЕДОСТУПЕН ИЛИ НЕСТАБИЛЕН!")
        print("  → Активируйте План Б (офлайн-показ):")
        print("     1. Откройте reports/offline-showcase/showcase.html")
        print("     2. Либо запустите локальный стек: coverage-qa panel --port 9020")
    print("=" * 64)


def main():
    parser = argparse.ArgumentParser(description="Plan B offline demo tools, health checker, and showcase bundle generator.")
    parser.add_argument("action", choices=["check", "bundle", "serve"], default="check", nargs="?",
                        help="Action to perform: check (default), bundle, or serve")
    parser.add_argument("--port", type=int, default=9030, help="Port for serve action (default: 9030)")

    parser.add_argument("--source", type=Path, help="Directory of a saved run")
    parser.add_argument("--output", type=Path, help="Output bundle directory")
    args = parser.parse_args()

    if args.action == "check":
        status = check_connectivity()
        print_status(status)
        return 0 if status["vps_alive"] else 1

    elif args.action == "bundle":
        out = bundle_offline_showcase(args.output, source_dir=args.source)
        print(f"✓ Offline showcase bundled successfully at:\n  {out / 'showcase.html'}")
        return 0

    elif args.action == "serve":
        bundle_dir = bundle_offline_showcase(args.output, source_dir=args.source)
        from http.server import HTTPServer, SimpleHTTPRequestHandler
        os.chdir(bundle_dir)
        server = HTTPServer(("127.0.0.1", args.port), SimpleHTTPRequestHandler)
        print(f"Serving offline showcase at: http://127.0.0.1:{args.port}/showcase.html")
        print("Press Ctrl+C to stop.")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nStopped.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
