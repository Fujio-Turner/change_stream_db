"""
Test suite for Phase 5: Jobs REST API (CRUD + refresh endpoints).

Tests all job operations:
- List all jobs (GET /api/jobs)
- Get one job (GET /api/jobs/{id})
- Create job (POST /api/jobs) with input/output copies
- Update job (PUT /api/jobs/{id})
- Delete job (DELETE /api/jobs/{id})
- Refresh input (POST /api/jobs/{id}/refresh-input)
- Refresh output (POST /api/jobs/{id}/refresh-output)
"""

import json
import uuid
import pytest
from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop

from storage.cbl_store import CBLStore, USE_CBL
from rest.api_v2 import (
    api_get_jobs,
    api_get_job,
    api_post_jobs,
    api_put_job,
    api_delete_job,
    api_refresh_job_input,
    api_refresh_job_output,
)


@pytest.mark.skipif(not USE_CBL, reason="CBL not available")
class TestJobsAPI(AioHTTPTestCase):
    """Test Jobs CRUD REST API endpoints."""

    async def get_application(self):
        """Create test application with job routes."""
        app = web.Application()

        app.router.add_get("/api/jobs", api_get_jobs)
        app.router.add_get("/api/jobs/{id}", api_get_job)
        app.router.add_post("/api/jobs", api_post_jobs)
        app.router.add_put("/api/jobs/{id}", api_put_job)
        app.router.add_delete("/api/jobs/{id}", api_delete_job)
        app.router.add_post("/api/jobs/{id}/refresh-input", api_refresh_job_input)
        app.router.add_post("/api/jobs/{id}/refresh-output", api_refresh_job_output)

        return app

    def setUp(self):
        """Clear CBL before each test."""
        super().setUp()
        self.store = CBLStore()
        # Clear jobs, checkpoints
        for job in self.store.list_jobs():
            self.store.delete_job(job.get("id"))

    def _seed_inputs(self):
        """Create test inputs."""
        inputs_doc = {
            "type": "inputs_changes",
            "src": [
                {
                    "id": "test-input-1",
                    "name": "Test Input 1",
                    "enabled": True,
                    "source_type": "sync_gateway",
                    "host": "http://localhost:4984",
                    "database": "testdb",
                    "scope": "test",
                    "collection": "orders",
                    "accept_self_signed_certs": False,
                    "auth": {"method": "basic", "username": "user", "password": "pass"},
                    "changes_feed": {
                        "feed_type": "continuous",
                        "include_docs": True,
                        "active_only": True,
                    },
                }
            ],
        }
        self.store.save_inputs_changes(inputs_doc)

    def _seed_outputs(self, output_type):
        """Create test outputs for a given type."""
        if output_type == "rdbms":
            outputs_doc = {
                "type": "outputs_rdbms",
                "src": [
                    {
                        "id": "test-output-rdbms-1",
                        "name": "Test PostgreSQL",
                        "engine": "postgres",
                        "host": "localhost",
                        "port": 5432,
                        "database": "testdb",
                        "username": "user",
                        "password": "pass",
                        "pool_max": 10,
                        "enabled": True,
                    }
                ],
            }
        elif output_type == "http":
            outputs_doc = {
                "type": "outputs_http",
                "src": [
                    {
                        "id": "test-output-http-1",
                        "name": "Test Webhook",
                        "target_url": "http://example.com/webhook",
                        "write_method": "POST",
                        "timeout_seconds": 30,
                        "retry_count": 3,
                        "enabled": True,
                    }
                ],
            }
        elif output_type == "cloud":
            outputs_doc = {
                "type": "outputs_cloud",
                "src": [
                    {
                        "id": "test-output-cloud-1",
                        "name": "Test S3",
                        "provider": "s3",
                        "region": "us-east-1",
                        "bucket": "test-bucket",
                        "prefix": "data/",
                        "enabled": True,
                    }
                ],
            }
        elif output_type == "stdout":
            outputs_doc = {
                "type": "outputs_stdout",
                "src": [
                    {
                        "id": "test-output-stdout-1",
                        "name": "Test Stdout",
                        "pretty_print": True,
                        "enabled": True,
                    }
                ],
            }

        self.store.save_outputs(output_type, outputs_doc)

    @unittest_run_loop
    async def test_list_jobs_empty(self):
        """GET /api/jobs with no jobs returns empty list."""
        resp = await self.client.get("/api/jobs")
        assert resp.status == 200
        data = await resp.json()
        assert data["count"] == 0
        assert data["jobs"] == []

    @unittest_run_loop
    async def test_create_job_rdbms(self):
        """POST /api/jobs creates a new job with RDBMS output."""
        self._seed_inputs()
        self._seed_outputs("rdbms")

        payload = {
            "input_id": "test-input-1",
            "output_type": "rdbms",
            "output_id": "test-output-rdbms-1",
            "name": "Test Job 1",
            "system": {"threads": 1},
            "mapping": {"source": "orders", "target": "orders"},
        }

        resp = await self.client.post("/api/jobs", json=payload)
        assert resp.status == 201
        data = await resp.json()
        assert data["status"] == "ok"
        assert "job_id" in data
        assert data["name"] == "Test Job 1"

        # Verify job was saved
        job_id = data["job_id"]
        resp2 = await self.client.get(f"/api/jobs/{job_id}")
        assert resp2.status == 200
        job = await resp2.json()
        assert job["id"] == job_id
        assert job["name"] == "Test Job 1"
        assert job["output_type"] == "rdbms"
        assert len(job["inputs"]) == 1
        assert len(job["outputs"]) == 1
        assert job["inputs"][0]["id"] == "test-input-1"
        assert job["outputs"][0]["id"] == "test-output-rdbms-1"

    @unittest_run_loop
    async def test_create_job_http(self):
        """POST /api/jobs creates a job with HTTP output."""
        self._seed_inputs()
        self._seed_outputs("http")

        payload = {
            "input_id": "test-input-1",
            "output_type": "http",
            "output_id": "test-output-http-1",
        }

        resp = await self.client.post("/api/jobs", json=payload)
        assert resp.status == 201
        data = await resp.json()
        job_id = data["job_id"]

        job_resp = await self.client.get(f"/api/jobs/{job_id}")
        job = await job_resp.json()
        assert job["output_type"] == "http"

    @unittest_run_loop
    async def test_create_job_cloud(self):
        """POST /api/jobs creates a job with Cloud output."""
        self._seed_inputs()
        self._seed_outputs("cloud")

        payload = {
            "input_id": "test-input-1",
            "output_type": "cloud",
            "output_id": "test-output-cloud-1",
        }

        resp = await self.client.post("/api/jobs", json=payload)
        assert resp.status == 201

    @unittest_run_loop
    async def test_create_job_stdout(self):
        """POST /api/jobs creates a job with Stdout output."""
        self._seed_inputs()
        self._seed_outputs("stdout")

        payload = {
            "input_id": "test-input-1",
            "output_type": "stdout",
            "output_id": "test-output-stdout-1",
        }

        resp = await self.client.post("/api/jobs", json=payload)
        assert resp.status == 201

    @unittest_run_loop
    async def test_create_job_missing_input_id(self):
        """POST /api/jobs without input_id returns 400."""
        self._seed_inputs()
        self._seed_outputs("rdbms")

        payload = {
            "output_type": "rdbms",
            "output_id": "test-output-rdbms-1",
        }

        resp = await self.client.post("/api/jobs", json=payload)
        assert resp.status == 400
        data = await resp.json()
        assert "input_id" in data["error"]

    @unittest_run_loop
    async def test_create_job_missing_output_type(self):
        """POST /api/jobs without output_type returns 400."""
        self._seed_inputs()
        self._seed_outputs("rdbms")

        payload = {
            "input_id": "test-input-1",
            "output_id": "test-output-rdbms-1",
        }

        resp = await self.client.post("/api/jobs", json=payload)
        assert resp.status == 400
        data = await resp.json()
        assert "output_type" in data["error"]

    @unittest_run_loop
    async def test_create_job_invalid_output_type(self):
        """POST /api/jobs with invalid output_type returns 400."""
        self._seed_inputs()

        payload = {
            "input_id": "test-input-1",
            "output_type": "invalid",
            "output_id": "test-output-1",
        }

        resp = await self.client.post("/api/jobs", json=payload)
        assert resp.status == 400
        data = await resp.json()
        assert "Invalid output_type" in data["error"]

    @unittest_run_loop
    async def test_create_job_missing_output_id(self):
        """POST /api/jobs without output_id returns 400."""
        self._seed_inputs()
        self._seed_outputs("rdbms")

        payload = {
            "input_id": "test-input-1",
            "output_type": "rdbms",
        }

        resp = await self.client.post("/api/jobs", json=payload)
        assert resp.status == 400
        data = await resp.json()
        assert "output_id" in data["error"]

    @unittest_run_loop
    async def test_create_job_nonexistent_input(self):
        """POST /api/jobs with nonexistent input_id returns 400."""
        self._seed_inputs()
        self._seed_outputs("rdbms")

        payload = {
            "input_id": "nonexistent",
            "output_type": "rdbms",
            "output_id": "test-output-rdbms-1",
        }

        resp = await self.client.post("/api/jobs", json=payload)
        assert resp.status == 400
        data = await resp.json()
        assert "not found" in data["error"]

    @unittest_run_loop
    async def test_create_job_nonexistent_output(self):
        """POST /api/jobs with nonexistent output_id returns 400."""
        self._seed_inputs()
        self._seed_outputs("rdbms")

        payload = {
            "input_id": "test-input-1",
            "output_type": "rdbms",
            "output_id": "nonexistent",
        }

        resp = await self.client.post("/api/jobs", json=payload)
        assert resp.status == 400
        data = await resp.json()
        assert "not found" in data["error"]

    @unittest_run_loop
    async def test_list_jobs(self):
        """GET /api/jobs lists all jobs."""
        self._seed_inputs()
        self._seed_outputs("rdbms")

        # Create 3 jobs
        for i in range(3):
            payload = {
                "input_id": "test-input-1",
                "output_type": "rdbms",
                "output_id": "test-output-rdbms-1",
                "name": f"Job {i}",
            }
            resp = await self.client.post("/api/jobs", json=payload)
            assert resp.status == 201

        # List jobs
        resp = await self.client.get("/api/jobs")
        assert resp.status == 200
        data = await resp.json()
        assert data["count"] == 3
        assert len(data["jobs"]) == 3

    @unittest_run_loop
    async def test_get_job_not_found(self):
        """GET /api/jobs/{id} with nonexistent id returns 404."""
        resp = await self.client.get("/api/jobs/nonexistent-id")
        assert resp.status == 404
        data = await resp.json()
        assert "not found" in data["error"]

    @unittest_run_loop
    async def test_update_job_name(self):
        """PUT /api/jobs/{id} updates job name."""
        self._seed_inputs()
        self._seed_outputs("rdbms")

        # Create job
        payload = {
            "input_id": "test-input-1",
            "output_type": "rdbms",
            "output_id": "test-output-rdbms-1",
            "name": "Original Name",
        }
        resp = await self.client.post("/api/jobs", json=payload)
        job_id = (await resp.json())["job_id"]

        # Update name
        update_payload = {"name": "Updated Name"}
        resp = await self.client.put(f"/api/jobs/{job_id}", json=update_payload)
        assert resp.status == 200

        # Verify update
        resp = await self.client.get(f"/api/jobs/{job_id}")
        job = await resp.json()
        assert job["name"] == "Updated Name"

    @unittest_run_loop
    async def test_update_job_system_config(self):
        """PUT /api/jobs/{id} updates system config."""
        self._seed_inputs()
        self._seed_outputs("rdbms")

        # Create job
        payload = {
            "input_id": "test-input-1",
            "output_type": "rdbms",
            "output_id": "test-output-rdbms-1",
            "system": {"threads": 1},
        }
        resp = await self.client.post("/api/jobs", json=payload)
        job_id = (await resp.json())["job_id"]

        # Update system
        update_payload = {"system": {"threads": 4, "batch_size": 100}}
        resp = await self.client.put(f"/api/jobs/{job_id}", json=update_payload)
        assert resp.status == 200

        # Verify update
        resp = await self.client.get(f"/api/jobs/{job_id}")
        job = await resp.json()
        assert job["system"]["threads"] == 4
        assert job["system"]["batch_size"] == 100

    @unittest_run_loop
    async def test_update_job_mapping(self):
        """PUT /api/jobs/{id} updates schema mapping."""
        self._seed_inputs()
        self._seed_outputs("rdbms")

        # Create job
        payload = {
            "input_id": "test-input-1",
            "output_type": "rdbms",
            "output_id": "test-output-rdbms-1",
            "mapping": {"source": "orders", "target": "orders"},
        }
        resp = await self.client.post("/api/jobs", json=payload)
        job_id = (await resp.json())["job_id"]

        # Update mapping
        update_payload = {
            "mapping": {
                "source": "orders",
                "target": "orders",
                "fields": {"order_id": "id"},
            }
        }
        resp = await self.client.put(f"/api/jobs/{job_id}", json=update_payload)
        assert resp.status == 200

        # Verify update
        resp = await self.client.get(f"/api/jobs/{job_id}")
        job = await resp.json()
        assert "fields" in job["mapping"]

    @unittest_run_loop
    async def test_update_job_nonexistent(self):
        """PUT /api/jobs/{id} with nonexistent id returns 404."""
        payload = {"name": "Updated"}
        resp = await self.client.put("/api/jobs/nonexistent", json=payload)
        assert resp.status == 404

    @unittest_run_loop
    async def test_delete_job(self):
        """DELETE /api/jobs/{id} removes job and checkpoint."""
        self._seed_inputs()
        self._seed_outputs("rdbms")

        # Create job
        payload = {
            "input_id": "test-input-1",
            "output_type": "rdbms",
            "output_id": "test-output-rdbms-1",
        }
        resp = await self.client.post("/api/jobs", json=payload)
        job_id = (await resp.json())["job_id"]

        # Delete job
        resp = await self.client.delete(f"/api/jobs/{job_id}")
        assert resp.status == 200

        # Verify deletion
        resp = await self.client.get(f"/api/jobs/{job_id}")
        assert resp.status == 404

    @unittest_run_loop
    async def test_delete_job_nonexistent(self):
        """DELETE /api/jobs/{id} with nonexistent id returns 404."""
        resp = await self.client.delete("/api/jobs/nonexistent")
        assert resp.status == 404

    @unittest_run_loop
    async def test_refresh_job_input(self):
        """POST /api/jobs/{id}/refresh-input re-copies input from source."""
        self._seed_inputs()
        self._seed_outputs("rdbms")

        # Create job
        payload = {
            "input_id": "test-input-1",
            "output_type": "rdbms",
            "output_id": "test-output-rdbms-1",
        }
        resp = await self.client.post("/api/jobs", json=payload)
        job_id = (await resp.json())["job_id"]

        # Get original input
        resp = await self.client.get(f"/api/jobs/{job_id}")
        original_job = await resp.json()
        original_input = original_job["inputs"][0]

        # Update input in source
        inputs_doc = self.store.load_inputs_changes()
        inputs_doc["src"][0]["name"] = "Updated Input Name"
        self.store.save_inputs_changes(inputs_doc)

        # Refresh job input
        resp = await self.client.post(f"/api/jobs/{job_id}/refresh-input")
        assert resp.status == 200

        # Verify input was updated in job
        resp = await self.client.get(f"/api/jobs/{job_id}")
        updated_job = await resp.json()
        assert updated_job["inputs"][0]["name"] == "Updated Input Name"

    @unittest_run_loop
    async def test_refresh_job_output(self):
        """POST /api/jobs/{id}/refresh-output re-copies output from source."""
        self._seed_inputs()
        self._seed_outputs("rdbms")

        # Create job
        payload = {
            "input_id": "test-input-1",
            "output_type": "rdbms",
            "output_id": "test-output-rdbms-1",
        }
        resp = await self.client.post("/api/jobs", json=payload)
        job_id = (await resp.json())["job_id"]

        # Update output in source
        outputs_doc = self.store.load_outputs("rdbms")
        outputs_doc["src"][0]["host"] = "new-host"
        self.store.save_outputs("rdbms", outputs_doc)

        # Refresh job output
        resp = await self.client.post(f"/api/jobs/{job_id}/refresh-output")
        assert resp.status == 200

        # Verify output was updated in job
        resp = await self.client.get(f"/api/jobs/{job_id}")
        updated_job = await resp.json()
        assert updated_job["outputs"][0]["host"] == "new-host"

    @unittest_run_loop
    async def test_refresh_job_input_nonexistent(self):
        """POST /api/jobs/{id}/refresh-input with nonexistent id returns 404."""
        resp = await self.client.post("/api/jobs/nonexistent/refresh-input")
        assert resp.status == 404

    @unittest_run_loop
    async def test_refresh_job_output_nonexistent(self):
        """POST /api/jobs/{id}/refresh-output with nonexistent id returns 404."""
        resp = await self.client.post("/api/jobs/nonexistent/refresh-output")
        assert resp.status == 404

    @unittest_run_loop
    async def test_create_job_checkpoint(self):
        """POST /api/jobs creates a checkpoint document."""
        self._seed_inputs()
        self._seed_outputs("rdbms")

        payload = {
            "input_id": "test-input-1",
            "output_type": "rdbms",
            "output_id": "test-output-rdbms-1",
        }

        resp = await self.client.post("/api/jobs", json=payload)
        job_id = (await resp.json())["job_id"]

        # Verify checkpoint was created
        checkpoint = self.store.load_checkpoint(job_id)
        assert checkpoint is not None
        assert checkpoint["job_id"] == job_id
        assert checkpoint["last_seq"] == "0"

    @unittest_run_loop
    async def test_job_copies_input_output(self):
        """Job creation copies input/output entries, not references."""
        self._seed_inputs()
        self._seed_outputs("rdbms")

        # Create job
        payload = {
            "input_id": "test-input-1",
            "output_type": "rdbms",
            "output_id": "test-output-rdbms-1",
        }
        resp = await self.client.post("/api/jobs", json=payload)
        job_id = (await resp.json())["job_id"]

        # Get job
        resp = await self.client.get(f"/api/jobs/{job_id}")
        job = await resp.json()

        # Verify input was copied (has all fields)
        assert "auth" in job["inputs"][0]
        assert "changes_feed" in job["inputs"][0]

        # Verify output was copied (has all fields)
        assert "engine" in job["outputs"][0]
        assert "port" in job["outputs"][0]

    @unittest_run_loop
    async def test_type_isolation_all_4_types(self):
        """Create and manage jobs for all 4 output types independently."""
        self._seed_inputs()
        self._seed_outputs("rdbms")
        self._seed_outputs("http")
        self._seed_outputs("cloud")
        self._seed_outputs("stdout")

        job_ids = {}

        # Create one job per output type
        for output_type, output_id in [
            ("rdbms", "test-output-rdbms-1"),
            ("http", "test-output-http-1"),
            ("cloud", "test-output-cloud-1"),
            ("stdout", "test-output-stdout-1"),
        ]:
            payload = {
                "input_id": "test-input-1",
                "output_type": output_type,
                "output_id": output_id,
                "name": f"Job {output_type}",
            }
            resp = await self.client.post("/api/jobs", json=payload)
            data = await resp.json()
            job_ids[output_type] = data["job_id"]

        # Verify each job has correct output type
        for output_type, job_id in job_ids.items():
            resp = await self.client.get(f"/api/jobs/{job_id}")
            job = await resp.json()
            assert job["output_type"] == output_type


# ─────────────────────────────────────────────────────────────────
# Standalone test functions (for pytest discovery)
# ─────────────────────────────────────────────────────────────────


@pytest.mark.skipif(not USE_CBL, reason="CBL not available")
def test_jobs_api_available():
    """Verify jobs API module is importable."""
    assert callable(api_get_jobs)
    assert callable(api_get_job)
    assert callable(api_post_jobs)
    assert callable(api_put_job)
    assert callable(api_delete_job)
    assert callable(api_refresh_job_input)
    assert callable(api_refresh_job_output)
