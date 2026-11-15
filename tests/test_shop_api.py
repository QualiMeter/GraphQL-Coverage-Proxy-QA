"""Black-box tests for the demo shop contract. Only HTTP, no store/DB imports."""

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest


CATALOG = "query Catalog { users { id name email } products { id name priceCents stock } }"
STATE = """query State($id: ID!) {
  products { id priceCents stock }
  user(id: $id) { id orders { id quantity totalCents status product { id } } }
}"""
CREATE = """mutation Purchase($user: ID!, $product: ID!, $quantity: Int!) {
  createOrder(userId: $user, productId: $product, quantity: $quantity) {
    id quantity totalCents status user { id } product { id stock }
  }
}"""


def seeds(api):
    data = api.gql("Получить доступных покупателей и товары", CATALOG)["data"]
    if not data["users"] or not data["products"]:
        pytest.skip("Для сценария нужны хотя бы один покупатель и товар")
    return data["users"][0], data["products"]


def state(api, user):
    return api.gql("Перечитать состояние независимо от mutation", STATE, {"id": user})["data"]


def expect_error(api, body, code):
    api.expect("Non-null mutation распространяет ошибку в data=null", body.get("data"), None)
    api.expect("Код бизнес-ошибки", [e.get("extensions", {}).get("code") for e in body.get("errors", [])], [code])
    api.expect("Ошибка относится к createOrder", body["errors"][0].get("path"), ["createOrder"])


@pytest.mark.case("Каталог: контракт данных и согласованность списка с отдельным объектом")
def test_catalog_and_product_lookup(api):
    _, products = seeds(api)
    ids = [p["id"] for p in products]
    api.expect("ID товаров уникальны", len(set(ids)), len(ids))
    for product in products:
        api.expect("Цена — неотрицательное целое", type(product["priceCents"]) is int and product["priceCents"] >= 0, True)
        api.expect("Остаток — неотрицательное целое", type(product["stock"]) is int and product["stock"] >= 0, True)
        body = api.gql("Получить товар отдельно по ID", "query($id: ID!) { product(id: $id) { id name priceCents stock } }", {"id": product["id"]})
        api.expect("Отдельный товар совпадает с каталогом", body["data"]["product"], product)


@pytest.mark.case("Несуществующий объект: null без GraphQL-ошибки")
def test_unknown_user(api):
    body = api.gql("Запросить неизвестного пользователя", "query($id: ID!) { user(id: $id) { id } }", {"id": f"missing-{uuid4().hex}"})
    api.expect("Nullable user возвращает null", body["data"], {"user": None})


@pytest.mark.case("Алиасы: два независимых имени в одном ответе")
def test_aliases(api):
    user, _ = seeds(api)
    body = api.gql("Выбрать одного пользователя с двумя алиасами", "query($id: ID!) { first: user(id: $id) { id } second: user(id: $id) { id } }", {"id": user["id"]})
    api.expect("Алиасы присутствуют и возвращают запрошенный ID", body["data"], {"first": {"id": user["id"]}, "second": {"id": user["id"]}})


@pytest.mark.case("@include: переменная действительно меняет набор полей ответа")
def test_include(api):
    user, _ = seeds(api)
    query = "query($id: ID!, $show: Boolean!) { user(id: $id) { id email @include(if: $show) } }"
    for show in (False, True):
        body = api.gql(f"Запросить email с show={show}", query, {"id": user["id"], "show": show})
        expected = {"id": user["id"]}
        if show:
            expected["email"] = user["email"]
        api.expect("Возвращаются только выбранные поля", body["data"]["user"], expected)


@pytest.mark.case("Fragments: эквивалентность обычной и фрагментной выборки")
def test_fragment(api):
    user, _ = seeds(api)
    plain = api.gql("Обычная выборка", "query($id: ID!) { user(id: $id) { id name email } }", {"id": user["id"]})
    fragment = api.gql("Та же выборка через fragment", "query($id: ID!) { user(id: $id) { ...Details } } fragment Details on User { id name email }", {"id": user["id"]})
    api.expect("Fragment не меняет данные", fragment["data"], plain["data"])


