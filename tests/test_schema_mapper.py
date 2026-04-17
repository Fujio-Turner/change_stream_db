#!/usr/bin/env python3
"""
Unit tests for schema/mapper.py

Covers:
  - resolve_path (basic paths, $, missing keys, non-dict intermediates)
  - apply_transform (trim, ltrim, rtrim, uppercase, lowercase, to_int,
    to_float, to_decimal, to_string, coalesce, to_date, json_stringify,
    unrecognised, malformed)
  - resolve_column (string path, dict with transform, dict without transform)
  - SqlOperation.to_sql (DELETE, INSERT, UPSERT, unknown op_type)
  - SqlOperation.__repr__
  - SchemaMapper.matches (field match, no match, empty match)
  - map_document upsert (parent + child tables, source_array expansion)
  - map_document delete (children-first ordering, on_delete="ignore")
  - delete_insert replace strategy
  - from_file class method
  - array child item-then-parent resolution
"""

import json
import os
import sys
import tempfile
import unittest
from decimal import Decimal
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from schema.mapper import (
    resolve_path,
    apply_transform,
    resolve_column,
    SqlOperation,
    SchemaMapper,
)


# ---------------------------------------------------------------------------
# resolve_path
# ---------------------------------------------------------------------------

class TestResolvePath(unittest.TestCase):
    """Tests for resolve_path()."""

    def test_dollar_returns_doc_itself(self):
        doc = {"a": 1}
        self.assertIs(resolve_path(doc, "$"), doc)

    def test_simple_key(self):
        self.assertEqual(resolve_path({"name": "Alice"}, "$.name"), "Alice")

    def test_nested_key(self):
        doc = {"address": {"city": "NYC"}}
        self.assertEqual(resolve_path(doc, "$.address.city"), "NYC")

    def test_missing_key_returns_none(self):
        self.assertIsNone(resolve_path({"a": 1}, "$.b"))

    def test_missing_intermediate_returns_none(self):
        self.assertIsNone(resolve_path({"a": 1}, "$.a.b"))

    def test_non_dict_intermediate_returns_none(self):
        self.assertIsNone(resolve_path({"a": "hello"}, "$.a.b"))

    def test_path_without_dollar_prefix_returns_none(self):
        self.assertIsNone(resolve_path({"a": 1}, "a"))

    def test_path_dollar_dot_only(self):
        # "$.x" with a single key
        self.assertEqual(resolve_path({"x": 42}, "$.x"), 42)


# ---------------------------------------------------------------------------
# apply_transform
# ---------------------------------------------------------------------------

class TestApplyTransform(unittest.TestCase):
    """Tests for apply_transform()."""

    def test_trim(self):
        self.assertEqual(apply_transform("  hi  ", "trim()"), "hi")

    def test_trim_none(self):
        self.assertIsNone(apply_transform(None, "trim()"))

    def test_ltrim(self):
        self.assertEqual(apply_transform("  hi  ", "ltrim()"), "hi  ")

    def test_ltrim_none(self):
        self.assertIsNone(apply_transform(None, "ltrim()"))

    def test_rtrim(self):
        self.assertEqual(apply_transform("  hi  ", "rtrim()"), "  hi")

    def test_rtrim_none(self):
        self.assertIsNone(apply_transform(None, "rtrim()"))

    def test_uppercase(self):
        self.assertEqual(apply_transform("hello", "uppercase()"), "HELLO")

    def test_uppercase_none(self):
        self.assertIsNone(apply_transform(None, "uppercase()"))

    def test_lowercase(self):
        self.assertEqual(apply_transform("HELLO", "lowercase()"), "hello")

    def test_lowercase_none(self):
        self.assertIsNone(apply_transform(None, "lowercase()"))

    def test_to_int(self):
        self.assertEqual(apply_transform("42", "to_int()"), 42)

    def test_to_int_invalid(self):
        self.assertIsNone(apply_transform("abc", "to_int()"))

    def test_to_float(self):
        self.assertAlmostEqual(apply_transform("3.14", "to_float()"), 3.14)

    def test_to_float_invalid(self):
        self.assertIsNone(apply_transform("abc", "to_float()"))

    def test_to_decimal_no_precision(self):
        result = apply_transform("12.345", "to_decimal()")
        self.assertEqual(result, Decimal("12.345"))

    def test_to_decimal_with_precision(self):
        result = apply_transform("12.345", "to_decimal($.total,2)")
        # Python's round() uses banker's rounding
        self.assertEqual(result, Decimal("12.34"))

    def test_to_decimal_invalid(self):
        self.assertIsNone(apply_transform("abc", "to_decimal()"))

    def test_to_string(self):
        self.assertEqual(apply_transform(42, "to_string()"), "42")

    def test_to_string_none(self):
        self.assertIsNone(apply_transform(None, "to_string()"))

    def test_coalesce_non_none(self):
        self.assertEqual(apply_transform("val", "coalesce(,default)"), "val")

    def test_coalesce_none_with_default(self):
        self.assertEqual(apply_transform(None, "coalesce(,fallback)"), "fallback")

    def test_coalesce_none_no_default(self):
        self.assertIsNone(apply_transform(None, "coalesce()"))

    def test_to_date_valid(self):
        result = apply_transform("2024-03-15", "to_date()")
        self.assertEqual(result, date(2024, 3, 15))

    def test_to_date_none(self):
        self.assertIsNone(apply_transform(None, "to_date()"))

    def test_to_date_invalid(self):
        self.assertIsNone(apply_transform("not-a-date", "to_date()"))

    def test_json_stringify(self):
        self.assertEqual(apply_transform({"a": 1}, "json_stringify()"), '{"a": 1}')

    def test_json_stringify_none(self):
        self.assertIsNone(apply_transform(None, "json_stringify()"))

    def test_unrecognised_transform_returns_value(self):
        self.assertEqual(apply_transform("val", "unknown_func()"), "val")

    def test_malformed_transform_returns_value(self):
        self.assertEqual(apply_transform("val", "not a transform"), "val")


