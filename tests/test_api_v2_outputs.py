"""
Tests for API v2.0 outputs endpoints (rdbms, http, cloud, stdout).
"""

import json
import pytest
from unittest.mock import patch, MagicMock
from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop

from rest.api_v2 import (
    api_get_outputs,
    api_post_outputs,
    api_put_outputs_entry,
    api_delete_outputs_entry,
)


class TestOutputsAPI(AioHTTPTestCase):
    """Test the /api/outputs_{type} endpoints."""

    def setUp(self):
        """Set up mocked CBL store."""
        super().setUp()
        self.store_data = {
            "rdbms": {"type": "outputs_rdbms", "src": []},
            "http": {"type": "outputs_http", "src": []},
            "cloud": {"type": "outputs_cloud", "src": []},
            "stdout": {"type": "outputs_stdout", "src": []},
        }
        self.mock_store = MagicMock()

        def load_outputs_side_effect(output_type):
            return (
                self.store_data[output_type]
                if self.store_data[output_type]["src"]
                else None
            )

        def save_outputs_side_effect(output_type, data):
            self.store_data[output_type] = data

        self.mock_store.load_outputs.side_effect = load_outputs_side_effect
        self.mock_store.save_outputs.side_effect = save_outputs_side_effect

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
        """Create test app with outputs routes."""
        app = web.Application()
        app.router.add_get("/api/outputs_{type}", api_get_outputs)
        app.router.add_post("/api/outputs_{type}", api_post_outputs)
        app.router.add_put("/api/outputs_{type}/{id}", api_put_outputs_entry)
        app.router.add_delete("/api/outputs_{type}/{id}", api_delete_outputs_entry)
        return app

    @unittest_run_loop
    async def test_get_empty_rdbms_outputs(self):
        """GET /api/outputs_rdbms on fresh DB returns empty array."""
        resp = await self.client.request("GET", "/api/outputs_rdbms")
        assert resp.status == 200
        data = await resp.json()
        assert data["type"] == "outputs_rdbms"
        assert data["src"] == []

    @unittest_run_loop
    async def test_post_rdbms_outputs_success(self):
        """POST /api/outputs_rdbms saves RDBMS outputs successfully."""
        payload = {
            "type": "outputs_rdbms",
            "src": [
                {
                    "id": "pg-prod",
                    "name": "Production PostgreSQL",
                    "enabled": True,
                    "engine": "postgres",
                    "host": "db.example.com",
                    "port": 5432,
                    "database": "prod",
                    "username": "user",
                    "password": "pass",
                    "pool_max": 10,
                }
            ],
        }
        resp = await self.client.post("/api/outputs_rdbms", data=json.dumps(payload))
        assert resp.status == 200
        result = await resp.json()
        assert result["status"] == "ok"
        assert result["src_count"] == 1

    @unittest_run_loop
    async def test_post_http_outputs_success(self):
        """POST /api/outputs_http saves HTTP outputs successfully."""
        payload = {
            "type": "outputs_http",
            "src": [
                {
                    "id": "webhook-prod",
                    "name": "Production Webhook",
                    "enabled": True,
                    "target_url": "https://api.example.com/webhook",
                    "write_method": "POST",
                    "timeout_seconds": 30,
                    "retry_count": 3,
                }
            ],
        }
        resp = await self.client.post("/api/outputs_http", data=json.dumps(payload))
        assert resp.status == 200
        result = await resp.json()
        assert result["status"] == "ok"
        assert result["src_count"] == 1

    @unittest_run_loop
    async def test_post_cloud_outputs_success(self):
        """POST /api/outputs_cloud saves cloud outputs successfully."""
        payload = {
            "type": "outputs_cloud",
            "src": [
                {
                    "id": "s3-prod",
                    "name": "Production S3",
                    "enabled": True,
                    "provider": "s3",
                    "bucket": "prod-bucket",
                    "region": "us-east-1",
                    "prefix": "changes/",
                }
            ],
        }
        resp = await self.client.post("/api/outputs_cloud", data=json.dumps(payload))
        assert resp.status == 200
        result = await resp.json()
        assert result["status"] == "ok"
        assert result["src_count"] == 1

    @unittest_run_loop
    async def test_post_stdout_outputs_success(self):
        """POST /api/outputs_stdout saves stdout outputs successfully."""
        payload = {
            "type": "outputs_stdout",
            "src": [
                {
                    "id": "stdout-test",
                    "name": "Test Stdout",
                    "enabled": True,
                    "pretty_print": True,
                }
            ],
        }
        resp = await self.client.post("/api/outputs_stdout", data=json.dumps(payload))
        assert resp.status == 200
        result = await resp.json()
        assert result["status"] == "ok"
        assert result["src_count"] == 1

    @unittest_run_loop
    async def test_post_outputs_validation_missing_id(self):
        """POST /api/outputs_rdbms rejects missing id."""
        payload = {
            "type": "outputs_rdbms",
            "src": [{"engine": "postgres"}],  # Missing id
        }
        resp = await self.client.post("/api/outputs_rdbms", data=json.dumps(payload))
        assert resp.status == 400
        data = await resp.json()
        assert "id is required" in data["error"]

    @unittest_run_loop
    async def test_post_outputs_validation_invalid_type(self):
        """POST /api/outputs_invalid rejects invalid type."""
        resp = await self.client.post("/api/outputs_invalid", data=json.dumps({}))
        assert resp.status == 400

    @unittest_run_loop
    async def test_put_outputs_entry_success(self):
        """PUT /api/outputs_rdbms/{id} updates an output entry."""
        # First insert
        payload = {
            "type": "outputs_rdbms",
            "src": [{"id": "pg-prod", "name": "PostgreSQL", "enabled": True}],
        }
        await self.client.post("/api/outputs_rdbms", data=json.dumps(payload))

        # Then update
        update_payload = {"name": "PostgreSQL (Updated)", "enabled": False}
        resp = await self.client.put(
            "/api/outputs_rdbms/pg-prod", data=json.dumps(update_payload)
        )
        assert resp.status == 200
        result = await resp.json()
        assert result["status"] == "ok"
        assert result["id"] == "pg-prod"

    @unittest_run_loop
    async def test_put_outputs_entry_not_found(self):
        """PUT /api/outputs_rdbms/{id} returns 404 for non-existent entry."""
        payload = {
            "type": "outputs_rdbms",
            "src": [{"id": "pg-prod", "name": "PostgreSQL"}],
        }
        await self.client.post("/api/outputs_rdbms", data=json.dumps(payload))

        resp = await self.client.put(
            "/api/outputs_rdbms/nonexistent", data=json.dumps({"name": "Updated"})
        )
        assert resp.status == 404

    @unittest_run_loop
    async def test_delete_outputs_entry_success(self):
        """DELETE /api/outputs_rdbms/{id} removes an output entry."""
        # First insert
        payload = {
            "type": "outputs_rdbms",
            "src": [{"id": "pg-prod", "name": "PostgreSQL"}],
        }
        await self.client.post("/api/outputs_rdbms", data=json.dumps(payload))

        # Then delete
        resp = await self.client.delete("/api/outputs_rdbms/pg-prod")
        assert resp.status == 200
        result = await resp.json()
        assert result["status"] == "ok"
        assert result["id"] == "pg-prod"

    @unittest_run_loop
    async def test_delete_outputs_entry_not_found(self):
        """DELETE /api/outputs_rdbms/{id} returns 404 for non-existent entry."""
        resp = await self.client.delete("/api/outputs_rdbms/nonexistent")
        assert resp.status == 404

    @unittest_run_loop
    async def test_multiple_output_types_isolated(self):
        """Different output types are stored independently."""
        # Insert into rdbms
        rdbms_payload = {
            "type": "outputs_rdbms",
            "src": [{"id": "pg-prod", "name": "PostgreSQL"}],
        }
        await self.client.post("/api/outputs_rdbms", data=json.dumps(rdbms_payload))

        # Insert into http
        http_payload = {
            "type": "outputs_http",
            "src": [{"id": "webhook-test", "name": "Webhook"}],
        }
        await self.client.post("/api/outputs_http", data=json.dumps(http_payload))

        # Verify they are separate
        rdbms_resp = await self.client.request("GET", "/api/outputs_rdbms")
        rdbms_data = await rdbms_resp.json()
        assert len(rdbms_data["src"]) == 1
        assert rdbms_data["src"][0]["id"] == "pg-prod"

        http_resp = await self.client.request("GET", "/api/outputs_http")
        http_data = await http_resp.json()
        assert len(http_data["src"]) == 1
        assert http_data["src"][0]["id"] == "webhook-test"
