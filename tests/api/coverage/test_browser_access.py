"""Real cookie sessions, roles, CSRF and service grants over HTTP."""
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
        r.api.expect("Cookie HttpOnly выдана сервером", "coverage_dev_session" in r.api.client.cookies, True)
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
