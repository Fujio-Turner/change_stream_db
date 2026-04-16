"""aiohttp web server for the changes_worker admin UI."""

import argparse
import json
from pathlib import Path

from aiohttp import web

from cbl_store import USE_CBL, CBLStore

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
CONFIG_PATH = ROOT / "config.json"
MAPPINGS_DIR = ROOT / "mappings"


def cors_headers():
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, PUT, DELETE, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
    }


def json_response(data, status=200):
    return web.json_response(data, status=status, headers=cors_headers())


def error_response(msg, status=400):
    return json_response({"error": msg}, status=status)


@web.middleware
async def cors_middleware(request, handler):
    if request.method == "OPTIONS":
        return web.Response(status=204, headers=cors_headers())
    resp = await handler(request)
    resp.headers.update(cors_headers())
    return resp


# --- Pages ---

async def favicon(request):
    return web.FileResponse(WEB / "static" / "favicon.svg",
                            headers={"Content-Type": "image/svg+xml"})


async def page_index(request):
    return web.FileResponse(WEB / "templates" / "index.html")


async def page_config(request):
    return web.FileResponse(WEB / "templates" / "config.html")


async def page_schema(request):
    return web.FileResponse(WEB / "templates" / "schema.html")


async def page_transforms(request):
    return web.FileResponse(WEB / "templates" / "transforms.html")


# --- Config API ---