# ---------------------------------------------------------------------------
# resolve_column
# ---------------------------------------------------------------------------

class TestResolveColumn(unittest.TestCase):
    """Tests for resolve_column()."""

    def test_string_path(self):
        doc = {"name": "Alice"}
        self.assertEqual(resolve_column(doc, "$.name"), "Alice")

    def test_dict_with_path_only(self):
        doc = {"status": "open"}
        self.assertEqual(resolve_column(doc, {"path": "$.status"}), "open")

    def test_dict_with_transform(self):
        doc = {"name": "alice"}
        result = resolve_column(doc, {"path": "$.name", "transform": "uppercase()"})
        self.assertEqual(result, "ALICE")

    def test_dict_no_path_defaults_to_dollar(self):
        doc = "scalar_value"
        self.assertEqual(resolve_column(doc, {}), "scalar_value")


# ---------------------------------------------------------------------------
# SqlOperation.to_sql
# ---------------------------------------------------------------------------

class TestSqlOperationToSql(unittest.TestCase):
    """Tests for SqlOperation.to_sql()."""

    def test_delete(self):
        op = SqlOperation("DELETE", "orders", where={"doc_id": "abc"})
        sql, params = op.to_sql()
        self.assertIn("DELETE FROM", sql)
        self.assertIn('"orders"', sql)
        self.assertIn('"doc_id" = $1', sql)
        self.assertEqual(params, ["abc"])

    def test_delete_multiple_where(self):
        op = SqlOperation("DELETE", "t", where={"a": 1, "b": 2})
        sql, params = op.to_sql()
        self.assertIn("AND", sql)
        self.assertEqual(len(params), 2)

    def test_insert(self):
        op = SqlOperation("INSERT", "items", data={"id": 1, "name": "Widget"})
        sql, params = op.to_sql()
        self.assertIn("INSERT INTO", sql)
        self.assertIn('"items"', sql)
        self.assertIn("$1", sql)
        self.assertIn("$2", sql)
        self.assertEqual(params, [1, "Widget"])

    def test_upsert(self):
        op = SqlOperation(
            "UPSERT", "orders",
            data={"doc_id": "abc", "status": "shipped"},
            conflict_column="doc_id",
        )
        sql, params = op.to_sql()
        self.assertIn("INSERT INTO", sql)
        self.assertIn("ON CONFLICT", sql)
        self.assertIn('"doc_id"', sql)
        self.assertIn("DO UPDATE SET", sql)
        self.assertIn('EXCLUDED."status"', sql)
        self.assertEqual(params, ["abc", "shipped"])

    def test_upsert_defaults_to_first_column(self):
        op = SqlOperation("UPSERT", "t", data={"pk": 1, "val": "x"})
        sql, _ = op.to_sql()
        self.assertIn('ON CONFLICT ("pk")', sql)

    def test_unknown_op_type_raises(self):
        op = SqlOperation("MERGE", "t", data={"a": 1})
        with self.assertRaises(ValueError) as ctx:
            op.to_sql()
        self.assertIn("MERGE", str(ctx.exception))


