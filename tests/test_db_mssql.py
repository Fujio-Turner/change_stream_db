#!/usr/bin/env python3
"""
Unit tests for db/db_mssql.py

Covers:
  - MSSQL SQL generation: DELETE, INSERT, UPSERT (MERGE)
  - Bind placeholder style (?)
  - Bracket quoting for identifiers ([col])
  - Error classification (_is_transient, _error_class)
  - DSN construction
  - _mssql_error_code helper
"""

import os
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from schema.mapper import SqlOperation
from db.db_mssql import MSSQLOutputForwarder, _mssql_error_code

_to_sql = MSSQLOutputForwarder._op_to_mssql_sql


# ===================================================================
# SQL Generation
# ===================================================================


class TestMSSQLDelete(unittest.TestCase):
    """DELETE → DELETE FROM [table] WHERE [col] = ?"""

    def test_single_where_clause(self):
        op = SqlOperation("DELETE", "orders", where={"doc_id": "order::1"})
        sql, params = _to_sql(op)
        self.assertEqual(sql, "DELETE FROM [orders] WHERE [doc_id] = ?")
        self.assertEqual(params, ["order::1"])

    def test_multiple_where_clauses(self):
        op = SqlOperation(
            "DELETE", "order_items", where={"order_doc_id": "order::1", "item_id": 42}
        )
        sql, params = _to_sql(op)
        self.assertIn("[order_doc_id] = ?", sql)
        self.assertIn("[item_id] = ?", sql)
        self.assertEqual(params, ["order::1", 42])


class TestMSSQLInsert(unittest.TestCase):
    """INSERT → INSERT INTO [table] (...) VALUES (?,?,...)"""

    def test_basic_insert(self):
        op = SqlOperation(
            "INSERT",
            "order_items",
            data={"order_doc_id": "order::1", "product_id": "p:100", "qty": 2},
        )
        sql, params = _to_sql(op)
        self.assertEqual(
            sql,
            "INSERT INTO [order_items] ([order_doc_id], [product_id], [qty]) "
            "VALUES (?, ?, ?)",
        )
        self.assertEqual(params, ["order::1", "p:100", 2])

    def test_single_column_insert(self):
        op = SqlOperation("INSERT", "tags", data={"tag": "priority"})
        sql, params = _to_sql(op)
        self.assertEqual(sql, "INSERT INTO [tags] ([tag]) VALUES (?)")
        self.assertEqual(params, ["priority"])

    def test_uses_question_mark_placeholders(self):
        op = SqlOperation("INSERT", "t", data={"a": 1, "b": 2})
        sql, _ = _to_sql(op)
        self.assertNotIn("$", sql)
        self.assertNotIn(":", sql)
        self.assertNotIn("%s", sql)
        self.assertEqual(sql.count("?"), 2)


class TestMSSQLUpsert(unittest.TestCase):
    """UPSERT → MERGE [table] AS t USING (VALUES ...) AS s(...) ON ... ;"""

    def test_merge_structure(self):
        op = SqlOperation(
            "UPSERT",
            "products",
            data={"doc_id": "p::1", "name": "Widget", "price": 19.99},
            conflict_column="doc_id",
        )
        sql, params = _to_sql(op)

        self.assertIn("MERGE [products] AS t", sql)
        self.assertIn("USING (VALUES", sql)
        self.assertIn("ON (", sql)
        self.assertIn("t.[doc_id] = s.[doc_id]", sql)
        self.assertIn("WHEN MATCHED THEN UPDATE SET", sql)
        self.assertIn("WHEN NOT MATCHED THEN INSERT", sql)
        # Ends with semicolon (required for MERGE)
        self.assertTrue(sql.rstrip().endswith(";"))

        # PK column NOT in UPDATE SET
        update_part = sql.split("UPDATE SET")[1].split("WHEN NOT")[0]
        self.assertNotIn("t.[doc_id] = s.[doc_id]", update_part)

        # Non-PK columns in UPDATE SET
        self.assertIn("t.[name] = s.[name]", sql)
        self.assertIn("t.[price] = s.[price]", sql)

        self.assertEqual(params, ["p::1", "Widget", 19.99])

    def test_uses_bracket_quoting(self):
        op = SqlOperation(
            "UPSERT", "t", data={"id": 1, "val": "x"}, conflict_column="id"
        )
        sql, _ = _to_sql(op)
        self.assertIn("[id]", sql)
        self.assertIn("[val]", sql)
        # No double-quotes (postgres) or back-ticks (mysql)
        self.assertNotIn('"', sql)
        self.assertNotIn("`", sql)

    def test_defaults_pk_to_first_column(self):
        op = SqlOperation("UPSERT", "t", data={"my_pk": "abc", "col2": 10})
        sql, _ = _to_sql(op)
        self.assertIn("t.[my_pk] = s.[my_pk]", sql)  # ON clause

    def test_single_column_skips_update(self):
        """If only the PK column, WHEN MATCHED should be omitted."""
        op = SqlOperation("UPSERT", "t", data={"id": 1}, conflict_column="id")
        sql, params = _to_sql(op)
        self.assertNotIn("WHEN MATCHED", sql)
        self.assertIn("WHEN NOT MATCHED THEN INSERT", sql)
        self.assertEqual(params, [1])

    def test_many_columns(self):
        data = {f"col{i}": i for i in range(10)}
        op = SqlOperation("UPSERT", "wide_table", data=data, conflict_column="col0")
        sql, params = _to_sql(op)
        self.assertEqual(len(params), 10)
        self.assertEqual(sql.count("?"), 10)


