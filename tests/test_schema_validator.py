#!/usr/bin/env python3
"""
Unit tests for schema/validator.py

Covers:
  - validate_schema: structure checks, SQL identifiers, FK consistency,
    primary keys, on_delete, sample_doc path resolution
  - validate_file: valid JSON, missing file, invalid JSON
"""

import json
import os
import sys
import tempfile
import unittest

# Ensure the module under test is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from schema.validator import validate_schema, validate_file


# ---------------------------------------------------------------------------
# Helper: minimal valid mapping
# ---------------------------------------------------------------------------

def _base_mapping(**overrides) -> dict:
    """Return a minimal valid mapping dict with a single parent table."""
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
        ],
    }
    m.update(overrides)
    return m


def _parent_child_mapping(**child_overrides) -> dict:
    """Return a mapping with a parent and a child table."""
    child = {
        "name": "orders",
        "parent": "users",
        "source_array": "$.orders",
        "foreign_key": {
            "column": "user_id",
            "references": "id",
        },
        "columns": {
            "order_id": "$.order_id",
            "user_id": "$.id",
        },
    }
    child.update(child_overrides)
    return {
        "tables": [
            {
                "name": "users",
                "primary_key": "id",
                "columns": {
                    "id": "$._id",
                    "name": "$.name",
                },
            },
            child,
        ],
    }


# ===================================================================
# validate_schema
# ===================================================================

class TestValidateSchemaStructure(unittest.TestCase):
    """Basic structural validation."""

    def test_empty_tables_list(self):
        warnings, errors = validate_schema({"tables": []})
        self.assertTrue(any("No tables" in e for e in errors))

    def test_no_tables_key(self):
        warnings, errors = validate_schema({})
        self.assertTrue(any("No tables" in e for e in errors))

    def test_table_with_no_name(self):
        mapping = {"tables": [{"columns": {"a": "$.a"}}]}
        warnings, errors = validate_schema(mapping)
        self.assertTrue(any("has no name" in e for e in errors))

    def test_table_invalid_sql_identifier(self):
        mapping = {"tables": [{"name": "1bad-name!", "primary_key": "id", "columns": {"id": "$.id"}}]}
        warnings, errors = validate_schema(mapping)
        self.assertTrue(any("invalid SQL identifier" in e for e in errors))

    def test_duplicate_table_names(self):
        mapping = {
            "tables": [
                {"name": "users", "primary_key": "id", "columns": {"id": "$.id"}},
                {"name": "users", "primary_key": "id", "columns": {"id": "$.id"}},
            ]
        }
        warnings, errors = validate_schema(mapping)
        self.assertTrue(any("Duplicate" in e for e in errors))

    def test_parent_table_missing_primary_key(self):
        mapping = {"tables": [{"name": "users", "columns": {"id": "$.id"}}]}
        warnings, errors = validate_schema(mapping)
        self.assertTrue(any("primary_key" in w for w in warnings))
        self.assertEqual(errors, [])

    def test_table_with_no_columns(self):
        mapping = {"tables": [{"name": "users", "primary_key": "id", "columns": {}}]}
        warnings, errors = validate_schema(mapping)
        self.assertTrue(any("no columns" in e for e in errors))

    def test_column_invalid_sql_identifier(self):
        mapping = {"tables": [{"name": "t", "primary_key": "id", "columns": {"bad col!": "$.x"}}]}
        warnings, errors = validate_schema(mapping)
        self.assertTrue(any("not a valid SQL identifier" in e for e in errors))

    def test_column_path_not_starting_with_dollar(self):
        mapping = {"tables": [{"name": "t", "primary_key": "id", "columns": {"a": "name"}}]}
        warnings, errors = validate_schema(mapping)
        self.assertTrue(any("must start with '$'" in e for e in errors))

    def test_column_path_dict_not_starting_with_dollar(self):
        mapping = {"tables": [{"name": "t", "primary_key": "id", "columns": {"a": {"path": "name"}}}]}
        warnings, errors = validate_schema(mapping)
        self.assertTrue(any("must start with '$'" in e for e in errors))