# ---------------------------------------------------------------------------
# SqlOperation.__repr__
# ---------------------------------------------------------------------------

class TestSqlOperationRepr(unittest.TestCase):
    """Tests for SqlOperation.__repr__()."""

    def test_repr_basic(self):
        op = SqlOperation("DELETE", "orders", where={"id": 1})
        r = repr(op)
        self.assertIn("SqlOperation(", r)
        self.assertIn("'DELETE'", r)
        self.assertIn("'orders'", r)
        self.assertIn("where=", r)

    def test_repr_with_data_and_conflict(self):
        op = SqlOperation("UPSERT", "t", data={"a": 1}, conflict_column="a")
        r = repr(op)
        self.assertIn("data=", r)
        self.assertIn("conflict_column=", r)

    def test_repr_minimal(self):
        op = SqlOperation("INSERT", "t")
        r = repr(op)
        self.assertNotIn("data=", r)
        self.assertNotIn("where=", r)


# ---------------------------------------------------------------------------
# SchemaMapper.matches
# ---------------------------------------------------------------------------

class TestSchemaMapperMatches(unittest.TestCase):
    """Tests for SchemaMapper.matches()."""

    def test_matches_field_value(self):
        m = SchemaMapper({"source": {"match": {"field": "type", "value": "order"}}})
        self.assertTrue(m.matches({"type": "order"}))
        self.assertFalse(m.matches({"type": "user"}))

    def test_matches_no_match_block(self):
        m = SchemaMapper({"source": {}})
        self.assertTrue(m.matches({"any": "doc"}))

    def test_matches_empty_match(self):
        m = SchemaMapper({"source": {"match": {}}})
        self.assertTrue(m.matches({"any": "doc"}))

    def test_matches_no_field_key(self):
        m = SchemaMapper({"source": {"match": {"value": "x"}}})
        self.assertTrue(m.matches({"any": "doc"}))


# ---------------------------------------------------------------------------
# SchemaMapper.map_document – upsert
# ---------------------------------------------------------------------------

_MAPPING_PARENT_CHILD = {
    "source": {"match": {"field": "type", "value": "order"}},
    "tables": [
        {
            "name": "orders",
            "primary_key": "doc_id",
            "columns": {
                "doc_id": "$._id",
                "status": "$.status",
                "total": {"path": "$.total", "transform": "to_decimal($.total,2)"},
            },
        },
        {
            "name": "order_items",
            "parent": "orders",
            "foreign_key": {"column": "order_doc_id", "references": "doc_id"},
            "source_array": "$.items",
            "replace_strategy": "delete_insert",
            "columns": {
                "order_doc_id": "$._id",
                "product_id": {"path": "$.product_id", "transform": "uppercase($.product_id)"},
                "qty": "$.qty",
                "price": "$.price",
            },
        },
    ],
}


