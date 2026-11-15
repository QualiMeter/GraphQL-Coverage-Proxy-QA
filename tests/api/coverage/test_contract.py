"""Independent black-box acceptance tests for the unchanged coverage backend."""

from uuid import uuid4

import pytest


pytestmark = pytest.mark.coverage
USER = 'query GetUser { user(id: "1") { id name orders { total } } }'
USER_FIELDS = {"Query.user", "User.id", "User.name", "User.orders", "Order.total"}
MUTATION = 'mutation CreateOrder($id: ID!) { createOrder(productId: $id) { id } }'
MUTATION_FIELDS = {"Mutation.createOrder", "Order.id"}
MINIMAL = 'query Minimal { user(id: "1") { id name } }'
MINIMAL_FIELDS = {"Query.user", "User.id", "User.name"}


def successful(run, query=USER, fields=USER_FIELDS, operation="GetUser", variables=None):
    status, body = run.graphql("Отправить GraphQL через настоящий прокси", query, fields=fields,
                               operation=operation, variables=variables)
    run.api.expect("HTTP upstream сохранён", status, 200)
    run.api.expect("GraphQL errors отсутствуют", body.get("errors", []), [])
    return body


def calls(run):
    return run.api.http("Счётчик обращений тестового upstream", "GET", run.stack.upstream.base + "/__test/calls", auth=False)[1]


@pytest.mark.case("Пустой прогон: настоящая introspection, 10 полей, отсутствие фиктивного покрытия")
def test_empty_and_schema_deduplication(coverage_run):
    r = coverage_run
    r.expect_fields(r.snapshot("До запросов"), set())
    duplicate = r.rest("Повторить импорт той же схемы", "POST", "/api/schemas/import/introspection", {"serviceId": r.service_id}, expected=201)
    r.api.expect("Идентичная схема не создаёт новый snapshot", duplicate["id"], r.snapshot_id)
    stats = r.rest("Статистика пустого прогона", "GET", f"/api/runs/{r.run_id}/stats")
    r.api.expect("Нет обращений", stats["totalRequests"], 0)
    r.api.expect("Неизвестная длительность — null", stats["avgDurationMs"], None)


@pytest.mark.case("Демонстрация: базовый → расширенный → повторный прогон с сохранением истории")
def test_demo_three_runs(coverage_run):
    r = coverage_run
    records = []
    for index, name in enumerate(("Базовый", "Расширенный", "Повторный")):
        if index:
            r.new_phase("demo_three_runs", name)
        else:
            r.record["name"] = name
        successful(r)
        checkpoint = r.snapshot(name + ": пользователь")
        r.expect_fields(checkpoint, USER_FIELDS)
        if index:
            body = successful(r, MUTATION, MUTATION_FIELDS, "CreateOrder", {"id": "10"})
            r.api.expect("Mutation вернула новый ID", isinstance(body["data"]["createOrder"]["id"], str), True)
            checkpoint = r.snapshot(name + ": создание заказа")
            r.expect_fields(checkpoint, USER_FIELDS | MUTATION_FIELDS)
        if index == 2:
            before_hits = dict(checkpoint["fieldHits"])
            successful(r)
            checkpoint = r.snapshot("Повтор: новые обращения без новых полей")
            r.expect_fields(checkpoint, USER_FIELDS | MUTATION_FIELDS)
            r.api.expect("Повтор увеличил hits только выбранных полей", checkpoint["fieldHits"],
                         {k: v + int(k in USER_FIELDS) for k, v in before_hits.items()})
            group = next(o for o in checkpoint["operations"] if o["operationName"] == "GetUser")
            r.api.expect("Операция объединяет повторные обращения", group["requestCount"], 2)
            r.api.expect("Оба обращения учтены", group["countedRequestCount"], 2)
            r.api.expect("В агрегате операции только её поля", sorted(f"{f['typeName']}.{f['fieldName']}" for f in group["fields"]), sorted(USER_FIELDS))
        r.finish()
        records.append(r.snapshot(name + ": завершён"))
    r.api.expect("История содержит три независимых прогона", len({c["graph"]["runId"] for c in records}), 3)
    history = r.rest("Прочитать историю с сервера", "GET", f"/api/runs?snapshotId={r.snapshot_id}")
    r.api.expect("Все три запуска сохранены", {x["id"] for x in history} == {c["graph"]["runId"] for c in records}, True)
    for checkpoint in records:
        run_id = checkpoint["graph"]["runId"]
        saved = r.rest("Проверить неизменность завершённого прогона", "GET", f"/api/runs/{run_id}/coverage")
        r.api.expect("Результат старого прогона не изменился", saved, checkpoint["graph"])


