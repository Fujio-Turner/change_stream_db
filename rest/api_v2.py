"""
API v2.0 handlers for inputs, outputs, jobs, and sessions management.

These endpoints manage the new CBL-based document model:
- Inputs (sources feeding into the pipeline)
- Outputs (destinations for processed data)
- Jobs (connections between input → output with schema mapping)
- Sessions (SG session management)
"""

import json
import logging
from aiohttp import web

from cbl_store import CBLStore, USE_CBL

logger = logging.getLogger("changes_worker")


# ─────────────────────────────────────────────────────────────────
# Inputs (/api/inputs_changes)
# ─────────────────────────────────────────────────────────────────


async def api_get_inputs_changes(request: web.Request) -> web.Response:
    """GET /api/inputs_changes — Load the inputs_changes document."""
    if not USE_CBL:
        return web.json_response({"error": "CBL is disabled"}, status=503)

    try:
        store = CBLStore()
        doc = store.load_inputs_changes()
        if not doc:
            return web.json_response({"type": "inputs_changes", "src": []})
        return web.json_response(doc)
    except Exception as e:
        logger.exception("Error loading inputs_changes")
        return web.json_response({"error": str(e)}, status=500)


async def api_post_inputs_changes(request: web.Request) -> web.Response:
    """POST /api/inputs_changes — Save the inputs_changes document."""
    if not USE_CBL:
        return web.json_response({"error": "CBL is disabled"}, status=503)

    try:
        data = await request.json()

        # Validate
        if not isinstance(data.get("src"), list):
            return web.json_response({"error": "src must be an array"}, status=400)

        # Validate each input entry
        for idx, src_entry in enumerate(data["src"]):
            if not isinstance(src_entry, dict):
                return web.json_response(
                    {"error": f"src[{idx}] must be an object"}, status=400
                )
            if not src_entry.get("id"):
                return web.json_response(
                    {"error": f"src[{idx}].id is required"}, status=400
                )
            if not src_entry.get("source_type"):
                return web.json_response(
                    {"error": f"src[{idx}].source_type is required"}, status=400
                )
            if src_entry.get("source_type") not in (
                "sync_gateway",
                "app_services",
                "edge_server",
                "couchdb",
            ):
                return web.json_response(
                    {"error": f"src[{idx}].source_type invalid"}, status=400
                )
            if not src_entry.get("host"):
                return web.json_response(
                    {"error": f"src[{idx}].host is required"}, status=400
                )

        store = CBLStore()
        store.save_inputs_changes(data)

        return web.json_response(
            {
                "status": "ok",
                "type": "inputs_changes",
                "src_count": len(data.get("src", [])),
            }
        )
    except json.JSONDecodeError:
        return web.json_response({"error": "Invalid JSON"}, status=400)
    except Exception as e:
        logger.exception("Error saving inputs_changes")
        return web.json_response({"error": str(e)}, status=500)


async def api_put_inputs_changes_entry(request: web.Request) -> web.Response:
    """PUT /api/inputs_changes/{id} — Update one input entry."""
    if not USE_CBL:
        return web.json_response({"error": "CBL is disabled"}, status=503)

    entry_id = request.match_info.get("id")

    try:
        data = await request.json()

        store = CBLStore()
        doc = store.load_inputs_changes()
        if not doc:
            return web.json_response(
                {"error": "inputs_changes document not found"}, status=404
            )

        # Find and update entry
        src = doc.get("src", [])
        for idx, entry in enumerate(src):
            if entry.get("id") == entry_id:
                src[idx] = {**entry, **data}
                doc["src"] = src
                store.save_inputs_changes(doc)
                return web.json_response({"status": "ok", "id": entry_id})

        return web.json_response({"error": f"Input {entry_id} not found"}, status=404)
    except json.JSONDecodeError:
        return web.json_response({"error": "Invalid JSON"}, status=400)
    except Exception as e:
        logger.exception(f"Error updating input {entry_id}")
        return web.json_response({"error": str(e)}, status=500)


async def api_delete_inputs_changes_entry(request: web.Request) -> web.Response:
    """DELETE /api/inputs_changes/{id} — Remove one input entry."""
    if not USE_CBL:
        return web.json_response({"error": "CBL is disabled"}, status=503)

    entry_id = request.match_info.get("id")

    try:
        store = CBLStore()
        doc = store.load_inputs_changes()
        if not doc:
            return web.json_response(
                {"error": "inputs_changes document not found"}, status=404
            )

        # Remove entry
        src = doc.get("src", [])
        src = [e for e in src if e.get("id") != entry_id]
        doc["src"] = src
        store.save_inputs_changes(doc)

        return web.json_response({"status": "ok", "id": entry_id})
    except Exception as e:
        logger.exception(f"Error deleting input {entry_id}")
        return web.json_response({"error": str(e)}, status=500)