class TestMapDocumentUpsert(unittest.TestCase):
    """Tests for map_document with is_delete=False (upsert path)."""

    def _sample_doc(self):
        return {
            "_id": "order::1",
            "type": "order",
            "status": "pending",
            "total": "99.999",
            "items": [
                {"product_id": "widget-a", "qty": 2, "price": 10.0},
                {"product_id": "widget-b", "qty": 1, "price": 79.99},
            ],
        }

    def test_upsert_parent_first_then_children(self):
        mapper = SchemaMapper(_MAPPING_PARENT_CHILD)
        ops = mapper.map_document(self._sample_doc())

        # First op should be parent UPSERT
        self.assertEqual(ops[0].op_type, "UPSERT")
        self.assertEqual(ops[0].table, "orders")
        self.assertEqual(ops[0].data["doc_id"], "order::1")
        self.assertEqual(ops[0].data["status"], "pending")
        self.assertEqual(ops[0].data["total"], Decimal("100.00"))
        self.assertEqual(ops[0].conflict_column, "doc_id")

    def test_upsert_delete_insert_strategy(self):
        mapper = SchemaMapper(_MAPPING_PARENT_CHILD)
        ops = mapper.map_document(self._sample_doc())

        # Second op: DELETE for child rows (delete_insert strategy)
        self.assertEqual(ops[1].op_type, "DELETE")
        self.assertEqual(ops[1].table, "order_items")
        self.assertEqual(ops[1].where, {"order_doc_id": "order::1"})

    def test_upsert_source_array_expansion(self):
        mapper = SchemaMapper(_MAPPING_PARENT_CHILD)
        ops = mapper.map_document(self._sample_doc())

        # ops[2] and ops[3] are INSERTs for child items
        self.assertEqual(ops[2].op_type, "INSERT")
        self.assertEqual(ops[2].table, "order_items")
        self.assertEqual(ops[2].data["order_doc_id"], "order::1")
        self.assertEqual(ops[2].data["product_id"], "WIDGET-A")
        self.assertEqual(ops[2].data["qty"], 2)

        self.assertEqual(ops[3].op_type, "INSERT")
        self.assertEqual(ops[3].data["product_id"], "WIDGET-B")
        self.assertEqual(ops[3].data["qty"], 1)

    def test_upsert_total_ops_count(self):
        mapper = SchemaMapper(_MAPPING_PARENT_CHILD)
        ops = mapper.map_document(self._sample_doc())
        # 1 parent UPSERT + 1 child DELETE + 2 child INSERTs = 4
        self.assertEqual(len(ops), 4)

    def test_upsert_source_array_missing_yields_empty(self):
        mapper = SchemaMapper(_MAPPING_PARENT_CHILD)
        doc = {"_id": "order::2", "type": "order", "status": "draft", "total": "0"}
        # No "items" key → should still produce parent UPSERT + child DELETE + no INSERTs
        ops = mapper.map_document(doc)
        self.assertEqual(ops[0].op_type, "UPSERT")
        self.assertEqual(ops[1].op_type, "DELETE")
        self.assertEqual(len(ops), 2)

    def test_upsert_source_array_not_a_list_yields_empty(self):
        mapper = SchemaMapper(_MAPPING_PARENT_CHILD)
        doc = {"_id": "order::3", "type": "order", "status": "bad", "total": "0", "items": "oops"}
        ops = mapper.map_document(doc)
        # parent UPSERT + child DELETE + 0 inserts
        self.assertEqual(len(ops), 2)


# ---------------------------------------------------------------------------
# SchemaMapper.map_document – delete
# ---------------------------------------------------------------------------

class TestMapDocumentDelete(unittest.TestCase):
    """Tests for map_document with is_delete=True."""

    def test_delete_children_before_parents(self):
        mapper = SchemaMapper(_MAPPING_PARENT_CHILD)
        doc = {"_id": "order::1", "status": "pending"}
        ops = mapper.map_document(doc, is_delete=True)

        # First op: child DELETE
        self.assertEqual(ops[0].op_type, "DELETE")
        self.assertEqual(ops[0].table, "order_items")
        self.assertEqual(ops[0].where, {"order_doc_id": "order::1"})

        # Second op: parent DELETE
        self.assertEqual(ops[1].op_type, "DELETE")
        self.assertEqual(ops[1].table, "orders")
        self.assertEqual(ops[1].where, {"doc_id": "order::1"})

    def test_delete_on_delete_ignore(self):
        mapping = {
            "source": {},
            "tables": [
                {
                    "name": "parent_tbl",
                    "primary_key": "id",
                    "columns": {"id": "$.id"},
                },
                {
                    "name": "child_tbl",
                    "parent": "parent_tbl",
                    "on_delete": "ignore",
                    "foreign_key": {"column": "parent_id", "references": "id"},
                    "columns": {"parent_id": "$.id", "val": "$.val"},
                },
            ],
        }
        mapper = SchemaMapper(mapping)
        doc = {"id": "abc"}
        ops = mapper.map_document(doc, is_delete=True)

        # Child table with on_delete="ignore" should be skipped
        self.assertEqual(len(ops), 1)
        self.assertEqual(ops[0].table, "parent_tbl")

    def test_delete_parent_on_delete_ignore(self):
        mapping = {
            "source": {},
            "tables": [
                {
                    "name": "parent_tbl",
                    "primary_key": "id",
                    "columns": {"id": "$.id"},
                    "on_delete": "ignore",
                },
            ],
        }
        mapper = SchemaMapper(mapping)
        doc = {"id": "abc"}
        ops = mapper.map_document(doc, is_delete=True)
        self.assertEqual(len(ops), 0)

    def test_delete_child_no_fk_uses_pk(self):
        mapping = {
            "source": {},
            "tables": [
                {"name": "parent", "primary_key": "id", "columns": {"id": "$.id"}},
                {
                    "name": "child",
                    "parent": "parent",
                    "primary_key": "cid",
                    "columns": {"cid": "$.child_id"},
                },
            ],
        }
        mapper = SchemaMapper(mapping)
        doc = {"id": "p1", "child_id": "c1"}
        ops = mapper.map_document(doc, is_delete=True)
        child_op = ops[0]
        self.assertEqual(child_op.where, {"cid": "c1"})


