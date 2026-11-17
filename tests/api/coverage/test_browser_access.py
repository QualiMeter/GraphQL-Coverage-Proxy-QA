from uuid import uuid4

import pytest

ORIGIN = "http://localhost:5173"


@pytest.mark.parametrize("role", ["viewer", "analyst", "operator"])
@pytest.mark.case("Доступ: cookie-сессия, роль, grant, CSRF и немедленный отзыв прав")
def test_cookie_roles_grants_and_csrf(coverage_run, role):
    r = coverage_run
    username = "qa-" + uuid4().hex
    password = "only-test-password-2026"
    user = r.rest("Создать тестового пользователя", "POST", "/api/auth/users",
                  {"username": username, "password": password, "role": role}, expected=201)

    def browser(method, path, payload=None, expected=200, csrf=None):
        status, body = r.api.http("Cookie: " + method + " " + path, method, r.base + path,
                                 payload=payload, auth=False,
                                 headers={"Origin": ORIGIN, **({"X-CSRF-Token": csrf} if csrf else {})})
        r.api.expect("Статус cookie-запроса", status, expected)
        return body

    session = browser("POST", "/api/auth/login", {"username": username, "password": password})
    csrf = session["csrfToken"]
    try:
        r.api.expect("Роль получена из настоящей сессии", session["user"]["role"], role)
        r.api.expect("Сессионная cookie выдана сервером", "coverage_dev_session" in r.api.client.cookies, True)
        browser("GET", f"/api/runs/{r.run_id}/coverage", expected=404)
        browser("GET", f"/api/runs/{r.run_id}/requests", expected=403 if role == "viewer" else 404)
        r.rest("Выдать grant", "PUT", f"/api/auth/users/{user['id']}/services/{r.service_id}", expected=204)
        browser("GET", f"/api/runs/{r.run_id}/coverage")
        browser("GET", f"/api/runs/{r.run_id}/requests", expected=403 if role == "viewer" else 200)
        history = browser("GET", f"/api/runs?serviceId={r.service_id}")
        r.api.expect("Служебная metadata не выдаётся browser-пользователю", history[0]["metadata"], {})
        browser("PATCH", f"/api/services/{r.service_id}", {"name": "Changed"}, expected=403)
        browser("PATCH", f"/api/services/{r.service_id}", {"name": "Changed"}, csrf=csrf,
                expected=200 if role == "operator" else 403)
        r.rest("Отозвать grant", "DELETE", f"/api/auth/users/{user['id']}/services/{r.service_id}", expected=204)
        browser("GET", f"/api/runs/{r.run_id}/coverage", expected=404)
        browser("GET", f"/api/runs/{r.run_id}/requests", expected=403 if role == "viewer" else 404)
        browser("POST", "/api/auth/logout", expected=403)
    finally:
        browser("POST", "/api/auth/logout", expected=204, csrf=csrf)
        r.rest("Деактивировать тестового пользователя", "PATCH", f"/api/auth/users/{user['id']}", {"isActive": False})
    r.api.expect("Выход удалил cookie-сессию", browser("GET", "/api/auth/session")["user"], None)


@pytest.mark.case("Доступ: публичный сервис открывает агрегаты, но сохраняет закрытый журнал и metadata")
def test_public_service_does_not_publish_private_evidence(coverage_run):
    r = coverage_run
    r.graphql("Данные для публичного графа", 'query PublicUser{user(id:"1"){id name}}', operation="PublicUser")
    r.rest("Опубликовать сервис", "PATCH", f"/api/services/{r.service_id}", {"isPublic": True})
    try:
        service = r.rest("Публичная карточка сервиса", "GET", f"/api/services/{r.service_id}", auth=False)
        r.api.expect("Внутренний endpoint не раскрыт", service["endpointUrl"], None)
        graph = r.rest("Анонимный граф покрытия", "GET", f"/api/runs/{r.run_id}/coverage", auth=False)
        r.api.expect("Публичный граф содержит настоящие hits", graph["summary"]["coveredFields"], 3)
        rows = r.rest("Публичная история", "GET", f"/api/runs?serviceId={r.service_id}", auth=False)
        r.api.expect("Служебная metadata скрыта", rows[0]["metadata"], {})
        r.rest("Публичность не открывает журнал", "GET", f"/api/runs/{r.run_id}/requests", expected=401, auth=False)
        id = r.record["exchanges"][0]["observedRequestIds"][0]
        r.rest("Публичность не открывает detail", "GET", f"/api/runs/{r.run_id}/requests/{id}", expected=401, auth=False)
    finally:
        r.rest("Снять публикацию", "PATCH", f"/api/services/{r.service_id}", {"isPublic": False})
    r.rest("Приватный граф снова закрыт", "GET", f"/api/runs/{r.run_id}/coverage", expected=404, auth=False)
    r.rest("Приватная история снова закрыта", "GET", f"/api/runs?serviceId={r.service_id}", expected=404, auth=False)
    response = next(e for e in reversed(r.api.entry["events"]) if e["kind"] == "request")
    r.api.expect("Отказ не раскрывает total приватной истории", response["responseHeaders"].get("x-total-count"), None)


@pytest.mark.case("Сессия: повторный вход отзывает прежнюю cookie, новая сессия остаётся рабочей")
def test_login_rotates_and_revokes_previous_cookie(coverage_run):
    r = coverage_run
    credentials = {"username": "rotation-" + uuid4().hex, "password": "only-test-password-2026", "role": "viewer"}
    user = r.rest("Создать пользователя для ротации", "POST", "/api/auth/users", credentials, expected=201)

    def session_request(method, path, payload=None, cookie=None, csrf=None):
        headers = {"Origin": ORIGIN}
        if cookie:
            headers["Cookie"] = "coverage_dev_session=" + cookie
        if csrf:
            headers["X-CSRF-Token"] = csrf
        status, body = r.api.http("Сессия: " + path, method, r.base + path, payload=payload, headers=headers, auth=False)
        r.api.expect("Статус проверки сессии", status, 204 if path.endswith("logout") else 200)
        return body

    current = None
    try:
        login = {k: credentials[k] for k in ("username", "password")}
        current = session_request("POST", "/api/auth/login", login)
        old = r.api.client.cookies.get("coverage_dev_session")
        cookie = next(c for c in r.api.client.cookies.jar if c.name == "coverage_dev_session")
        r.api.expect("Cookie действительно HttpOnly", cookie.has_nonstandard_attr("HttpOnly"), True)
        r.api.expect("Cookie имеет SameSite=lax", cookie.get_nonstandard_attr("SameSite"), "lax")
        current = session_request("POST", "/api/auth/login", login)
        new = r.api.client.cookies.get("coverage_dev_session")
        r.api.expect("Токен сменился, значения не выводятся в отчёт", bool(old and new and old != new), True)
        r.api.expect("Старая cookie больше не авторизует", session_request("GET", "/api/auth/session", cookie=old)["user"], None)
        r.api.expect("Новая сессия принадлежит нужному пользователю", session_request("GET", "/api/auth/session")["user"]["id"], user["id"])
    finally:
        if current:
            session_request("POST", "/api/auth/logout", csrf=current["csrfToken"])
        r.rest("Деактивировать пользователя ротации", "PATCH", f"/api/auth/users/{user['id']}", {"isActive": False})
