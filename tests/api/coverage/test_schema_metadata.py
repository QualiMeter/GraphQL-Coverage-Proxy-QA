import json
from uuid import UUID, uuid5

import pytest

from coverage_qa.environment import ROOT
from tests.api.coverage.test_journal import page


@pytest.mark.case("Схема: label не меняет SDL, hash, relations и историческое покрытие")
def test_schema_labels_preserve_identity(coverage_run):
    r = coverage_run
    original = r.rest("Сохранённая SDL", "GET", f"/api/schemas/{r.snapshot_id}/sdl")
    graph = r.rest("Исходный граф", "GET", f"/api/schemas/{r.snapshot_id}/graph")
    changed = r.rest("Изменить displayName", "PATCH", f"/api/schemas/{r.snapshot_id}", {"displayName": "  Release candidate  "})
    r.api.expect("Label нормализован", changed["displayName"], "Release candidate")
    duplicate = r.rest("Повторный импорт не сбрасывает label", "POST", "/api/schemas/import/introspection", {"serviceId": r.service_id}, expected=201)
    r.api.expect("Тот же snapshot и label", (duplicate["id"], duplicate["displayName"]), (r.snapshot_id, "Release candidate"))
    r.api.expect("SDL и hash не изменились", r.rest("Перечитать SDL", "GET", f"/api/schemas/{r.snapshot_id}/sdl"), original)
    r.api.expect("Граф не изменился", r.rest("Перечитать граф", "GET", f"/api/schemas/{r.snapshot_id}/graph"), graph)
    cleared = r.rest("Очистить label", "PATCH", f"/api/schemas/{r.snapshot_id}", {"displayName": None})
    r.api.expect("Label очищен", cleared["displayName"], None)


@pytest.mark.parametrize("payload", [{}, {"displayName": "  "}, {"displayName": "x", "schemaHash": "replace"}])
@pytest.mark.case("Схема: пустое или запрещённое изменение label отклоняется")
def test_invalid_schema_label(coverage_run, payload):
    r = coverage_run
    r.rest("Неверное изменение схемы", "PATCH", f"/api/schemas/{r.snapshot_id}", payload, expected=422)


@pytest.mark.case("Граф: реальные root types, аргументы, описания и стабильные relation IDs")
def test_graph_metadata_from_real_sdl(coverage_run):
    r = coverage_run
    sdl = '''schema { query: StoreRoot }
    "A buyer" interface Buyer { id: ID! }
    type Person implements Buyer { id: ID! name: String }
    union SearchResult = Person
    "Store entry" type StoreRoot {
      "Find buyer" buyer(secret: String = "SCHEMA_DEFAULT_SECRET"): Buyer
      search: SearchResult
      old: String @deprecated(reason: "Use buyer")
    }'''
    snapshot = r.rest("Импортировать реальную SDL метаданных", "POST", "/api/schemas/import", {"serviceId": r.service_id, "sdl": sdl}, expected=201)
    graph = r.rest("Граф новой схемы", "GET", f"/api/schemas/{snapshot['id']}/graph")
    r.api.expect("Имя query root берётся из SDL", graph["rootTypes"], {"query": "StoreRoot", "mutation": None, "subscription": None})
    types = {t["name"]: t for t in graph["types"]}
    r.api.expect("Интерфейс действительно реализован", types["Person"]["interfaces"], ["Buyer"])
    r.api.expect("Union имеет реальный possible type", types["SearchResult"]["possibleTypes"], ["Person"])
    r.api.expect("Union не получает выдуманные поля", types["SearchResult"]["fields"], [])
    fields = {f["name"]: f for f in types["StoreRoot"]["fields"]}
    r.api.expect("Описание поля сохранено", fields["buyer"]["description"], "Find buyer")
    r.api.expect("Причина deprecation сохранена", fields["old"]["deprecationReason"], "Use buyer")
    arg = fields["buyer"]["args"][0]
    r.api.expect("Default аргумента явно замаскирован", (arg["defaultValueAvailability"], arg["defaultValue"]), ("redacted", '"[REDACTED]"'))
    for relation in graph["relations"]:
        coordinate = [relation["fromType"], relation["field"], relation["toType"]]
        expected = str(uuid5(UUID(snapshot["id"]), json.dumps(coordinate, ensure_ascii=True, separators=(",", ":"))))
        r.api.expect("Стабильный ID relation по snapshot и координате", relation["id"], expected)


@pytest.mark.case("История сервиса: разные snapshots, фильтры статуса и pagination total")
def test_service_history_across_snapshots(coverage_run):
    r = coverage_run
    first_id, first_snapshot = r.run_id, r.snapshot_id
    r.finish()
    sdl = (ROOT / "tests/support/upstream/schema.graphql").read_text(encoding="utf-8") + "\nextend type Query { extra: String }"
    second = r.rest("Новая версия SDL", "POST", "/api/schemas/import", {"serviceId": r.service_id, "sdl": sdl}, expected=201)
    newer = r.rest("Run новой версии", "POST", "/api/runs", {"snapshotId": second["id"], "name": "New version"}, expected=201)
    try:
        rows, total = page(r, f"/api/runs?serviceId={r.service_id}&limit=1&offset=1")
        r.api.expect("Total включает обе версии", total, 2)
        r.api.expect("История не подменяет старый snapshot текущим", [(x["id"], x["snapshotId"]) for x in rows], [(first_id, first_snapshot)])
        rows, total = page(r, f"/api/runs?serviceId={r.service_id}&status=running")
        r.api.expect("Фильтр running", ([x["id"] for x in rows], total), ([newer["id"]], 1))
        r.rest("Два history scope запрещены", "GET", f"/api/runs?serviceId={r.service_id}&snapshotId={first_snapshot}", expected=422)
    finally:
        r.rest("Завершить дополнительный run", "POST", f"/api/runs/{newer['id']}/finish")