# ---------------------------------------------------------------------------
# SchemaMapper – child item-then-parent resolution
# ---------------------------------------------------------------------------

class TestChildItemThenParentResolution(unittest.TestCase):
    """Column values resolve against the array item first, then fall back to
    the parent document (e.g. ``$._id`` referenced from a child row)."""

    def test_item_field_takes_precedence(self):
        mapping = {
            "source": {},
            "tables": [
                {"name": "p", "primary_key": "id", "columns": {"id": "$.id"}},
                {
                    "name": "c",
                    "parent": "p",
                    "source_array": "$.arr",
                    "foreign_key": {"column": "pid", "references": "id"},
                    "columns": {
                        "pid": "$.id",
                        "val": "$.val",
                    },
                },
            ],
        }
        mapper = SchemaMapper(mapping)
        doc = {"id": "parent_id", "arr": [{"id": "item_id", "val": "v1"}]}
        ops = mapper.map_document(doc)
        # The child INSERT should resolve $.id from the item ("item_id"),
        # not the parent ("parent_id")
        child_insert = [o for o in ops if o.op_type == "INSERT"][0]
        self.assertEqual(child_insert.data["pid"], "item_id")
        self.assertEqual(child_insert.data["val"], "v1")

    def test_fallback_to_parent(self):
        mapping = {
            "source": {},
            "tables": [
                {"name": "p", "primary_key": "id", "columns": {"id": "$._id"}},
                {
                    "name": "c",
                    "parent": "p",
                    "source_array": "$.arr",
                    "foreign_key": {"column": "pid", "references": "id"},
                    "columns": {
                        "pid": "$._id",
                        "val": "$.val",
                    },
                },
            ],
        }
        mapper = SchemaMapper(mapping)
        doc = {"_id": "parent_id", "arr": [{"val": "v1"}]}
        ops = mapper.map_document(doc)
        child_insert = [o for o in ops if o.op_type == "INSERT"][0]
        # Item has no _id → falls back to parent's _id
        self.assertEqual(child_insert.data["pid"], "parent_id")
        self.assertEqual(child_insert.data["val"], "v1")


# ---------------------------------------------------------------------------
# SchemaMapper.from_file
# ---------------------------------------------------------------------------

class TestSchemaMapperFromFile(unittest.TestCase):
    """Tests for SchemaMapper.from_file()."""

    def test_from_file_loads_mapping(self):
        mapping = {
            "source": {"match": {"field": "type", "value": "test"}},
            "tables": [
                {"name": "t", "primary_key": "id", "columns": {"id": "$.id"}},
            ],
        }
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump(mapping, f)
            f.flush()
            path = f.name

        try:
            mapper = SchemaMapper.from_file(path)
            self.assertTrue(mapper.matches({"type": "test"}))
            self.assertFalse(mapper.matches({"type": "other"}))
            self.assertEqual(len(mapper.tables), 1)
        finally:
            os.unlink(path)

    def test_from_file_with_real_mapping(self):
        mapping_path = os.path.join(
            os.path.dirname(__file__), "..", "mappings", "order.json"
        )
        if not os.path.exists(mapping_path):
            self.skipTest("mappings/order.json not found")
        mapper = SchemaMapper.from_file(mapping_path)
        self.assertTrue(mapper.matches({"type": "order"}))
        self.assertFalse(mapper.matches({"type": "user"}))
        self.assertEqual(len(mapper.tables), 2)


# ---------------------------------------------------------------------------
# Child table without source_array upserts like parent
# ---------------------------------------------------------------------------

class TestChildWithoutSourceArray(unittest.TestCase):
    """A child table with no source_array gets an UPSERT like a parent."""

    def test_child_upsert_without_source_array(self):
        mapping = {
            "source": {},
            "tables": [
                {"name": "p", "primary_key": "id", "columns": {"id": "$.id", "name": "$.name"}},
                {
                    "name": "c",
                    "parent": "p",
                    "primary_key": "id",
                    "foreign_key": {"column": "pid", "references": "id"},
                    "columns": {"id": "$.id", "pid": "$.id", "extra": "$.extra"},
                },
            ],
        }
        mapper = SchemaMapper(mapping)
        doc = {"id": "1", "name": "test", "extra": "e"}
        ops = mapper.map_document(doc)
        self.assertEqual(ops[0].op_type, "UPSERT")
        self.assertEqual(ops[0].table, "p")
        self.assertEqual(ops[1].op_type, "UPSERT")
        self.assertEqual(ops[1].table, "c")


if __name__ == "__main__":
    unittest.main()
