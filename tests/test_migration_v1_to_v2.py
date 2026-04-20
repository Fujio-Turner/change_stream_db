#!/usr/bin/env python3
"""
Integration tests for v1.x → v2.0 schema migration.

Tests the migrate_v1_to_v2() function which transforms v1.x config documents
into v2.0 structure (inputs_changes, outputs_{type}, jobs, checkpoints).
"""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

# Ensure the module under test is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import cbl_store
from cbl_store import CBLStore

# Mock the CBL module and internal CFFI objects for testing without actual CBL dependency
cbl_mock = MagicMock()
ffi_mock = MagicMock()
lib_mock = MagicMock()
cbl_mock._PyCBL.ffi = ffi_mock
cbl_mock._PyCBL.lib = lib_mock
sys.modules["CouchbaseLite"] = cbl_mock
sys.modules["CouchbaseLite._PyCBL"] = MagicMock(ffi=ffi_mock, lib=lib_mock)


class TestMigrationV1toV2Base(unittest.TestCase):
    """Base class for migration tests with mocked CBL storage."""

    def setUp(self):
        """Set up mocks for CBL storage."""
        self.temp_dir = tempfile.mkdtemp()
        cbl_store.configure_cbl(db_dir=self.temp_dir, db_name="test_db")
        self.mock_db = MagicMock()

        # Patch get_db
        self.db_patcher = patch.object(cbl_store, "get_db", return_value=self.mock_db)
        self.db_patcher.start()

        # Patch _ensure_dlq_indexes
        self.dlq_patcher = patch.object(cbl_store, "_ensure_dlq_indexes")
        self.dlq_patcher.start()

        # Inject CFFI mocks
        cbl_store.lib = lib_mock
        cbl_store.ffi = ffi_mock
        cbl_store.stringParam = MagicMock()
        cbl_store._cbl_gError = MagicMock()
        cbl_store.MutableDocument = MagicMock(side_effect=lambda doc_id: MagicMock())

        self.store = CBLStore()

    def tearDown(self):
        """Clean up mocks."""
        self.db_patcher.stop()
        self.dlq_patcher.stop()
        import shutil

        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)


class TestMigrationSkipConditions(TestMigrationV1toV2Base):
    """Test conditions where migration should be skipped."""

    def test_migration_skipped_no_config(self):
        """Migration should return False if no config exists."""
        with patch.object(self.store, "load_config", return_value=None):
            result = self.store.migrate_v1_to_v2()
            self.assertFalse(result)

    def test_migration_skipped_already_v2(self):
        """Migration should return False if config already has schema_version 2.0."""
        config = {"schema_version": "2.0"}
        with patch.object(self.store, "load_config", return_value=config):
            result = self.store.migrate_v1_to_v2()
            self.assertFalse(result)

    def test_migration_skipped_non_v1_config(self):
        """Migration should return False if config doesn't have v1.x fields."""
        config = {"some_other_field": "value"}
        with patch.object(self.store, "load_config", return_value=config):
            with patch.object(self.store, "save_config") as mock_save:
                result = self.store.migrate_v1_to_v2()
                self.assertFalse(result)
                # Should mark as v2.0
                mock_save.assert_called_once()
                call_args = mock_save.call_args[0][0]
                self.assertEqual(call_args.get("schema_version"), "2.0")


class TestMigrationInputs(TestMigrationV1toV2Base):
    """Test extraction of v1.x inputs → v2.0 inputs_changes."""

    def test_migration_extracts_gateway(self):
        """Migration should extract gateway → inputs_changes."""
        v1_config = {
            "gateway": {
                "src": "sync_gateway",
                "url": "http://localhost:4984",
                "database": "mydb",
                "scope": "us",
                "collection": "orders",
                "accept_self_signed_certs": False,
            },
            "auth": {
                "method": "basic",
                "username": "bob",
                "password": "password",
                "session_cookie": "",
                "bearer_token": "",
            },
            "changes_feed": {
                "feed_type": "longpoll",
                "poll_interval_seconds": 10,
                "active_only": True,
                "include_docs": False,
                "since": "0",
            },
        }

        with (
            patch.object(self.store, "load_config", return_value=v1_config),
            patch.object(self.store, "list_mappings", return_value=[]),
            patch.object(self.store, "save_inputs_changes") as mock_save_inputs,
            patch.object(self.store, "save_outputs"),
            patch.object(self.store, "save_job"),
            patch.object(self.store, "save_checkpoint"),
            patch.object(self.store, "save_config"),
        ):
            result = self.store.migrate_v1_to_v2()
            self.assertTrue(result)
            # Verify inputs_changes was saved
            mock_save_inputs.assert_called_once()
            call_args = mock_save_inputs.call_args[0][0]
            self.assertIn("src", call_args)
            self.assertEqual(len(call_args["src"]), 1)
            src = call_args["src"][0]
            self.assertEqual(src["host"], "http://localhost:4984")
            self.assertEqual(src["database"], "mydb")
            self.assertEqual(src["collection"], "orders")
            self.assertEqual(src["auth"]["username"], "bob")
            self.assertEqual(src["changes_feed"]["feed_type"], "longpoll")


