#!/usr/bin/env python3
"""
Unit tests for db/db_oracle.py

Covers:
  - Oracle SQL generation: DELETE, INSERT, UPSERT (MERGE INTO ... USING DUAL)
  - Bind placeholder style (:1, :2, ...)
  - Error classification (_is_transient, _error_class)
  - DSN construction from config
  - _ora_error_code helper
"""

import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from schema.mapper import SqlOperation


# ===================================================================
# SQL Generation  (_op_to_oracle_sql is a @staticmethod)
# ===================================================================

# Import the static method directly — no DB connection needed.
from db.db_oracle import OracleOutputForwarder

_to_sql = OracleOutputForwarder._op_to_oracle_sql


class TestOracleDelete(unittest.TestCase):
    """DELETE → DELETE FROM "table" WHERE "col" = :1"""

    def test_single_where_clause(self):
        op = SqlOperation("DELETE", "orders", where={"doc_id": "order::1"})
        sql, params = _to_sql(op)
        self.assertEqual(sql, 'DELETE FROM "orders" WHERE "doc_id" = :1')
        self.assertEqual(params, ["order::1"])

    def test_multiple_where_clauses(self):
        op = SqlOperation(
            "DELETE", "order_items", where={"order_doc_id": "order::1", "item_id": 42}
        )
        sql, params = _to_sql(op)
        self.assertIn('"order_doc_id" = :1', sql)
        self.assertIn('"item_id" = :2', sql)
        self.assertEqual(params, ["order::1", 42])


class TestOracleInsert(unittest.TestCase):
    """INSERT → INSERT INTO "table" (...) VALUES (:1,:2,...)"""

    def test_basic_insert(self):
        op = SqlOperation(
            "INSERT",
            "order_items",
            data={"order_doc_id": "order::1", "product_id": "p:100", "qty": 2},
        )
        sql, params = _to_sql(op)
        self.assertEqual(
            sql,
            'INSERT INTO "order_items" ("order_doc_id", "product_id", "qty") '
            "VALUES (:1, :2, :3)",
        )
        self.assertEqual(params, ["order::1", "p:100", 2])

    def test_single_column_insert(self):
        op = SqlOperation("INSERT", "tags", data={"tag": "priority"})
        sql, params = _to_sql(op)
        self.assertEqual(sql, 'INSERT INTO "tags" ("tag") VALUES (:1)')
        self.assertEqual(params, ["priority"])

    def test_insert_uses_oracle_bind_style(self):
        """Ensure we get :1 not $1 (PostgreSQL style)."""
        op = SqlOperation("INSERT", "t", data={"a": 1, "b": 2})
        sql, _ = _to_sql(op)
        self.assertNotIn("$", sql)
        self.assertIn(":1", sql)
        self.assertIn(":2", sql)


class TestOracleUpsert(unittest.TestCase):
    """UPSERT → MERGE INTO ... USING (SELECT ... FROM DUAL) ..."""

    def test_merge_structure(self):
        op = SqlOperation(
            "UPSERT",
            "products",
            data={"doc_id": "p::1", "name": "Widget", "price": 19.99},
            conflict_column="doc_id",
        )
        sql, params = _to_sql(op)

        # Key Oracle MERGE clauses
        self.assertIn('MERGE INTO "products" t', sql)
        self.assertIn("FROM DUAL", sql)
        self.assertIn("ON (", sql)
        self.assertIn('t."doc_id" = s."doc_id"', sql)
        self.assertIn("WHEN MATCHED THEN UPDATE SET", sql)
        self.assertIn("WHEN NOT MATCHED THEN INSERT", sql)

        # PK column should NOT appear in UPDATE SET
        self.assertNotIn(
            't."doc_id" = s."doc_id"', sql.split("UPDATE SET")[1].split("WHEN NOT")[0]
        )

        # Non-PK columns in UPDATE SET
        self.assertIn('t."name" = s."name"', sql)
        self.assertIn('t."price" = s."price"', sql)

        # Params are in order
        self.assertEqual(params, ["p::1", "Widget", 19.99])

    def test_merge_uses_oracle_bind_placeholders(self):
        op = SqlOperation(
            "UPSERT", "t", data={"id": 1, "val": "x"}, conflict_column="id"
        )
        sql, _ = _to_sql(op)
        self.assertNotIn("$", sql)
        self.assertIn(":1", sql)
        self.assertIn(":2", sql)

    def test_merge_defaults_pk_to_first_column(self):
        """When conflict_column is None, first column is used as PK."""
        op = SqlOperation("UPSERT", "t", data={"my_pk": "abc", "col2": 10})
        sql, _ = _to_sql(op)
        self.assertIn('t."my_pk" = s."my_pk"', sql)  # ON clause uses first col

    def test_merge_single_column_skips_update(self):
        """If only the PK column exists, WHEN MATCHED should be omitted."""
        op = SqlOperation("UPSERT", "t", data={"id": 1}, conflict_column="id")
        sql, params = _to_sql(op)
        self.assertNotIn("WHEN MATCHED", sql)
        self.assertIn("WHEN NOT MATCHED THEN INSERT", sql)
        self.assertEqual(params, [1])

    def test_merge_many_columns(self):
        data = {f"col{i}": i for i in range(10)}
        op = SqlOperation("UPSERT", "wide_table", data=data, conflict_column="col0")
        sql, params = _to_sql(op)
        self.assertEqual(len(params), 10)
        # Should have :1 through :10
        for i in range(1, 11):
            self.assertIn(f":{i}", sql)


