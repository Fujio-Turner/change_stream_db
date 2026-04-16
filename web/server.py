"""aiohttp web server for the changes_worker admin UI."""

import argparse
import json
from pathlib import Path

from aiohttp import web

import datetime

from cbl_store import USE_CBL, CBLStore
from schema.mapper import SchemaMapper

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


async def page_wizard(request):
    return web.FileResponse(WEB / "templates" / "wizard.html")


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
    else:
        CONFIG_PATH.write_text(json.dumps(body, indent=2) + "\n")

    # Signal the worker to restart its changes feed with the new config
    restart_result = await _signal_worker_restart()
    return json_response({"ok": True, "restart": restart_result})


async def _signal_worker_restart() -> str:
    """POST to the worker's /_restart endpoint to trigger a feed restart."""
    import os
    import aiohttp as _aiohttp
    worker_host = os.environ.get("METRICS_HOST")
    if not worker_host:
        return "skipped"  # running locally, no separate worker
    try:
        cfg = CBLStore().load_config() if USE_CBL else json.loads(CONFIG_PATH.read_text())
        port = cfg.get("metrics", {}).get("port", 9090)
    except Exception:
        port = 9090
    url = f"http://{worker_host}:{port}/_restart"
    try:
        async with _aiohttp.ClientSession() as session:
            async with session.post(url, timeout=_aiohttp.ClientTimeout(total=5)) as resp:
                return "ok" if resp.status == 200 else f"error:{resp.status}"
    except Exception as exc:
        return f"error:{exc}"


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


# --- DB Introspection API ---

# Supported RDBMS drivers (auto-detected based on what's installed)
_DB_DRIVERS = {}

def _detect_db_drivers():
    """Check which RDBMS drivers are installed."""
    global _DB_DRIVERS
    _DB_DRIVERS = {}
    try:
        import asyncpg  # noqa: F401
        _DB_DRIVERS["postgres"] = {"name": "PostgreSQL", "driver": "asyncpg"}
    except ImportError:
        pass
    try:
        import aiomysql  # noqa: F401
        _DB_DRIVERS["mysql"] = {"name": "MySQL", "driver": "aiomysql"}
    except ImportError:
        pass
    try:
        import aioodbc  # noqa: F401
        _DB_DRIVERS["mssql"] = {"name": "SQL Server", "driver": "aioodbc"}
    except ImportError:
        pass
    try:
        import cx_Oracle  # noqa: F401
        _DB_DRIVERS["oracle"] = {"name": "Oracle", "driver": "cx_Oracle"}
    except ImportError:
        pass
    return _DB_DRIVERS


_detect_db_drivers()


async def list_db_drivers(request):
    """Return which RDBMS drivers are installed and available."""
    drivers = _detect_db_drivers()
    return json_response({"drivers": drivers})


async def db_introspect(request):
    """
    Connect to an RDBMS and return all tables + columns.
    POST body: {"db_type": "postgres", "host": "...", "port": 5432, ...}
    """
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return error_response("Invalid JSON")

    db_type = body.get("db_type", "")
    if db_type not in _DB_DRIVERS:
        installed = list(_DB_DRIVERS.keys())
        return error_response(
            f"Unknown or unavailable db_type '{db_type}'. "
            f"Installed drivers: {installed}"
        )

    try:
        if db_type == "postgres":
            from db.db_postgres import introspect_tables
            tables = await introspect_tables(body)
            return json_response({"tables": tables})
        else:
            return error_response(f"Introspection not yet implemented for {db_type}", 501)
    except Exception as exc:
        return json_response(
            {"error": "introspect_failed", "detail": str(exc)}, status=500
        )


async def db_test_connection(request):
    """Test connectivity to an RDBMS. POST body same as introspect."""
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return error_response("Invalid JSON")

    db_type = body.get("db_type", "")
    if db_type not in _DB_DRIVERS:
        return error_response(f"Driver not installed for '{db_type}'")

    try:
        if db_type == "postgres":
            import asyncpg
            ssl_ctx = None
            if body.get("ssl"):
                import ssl as _ssl
                ssl_ctx = _ssl.create_default_context()
                ssl_ctx.check_hostname = False
                ssl_ctx.verify_mode = _ssl.CERT_NONE
            conn = await asyncpg.connect(
                host=body.get("host", "localhost"),
                port=body.get("port", 5432),
                database=body.get("database", ""),
                user=body.get("user", "postgres"),
                password=body.get("password", ""),
                ssl=ssl_ctx,
            )
            ver = await conn.fetchval("SELECT version()")
            await conn.close()
            return json_response({"ok": True, "version": ver})
        else:
            return error_response(f"Test not yet implemented for {db_type}", 501)
    except Exception as exc:
        return json_response({"ok": False, "error": str(exc)}, status=200)