# ─────────────────────────────────────────────────────────────────
# Outputs (/api/outputs_{type})
# ─────────────────────────────────────────────────────────────────


async def api_get_outputs(request: web.Request) -> web.Response:
    """GET /api/outputs_{type} — Load outputs for a given type."""
    if not USE_CBL:
        return web.json_response({"error": "CBL is disabled"}, status=503)

    output_type = request.match_info.get("type")
    if output_type not in ("rdbms", "http", "cloud", "stdout"):
        return web.json_response({"error": "Invalid output type"}, status=400)

    try:
        store = CBLStore()
        doc = store.load_outputs(output_type)
        if not doc:
            return web.json_response({"type": f"outputs_{output_type}", "src": []})
        return web.json_response(doc)
    except Exception as e:
        logger.exception(f"Error loading outputs_{output_type}")
        return web.json_response({"error": str(e)}, status=500)


async def api_post_outputs(request: web.Request) -> web.Response:
    """POST /api/outputs_{type} — Save outputs for a given type."""
    if not USE_CBL:
        return web.json_response({"error": "CBL is disabled"}, status=503)

    output_type = request.match_info.get("type")
    if output_type not in ("rdbms", "http", "cloud", "stdout"):
        return web.json_response({"error": "Invalid output type"}, status=400)

    try:
        data = await request.json()

        # Validate
        if not isinstance(data.get("src"), list):
            return web.json_response({"error": "src must be an array"}, status=400)

        for idx, src_entry in enumerate(data["src"]):
            if not isinstance(src_entry, dict):
                return web.json_response(
                    {"error": f"src[{idx}] must be an object"}, status=400
                )
            if not src_entry.get("id"):
                return web.json_response(
                    {"error": f"src[{idx}].id is required"}, status=400
                )

        store = CBLStore()
        store.save_outputs(output_type, data)

        return web.json_response(
            {
                "status": "ok",
                "type": f"outputs_{output_type}",
                "src_count": len(data.get("src", [])),
            }
        )
    except json.JSONDecodeError:
        return web.json_response({"error": "Invalid JSON"}, status=400)
    except Exception as e:
        logger.exception(f"Error saving outputs_{output_type}")
        return web.json_response({"error": str(e)}, status=500)


async def api_put_outputs_entry(request: web.Request) -> web.Response:
    """PUT /api/outputs_{type}/{id} — Update one output entry."""
    if not USE_CBL:
        return web.json_response({"error": "CBL is disabled"}, status=503)

    output_type = request.match_info.get("type")
    entry_id = request.match_info.get("id")

    if output_type not in ("rdbms", "http", "cloud", "stdout"):
        return web.json_response({"error": "Invalid output type"}, status=400)

    try:
        data = await request.json()

        store = CBLStore()
        doc = store.load_outputs(output_type)
        if not doc:
            return web.json_response(
                {"error": f"outputs_{output_type} document not found"}, status=404
            )

        # Find and update entry
        src = doc.get("src", [])
        for idx, entry in enumerate(src):
            if entry.get("id") == entry_id:
                src[idx] = {**entry, **data}
                doc["src"] = src
                store.save_outputs(output_type, doc)
                return web.json_response({"status": "ok", "id": entry_id})

        return web.json_response({"error": f"Output {entry_id} not found"}, status=404)
    except json.JSONDecodeError:
        return web.json_response({"error": "Invalid JSON"}, status=400)
    except Exception as e:
        logger.exception(f"Error updating output {entry_id}")
        return web.json_response({"error": str(e)}, status=500)


async def api_delete_outputs_entry(request: web.Request) -> web.Response:
    """DELETE /api/outputs_{type}/{id} — Remove one output entry."""
    if not USE_CBL:
        return web.json_response({"error": "CBL is disabled"}, status=503)

    output_type = request.match_info.get("type")
    entry_id = request.match_info.get("id")

    if output_type not in ("rdbms", "http", "cloud", "stdout"):
        return web.json_response({"error": "Invalid output type"}, status=400)

    try:
        store = CBLStore()
        doc = store.load_outputs(output_type)
        if not doc:
            return web.json_response(
                {"error": f"outputs_{output_type} document not found"}, status=404
            )

        # Remove entry
        src = doc.get("src", [])
        src = [e for e in src if e.get("id") != entry_id]
        doc["src"] = src
        store.save_outputs(output_type, doc)

        return web.json_response({"status": "ok", "id": entry_id})
    except Exception as e:
        logger.exception(f"Error deleting output {entry_id}")
        return web.json_response({"error": str(e)}, status=500)
