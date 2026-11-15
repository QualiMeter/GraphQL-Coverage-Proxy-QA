import hashlib
import json
import os
import re
import time
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from playwright.sync_api import expect, sync_playwright

from tests.support.http_client import API, ensure_entry, redact
from tests.support.coverage_client import CoverageRun
from coverage_qa.environment import ROOT, Server
from coverage_qa.reporting.progress import emit, evidence_id


pytestmark = pytest.mark.browser


def cents(text):
    return int(Decimal(re.sub(r"[^0-9,.]", "", text).replace(",", ".")) * 100)



def login(page, account="alice"):
    page.get_by_role("button", name="Войти", exact=True).click()
    page.locator(f'[data-account="{account}"]').click()
    page.locator("#login-form").get_by_role("button", name="Войти", exact=True).click()
    expect(page.locator("#login-dialog")).not_to_be_visible()


@pytest.mark.case("Магазин: каталог, поиск, наличие и корзина без преждевременного списания")
def test_storefront_catalog_and_cart(storefront):
    s, p = storefront, storefront.page
    initial = s.direct("{products{ id stock priceCents }}")["products"]
    s.api.expect("Показаны все товары API", p.locator(".product-card").count(), len(initial))
    expect(p.get_by_test_id("product-mouse").get_by_role("button", name="Нет в наличии", exact=True)).to_be_disabled()
    p.get_by_role("searchbox", name="Поиск товаров").fill("Keyboard")
    expect(p.locator(".product-card")).to_have_count(1)
    p.get_by_role("searchbox", name="Поиск товаров").fill("")
    p.get_by_role("button", name="Мониторы", exact=True).click()
    expect(p.locator(".product-card")).to_have_count(1)
    p.get_by_role("button", name="Всё для стола", exact=True).click()
    p.get_by_test_id("product-keyboard").get_by_role("button", name="В корзину +", exact=True).click()
    p.get_by_role("button", name="Открыть корзину", exact=True).click()
    p.get_by_role("button", name="Увеличить количество Mechanical Keyboard", exact=True).click()
    price = next(x["priceCents"] for x in initial if x["id"] == "keyboard")
    s.api.expect("Итог корзины рассчитан по каталогу", cents(p.locator("#cart-total").inner_text()), price * 2)
    s.api.expect("Добавление в корзину ещё не списывает товар", s.direct("{products{ id stock priceCents }}")["products"], initial)


@pytest.mark.case("Магазин: браузерная покупка → сохранённый заказ → отмена → возврат остатков")
def test_storefront_checkout_and_cancel(storefront):
    s, p = storefront, storefront.page
    initial = {x["id"]: x for x in s.direct("{products{ id priceCents stock }}")["products"]}
    login(p)
    for id in ("keyboard", "cable"):
        p.get_by_test_id("product-" + id).get_by_role("button", name="В корзину +", exact=True).click()
    p.get_by_role("button", name="Открыть корзину", exact=True).click()
    p.get_by_role("button", name="Увеличить количество USB-C Cable", exact=True).click()
    expected = initial["keyboard"]["priceCents"] + initial["cable"]["priceCents"] * 2
    s.api.expect("Предварительная сумма", cents(p.locator("#cart-total").inner_text()), expected)
    p.get_by_role("button", name="Оформить заказ", exact=True).click()
    expect(p.get_by_test_id("success-order-id")).to_be_visible()
    id = p.get_by_test_id("success-order-id").inner_text()
    saved = s.direct("query($id:ID!){order(id:$id){id status totalCents items{quantity product{id}}}}", {"id": id})["order"]
    s.api.expect("В PostgreSQL сохранена правильная сумма", saved["totalCents"], expected)
    s.api.expect("В PostgreSQL сохранены обе позиции", {x["product"]["id"]: x["quantity"] for x in saved["items"]}, {"keyboard": 1, "cable": 2})
    p.get_by_role("button", name="Посмотреть заказ", exact=True).click()
    order = p.get_by_test_id("order-" + id)
    expect(order).to_be_visible()
    order.get_by_role("button", name="Отменить заказ", exact=True).click()
    p.get_by_role("button", name="Да, отменить заказ", exact=True).click()
    expect(order.locator(".status-pill")).to_have_text("Отменён")
    s.api.expect("Статус изменён в БД, а не только на экране", s.direct("query($id:ID!){order(id:$id){status}}", {"id": id})["order"]["status"], "CANCELLED")
    actual = {x["id"]: x for x in s.direct("{products{ id priceCents stock }}")["products"]}
    s.api.expect("Отмена полностью восстановила остатки", actual, initial)
    checkpoint = s.run.snapshot("После покупки в браузере")
    s.api.expect("Действия магазина попали в покрытие", checkpoint["fieldHits"]["Mutation.createOrder"] > 0 and checkpoint["fieldHits"]["Mutation.cancelOrder"] > 0, True)


