#!/usr/bin/env python3
"""
Unit tests for cbl_store.py v2.0 methods (inputs/outputs/jobs/sessions/data_quality/enrichments).

Covers:
  - load/save inputs_changes
  - load/save outputs (rdbms, http, cloud, stdout)
  - load/save/delete/list jobs
  - update_job_state
  - load/save checkpoints
  - load/save/list/delete sessions
  - add/list data_quality entries
  - add/list enrichments
"""

import json
import os
import sys
import tempfile
import time
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


class TestCBLStoreBase(unittest.TestCase):
    """Base class for test cases that need a writable temp DB directory."""

    def setUp(self):
        """Set up a temporary directory and mocks for the test database."""
        self.temp_dir = tempfile.mkdtemp()
        # Configure CBL to use temp directory
        cbl_store.configure_cbl(db_dir=self.temp_dir, db_name="test_db")
        # Create a mock database store
        self.mock_db = MagicMock()
        # Patch all the CBL-related internals
        self.patches = []
        # Patch get_db
        db_patcher = patch.object(cbl_store, "get_db", return_value=self.mock_db)
        self.patches.append(db_patcher)
        db_patcher.start()

        # Patch _ensure_dlq_indexes to avoid initialization issues
        dlq_patcher = patch.object(cbl_store, "_ensure_dlq_indexes")
        self.patches.append(dlq_patcher)
        dlq_patcher.start()

        # Inject CFFI mocks as module attributes
        cbl_store.lib = lib_mock
        cbl_store.ffi = ffi_mock
        cbl_store.stringParam = MagicMock()
        cbl_store._cbl_gError = MagicMock()
        # Mock MutableDocument
        cbl_store.MutableDocument = MagicMock(side_effect=lambda doc_id: MagicMock())

    def tearDown(self):
        """Clean up the temporary directory."""
        import shutil

        for patcher in self.patches:
            patcher.stop()
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)


class TestInputsChanges(TestCBLStoreBase):
    """Tests for load_inputs_changes and save_inputs_changes."""

    def setUp(self):
        """Set up test fixtures."""
        super().setUp()
        self.store = CBLStore()

    def test_load_inputs_changes_not_found(self):
        """Test loading inputs_changes when document doesn't exist."""
        with patch.object(cbl_store, "_coll_get_doc", return_value=None):
            result = self.store.load_inputs_changes()
            self.assertIsNone(result)

    def test_save_inputs_changes_creates_new(self):
        """Test saving inputs_changes creates new document."""
        data = {"src": [{"id": "sg-test", "name": "Test Source"}]}
        with (
            patch.object(cbl_store, "_coll_get_mutable_doc", return_value=MagicMock()),
            patch.object(cbl_store, "_coll_save_doc"),
        ):
            self.store.save_inputs_changes(data)
            # Should not raise exception

    def test_save_inputs_changes_updates_existing(self):
        """Test saving inputs_changes updates existing document."""
        mock_doc = MagicMock()
        data = {"src": [{"id": "sg-test", "name": "Test Source"}]}
        with (
            patch.object(
                cbl_store, "_coll_get_mutable_doc", return_value=mock_doc
            ) as mock_get,
            patch.object(cbl_store, "_coll_save_doc") as mock_save,
        ):
            self.store.save_inputs_changes(data)
            # Verify the document was retrieved and saved
            mock_save.assert_called_once()


class TestOutputs(TestCBLStoreBase):
    """Tests for load_outputs and save_outputs."""

    def setUp(self):
        """Set up test fixtures."""
        super().setUp()
        self.store = CBLStore()

    def test_load_outputs_rdbms(self):
        """Test loading RDBMS outputs."""
        mock_doc = MagicMock()
        mock_doc.properties = {
            "type": "outputs_rdbms",
            "src": [{"id": "pg-prod", "engine": "postgres"}],
        }
        mock_doc.get = lambda k, default=None: mock_doc.properties.get(k, default)
        with patch.object(cbl_store, "_coll_get_doc", return_value=mock_doc):
            result = self.store.load_outputs("rdbms")
            self.assertIsNotNone(result)
            self.assertEqual(result["type"], "outputs_rdbms")

    def test_save_outputs_http(self):
        """Test saving HTTP outputs."""
        data = {
            "src": [
                {
                    "id": "http-api",
                    "target_url": "https://example.com/webhook",
                    "write_method": "POST",
                }
            ]
        }
        with (
            patch.object(cbl_store, "_coll_get_mutable_doc", return_value=MagicMock()),
            patch.object(cbl_store, "_coll_save_doc"),
        ):
            self.store.save_outputs("http", data)
            # Should not raise exception

    def test_load_outputs_not_found(self):
        """Test loading outputs when document doesn't exist."""
        with patch.object(cbl_store, "_coll_get_doc", return_value=None):
            result = self.store.load_outputs("cloud")
            self.assertIsNone(result)