class TestMSSQLUnknownOp(unittest.TestCase):
    def test_unknown_op_raises(self):
        op = SqlOperation("TRUNCATE", "t")
        with self.assertRaises(ValueError):
            _to_sql(op)


# ===================================================================
# DSN Construction
# ===================================================================


class TestDsnConstruction(unittest.TestCase):
    @patch("db.db_mssql.aioodbc", new_callable=MagicMock)
    def test_dsn_components(self, mock_aioodbc):
        fwd = MSSQLOutputForwarder.__new__(MSSQLOutputForwarder)
        fwd._host = "dbserver"
        fwd._port = 1433
        fwd._database = "mydb"
        fwd._user = "sa"
        fwd._password = "secret"
        fwd._driver = "ODBC Driver 18 for SQL Server"
        fwd._trust_cert = True
        dsn = fwd._build_dsn()

        self.assertIn("SERVER=dbserver,1433", dsn)
        self.assertIn("DATABASE=mydb", dsn)
        self.assertIn("UID=sa", dsn)
        self.assertIn("TrustServerCertificate=yes", dsn)

    @patch("db.db_mssql.aioodbc", new_callable=MagicMock)
    def test_dsn_without_trust_cert(self, mock_aioodbc):
        fwd = MSSQLOutputForwarder.__new__(MSSQLOutputForwarder)
        fwd._host = "db"
        fwd._port = 1433
        fwd._database = "x"
        fwd._user = "u"
        fwd._password = "p"
        fwd._driver = "ODBC Driver 18 for SQL Server"
        fwd._trust_cert = False
        dsn = fwd._build_dsn()
        self.assertNotIn("TrustServerCertificate", dsn)


# ===================================================================
# Error Code Extraction
# ===================================================================


class TestMssqlErrorCode(unittest.TestCase):
    def test_extracts_code_from_string(self):
        exc = Exception("('HY000', '[HY000] Something failed (1205)')")
        self.assertEqual(_mssql_error_code(exc), 1205)

    def test_extracts_code_from_int_arg(self):
        exc = Exception(2003)
        self.assertEqual(_mssql_error_code(exc), 2003)

    def test_returns_none_for_no_code(self):
        exc = Exception("no code here")
        self.assertIsNone(_mssql_error_code(exc))


# ===================================================================
# Error Classification
# ===================================================================


class TestMSSQLErrorClassification(unittest.TestCase):
    def setUp(self):
        self.mock_aioodbc = MagicMock()
        patcher = patch("db.db_mssql.aioodbc", self.mock_aioodbc)
        patcher.start()
        self.addCleanup(patcher.stop)

        self.fwd = MSSQLOutputForwarder.__new__(MSSQLOutputForwarder)

    def test_connection_error_is_transient(self):
        self.assertTrue(self.fwd._is_transient(ConnectionError("gone")))

    def test_os_error_is_transient(self):
        self.assertTrue(self.fwd._is_transient(OSError("network")))

    def test_timeout_is_transient(self):
        self.assertTrue(self.fwd._is_transient(TimeoutError("slow")))

    def test_timeout_error_class(self):
        self.assertEqual(self.fwd._error_class(TimeoutError()), "timeout")

    def test_connection_error_class(self):
        self.assertEqual(self.fwd._error_class(ConnectionError()), "connection")

    def test_generic_exception_not_transient(self):
        self.assertFalse(self.fwd._is_transient(ValueError("bad")))

    def test_generic_exception_class(self):
        self.assertEqual(self.fwd._error_class(ValueError("bad")), "unknown")


if __name__ == "__main__":
    unittest.main()
