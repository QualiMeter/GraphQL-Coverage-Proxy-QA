"""Separate demo storefront. Forwards GraphQL to our commerce API only."""

import argparse
import os
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles


ROOT = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(app):
    app.state.client = httpx.AsyncClient(timeout=40, trust_env=False, follow_redirects=False)
    app.state.target = os.environ.get("SHOP_GRAPHQL_URL", "http://127.0.0.1:9100/graphql")
    app.state.coverage_key = os.environ.get("SHOP_COVERAGE_API_KEY")
    app.state.run_id = os.environ.get("SHOP_COVERAGE_RUN_ID")
    yield
    await app.state.client.aclose()


app = FastAPI(title="FORMA Demo Shop", lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/")
async def index():
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/api/config")
async def config(request: Request):
    return {"coverageEnabled": bool(request.app.state.coverage_key and request.app.state.run_id),
            "runId": request.app.state.run_id}


@app.post("/api/graphql")
async def graphql_proxy(request: Request):
    body = await request.body()
    if len(body) > 1024 * 1024:
        return JSONResponse({"errors": [{"message": "Request too large"}]}, status_code=413)
    headers = {"Content-Type": "application/json"}
    if request.app.state.coverage_key:
        headers["X-Coverage-Api-Key"] = request.app.state.coverage_key
    if request.app.state.run_id:
        headers["X-Coverage-Run-Id"] = request.app.state.run_id
    if request.headers.get("authorization"):
        headers["Authorization"] = request.headers["authorization"]
    try:
        upstream = await request.app.state.client.post(request.app.state.target, content=body, headers=headers)
    except httpx.TimeoutException:
        return JSONResponse({"errors": [{"message": "GraphQL API timed out"}]}, status_code=504)
    except httpx.HTTPError:
        return JSONResponse({"errors": [{"message": "GraphQL API unavailable"}]}, status_code=502)
    return Response(upstream.content, status_code=upstream.status_code,
                    headers={"Content-Type": upstream.headers.get("content-type", "application/json"), "Cache-Control": "no-store"})


app.mount("/assets", StaticFiles(directory=ROOT / "static"), name="shop-assets")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=9110)
    args = parser.parse_args()
    import uvicorn
    print(f"FORMA shop: http://localhost:{args.port}/", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()
