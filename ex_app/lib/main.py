"""n8n ExApp - Nextcloud External Application wrapper for n8n workflow automation."""

import asyncio
import json
import logging
import os
import secrets
import string
import subprocess
import threading
import typing
from base64 import b64decode
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

import httpx
from fastapi import BackgroundTasks, Depends, FastAPI, Request
from fastapi.responses import JSONResponse, Response
from nc_py_api import NextcloudApp
from nc_py_api.ex_app import (
    nc_app,
    persistent_storage,
    run_app,
    setup_nextcloud_logging,
)
from nc_py_api.ex_app.integration_fastapi import AppAPIAuthMiddleware


# ── Logging ─────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.WARNING,
    format="[%(funcName)s]: %(message)s",
    datefmt="%H:%M:%S",
)
LOGGER = logging.getLogger("n8n")
LOGGER.setLevel(logging.DEBUG)


# ── Configuration ───────────────────────────────────────────────────
N8N_PORT = 5678
N8N_URL = f"http://localhost:{N8N_PORT}"
N8N_PROCESS = None

# Detect HaRP mode and set proxy prefix accordingly
APP_ID = os.environ.get("APP_ID", "n8n")
HARP_ENABLED = bool(os.environ.get("HP_SHARED_KEY"))
if HARP_ENABLED:
    PROXY_PREFIX = f"/exapps/{APP_ID}"
else:
    PROXY_PREFIX = f"/index.php/apps/app_api/proxy/{APP_ID}"

# n8n owner account defaults
OWNER_EMAIL = "admin@n8n.local"
OWNER_FIRST_NAME = "Nextcloud"
OWNER_LAST_NAME = "Admin"


# ── User Storage ──────────────────────────────────────────────────
# Persistent JSON file mapping n8n emails to {password, cookie} dicts.
# Survives container restarts via persistent_storage() volume.
USERS_STORAGE: dict[str, dict[str, str]] = {}
_USERS_FILE: str = ""


def _users_file_path() -> str:
    global _USERS_FILE
    if not _USERS_FILE:
        _USERS_FILE = os.path.join(persistent_storage(), "n8n_users_config.json")
    return _USERS_FILE


def _load_users_storage() -> None:
    global USERS_STORAGE
    path = _users_file_path()
    if os.path.exists(path):
        with open(path, "r") as f:
            USERS_STORAGE = json.load(f)
        LOGGER.info("Loaded %d users from storage", len(USERS_STORAGE))
    else:
        USERS_STORAGE = {}


def _save_users_storage() -> None:
    path = _users_file_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(USERS_STORAGE, f, indent=2)


def _add_user(email: str, password: str, cookie: str) -> None:
    USERS_STORAGE[email] = {"password": password, "cookie": cookie}
    _save_users_storage()


def _generate_password() -> str:
    """Generate a password meeting n8n requirements (8-64 chars, 1 upper, 1 digit)."""
    alphabet = string.ascii_letters + string.digits
    while True:
        pw = "".join(secrets.choice(alphabet) for _ in range(16))
        if any(c.isupper() for c in pw) and any(c.isdigit() for c in pw):
            return pw