class TestValidateSchemaParentChild(unittest.TestCase):
    """Parent/child FK consistency checks."""

    def test_valid_parent_child_no_errors(self):
        mapping = _parent_child_mapping()
        warnings, errors = validate_schema(mapping)
        self.assertEqual(errors, [])

    def test_child_parent_not_found(self):
        mapping = _parent_child_mapping(parent="nonexistent")
        warnings, errors = validate_schema(mapping)
        self.assertTrue(any("not found in mapping" in e for e in errors))

    def test_child_missing_fk_column(self):
        mapping = _parent_child_mapping(foreign_key={"references": "id"})
        warnings, errors = validate_schema(mapping)
        self.assertTrue(any("foreign_key.column" in e for e in errors))

    def test_child_missing_fk_references(self):
        mapping = _parent_child_mapping(foreign_key={"column": "user_id"})
        warnings, errors = validate_schema(mapping)
        self.assertTrue(any("foreign_key.references" in e for e in errors))

    def test_child_fk_references_column_not_in_parent(self):
        mapping = _parent_child_mapping(
            foreign_key={"column": "user_id", "references": "nonexistent_col"}
        )
        warnings, errors = validate_schema(mapping)
        self.assertTrue(any("not a column in parent" in e for e in errors))

    def test_child_with_parent_but_no_source_array(self):
        mapping = _parent_child_mapping()
        # Remove source_array from child
        del mapping["tables"][1]["source_array"]
        warnings, errors = validate_schema(mapping)
        self.assertTrue(any("no source_array" in w for w in warnings))


class TestValidateSchemaOnDelete(unittest.TestCase):
    """on_delete validation."""

    def test_invalid_on_delete_value(self):
        mapping = _base_mapping()
        mapping["tables"][0]["on_delete"] = "cascade"
        warnings, errors = validate_schema(mapping)
        self.assertTrue(any("on_delete" in e for e in errors))

    def test_on_delete_delete_valid(self):
        mapping = _base_mapping()
        mapping["tables"][0]["on_delete"] = "delete"
        warnings, errors = validate_schema(mapping)
        on_delete_errors = [e for e in errors if "on_delete" in e]
        self.assertEqual(on_delete_errors, [])

    def test_on_delete_ignore_valid(self):
        mapping = _base_mapping()
        mapping["tables"][0]["on_delete"] = "ignore"
        warnings, errors = validate_schema(mapping)
        on_delete_errors = [e for e in errors if "on_delete" in e]
        self.assertEqual(on_delete_errors, [])


class TestValidateSchemaSampleDoc(unittest.TestCase):
    """Sample document path resolution checks."""

    def test_path_found_no_warning(self):
        mapping = _base_mapping()
        sample_doc = {"id": "123", "name": "Alice"}
        warnings, errors = validate_schema(mapping, sample_doc=sample_doc)
        path_warnings = [w for w in warnings if "not found in sample doc" in w]
        self.assertEqual(path_warnings, [])
        self.assertEqual(errors, [])

    def test_path_not_found_warning(self):
        mapping = _base_mapping()
        sample_doc = {"id": "123"}  # missing "name"
        warnings, errors = validate_schema(mapping, sample_doc=sample_doc)
        self.assertTrue(any("not found in sample doc" in w for w in warnings))

    def test_source_array_checks_array_items(self):
        mapping = _parent_child_mapping()
        sample_doc = {
            "_id": "u1",
            "name": "Alice",
            "orders": [
                {"order_id": "o1", "id": "u1"},
            ],
        }
        warnings, errors = validate_schema(mapping, sample_doc=sample_doc)
        # Paths should resolve via parent doc or array items — no path warnings
        path_warnings = [w for w in warnings if "not found in sample doc" in w]
        self.assertEqual(path_warnings, [])

    def test_source_array_item_path_not_found_warning(self):
        mapping = _parent_child_mapping()
        # Add a column that won't be found in either parent or array items
        mapping["tables"][1]["columns"]["missing_col"] = "$.does_not_exist"
        sample_doc = {
            "_id": "u1",
            "name": "Alice",
            "orders": [
                {"order_id": "o1"},
            ],
        }
        warnings, errors = validate_schema(mapping, sample_doc=sample_doc)
        self.assertTrue(any("not found in sample doc" in w for w in warnings))


# ===================================================================
# validate_file
# ===================================================================

class TestValidateFile(unittest.TestCase):
    """Tests for validate_file()."""

    def test_valid_json_file(self):
        mapping = _base_mapping()
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump(mapping, f)
            f.flush()
            tmp_path = f.name
        try:
            warnings, errors = validate_file(tmp_path)
            self.assertEqual(errors, [])
        finally:
            os.unlink(tmp_path)

    def test_file_not_found(self):
        warnings, errors = validate_file("/tmp/nonexistent_file_12345.json")
        self.assertTrue(any("Cannot read mapping file" in e for e in errors))

    def test_invalid_json(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            f.write("{not valid json!!!")
            f.flush()
            tmp_path = f.name
        try:
            warnings, errors = validate_file(tmp_path)
            self.assertTrue(any("Cannot read mapping file" in e for e in errors))
        finally:
            os.unlink(tmp_path)


if __name__ == "__main__":
    unittest.main()
