from types import SimpleNamespace

import httpx
import pytest

from tests.live.test_deployment import get


@pytest.mark.parametrize("failure", [httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout])
@pytest.mark.case("Публичный сервер: сетевой сбой явно включённой проверки считается ошибкой")
def test_network_failure_is_failure(failure):
    def unavailable(*args, **kwargs):
        raise failure("controlled network failure")
    api = SimpleNamespace(url="http://example.invalid", http=unavailable)
    with pytest.raises(pytest.fail.Exception, match="недоступен по сети"):
        get(api, "/health")

@pytest.mark.case("Магазин: запрос сверх лимита отклоняется до чтения следующего блока")
def test_storefront_request_body_limit():
    import asyncio
    from starlette.requests import Request
    from examples.shop.web.server import graphql_proxy

    chunks = iter([b"x" * (1024 * 1024), b"x"])
    async def receive():
        return {"type": "http.request", "body": next(chunks), "more_body": True}

    request = Request({"type": "http", "headers": []}, receive)
    assert asyncio.run(graphql_proxy(request)).status_code == 413