@pytest.mark.case("Прокси прозрачен: прямой и проксированный read-only запрос дают одинаковый ответ")
@pytest.mark.parametrize("browser_context", [False, True], ids=["server-e2e", "browser-context"])
def test_transparent_response_and_headers(coverage_run, browser_context):
    r = coverage_run
    payload = {"query": MINIMAL, "operationName": "Minimal"}
    direct_status, direct = r.api.http("Прямой запрос upstream", "POST", r.stack.upstream.base + "/graphql", payload=payload, auth=False)
    status, proxied = r.api.raw("Проксированный запрос с Authorization и Cookie", payload=payload,
                               headers={"X-Coverage-Run-Id": r.run_id, "Authorization": "Bearer synthetic-test-token",
                                        "Cookie": "test_cookie=secret_session_token",
                                        **({"Origin": "http://localhost:5173", "Sec-Fetch-Site": "same-origin"} if browser_context else {})})
    r.api.expect("Статус сохранён", status, direct_status)
    r.api.expect("Тело ответа сохранено", proxied, direct)
    last = calls(r)[-1]
    r.api.expect("Служебный API-ключ не ушёл upstream", last["hasCoverageKey"], False)
    r.api.expect("Служебный run ID не ушёл upstream", last["hasRunId"], False)
    r.api.expect("Authorization передан upstream", last["authorizationPresent"], True)
    r.api.expect("Cookies разрешены только серверному E2E", last["hasCookie"], not browser_context)
    r.expect_fields(r.snapshot("После проксирования"), MINIMAL_FIELDS)


@pytest.mark.case("Aliases и variables: учитываются координаты схемы, не имена в ответе")
def test_aliases_variables_and_directives(coverage_run):
    r = coverage_run
    query = 'query Aliases($id: ID!, $show: Boolean!) { customer: user(id: $id) { id displayName: name @include(if: $show) } }'
    successful(r, query, {"Query.user", "User.id"}, "Aliases", {"id": "1", "show": False})
    r.expect_fields(r.snapshot("show=false"), {"Query.user", "User.id"})
    body = successful(r, query, MINIMAL_FIELDS, "Aliases", {"id": "1", "show": True})
    r.api.expect("Alias присутствует в ответе", body["data"]["customer"]["displayName"], "Anna")
    checkpoint = r.snapshot("show=true")
    r.expect_fields(checkpoint, MINIMAL_FIELDS)
    r.api.expect("Повторное id учитывается дважды", checkpoint["fieldHits"]["User.id"], 2)


@pytest.mark.case("operationName: анализируется только выбранная операция документа")
def test_selected_operation(coverage_run):
    r = coverage_run
    successful(r, 'query A { products { title } } query B { user(id: "1") { id } }', {"Query.user", "User.id"}, "B")
    r.expect_fields(r.snapshot("Выбрана операция B"), {"Query.user", "User.id"})


@pytest.mark.case("Повтор поля через aliases в одном запросе даёт один hit")
def test_duplicate_fields_in_one_request(coverage_run):
    r = coverage_run
    expected = {"Query.user", "User.id"}
    successful(r, 'query Duplicate { first: user(id: "1") { id } second: user(id: "1") { id } }', expected, "Duplicate")
    checkpoint = r.snapshot("Два aliases в одном HTTP-запросе")
    r.expect_fields(checkpoint, expected)
    r.api.expect("Каждое поле учтено один раз", {k: v for k, v in checkpoint["fieldHits"].items() if v}, {k: 1 for k in expected})


@pytest.mark.parametrize("query,operation,expected_status", [
    pytest.param("{", None, "parse_error", id="syntax"),
    pytest.param("{ missingField }", None, "validation_error", id="validation"),
    pytest.param('query Fragment { user(id: "1") { ...U } } fragment U on User { id name }', "Fragment", "unsupported", id="fragment"),
])
@pytest.mark.case("Ошибка или неподдерживаемый анализ сохраняется без ложного покрытия")
def test_analysis_failures(coverage_run, query, operation, expected_status):
    r = coverage_run
    status, body = r.graphql("Передать операцию upstream независимо от анализа", query, operation=operation)
    r.api.expect("Запрос дошёл upstream", status, 200)
    if expected_status == "unsupported":
        r.api.expect("Upstream исполнил поддерживаемый им fragment", body.get("errors", []), [])
    checkpoint = r.snapshot("После ошибочного анализа")
    r.expect_fields(checkpoint, set())
    r.api.expect("Есть ровно одно наблюдение", len(checkpoint["requests"]), 1)
    r.api.expect("Честный статус анализатора", checkpoint["requests"][0]["analysisStatus"], expected_status)


@pytest.mark.parametrize("mode,status,has_data,has_errors,counted", [
    ("no-data", 200, False, True, False),
    ("partial", 200, True, True, True),
    ("http-error", 503, False, True, False),
    ("non-json", 503, None, None, False),
    ("gzip", 200, True, False, True),
])
@pytest.mark.case("Ответы upstream: null, partial errors, HTTP 503, non-JSON и gzip")
def test_response_semantics(coverage_run, mode, status, has_data, has_errors, counted):
    r = coverage_run
    actual_status, _ = r.graphql("Получить контролируемый ответ: " + mode, MINIMAL,
                                  operation="Minimal", fields=MINIMAL_FIELDS, mode=mode)
    r.api.expect("HTTP-статус upstream сохранён", actual_status, status)
    checkpoint = r.snapshot(mode)
    r.expect_fields(checkpoint, MINIMAL_FIELDS if counted else set())
    observation = checkpoint["requests"][0]
    r.api.expect("Признак data", observation["responseHasData"], has_data)
    r.api.expect("Признак GraphQL errors", observation["graphqlHasErrors"], has_errors)
    stats = r.rest("Сверить статистику", "GET", f"/api/runs/{r.run_id}/stats")
    r.api.expect("Counted requests соответствует семантике data", stats["countedRequests"], int(counted))


