"""Business rules are tested by HTTP and re-reading committed PostgreSQL state.

Every test runs twice: direct GraphQL endpoint and unchanged Coverage Proxy.
"""

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from business_client import CATALOG


def stock(client):
    return {p["id"]: p["stock"] for p in client.catalog()}


def error_code(client, body, expected):
    client.api.expect("Предсказуемый код ошибки", [e.get("extensions", {}).get("code") for e in body.get("errors", [])], [expected])


@pytest.mark.case("Каталог: пагинация, типы, границы и согласованность отдельного товара")
def test_catalog_pagination(business):
    b = business
    full = b.catalog()
    first = b.gql("Первая страница", "{products(limit:2,offset:0){id name priceCents stock}}", token=None)["data"]["products"]
    second = b.gql("Вторая страница", "{products(limit:2,offset:2){id name priceCents stock}}", token=None)["data"]["products"]
    b.api.expect("Пагинация не теряет и не повторяет товары", first + second, full)
    b.api.expect("Цены и остатки неотрицательные целые", all(type(p["priceCents"]) is int and type(p["stock"]) is int and p["priceCents"] >= 0 and p["stock"] >= 0 for p in full), True)
    p = full[0]
    one = b.gql("Товар по ID", "query($id:ID!){product(id:$id){id name priceCents stock}}", {"id": p["id"]})
    b.api.expect("Отдельное чтение совпадает с каталогом", one["data"]["product"], p)
    invalid = b.gql("Некорректный размер страницы", "{products(limit:101){id}}", errors=True)
    error_code(b, invalid, "BAD_USER_INPUT")


@pytest.mark.case("Авторизация: неверный пароль и отсутствие/невалидность токена")
def test_authentication(business):
    b = business
    for token in (None, "invalid-token"):
        body = b.gql("Закрытые данные без действительной сессии", "{me{id email}}", token=token, errors=True)
        error_code(b, body, "UNAUTHENTICATED")
        b.api.expect("Non-null me не раскрывает данные", body["data"], None)
    failed = b.gql("Неверный пароль", "mutation($email:String!,$password:String!){login(email:$email,password:$password){token}}",
                   {"email": "alice@example.test", "password": "wrong-password"}, token=None, errors=True)
    error_code(b, failed, "INVALID_CREDENTIALS")
    me = b.gql("С действительной сессией", "{me{id email role}}")
    b.api.expect("Пользователь определён по токену", me["data"]["me"], {"id": "alice", "email": "alice@example.test", "role": "CUSTOMER"})


@pytest.mark.case("Роли: покупатель получает partial errors, администратор видит статистику")
def test_roles_and_partial_response(business):
    b = business
    body = b.gql("Каталог вместе с запрещённой статистикой", "{products(limit:1){id} adminStats{customers}}", errors=True)
    error_code(b, body, "FORBIDDEN")
    b.api.expect("Публичная часть ответа сохранена", len(body["data"]["products"]), 1)
    b.api.expect("Закрытая часть ответа null", body["data"]["adminStats"], None)
    b.login("admin")
    stats = b.gql("Статистика администратора", "{adminStats{customers activeOrders revenueCents}}")
    b.api.expect("В базе два покупателя", stats["data"]["adminStats"]["customers"], 2)


@pytest.mark.case("Профиль: изменение сохраняется и восстанавливается после проверки")
def test_profile_persistence(business):
    b = business
    previous = b.gql("Исходное имя", "{me{name}}")["data"]["me"]["name"]
    name = "QA-" + uuid4().hex[:8]
    query = "mutation($name:String!){updateProfile(name:$name){id name}}"
    try:
        b.gql("Изменить имя", query, {"name": name})
        after = b.gql("Перечитать из PostgreSQL", "{me{name}}")
        b.api.expect("Имя сохранено", after["data"]["me"]["name"], name)
        failed = b.gql("Пустое имя", query, {"name": "  "}, errors=True)
        error_code(b, failed, "BAD_USER_INPUT")
        b.api.expect("Отклонённое имя не записано", b.gql("Прочитать после отказа", "{me{name}}")["data"]["me"]["name"], name)
    finally:
        b.gql("Восстановить профиль", query, {"name": previous})


@pytest.mark.case("Заказ нескольких товаров: сумма, снимки цен, остатки, сохранение и отмена")
def test_checkout_and_cancel(business):
    b = business
    catalog = {p["id"]: p for p in b.catalog()}
    initial = {id: p["stock"] for id, p in catalog.items()}
    items = [{"productId": "keyboard", "quantity": 2}, {"productId": "cable", "quantity": 3}]
    order = b.create(items)
    b.api.expect("Итог рассчитан по исходным ценам", order["totalCents"], catalog["keyboard"]["priceCents"] * 2 + catalog["cable"]["priceCents"] * 3)
    b.api.expect("Покупатель заказа", order["customer"]["id"], "alice")
    b.api.expect("Новый статус", order["status"], "CREATED")
    quantities = {x["productId"]: x["quantity"] for x in items}
    b.api.expect("Остатки списаны только у выбранных товаров", stock(b), {id: count - quantities.get(id, 0) for id, count in initial.items()})
    saved = b.read_order(order["id"])["data"]["order"]
    b.api.expect("Независимый query видит сохранённый заказ", saved, order)
    for line in saved["items"]:
        p = catalog[line["product"]["id"]]
        b.api.expect("Цена строки зафиксирована из каталога", line["unitPriceCents"], p["priceCents"])
        b.api.expect("Сумма строки", line["totalCents"], p["priceCents"] * line["quantity"])
    b.cancel(order["id"])
    b.api.expect("Отмена вернула все остатки", stock(b), initial)
    b.cancel(order["id"])
    b.api.expect("Повтор отмены не удвоил возврат", stock(b), initial)


