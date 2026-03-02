# n8n ExApp - Implementation Notes

Comparison notes from studying the official Nextcloud Flow ExApp (Windmill wrapper) to keep our n8n implementation aligned.

## Flow ExApp Architecture (Reference)

The Flow ExApp (`nextcloud/flow`) is the official Nextcloud ExApp that wraps Windmill. Key facts:

- **App ID**: `flow`
- **Image**: `ghcr.io/nextcloud/flow:1.3.1`
- **Framework**: Python FastAPI + `nc_py_api` (official Nextcloud ExApp SDK)
- **Windmill port**: 8000 (proxied through FastAPI)
- **GitHub**: https://github.com/nextcloud/flow

## Key Differences: Flow vs Our n8n ExApp

### 1. Authentication: `nc_py_api` vs Manual Header Parsing

**Flow uses `nc_py_api`** which provides `AppAPIAuthMiddleware`:
```python
from nc_py_api.ex_app import AppAPIAuthMiddleware, run_app
APP.add_middleware(AppAPIAuthMiddleware)
```
This automatically validates the `AUTHORIZATION-APP-API` header and handles `APP_SECRET`.

**Our n8n ExApp** manually parses the header in `/init` and `/enabled`:
```python
auth_header = request.headers.get("AUTHORIZATION-APP-API", "")
decoded = base64.b64decode(auth_header).decode()
APP_SECRET = decoded[1:]  # strip leading ":"
```

**Action item**: Consider switching to `nc_py_api` for proper auth handling.

### 2. UI Registration: `nc_py_api` SDK vs Raw OCS API Calls

**Flow** uses the SDK:
```python
nc.ui.resources.set_script("top_menu", "flow", "ex_app/js/flow-main")
nc.ui.top_menu.register("flow", "Workflow Engine", "ex_app/img/app.svg", True)
```

**Our n8n** uses raw HTTP calls to the OCS API:
```python
await client.post(
    f"{NEXTCLOUD_URL}/ocs/v1.php/apps/app_api/api/v1/ui/top-menu",
    headers=headers,
    json={"name": "n8n", "displayName": "n8n", "icon": "img/app.svg"},
)
```

**Insight**: The SDK approach is cleaner and handles auth automatically.

### 3. Frontend: Vue Webpack Bundle vs Inline JS

**Flow** has a proper Vue 2 frontend built with webpack:
- `ex_app/src/App.vue` + `IframeView.vue`
- Built to `ex_app/js/flow-main.js`
- Uses Nextcloud's webpack config and CSP nonce setup
- Sets `__webpack_public_path__` to load assets through AppAPI proxy

**Our n8n** uses an inline JS string served from FastAPI:
```python
IFRAME_LOADER_JS = f"""
(function() {{
    var content = document.getElementById('content');
    var iframe = document.createElement('iframe');
    iframe.src = '{PROXY_PREFIX}/';
    content.appendChild(iframe);
}})();
""".strip()
```

**Tradeoff**: Our approach is simpler and has no build step. The Flow approach is more "proper" but adds webpack/Node.js build complexity. For an iframe loader, our approach is likely fine.

### 4. User Provisioning

**Flow** maps Nextcloud users to Windmill users:
- Extracts username from `AUTHORIZATION-APP-API` header (format: `username:secret`)
- Creates Windmill user with email `wapp_{username}@windmill.dev`
- Caches tokens in `windmill_users_config.json`
- Auto-provisions on first visit

**Our n8n** does NOT provision users. n8n has its own user system that users set up manually on first visit.

**Question**: Should we add n8n user auto-provisioning based on the Nextcloud user?

### 5. Route Access Levels

**Flow** uses differentiated access levels in `info.xml`:
- `^api/w/nextcloud/jobs/.*` -> PUBLIC (for webhook triggers)
- `^api/w/nextcloud/jobs_u/.*` -> PUBLIC (for user-scoped webhooks)
- `.*` (catch-all) -> ADMIN only