async def parse_ddl(request):
    """
    Parse a CREATE TABLE DDL statement and return column definitions.
    POST body: {"ddl": "CREATE TABLE orders (id INT PRIMARY KEY, ...)"}
    Supports Postgres/MySQL/MSSQL/Oracle syntax.
    """
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return error_response("Invalid JSON")

    ddl = body.get("ddl", "").strip()
    if not ddl:
        return error_response("No DDL provided")

    try:
        tables = _parse_create_tables(ddl)
        return json_response({"tables": tables})
    except Exception as exc:
        return json_response(
            {"error": "parse_failed", "detail": str(exc)}, status=400
        )


def _parse_create_tables(ddl: str) -> list[dict]:
    """
    Parse one or more CREATE TABLE statements from DDL text.
    Returns a list of table definitions compatible with the mapping format.
    """
    import re

    results = []
    # Find CREATE TABLE header, then extract balanced parentheses body
    header_re = re.compile(
        r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?'
        r'(?:`|"|\[)?(\w+)(?:`|"|\])?\s*'          # table name or schema
        r'(?:\.(?:`|"|\[)?(\w+)(?:`|"|\])?\s*)?'    # optional .table
        r'\(',
        re.IGNORECASE,
    )

    for m in header_re.finditer(ddl):
        table_name = m.group(2) or m.group(1)
        # Extract balanced parentheses body starting after the opening '('
        start = m.end()
        depth = 1
        pos = start
        while pos < len(ddl) and depth > 0:
            if ddl[pos] == '(':
                depth += 1
            elif ddl[pos] == ')':
                depth -= 1
            pos += 1
        body = ddl[start:pos - 1].strip()

        columns = []
        pk_cols = []

        # Split on commas, but respect parentheses (for types like NUMERIC(10,2))
        parts = _split_ddl_body(body)

        for part in parts:
            part = part.strip()
            if not part:
                continue

            # Check for PRIMARY KEY constraint
            pk_match = re.match(
                r'(?:CONSTRAINT\s+\w+\s+)?PRIMARY\s+KEY\s*\((.+?)\)',
                part, re.IGNORECASE,
            )
            if pk_match:
                pk_cols = [
                    c.strip().strip('`"[]')
                    for c in pk_match.group(1).split(",")
                ]
                continue

            # Check for FOREIGN KEY / other constraints — skip
            if re.match(
                r'(?:CONSTRAINT|FOREIGN\s+KEY|UNIQUE|CHECK|INDEX)',
                part, re.IGNORECASE,
            ):
                continue

            # Parse column: name type [NOT NULL] [DEFAULT ...] [PRIMARY KEY] ...
            col_match = re.match(
                r'(?:`|"|\[)?(\w+)(?:`|"|\])?\s+'
                r'([\w]+(?:\s*\([^)]*\))?(?:\s+(?:UNSIGNED|VARYING|PRECISION|WITHOUT\s+TIME\s+ZONE|WITH\s+TIME\s+ZONE))*)',
                part, re.IGNORECASE,
            )
            if not col_match:
                continue

            col_name = col_match.group(1)
            col_type = col_match.group(2).strip()

            nullable = "NOT NULL" not in part.upper()

            if re.search(r'PRIMARY\s+KEY', part, re.IGNORECASE):
                pk_cols.append(col_name)

            columns.append({
                "name": col_name,
                "type": col_type.lower(),
                "display_type": col_type.lower(),
                "nullable": nullable,
                "default": None,
            })

        results.append({
            "table_name": table_name,
            "columns": columns,
            "primary_key": pk_cols,
            "foreign_keys": [],
        })

    if not results:
        raise ValueError("No CREATE TABLE statements found in DDL")

    return results


def _split_ddl_body(body: str) -> list[str]:
    """Split DDL column definitions on commas, respecting parentheses."""
    parts = []
    depth = 0
    current = []
    for ch in body:
        if ch == '(':
            depth += 1
            current.append(ch)
        elif ch == ')':
            depth -= 1
            current.append(ch)
        elif ch == ',' and depth == 0:
            parts.append(''.join(current))
            current = []
        else:
            current.append(ch)
    if current:
        parts.append(''.join(current))
    return parts


# --- Wizard API ---

