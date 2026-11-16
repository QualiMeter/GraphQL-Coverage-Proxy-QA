"""TST-07 acceptance test: verify server-side run coverage in React UI against REST API."""
import mimetypes
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest
from playwright.sync_api import expect, sync_playwright

from coverage_qa.environment import ROOT

pytestmark = [pytest.mark.coverage, pytest.mark.browser]


def find_frontend_dist():
    candidates = [
        ROOT.parent / "GraphQL-Coverage-Proxy-Frontend" / "dist",
        ROOT.parent / "frontend" / "dist",
        ROOT / "dist",
    ]
    for c in candidates:
        if (c / "index.html").is_file():
            return c
    raise FileNotFoundError(
        "Frontend production build (dist/index.html) not found. "
        "Run 'npm run build' inside GraphQL-Coverage-Proxy-Frontend."
    )


class FrontendBridge(BaseHTTPRequestHandler):
    dist_dir = None
    backend_base = None

    def log_message(self, *_):
        pass

    def do_GET(self):
        if self.path.startswith("/coverage"):
            sub = self.path[len("/coverage"):].split("?")[0].lstrip("/")
            target = self.dist_dir / ("index.html" if not sub or sub == "index.html" else sub)
            if target.is_file():
                self.send_response(200)
                mime = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
                self.send_header("Content-Type", mime)
                self.end_headers()
                self.wfile.write(target.read_bytes())
                return
            self.send_error(404)
            return

        if self.path.startswith("/api"):
            try:
                with httpx.Client(timeout=15.0) as client:
                    resp = client.get(self.backend_base + self.path)
                    self.send_response(resp.status_code)
                    for k, v in resp.headers.items():
                        if k.lower() not in ("transfer-encoding", "content-encoding", "content-length"):
                            self.send_header(k, v)
                    self.send_header("Content-Length", str(len(resp.content)))
                    self.end_headers()
                    self.wfile.write(resp.content)
            except Exception:
                self.send_error(502)
            return

        self.send_error(404)


