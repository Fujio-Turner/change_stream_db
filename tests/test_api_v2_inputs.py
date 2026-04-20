"""
Tests for API v2.0 inputs endpoints.
"""

import json
import pytest
from unittest.mock import patch, MagicMock
from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop

from rest.api_v2 import (
    api_get_inputs_changes,
    api_post_inputs_changes,
    api_put_inputs_changes_entry,
    api_delete_inputs_changes_entry,
)


class TestInputsAPI(AioHTTPTestCase):
    """Test the /api/inputs_changes endpoints."""

    def setUp(self):
        """Set up mocked CBL store."""
        super().setUp()
        self.store_data = {"type": "inputs_changes", "src": []}
        self.mock_store = MagicMock()

        def load_inputs_side_effect():
            return self.store_data if self.store_data["src"] else None

        def save_inputs_side_effect(data):
            self.store_data = data

        self.mock_store.load_inputs_changes.side_effect = load_inputs_side_effect
        self.mock_store.save_inputs_changes.side_effect = save_inputs_side_effect

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
        """Create test app with inputs routes."""
        app = web.Application()
        app.router.add_get("/api/inputs_changes", api_get_inputs_changes)
        app.router.add_post("/api/inputs_changes", api_post_inputs_changes)
        app.router.add_put("/api/inputs_changes/{id}", api_put_inputs_changes_entry)
        app.router.add_delete(
            "/api/inputs_changes/{id}", api_delete_inputs_changes_entry
        )
        return app

    @unittest_run_loop
    async def test_get_empty_inputs(self):
        """GET /api/inputs_changes on fresh DB returns empty array."""
        resp = await self.client.request("GET", "/api/inputs_changes")
        assert resp.status == 200
        data = await resp.json()
        assert data["type"] == "inputs_changes"
        assert data["src"] == []

    @unittest_run_loop
    async def test_post_inputs_success(self):
        """POST /api/inputs_changes saves inputs successfully."""
        payload = {
            "type": "inputs_changes",
            "src": [
                {
                    "id": "sg-test",
                    "name": "Test Source",
                    "enabled": True,
                    "source_type": "sync_gateway",
                    "host": "http://localhost:4984",
                    "database": "db",
                    "scope": "test",
                    "collection": "items",
                    "auth": {"method": "basic", "username": "user", "password": "pass"},
                }
            ],
        }
        resp = await self.client.request("POST", "/api/inputs_changes", json=payload)
        assert resp.status == 200
        data = await resp.json()
        assert data["status"] == "ok"
        assert data["src_count"] == 1

    @unittest_run_loop
    async def test_post_inputs_validation_missing_id(self):
        """POST /api/inputs_changes validates src[].id is required."""
        payload = {
            "type": "inputs_changes",
            "src": [
                {
                    "name": "No ID",
                    "source_type": "sync_gateway",
                    "host": "http://localhost:4984",
                }
            ],
        }
        resp = await self.client.request("POST", "/api/inputs_changes", json=payload)
        assert resp.status == 400
        data = await resp.json()
        assert "id" in data["error"]

    @unittest_run_loop
    async def test_post_inputs_validation_missing_source_type(self):
        """POST /api/inputs_changes validates src[].source_type is required."""
        payload = {
            "type": "inputs_changes",
            "src": [{"id": "test", "name": "Test", "host": "http://localhost:4984"}],
        }
        resp = await self.client.request("POST", "/api/inputs_changes", json=payload)
        assert resp.status == 400
        data = await resp.json()
        assert "source_type" in data["error"]

    @unittest_run_loop
    async def test_post_inputs_validation_invalid_source_type(self):
        """POST /api/inputs_changes validates source_type is one of the allowed values."""
        payload = {
            "type": "inputs_changes",
            "src": [
                {
                    "id": "test",
                    "source_type": "invalid_type",
                    "host": "http://localhost:4984",
                }
            ],
        }
        resp = await self.client.request("POST", "/api/inputs_changes", json=payload)
        assert resp.status == 400

    @unittest_run_loop
    async def test_post_inputs_validation_missing_host(self):
        """POST /api/inputs_changes validates src[].host is required."""
        payload = {
            "type": "inputs_changes",
            "src": [{"id": "test", "source_type": "sync_gateway"}],
        }
        resp = await self.client.request("POST", "/api/inputs_changes", json=payload)
        assert resp.status == 400
        data = await resp.json()
        assert "host" in data["error"]

    @unittest_run_loop
    async def test_get_after_post(self):
        """GET /api/inputs_changes returns saved inputs."""
        # Post an input
        payload = {
            "type": "inputs_changes",
            "src": [
                {
                    "id": "sg-us",
                    "name": "US Region",
                    "source_type": "sync_gateway",
                    "host": "http://localhost:4984",
                    "database": "db",
                    "scope": "us",
                    "collection": "orders",
                    "auth": {
                        "method": "basic",
                        "username": "bob",
                        "password": "secret",
                    },
                }
            ],
        }
        resp = await self.client.request("POST", "/api/inputs_changes", json=payload)
        assert resp.status == 200

        # Get and verify
        resp = await self.client.request("GET", "/api/inputs_changes")
        assert resp.status == 200
        data = await resp.json()
        assert len(data["src"]) == 1
        assert data["src"][0]["id"] == "sg-us"
        assert data["src"][0]["name"] == "US Region"

    @unittest_run_loop
    async def test_put_update_input(self):
        """PUT /api/inputs_changes/{id} updates an input."""
        # First post an input
        payload = {
            "type": "inputs_changes",
            "src": [
                {
                    "id": "sg-test",
                    "name": "Original Name",
                    "source_type": "sync_gateway",
                    "host": "http://localhost:4984",
                }
            ],
        }
        resp = await self.client.request("POST", "/api/inputs_changes", json=payload)
        assert resp.status == 200

        # Update it
        update = {"name": "Updated Name"}
        resp = await self.client.request(
            "PUT", "/api/inputs_changes/sg-test", json=update
        )
        assert resp.status == 200
        data = await resp.json()
        assert data["status"] == "ok"

        # Verify the update
        resp = await self.client.request("GET", "/api/inputs_changes")
        data = await resp.json()
        assert data["src"][0]["name"] == "Updated Name"

    @unittest_run_loop
    async def test_delete_input(self):
        """DELETE /api/inputs_changes/{id} removes an input."""
        # First post an input
        payload = {
            "type": "inputs_changes",
            "src": [
                {
                    "id": "sg-remove",
                    "name": "To Remove",
                    "source_type": "sync_gateway",
                    "host": "http://localhost:4984",
                }
            ],
        }
        resp = await self.client.request("POST", "/api/inputs_changes", json=payload)
        assert resp.status == 200

        # Delete it
        resp = await self.client.request("DELETE", "/api/inputs_changes/sg-remove")
        assert resp.status == 200

        # Verify it's gone
        resp = await self.client.request("GET", "/api/inputs_changes")
        data = await resp.json()
        assert len(data["src"]) == 0

    @unittest_run_loop
    async def test_delete_nonexistent_input(self):
        """DELETE /api/inputs_changes/{id} on non-existent input returns 404."""
        resp = await self.client.request("DELETE", "/api/inputs_changes/nonexistent")
        # Returns 404 when the inputs_changes document doesn't exist
        assert resp.status == 404