class TestMigrationOutputs(TestMigrationV1toV2Base):
    """Test extraction of v1.x output → v2.0 outputs_{type}."""

    def test_migration_extracts_postgres_output(self):
        """Migration should extract PostgreSQL output → outputs_rdbms."""
        v1_config = {
            "gateway": {"src": "sync_gateway", "url": "http://localhost:4984"},
            "auth": {"method": "basic"},
            "changes_feed": {"feed_type": "longpoll"},
            "output": {
                "mode": "postgres",
                "postgres": {
                    "host": "localhost",
                    "port": 5432,
                    "database": "myapp",
                    "user": "postgres",
                    "password": "secret",
                    "schema": "public",
                    "ssl": False,
                    "pool_min": 2,
                    "pool_max": 10,
                },
            },
        }

        with (
            patch.object(self.store, "load_config", return_value=v1_config),
            patch.object(self.store, "list_mappings", return_value=[]),
            patch.object(self.store, "save_inputs_changes"),
            patch.object(self.store, "save_outputs") as mock_save_outputs,
            patch.object(self.store, "save_job"),
            patch.object(self.store, "save_checkpoint"),
            patch.object(self.store, "save_config"),
        ):
            result = self.store.migrate_v1_to_v2()
            self.assertTrue(result)
            # Verify outputs_rdbms was saved
            mock_save_outputs.assert_called_once()
            call_args = mock_save_outputs.call_args
            self.assertEqual(call_args[0][0], "rdbms")  # output_type
            data = call_args[0][1]
            self.assertEqual(data["type"], "outputs_rdbms")
            self.assertEqual(len(data["src"]), 1)
            output = data["src"][0]
            self.assertEqual(output["engine"], "postgres")
            self.assertEqual(output["host"], "localhost")
            self.assertEqual(output["port"], 5432)
            self.assertEqual(output["user"], "postgres")

    def test_migration_extracts_http_output(self):
        """Migration should extract HTTP output → outputs_http."""
        v1_config = {
            "gateway": {"src": "sync_gateway", "url": "http://localhost:4984"},
            "auth": {"method": "none"},
            "changes_feed": {"feed_type": "longpoll"},
            "output": {
                "mode": "http",
                "target_url": "https://api.example.com/events",
                "url_template": "{target_url}/{doc_id}",
                "write_method": "POST",
                "delete_method": "DELETE",
            },
        }

        with (
            patch.object(self.store, "load_config", return_value=v1_config),
            patch.object(self.store, "list_mappings", return_value=[]),
            patch.object(self.store, "save_inputs_changes"),
            patch.object(self.store, "save_outputs") as mock_save_outputs,
            patch.object(self.store, "save_job"),
            patch.object(self.store, "save_checkpoint"),
            patch.object(self.store, "save_config"),
        ):
            result = self.store.migrate_v1_to_v2()
            self.assertTrue(result)
            # Verify outputs_http was saved
            mock_save_outputs.assert_called_once()
            call_args = mock_save_outputs.call_args
            self.assertEqual(call_args[0][0], "http")
            data = call_args[0][1]
            self.assertEqual(data["type"], "outputs_http")
            output = data["src"][0]
            self.assertEqual(output["target_url"], "https://api.example.com/events")
            self.assertEqual(output["write_method"], "POST")


