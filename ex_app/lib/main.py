"""
n8n ExApp - Nextcloud External Application wrapper for n8n workflow automation.

This module provides the lifecycle endpoints required by Nextcloud's AppAPI
to manage the n8n container as an external application.
"""

import os
import json
import time
import subprocess
import threading
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, Request, Response, BackgroundTasks
from fastapi.responses import JSONResponse


# Configuration from environment
APP_ID = os.environ.get("APP_ID", "n8n")
APP_VERSION = os.environ.get("APP_VERSION", "1.0.0")
APP_SECRET = os.environ.get("APP_SECRET", "")
APP_HOST = os.environ.get("APP_HOST", "0.0.0.0")
APP_PORT = int(os.environ.get("APP_PORT", "9000"))
NEXTCLOUD_URL = os.environ.get("NEXTCLOUD_URL", "http://nextcloud")

# n8n configuration
N8N_PORT = 5678
N8N_PROCESS = None
INIT_PROGRESS = 0


def get_nc_headers() -> dict:
    """Get headers for Nextcloud API calls."""
    import base64
    auth = base64.b64encode(f":{APP_SECRET}".encode()).decode()
    return {
        "EX-APP-ID": APP_ID,
        "EX-APP-VERSION": APP_VERSION,
        "AUTHORIZATION-APP-API": auth,
    }


async def report_status(progress: int):
    """Report initialization progress to Nextcloud."""
    global INIT_PROGRESS
    INIT_PROGRESS = progress
    try:
        async with httpx.AsyncClient() as client:
            await client.put(
                f"{NEXTCLOUD_URL}/ocs/v1.php/apps/app_api/apps/status",
                headers=get_nc_headers(),
                json={"progress": progress},
                timeout=10,
            )
    except Exception as e:
        print(f"Failed to report status: {e}")


def start_n8n():
    """Start the n8n process."""
    global N8N_PROCESS

    env = os.environ.copy()

    # Configure n8n
    env["N8N_PORT"] = str(N8N_PORT)
    env["N8N_HOST"] = "0.0.0.0"
    env["N8N_PROTOCOL"] = "http"
    env["GENERIC_TIMEZONE"] = env.get("N8N_TIMEZONE", "Europe/Amsterdam")
    env["TZ"] = env.get("N8N_TIMEZONE", "Europe/Amsterdam")

    # Use persistent storage
    storage_path = env.get("APP_PERSISTENT_STORAGE", "/data")
    env["N8N_USER_FOLDER"] = storage_path

    # External database configuration
    external_db = env.get("N8N_EXTERNAL_DATABASE")
    if external_db:
        env["DB_TYPE"] = "postgresdb"
        # Parse postgres://user:pass@host:port/db
        from urllib.parse import urlparse
        parsed = urlparse(external_db)
        env["DB_POSTGRESDB_HOST"] = parsed.hostname or "localhost"
        env["DB_POSTGRESDB_PORT"] = str(parsed.port or 5432)
        env["DB_POSTGRESDB_DATABASE"] = parsed.path.lstrip("/") or "n8n"
        env["DB_POSTGRESDB_USER"] = parsed.username or "n8n"
        env["DB_POSTGRESDB_PASSWORD"] = parsed.password or ""
    else:
        env["DB_TYPE"] = "sqlite"
        env["DB_SQLITE_DATABASE"] = f"{storage_path}/database.sqlite"

    # Encryption key
    if not env.get("N8N_ENCRYPTION_KEY"):
        import secrets
        key_file = f"{storage_path}/.encryption_key"
        if os.path.exists(key_file):
            with open(key_file, "r") as f:
                env["N8N_ENCRYPTION_KEY"] = f.read().strip()
        else:
            env["N8N_ENCRYPTION_KEY"] = secrets.token_hex(32)
            os.makedirs(storage_path, exist_ok=True)
            with open(key_file, "w") as f:
                f.write(env["N8N_ENCRYPTION_KEY"])

    # Start n8n
    N8N_PROCESS = subprocess.Popen(
        ["n8n", "start"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    # Log output in background
    def log_output():
        for line in N8N_PROCESS.stdout:
            print(f"[n8n] {line.decode().strip()}")

    threading.Thread(target=log_output, daemon=True).start()


async def wait_for_n8n(timeout: int = 90) -> bool:
    """Wait for n8n to become healthy."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"http://localhost:{N8N_PORT}/healthz", timeout=5)
                if resp.status_code == 200:
                    return True
        except Exception:
            pass
        await report_status(int((time.time() - start) / timeout * 90))
        time.sleep(2)
    return False


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    print(f"Starting n8n ExApp v{APP_VERSION}")
    yield
    # Cleanup
    if N8N_PROCESS:
        N8N_PROCESS.terminate()
        N8N_PROCESS.wait(timeout=10)


app = FastAPI(lifespan=lifespan)


@app.get("/heartbeat")
async def heartbeat():
    """Health check endpoint required by AppAPI."""
    # Check if n8n is responding
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"http://localhost:{N8N_PORT}/healthz", timeout=5)
            if resp.status_code == 200:
                return JSONResponse({"status": "ok"})
    except Exception:
        pass
    return JSONResponse({"status": "error"}, status_code=503)


@app.post("/init")
async def init(background_tasks: BackgroundTasks):
    """Initialization endpoint required by AppAPI."""

    async def do_init():
        await report_status(0)

        # Start n8n
        start_n8n()
        await report_status(10)

        # Wait for n8n to be ready
        if await wait_for_n8n():
            await report_status(100)
            print("n8n initialization complete")
        else:
            print("n8n failed to start within timeout")

    background_tasks.add_task(do_init)
    return JSONResponse({"status": "init_started"})


@app.put("/enabled")
async def enabled(request: Request):
    """Enable/disable endpoint required by AppAPI."""
    data = await request.json()
    is_enabled = data.get("enabled", False)

    if is_enabled:
        if not N8N_PROCESS or N8N_PROCESS.poll() is not None:
            start_n8n()
    else:
        if N8N_PROCESS and N8N_PROCESS.poll() is None:
            N8N_PROCESS.terminate()

    return JSONResponse({"status": "ok"})


# Proxy all other requests to n8n
@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def proxy(request: Request, path: str):
    """Proxy requests to n8n."""
    try:
        async with httpx.AsyncClient() as client:
            # Forward the request
            url = f"http://localhost:{N8N_PORT}/{path}"

            # Get request body if present
            body = await request.body()

            # Forward headers (filter out hop-by-hop headers)
            headers = {
                k: v for k, v in request.headers.items()
                if k.lower() not in ("host", "connection", "transfer-encoding")
            }

            resp = await client.request(
                method=request.method,
                url=url,
                content=body,
                headers=headers,
                params=request.query_params,
                timeout=300,
            )

            # Return response
            return Response(
                content=resp.content,
                status_code=resp.status_code,
                headers=dict(resp.headers),
            )
    except Exception as e:
        return JSONResponse(
            {"error": str(e)},
            status_code=502,
        )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=APP_HOST, port=APP_PORT)