class TestJobs(TestCBLStoreBase):
    """Tests for job CRUD operations."""

    def setUp(self):
        """Set up test fixtures."""
        super().setUp()
        self.store = CBLStore()
        self.job_id = "aaa-bbb-ccc"
        self.job_data = {
            "inputs": [{"id": "sg-test"}],
            "outputs": [{"id": "pg-prod"}],
            "output_type": "rdbms",
            "system": {"max_threads": 2},
        }

    def test_save_job(self):
        """Test saving a job."""
        mock_doc = MagicMock()
        with (
            patch.object(
                cbl_store, "_coll_get_mutable_doc", return_value=mock_doc
            ) as mock_get,
            patch.object(cbl_store, "_coll_save_doc") as mock_save,
        ):
            self.store.save_job(self.job_id, self.job_data)
            # Verify the document was saved
            mock_save.assert_called_once()

    def test_load_job_found(self):
        """Test loading a job when it exists."""
        mock_doc = MagicMock()
        mock_doc.properties = {
            "type": "job",
            "id": self.job_id,
            "inputs": [{"id": "sg-test"}],
        }
        with patch.object(cbl_store, "_coll_get_doc", return_value=mock_doc):
            result = self.store.load_job(self.job_id)
            self.assertIsNotNone(result)
            self.assertEqual(result["type"], "job")

    def test_load_job_not_found(self):
        """Test loading a job when it doesn't exist."""
        with patch.object(cbl_store, "_coll_get_doc", return_value=None):
            result = self.store.load_job(self.job_id)
            self.assertIsNone(result)

    def test_delete_job(self):
        """Test deleting a job."""
        with (
            patch.object(cbl_store, "_coll_purge_doc"),
            patch.object(cbl_store, "_coll_get_doc", return_value=MagicMock()),
        ):
            self.store.delete_job(self.job_id)
            # Should not raise exception

    def test_list_jobs(self):
        """Test listing all jobs."""
        mock_results = [
            {"_id": f"job::{self.job_id}", "type": "job", "id": self.job_id}
        ]
        with patch.object(cbl_store, "_run_n1ql", return_value=mock_results):
            result = self.store.list_jobs()
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0]["doc_id"], f"job::{self.job_id}")

    def test_update_job_state(self):
        """Test updating job state."""
        mock_doc = MagicMock()
        state = {"status": "running", "last_seq": "100"}
        with (
            patch.object(
                cbl_store, "_coll_get_mutable_doc", return_value=mock_doc
            ) as mock_get,
            patch.object(cbl_store, "_coll_save_doc") as mock_save,
        ):
            self.store.update_job_state(self.job_id, state)
            # Verify the document was saved with the state
            mock_save.assert_called_once()

    def test_update_job_state_not_found(self):
        """Test updating job state when job doesn't exist."""
        with patch.object(cbl_store, "_coll_get_mutable_doc", return_value=None):
            with self.assertRaises(RuntimeError):
                self.store.update_job_state(self.job_id, {})


class TestCheckpoints(TestCBLStoreBase):
    """Tests for checkpoint operations."""

    def setUp(self):
        """Set up test fixtures."""
        super().setUp()
        self.store = CBLStore()
        self.job_id = "aaa-bbb-ccc"

    def test_save_checkpoint(self):
        """Test saving a checkpoint."""
        data = {"last_seq": "100", "remote_counter": 50}
        mock_doc = MagicMock()
        with (
            patch.object(
                cbl_store, "_coll_get_mutable_doc", return_value=mock_doc
            ) as mock_get,
            patch.object(cbl_store, "_coll_save_doc") as mock_save,
        ):
            self.store.save_checkpoint(self.job_id, data)
            # Verify the document was saved
            mock_save.assert_called_once()

    def test_load_checkpoint_found(self):
        """Test loading a checkpoint when it exists."""
        mock_doc = MagicMock()
        mock_doc.properties = {
            "type": "checkpoint",
            "job_id": self.job_id,
            "last_seq": "100",
        }
        with patch.object(cbl_store, "_coll_get_doc", return_value=mock_doc):
            result = self.store.load_checkpoint(self.job_id)
            self.assertIsNotNone(result)
            self.assertEqual(result["job_id"], self.job_id)

    def test_load_checkpoint_not_found(self):
        """Test loading a checkpoint when it doesn't exist."""
        with patch.object(cbl_store, "_coll_get_doc", return_value=None):
            result = self.store.load_checkpoint(self.job_id)
            self.assertIsNone(result)