@pytest.mark.parametrize("kind,expected", [("invalid", 400), ("unknown", 404), ("finished", 409)])
@pytest.mark.case("Некорректный run ID отклоняется до обращения к upstream")
def test_rejected_run(coverage_run, kind, expected):
    r = coverage_run
    before = len(calls(r))
    if kind == "finished":
        r.finish()
    value = "bad-id" if kind == "invalid" else str(uuid4()) if kind == "unknown" else r.run_id
    status, _ = r.graphql("Отклоняемый run ID", MINIMAL, operation="Minimal", run_id=value)
    r.api.expect("Статус отклонения", status, expected)
    r.api.expect("Upstream не вызван", len(calls(r)), before)
    r.expect_fields(r.snapshot("Отклонённый запрос"), set())


@pytest.mark.case("Без авторизации нельзя создавать сервис или отправлять GraphQL")
def test_unauthorized(coverage_run):
    r = coverage_run
    before = len(calls(r))
    for path, payload in [("/api/services", {"key": "unauthorized", "name": "Unauthorized"}),
                          ("/graphql", {"query": MINIMAL})]:
        status, _ = r.api.http("Запрос без ключа", "POST", r.base + path, payload=payload, auth=False)
        r.api.expect("Нужна авторизация", status, 401)
    r.api.expect("Неавторизованный запрос не попал upstream", len(calls(r)), before)


@pytest.mark.case("Конфликт активных прогонов и завершение: API возвращает 409")
def test_run_lifecycle_conflicts(coverage_run):
    r = coverage_run
    r.rest("Запрет второго активного прогона", "POST", "/api/runs", {"snapshotId": r.snapshot_id, "name": "Conflicting"}, expected=409)
    r.finish()
    r.rest("Повторное завершение", "POST", f"/api/runs/{r.run_id}/finish", expected=409)


@pytest.mark.case("Изоляция прогонов: данные первого сервиса не попадают во второй")
def test_run_isolation(coverage_run):
    from tests.support.coverage_client import CoverageRun
    r = coverage_run
    other = CoverageRun(r.api, r.stack, r.report)
    try:
        other.prepare("isolated-other-run")
        successful(r)
        successful(other, 'query Products { products { id title } }', {"Query.products", "Product.id", "Product.title"}, "Products")
        r.expect_fields(r.snapshot("Первый прогон"), USER_FIELDS)
        other.expect_fields(other.snapshot("Второй прогон"), {"Query.products", "Product.id", "Product.title"})
    finally:
        if other.run_id:
            other.finish()
            other.snapshot("Итог второго прогона")


@pytest.mark.case("Завершённый прогон сохраняется после перезапуска процесса бэкенда")
def test_persistence_after_restart(coverage_run):
    r = coverage_run
    if not hasattr(r.stack.proxy, "restart"):
        pytest.skip("Перезапуск разрешён только для собственного тестового процесса")
    successful(r)
    r.finish()
    before = r.snapshot("Перед перезапуском")
    r.stack.proxy.restart()
    after = r.snapshot("После перезапуска")
    r.api.expect("Граф сохранён в PostgreSQL", after["graph"], before["graph"])
    r.api.expect("Наблюдения сохранены", after["requests"], before["requests"])


@pytest.mark.case("Сетевая ошибка upstream даёт 502 без выдуманного наблюдения")
def test_network_failure(coverage_run):
    r = coverage_run
    if not hasattr(r.stack.upstream, "stop"):
        pytest.skip("Остановка разрешена только для тестового upstream")
    r.stack.upstream.stop()
    try:
        status, _ = r.graphql("Upstream недоступен", MINIMAL, operation="Minimal")
        r.api.expect("Сетевая ошибка", status, 502)
        checkpoint = r.snapshot("Нет ответа upstream")
        r.expect_fields(checkpoint, set())
        r.api.expect("Текущий контракт не сохраняет сетевые сбои как обращения", checkpoint["requests"], [])
    finally:
        r.stack.upstream.start()


@pytest.mark.case("Таймаут upstream даёт 504 без фиктивного покрытия")
def test_upstream_timeout(coverage_run):
    r = coverage_run
    status, _ = r.graphql("Ответ задержан дольше таймаута прокси", MINIMAL, operation="Minimal", mode="delay")
    r.api.expect("Таймаут upstream", status, 504)
    checkpoint = r.snapshot("Таймаут")
    r.expect_fields(checkpoint, set())
    r.api.expect("Сетевой таймаут не создаёт наблюдение по текущему контракту", checkpoint["requests"], [])