# ── n8n Process Management ─────────────────────────────────────────
def start_n8n():
    """Start the n8n subprocess."""
    global N8N_PROCESS

    if N8N_PROCESS is not None and N8N_PROCESS.poll() is None:
        return

    env = os.environ.copy()
    env["N8N_PORT"] = str(N8N_PORT)
    env["N8N_HOST"] = "0.0.0.0"
    env["N8N_PROTOCOL"] = "http"
    env["GENERIC_TIMEZONE"] = env.get("N8N_TIMEZONE", "Europe/Amsterdam")
    env["TZ"] = env.get("N8N_TIMEZONE", "Europe/Amsterdam")

    storage_path = persistent_storage()
    env["N8N_USER_FOLDER"] = storage_path

    external_db = env.get("N8N_EXTERNAL_DATABASE")
    if external_db:
        env["DB_TYPE"] = "postgresdb"
        parsed = urlparse(external_db)
        env["DB_POSTGRESDB_HOST"] = parsed.hostname or "localhost"
        env["DB_POSTGRESDB_PORT"] = str(parsed.port or 5432)
        env["DB_POSTGRESDB_DATABASE"] = parsed.path.lstrip("/") or "n8n"
        env["DB_POSTGRESDB_USER"] = parsed.username or "n8n"
        env["DB_POSTGRESDB_PASSWORD"] = parsed.password or ""
    else:
        env["DB_TYPE"] = "sqlite"
        env["DB_SQLITE_DATABASE"] = f"{storage_path}/database.sqlite"

    if not env.get("N8N_ENCRYPTION_KEY"):
        key_file = f"{storage_path}/.encryption_key"
        if os.path.exists(key_file):
            with open(key_file, "r") as f:
                env["N8N_ENCRYPTION_KEY"] = f.read().strip()
        else:
            env["N8N_ENCRYPTION_KEY"] = secrets.token_hex(32)
            os.makedirs(storage_path, exist_ok=True)
            with open(key_file, "w") as f:
                f.write(env["N8N_ENCRYPTION_KEY"])

    # No path prefix - we rewrite paths in the proxy layer
    env.pop("N8N_PATH_PREFIX", None)

    N8N_PROCESS = subprocess.Popen(
        ["n8n", "start"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    def log_output():
        for line in N8N_PROCESS.stdout:
            LOGGER.info("[n8n] %s", line.decode().strip())

    threading.Thread(target=log_output, daemon=True).start()
    LOGGER.info("n8n started with PID: %d", N8N_PROCESS.pid)


def stop_n8n():
    """Stop the n8n subprocess."""
    global N8N_PROCESS
    if N8N_PROCESS is not None:
        N8N_PROCESS.terminate()
        try:
            N8N_PROCESS.wait(timeout=30)
        except subprocess.TimeoutExpired:
            N8N_PROCESS.kill()
        N8N_PROCESS = None
        LOGGER.info("n8n stopped")


async def wait_for_n8n(timeout: int = 90) -> bool:
    """Wait for n8n to become healthy."""
    for _ in range(timeout):
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{N8N_URL}/healthz",
                    timeout=5,
                )
                if resp.status_code == 200:
                    return True
        except Exception:
            pass
        await asyncio.sleep(1)
    return False


# ── n8n Authentication ─────────────────────────────────────────────

def _extract_cookie(response: httpx.Response) -> str:
    """Extract the n8n-auth cookie value from a response."""
    for header_value in response.headers.get_list("set-cookie"):
        if header_value.startswith("n8n-auth="):
            return header_value.split(";")[0].split("=", 1)[1]
    return ""


async def _n8n_needs_setup() -> bool:
    """Check if n8n still needs initial owner setup.

    Retries until the REST API returns valid JSON (it may not be ready
    immediately after the healthz endpoint starts responding).
    """
    for attempt in range(30):
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"{N8N_URL}/rest/settings", timeout=10)
                if resp.status_code != 200:
                    await asyncio.sleep(2)
                    continue
                data = resp.json().get("data", {})
                return data.get("userManagement", {}).get(
                    "showSetupOnFirstLoad", True
                )
        except (json.JSONDecodeError, httpx.RequestError) as exc:
            LOGGER.debug("Settings not ready (attempt %d): %s", attempt, exc)
            await asyncio.sleep(2)
    LOGGER.warning("Could not determine n8n setup state after retries")
    return True


async def _setup_owner() -> str:
    """Set up the n8n owner account. Returns the auth cookie."""
    password = _generate_password()
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{N8N_URL}/rest/owner/setup",
            json={
                "email": OWNER_EMAIL,
                "firstName": OWNER_FIRST_NAME,
                "lastName": OWNER_LAST_NAME,
                "password": password,
            },
            timeout=30,
        )
        if resp.status_code != 200:
            LOGGER.error("Owner setup failed: %s %s", resp.status_code, resp.text)
            return ""
        cookie = _extract_cookie(resp)
        _add_user(OWNER_EMAIL, password, cookie)
        LOGGER.info("n8n owner account created: %s", OWNER_EMAIL)
        return cookie


