"""Controlled test server, not a replacement for the team's backend/frontend."""

import asyncio
import gzip
import json
import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from graphql import GraphQLError, build_schema, graphql


SDL = Path(__file__).with_name("schema.graphql").read_text(encoding="utf-8")
schema = build_schema(SDL)
products = [{"id": "10", "title": "Keyboard"}, {"id": "20", "title": "Mouse"}]
orders = [{"id": "1", "total": 50.0}]
calls = []
schema.get_type("Query").fields["user"].resolve = lambda _, info, id: {"id": "1", "name": os.environ.get("QA_UPSTREAM_NAME", "Anna"), "orders": list(orders)} if id == "1" else None
schema.get_type("Query").fields["products"].resolve = lambda *_: products


def create_order(_, info, productId):
    if productId not in {p["id"] for p in products}:
        raise GraphQLError("Product not found", extensions={"code": "NOT_FOUND"})
    order = {"id": str(len(orders) + 1), "total": 50.0 if productId == "10" else 20.0}
    orders.append(order)
    return order


schema.get_type("Mutation").fields["createOrder"].resolve = create_order
app = FastAPI()


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/__test/calls")
async def inspect_calls():
    # Test-generated credentials are never returned or logged.
    return calls


@app.post("/graphql")
async def execute(request: Request):
    body = await request.body()
    mode = request.headers.get("x-test-mode", "normal")
    calls.append({"body": body.decode("utf-8", errors="replace"), "mode": mode,
                  "hasCoverageKey": "x-coverage-api-key" in request.headers,
                  "hasRunId": "x-coverage-run-id" in request.headers,
                  "authorizationPresent": "authorization" in request.headers,
                  "hasCookie": "cookie" in request.headers})
    if mode == "delay":
        await asyncio.sleep(31)
    if mode == "non-json":
        return Response(b"upstream temporary failure", status_code=503, media_type="text/plain")
    if mode == "no-data":
        return JSONResponse({"data": None, "errors": [{"message": "Resolver failed"}]})
    if mode == "partial":
        return JSONResponse({"data": {"user": {"id": "1", "name": None}},
                             "errors": [{"message": "Name failed", "path": ["user", "name"]}]})
    if mode == "http-error":
        return JSONResponse({"data": None, "errors": [{"message": "Unavailable"}]}, status_code=503)
    try:
        payload = json.loads(body)
    except ValueError:
        return JSONResponse({"errors": [{"message": "Invalid JSON"}]}, status_code=400)
    if not isinstance(payload, dict) or not isinstance(payload.get("query"), str):
        return JSONResponse({"errors": [{"message": "Expected a query"}]}, status_code=400)
    result = await graphql(schema, payload["query"], variable_values=payload.get("variables"), operation_name=payload.get("operationName"))
    if mode == "gzip":
        return Response(gzip.compress(json.dumps(result.formatted).encode(), mtime=0),
                        headers={"Content-Encoding": "gzip", "Content-Type": "application/json"})
    return JSONResponse(result.formatted)


@app.get("/browser", response_class=HTMLResponse)
async def browser_fixture():
    # Same-origin bridge is bound to one test run on the Python test runner.
    return Path(__file__).with_name("browser.html").read_text(encoding="utf-8")
