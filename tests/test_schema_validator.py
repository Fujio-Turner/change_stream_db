#!/usr/bin/env python3
"""
Unit tests for schema/validator.py

Covers:
  - validate_schema: empty tables, no name, invalid SQL identifier, duplicate
    names, missing primary_key warning, no columns, invalid column name, path
    not starting with $, valid parent+child, parent not found, missing FK
    column/references, FK references non-existent column, child with parent but
    no source_array, invalid on_delete, sample_doc path found/not-found,
    source_array item checks
  - validate_file: valid file, file not found, invalid JSON
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from schema.validator import validate_schema, validate_file


# ---------------------------------------------------------------------------
# Helper: minimal valid mapping
# ---------------------------------------------------------------------------

def _base_mapping(**overrides) -> dict:
    """Return a minimal valid mapping dict with one parent table."""
    m = {
        "tables": [
            {
                "name": "users",
                "primary_key": "id",
                "columns": {
                    "id": "$.id",
                    "name": "$.name",
                },
            }
        ]
    }
    m.update(overrides)
    return m


def _parent_child_mapping() -> dict:
    """Return a mapping with a valid parent + child relationship."""
    return {
        "tables": [
            {
                "name": "orders",
                "primary_key": "order_id",
                "columns": {
                    "order_id": "$.order_id",
                    "total": "$.total",
                },
            },
            {
                "name": "order_items",
                "parent": "orders",
                "source_array": "$.items",
                "foreign_key": {
                    "column": "order_id",
                    "references": "order_id",
                },
                "columns": {
                    "order_id": "$.order_id",
                    "product": "$.product",
                },
            },
        ]
    }


# ===================================================================
# validate_schema
# ===================================================================

class TestValidateSchemaBasicStructure(unittest.TestCase):
    """Tests for basic mapping structure checks."""

    def test_valid_mapping_no_errors(self):
        warnings, errors = validate_schema(_base_mapping())
        self.assertEqual(errors, [])

    def test_empty_tables(self):
        warnings, errors = validate_schema({"tables": []})
        self.assertTrue(any("No tables" in e for e in errors))

    def test_no_tables_key(self):
        warnings, errors = validate_schema({})
        self.assertTrue(any("No tables" in e for e in errors))

    def test_table_no_name(self):
        mapping = {"tables": [{"columns": {"id": "$.id"}}]}
        warnings, errors = validate_schema(mapping)
        self.assertTrue(any("index 0" in e and "no name" in e for e in errors))

    def test_invalid_sql_identifier(self):
        mapping = _base_mapping()
        mapping["tables"][0]["name"] = "1bad-name"
        warnings, errors = validate_schema(mapping)
        self.assertTrue(any("invalid SQL identifier" in e for e in errors))

    def test_duplicate_table_names(self):
        mapping = {
            "tables": [
                {"name": "tbl", "primary_key": "id", "columns": {"id": "$.id"}},
                {"name": "tbl", "primary_key": "id", "columns": {"id": "$.id"}},
            ]
        }
        warnings, errors = validate_schema(mapping)
        self.assertTrue(any("Duplicate" in e for e in errors))


class TestValidateSchemaColumns(unittest.TestCase):
    """Tests for column-level checks."""

    def test_no_columns(self):
        mapping = {"tables": [{"name": "empty", "primary_key": "id", "columns": {}}]}
        warnings, errors = validate_schema(mapping)
        self.assertTrue(any("no columns" in e for e in errors))

    def test_invalid_column_name(self):
        mapping = _base_mapping()
        mapping["tables"][0]["columns"]["bad col!"] = "$.x"
        warnings, errors = validate_schema(mapping)
        self.assertTrue(any("not a valid SQL identifier" in e for e in errors))

    def test_path_not_starting_with_dollar(self):
        mapping = _base_mapping()
        mapping["tables"][0]["columns"]["status"] = "status"
        warnings, errors = validate_schema(mapping)
        self.assertTrue(any("must start with '$'" in e for e in errors))

    def test_path_with_dict_col_def(self):
        mapping = _base_mapping()
        mapping["tables"][0]["columns"]["status"] = {"path": "no_dollar"}
        warnings, errors = validate_schema(mapping)
        self.assertTrue(any("must start with '$'" in e for e in errors))

    def test_valid_path_no_error(self):
        mapping = _base_mapping()
        mapping["tables"][0]["columns"]["email"] = "$.email"
        warnings, errors = validate_schema(mapping)
        self.assertEqual(errors, [])


class TestValidateSchemaPrimaryKey(unittest.TestCase):
    """Tests for primary_key warnings."""

    def test_missing_primary_key_warning(self):
        mapping = {"tables": [{"name": "logs", "columns": {"msg": "$.msg"}}]}
        warnings, errors = validate_schema(mapping)
        self.assertTrue(any("no primary_key" in w for w in warnings))

    def test_primary_key_present_no_warning(self):
        warnings, errors = validate_schema(_base_mapping())
        self.assertFalse(any("no primary_key" in w for w in warnings))


class TestValidateSchemaParentChild(unittest.TestCase):
    """Tests for parent/child and foreign key checks."""

    def test_valid_parent_child(self):
        warnings, errors = validate_schema(_parent_child_mapping())
        self.assertEqual(errors, [])
        self.assertFalse(any("source_array" in w for w in warnings))

    def test_parent_not_found(self):
        mapping = _parent_child_mapping()
        mapping["tables"][1]["parent"] = "nonexistent"
        warnings, errors = validate_schema(mapping)
        self.assertTrue(any("parent 'nonexistent' not found" in e for e in errors))

    def test_missing_fk_column(self):
        mapping = _parent_child_mapping()
        mapping["tables"][1]["foreign_key"] = {"references": "order_id"}
        warnings, errors = validate_schema(mapping)
        self.assertTrue(any("foreign_key.column" in e for e in errors))

    def test_missing_fk_references(self):
        mapping = _parent_child_mapping()
        mapping["tables"][1]["foreign_key"] = {"column": "order_id"}
        warnings, errors = validate_schema(mapping)
        self.assertTrue(any("foreign_key.references" in e for e in errors))

    def test_fk_references_nonexistent_parent_column(self):
        mapping = _parent_child_mapping()
        mapping["tables"][1]["foreign_key"]["references"] = "no_such_col"
        warnings, errors = validate_schema(mapping)
        self.assertTrue(any("not a column in parent" in e for e in errors))

    def test_child_with_parent_but_no_source_array(self):
        mapping = _parent_child_mapping()
        del mapping["tables"][1]["source_array"]
        warnings, errors = validate_schema(mapping)
        self.assertTrue(any("no source_array" in w for w in warnings))


class TestValidateSchemaOnDelete(unittest.TestCase):
    """Tests for on_delete validation."""

    def test_valid_on_delete_values(self):
        for value in ("delete", "ignore"):
            mapping = _base_mapping()
            mapping["tables"][0]["on_delete"] = value
            warnings, errors = validate_schema(mapping)
            self.assertEqual(errors, [], f"Unexpected errors for on_delete={value!r}")

    def test_invalid_on_delete(self):
        mapping = _base_mapping()
        mapping["tables"][0]["on_delete"] = "cascade"
        warnings, errors = validate_schema(mapping)
        self.assertTrue(any("on_delete" in e and "cascade" in e for e in errors))

    def test_default_on_delete_no_error(self):
        warnings, errors = validate_schema(_base_mapping())
        on_delete_errors = [e for e in errors if "on_delete" in e]
        self.assertEqual(on_delete_errors, [])


class TestValidateSchemaSampleDoc(unittest.TestCase):
    """Tests for sample_doc path resolution checks."""

    def test_sample_doc_path_found(self):
        sample = {"id": "1", "name": "Alice"}
        warnings, errors = validate_schema(_base_mapping(), sample_doc=sample)
        self.assertEqual(errors, [])
        path_warnings = [w for w in warnings if "not found in sample doc" in w]
        self.assertEqual(path_warnings, [])

    def test_sample_doc_path_not_found(self):
        sample = {"id": "1"}
        warnings, errors = validate_schema(_base_mapping(), sample_doc=sample)
        self.assertTrue(any("not found in sample doc" in w for w in warnings))

    def test_sample_doc_source_array_item_found(self):
        mapping = _parent_child_mapping()
        sample = {
            "order_id": "o1",
            "total": 100,
            "items": [{"product": "Widget", "order_id": "o1"}],
        }
        warnings, errors = validate_schema(mapping, sample_doc=sample)
        self.assertEqual(errors, [])

    def test_sample_doc_source_array_item_not_found(self):
        mapping = _parent_child_mapping()
        # child column "product" maps to "$.product" — not in parent doc or array item
        sample = {
            "order_id": "o1",
            "total": 100,
            "items": [{"sku": "W1"}],
        }
        warnings, errors = validate_schema(mapping, sample_doc=sample)
        self.assertTrue(any("not found in sample doc or array item" in w for w in warnings))


# ===================================================================
# validate_file
# ===================================================================

class TestValidateFile(unittest.TestCase):
    """Tests for validate_file()."""

    def test_valid_file(self):
        mapping = _base_mapping()
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump(mapping, f)
            f.flush()
            path = f.name
        try:
            warnings, errors = validate_file(path)
            self.assertEqual(errors, [])
        finally:
            os.unlink(path)

    def test_file_not_found(self):
        warnings, errors = validate_file("/tmp/nonexistent_mapping_12345.json")
        self.assertTrue(any("Cannot read mapping file" in e for e in errors))

    def test_invalid_json(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            f.write("{bad json!!!")
            f.flush()
            path = f.name
        try:
            warnings, errors = validate_file(path)
            self.assertTrue(any("Cannot read mapping file" in e for e in errors))
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
