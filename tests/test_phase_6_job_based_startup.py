#!/usr/bin/env python3
"""
Unit tests for Phase 6: Job-Based Startup

Tests the job loading, config building, and checkpoint isolation.
"""

import unittest
from unittest.mock import Mock, MagicMock, patch
import sys
import os

# Add parent to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import (
    load_enabled_jobs,
    build_pipeline_config_from_job,
    migrate_legacy_config_to_job,
)


class TestLoadEnabledJobs(unittest.TestCase):
    """Test load_enabled_jobs function"""

    def test_load_enabled_jobs_empty(self):
        """Test loading jobs when none exist"""
        db = Mock()
        db.list_jobs.return_value = []

        jobs = load_enabled_jobs(db)

        self.assertEqual(jobs, [])
        db.list_jobs.assert_called_once()

    def test_load_enabled_jobs_filters_disabled(self):
        """Test that disabled jobs are filtered out"""
        db = Mock()
        db.list_jobs.return_value = [
            {"_id": "job1", "name": "Job 1", "enabled": True},
            {"_id": "job2", "name": "Job 2", "enabled": False},
            {"_id": "job3", "name": "Job 3", "enabled": True},
        ]

        jobs = load_enabled_jobs(db)

        self.assertEqual(len(jobs), 2)
        self.assertEqual(jobs[0]["_id"], "job1")
        self.assertEqual(jobs[1]["_id"], "job3")

    def test_load_enabled_jobs_handles_missing_enabled_field(self):
        """Test that jobs without 'enabled' field default to False"""
        db = Mock()
        db.list_jobs.return_value = [
            {"_id": "job1", "name": "Job 1", "enabled": True},
            {"_id": "job2", "name": "Job 2"},  # No 'enabled' field
        ]

        jobs = load_enabled_jobs(db)

        # Only job1 (with enabled=True) should be returned
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["_id"], "job1")

    def test_load_enabled_jobs_handles_error(self):
        """Test error handling when list_jobs fails"""
        db = Mock()
        db.list_jobs.side_effect = Exception("DB connection failed")

        # Should not raise, but return empty list
        jobs = load_enabled_jobs(db)

        self.assertEqual(jobs, [])


class TestBuildPipelineConfigFromJob(unittest.TestCase):
    """Test build_pipeline_config_from_job function"""

    def test_build_config_basic(self):
        """Test basic config building from job"""
        job_doc = {
            "_id": "job123",
            "name": "Test Job",
            "inputs": [
                {
                    "url": "http://localhost:4984/db",
                    "database": "db",
                    "src": "sync_gateway",
                    "auth": {"method": "basic", "username": "user"},
                    "changes_feed": {"poll_interval_seconds": 10},
                    "processing": {"max_concurrent": 20},
                }
            ],
            "outputs": [
                {
                    "mode": "http",
                    "target_url": "http://localhost:3000",
                }
            ],
            "output_type": "http",
        }

        config = build_pipeline_config_from_job(job_doc)

        self.assertEqual(config["job_id"], "job123")
        self.assertEqual(config["job_name"], "Test Job")
        self.assertEqual(config["gateway"]["url"], "http://localhost:4984/db")
        self.assertEqual(config["output"]["mode"], "http")
        self.assertTrue(config["checkpoint"]["enabled"])

    def test_build_config_with_id_field(self):
        """Test building config when job uses 'id' instead of '_id'"""
        job_doc = {
            "id": "job456",
            "name": "Another Job",
            "inputs": [{"url": "http://localhost:4984/db", "database": "db"}],
            "outputs": [{"mode": "stdout"}],
        }

        config = build_pipeline_config_from_job(job_doc)

        self.assertEqual(config["job_id"], "job456")

    def test_build_config_missing_inputs_raises(self):
        """Test that missing inputs raises ValueError"""
        job_doc = {
            "_id": "job789",
            "name": "Bad Job",
            "inputs": [],
            "outputs": [{"mode": "stdout"}],
        }

        with self.assertRaises(ValueError) as ctx:
            build_pipeline_config_from_job(job_doc)

        self.assertIn("missing inputs", str(ctx.exception))

    def test_build_config_missing_outputs_raises(self):
        """Test that missing outputs raises ValueError"""
        job_doc = {
            "_id": "job999",
            "name": "Bad Job",
            "inputs": [{"url": "http://localhost:4984/db", "database": "db"}],
            "outputs": [],
        }

        with self.assertRaises(ValueError) as ctx:
            build_pipeline_config_from_job(job_doc)

        self.assertIn("missing inputs or outputs", str(ctx.exception))

    def test_build_config_checkpoint_isolation(self):
        """Test that checkpoint filename includes job_id"""
        job_doc = {
            "_id": "job_abc123",
            "inputs": [{"url": "http://localhost/db", "database": "db"}],
            "outputs": [{"mode": "stdout"}],
        }

        config = build_pipeline_config_from_job(job_doc)

        # Checkpoint file should include job_id
        self.assertIn("job_abc123", config["checkpoint"]["file"])