@pytest.mark.case("operationName выбирает операцию из документа")
def test_operation_name(api):
    body = api.gql("Выполнить только Health из двух операций", "query Health { health } query Users { users { id } }", operation="Health")
    api.expect("Выполнена только выбранная операция", body["data"], {"health": "ok"})


@pytest.mark.case("Introspection: API раскрывает Query и Mutation")
def test_introspection(api):
    body = api.gql("Получить корневые типы", "{ __schema { queryType { name } mutationType { name } } }")
    api.expect("Корневые типы соответствуют контракту", body["data"]["__schema"], {"queryType": {"name": "Query"}, "mutationType": {"name": "Mutation"}})


@pytest.mark.parametrize("query,variables", [
    pytest.param("{", None, id="syntax"),
    pytest.param("{ nonexistentField }", None, id="unknown-field"),
    pytest.param("query($id: ID!) { user(id: $id) { id } }", {}, id="missing-variable"),
    pytest.param("query A { health } query B { health }", None, id="ambiguous-operation"),
])
@pytest.mark.case("Некорректная GraphQL-операция: errors при HTTP 200")
def test_graphql_errors(api, query, variables):
    body = api.gql("Отправить некорректную GraphQL-операцию", query, variables, errors=True)
    api.expect("Полезные данные отсутствуют", body.get("data"), None)
    api.expect("Есть сообщения GraphQL errors", bool(body.get("errors")), True)


@pytest.mark.parametrize("payload", [[], {}, {"query": 42}, {"query": "{ health }", "variables": []}], ids=["batch", "no-query", "query-number", "variables-array"])
@pytest.mark.case("Некорректный HTTP-конверт: 400 вместо выполнения GraphQL")
def test_invalid_envelope(api, payload):
    status, body = api.raw("Отправить неподдерживаемый JSON-конверт", payload=payload)
    api.expect("HTTP-статус", status, 400)
    api.expect("Описание ошибки присутствует", isinstance(body, dict) and bool(body.get("errors")), True)


@pytest.mark.case("Повреждённый JSON: контролируемый ответ 400")
def test_malformed_json(api):
    status, body = api.raw("Отправить незакрытый JSON", content="{")
    api.expect("HTTP-статус", status, 400)
    api.expect("Описание ошибки присутствует", bool(body.get("errors")), True)


@pytest.mark.case("Изменение имени: сохранение и восстановление исходного значения")
def test_rename_and_restore(api):
    user, _ = seeds(api)
    query = "mutation($id: ID!, $name: String!) { updateUserName(id: $id, name: $name) { id name } }"
    name = f"API test {uuid4().hex[:8]}"
    try:
        body = api.gql("Изменить имя на уникальное", query, {"id": user["id"], "name": name})
        api.expect("Mutation вернула новое имя", body["data"]["updateUserName"], {"id": user["id"], "name": name})
        actual = api.gql("Проверить имя отдельным query", "query($id: ID!) { user(id: $id) { name } }", {"id": user["id"]})
        api.expect("Имя действительно сохранено", actual["data"]["user"]["name"], name)
    finally:
        restored = api.gql("Восстановить исходное имя (cleanup)", query, {"id": user["id"], "name": user["name"]})
        api.expect("Cleanup выполнен", restored["data"]["updateUserName"]["name"], user["name"])


@pytest.mark.case("Пустое имя отклоняется и не меняет пользователя")
def test_blank_name(api):
    user, _ = seeds(api)
    body = api.gql("Попробовать сохранить пробелы вместо имени", "mutation($id: ID!) { updateUserName(id: $id, name: \"  \") { id } }", {"id": user["id"]}, errors=True)
    api.expect("Код ошибки", [e.get("extensions", {}).get("code") for e in body.get("errors", [])], ["BAD_USER_INPUT"])
    after = api.gql("Перечитать имя", "query($id: ID!) { user(id: $id) { name } }", {"id": user["id"]})
    api.expect("Имя не изменилось", after["data"]["user"]["name"], user["name"])


