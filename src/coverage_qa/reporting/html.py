import csv
import html
import io
import json
from pathlib import Path
from coverage_qa.reporting.metrics import graph_field_hits
from coverage_qa.reporting.test_coverage import execution_html, server_executions, server_matrix_csv


def escape(value):
    return html.escape(str(value))


def pretty(value):
    return escape(json.dumps(value, ensure_ascii=False, indent=2))


def final_measurement(run):
    checkpoints = run.get("checkpoints") or []
    snapshot = checkpoints[-1] if checkpoints else run
    graph = snapshot.get("graph") or {}
    percent = graph.get("summary", snapshot.get("summary", {})).get("coveragePercent")
    if "types" in graph:
        hits = graph_field_hits(graph)
    else:
        hits = snapshot.get("fieldHits", snapshot.get("finalFieldHits"))
    return hits, percent


def run_owners(report):
    tests = report.get("tests", [])
    known_ids = {test["id"] for test in tests}
    observed = {}
    for test in tests:
        for event in test.get("events", []):
            if event.get("kind") == "coverage":
                observed.setdefault(event["runId"], set()).add(test["id"])
    owners = {}
    for run in report.get("coverageRuns", []):
        rid = run["runId"]
        if run.get("testId") in known_ids:
            owners[rid] = run["testId"]
        elif len(observed.get(rid, ())) == 1:
            owners[rid] = next(iter(observed[rid]))
    return owners


def generate_coverage_csv(report: dict) -> str:
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["run_id", "test_id", "run_name", "type_name", "field_name", "coordinate", "hits", "covered", "coverage_percent"])
    owners = run_owners(report)
    for run in report.get("coverageRuns", []):
        rid = run["runId"]
        hits, percent = final_measurement(run)
        for coord, count in sorted((hits or {}).items()):
            type_name, _, field_name = coord.partition(".")
            writer.writerow([rid, owners.get(rid, ""), run.get("name", ""), type_name, field_name,
                             coord, count, count > 0, percent])
    return output.getvalue()


def generate_test_matrix_csv(report: dict) -> str:
    if server_executions(report):
        return server_matrix_csv(report)
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["test_id", "test_title", "status", "duration_sec", "run_id", "covered_fields_count", "covered_coordinates"])
    tests = {test["id"]: test for test in report.get("tests", [])}
    owners = run_owners(report)
    for run in report.get("coverageRuns", []):
        rid = run["runId"]
        owner = owners.get(rid, "")
        test = tests.get(owner, {})
        hits, _ = final_measurement(run)
        selected = sorted(coord for coord, count in (hits or {}).items() if count)
        duration = round(test["duration"], 3) if "duration" in test else None
        writer.writerow([owner, test.get("title"), test.get("status"), duration, rid,
                         len(selected) if hits is not None else None, "; ".join(selected)])
    linked = set(owners.values())
    for owner, test in tests.items():
        if owner not in linked:
            writer.writerow([owner, test.get("title"), test.get("status"), round(test.get("duration", 0), 3), "", "", ""])
    return output.getvalue()