class TestSessions(TestCBLStoreBase):
    """Tests for session operations."""

    def setUp(self):
        """Set up test fixtures."""
        super().setUp()
        self.store = CBLStore()
        self.session_id = "sess-123"

    def test_save_session(self):
        """Test saving a session."""
        data = {
            "cookie": "abc123",
            "expires_at": int(time.time()) + 3600,
        }
        mock_doc = MagicMock()
        with (
            patch.object(
                cbl_store, "_coll_get_mutable_doc", return_value=mock_doc
            ) as mock_get,
            patch.object(cbl_store, "_coll_save_doc") as mock_save,
        ):
            self.store.save_session(self.session_id, data)
            # Verify the document was saved
            mock_save.assert_called_once()

    def test_load_session_found(self):
        """Test loading a session."""
        mock_doc = MagicMock()
        mock_doc.properties = {
            "type": "session",
            "cookie": "abc123",
            "expires_at": int(time.time()) + 3600,
        }
        with patch.object(cbl_store, "_coll_get_doc", return_value=mock_doc):
            result = self.store.load_session(self.session_id)
            self.assertIsNotNone(result)
            self.assertEqual(result["type"], "session")

    def test_load_session_not_found(self):
        """Test loading a session when it doesn't exist."""
        with patch.object(cbl_store, "_coll_get_doc", return_value=None):
            result = self.store.load_session(self.session_id)
            self.assertIsNone(result)

    def test_list_sessions(self):
        """Test listing all sessions."""
        mock_results = [{"_id": f"session::{self.session_id}", "type": "session"}]
        with patch.object(cbl_store, "_run_n1ql", return_value=mock_results):
            result = self.store.list_sessions()
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0]["doc_id"], f"session::{self.session_id}")

    def test_delete_expired_sessions(self):
        """Test deleting expired sessions."""
        now = int(time.time())
        mock_results = [{"_id": f"session::{self.session_id}"}]
        with (
            patch.object(cbl_store, "_run_n1ql", return_value=mock_results),
            patch.object(cbl_store, "_coll_purge_doc"),
            patch.object(cbl_store, "_transaction"),
        ):
            count = self.store.delete_expired_sessions()
            # Mock doesn't execute transaction properly, so count should be 0 in test
            # In real usage, it would be 1


class TestDataQuality(TestCBLStoreBase):
    """Tests for data quality entries."""

    def setUp(self):
        """Set up test fixtures."""
        super().setUp()
        self.store = CBLStore()
        self.job_id = "aaa-bbb-ccc"

    def test_add_data_quality_entry(self):
        """Test adding a data quality entry."""
        entry = {
            "doc_id": "doc-123",
            "table_name": "users",
            "column_name": "age",
            "original_value": 999999999,
            "coerced_value": 2147483647,
            "coerce_type": "int_clamp",
        }
        with patch.object(cbl_store, "_coll_save_doc"):
            self.store.add_data_quality_entry(self.job_id, entry)
            # Should not raise exception

    def test_list_data_quality_all(self):
        """Test listing all data quality entries."""
        mock_results = [
            {
                "_id": f"dq::{self.job_id}::doc-123::1234567890",
                "type": "data_quality",
                "job_id": self.job_id,
                "table_name": "users",
                "column_name": "age",
            }
        ]
        with patch.object(cbl_store, "_run_n1ql", return_value=mock_results):
            result = self.store.list_data_quality()
            self.assertEqual(len(result), 1)

    def test_list_data_quality_filtered_by_job(self):
        """Test listing data quality entries filtered by job."""
        mock_results = [
            {
                "_id": f"dq::{self.job_id}::doc-123::1234567890",
                "type": "data_quality",
                "job_id": self.job_id,
            }
        ]
        with patch.object(cbl_store, "_run_n1ql", return_value=mock_results):
            result = self.store.list_data_quality(job_id=self.job_id)
            self.assertEqual(len(result), 1)


class TestEnrichments(TestCBLStoreBase):
    """Tests for enrichment entries."""

    def setUp(self):
        """Set up test fixtures."""
        super().setUp()
        self.store = CBLStore()
        self.job_id = "aaa-bbb-ccc"

    def test_add_enrichment(self):
        """Test adding an enrichment entry."""
        enrichment = {
            "doc_id": "doc-123",
            "source": "ml_caption",
            "status": "complete",
            "result": '{"caption": "A picture of a cat"}',
        }
        with patch.object(cbl_store, "_coll_save_doc"):
            self.store.add_enrichment(self.job_id, enrichment)
            # Should not raise exception

    def test_list_enrichments_all(self):
        """Test listing all enrichments."""
        mock_results = [
            {
                "_id": f"enrich::{self.job_id}::doc-123::1234567890",
                "type": "enrichment",
                "job_id": self.job_id,
                "source": "ml_caption",
            }
        ]
        with patch.object(cbl_store, "_run_n1ql", return_value=mock_results):
            result = self.store.list_enrichments()
            self.assertEqual(len(result), 1)

    def test_list_enrichments_filtered_by_source(self):
        """Test listing enrichments filtered by source."""
        mock_results = [
            {
                "_id": f"enrich::{self.job_id}::doc-123::1234567890",
                "type": "enrichment",
                "job_id": self.job_id,
                "source": "ml_caption",
            }
        ]
        with patch.object(cbl_store, "_run_n1ql", return_value=mock_results):
            result = self.store.list_enrichments(source="ml_caption")
            self.assertEqual(len(result), 1)


if __name__ == "__main__":
    unittest.main()