async def wizard_test_source(request):
    """Test connectivity to SG/App Services/Edge Server and return a random sample doc."""
    import random
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return error_response("Invalid JSON")

    gw = body.get("gateway", {})
    auth_cfg = body.get("auth", {})
    url = gw.get("url", "").rstrip("/")
    db = gw.get("database", "")
    scope = gw.get("scope", "_default")
    collection = gw.get("collection", "_default")
    src = gw.get("src", "sync_gateway")

    if not url or not db:
        return error_response("URL and database are required")

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

    try:
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
                return json_response({"ok": True, "doc": doc, "pool_size": len(results)})
    except Exception as exc:
        return json_response({"error": "fetch_failed", "detail": str(exc)}, status=500)


async def wizard_test_output(request):
    """Test connectivity to an HTTP output endpoint."""
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return error_response("Invalid JSON")

    target_url = body.get("target_url", "").strip()
    if not target_url:
        return error_response("target_url is required")

    import aiohttp as _aiohttp
    ssl_ctx = None
    if body.get("accept_self_signed_certs"):
        import ssl
        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE

    headers = {}
    auth_cfg = body.get("auth", {})
    method = auth_cfg.get("method", "none")
    basic_auth = None
    if method == "basic" and auth_cfg.get("username"):
        basic_auth = _aiohttp.BasicAuth(auth_cfg["username"],
                                        auth_cfg.get("password", ""))
    elif method == "bearer" and auth_cfg.get("bearer_token"):
        headers["Authorization"] = f"Bearer {auth_cfg['bearer_token']}"

    try:
        connector = _aiohttp.TCPConnector(ssl=ssl_ctx) if ssl_ctx else _aiohttp.TCPConnector()
        async with _aiohttp.ClientSession(connector=connector) as session:
            async with session.head(target_url, auth=basic_auth, headers=headers,
                                    timeout=_aiohttp.ClientTimeout(total=10),
                                    allow_redirects=True) as resp:
                return json_response({
                    "ok": True,
                    "status": resp.status,
                    "content_type": resp.headers.get("Content-Type", ""),
                })
    except Exception as exc:
        return json_response({"ok": False, "error": str(exc)}, status=200)


# --- Validate Mapping ---

async def validate_mapping(request):
    try:
        body = await request.json()
    except Exception:
        return error_response("Invalid JSON body")

    mapping = body.get("mapping")
    doc = body.get("doc")
    if mapping is None or doc is None:
        return error_response("Both 'mapping' and 'doc' are required")

    try:
        mapper = SchemaMapper(mapping)
        matched = mapper.matches(doc)
        if not matched:
            return json_response({"matches": False, "ops": []})

        ops = mapper.map_document(doc)
        result_ops = []
        for op in ops:
            sql, params = op.to_sql()
            safe_params = []
            for p in params:
                if isinstance(p, (datetime.date, datetime.datetime)):
                    safe_params.append(str(p))
                else:
                    safe_params.append(p)
            result_ops.append({
                "type": op.op_type,
                "table": op.table,
                "sql": sql,
                "params": safe_params,
            })
        return json_response({"matches": True, "ops": result_ops})
    except Exception as exc:
        return error_response(str(exc), status=500)


# --- App factory ---

def create_app():
    app = web.Application(middlewares=[cors_middleware])

    # Pages
    app.router.add_get("/favicon.ico", favicon)
    app.router.add_get("/", page_index)
    app.router.add_get("/config", page_config)
    app.router.add_get("/schema", page_schema)
    app.router.add_get("/transforms", page_transforms)
    app.router.add_get("/wizard", page_wizard)

    # Config API
    app.router.add_get("/api/config", get_config)
    app.router.add_put("/api/config", put_config)

    # Mappings API
    app.router.add_get("/api/mappings", list_mappings)
    app.router.add_get("/api/mappings/{name}", get_mapping)
    app.router.add_put("/api/mappings/{name}", put_mapping)
    app.router.add_delete("/api/mappings/{name}", delete_mapping)
    app.router.add_post("/api/mappings/validate", validate_mapping)

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

    # DB Introspection API
    app.router.add_get("/api/db/drivers", list_db_drivers)
    app.router.add_post("/api/db/test", db_test_connection)
    app.router.add_post("/api/db/introspect", db_introspect)
    app.router.add_post("/api/db/parse-ddl", parse_ddl)

    # Wizard API
    app.router.add_post("/api/wizard/test-source", wizard_test_source)
    app.router.add_post("/api/wizard/test-output", wizard_test_output)

    # Static files
    app.router.add_static("/static/", WEB / "static", show_index=False)

    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="changes_worker admin UI")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()
    web.run_app(create_app(), host=args.host, port=args.port)