**Our n8n** uses a single catch-all:
- `.*` -> USER access

**Action item**: Consider whether n8n webhook endpoints should be PUBLIC for external trigger support, and whether the UI should be restricted to ADMIN.

### 6. Database Strategy

**Flow** bundles PostgreSQL inside the container:
- `install_pgsql.sh` installs PostgreSQL during Docker build
- `init_pgsql.sh` initializes and starts it at runtime
- Optional `EXTERNAL_DATABASE` env var for external PostgreSQL

**Our n8n** defaults to SQLite with optional external PostgreSQL:
- SQLite stored in `/data/database.sqlite`
- `N8N_EXTERNAL_DATABASE` env var for PostgreSQL
- No bundled database

**Insight**: Bundling PostgreSQL is heavier but more reliable for production.

### 7. Dockerfile Size

**Flow** is massive -- multi-stage Rust compilation plus every runtime:
- Python 3.11 + 3.12, Node.js 20, Deno, Bun, Go, PHP 8.3, PowerShell
- DuckDB FFI, kubectl, Helm, AWS CLI, Docker client
- Compiles Windmill from Rust source

**Our n8n** is lightweight:
- Alpine Python builder + n8n base image
- Just Python + n8n (Node.js)

### 8. Proxy URL Rewriting

**Flow** modifies the Windmill frontend at build time:
```bash
sed -i "s|BASE: '/api'|BASE: '/index.php/apps/app_api/proxy/flow/api'|" \
    /frontend/src/lib/gen/core/OpenAPI.ts
```

**Our n8n** rewrites at runtime:
```python
def rewrite_html(content: bytes) -> bytes:
    text = re.sub(
        r'((?:src|href|crossorigin href)=")(/.../)',
        rf'\1{PROXY_PREFIX}\2',
        text,
    )
```

**Tradeoff**: Build-time is more reliable but requires rebuilding the Windmill frontend. Runtime rewriting is flexible but fragile.

### 9. HaRP Support

**Flow** has built-in HaRP/FRP support:
- Detects `HP_SHARED_KEY` env var
- Starts FRP client to tunnel through HaRP
- Uses unix socket `/tmp/exapp.sock`
- Changes proxy prefix to `/exapps/flow`

**Our n8n** has no HaRP support -- only manual-install daemon.

**Action item**: Add HaRP/FRP support for production deployment.

### 10. Webhook Integration

**Flow** has a background task that syncs Windmill flows to Nextcloud webhook_listeners:
- Scans Windmill flows every 30 seconds
- Finds flows with `CORE:LISTEN_TO_EVENT` modules
- Registers/updates/deletes matching Nextcloud webhooks
- Uses `nc_py_api` webhook API

**Our n8n** has no webhook integration with Nextcloud.

**Action item**: Consider implementing webhook bridge for n8n workflows.

## Summary: Priority Improvements for n8n ExApp

