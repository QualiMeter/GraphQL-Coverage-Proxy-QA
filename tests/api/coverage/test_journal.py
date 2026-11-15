import math
from uuid import uuid4

import pytest

QUERY = 'query UserInfo($id:ID!){user(id:$id){id name}}'
FIELDS = ["Query.user", "User.id", "User.name"]


def send(r, query=QUERY, name="UserInfo", variables=None):
    r.graphql("Запрос для проверки журнала", query, operation=name, variables=variables or {"id": "1"})
    return r.record["exchanges"][-1]


def page(r, path):
    body = r.rest("Страница с серверным total", "GET", path)
    headers = r.api.entry["events"][-2]["responseHeaders"]
    r.api.expect("Ответ не кешируется", headers.get("cache-control"), "no-store")
    return body, int(headers["x-total-count"])


@pytest.mark.case("Журнал: каждое повторное обращение имеет собственные поля и неизменный operationKey")
def test_request_details_are_per_exchange(coverage_run):
    r = coverage_run
    one = send(r)
    status, _ = r.graphql("Отклонённый запрос с чужим run ID", QUERY, operation="UserInfo",
                          variables={"id": "1"}, run_id=str(uuid4()))
    rejected = r.record["exchanges"][-1]
    r.api.expect("Неизвестный run отклонён", status, 404)
    r.snapshot("Отклонённый запрос рядом с успешной операцией")
    r.api.expect("Отказу не приписаны поля успешного запроса", "actualSelectedFields" in rejected, False)
    r.api.expect("Отказу не приписан чужой агрегат", "observedOperationKey" in rejected, False)
    two = send(r)
    r.api.expect("Разные ID HTTP-наблюдений", one["observedRequestIds"] != two["observedRequestIds"], True)
    for exchange in (one, two):
        r.api.expect("Фактические поля отдельного запроса", exchange["actualSelectedFields"], FIELDS)
        r.api.expect("Запрос учтён", exchange["countedInCoverage"], True)
        r.api.expect("Поля взяты из server detail", exchange["requestDetails"][0]["id"], exchange["observedRequestIds"][0])
    checkpoint = r.snapshot("Два одинаковых обращения")
    r.api.expect("Одна операция объединяет два обращения", checkpoint["operations"][0]["requestCount"], 2)
    r.api.expect("Detail и aggregate используют один ключ", one["requestDetails"][0]["operationKey"], checkpoint["operations"][0]["operationKey"])
    r.rest("Другой прогон того же snapshot", "POST", "/api/runs", {"snapshotId": r.snapshot_id, "name": "conflict"}, expected=409)
    r.rest("Неверная пара run/request не раскрывает detail", "GET",
           f"/api/runs/{uuid4()}/requests/{one['observedRequestIds'][0]}", expected=404)


@pytest.mark.parametrize("query,name,variables,availability", [
    (QUERY, "UserInfo", {"id": "1"}, "available"),
    ('query Safe($id:ID!="DEFAULT_SECRET"){alias:user(id:$id){id name}} # COMMENT_SECRET', "Safe", {"id": "1", "password": "VARIABLE_SECRET"}, "redacted"),
    ("{", None, {}, "unavailable"),
])
@pytest.mark.case("Журнал: настоящий безопасный AST доступен, замаскирован или явно отсутствует")
def test_safe_document_contract(coverage_run, query, name, variables, availability):
    r = coverage_run
    exchange = send(r, query, name, variables)
    detail = exchange["requestDetails"][0]
    r.api.expect("Состояние безопасного документа", detail["documentAvailability"], availability)
    document = detail["safeGraphqlDocument"]
    if availability == "unavailable":
        r.api.expect("Документ не выдуман для ошибки parse", document, None)
        r.api.expect("Ошибочное обращение не учтено", detail["countedInCoverage"], False)
    else:
        r.api.expect("Сохранена структура реального query", "user(id: $id)" in document, True)
        r.api.expect("Секреты и комментарии отсутствуют", any(s in document for s in ("DEFAULT_SECRET", "COMMENT_SECRET", "VARIABLE_SECRET")), False)
        if availability == "redacted":
            r.api.expect("Литерал явно замаскирован", "[REDACTED]" in document, True)
    rows = r.rest("Список не включает защищённый AST", "GET", f"/api/runs/{r.run_id}/requests")
    r.api.expect("AST только в detail", any("safeGraphqlDocument" in row for row in rows), False)


@pytest.mark.case("Журнал: фильтры AND, пагинация и total относятся к реальным обращениям")
def test_filters_pagination_and_totals(coverage_run):
    r = coverage_run
    send(r)
    send(r, 'query Products {products{id title}}', "Products")
    send(r)
    base = f"/api/runs/{r.run_id}/requests"
    all_rows, total = page(r, base + "?limit=500&sortOrder=asc")
    r.api.expect("Всего три обращения", total, 3)
    rows, total = page(r, base + "?operationName=UserInfo&typeName=User&fieldName=id&limit=1&offset=1&sortOrder=asc")
    expected = [row for row in all_rows if row["operationName"] == "UserInfo"]
    r.api.expect("Total до пагинации", total, 2)
    r.api.expect("Выбрана вторая совпавшая запись", [x["id"] for x in rows], [expected[1]["id"]])
    rows, total = page(r, base + "?operationName=UserInfo&typeName=Product&fieldName=id")
    r.api.expect("AND не объединяет разные операции", (rows, total), ([], 0))
    rows, total = page(r, base + "?offset=100")
    r.api.expect("Пустая страница сохраняет total", (rows, total), ([], 3))


@pytest.mark.parametrize("query", ["fieldName=id", "httpStatusClass=6", "limit=0", "from=2026-11-15", "operationKey=not-a-hash"])
@pytest.mark.case("Журнал: неверные фильтры возвращают 422")
def test_invalid_journal_filters(coverage_run, query):
    r = coverage_run
    r.rest("Отказ некорректному фильтру", "GET", f"/api/runs/{r.run_id}/requests?{query}", expected=422)


@pytest.mark.case("Статистика: статусы анализа, среднее и P95 соответствуют журналу")
def test_analysis_statuses_and_percentiles(coverage_run):
    r = coverage_run
    empty = r.rest("Пустая статистика", "GET", f"/api/runs/{r.run_id}/stats")
    r.api.expect("Нет выдуманной длительности", (empty["p95DurationMs"], empty["avgDurationMs"]), (None, None))
    send(r)
    send(r, "{", None)
    send(r, "{missingField}", None)
    send(r, 'query Frag{user(id:"1"){...U}} fragment U on User{id}', "Frag")
    rows = r.rest("Измерения длительности из БД", "GET", f"/api/runs/{r.run_id}/requests")
    stats = r.rest("Статистика смешанного прогона", "GET", f"/api/runs/{r.run_id}/stats")
    r.api.expect("Распределение статусов анализа", stats["analysisStatusCounts"],
                 {"success": 1, "parseError": 1, "validationError": 1, "unsupported": 1, "internalError": 0})
    r.api.expect("Все обращения учтены в статистике", stats["totalRequests"], 4)
    durations = sorted(x["durationMs"] for x in rows if x["durationMs"] is not None)
    position = (len(durations) - 1) * .95
    lo, hi = math.floor(position), math.ceil(position)
    expected = durations[lo] + (durations[hi] - durations[lo]) * (position - lo)
    r.api.expect("P95 — независимая линейная интерполяция", abs(stats["p95DurationMs"] - expected) < 1e-6, True)
    r.api.expect("Среднее — по фактическим durations", abs(stats["avgDurationMs"] - sum(durations)/len(durations)) < 1e-6, True)
