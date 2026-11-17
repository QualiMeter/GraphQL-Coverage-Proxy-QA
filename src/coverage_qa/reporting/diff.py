from __future__ import annotations

import csv
import io
import json
import sys
from pathlib import Path
from typing import Any
from coverage_qa.reporting.metrics import graph_field_hits


def extract_field_hits(run_data: dict[str, Any]) -> dict[str, int]:
    if not isinstance(run_data, dict):
        return {}
    if run_data.get("checkpoints"):
        return extract_field_hits(run_data["checkpoints"][-1])
    graph = run_data.get("graph", run_data)
    if isinstance(graph, dict) and isinstance(graph.get("types"), list):
        return graph_field_hits(graph)
    if "fieldHits" in run_data and isinstance(run_data["fieldHits"], dict):
        return {str(k): int(v) for k, v in run_data["fieldHits"].items()}
    if "finalFieldHits" in run_data and isinstance(run_data["finalFieldHits"], dict):
        return {str(k): int(v) for k, v in run_data["finalFieldHits"].items()}
    if "hits" in run_data and isinstance(run_data["hits"], dict):
        return {str(k): int(v) for k, v in run_data["hits"].items()}
    runs = run_data.get("coverageRuns", run_data.get("runs"))
    if isinstance(runs, list):
        combined = {}
        for r in runs:
            for k, v in extract_field_hits(r).items():
                combined[k] = max(combined.get(k, 0), v)
        return combined
    return {}


def compare_runs(before_data: dict[str, Any], after_data: dict[str, Any]) -> dict[str, Any]:
    hits_before = extract_field_hits(before_data)
    hits_after = extract_field_hits(after_data)

    all_coords = sorted(set(hits_before.keys()) | set(hits_after.keys()))
    total_fields = len(all_coords)

    covered_before = {c for c, h in hits_before.items() if h > 0}
    covered_after = {c for c, h in hits_after.items() if h > 0}

    newly_covered = sorted(covered_after - covered_before)
    uncovered = sorted(covered_before - covered_after)
    still_uncovered = sorted(c for c in all_coords if c not in covered_before and c not in covered_after)
    maintained_coverage = sorted(covered_before & covered_after)

    hits_deltas = {}
    for c in all_coords:
        hb = hits_before.get(c, 0)
        ha = hits_after.get(c, 0)
        hits_deltas[c] = ha - hb

    pct_before = round(len(covered_before) / len(hits_before) * 100, 2) if hits_before else 0.0
    pct_after = round(len(covered_after) / len(hits_after) * 100, 2) if hits_after else 0.0
    delta_pct = round(pct_after - pct_before, 2)

    return {
        "summary": {
            "totalFields": total_fields,
            "beforeTotalFields": len(hits_before),
            "afterTotalFields": len(hits_after),
            "beforeCovered": len(covered_before),
            "afterCovered": len(covered_after),
            "deltaCovered": len(covered_after) - len(covered_before),
            "beforePercent": pct_before,
            "afterPercent": pct_after,
            "deltaPercent": delta_pct,
        },
        "newlyCovered": newly_covered,
        "droppedCoverage": uncovered,
        "maintainedCoverage": maintained_coverage,
        "stillUncovered": still_uncovered,
        "hitsBefore": hits_before,
        "hitsAfter": hits_after,
        "hitsDeltas": hits_deltas,
        "addedFields": sorted(set(hits_after) - set(hits_before)),
        "removedFields": sorted(set(hits_before) - set(hits_after)),
    }


def hit_status(before: int, after: int) -> tuple[str, str]:
    if before == after:
        return ("maintained", "Без изменений") if after else ("uncovered", "Не покрыто")
    if before == 0:
        return "newly_covered", "Впервые покрыто"
    if after == 0:
        return "dropped", "Покрытие потеряно"
    if after < before:
        return "decrease", "Снижение обращений"
    return "growth", "Рост обращений"


def format_diff_markdown(diff: dict[str, Any]) -> str:
    s = diff["summary"]
    lines = [
        "### Сравнение покрытия (Coverage Diff)",
        f"- **Покрыто полей:** {s['beforeCovered']} → {s['afterCovered']} (дельта: **{s['deltaCovered']:+d}**)",
        f"- **Процент покрытия:** {s['beforePercent']:.2f}% → {s['afterPercent']:.2f}% (дельта: **{s['deltaPercent']:+.2f}%**)",
        f"- Полей в снимках {s['beforeTotalFields']} → {s['afterTotalFields']}. Добавлено {len(diff['addedFields'])}, удалено {len(diff['removedFields'])}.",
        "",
        "| Координата схемы | Было (hits) | Стало (hits) | Дельта | Статус |",
        "|---|---|---|---|---|",
    ]
    for coord, delta in sorted(diff["hitsDeltas"].items()):
        hb = diff["hitsBefore"].get(coord, 0)
        ha = diff["hitsAfter"].get(coord, 0)
        _, status = hit_status(hb, ha)
        lines.append(f"| `{coord}` | {hb} | {ha} | {delta:+d} | {status} |")
    return "\n".join(lines)


def format_diff_csv(diff: dict[str, Any]) -> str:
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["coordinate", "hits_before", "hits_after", "delta_hits", "status", "schema_change"])
    for coord, delta in sorted(diff["hitsDeltas"].items()):
        hb = diff["hitsBefore"].get(coord, 0)
        ha = diff["hitsAfter"].get(coord, 0)
        status, _ = hit_status(hb, ha)
        change = "present"
        if coord in diff["addedFields"]:
            change = "added"
        elif coord in diff["removedFields"]:
            change = "removed"
        writer.writerow([coord, hb, ha, delta, status, change])
    return output.getvalue()


def load_run_data(target: str | Path) -> dict[str, Any]:
    path = Path(target)
    if path.is_dir():
        for candidate in ("coverage.json", "results.json", "field-usage.json"):
            if (path / candidate).exists():
                return json.loads((path / candidate).read_text(encoding="utf-8"))
        raise FileNotFoundError(f"No coverage files found in directory {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    if len(sys.argv) < 3:
        print("Usage: python -m coverage_qa.reporting.diff <before_json_or_folder> <after_json_or_folder>")
        sys.exit(1)
    before = load_run_data(sys.argv[1])
    after = load_run_data(sys.argv[2])
    diff = compare_runs(before, after)
    print(format_diff_markdown(diff))


if __name__ == "__main__":
    main()
