from uuid import UUID, uuid4

import httpx
import pytest


def get(api, path, status=200):
    try:
        actual, body = api.http("Публичный сервер: GET " + path, "GET", api.url + path, auth=False)
    except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError) as exc:
        pytest.fail(f"Публичный сервер {api.url} недоступен по сети: {exc}")
    api.expect("HTTP-статус публичного endpoint", actual, status)
    return body


@pytest.mark.case("Публичное развёртывание: health и OpenAPI доступны")
def test_live_health_and_openapi(live_api):
    api = live_api
    api.expect("Сервер отвечает на health", get(api, "/health"), {"status": "ok"})
    spec = get(api, "/openapi.json")
    api.expect("Опубликован OpenAPI 3", spec.get("openapi", "").startswith("3."), True)
    api.expect("Основные API-пути опубликованы", {"/graphql", "/api/services", "/api/runs"}.issubset(spec["paths"]), True)


@pytest.mark.case("Публичное развёртывание: каталог показывает только публичные сервисы")
def test_live_public_catalog(live_api):
    api = live_api
    services = get(api, "/api/services")
    api.expect("Каталог — JSON-массив", isinstance(services, list), True)
    for service in services:
        api.expect("ID — UUID", str(UUID(service["id"])), service["id"])
        api.expect("Анонимно виден только публичный сервис", service["isPublic"], True)


@pytest.mark.case("Публичное развёртывание: схема и граф соответствуют опубликованному сервису")
def test_live_schema_graph(live_api):
    api = live_api
    services = get(api, "/api/services")
    if not services:
        pytest.skip("На сервере нет публичных сервисов для проверки графа")
    schemas = get(api, "/api/schemas?serviceId=" + services[0]["id"])
    if not schemas:
        pytest.skip("У публичного сервиса пока нет схем")
    graph = get(api, "/api/schemas/" + schemas[0]["id"] + "/graph")
    api.expect("Граф относится к выбранной схеме", graph["schemaId"], schemas[0]["id"])
    api.expect("Граф без прогона не создаёт фиктивного покрытия", graph["summary"]["coveredFields"], 0)
    api.expect("Все поля графа без hits", all(f["hits"] == 0 for t in graph["types"] for f in t["fields"]), True)


@pytest.mark.case("Публичное развёртывание: неизвестный прогон не раскрывает данные")
def test_live_unknown_run(live_api):
    get(live_api, "/api/runs/" + str(uuid4()) + "/coverage", status=404)