1. **HIGH**: Switch to `nc_py_api` for auth middleware and UI registration
2. **HIGH**: Add HaRP/FRP support for production deployments
3. **MEDIUM**: Add proper route access levels (PUBLIC for webhooks, ADMIN for UI)
4. **MEDIUM**: Add n8n user auto-provisioning from Nextcloud users
5. **LOW**: Consider bundling PostgreSQL for reliability
6. **LOW**: Add Nextcloud webhook integration bridge
7. **OK AS-IS**: Inline JS iframe loader (simpler than webpack bundle for our use case)
8. **OK AS-IS**: Runtime HTML rewriting (works for n8n's asset structure)

## Installation Notes

### Installing Flow ExApp via AppAPI

1. Ensure `app_api` and `webhook_listeners` are enabled
2. Register a deploy daemon (HaRP or Docker Socket Proxy):
   ```bash
   occ app_api:daemon:register harp "HaRP" "docker-install" "http" \
     "openregister-harp:8780" "http://nextcloud" \
     --net=openregister-network --harp \
     --harp_frp_address="openregister-harp:8782" \
     --harp_shared_key="harp-secret-change-me" --set-default
   ```
3. Install via: `occ app_api:app:register flow harp --wait-finish`
4. The image is very large (~several GB) due to all bundled runtimes
5. First start initializes PostgreSQL and creates the `nextcloud` workspace
6. Default Windmill credentials (`admin@windmill.dev` / `changeme`) are changed automatically on first boot

### Windmill OSS Limitations (Encountered During Setup)

Several Windmill features are **Enterprise-only** and fail silently/noisily in OSS:

1. **`POST /api/users/setpassword`** -- `initialize_windmill()` tries to change the admin password on first boot. Crashes the container in a loop.
   - **Fix**: Pre-create `windmill_users_config.json` in the data volume so `initialize_windmill()` skips the password change path.

2. **`POST /api/users/create`** -- User provisioning tries to create Windmill users per Nextcloud user. Returns 500 "Not implemented in Windmill's Open Source repository".
   - **Fix**: Pre-populate `windmill_users_config.json` with `wapp_{username}@windmill.dev` entries using the admin token. The provision code checks token validity first; if valid, it skips user creation.

3. **`POST /api/workspaces/edit_auto_invite`** -- Auto-invite configuration. Non-fatal but logs errors.

### HaRP/FRP TLS Certificate Sharing

The FRP tunnel between ExApp containers and HaRP requires TLS certificates:

- HaRP generates certs at `/certs/frp/` (`ca.crt`, `server.crt`, `server.key`, `client.crt`, `client.key`)
- ExApp containers need `ca.crt`, `client.crt`, `client.key` at `/certs/frp/`
- Flow's entrypoint auto-detects `/certs/frp/` and enables TLS in the FRP client config
- Without certs, FRP connection fails with "connect to server error: EOF"

```bash
# Copy certs from HaRP to ExApp container
docker cp openregister-harp:/certs/frp/ca.crt /tmp/frp-ca.crt
docker cp openregister-harp:/certs/frp/client.crt /tmp/frp-client.crt
docker cp openregister-harp:/certs/frp/client.key /tmp/frp-client.key
docker exec nc_app_flow mkdir -p /certs/frp
docker cp /tmp/frp-ca.crt nc_app_flow:/certs/frp/ca.crt
docker cp /tmp/frp-client.crt nc_app_flow:/certs/frp/client.crt
docker cp /tmp/frp-client.key nc_app_flow:/certs/frp/client.key
```

### Apache ProxyPass for HaRP

Nextcloud's ExAppProxy controller makes internal HTTP requests to `http://nextcloud/exapps/{appId}/...` for HaRP-based daemons. Apache needs a ProxyPass rule to forward these to HaRP:

```apache
# /etc/apache2/conf-available/harp-exapps.conf
<IfModule mod_proxy.c>
    ProxyPass "/exapps/" "http://openregister-harp:8780/exapps/"
    ProxyPassReverse "/exapps/" "http://openregister-harp:8780/exapps/"
</IfModule>
```

Enable with: `a2enmod proxy proxy_http && a2enconf harp-exapps && apachectl graceful`

### ExAppProxy Request Flow (for debugging)

```
Browser → /index.php/apps/app_api/proxy/{appId}/{path}
       → ExAppProxyController (PHP)
       → Guzzle GET http://nextcloud/exapps/{appId}/{path}
         (adds AUTHORIZATION-APP-API header)
       → Apache ProxyPass → HaRP:8780
       → HaRP validates via SPOE agent, adds auth headers
       → HaRP routes via FRP tunnel to ExApp container
       → FastAPI in container handles request
```

Key debugging points:
- HaRP returns 403 if AUTHORIZATION-APP-API header is missing/invalid
- HaRP returns 404 if AppID not found in path
- ExAppProxy returns 500 silently when upstream returns error (check HaRP logs, not Nextcloud logs)
- Flow container returns 500 if user provisioning fails (check `docker logs nc_app_flow`)
