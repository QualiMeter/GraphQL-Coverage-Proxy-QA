"""Loopback HTTP interface for pytest runs."""

import argparse
import hashlib
import hmac
import json
import os
import re
import secrets
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from coverage_qa.reporting.history import new_run, save_json, update_history
from coverage_qa.reporting.html import write_report


from coverage_qa.environment import ROOT

WEB = Path(__file__).resolve().parent / "web"
RUN_ID = re.compile(r"^[A-Za-z0-9_-]{1,80}$")


def load(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


class Manager:
    def __init__(self, reports=None):
        self.reports = Path(reports or ROOT / "reports").resolve()
        self.reports.mkdir(parents=True, exist_ok=True)
        self.state = self.reports / ".panel"
        self.state.mkdir(exist_ok=True)
        self.token = secrets.token_urlsafe(32)
        self.lock = threading.Lock()
        self.process = None
        self.active = load(self.state / "active.json", {}).get("id")
        self._catalog = None

    def catalog(self):
        if self._catalog is None:
            target = self.state / "catalog.json"
            result = subprocess.run([
                sys.executable, "-m", "pytest", "-c", str(ROOT / "pyproject.toml"),
                str(ROOT / "tests"), "--collect-only", "-q", "--catalog-file", str(target),
            ], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
                env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"}, timeout=40)
            if result.returncode:
                raise RuntimeError("Не удалось получить список тестов: " + result.stdout[-2000:] + result.stderr[-1000:])
            self._catalog = [x for x in load(target, []) if x["group"] != "legacy"]
        return self._catalog

    def folder(self, id):
        if not RUN_ID.fullmatch(id):
            raise HTTPException(400, "Некорректный ID запуска")
        path = self.reports / "runs" / id
        if not (path / "manifest.json").exists():
            raise HTTPException(404, "Запуск не найден")
        return path

    def history(self):
        rows = []
        for path in sorted((self.reports / "runs").glob("*/manifest.json"), reverse=True):
            m = load(path)
            if not m:
                continue
            rows.append({k: m.get(k) for k in ("id", "label", "suite", "status", "startedAt", "localTime", "finishedAt", "passed", "failed", "skipped", "exitCode")})
        return rows

    def snapshot(self, id):
        folder = self.folder(id)
        manifest = load(folder / "manifest.json", {})
        progress = load(folder / "progress.json")
        if not progress:
            result = load(folder / "results.json", {})
            selected = load(folder / "selection.json", [])
            catalog = {x["id"]: x for x in self.catalog()}
            tests = result.get("tests", []) or [{**catalog.get(x, {"id": x, "title": x}), "status": "pending"} for x in selected]
            progress = {"id": id, "tests": [{**{k: t.get(k, catalog.get(t["id"], {}).get(k)) for k in ("id", "title", "variant", "group", "status", "duration")},
                                               "evidenceId": hashlib.sha256(t["id"].encode()).hexdigest()[:16]} for t in tests],
                        "passed": sum(t["status"] == "passed" for t in tests),
                        "failed": sum(t["status"] == "failed" for t in tests),
                        "skipped": sum(t["status"] == "skipped" for t in tests),
                        "requests": sum(e["kind"] == "request" for t in tests for e in t.get("events", [])),
                        "checks": sum(e["kind"] == "check" for t in tests for e in t.get("events", [])),
                        "failedChecks": 0, "activeTest": None}
        return {**progress, "manifest": manifest, "cancelRequested": (folder / "cancel.requested").exists(),
                "reportUrl": f"/reports/runs/{id}/index.html" if (folder / "index.html").exists() else None}

    def evidence(self, id, key):
        if not re.fullmatch(r"[a-f0-9]{16}", key):
            raise HTTPException(400, "Некорректный ID сценария")
        folder = self.folder(id)
        evidence = load(folder / "live" / (key + ".json"))
        if evidence is None:
            for test in load(folder / "results.json", {}).get("tests", []):
                if hashlib.sha256(test["id"].encode()).hexdigest()[:16] == key:
                    evidence = test
                    break
        if evidence is None:
            return {"events": [], "status": "pending", "title": "Сценарий ещё не выполнялся"}
        return evidence

    def start(self, ids, label, include_live):
        catalog = {x["id"]: x for x in self.catalog()}
        selected = list(dict.fromkeys(ids))
        if not selected or any(x not in catalog for x in selected):
            raise HTTPException(400, "Выберите существующие сценарии")
        if any(catalog[x]["group"] == "live" for x in selected) and not include_live:
            raise HTTPException(400, "Для публичного сервера включите GET-only проверки")
        with self.lock:
            if self.active:
                try:
                    manifest = self.snapshot(self.active).get("manifest", {})
                    is_running = self.process is not None and self.process.poll() is None
                    if manifest.get("status") == "starting" and is_running:
                        raise HTTPException(409, "Один запуск уже выполняется")
                    if not is_running:
                        self.active = None
                except HTTPException as exc:
                    if exc.status_code == 409:
                        raise
                    self.active = None
            folder = new_run(self.reports, label, "panel")
            save_json(folder / "selection.json", selected)
            command = [sys.executable, "-m", "coverage_qa", "test", "--suite", "all", "--local",
                       "--report-dir", str(self.reports), "--run-folder", str(folder),
                       "--selection-file", str(folder / "selection.json"), "--label", label]
            if include_live:
                command += ["--live-base-url", "https://graphql.rebyte.fun"]
            log = (folder / "panel.log").open("w", encoding="utf-8")
            try:
                self.process = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                                                env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"})
            except Exception:
                log.close()
                raise
            self.active = folder.name
            save_json(self.state / "active.json", {"id": self.active, "pid": self.process.pid})
            threading.Thread(target=self._monitor, args=(self.process, folder, log), daemon=True).start()
            return folder.name

    def _monitor(self, process, folder, log):
        try:
            code = process.wait()
            if not (folder / "results.json").exists():
                manifest = load(folder / "manifest.json", {})
                report = {"id": folder.name, "label": manifest.get("label", "Запуск"),
                          "endpoint": "Локальное окружение", "startedAt": manifest.get("startedAt"),
                          "finishedAt": datetime.now(timezone.utc).isoformat(), "exitCode": code or 2,
                          "cancelled": (folder / "cancel.requested").exists(),
                          "tests": [], "coverageRuns": [], "infrastructureErrors": ["Runner завершился без отчёта. Подробности в panel.log."]}
                save_json(folder / "results.json", report)
                write_report(report, folder / "index.html")
                update_history(folder, report)
        finally:
            log.close()

    def cancel(self, id):
        folder = self.folder(id)
        if load(folder / "manifest.json", {}).get("status") != "starting":
            raise HTTPException(409, "Запуск уже завершён")
        (folder / "cancel.requested").touch()
        # The pytest setup hook stops before the next test. Let the current
        # request and fixture teardown finish, including PostgreSQL cleanup.


class StartRequest(BaseModel):
    node_ids: list[str] = Field(min_length=1, max_length=2000)
    label: str = Field(default="Запуск из панели", min_length=1, max_length=100)
    include_live: bool = False


def create_app(manager=None):
    manager = manager or Manager()
    app = FastAPI(title="GraphQL Test Control")
    app.state.manager = manager

    @app.middleware("http")
    async def local_commands(request: Request, call_next):
        if request.method == "POST":
            token = request.headers.get("X-Panel-Token", "")
            if not hmac.compare_digest(token, manager.token):
                from fastapi.responses import JSONResponse
                return JSONResponse({"detail": "Перезагрузите панель: отсутствует токен управления"}, status_code=403)
            origin = request.headers.get("Origin")
            allowed_origins = {f"http://localhost:{request.url.port}", f"http://127.0.0.1:{request.url.port}"}
            public_origin = os.environ.get("QA_PANEL_PUBLIC_ORIGIN", "").strip().rstrip("/")
            if public_origin:
                allowed_origins.add(public_origin)
            if origin and origin not in allowed_origins:
                from fastapi.responses import JSONResponse
                return JSONResponse({"detail": "Команды разрешены только из локальной панели"}, status_code=403)
        response = await call_next(request)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/")
    def index():
        return FileResponse(WEB / "index.html")

    @app.get("/api/state")
    def state():
        active = manager.active
        if active:
            try:
                if load(manager.folder(active) / "manifest.json", {}).get("status") != "starting":
                    active = None
            except HTTPException:
                active = None
        return {"token": manager.token, "catalog": manager.catalog(), "active": active, "history": manager.history()}

    @app.post("/api/runs")
    def start(payload: StartRequest):
        return {"id": manager.start(payload.node_ids, payload.label, payload.include_live)}

    @app.post("/api/catalog/refresh")
    def refresh_catalog():
        with manager.lock:
            if manager.active and manager.snapshot(manager.active)["manifest"].get("status") == "starting":
                raise HTTPException(409, "Дождитесь завершения текущего запуска")
            manager._catalog = None
            try:
                return {"catalog": manager.catalog()}
            except RuntimeError as exc:
                raise HTTPException(400, str(exc)) from exc

    @app.get("/api/runs/{id}")
    def run(id: str):
        return manager.snapshot(id)

    @app.get("/api/runs/{id}/tests/{key}")
    def evidence(id: str, key: str):
        return manager.evidence(id, key)

    @app.post("/api/runs/{id}/cancel")
    def cancel(id: str):
        manager.cancel(id)
        return {"status": "cancel-requested"}

    app.mount("/assets", StaticFiles(directory=WEB), name="panel-assets")
    app.mount("/reports", StaticFiles(directory=manager.reports, html=True), name="reports")
    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=9020)
    args = parser.parse_args()
    import uvicorn
    print(f"Test control: http://localhost:{args.port}/", flush=True)
    uvicorn.run(create_app(), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
