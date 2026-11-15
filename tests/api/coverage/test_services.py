"""Real per-service routing and outbound restrictions through public HTTP APIs."""
from uuid import uuid4

import pytest

from tests.support.coverage_client import CoverageRun


@pytest.mark.case("Сервисы: run направляется в собственный endpoint, данные двух upstream не смешиваются")
def test_two_real_upstreams_are_routed(coverage_run):
    r = coverage_run
    if not getattr(r.stack, "secondary", None):
        pytest.skip("Two-upstream check requires managed local infrastructure")
    other = CoverageRun(r.api, r.stack, r.report)
    try:
        other.prepare("secondary-service", endpoint=r.stack.secondary.base + "/graphql")
        query = 'query Route {user(id:"1"){id name}}'
        one = r.graphql("Первый реальный upstream", query, operation="Route")[1]
        two = other.graphql("Второй реальный upstream", query, operation="Route")[1]
        r.api.expect("Первый endpoint", one["data"]["user"]["name"], "Anna")
        r.api.expect("Второй endpoint выбран через service.endpointUrl", two["data"]["user"]["name"], "Secondary upstream")
        first, second = r.snapshot("Первый сервис"), other.snapshot("Второй сервис")
        r.api.expect("Каждый run содержит своё обращение", (len(first["requests"]), len(second["requests"])), (1, 1))
        r.api.expect("ID наблюдений не пересекаются", {x["id"] for x in first["requests"]}.isdisjoint(x["id"] for x in second["requests"]), True)
        for checkpoint in (first, second):
            r.expect_fields(checkpoint, {"Query.user", "User.id", "User.name"})
    finally:
        if other.run_id:
            other.finish()
            other.snapshot("Итог второго upstream")


@pytest.mark.parametrize("endpoint", ["file:///etc/passwd", "http://169.254.169.254/latest/meta-data", "http://127.0.0.1:1/graphql", "https://user:password@127.0.0.1/graphql"])
@pytest.mark.case("Сервисы: запрещённый endpoint отклоняется до исходящего запроса")
def test_unsafe_service_endpoints_rejected(coverage_run, endpoint):
    r = coverage_run
    before = r.api.http("Исходные вызовы upstream", "GET", r.stack.upstream.base + "/__test/calls", auth=False)[1]
    r.rest("Запрет endpoint", "POST", "/api/services", {"key": "blocked-" + uuid4().hex, "name": "Blocked", "endpointUrl": endpoint}, expected=422)
    after = r.api.http("Upstream не вызван", "GET", r.stack.upstream.base + "/__test/calls", auth=False)[1]
    r.api.expect("Исходящий HTTP не выполнялся", after, before)


@pytest.mark.case("Introspection: клиент не может подменить URL сохранённого сервиса")
def test_introspection_url_override_rejected(coverage_run):
    r = coverage_run
    r.rest("URL берётся только из service", "POST", "/api/schemas/import/introspection",
           {"serviceId": r.service_id, "endpointUrl": "http://127.0.0.1:1/graphql"}, expected=422)
