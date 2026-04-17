#!/usr/bin/env python3
"""
Unit tests for db/db_mysql.py

Covers:
  - MySQL SQL generation: DELETE, INSERT, UPSERT (ON DUPLICATE KEY UPDATE)
  - Bind placeholder style (%s)
  - Back-tick quoting for identifiers
  - Error classification (_is_transient, _error_class)
"""

import os
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from schema.mapper import SqlOperation
from db.db_mysql import MySQLOutputForwarder

_to_sql = MySQLOutputForwarder._op_to_mysql_sql


# ===================================================================
# SQL Generation
# ===================================================================


class TestMySQLDelete(unittest.TestCase):
    """DELETE → DELETE FROM `table` WHERE `col` = %s"""

    def test_single_where_clause(self):
        op = SqlOperation("DELETE", "orders", where={"doc_id": "order::1"})
        sql, params = _to_sql(op)
        self.assertEqual(sql, "DELETE FROM `orders` WHERE `doc_id` = %s")
        self.assertEqual(params, ["order::1"])

    def test_multiple_where_clauses(self):
        op = SqlOperation(
            "DELETE", "order_items", where={"order_doc_id": "order::1", "item_id": 42}
        )
        sql, params = _to_sql(op)
        self.assertIn("`order_doc_id` = %s", sql)
        self.assertIn("`item_id` = %s", sql)
        self.assertEqual(params, ["order::1", 42])


class TestMySQLInsert(unittest.TestCase):
    """INSERT → INSERT INTO `table` (...) VALUES (%s,%s,...)"""

    def test_basic_insert(self):
        op = SqlOperation(
            "INSERT",
            "order_items",
            data={"order_doc_id": "order::1", "product_id": "p:100", "qty": 2},
        )
        sql, params = _to_sql(op)
        self.assertEqual(
            sql,
            "INSERT INTO `order_items` (`order_doc_id`, `product_id`, `qty`) "
            "VALUES (%s, %s, %s)",
        )
        self.assertEqual(params, ["order::1", "p:100", 2])

    def test_single_column_insert(self):
        op = SqlOperation("INSERT", "tags", data={"tag": "priority"})
        sql, params = _to_sql(op)
        self.assertEqual(sql, "INSERT INTO `tags` (`tag`) VALUES (%s)")
        self.assertEqual(params, ["priority"])

    def test_uses_percent_s_not_dollar(self):
        op = SqlOperation("INSERT", "t", data={"a": 1, "b": 2})
        sql, _ = _to_sql(op)
        self.assertNotIn("$", sql)
        self.assertNotIn(":", sql)
        self.assertIn("%s", sql)


class TestMySQLUpsert(unittest.TestCase):
    """UPSERT → INSERT INTO ... ON DUPLICATE KEY UPDATE ..."""

    def test_on_duplicate_key_structure(self):
        op = SqlOperation(
            "UPSERT",
            "products",
            data={"doc_id": "p::1", "name": "Widget", "price": 19.99},
            conflict_column="doc_id",
        )
        sql, params = _to_sql(op)

        self.assertIn("INSERT INTO `products`", sql)
        self.assertIn("ON DUPLICATE KEY UPDATE", sql)
        # PK column should NOT appear in UPDATE
        self.assertNotIn("`doc_id` = VALUES(`doc_id`)", sql)
        # Non-PK columns in UPDATE
        self.assertIn("`name` = VALUES(`name`)", sql)
        self.assertIn("`price` = VALUES(`price`)", sql)
        self.assertEqual(params, ["p::1", "Widget", 19.99])

    def test_uses_backtick_quoting(self):
        op = SqlOperation(
            "UPSERT", "t", data={"id": 1, "val": "x"}, conflict_column="id"
        )
        sql, _ = _to_sql(op)
        self.assertIn("`id`", sql)
        self.assertIn("`val`", sql)
        # No double-quotes (postgres) or brackets (mssql)
        self.assertNotIn('"', sql)
        self.assertNotIn("[", sql)

    def test_defaults_pk_to_first_column(self):
        op = SqlOperation("UPSERT", "t", data={"my_pk": "abc", "col2": 10})
        sql, _ = _to_sql(op)
        # ON DUPLICATE KEY UPDATE should not include the first column
        self.assertNotIn("`my_pk` = VALUES(`my_pk`)", sql)
        self.assertIn("`col2` = VALUES(`col2`)", sql)

    def test_single_column_upsert_no_update(self):
        """If only the PK column exists, no ON DUPLICATE KEY UPDATE clause."""
        op = SqlOperation("UPSERT", "t", data={"id": 1}, conflict_column="id")
        sql, params = _to_sql(op)
        self.assertNotIn("ON DUPLICATE KEY UPDATE", sql)
        self.assertEqual(params, [1])

    def test_many_columns(self):
        data = {f"col{i}": i for i in range(10)}
        op = SqlOperation("UPSERT", "wide_table", data=data, conflict_column="col0")
        sql, params = _to_sql(op)
        self.assertEqual(len(params), 10)
        self.assertEqual(sql.count("%s"), 10)


class TestMySQLUnknownOp(unittest.TestCase):
    def test_unknown_op_raises(self):
        op = SqlOperation("TRUNCATE", "t")
        with self.assertRaises(ValueError):
            _to_sql(op)


# ===================================================================
# Error Classification
# ===================================================================


class TestMySQLErrorClassification(unittest.TestCase):
    def setUp(self):
        self.mock_aiomysql = MagicMock()
        self.mock_aiomysql.Error = type("Error", (Exception,), {})

        patcher = patch("db.db_mysql.aiomysql", self.mock_aiomysql)
        patcher.start()
        self.addCleanup(patcher.stop)

        self.fwd = MySQLOutputForwarder.__new__(MySQLOutputForwarder)

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