class TestMigrateLegacyConfig(unittest.TestCase):
    """Test migrate_legacy_config_to_job function"""

    def test_migrate_valid_config(self):
        """Test migrating a valid v1.x config"""
        db = Mock()
        cfg = {
            "gateway": {
                "url": "http://localhost:4984/db",
                "database": "db",
            },
            "output": {
                "mode": "http",
                "target_url": "http://localhost:3000",
            },
            "system": {"max_threads": 4},
            "retry": {"max_retries": 3},
        }

        job_doc = migrate_legacy_config_to_job(db, cfg)

        self.assertIsNotNone(job_doc)
        self.assertIn("legacy_auto_migrated", job_doc["_id"])
        self.assertTrue(job_doc["enabled"])
        self.assertEqual(len(job_doc["inputs"]), 1)
        self.assertEqual(len(job_doc["outputs"]), 1)
        db.save_job.assert_called_once()

    def test_migrate_missing_gateway(self):
        """Test that missing gateway is handled"""
        db = Mock()
        cfg = {
            "output": {"mode": "http"},
        }

        result = migrate_legacy_config_to_job(db, cfg)

        self.assertIsNone(result)
        db.save_job.assert_not_called()

    def test_migrate_missing_output(self):
        """Test that missing output is handled"""
        db = Mock()
        cfg = {
            "gateway": {"url": "http://localhost/db"},
        }

        result = migrate_legacy_config_to_job(db, cfg)

        self.assertIsNone(result)
        db.save_job.assert_not_called()

    def test_migrate_error_handling(self):
        """Test error handling during migration"""
        db = Mock()
        db.save_job.side_effect = Exception("DB error")

        cfg = {
            "gateway": {"url": "http://localhost/db"},
            "output": {"mode": "http"},
        }

        result = migrate_legacy_config_to_job(db, cfg)

        self.assertIsNone(result)


class TestCheckpointJobIsolation(unittest.TestCase):
    """Test that checkpoints are properly isolated by job_id"""

    def test_checkpoint_receives_job_id(self):
        """Test that job_id is passed to Checkpoint constructor"""
        from main import Checkpoint

        # Create a checkpoint with job_id
        cfg = {"enabled": True}
        gw = {"url": "http://localhost/db", "database": "db"}
        channels = ["ch1"]
        job_id = "test_job_123"

        checkpoint = Checkpoint(cfg, gw, channels, job_id=job_id)

        # Check that the checkpoint stores the job_id
        self.assertEqual(checkpoint._job_id, job_id)

    def test_checkpoint_without_job_id(self):
        """Test that checkpoint works without job_id (backward compat)"""
        from main import Checkpoint

        cfg = {"enabled": True}
        gw = {"url": "http://localhost/db", "database": "db"}
        channels = ["ch1"]

        checkpoint = Checkpoint(cfg, gw, channels)

        # Should default to None
        self.assertIsNone(checkpoint._job_id)


if __name__ == "__main__":
    unittest.main()
