"""Учебный пример: один новый сценарий автоматически проверяется двумя путями."""

import pytest


@pytest.mark.case("Отмена заказа: фильтры CANCELLED и CREATED соответствуют сохранённому состоянию")
def test_cancelled_order_filter(business):
    # business подготовит API, выполнит вход и после теста уберёт созданный заказ из активных.
    b = business
    order = b.create([{"productId": "cable", "quantity": 1}])
    b.cancel(order["id"])

    cancelled = b.gql(
        "Получить отменённые заказы",
        "{ orders(status: CANCELLED) { id status } }",
    )["data"]["orders"]

    b.api.expect(
        "Созданный заказ появился среди отменённых",
        [row for row in cancelled if row["id"] == order["id"]],
        [{"id": order["id"], "status": "CANCELLED"}],
    )
    b.api.expect("Фильтр не вернул другой статус", all(row["status"] == "CANCELLED" for row in cancelled), True)

    active = b.gql(
        "Проверить список активных заказов отдельно",
        "{ orders(status: CREATED) { id } }",
    )["data"]["orders"]
    b.api.expect("Отменённого заказа нет среди активных", any(row["id"] == order["id"] for row in active), False)
