import os
import asyncio
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx


ROOT = Path(__file__).resolve().parents[2]


def find_backend(directory=None):
    configured = os.environ.get("COVERAGE_BACKEND_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    directory = Path(directory) if directory is not None else ROOT.parent
    candidates = (
        directory / "GraphQL-Coverage-Proxy-Beckend",
        directory / "coverage-backend",
        directory / "GraphQL-Coverage-Proxy-Beckend-main" / "GraphQL-Coverage-Proxy-Beckend-main",
    )
    return next((p for p in candidates if (p / "alembic.ini").is_file()), candidates[0])


TEAM_BACKEND = find_backend()


def source_identity(directory):
    result = {"path": str(Path(directory).resolve()), "commit": None, "dirty": None}
    try:
        commit = subprocess.run(["git", "-C", str(directory), "rev-parse", "HEAD"],
                                capture_output=True, text=True, timeout=5)
        if commit.returncode == 0:
            result["commit"] = commit.stdout.strip()
            status = subprocess.run(["git", "-C", str(directory), "status", "--porcelain", "--untracked-files=no"],
                                    capture_output=True, text=True, timeout=5)
            result["dirty"] = bool(status.stdout.strip()) if status.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        pass
    return result


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Server:
    def __init__(self, module, directory, log_path, env=None):
        self.port = free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.module, self.directory, self.log_path = module, Path(directory), Path(log_path)
        self.env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", **(env or {})}
        self.process = self.log = None

    def start(self):
        self.log = self.log_path.open("a", encoding="utf-8")
        self.process = subprocess.Popen([
            sys.executable, "-m", "uvicorn", self.module,
            "--app-dir", str(self.directory), "--host", "127.0.0.1", "--port", str(self.port),
        ], env=self.env, stdout=self.log, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + 25
        with httpx.Client(timeout=1, trust_env=False) as client:
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    raise RuntimeError(f"Server {self.module} exited; see {self.log_path}")
                try:
                    if client.get(self.base + "/health").status_code == 200:
                        return self
                except httpx.HTTPError:
                    pass
                time.sleep(0.1)
        raise RuntimeError(f"Server {self.module} did not become ready; see {self.log_path}")

    def stop(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        if self.log:
            self.log.close()

    def restart(self):
        self.stop()
        return self.start()


class CoverageStack:
    def __init__(self, folder, backend_dir=TEAM_BACKEND, *, commerce=False, upstream_module="tests.support.upstream.app:app"):
        self.folder, self.backend_dir = Path(folder), Path(backend_dir)
        self.project = "gqltests" + secrets.token_hex(6)
        self.key = secrets.token_hex(32)
        self.upstream = self.secondary = self.proxy = None
        self.compose_started = False
        self.commerce = commerce
        self.upstream_module = upstream_module
        self.command = ["docker", "compose", "-p", self.project, "-f", str(ROOT / "tests" / "support" / "compose.yaml")]

    def start(self):
        if not (self.backend_dir / "alembic.ini").is_file():
            raise RuntimeError("Coverage backend not found. Clone GraphQL-Coverage-Proxy-Beckend next to QA, "
                               "set COVERAGE_BACKEND_DIR, or pass --coverage-backend-dir <checkout>.")
        self.identity = source_identity(self.backend_dir)
        with (self.folder / "infrastructure.log").open("a", encoding="utf-8") as log:
            subprocess.run(["docker", "info"], stdout=log, stderr=subprocess.STDOUT, check=True, timeout=15)
            self.compose_started = True
            subprocess.run([*self.command, "up", "-d", "--wait", "--wait-timeout", "60"], stdout=log,
                           stderr=subprocess.STDOUT, check=True, timeout=180)
            address = subprocess.check_output([*self.command, "port", "postgres", "5432"], text=True, timeout=15).strip()
            port = int(address.rsplit(":", 1)[1])
            self.database_url = f"postgresql+asyncpg://coverage_tests:isolated_local_tests@127.0.0.1:{port}/coverage_tests"
            upstream_env = {}
            if self.commerce:
                import asyncpg
                async def create_database():
                    conn = await asyncpg.connect(self.database_url.replace("postgresql+asyncpg:", "postgresql:"))
                    try:
                        await conn.execute("CREATE DATABASE commerce_tests")
                    finally:
                        await conn.close()
                asyncio.run(create_database())
                upstream_env["COMMERCE_DATABASE_URL"] = self.database_url.replace("postgresql+asyncpg:", "postgresql:").rsplit("/", 1)[0] + "/commerce_tests"
            self.upstream = Server("examples.shop.api.app:app" if self.commerce else self.upstream_module, ROOT,
                                   self.folder / ("commerce.log" if self.commerce else "upstream.log"), upstream_env)
            self.upstream.start()
            origins = [self.upstream.base]
            if not self.commerce:
                self.secondary = Server("tests.support.upstream.app:app", ROOT, self.folder / "secondary-upstream.log",
                                        {"QA_UPSTREAM_NAME": "Secondary upstream"}).start()
                origins.append(self.secondary.base)
            env = {"DATABASE_URL": self.database_url, "TARGET_GRAPHQL_URL": self.upstream.base + "/graphql",
                   "COVERAGE_DEV_UPSTREAM_ORIGINS": ",".join(origins),
                   "COVERAGE_API_KEY": self.key, "CORS_ORIGINS": "", "COVERAGE_DEV_MODE": "true",
                   "COVERAGE_PUBLIC_ORIGIN": "http://localhost:5173", "PYTHONDONTWRITEBYTECODE": "1"}
            subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=self.backend_dir,
                           env={**os.environ, **env}, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=45)
            self.proxy = Server("app.main:app", self.backend_dir, self.folder / "proxy.log", env)
            self.proxy.start()
        return self

    def close(self):
        errors = []
        for server in (self.proxy, self.upstream, self.secondary):
            if server:
                try:
                    server.stop()
                except Exception as exc:
                    errors.append(str(exc))
        if self.compose_started:
            with (self.folder / "infrastructure.log").open("a", encoding="utf-8") as log:
                try:
                    # Only this unique project is removed; there are no host data volumes.
                    subprocess.run([*self.command, "down", "--remove-orphans"], stdout=log,
                                   stderr=subprocess.STDOUT, check=True, timeout=30)
                except Exception as exc:
                    errors.append(f"Cleanup {self.project}: {exc}")
        return errors