async def _login_user(email: str, password: str) -> str:
    """Login to n8n and return the auth cookie."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{N8N_URL}/rest/login",
            json={"emailOrLdapLoginId": email, "password": password},
            timeout=10,
        )
        if resp.status_code != 200:
            LOGGER.warning("Login failed for %s: %s", email, resp.status_code)
            return ""
        return _extract_cookie(resp)


async def _check_cookie(cookie: str) -> bool:
    """Check if an n8n-auth cookie is still valid."""
    if not cookie:
        return False
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{N8N_URL}/rest/login",
            cookies={"n8n-auth": cookie},
            timeout=10,
        )
        return resp.status_code == 200


async def _get_owner_cookie() -> str:
    """Get a valid auth cookie for the owner account, re-logging in if needed."""
    if OWNER_EMAIL not in USERS_STORAGE:
        return ""
    entry = USERS_STORAGE[OWNER_EMAIL]
    if await _check_cookie(entry.get("cookie", "")):
        return entry["cookie"]
    # Re-login
    cookie = await _login_user(OWNER_EMAIL, entry["password"])
    if cookie:
        _add_user(OWNER_EMAIL, entry["password"], cookie)
    return cookie


async def initialize_n8n() -> None:
    """Bootstrap n8n: set up owner account on first run, or reload stored credentials."""
    _load_users_storage()

    if not await wait_for_n8n():
        LOGGER.error("n8n did not become healthy, skipping initialization")
        return

    if await _n8n_needs_setup():
        LOGGER.info("n8n needs initial setup, creating owner account...")
        await _setup_owner()
    elif OWNER_EMAIL in USERS_STORAGE:
        LOGGER.info("n8n already initialized, validating owner credentials...")
        cookie = await _get_owner_cookie()
        if cookie:
            LOGGER.info("Owner credentials valid")
        else:
            LOGGER.warning("Owner credentials invalid, n8n may need manual setup")
    else:
        LOGGER.warning("n8n is set up but no stored credentials found")


# ── User Provisioning ─────────────────────────────────────────────

def _get_nc_username(request: Request) -> str:
    """Extract the Nextcloud username from the AUTHORIZATION-APP-API header."""
    auth_header = request.headers.get("AUTHORIZATION-APP-API", "")
    if not auth_header:
        return ""
    try:
        decoded = b64decode(auth_header).decode("UTF-8")
        username, _ = decoded.split(":", maxsplit=1)
        return username
    except (ValueError, Exception):
        return ""


def _nc_to_n8n_email(nc_username: str) -> str:
    """Convert a Nextcloud username to an n8n email address."""
    if not nc_username:
        return ""
    # Prefix with nca_ (Nextcloud App) to avoid collisions
    prefix = "nca_"
    if len(prefix) + len(nc_username) + len("@n8n.local") > 50:
        return f"{nc_username}@n8n.local"
    return f"{prefix}{nc_username}@n8n.local"


async def _create_n8n_user(nc_username: str) -> str:
    """Create a new n8n user for a Nextcloud user via invite + accept flow."""
    owner_cookie = await _get_owner_cookie()
    if not owner_cookie:
        LOGGER.error("Cannot create user: no valid owner cookie")
        return ""

    email = _nc_to_n8n_email(nc_username)
    password = _generate_password()

    async with httpx.AsyncClient() as client:
        # Step 1: Invite the user
        invite_resp = await client.post(
            f"{N8N_URL}/rest/invitations",
            json=[{"email": email, "role": "global:member"}],
            cookies={"n8n-auth": owner_cookie},
            timeout=10,
        )
        if invite_resp.status_code != 200:
            LOGGER.error(
                "Failed to invite user %s: %s %s",
                email,
                invite_resp.status_code,
                invite_resp.text,
            )
            return ""

        invite_data = invite_resp.json().get("data", [])
        if not invite_data or invite_data[0].get("error"):
            LOGGER.error("Invite response error: %s", invite_data)
            return ""

        user_info = invite_data[0].get("user", {})
        invitee_id = user_info.get("id", "")
        if not invitee_id:
            LOGGER.error("No invitee ID in response: %s", invite_data)
            return ""

        # Get the owner's user ID for the accept call
        owner_resp = await client.get(
            f"{N8N_URL}/rest/login",
            cookies={"n8n-auth": owner_cookie},
            timeout=10,
        )
        owner_id = owner_resp.json().get("data", {}).get("id", "")

        # Step 2: Accept the invitation (sets up the user with a password)
        accept_resp = await client.post(
            f"{N8N_URL}/rest/invitations/{invitee_id}/accept",
            json={
                "inviterId": owner_id,
                "firstName": nc_username,
                "lastName": "(Nextcloud)",
                "password": password,
            },
            timeout=10,
        )
        if accept_resp.status_code != 200:
            LOGGER.error(
                "Failed to accept invite for %s: %s %s",
                email,
                accept_resp.status_code,
                accept_resp.text,
            )
            return ""

        cookie = _extract_cookie(accept_resp)
        if cookie:
            _add_user(email, password, cookie)
            LOGGER.info("Created n8n user: %s for NC user: %s", email, nc_username)
        return cookie


async def provision_user(request: Request, create_if_missing: bool) -> str:
    """Provision and authenticate the Nextcloud user in n8n.

    Returns the n8n-auth cookie value, or empty string if provisioning fails.
    """
    nc_username = _get_nc_username(request)
    if not nc_username:
        return ""

    email = _nc_to_n8n_email(nc_username)

    # User exists in storage — check if cookie is still valid
    if email in USERS_STORAGE:
        entry = USERS_STORAGE[email]
        if await _check_cookie(entry.get("cookie", "")):
            return entry["cookie"]
        # Cookie expired, re-login
        cookie = await _login_user(email, entry["password"])
        if cookie:
            _add_user(email, entry["password"], cookie)
            return cookie
        if not create_if_missing:
            return ""
        # Login failed (maybe user was deleted), recreate
        del USERS_STORAGE[email]
        _save_users_storage()

    # User doesn't exist — create if allowed
    if not create_if_missing:
        return ""

    return await _create_n8n_user(nc_username)


# ── Path Rewriting ─────────────────────────────────────────────────
# n8n's Vite build uses "/" as base - we rewrite paths to route through
# the ExApp proxy. This covers HTML tags, JS dynamic imports, and CSS urls.
_REWRITE_PREFIXES = ("/assets/", "/static/", "/favicon", "/icons/", "/types/")


def rewrite_content(content: bytes, content_type: str) -> bytes:
    """Rewrite absolute n8n paths to use the proxy prefix."""
    if not any(t in content_type for t in ("text/html", "javascript", "text/css")):
        return content

    text = content.decode("utf-8", errors="replace")

    for prefix in _REWRITE_PREFIXES:
        text = text.replace(f'"{prefix}', f'"{PROXY_PREFIX}{prefix}')
        text = text.replace(f"'{prefix}", f"'{PROXY_PREFIX}{prefix}")
        text = text.replace(f"({prefix}", f"({PROXY_PREFIX}{prefix}")
        text = text.replace(f"`{prefix}", f"`{PROXY_PREFIX}{prefix}")

    # Rewrite HTML attribute paths (href="/assets/...", src="/static/...")
    text = text.replace('href="/rest/', f'href="{PROXY_PREFIX}/rest/')
    text = text.replace('src="/rest/', f'src="{PROXY_PREFIX}/rest/')

    # Rewrite window.BASE_PATH for n8n's router
    text = text.replace(
        "window.BASE_PATH = '/';",
        f"window.BASE_PATH = '{PROXY_PREFIX}/';",
    )

    # Rewrite Vite's assetsURL function that hardcodes "/" as base
    text = text.replace(
        'return "/" + dep',
        f'return "{PROXY_PREFIX}/" + dep',
    )

    return text.encode("utf-8")


# ── Lifespan ────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(_app: FastAPI):
    setup_nextcloud_logging("n8n", logging_level=logging.WARNING)
    LOGGER.info("Starting n8n ExApp")
    start_n8n()
    await initialize_n8n()
    yield
    stop_n8n()
    LOGGER.info("n8n ExApp shutdown complete")


# ── FastAPI App ─────────────────────────────────────────────────────
APP = FastAPI(lifespan=lifespan)
APP.add_middleware(AppAPIAuthMiddleware)


# ── Inline iframe loader JS ────────────────────────────────────────
# Uses CSS injection instead of inline styles to avoid Nextcloud core
# overwriting our styles. Runs DOM manipulation on DOMContentLoaded so
# it executes after Nextcloud core has finished setting up #content.
IFRAME_LOADER_JS = f"""
(function() {{
    var style = document.createElement('style');
    style.textContent =
        '#content.app-app_api {{' +
        '  margin-top: var(--header-height) !important;' +
        '  height: var(--body-height) !important;' +
        '  width: calc(100% - var(--body-container-margin) * 2) !important;' +
        '  border-radius: var(--body-container-radius) !important;' +
        '  overflow: hidden !important;' +
        '  padding: 0 !important;' +
        '}}' +
        '#content.app-app_api > iframe {{ width: 100%; height: 100%; border: none; display: block; }}';
    document.head.appendChild(style);

    function setup() {{
        var content = document.getElementById('content');
        if (!content) return;
        content.innerHTML = '';
        var iframe = document.createElement('iframe');
        iframe.src = '{PROXY_PREFIX}/';
        iframe.allow = 'clipboard-read; clipboard-write';
        content.appendChild(iframe);
    }}

    if (document.readyState === 'loading') {{
        document.addEventListener('DOMContentLoaded', setup);
    }} else {{
        setup();
    }}
}})();
""".strip()


@APP.get("/js/n8n-iframe-loader.js")
async def iframe_loader():
    """Serve the inline iframe loader script."""
    return Response(
        content=IFRAME_LOADER_JS,
        media_type="application/javascript",
    )


# ── Enabled Handler ────────────────────────────────────────────────
def enabled_handler(enabled: bool, nc: NextcloudApp) -> str:
    """Handle app enable/disable events."""
    if enabled:
        LOGGER.info("Enabling n8n ExApp")
        nc.ui.resources.set_script("top_menu", "n8n", "js/n8n-iframe-loader")
        nc.ui.top_menu.register("n8n", "n8n", "ex_app/img/app.svg", True)
        start_n8n()
    else:
        LOGGER.info("Disabling n8n ExApp")
        nc.ui.resources.delete_script("top_menu", "n8n", "js/n8n-iframe-loader")
        nc.ui.top_menu.unregister("n8n")
        stop_n8n()
    return ""


# ── Required Endpoints ──────────────────────────────────────────────
@APP.get("/heartbeat")
async def heartbeat_callback():
    """Heartbeat endpoint for AppAPI health checks."""
    return JSONResponse(content={"status": "ok"})


@APP.post("/init")
async def init_callback(
    b_tasks: BackgroundTasks,
    nc: typing.Annotated[NextcloudApp, Depends(nc_app)],
):
    """Initialization endpoint called by AppAPI after installation."""
    b_tasks.add_task(init_n8n_task, nc)
    return JSONResponse(content={})


@APP.put("/enabled")
def enabled_callback(
    enabled: bool,
    nc: typing.Annotated[NextcloudApp, Depends(nc_app)],
):
    """Enable/disable callback from AppAPI."""
    return JSONResponse(content={"error": enabled_handler(enabled, nc)})


async def init_n8n_task(nc: NextcloudApp):
    """Background task for n8n initialization with progress reporting."""
    nc.set_init_status(0)
    LOGGER.info("Starting n8n initialization...")

    start_n8n()
    nc.set_init_status(20)

    if await wait_for_n8n():
        nc.set_init_status(60)
        await initialize_n8n()
        nc.set_init_status(80)
        nc.ui.resources.set_script("top_menu", "n8n", "js/n8n-iframe-loader")
        nc.ui.top_menu.register("n8n", "n8n", "ex_app/img/app.svg", True)
        nc.set_init_status(100)
        LOGGER.info("n8n initialization complete")
    else:
        LOGGER.error("n8n failed to start within timeout")


# ── Catch-All Proxy ────────────────────────────────────────────────
@APP.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
)
async def proxy(request: Request, path: str):
    """Proxy all requests to n8n with transparent auth injection."""
    # Serve ex_app static files (icons, JS) directly from disk
    if path.startswith("ex_app/"):
        file_path = Path(__file__).parent.parent.parent / path
        if file_path.is_file():
            from starlette.responses import FileResponse

            return FileResponse(str(file_path))

    # Determine if this is a frontend page load (create users) or API call
    is_page_load = not path.startswith("rest/") and not path.startswith("api/")
    n8n_cookie = await provision_user(request, create_if_missing=is_page_load)

    # Build headers, stripping host/cookie (we inject our own cookie)
    headers = {
        k: v
        for k, v in request.headers.items()
        if k.lower()
        not in (
            "host",
            "connection",
            "transfer-encoding",
            "accept-encoding",
            "cookie",
        )
    }

    # Build cookies dict: start with request cookies, override with our auth
    cookies = dict(request.cookies)
    if n8n_cookie:
        cookies["n8n-auth"] = n8n_cookie

    try:
        async with httpx.AsyncClient() as client:
            url = f"{N8N_URL}/{path}"

            resp = await client.request(
                method=request.method,
                url=url,
                content=await request.body(),
                headers=headers,
                cookies=cookies,
                params=request.query_params,
                timeout=300,
            )

            content = resp.content
            content_type = resp.headers.get("content-type", "")
            content = rewrite_content(content, content_type)

            # Forward response headers, filtering problematic ones
            resp_headers = {
                k: v
                for k, v in resp.headers.items()
                if k.lower()
                not in (
                    "content-encoding",
                    "transfer-encoding",
                    "content-length",
                )
            }

            return Response(
                content=content,
                status_code=resp.status_code,
                headers=resp_headers,
            )
    except httpx.RequestError as e:
        LOGGER.error("Proxy error: %s", str(e))
        return JSONResponse(
            {"error": f"Proxy error: {str(e)}"},
            status_code=502,
        )


# ── Entry Point ─────────────────────────────────────────────────────
if __name__ == "__main__":
    os.chdir(Path(__file__).parent)
    run_app(APP, log_level="info")
