"""Offline regression checks for explicit deployment checks."""
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