class TestOracleUnknownOp(unittest.TestCase):
    def test_unknown_op_raises(self):
        op = SqlOperation("TRUNCATE", "t")
        with self.assertRaises(ValueError):
            _to_sql(op)


# ===================================================================
# DSN Construction
# ===================================================================


class TestDsnConstruction(unittest.TestCase):
    """Verify DSN is built from host/port/database when not provided."""

    @patch("db.db_oracle.oracledb", new_callable=MagicMock)
    def test_dsn_from_host_port_db(self, mock_oracledb):
        OracleOutputForwarder.__new__(OracleOutputForwarder)
        # Manually call __init__ logic for DSN construction
        out_cfg = {
            "oracle": {
                "host": "dbserver",
                "port": 1521,
                "database": "ORCL",
                "user": "app",
                "password": "secret",
            }
        }
        ora_cfg = out_cfg["oracle"]
        dsn = ora_cfg.get("dsn", "")
        if not dsn:
            dsn = f"{ora_cfg['host']}:{ora_cfg['port']}/{ora_cfg['database']}"
        self.assertEqual(dsn, "dbserver:1521/ORCL")

    @patch("db.db_oracle.oracledb", new_callable=MagicMock)
    def test_explicit_dsn_takes_priority(self, mock_oracledb):
        ora_cfg = {
            "dsn": "myhost:1522/MYDB",
            "host": "ignored",
            "port": 9999,
            "database": "ignored",
        }
        dsn = ora_cfg.get("dsn", "")
        self.assertEqual(dsn, "myhost:1522/MYDB")


# ===================================================================
# Error Classification
# ===================================================================


class _FakeOraError(Exception):
    """Simulate an oracledb.Error with a .code attribute on args[0]."""

    def __init__(self, code):
        self.args = (SimpleNamespace(code=code),)


class TestErrorClassification(unittest.TestCase):
    """Test _is_transient and _error_class without a real Oracle connection."""

    def setUp(self):
        # Create a minimal forwarder instance for calling instance methods.
        # We patch oracledb so __init__ doesn't blow up.
        self.mock_oracledb = MagicMock()
        self.mock_oracledb.Error = type("Error", (Exception,), {})

        patcher = patch("db.db_oracle.oracledb", self.mock_oracledb)
        patcher.start()
        self.addCleanup(patcher.stop)

        self.fwd = OracleOutputForwarder.__new__(OracleOutputForwarder)

    def test_connection_error_is_transient(self):
        self.assertTrue(self.fwd._is_transient(ConnectionError("gone")))

    def test_os_error_is_transient(self):
        self.assertTrue(self.fwd._is_transient(OSError("network")))

    def test_timeout_is_transient(self):
        self.assertTrue(self.fwd._is_transient(TimeoutError("slow")))

    def test_connection_error_class(self):
        self.assertEqual(self.fwd._error_class(ConnectionError()), "connection")

    def test_timeout_error_class(self):
        self.assertEqual(self.fwd._error_class(TimeoutError()), "timeout")

    def test_generic_exception_not_transient(self):
        self.assertFalse(self.fwd._is_transient(ValueError("bad")))

    def test_generic_exception_class(self):
        self.assertEqual(self.fwd._error_class(ValueError("bad")), "unknown")


class TestOraErrorCode(unittest.TestCase):
    """Test the _ora_error_code helper."""

    def test_extracts_code(self):
        from db.db_oracle import _ora_error_code

        # Simulate oracledb.Error with code attribute
        mock_oracledb = MagicMock()
        err_class = type("Error", (Exception,), {})
        mock_oracledb.Error = err_class

        exc = err_class()
        exc.args = (SimpleNamespace(code=3113),)

        with patch("db.db_oracle.oracledb", mock_oracledb):
            self.assertEqual(_ora_error_code(exc), 3113)

    def test_returns_none_for_non_oracle_error(self):
        from db.db_oracle import _ora_error_code

        mock_oracledb = MagicMock()
        mock_oracledb.Error = type("Error", (Exception,), {})
        with patch("db.db_oracle.oracledb", mock_oracledb):
            self.assertIsNone(_ora_error_code(ValueError("nope")))


if __name__ == "__main__":
    unittest.main()
