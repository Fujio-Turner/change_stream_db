"""
Tests for API v2.0 tables_rdbms endpoints.
"""

import json
import pytest
from unittest.mock import patch, MagicMock
from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop

from rest.api_v2 import (
    api_get_tables_rdbms,
    api_post_tables_rdbms,
    api_get_table_rdbms_entry,
    api_put_table_rdbms_entry,
    api_delete_table_rdbms_entry,
    api_get_table_rdbms_used_by,
)

SAMPLE_TABLE = {
    "id": "tbl-orders",
    "name": "orders",
    "engine_hint": "postgres",
    "sql": "CREATE TABLE IF NOT EXISTS orders (doc_id TEXT PRIMARY KEY, status TEXT)",
    "columns": [
        {"name": "doc_id", "type": "TEXT", "primary_key": True, "nullable": False},
        {"name": "status", "type": "TEXT", "primary_key": False, "nullable": True},
    ],
}

SAMPLE_TABLE_2 = {
    "id": "tbl-users",
    "name": "users",
    "engine_hint": "postgres",
    "sql": "CREATE TABLE IF NOT EXISTS users (doc_id TEXT PRIMARY KEY, email TEXT)",
    "columns": [
        {"name": "doc_id", "type": "TEXT", "primary_key": True, "nullable": False},
        {"name": "email", "type": "TEXT", "primary_key": False, "nullable": True},
    ],
}