@pytest.mark.case("Магазин: неверный пароль даёт настоящую ошибку; инспектор скрывает секреты")
def test_storefront_login_error_and_inspector(storefront):
    s, p = storefront, storefront.page
    p.get_by_role("button", name="Войти", exact=True).click()
    p.locator("#login-email").fill("alice@example.test")
    p.locator("#login-password").fill("definitely-wrong-password")
    p.locator("#login-form").get_by_role("button", name="Войти", exact=True).click()
    expect(p.locator("#login-error")).to_contain_text("Неверный email или пароль")
    p.get_by_role("button", name="Закрыть вход", exact=True).click()
    p.locator("#inspector-button").click()
    expect(p.locator("#request-detail")).to_contain_text("INVALID_CREDENTIALS")
    text = p.locator("#request-detail").inner_text()
    s.api.expect("Пароль не попал в визуальный журнал", "definitely-wrong-password" in text, False)
    s.api.expect("Секрет явно замаскирован", "<скрыто>" in text, True)


@pytest.mark.case("Магазин: административный кабинет показывает реальную статистику API")
def test_storefront_admin_profile(storefront):
    s, p = storefront, storefront.page
    login(p, "admin")
    p.get_by_role("button", name="Личный кабинет Administrator", exact=True).click()
    expect(p.locator("#admin-section")).to_be_visible()
    expect(p.locator("#admin-stats>div")).to_have_count(3)
    s.api.expect("Количество покупателей получено из API", p.locator("#admin-stats>div").first.locator("strong").inner_text(), "2")


@pytest.mark.case("Магазин: задержанный ответ заказов не восстанавливает данные после выхода")
def test_storefront_logout_discards_pending_orders(storefront):
    from uuid import uuid4
    s, p = storefront, storefront.page
    s.direct('mutation($input:CreateOrderInput!){createOrder(input:$input){id}}', {
        "input": {"items": [{"productId": "cable", "quantity": 1}], "idempotencyKey": str(uuid4())}})
    login(p)
    p.locator("#account-button").click()
    expect(p.locator("#logout")).to_be_visible()
    pending = []

    def hold_orders(route):
        if route.request.post_data_json.get("operationName") == "GetOrders":
            response = route.fetch()
            pending.append((route, response))
        else:
            route.continue_()

    p.route("**/api/graphql", hold_orders)
    p.evaluate("void loadOrders()")
    for _ in range(100):
        if pending:
            break
        p.wait_for_timeout(100)
    assert pending
    assert pending[0][1].json()["data"]["orders"]
    p.locator("#logout").click()
    with p.expect_response(lambda r: r.url.endswith("/api/graphql") and r.request.post_data_json.get("operationName") == "GetOrders"):
        pending[0][0].fulfill(response=pending[0][1])
    p.wait_for_function("S.requests.at(-1).ms !== null")
    assert p.evaluate("S.me === null && S.orders.length === 0")
    expect(p.locator("#orders-list .order-card")).to_have_count(0)