@pytest.mark.parametrize("quantity,product_id,code", [
    pytest.param(0, None, "BAD_USER_INPUT", id="zero"),
    pytest.param(-1, None, "BAD_USER_INPUT", id="negative"),
    pytest.param(1, "missing", "NOT_FOUND", id="unknown-product"),
])
@pytest.mark.case("Отклонённый заказ не меняет остатки и историю")
def test_rejected_purchase(api, quantity, product_id, code):
    user, products = seeds(api)
    before = state(api, user["id"])
    body = api.gql("Создать заведомо некорректный заказ", CREATE, {"user": user["id"], "product": product_id or products[0]["id"], "quantity": quantity}, errors=True)
    expect_error(api, body, code)
    after = state(api, user["id"])
    api.expect("Полное состояние совпадает с исходным", after, before)


@pytest.mark.case("Покупка: сумма, остаток, история, отдельное чтение и запрет перепродажи")
def test_purchase_lifecycle(api):
    user, products = seeds(api)
    product = next((p for p in products if p["stock"] >= 2), None)
    if product is None:
        pytest.skip("Нужен товар с остатком >= 2; запустите изолированный образец через --local")
    before = state(api, user["id"])
    previous_ids = [o["id"] for o in before["user"]["orders"]]
    body = api.gql("Купить две единицы товара", CREATE, {"user": user["id"], "product": product["id"], "quantity": 2})
    order = body["data"]["createOrder"]
    api.expect("Сумма = цена из каталога × количество", order["totalCents"], product["priceCents"] * 2)
    api.expect("Заказ принадлежит выбранному покупателю", order["user"]["id"], user["id"])
    api.expect("Выбран правильный товар", order["product"]["id"], product["id"])
    api.expect("Сохранено количество", order["quantity"], 2)
    api.expect("Начальный статус", order["status"], "CREATED")
    api.expect("ID ранее не встречался", order["id"] not in previous_ids, True)
    after = state(api, user["id"])
    stock = next(p["stock"] for p in after["products"] if p["id"] == product["id"])
    api.expect("Остаток изменился в отдельном query", stock, product["stock"] - 2)
    api.expect("Остальные товары не изменились", [p for p in after["products"] if p["id"] != product["id"]], [p for p in before["products"] if p["id"] != product["id"]])
    api.expect("Ровно один новый заказ в истории", [o["id"] for o in after["user"]["orders"]], previous_ids + [order["id"]])
    saved = api.gql("Прочитать новый заказ по ID", "query($id: ID!) { order(id: $id) { id quantity totalCents status user { id } product { id stock } } }", {"id": order["id"]})
    api.expect("Отдельно прочитанный заказ совпадает с mutation", saved["data"]["order"], order)
    rejected = api.gql("Попробовать купить больше текущего остатка", CREATE, {"user": user["id"], "product": product["id"], "quantity": stock + 1}, errors=True)
    expect_error(api, rejected, "OUT_OF_STOCK")
    api.expect("После отказа состояние не изменилось", state(api, user["id"]), after)


@pytest.mark.case("Конкурентные покупки: две заявки на весь остаток, только одна успешна")
def test_concurrent_purchases(api):
    user, products = seeds(api)
    product = next((p for p in products if p["stock"] > 0), None)
    if product is None:
        pytest.skip("Нужен товар с ненулевым остатком; используйте --local")
    before = state(api, user["id"])
    # Each request is individually valid; together they exceed available stock.
    variables = {"user": user["id"], "product": product["id"], "quantity": product["stock"]}
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(api.raw, f"Конкурентный заказ {i+1}", payload={"query": CREATE, "variables": variables}) for i in range(2)]
        responses = [future.result() for future in futures]
    api.expect("Оба HTTP-запроса обработаны", [status for status, _ in responses], [200, 200])
    successes = [b["data"]["createOrder"] for _, b in responses if b.get("data") and not b.get("errors")]
    failures = [b for _, b in responses if b.get("errors")]
    api.expect("Ровно один заказ создан", len(successes), 1)
    api.expect("Ровно один отказ", len(failures), 1)
    expect_error(api, failures[0], "OUT_OF_STOCK")
    api.expect("Сумма единственного заказа", successes[0]["totalCents"], product["priceCents"] * product["stock"])
    after = state(api, user["id"])
    api.expect("Итоговый остаток равен нулю", next(p["stock"] for p in after["products"] if p["id"] == product["id"]), 0)
    api.expect("В истории добавлена только одна запись", [o["id"] for o in after["user"]["orders"]], [o["id"] for o in before["user"]["orders"]] + [successes[0]["id"]])