def write_report(report, path: Path):
    path = Path(path)
    csv_text = generate_coverage_csv(report)
    path.with_name("coverage.csv").write_text(csv_text, encoding="utf-8")
    matrix_text = generate_test_matrix_csv(report)
    path.with_name("test-matrix.csv").write_text(matrix_text, encoding="utf-8")
    path.with_name("server-test-coverage.json").write_text(
        json.dumps({"source": "backend", "executions": server_executions(report)}, ensure_ascii=False, indent=2), encoding="utf-8")

    tests = report["tests"]
    counts = {s: sum(t["status"] == s for t in tests) for s in ("passed", "failed", "skipped")}
    requests = sum(e["kind"] == "request" for t in tests for e in t["events"])
    checks = [e for t in tests for e in t["events"] if e["kind"] == "check"]
    labels = {"passed": "Пройден", "failed": "Ошибка", "skipped": "Пропущен"}
    cards = []
    for test in tests:
        events = []
        for event in test["events"]:
            if event["kind"] == "request":
                events.append(f'''<details class="request"><summary>↗ {escape(event['title'])}
                  <span>HTTP {escape(event['httpStatus'])} · {event['durationMs']} мс</span></summary>
                  <p><code>{escape(event.get('method', 'POST'))} {escape(event.get('url', ''))}</code></p>
                  <div class="columns"><div><h4>Запрос</h4><pre>{pretty(event['request'])}</pre><h4>Заголовки (секреты скрыты)</h4><pre>{pretty(event.get('headers', {}))}</pre></div>
                  <div><h4>Ответ</h4><pre>{pretty(event['response'])}</pre></div></div>
                  {f'<p>Наблюдение: <code>{escape(event.get("observedRequestId"))}</code> · countedInCoverage: {escape(event.get("countedInCoverage"))}</p><h4>Фактические поля этого запроса</h4><pre>{pretty(event["actualSelectedFields"])}</pre>' if 'actualSelectedFields' in event else ''}
                  {f'<pre class="failure">{escape(event["error"])}</pre>' if event['error'] else ''}</details>''')
            elif event["kind"] == "coverage":
                fields = ''.join(f'<tr><td>{escape(name)}</td><td>{hits}</td></tr>' for name, hits in sorted(event["fieldHits"].items()))
                events.append(f'<details class="request"><summary>Поля схемы: {escape(event["title"])} · {event["summary"]["coveragePercent"]}%</summary><p>runId: <code>{escape(event["runId"])}</code></p><table><tr><th>Поле</th><th>Hits</th></tr>{fields}</table></details>')
            elif event["kind"] == "attachment":
                path_link = escape(event["path"])
                preview = f'<img src="{path_link}" alt="Результат браузерного теста" style="max-width:100%">' if path_link.endswith('.png') else ''
                events.append(f'<p><a href="{path_link}">{escape(event["title"])}</a></p>{preview}')
            elif event["kind"] == "check":
                status = "pass" if event["passed"] else "fail"
                events.append(f'''<div class="check {status}"><strong>{'✓' if event['passed'] else '✗'} {escape(event['title'])}</strong>
                  <div class="values"><div>Ожидание<pre>{pretty(event['expected'])}</pre></div>
                  <div>Факт<pre>{pretty(event['actual'])}</pre></div></div></div>''')
        failure = f'<h4>Причина</h4><pre class="failure">{escape(test["failure"])}</pre>' if test["failure"] else ""
        transport = f'<p>Транспорт: <strong>{escape(test["transport"])}</strong> · <code>{escape(test.get("target", ""))}</code></p>' if test.get("transport") else ''
        cards.append(f'''<details class="case {test['status']}" {'open' if test['status'] == 'failed' else ''}>
          <summary><span class="pill">{labels[test['status']]}</span> {escape(test['title'])}
          <span class="duration">{test['duration']:.2f} с</span></summary>
          <div class="content"><code>{escape(test['id'])}</code>{transport}{execution_html(test.get('serverTestCoverage', []))}{''.join(events)}{failure}</div></details>''')
    passed_checks = sum(e["passed"] for e in checks)
    contexts = []
    for run in report.get("coverageRuns", []):
        selected = sorted(k for k, v in run.get("finalFieldHits", {}).items() if v)
        contexts.append(f'<tr><td>{escape(run.get("name", ""))}</td><td><code>{escape(run["testId"])}</code></td><td><code>{escape(run["runId"])}</code></td><td>{len(selected)}</td><td>{escape(", ".join(selected))}</td></tr>')
    coverage_section = ('<h2>Сохранённые прогоны и поля</h2><p>Данные получены из API покрытия. Связь теста с прогоном сохранена в field-usage.json. declaredSelectedFields в нём — ожидание теста; checkpoints — фактические агрегаты сервера. Значения полей ответа могут не быть проверены даже при наличии покрытия.</p><div style="overflow:auto"><table><tr><th>Прогон</th><th>Тест</th><th>runId</th><th>Полей</th><th>Покрытые координаты</th></tr>' + ''.join(contexts) + '</table></div>') if contexts else ''
    infra = ''.join(f'<pre class="failure">{escape(error)}</pre>' for error in report.get("infrastructureErrors", []))
    infra += ''.join(f'<p>{escape(warning)}</p>' for warning in report.get("liveWarnings", []))
    source = f'''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
      <title>API tests · отчёт</title><style>
      *{{box-sizing:border-box}}body{{margin:0;background:#f3f5f8;color:#203048;font:15px/1.5 system-ui}}main{{max-width:1250px;margin:auto;padding:36px 24px}}header{{border-top:5px solid #315bdb;padding-top:20px}}h1{{font-size:36px;margin:8px 0}}p,.muted{{color:#65738a}}.metrics{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:24px 0}}.metric{{background:white;padding:18px;border:1px solid #dce3ec;border-radius:10px}}.metric b{{font-size:30px;display:block}}.toolbar{{margin:24px 0}}button{{border:1px solid #ccd6e6;border-radius:6px;background:white;color:#23416d;padding:9px 16px;cursor:pointer;margin:4px}}button[aria-pressed=true]{{background:#23416d;color:white}}.case{{border:1px solid #dce3ec;border-left:4px solid #27966e;border-radius:8px;background:white;margin:12px 0}}.case.failed{{border-left-color:#d84252}}.case.skipped{{border-left-color:#b08a20}}summary{{cursor:pointer;padding:17px;list-style-position:inside}}.case>summary{{font-weight:650}}.pill{{font-size:12px;padding:4px 8px;background:#e5f5ed;color:#226b4b;border-radius:5px;margin-right:10px}}.failed .pill{{color:#ac2238;background:#ffeaed}}.skipped .pill{{color:#8a6415;background:#fff4cf}}.duration,.request summary span{{float:right;font-size:12px;color:#6b7990}}.content{{padding:0 20px 20px}}code{{font:12px Consolas,monospace;overflow-wrap:anywhere}}pre{{font:12px/1.6 Consolas,monospace;white-space:pre-wrap;overflow-wrap:anywhere;max-height:380px;overflow:auto;background:#eef2f7;border-radius:5px;padding:12px;margin:5px 0}}.request{{margin:14px 0;background:#f8faff;border:1px solid #dce3ec;border-radius:6px}}.columns,.values{{display:grid;grid-template-columns:1fr 1fr;gap:14px;padding:0 12px 12px}}h4{{margin:10px 0}}.check{{border-top:1px solid #e8edf3;padding:12px 0}}.check strong{{font-size:14px}}.pass strong{{color:#227b55}}.fail strong{{color:#ba2840}}.values{{padding:5px 0 0;font-size:12px;color:#65738a}}.failure{{background:#fff0f1;color:#9e2438}}footer{{padding:24px 0;color:#65738a;font-size:13px}}@media(max-width:650px){{.columns,.values{{grid-template-columns:1fr}}.metrics{{grid-template-columns:1fr 1fr}}.duration,.request summary span{{float:none;display:block}}}}
      </style><main><header><div class="muted">HTTP / GRAPHQL · РЕЗУЛЬТАТЫ ЗАПУСКА</div><h1>Что API действительно сделал</h1>
      <p>{escape(report['label'])}<br>ID запуска: <code>{escape(report.get('id', ''))}</code><br>Адрес: <code>{escape(report['endpoint'])}</code><br>Начало (UTC): {escape(report['startedAt'])} · код завершения: {report['exitCode']}</p>
      <p><a href="coverage.csv" download>Скачать покрытие (CSV)</a> · <a href="test-matrix.csv" download>Матрица Тест-Поле (CSV)</a> · <a href="server-test-coverage.json" download>Test Coverage (JSON)</a> · <a href="../../index.html">История запусков</a></p></header>{infra}
      <div class="metrics"><div class="metric"><b>{counts['passed']}/{len(tests)}</b>сценариев пройдено</div>
      <div class="metric"><b>{counts['failed']}</b>с ошибками · {counts['skipped']} пропущено</div>
      <div class="metric"><b>{requests}</b>реальных HTTP-запросов</div><div class="metric"><b>{passed_checks}/{len(checks)}</b>проверок пройдено</div></div>
      <p>Откройте сценарий: запросы идут в порядке выполнения, рядом с проверками — ожидание и факт. Ошибка останавливает сценарий; невыполненные шаги не показаны как успешные.</p>
      <div class="toolbar"><button data-filter="all" aria-pressed="true">Все сценарии</button><button data-filter="failed" aria-pressed="false">Только ошибки</button><button id="expand">Раскрыть всё</button></div>
      {coverage_section}{''.join(cards)}<footer>Источник — результаты pytest и записанные HTTP-ответы. Это статический отчёт; он не обращается к API и не меняет данные. Показатели покрытия взяты из Coverage API; ожидаемые значения проверяются тестами отдельно. results.json и field-usage.json содержат исходные результаты. Нет тестов: код 5; ошибка сбора: код 2 — это не успешный запуск.</footer></main>
      <script>for(const b of document.querySelectorAll('[data-filter]'))b.onclick=()=>{{for(const x of document.querySelectorAll('[data-filter]'))x.setAttribute('aria-pressed',String(x===b));for(const c of document.querySelectorAll('.case'))c.hidden=b.dataset.filter!=='all'&&!c.classList.contains(b.dataset.filter)}};document.getElementById('expand').onclick=()=>{{const open=[...document.querySelectorAll('.case')].some(c=>!c.open);for(const d of document.querySelectorAll('details'))d.open=open}};</script></html>'''
    path.write_text(source, encoding="utf-8")
