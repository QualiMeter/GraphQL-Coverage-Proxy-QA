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
from coverage_qa.tracking import begin_current_test
from coverage_qa.reporting.progress import emit, evidence_id


@pytest.fixture
def storefront(request):
    deployed_run_id = request.config.getoption("--deployed-run-id")
    if deployed_run_id:
        business_stack = SimpleNamespace(
            proxy=SimpleNamespace(base=request.config.getoption("--coverage-url").rstrip("/")),
            upstream=SimpleNamespace(base=request.config.getoption("--upstream-url").rstrip("/")),
            key=os.environ["COVERAGE_API_KEY"],
        )
    else:
        business_stack = request.getfixturevalue("business_stack")
    entry = ensure_entry(request)
    api = API(business_stack.proxy.base + "/graphql", entry, business_stack.key)
    run = CoverageRun(api, business_stack, request.config.api_report, total_fields=35)
    run.deployed = bool(deployed_run_id)
    server = None
    browser = context = page = None
    token = None
    created_orders = set()

    def remember_order(body):
        data = body.get("data") if isinstance(body, dict) else None
        order = data.get("createOrder") if isinstance(data, dict) else None
        if isinstance(order, dict) and order.get("id"):
            created_orders.add(order["id"])
    slug = "shop-" + evidence_id(request.node.nodeid)

    def direct(query, variables=None):
        status, body = api.http("Независимая проверка PostgreSQL через API", "POST", business_stack.upstream.base + "/graphql",
                                payload={"query": query, "variables": variables}, auth=False,
                                headers={"Authorization": "Bearer " + token} if token else {})
        api.expect("Проверочное чтение: HTTP", status, 200)
        api.expect("Проверочное чтение: нет GraphQL errors", body.get("errors", []), [])
        remember_order(body)
        return body["data"]

    try:
        if deployed_run_id:
            run.run_id = deployed_run_id
            run.record = {"testId": request.node.nodeid, "serviceId": None,
                          "snapshotId": None, "runId": run.run_id, "schemaHash": None,
                          "name": "FORMA Demo Shop · E2E", "exchanges": [],
                          "checkpoints": [], "requestDetails": {}}
            request.config.api_report["coverageRuns"].append(run.record)
            entry["coverageContext"] = {"runId": run.run_id}
        else:
            run.prepare(request.node.nodeid)
        if run.execution is None:
            run.execution = begin_current_test(run.base, run.run_id, api.key)
        token = direct('mutation($password:String!){login(email:"alice@example.test",password:$password){token}}',
                       {"password": "test-password-2026"})["login"]["token"]
        server = Server("examples.shop.web.server:app", ROOT, request.config.run_folder / (slug + ".log"), {
            "SHOP_GRAPHQL_URL": business_stack.proxy.base + "/graphql",
            "SHOP_COVERAGE_API_KEY": business_stack.key, "SHOP_COVERAGE_RUN_ID": run.run_id,
            "SHOP_COVERAGE_TEST_EXECUTION_ID": run.execution.id if run.execution is not None else "",
        })
        server.start()
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
            try:
                browser = playwright.chromium.launch(headless=True, **({"channel": channel} if channel else {}))
            except Exception as exc:
                if "Executable doesn't exist" in str(exc):
                    pytest.skip("Браузер Playwright не установлен. Запустите: python -m playwright install chromium")
                raise
            context = browser.new_context(viewport={"width": 1440, "height": 1050})
            page = context.new_page()
            starts = {}

            def started(req):
                if req.url.endswith("/api/graphql"):
                    starts[req] = time.perf_counter()

            def response_received(response):
                if not response.url.endswith("/api/graphql"):
                    return
                payload = response.request.post_data_json
                try:
                    body = response.json()
                except Exception:
                    body = {"rawResponse": response.text()}
                remember_order(body)
                event = {"kind": "request", "title": "Магазин: " + (payload.get("operationName") or "GraphQL"),
                         "method": "POST", "url": response.url, "request": redact(payload), "response": redact(body),
                         "headers": {"Authorization": "<redacted>"} if "authorization" in response.request.headers else {},
                         "httpStatus": response.status, "durationMs": round((time.perf_counter() - starts.get(response.request, time.perf_counter())) * 1000, 2),
                         "error": None}
                entry["events"].append(event)
                run.record["exchanges"].append({"title": event["title"], "operationName": payload.get("operationName"),
                                                "queryHash": hashlib.sha256(payload["query"].encode()).hexdigest(),
                                                "declaredSelectedFields": [], "httpStatus": response.status,
                                                "eventIndex": len(entry["events"]) - 1, "runId": run.run_id,
                                                "attribution": "browser-operation-aggregate"})
                emit()

            page.on("request", started)
            page.on("response", response_received)
            page.goto(server.base)
            expect(page.get_by_test_id("product-keyboard")).to_be_visible()
            entry["transport"] = "browser → storefront → coverage-proxy → commerce-api"
            entry["target"] = server.base
            try:
                yield SimpleNamespace(page=page, api=api, direct=direct, run=run, remember_order=remember_order)
            finally:
                if page:
                    image_name = slug + ".png"
                    page.screenshot(path=str(request.config.run_folder / image_name), full_page=True)
                    entry["events"].append({"kind": "attachment", "title": "Магазин: результат браузерного сценария", "path": image_name})
                if browser:
                    browser.close()
    finally:
        try:
            if server:
                server.stop()
            for order_id in created_orders:
                direct("mutation($id:ID!){cancelOrder(id:$id){id status}}", {"id": order_id})
        finally:
            if run.run_id:
                if run.deployed:
                    run.finished = True
                else:
                    run.finish()
                run.snapshot("Фактическое покрытие браузерного магазина")
            api.client.close()
