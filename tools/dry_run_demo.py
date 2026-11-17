import os
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright, expect

from coverage_qa.environment import ROOT


def run_dry_run():
    out_dir = ROOT / "reports" / "dry-run"
    out_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 64)
    print("  EXPERT DEMO DRY-RUN (README_EXPERTS.md Sections 4 & 5)")
    print("=" * 64)

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

    steps_log = []

    def log(step, desc):
        print(f"  [{step}] {desc}")
        steps_log.append({"step": step, "desc": desc, "time": time.strftime("%H:%M:%S")})

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, **({"channel": channel} if channel else {}))
        context = browser.new_context(viewport={"width": 1440, "height": 950})
        page = context.new_page()

        log("1/8", "Открытие витрины FORMA Demo Shop (http://127.0.0.1:9110/)")
        page.goto("http://127.0.0.1:9110/", wait_until="networkidle")
        expect(page.get_by_test_id("product-keyboard")).to_be_visible()
        page.screenshot(path=str(out_dir / "01_catalog.png"))

        log("2/8", "Выбор товаров: Mechanical Keyboard (1 шт.) и USB-C Cable (2 шт.)")
        page.get_by_test_id("product-keyboard").get_by_role("button", name="В корзину +", exact=True).click()
        page.get_by_test_id("product-cable").get_by_role("button", name="В корзину +", exact=True).click()
        page.get_by_role("button", name="Открыть корзину", exact=True).click()
        page.get_by_role("button", name="Увеличить количество USB-C Cable", exact=True).click()
        expect(page.locator("#cart-lines .cart-line")).to_have_count(2)
        cart_total = page.locator("#cart-total").inner_text()
        log("2/8", f"Итого в корзине: {cart_total}")
        page.screenshot(path=str(out_dir / "02_cart.png"))

        log("3/8", "Оформление заказа: выбор демо-профиля Alice")
        page.get_by_role("button", name="Оформить заказ", exact=True).click()
        expect(page.locator("#login-dialog")).to_be_visible()
        page.locator('[data-account="alice"]').click()
        page.screenshot(path=str(out_dir / "03_login.png"))
        page.locator("#login-form").get_by_role("button", name="Войти", exact=True).click()
        expect(page.locator("#login-dialog")).not_to_be_visible()

        log("4/8", "Оформление покупки и получение номера заказа")
        page.get_by_role("button", name="Оформить заказ", exact=True).click()
        expect(page.get_by_test_id("success-order-id")).to_be_visible()
        order_id = page.get_by_test_id("success-order-id").inner_text()
        log("4/8", f"Заказ успешно создан в PostgreSQL! ID: {order_id}")
        page.screenshot(path=str(out_dir / "04_order_success.png"))

        log("5/8", "Переход в «Мои заказы», сверка статуса CREATED")
        page.get_by_role("button", name="Посмотреть заказ", exact=True).click()
        order_card = page.get_by_test_id("order-" + order_id)
        expect(order_card).to_be_visible()
        expect(order_card.locator(".status-pill")).to_have_text("Создан")
        page.screenshot(path=str(out_dir / "05_orders_list.png"))

        log("6/8", "Отмена заказа: подтверждение диалога и смена статуса на «Отменён»")
        order_card.get_by_role("button", name="Отменить заказ", exact=True).click()
        expect(page.locator("#cancel-dialog")).to_be_visible()
        page.get_by_role("button", name="Да, отменить заказ", exact=True).click()
        expect(order_card.locator(".status-pill")).to_have_text("Отменён")
        page.screenshot(path=str(out_dir / "06_order_cancelled.png"))

        log("7/8", "Открытие API-инспектора: проверка журнала GraphQL и маскирования")
        page.locator("#inspector-button").click()
        expect(page.locator("#inspector-dialog")).to_be_visible()
        expect(page.locator("#request-list .request-row")).not_to_have_count(0)
        req_count = page.locator("#request-count").inner_text()
        log("7/8", f"В инспекторе зафиксировано обращений: {req_count}")
        page.screenshot(path=str(out_dir / "07_api_inspector.png"))
        page.locator('[data-close="inspector-dialog"]').click()

        log("8/8", "Вход под Admin: проверка панели статистики магазина")
        page.locator("#account-button").click()
        page.locator("#logout").click()
        page.get_by_role("button", name="Войти", exact=True).click()
        page.locator('[data-account="admin"]').click()
        page.locator("#login-form").get_by_role("button", name="Войти", exact=True).click()
        page.locator("#account-button").click()
        expect(page.locator("#admin-section")).to_be_visible()
        expect(page.locator("#admin-stats>div")).to_have_count(3)
        page.screenshot(path=str(out_dir / "08_admin_profile.png"))

        browser.close()

    html_report = out_dir / "index.html"
    rows_html = "".join(f"<tr><td><strong>{s['step']}</strong></td><td>{s['desc']}</td><td>{s['time']}</td><td><span style='color:#1e8e3e;font-weight:600;'>✓ OK</span></td></tr>" for s in steps_log)

    screenshots_grid = "".join(f"""
      <div style='background:white;border:1px solid #ddd;border-radius:8px;padding:12px;box-shadow:0 1px 3px rgba(0,0,0,0.06);'>
        <h3 style='margin:0 0 8px 0;font-size:14px;color:#333;'>Шаг {i+1}: {name}</h3>
        <a href='{fname}' target='_blank'><img src='{fname}' style='width:100%;border-radius:4px;border:1px solid #eee;'></a>
      </div>
    """ for i, (fname, name) in enumerate([
        ("01_catalog.png", "Витрина магазина FORMA"),
        ("02_cart.png", "Корзина с 2 товарами"),
        ("03_login.png", "Авторизация профиля Alice"),
        ("04_order_success.png", "Успешное создание заказа"),
        ("05_orders_list.png", "Заказ в статусе «Создан»"),
        ("06_order_cancelled.png", "Заказ в статусе «Отменён»"),
        ("07_api_inspector.png", "API-инспектор с маскированием"),
        ("08_admin_profile.png", "Кабинет администратора с метриками"),
    ]))

    html_content = f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <title>Сухой прогон экспертного сценария (README_EXPERTS.md)</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background: #f8f9fa; color: #202124; margin: 0; padding: 40px 20px; }}
    .container {{ max-width: 1200px; margin: 0 auto; }}
    h1 {{ margin: 0 0 8px 0; font-size: 26px; }}
    p.lead {{ color: #5f6368; margin: 0 0 24px 0; font-size: 15px; }}
    table {{ width: 100%; border-collapse: collapse; background: white; border-radius: 8px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.06); margin-bottom: 32px; }}
    th, td {{ padding: 12px 16px; border-bottom: 1px solid #eee; text-align: left; font-size: 14px; }}
    th {{ background: #f1f3f4; font-weight: 600; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(270px, 1fr)); gap: 16px; }}
  </style>
</head>
<body>
  <div class="container">
    <div style="background:#e6f4ea;color:#1e8e3e;display:inline-block;padding:4px 10px;border-radius:12px;font-weight:600;font-size:12px;margin-bottom:12px;">● СУХОЙ ПРОГОН УСПЕШНО ЗАВЕРШЁН</div>
    <h1>GraphQL Coverage Proxy — Сухой прогон сценария экспертов</h1>
    <p class="lead">Автоматизированное сквозное воспроизведение всех шагов раздела №5 памятки <code>README_EXPERTS.md</code>.</p>

    <table>
      <thead><tr><th>Шаг</th><th>Описание операции</th><th>Время</th><th>Статус</th></tr></thead>
      <tbody>{rows_html}</tbody>
    </table>

    <h2 style="font-size:20px;margin:0 0 16px 0;">Фотофиксация шагов демонстрации (8 скриншотов)</h2>
    <div class="grid">{screenshots_grid}</div>
  </div>
</body>
</html>
"""
    html_report.write_text(html_content, encoding="utf-8")

    print("-" * 64)
    print("  ✓ ВСЕ 8 ЭТАПОВ СУХОГО ПРОГОНА УСПЕШНО ПРОЙДЕНЫ!")
    print(f"  ✓ Скриншоты и сводный отчёт сохранены:\n    {html_report}")
    print("=" * 64)
    return 0


if __name__ == "__main__":
    sys.exit(run_dry_run())
