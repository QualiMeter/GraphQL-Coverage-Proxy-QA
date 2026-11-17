import csv
import html
import io


def server_executions(report):
    executions = {}
    for test in report.get("tests", []):
        for snapshot in test.get("serverTestCoverage", []):
            if snapshot.get("source") == "backend" and snapshot.get("execution", {}).get("id"):
                ident = snapshot["execution"]["id"]
                current = executions.get(ident)
                if current is None or snapshot.get("fetchedAt", "") >= current.get("fetchedAt", ""):
                    executions[ident] = snapshot
    return list(executions.values())


def server_matrix_csv(report):
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["run_id", "execution_id", "test_id", "test_title", "framework", "attempt", "status", "duration_ms",
                     "request_count", "type_name", "field_name", "selected_request_count", "hits", "observed_count",
                     "not_observed_count", "indeterminate_count", "unavailable_count"])
    for snapshot in server_executions(report):
        execution = snapshot["execution"]
        prefix = [execution.get(key) for key in ("runId", "id", "testId", "name", "framework", "attempt", "status", "durationMs", "requestCount")]
        for field in snapshot.get("fields") or [{}]:
            writer.writerow(prefix + [field.get(key) for key in ("typeName", "fieldName", "selectedRequestCount", "hits",
                "observedCount", "notObservedCount", "indeterminateCount", "unavailableCount")])
    return output.getvalue()


def execution_html(snapshots):
    parts = []
    escape = lambda value: html.escape(str(value))
    for snapshot in snapshots:
        execution = snapshot["execution"]
        rows = []
        for field in snapshot.get("fields", []):
            cells = [field["typeName"] + "." + field["fieldName"], field["selectedRequestCount"], field["hits"],
                     field["observedCount"], field["notObservedCount"], field["indeterminateCount"], field["unavailableCount"]]
            rows.append("<tr>" + "".join("<td>" + escape(cell) + "</td>" for cell in cells) + "</tr>")
        pending = '<p class="failure">Результат теста не подтверждён после завершения сбора.</p>' if execution.get("resultMissing") else ''
        parts.append(f'<section class="server-coverage"><h3>Test Coverage · {escape(execution["name"])}</h3>'
            f'<p>Источник: backend · {escape(snapshot.get("fetchedAt", ""))}<br>'
            f'Результат: <strong>{escape(execution["status"])}</strong> · {escape(execution["framework"])} · попытка {escape(execution["attempt"])}<br>'
            f'Test ID: <code>{escape(execution["testId"])}</code><br>Выполнение: <code>{escape(execution["id"])}</code><br>'
            f'Прогон: <code>{escape(execution["runId"])}</code> · сбор: {escape(execution["collectionStatus"])}<br>'
            f'Записей журнала: {escape(execution["requestCount"])} · учтено: {escape(execution["countedRequestCount"])} · '
            f'покрытие: {escape(execution["coveragePercent"])}%</p>{pending}'
            '<div style="overflow:auto"><table><tr><th>Поле</th><th>Выбрано в запросах</th><th>Hits</th><th>Наблюдалось</th>'
            '<th>Отсутствовало</th><th>Неопределённо</th><th>Недоступно</th></tr>' + ''.join(rows) + '</table></div></section>')
    return ''.join(parts)