@pytest.mark.parametrize("items,code", [
    pytest.param([], "BAD_USER_INPUT", id="empty"),
    pytest.param([{"productId": "keyboard", "quantity": 0}], "BAD_USER_INPUT", id="zero"),
    pytest.param([{"productId": "keyboard", "quantity": -1}], "BAD_USER_INPUT", id="negative"),
    pytest.param([{"productId": "unknown", "quantity": 1}], "NOT_FOUND", id="missing-product"),
    pytest.param([{"productId": "keyboard", "quantity": 1}, {"productId": "keyboard", "quantity": 1}], "BAD_USER_INPUT", id="duplicate-product"),
])
@pytest.mark.case("Некорректный заказ: ошибка без изменения запасов и истории")
def test_invalid_orders(business, items, code):
    b = business
    initial = stock(b)
    before = b.gql("История до попытки", "{orders{id status totalCents}}")
    body = b.create(items, errors=True)
    error_code(b, body, code)
    b.api.expect("Остатки не изменились", stock(b), initial)
    after = b.gql("История после попытки", "{orders{id status totalCents}}")
    b.api.expect("История не изменилась", after["data"], before["data"])


@pytest.mark.case("Атомарность: один недоступный товар откатывает весь заказ")
def test_atomic_checkout(business):
    b = business
    initial = stock(b)
    body = b.create([{"productId": "cable", "quantity": 1}, {"productId": "mouse", "quantity": 1}], errors=True)
    error_code(b, body, "OUT_OF_STOCK")
    b.api.expect("Доступный товар тоже не списан", stock(b), initial)


@pytest.mark.case("Изоляция покупателей: чужой заказ нельзя прочитать или отменить")
def test_order_ownership(business):
    b = business
    initial = stock(b)
    order = b.create([{"productId": "cable", "quantity": 1}])
    b.login("bob")
    error_code(b, b.read_order(order["id"], errors=True), "NOT_FOUND")
    error_code(b, b.cancel(order["id"], errors=True), "NOT_FOUND")
    b.api.expect("Чужая отмена не вернула остаток", stock(b)["cable"], initial["cable"] - 1)
    listed = b.gql("Список заказов другого покупателя", "{orders{id}}")
    b.api.expect("Чужого ID нет в списке", order["id"] in {x["id"] for x in listed["data"]["orders"]}, False)


@pytest.mark.case("Идемпотентность: повтор не создаёт заказ и не списывает товар второй раз")
def test_idempotency(business):
    b = business
    initial = stock(b)
    items = [{"productId": "keyboard", "quantity": 1}]
    key = uuid4().hex
    first = b.create(items, key)
    second = b.create(items, key)
    b.api.expect("Повтор вернул тот же заказ", second, first)
    b.api.expect("Списание выполнено один раз", stock(b)["keyboard"], initial["keyboard"] - 1)
    conflict = b.create([{"productId": "keyboard", "quantity": 2}], key, errors=True)
    error_code(b, conflict, "IDEMPOTENCY_CONFLICT")
    b.api.expect("Конфликт не меняет остаток", stock(b)["keyboard"], initial["keyboard"] - 1)


@pytest.mark.case("Два покупателя одновременно покупают весь остаток: один успех, один отказ")
def test_competing_buyers(business):
    b = business
    alice = b.token
    bob = b.login("bob")
    initial = stock(b)
    quantity = initial["keyboard"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(b.create, [{"productId": "keyboard", "quantity": quantity}], errors=True, token=token) for token in (alice, bob)]
        results = [f.result() for f in futures]
    successes = [x for x in results if "id" in x]
    failures = [x for x in results if x.get("errors")]
    b.api.expect("Один успешный заказ", len(successes), 1)
    b.api.expect("Один отказ", len(failures), 1)
    error_code(b, failures[0], "OUT_OF_STOCK")
    b.api.expect("Нет перепродажи и отрицательного stock", stock(b)["keyboard"], 0)


@pytest.mark.case("Заказ и сессия сохраняются после перезапуска GraphQL-сервиса")
def test_database_persistence(business):
    b = business
    order = b.create([{"productId": "cable", "quantity": 1}])
    b.stack.upstream.restart()
    saved = b.read_order(order["id"])["data"]["order"]
    b.api.expect("Заказ и авторизация пережили перезапуск", saved, order)


@pytest.mark.case("Реальный бизнес-API: точные поля покрытия взяты из прокси, а не из тестового магазина")
def test_business_field_coverage(business):
    b = business
    query = "query PublicCatalog { products(limit:2) { id name priceCents } }"
    expected = {"Query.products", "Product.id", "Product.name", "Product.priceCents"}
    b.gql("Каталог с ограниченным набором полей", query, token=None, fields=expected, operation="PublicCatalog")
    if b.run:
        checkpoint = b.run.snapshot("Измеренное покрытие бизнес-API")
        b.run.expect_fields(checkpoint, expected)
        before = dict(checkpoint["fieldHits"])
        b.gql("Повтор того же бизнес-запроса", query, token=None, fields=expected, operation="PublicCatalog")
        after = b.run.snapshot("Покрытие после повтора")
        b.run.expect_fields(after, expected)
        b.api.expect("Увеличились только hits выбранных полей", after["fieldHits"], {k: v + int(k in expected) for k, v in before.items()})
    else:
        b.api.expect("Прямой ответ содержит товары", bool(b.gql("Повтор каталога", query, token=None)["data"]["products"]), True)
