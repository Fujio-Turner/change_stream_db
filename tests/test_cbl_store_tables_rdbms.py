#!/usr/bin/env python3
"""
Unit tests for cbl_store.py tables_rdbms methods.

Covers:
  - load/save tables_rdbms
  - get/upsert/delete individual table entries
  - get_tables_rdbms_used_by (reverse lookup)
"""

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


class TestTablesRdbms(TestCBLStoreBase):
    """Tests for tables_rdbms CRUD operations."""

    def setUp(self):
        """Set up test fixtures."""
        super().setUp()
        self.store = CBLStore()

    def test_load_tables_rdbms_not_found(self):
        """Test loading tables_rdbms when document doesn't exist."""
        with patch.object(cbl_store, "_coll_get_doc", return_value=None):
            result = self.store.load_tables_rdbms()
            self.assertIsNone(result)

    def test_save_tables_rdbms_creates_new(self):
        """Test saving tables_rdbms creates new document."""
        data = {"tables": [SAMPLE_TABLE]}
        with (
            patch.object(cbl_store, "_coll_get_mutable_doc", return_value=None),
            patch.object(cbl_store, "_coll_save_doc") as mock_save,
        ):
            self.store.save_tables_rdbms(data)
            mock_save.assert_called_once()

    def test_save_tables_rdbms_updates_existing(self):
        """Test saving tables_rdbms updates existing document."""
        mock_doc = MagicMock()
        data = {"tables": [SAMPLE_TABLE]}
        with (
            patch.object(cbl_store, "_coll_get_mutable_doc", return_value=mock_doc),
            patch.object(cbl_store, "_coll_save_doc") as mock_save,
        ):
            self.store.save_tables_rdbms(data)
            mock_save.assert_called_once()

    def test_load_tables_rdbms_returns_data(self):
        """Test loading tables_rdbms returns dict with type and tables."""
        mock_doc = MagicMock()
        mock_doc.get = lambda k, default=None: {
            "type": "tables_rdbms",
            "tables": [SAMPLE_TABLE],
        }.get(k, default)
        mock_doc.__contains__ = lambda self_doc, key: key in {
            "type",
            "tables",
        }
        with patch.object(cbl_store, "_coll_get_doc", return_value=mock_doc):
            result = self.store.load_tables_rdbms()
            self.assertIsNotNone(result)
            self.assertEqual(result["type"], "tables_rdbms")
            self.assertEqual(len(result["tables"]), 1)
            self.assertEqual(result["tables"][0]["id"], "tbl-orders")

    def test_get_table_rdbms_found(self):
        """Test get_table_rdbms finds a table by ID."""
        doc = {"type": "tables_rdbms", "tables": [SAMPLE_TABLE, SAMPLE_TABLE_2]}
        with patch.object(self.store, "load_tables_rdbms", return_value=doc):
            result = self.store.get_table_rdbms("tbl-orders")
            self.assertIsNotNone(result)
            self.assertEqual(result["id"], "tbl-orders")
            self.assertEqual(result["name"], "orders")

    def test_get_table_rdbms_not_found(self):
        """Test get_table_rdbms returns None for missing ID."""
        doc = {"type": "tables_rdbms", "tables": [SAMPLE_TABLE]}
        with patch.object(self.store, "load_tables_rdbms", return_value=doc):
            result = self.store.get_table_rdbms("tbl-nonexistent")
            self.assertIsNone(result)

    def test_upsert_table_rdbms_add_new(self):
        """Test upsert_table_rdbms adds a new table entry."""
        doc = {"type": "tables_rdbms", "tables": [SAMPLE_TABLE]}
        with (
            patch.object(self.store, "load_tables_rdbms", return_value=doc),
            patch.object(self.store, "save_tables_rdbms") as mock_save,
        ):
            self.store.upsert_table_rdbms(SAMPLE_TABLE_2)
            mock_save.assert_called_once()
            saved_data = mock_save.call_args[0][0]
            self.assertEqual(len(saved_data["tables"]), 2)
            self.assertEqual(saved_data["tables"][1]["id"], "tbl-users")

    def test_upsert_table_rdbms_update_existing(self):
        """Test upsert_table_rdbms updates existing entry by ID."""
        doc = {"type": "tables_rdbms", "tables": [SAMPLE_TABLE]}
        updated_entry = {**SAMPLE_TABLE, "name": "orders_v2"}
        with (
            patch.object(self.store, "load_tables_rdbms", return_value=doc),
            patch.object(self.store, "save_tables_rdbms") as mock_save,
        ):
            self.store.upsert_table_rdbms(updated_entry)
            mock_save.assert_called_once()
            saved_data = mock_save.call_args[0][0]
            self.assertEqual(len(saved_data["tables"]), 1)
            self.assertEqual(saved_data["tables"][0]["name"], "orders_v2")

    def test_upsert_table_rdbms_no_id_raises(self):
        """Test upsert_table_rdbms raises ValueError when id missing."""
        with self.assertRaises(ValueError):
            self.store.upsert_table_rdbms({"name": "no_id_table"})

    def test_delete_table_rdbms_found(self):
        """Test delete_table_rdbms removes entry, returns True."""
        doc = {"type": "tables_rdbms", "tables": [SAMPLE_TABLE, SAMPLE_TABLE_2]}
        with (
            patch.object(self.store, "load_tables_rdbms", return_value=doc),
            patch.object(self.store, "save_tables_rdbms") as mock_save,
        ):
            result = self.store.delete_table_rdbms("tbl-orders")
            self.assertTrue(result)
            mock_save.assert_called_once()
            saved_data = mock_save.call_args[0][0]
            self.assertEqual(len(saved_data["tables"]), 1)
            self.assertEqual(saved_data["tables"][0]["id"], "tbl-users")

    def test_delete_table_rdbms_not_found(self):
        """Test delete_table_rdbms returns False when ID doesn't exist."""
        doc = {"type": "tables_rdbms", "tables": [SAMPLE_TABLE]}
        with patch.object(self.store, "load_tables_rdbms", return_value=doc):
            result = self.store.delete_table_rdbms("tbl-nonexistent")
            self.assertFalse(result)

    def test_get_tables_rdbms_used_by(self):
        """Test get_tables_rdbms_used_by returns jobs that reference a table by library_ref."""
        job_list = [{"id": "job-1", "doc_id": "job::job-1"}]
        full_job = {
            "id": "job-1",
            "name": "Orders Pipeline",
            "outputs": [
                {
                    "id": "pg-prod",
                    "tables": [
                        {"name": "orders", "library_ref": "tbl-orders"},
                    ],
                }
            ],
        }
        with (
            patch.object(self.store, "list_jobs", return_value=job_list),
            patch.object(self.store, "load_job", return_value=full_job),
        ):
            result = self.store.get_tables_rdbms_used_by("tbl-orders")
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0]["job_id"], "job-1")
            self.assertEqual(result[0]["job_name"], "Orders Pipeline")
            self.assertEqual(result[0]["table_name"], "orders")


if __name__ == "__main__":
    unittest.main()