class TestMigrationJob(TestMigrationV1toV2Base):
    """Test creation of job document from v1.x config."""

    def test_migration_creates_job(self):
        """Migration should create a job document."""
        v1_config = {
            "gateway": {"src": "sync_gateway", "url": "http://localhost:4984"},
            "auth": {"method": "basic"},
            "changes_feed": {"feed_type": "longpoll"},
            "output": {"mode": "stdout"},
            "threads": 4,
            "processing": {"max_concurrent": 20},
            "retry": {"max_retries": 5},
            "attachments": {"enabled": False},
        }

        with (
            patch.object(self.store, "load_config", return_value=v1_config),
            patch.object(self.store, "list_mappings", return_value=[]),
            patch.object(self.store, "save_inputs_changes"),
            patch.object(self.store, "save_outputs"),
            patch.object(self.store, "save_job") as mock_save_job,
            patch.object(self.store, "save_checkpoint"),
            patch.object(self.store, "save_config"),
        ):
            result = self.store.migrate_v1_to_v2()
            self.assertTrue(result)
            # Verify job was saved
            mock_save_job.assert_called_once()
            job_id = mock_save_job.call_args[0][0]
            job_data = mock_save_job.call_args[0][1]
            self.assertIsNotNone(job_id)
            self.assertEqual(job_data["id"], job_id)  # Check ID instead of type
            self.assertEqual(job_data["system"]["threads"], 4)
            self.assertEqual(job_data["system"]["processing"]["max_concurrent"], 20)
            self.assertEqual(job_data["state"]["status"], "stopped")


class TestMigrationCheckpoint(TestMigrationV1toV2Base):
    """Test migration of checkpoint file."""

    def test_migration_creates_checkpoint_from_file(self):
        """Migration should create checkpoint from checkpoint.json if it exists."""
        from pathlib import Path

        v1_config = {
            "gateway": {"src": "sync_gateway", "url": "http://localhost:4984"},
            "auth": {"method": "basic"},
            "changes_feed": {"feed_type": "longpoll"},
            "output": {"mode": "stdout"},
        }

        # Create a temporary checkpoint file
        cp_data = {"SGs_Seq": "123", "remote_counter": 50}

        with (
            patch("pathlib.Path") as mock_path_class,
            patch.object(self.store, "load_config", return_value=v1_config),
            patch.object(self.store, "list_mappings", return_value=[]),
            patch.object(self.store, "save_inputs_changes"),
            patch.object(self.store, "save_outputs"),
            patch.object(self.store, "save_job"),
            patch.object(self.store, "save_checkpoint") as mock_save_checkpoint,
            patch.object(self.store, "save_config"),
        ):
            # Mock Path to return a checkpoint file that exists
            mock_path_instance = MagicMock()
            mock_path_instance.exists.return_value = True
            mock_path_instance.read_text.return_value = json.dumps(cp_data)
            mock_path_class.return_value = mock_path_instance

            result = self.store.migrate_v1_to_v2()
            self.assertTrue(result)
            # Verify checkpoint was saved
            mock_save_checkpoint.assert_called_once()
            call_args = mock_save_checkpoint.call_args[0]
            checkpoint_data = call_args[1]
            self.assertEqual(checkpoint_data["last_seq"], "123")
            self.assertEqual(checkpoint_data["remote_counter"], 50)


class TestMigrationIdempotency(TestMigrationV1toV2Base):
    """Test that migration is idempotent (can be run multiple times safely)."""

    def test_migration_marks_as_v2(self):
        """Migration should mark config as v2.0 to prevent re-migration."""
        v1_config = {
            "gateway": {"src": "sync_gateway", "url": "http://localhost:4984"},
            "auth": {"method": "basic"},
            "changes_feed": {"feed_type": "longpoll"},
            "output": {"mode": "stdout"},
        }

        with (
            patch.object(self.store, "load_config", return_value=v1_config),
            patch.object(self.store, "list_mappings", return_value=[]),
            patch.object(self.store, "save_inputs_changes"),
            patch.object(self.store, "save_outputs"),
            patch.object(self.store, "save_job"),
            patch.object(self.store, "save_checkpoint"),
            patch.object(self.store, "save_config") as mock_save_config,
        ):
            result = self.store.migrate_v1_to_v2()
            self.assertTrue(result)
            # Verify config was saved with schema_version
            mock_save_config.assert_called_once()
            saved_config = mock_save_config.call_args[0][0]
            self.assertEqual(saved_config["schema_version"], "2.0")

    def test_migration_second_run_skips(self):
        """Second migration run should skip if schema_version is 2.0."""
        # After first migration, config should have schema_version
        migrated_config = {
            "schema_version": "2.0",
            "admin_ui": {},
            "metrics": {},
        }

        with patch.object(self.store, "load_config", return_value=migrated_config):
            result = self.store.migrate_v1_to_v2()
            self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