class TestTablesRdbmsAPI(AioHTTPTestCase):
    """Test the /api/v2/tables_rdbms endpoints."""

    def setUp(self):
        """Set up mocked CBL store."""
        super().setUp()
        self.tables_data = {"type": "tables_rdbms", "tables": []}
        self.mock_store = MagicMock()

        def load_tables_rdbms_side_effect():
            if not self.tables_data["tables"]:
                return None
            return self.tables_data

        def save_tables_rdbms_side_effect(data):
            self.tables_data = data

        def get_table_rdbms_side_effect(table_id):
            for tbl in self.tables_data["tables"]:
                if tbl.get("id") == table_id:
                    return tbl
            return None

        def upsert_table_rdbms_side_effect(entry):
            table_id = entry.get("id")
            if not table_id:
                raise ValueError("table_entry must have an 'id' field")
            for idx, tbl in enumerate(self.tables_data["tables"]):
                if tbl.get("id") == table_id:
                    self.tables_data["tables"][idx] = entry
                    return
            self.tables_data["tables"].append(entry)

        def delete_table_rdbms_side_effect(table_id):
            original_len = len(self.tables_data["tables"])
            self.tables_data["tables"] = [
                t for t in self.tables_data["tables"] if t.get("id") != table_id
            ]
            return len(self.tables_data["tables"]) < original_len

        def get_tables_rdbms_used_by_side_effect(table_id):
            return self._used_by_result

        self.mock_store.load_tables_rdbms.side_effect = load_tables_rdbms_side_effect
        self.mock_store.save_tables_rdbms.side_effect = save_tables_rdbms_side_effect
        self.mock_store.get_table_rdbms.side_effect = get_table_rdbms_side_effect
        self.mock_store.upsert_table_rdbms.side_effect = upsert_table_rdbms_side_effect
        self.mock_store.delete_table_rdbms.side_effect = delete_table_rdbms_side_effect
        self.mock_store.get_tables_rdbms_used_by.side_effect = (
            get_tables_rdbms_used_by_side_effect
        )

        self._used_by_result = []

        # Patch both CBLStore and USE_CBL
        self.cbl_patcher = patch("rest.api_v2.CBLStore", return_value=self.mock_store)
        self.use_cbl_patcher = patch("rest.api_v2.USE_CBL", True)

        self.cbl_patcher.start()
        self.use_cbl_patcher.start()

    def tearDown(self):
        """Clean up patches."""
        self.cbl_patcher.stop()
        self.use_cbl_patcher.stop()
        super().tearDown()

    async def get_application(self):
        """Create test app with tables_rdbms routes."""
        app = web.Application()
        app.router.add_get("/api/v2/tables_rdbms", api_get_tables_rdbms)
        app.router.add_post("/api/v2/tables_rdbms", api_post_tables_rdbms)
        app.router.add_get(
            "/api/v2/tables_rdbms/{id}/used-by", api_get_table_rdbms_used_by
        )
        app.router.add_get("/api/v2/tables_rdbms/{id}", api_get_table_rdbms_entry)
        app.router.add_put("/api/v2/tables_rdbms/{id}", api_put_table_rdbms_entry)
        app.router.add_delete("/api/v2/tables_rdbms/{id}", api_delete_table_rdbms_entry)
        return app

    @unittest_run_loop
    async def test_get_empty_tables_rdbms(self):
        """GET /api/v2/tables_rdbms on fresh DB returns empty tables array."""
        resp = await self.client.request("GET", "/api/v2/tables_rdbms")
        assert resp.status == 200
        data = await resp.json()
        assert data["type"] == "tables_rdbms"
        assert data["tables"] == []

    @unittest_run_loop
    async def test_post_tables_rdbms_success(self):
        """POST /api/v2/tables_rdbms with valid tables saves successfully."""
        payload = {"tables": [SAMPLE_TABLE]}
        resp = await self.client.post("/api/v2/tables_rdbms", data=json.dumps(payload))
        assert resp.status == 200
        result = await resp.json()
        assert result["status"] == "ok"
        assert result["tables_count"] == 1

    @unittest_run_loop
    async def test_post_tables_rdbms_missing_id(self):
        """POST /api/v2/tables_rdbms with entry missing id returns 400."""
        payload = {
            "tables": [{"name": "orders", "engine_hint": "postgres"}],
        }
        resp = await self.client.post("/api/v2/tables_rdbms", data=json.dumps(payload))
        assert resp.status == 400
        data = await resp.json()
        assert "id is required" in data["error"]

    @unittest_run_loop
    async def test_post_tables_rdbms_missing_name(self):
        """POST /api/v2/tables_rdbms with entry missing name returns 400."""
        payload = {
            "tables": [{"id": "tbl-orders", "engine_hint": "postgres"}],
        }
        resp = await self.client.post("/api/v2/tables_rdbms", data=json.dumps(payload))
        assert resp.status == 400
        data = await resp.json()
        assert "name is required" in data["error"]

    @unittest_run_loop
    async def test_post_tables_rdbms_invalid_format(self):
        """POST /api/v2/tables_rdbms without tables array returns 400."""
        payload = {"not_tables": "invalid"}
        resp = await self.client.post("/api/v2/tables_rdbms", data=json.dumps(payload))
        assert resp.status == 400
        data = await resp.json()
        assert "tables must be an array" in data["error"]

    @unittest_run_loop
    async def test_get_table_entry_not_found(self):
        """GET /api/v2/tables_rdbms/unknown returns 404."""
        resp = await self.client.request("GET", "/api/v2/tables_rdbms/unknown")
        assert resp.status == 404

    @unittest_run_loop
    async def test_get_table_entry_found(self):
        """GET /api/v2/tables_rdbms/{id} returns table when it exists."""
        self.tables_data = {"type": "tables_rdbms", "tables": [SAMPLE_TABLE]}
        resp = await self.client.request("GET", "/api/v2/tables_rdbms/tbl-orders")
        assert resp.status == 200
        data = await resp.json()
        assert data["id"] == "tbl-orders"
        assert data["name"] == "orders"

    @unittest_run_loop
    async def test_put_table_entry(self):
        """PUT /api/v2/tables_rdbms/{id} updates a table."""
        self.tables_data = {"type": "tables_rdbms", "tables": [SAMPLE_TABLE]}
        update_payload = {"name": "orders_v2", "engine_hint": "mysql"}
        resp = await self.client.put(
            "/api/v2/tables_rdbms/tbl-orders", data=json.dumps(update_payload)
        )
        assert resp.status == 200
        result = await resp.json()
        assert result["status"] == "ok"
        assert result["id"] == "tbl-orders"

    @unittest_run_loop
    async def test_delete_table_entry(self):
        """DELETE /api/v2/tables_rdbms/{id} removes a table."""
        self.tables_data = {"type": "tables_rdbms", "tables": [SAMPLE_TABLE]}
        resp = await self.client.delete("/api/v2/tables_rdbms/tbl-orders")
        assert resp.status == 200
        result = await resp.json()
        assert result["status"] == "ok"
        assert result["id"] == "tbl-orders"

    @unittest_run_loop
    async def test_delete_table_entry_not_found(self):
        """DELETE /api/v2/tables_rdbms/unknown returns 404."""
        resp = await self.client.delete("/api/v2/tables_rdbms/unknown")
        assert resp.status == 404

    @unittest_run_loop
    async def test_get_used_by(self):
        """GET /api/v2/tables_rdbms/{id}/used-by returns jobs referencing this table."""
        self._used_by_result = [
            {
                "job_id": "job-1",
                "job_name": "Orders Pipeline",
                "table_name": "orders",
            }
        ]
        resp = await self.client.request(
            "GET", "/api/v2/tables_rdbms/tbl-orders/used-by"
        )
        assert resp.status == 200
        data = await resp.json()
        assert data["table_id"] == "tbl-orders"
        assert len(data["used_by"]) == 1
        assert data["used_by"][0]["job_id"] == "job-1"