@pytest.mark.case("TST-07: Выбор серверного прогона в React → сверка покрытия графа и таблицы с REST API")
def test_tst07_run_coverage_in_react(coverage_run, request):
    run = coverage_run
    folder = request.config.run_folder

    # 1. Сформировать реальное покрытие через GraphQL-запросы прокси
    query = '{ user(id: "1") { id name orders { total } } }'
    run.graphql(
        "TST-07: Запрос карточки пользователя с заказами",
        query,
        fields={"Query.user", "User.id", "User.name", "User.orders", "Order.total"},
    )

    # 2. Опубликовать сервис и задать явное имя для селектора в React
    run.rest(
        "Задать имя и опубликовать тестовый сервис для анонимного просмотра в React",
        "PATCH",
        f"/api/services/{run.service_id}",
        {"name": "TST-07 Service", "isPublic": True},
    )

    # 3. Получить эталонный граф покрытия из REST API
    checkpoint = run.snapshot("TST-07: Контрольный снимок покрытия из REST")
    cov_api = checkpoint["graph"]
    summary = cov_api["summary"]
    run.expect_fields(checkpoint, {"Query.user", "User.id", "User.name", "User.orders", "Order.total"})

    # 4. Подготовить адрес фронтенда (локальный bridge или публичный стенд)
    live_base = request.config.getoption("--live-base-url")
    server = None
    server_thread = None

    if live_base:
        frontend_url = f"{live_base.rstrip('/')}/coverage/"
    else:
        dist_dir = find_frontend_dist()
        FrontendBridge.dist_dir = dist_dir
        FrontendBridge.backend_base = run.base
        server = ThreadingHTTPServer(("127.0.0.1", 0), FrontendBridge)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        frontend_url = f"http://127.0.0.1:{server.server_port}/coverage/"

    # 5. Запуск браузера Playwright и проверка React-интерфейса
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
        page = None
        try:
            try:
                browser = playwright.chromium.launch(headless=True, **({"channel": channel} if channel else {}))
            except Exception as exc:
                if "Executable doesn't exist" in str(exc):
                    pytest.skip("Браузер Playwright не установлен. Запустите: python -m playwright install chromium")
                raise

            context = browser.new_context(viewport={"width": 1400, "height": 950})
            page = context.new_page()

            # Открыть интерфейс покрытия
            page.goto(frontend_url, wait_until="domcontentloaded", timeout=20000)
            page.wait_for_selector(".analytics-app", timeout=15000)

            # Выбрать созданный сервис в выпадающем меню
            service_button = page.locator("button.context-trigger", has_text="Сервис")
            service_button.click()
            page.wait_for_selector(".context-menu", timeout=5000)

            # Ожидаем появление в меню не только mock-сервиса
            page.wait_for_selector('.context-menu button[role="option"]:has-text("TST-07 Service")', timeout=10000)
            service_row = page.locator('.context-menu button[role="option"]', has_text="TST-07 Service")
            service_row.first.click()
            page.wait_for_timeout(300)

            # Перейти в раздел «История»
            page.locator("button", has_text="История").click()
            page.wait_for_selector(".run-history-table", timeout=8000)

            # Найти строку нашего прогона и нажать выбор
            run_btn = page.locator(".run-history-select").filter(has_text=run.run_id[:8])
            if not run_btn.count():
                run_btn = page.locator(".run-history-select").first
            run_name = run_btn.inner_text().strip()
            run_btn.click()

            # Дождаться перехода на страницу схемы и отрисовки графа
            page.wait_for_selector(".type-node", timeout=10000)

            # Проверка 1: Сводка покрытия в TopChrome соответствует REST API
            expected_pct = f"{summary['coveragePercent']:.2f}%"
            expected_covered = str(summary["coveredFields"])
            expected_total = str(summary["totalFields"])
            expected_uncovered = str(summary["totalFields"] - summary["coveredFields"])

            expect(page.locator(".summary-coverage .coverage-overview strong")).to_have_text(expected_pct)
            expect(page.locator('.summary-coverage button[aria-label^="Покрыто:"] strong')).to_have_text(expected_covered)
            expect(page.locator(".summary-coverage .static-metric strong")).to_have_text(expected_total)
            expect(page.locator('.summary-coverage button[aria-label^="Не покрыто:"] strong')).to_have_text(expected_uncovered)

            run.api.expect("React TopChrome: Процент общего покрытия совпадает с REST",
                           page.locator(".summary-coverage .coverage-overview strong").inner_text().strip(),
                           expected_pct)
            run.api.expect("React TopChrome: Число покрытых полей совпадает с REST",
                           page.locator('.summary-coverage button[aria-label^="Покрыто:"] strong').inner_text().strip(),
                           expected_covered)
            run.api.expect("React TopChrome: Общее число полей схемы совпадает с REST",
                           page.locator(".summary-coverage .static-metric strong").inner_text().strip(),
                           expected_total)

            # Проверка 2: Узлы графа ReactFlow показывают точный процент и статус полей
            for t in cov_api["types"]:
                node = page.locator(".type-node").filter(
                    has=page.locator("header strong", has_text=re.compile(f"^{re.escape(t['name'])}$"))
                )
                expect(node).to_be_visible()
                if t["coveragePercent"] is not None:
                    node_pct = node.locator("header b").inner_text().strip()
                    run.api.expect(f"React граф: Процент типа {t['name']}", node_pct, f"{t['coveragePercent']:.0f}%")

                for f in t["fields"]:
                    field_elem = node.locator(".node-field").filter(
                        has=page.locator(".field-name", has_text=re.compile(f"^{re.escape(f['name'])}$"))
                    )
                    expect(field_elem).to_be_visible()
                    dot = field_elem.locator(".status-dot")
                    if f["covered"]:
                        expect(dot).to_have_class(re.compile(r"\bcovered\b"))
                    else:
                        expect(dot).not_to_have_class(re.compile(r"\bcovered\b"))

            run.api.expect("React граф: Все типы и поля отображают правильный статус покрытия", True, True)

            # Проверка 3: Переключение в режим таблицы и поколоночная сверка с REST
            page.locator(".segmented button", has_text="Таблица").click()
            page.wait_for_selector(".schema-coverage-table", timeout=5000)

            table_types = {t["name"]: {f["name"]: f for f in t["fields"]} for t in cov_api["types"]}
            verified_fields = 0

            for row in page.locator(".schema-coverage-table tbody tr").all():
                type_name = row.locator(".schema-table-type").inner_text().strip()
                field_name = row.locator(".schema-table-field").inner_text().strip()
                hits_text = row.locator(".schema-table-hits").inner_text().strip()
                status_text = row.locator(".schema-table-status").inner_text().strip()

                expected_f = table_types[type_name][field_name]
                expected_hits = "—" if expected_f["hits"] is None else str(expected_f["hits"])
                expected_status = "Покрыто" if expected_f["covered"] else "Не покрыто"

                assert hits_text == expected_hits, (
                    f"Hits mismatch for {type_name}.{field_name}: UI {hits_text} != REST {expected_hits}"
                )
                assert status_text == expected_status, (
                    f"Status mismatch for {type_name}.{field_name}: UI {status_text} != REST {expected_status}"
                )
                verified_fields += 1

            run.api.expect("React таблица: Число сверенных строк полей", verified_fields, summary["totalFields"])

            # Сохранение скриншотов в отчёт
            table_screenshot = folder / "tst07-table-coverage.png"
            page.screenshot(path=str(table_screenshot), full_page=True)
            run.api.entry["events"].append({
                "kind": "attachment",
                "title": "TST-07: Таблица покрытия в React UI",
                "path": "tst07-table-coverage.png",
            })

            page.locator(".segmented button", has_text="Граф").click()
            page.wait_for_selector(".type-node", timeout=5000)
            page.wait_for_timeout(300)

            graph_screenshot = folder / "tst07-graph-coverage.png"
            page.screenshot(path=str(graph_screenshot), full_page=True)
            run.api.entry["events"].append({
                "kind": "attachment",
                "title": "TST-07: Интерактивный граф покрытия в React UI",
                "path": "tst07-graph-coverage.png",
            })

        finally:
            if browser:
                browser.close()
            if server:
                server.shutdown()
                server.server_close()
            if server_thread:
                server_thread.join(timeout=5)