@pytest.mark.case("Магазин: потерянный ответ покупки → повтор → один заказ и одно списание")
def test_storefront_retry_after_lost_checkout_response(storefront):
    s, p = storefront, storefront.page
    before = s.direct("{products{id stock}}")["products"]
    initial_stock = next(x["stock"] for x in before if x["id"] == "cable")
    previous = {x["id"] for x in s.direct("{orders{id}}")["orders"]}
    login(p)
    attempts = []

    def lose_first_response(route):
        payload = route.request.post_data_json
        if payload.get("operationName") != "CreateOrder":
            route.continue_()
            return
        response = route.fetch(max_retries=0, max_redirects=0)
        body = response.json()
        s.remember_order(body)
        s.api.expect("Настоящий API завершил mutation до сбоя ответа", response.status, 200)
        s.api.expect("Mutation не вернула GraphQL errors", body.get("errors", []), [])
        attempts.append({"input": payload["variables"]["input"], "orderId": body["data"]["createOrder"]["id"]})
        if len(attempts) == 1:
            route.fulfill(status=502, content_type="application/json", body=json.dumps({"errors": [{"message": "Controlled lost response after commit"}]}))
        else:
            route.fulfill(response=response)

    p.route("**/api/graphql", lose_first_response)
    p.get_by_test_id("product-cable").get_by_role("button", name="В корзину +", exact=True).click()
    p.get_by_role("button", name="Открыть корзину", exact=True).click()
    p.get_by_role("button", name="Оформить заказ", exact=True).click()
    expect(p.locator("#checkout-error")).to_contain_text("502")
    expect(p.get_by_role("button", name="Оформить заказ", exact=True)).to_be_enabled()
    s.api.expect("До повтора сервер уже сохранил ровно один новый заказ", sorted({x["id"] for x in s.direct("{orders{id}}")["orders"]} - previous), [attempts[0]["orderId"]])
    p.get_by_role("button", name="Оформить заказ", exact=True).click()
    expect(p.get_by_test_id("success-order-id")).to_be_visible()
    id = p.get_by_test_id("success-order-id").inner_text()
    s.api.expect("Обе mutation исполнились через настоящий API", len(attempts), 2)
    s.api.expect("Повтор сохранил тот же idempotency key и состав", attempts[1]["input"], attempts[0]["input"])
    s.api.expect("Браузер получил исходный заказ", id, attempts[0]["orderId"])
    s.api.expect("Повтор не создал второй заказ", sorted({x["id"] for x in s.direct("{orders{id}}")["orders"]} - previous), [id])
    remaining = next(x["stock"] for x in s.direct("{products{id stock}}")["products"] if x["id"] == "cable")
    s.api.expect("Товар списан один раз", remaining, initial_stock - 1)
    checkpoint = s.run.snapshot("Потерянный ответ и повторная покупка")
    operation = next(x for x in checkpoint["operations"] if x["operationName"] == "CreateOrder")
    s.api.expect("Proxy сохранил оба обращения mutation", operation["requestCount"], 2)
    s.api.expect("Покрытие отражает два вызова, а не число заказов", checkpoint["fieldHits"]["Mutation.createOrder"], 2)

@pytest.mark.case("Магазин: временный отказ при восстановлении профиля сохраняет сессию")
def test_storefront_session_survives_profile_outage(storefront):
    page = storefront.page
    login(page)
    key = "forma-session-v1"
    original = page.evaluate("(key) => sessionStorage.getItem(key)", key)
    assert original

    def profile_outage(route):
        if route.request.post_data_json.get("operationName") == "GetMe":
            route.fulfill(status=503, content_type="application/json", body="{}")
        else:
            route.continue_()

    page.route("**/api/graphql", profile_outage)
    page.reload()
    expect(page.locator("#toast")).to_contain_text("503")
    assert page.evaluate("(key) => sessionStorage.getItem(key) === null", key) is False
    page.unroute("**/api/graphql", profile_outage)
    page.reload()
    expect(page.get_by_role("button", name="Войти", exact=True)).not_to_be_visible()
