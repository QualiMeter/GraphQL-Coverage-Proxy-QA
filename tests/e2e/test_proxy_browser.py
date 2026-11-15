import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright


pytestmark = [pytest.mark.coverage, pytest.mark.browser]


@pytest.mark.case("Браузер: клик → реальный прокси → ответ на странице → сохранённые поля")
def test_browser_exchange(coverage_run, request):
    run = coverage_run
    html = (Path(__file__).parents[1] / "support" / "upstream" / "browser.html").read_bytes()
    errors = []

    class Bridge(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(html)

        def do_POST(self):
            try:
                if self.path != "/exchange":
                    self.send_error(404)
                    return
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
                status, payload = run.graphql("Браузер через тестовый bridge", body["query"], operation=body["operationName"],
                                             fields={"Query.user", "User.id", "User.name"})
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(payload).encode())
            except Exception as exc:
                errors.append(str(exc))
                self.send_error(502)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Bridge)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    folder = request.config.run_folder
    with sync_playwright() as playwright:
        channel = os.environ.get("API_TEST_BROWSER_CHANNEL")
        if not channel and os.name == "nt":
            edge_candidates = [
                Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")) / "Microsoft/Edge/Application/msedge.exe",
                Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Microsoft/Edge/Application/msedge.exe",
                Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"),
                Path("C:/Program Files/Microsoft/Edge/Application/msedge.exe"),
            ]
            if any(p.exists() for p in edge_candidates):
                channel = "msedge"
        browser = None
        context = None
        page = None
        try:
            try:
                browser = playwright.chromium.launch(headless=True, **({"channel": channel} if channel else {}))
            except Exception as exc:
                if "Executable doesn't exist" in str(exc):
                    pytest.skip("Браузер Playwright не установлен. Запустите: python -m playwright install chromium")
                raise
            context = browser.new_context(viewport={"width": 1100, "height": 700})
            page = context.new_page()
            page.goto(f"http://127.0.0.1:{server.server_port}/")
            page.get_by_role("button", name="Получить пользователя").click()
            expect(page.get_by_role("status")).to_contain_text('"Anna"', timeout=15000)
            payload = json.loads(page.get_by_role("status").inner_text())
            run.api.expect("Данные реально появились в браузере", payload, {"data": {"user": {"id": "1", "name": "Anna"}}})
            run.api.expect("Нет ошибок тестового bridge", errors, [])
            checkpoint = run.snapshot("После клика в браузере")
            run.expect_fields(checkpoint, {"Query.user", "User.id", "User.name"})
            run.api.expect("Ровно одно наблюдение браузера", len(checkpoint["requests"]), 1)
        finally:
            try:
                if page:
                    page.screenshot(path=str(folder / "browser.png"), full_page=True)
                    run.api.entry["events"].append({"kind": "attachment", "title": "Результат браузерного сценария", "path": "browser.png"})
            finally:
                if browser:
                    browser.close()
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)