async def get_config(request):
    if USE_CBL:
        return json_response(CBLStore().load_config() or {})
    try:
        data = json.loads(CONFIG_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        return error_response(str(exc), 500)
    return json_response(data)


async def put_config(request):
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return error_response("Invalid JSON")
    if USE_CBL:
        CBLStore().save_config(body)
        return json_response({"ok": True})
    CONFIG_PATH.write_text(json.dumps(body, indent=2) + "\n")
    return json_response({"ok": True})


# --- Mappings API ---

def _valid_mapping_name(name: str) -> bool:
    return name.endswith((".yaml", ".yml", ".json")) and "/" not in name and "\\" not in name


async def list_mappings(request):
    if USE_CBL:
        return json_response(CBLStore().list_mappings())
    MAPPINGS_DIR.mkdir(exist_ok=True)
    files = sorted(
        p for p in MAPPINGS_DIR.iterdir()
        if p.is_file() and p.suffix in (".yaml", ".yml", ".json")
    )
    result = [{"name": p.name, "content": p.read_text()} for p in files]
    return json_response(result)


async def get_mapping(request):
    name = request.match_info["name"]
    if not _valid_mapping_name(name):
        return error_response("Invalid filename")
    if USE_CBL:
        content = CBLStore().get_mapping(name)
        if content is None:
            return error_response("Not found", 404)
        return json_response({"name": name, "content": content})
    path = MAPPINGS_DIR / name
    if not path.is_file():
        return error_response("Not found", 404)
    return json_response({"name": name, "content": path.read_text()})


async def put_mapping(request):
    name = request.match_info["name"]
    if not _valid_mapping_name(name):
        return error_response("Invalid filename")
    content = await request.text()
    if USE_CBL:
        CBLStore().save_mapping(name, content)
        return json_response({"ok": True})
    MAPPINGS_DIR.mkdir(exist_ok=True)
    (MAPPINGS_DIR / name).write_text(content)
    return json_response({"ok": True})


async def delete_mapping(request):
    name = request.match_info["name"]
    if not _valid_mapping_name(name):
        return error_response("Invalid filename")
    if USE_CBL:
        CBLStore().delete_mapping(name)
        return json_response({"ok": True})
    path = MAPPINGS_DIR / name
    if not path.is_file():
        return error_response("Not found", 404)
    path.unlink()
    return json_response({"ok": True})


# --- DLQ API ---

async def list_dlq(request):
    if not USE_CBL:
        return json_response([])
    return json_response(CBLStore().list_dlq())


async def get_dlq_entry(request):
    if not USE_CBL:
        return error_response("DLQ requires CBL", 501)
    dlq_id = request.match_info["id"]
    entry = CBLStore().get_dlq_entry(dlq_id)
    if not entry:
        return error_response("Not found", 404)
    return json_response(entry)


async def retry_dlq_entry(request):
    if not USE_CBL:
        return error_response("DLQ requires CBL", 501)
    dlq_id = request.match_info["id"]
    CBLStore().mark_dlq_retried(dlq_id)
    return json_response({"ok": True})


async def delete_dlq_entry(request):
    if not USE_CBL:
        return error_response("DLQ requires CBL", 501)
    dlq_id = request.match_info["id"]
    CBLStore().delete_dlq_entry(dlq_id)
    return json_response({"ok": True})


async def clear_dlq(request):
    if not USE_CBL:
        return error_response("DLQ requires CBL", 501)
    CBLStore().clear_dlq()
    return json_response({"ok": True})


async def dlq_count(request):
    if not USE_CBL:
        return json_response({"count": 0})
    return json_response({"count": CBLStore().dlq_count()})


# --- Status API ---

async def get_status(request):
    """Return health status for dashboard indicators."""
    return json_response({
        "cbl": "green" if USE_CBL else "red",
    })


# --- Metrics API ---

async def get_metrics(request):
    try:
        if USE_CBL:
            cfg = CBLStore().load_config() or {}
        else:
            cfg = json.loads(CONFIG_PATH.read_text())
        m = cfg.get("metrics", {})
        if not m.get("enabled"):
            return json_response({"error": "metrics_disabled"})
        host = m.get("host", "127.0.0.1")
        # 0.0.0.0 is a bind address, not connectable;
        # In Docker Compose the worker is a separate service, use its
        # service name.  Fall back to loopback for local dev.
        import os
        worker_host = os.environ.get("METRICS_HOST")
        if worker_host:
            host = worker_host
        elif host in ("0.0.0.0", "::"):
            host = "127.0.0.1"
        port = m.get("port", 9090)
        import aiohttp
        url = f"http://{host}:{port}/_metrics"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                text = await resp.text()
                return web.Response(text=text, content_type="text/plain", headers=cors_headers())
    except Exception as exc:
        return json_response({"error": "metrics_unreachable", "detail": str(exc)})


# --- Sample Doc API (fetch one doc from changes feed in dry-run mode) ---

async def get_sample_doc(request):
    """Fetch 100 docs from the changes feed and return one at random."""
    import random
    try:
        if USE_CBL:
            cfg = CBLStore().load_config() or {}
        else:
            cfg = json.loads(CONFIG_PATH.read_text())
        gw = cfg.get("gateway", {})
        auth_cfg = cfg.get("auth", {})
        url = gw.get("url", "").rstrip("/")
        db = gw.get("database", "")
        scope = gw.get("scope", "_default")
        collection = gw.get("collection", "_default")
        src = gw.get("src", "sync_gateway")

        if src == "sync_gateway":
            changes_url = f"{url}/{db}.{scope}.{collection}/_changes"
        else:
            changes_url = f"{url}/{db}/_changes"

        params = {"limit": "100", "include_docs": "true", "since": "0"}

        import aiohttp as _aiohttp
        ssl_ctx = None
        if gw.get("accept_self_signed_certs"):
            import ssl
            ssl_ctx = ssl.create_default_context()
            ssl_ctx.check_hostname = False
            ssl_ctx.verify_mode = ssl.CERT_NONE

        headers = {}
        method = auth_cfg.get("method", "none")
        basic_auth = None
        if method == "basic" and auth_cfg.get("username"):
            basic_auth = _aiohttp.BasicAuth(auth_cfg["username"],
                                            auth_cfg.get("password", ""))
        elif method == "bearer" and auth_cfg.get("bearer_token"):
            headers["Authorization"] = f"Bearer {auth_cfg['bearer_token']}"
        elif method == "session" and auth_cfg.get("session_cookie"):
            headers["Cookie"] = f"SyncGatewaySession={auth_cfg['session_cookie']}"

        connector = _aiohttp.TCPConnector(ssl=ssl_ctx) if ssl_ctx else _aiohttp.TCPConnector()
        async with _aiohttp.ClientSession(connector=connector) as session:
            async with session.get(changes_url, params=params, auth=basic_auth,
                                   headers=headers,
                                   timeout=_aiohttp.ClientTimeout(total=10)) as resp:
                data = await resp.json()
                results = data.get("results", [])
                if not results:
                    return json_response({"error": "no_docs", "detail": "No documents in changes feed"})
                pick = random.choice(results)
                doc = pick.get("doc", pick)
                return json_response({"doc": doc, "pool_size": len(results)})
    except Exception as exc:
        return json_response({"error": "fetch_failed", "detail": str(exc)}, status=500)


# --- App factory ---

def create_app():
    app = web.Application(middlewares=[cors_middleware])

    # Pages
    app.router.add_get("/favicon.ico", favicon)
    app.router.add_get("/", page_index)
    app.router.add_get("/config", page_config)
    app.router.add_get("/schema", page_schema)
    app.router.add_get("/transforms", page_transforms)

    # Config API
    app.router.add_get("/api/config", get_config)
    app.router.add_put("/api/config", put_config)

    # Mappings API
    app.router.add_get("/api/mappings", list_mappings)
    app.router.add_get("/api/mappings/{name}", get_mapping)
    app.router.add_put("/api/mappings/{name}", put_mapping)
    app.router.add_delete("/api/mappings/{name}", delete_mapping)

    # DLQ API
    app.router.add_get("/api/dlq", list_dlq)
    app.router.add_get("/api/dlq/count", dlq_count)
    app.router.add_get("/api/dlq/{id}", get_dlq_entry)
    app.router.add_post("/api/dlq/{id}/retry", retry_dlq_entry)
    app.router.add_delete("/api/dlq/{id}", delete_dlq_entry)
    app.router.add_delete("/api/dlq", clear_dlq)

    # Status API
    app.router.add_get("/api/status", get_status)

    # Metrics API
    app.router.add_get("/api/metrics", get_metrics)

    # Sample Doc API
    app.router.add_get("/api/sample-doc", get_sample_doc)

    # Static files
    app.router.add_static("/static/", WEB / "static", show_index=False)

    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="changes_worker admin UI")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()
    web.run_app(create_app(), host=args.host, port=args.port)
