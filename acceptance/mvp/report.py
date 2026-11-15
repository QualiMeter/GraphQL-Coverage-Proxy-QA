"""Render verified API checkpoints after the persistence check succeeds."""
import csv
import json
from html import escape


def write_summary(folder, graph):
    steps = json.loads((folder / "steps.json").read_text(encoding="utf-8"))
    columns = "".join(f"<th>{escape(step['label'])}<br>{step['percent']:.2f}%</th>" for step in steps)
    rows = []
    csv_rows = []
    for coordinate in sorted(steps[0]["hits"]):
        cells = []
        previous = 0
        step_counts = []
        for step in steps:
            hits = step["hits"][coordinate]
            cells.append(f'<td class="{"growth" if hits > previous else ""}">{hits}</td>')
            step_counts.append(hits)
            previous = hits
        rows.append(f"<tr><th>{escape(coordinate)}</th>{''.join(cells)}</tr>")
        tname, _, fname = coordinate.partition(".")
        final_hits = step_counts[-1]
        csv_rows.append([coordinate, tname, fname, *step_counts, final_hits, final_hits > 0])

    csv_path = folder / "coverage.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        header = ["coordinate", "type", "field", *(s["label"] for s in steps), "final_hits", "covered"]
        writer.writerow(header)
        writer.writerows(csv_rows)

    summary = graph["summary"]
    html = f"""<!doctype html><html lang="ru"><meta charset="utf-8">
<title>Приёмка GraphQL Coverage Proxy</title>
<style>body{{font:16px system-ui;color:#182d30;margin:4vw;background:#fafaf7}}
table{{border-collapse:collapse;background:white}}td,th{{padding:.7em;border:1px solid #ced6d3}}
td{{text-align:center}}tbody th{{text-align:left}}.growth{{background:#d5f0df;font-weight:bold}}
code{{overflow-wrap:anywhere}}</style>
<h1>Покрытие схемы · {summary['coveredFields']} из {summary['totalFields']} полей</h1>
<p>Прогон <code>{escape(graph['runId'])}</code></p>
<p>В ячейках показаны обращения к каждому полю. Зелёным отмечен рост счётчика относительно предыдущего шага.</p>
<table><thead><tr><th>Поле схемы</th>{columns}</tr></thead><tbody>{''.join(rows)}</tbody></table>
<p>После перезапуска бэкенда полный ответ о покрытии совпал с сохранённым.</p>
<p><a href="coverage.json">Ответ API</a> · <a href="coverage.csv" download>Выгрузка в CSV</a> · <a href="steps.json">Данные по шагам</a> ·
<a href="html/index.html">Отчёт Playwright</a></p></html>"""
    (folder / "index.html").write_text(html, encoding="utf-8")
