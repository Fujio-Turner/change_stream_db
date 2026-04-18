"""
Schema validator – validates a schema mapping definition file.

Checks:
  - All referenced JSON paths are syntactically valid
  - All target tables / columns are syntactically valid
  - Foreign-key references between parent and child tables are consistent
  - Primary keys are defined for parent tables
  - Source arrays exist for child tables with parents
"""

import json
import re
import logging
from pathlib import Path
from typing import Any  # noqa: F401

try:
    from icecream import ic
except ImportError:
    ic = lambda *a, **kw: None  # noqa: E731

logger = logging.getLogger("changes_worker")


def validate_schema(
    mapping: dict, sample_doc: dict | None = None
) -> tuple[list[str], list[str]]:
    """
    Validate a schema mapping definition.

    Args:
        mapping: The mapping definition dict
        sample_doc: Optional sample document to validate JSON paths against

    Returns:
        (warnings, errors) — lists of human-readable messages
    """
    warnings: list[str] = []
    errors: list[str] = []

    # Check basic structure
    tables = mapping.get("tables", [])
    if not tables:
        errors.append("No tables defined in mapping")
        return warnings, errors

    table_names = set()
    for i, table in enumerate(tables):
        name = table.get("name", "")
        if not name:
            errors.append(f"Table at index {i} has no name")
            continue
        if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", name):
            errors.append(f"Table '{name}' has invalid SQL identifier name")
        if name in table_names:
            errors.append(f"Duplicate table name: '{name}'")
        table_names.add(name)

        # Check primary key on parent tables
        parent = table.get("parent", "")
        if not parent and not table.get("primary_key"):
            warnings.append(
                f"Table '{name}' has no primary_key — UPSERTs won't work, only INSERTs"
            )

        # Check columns
        columns = table.get("columns", {})
        if not columns:
            errors.append(f"Table '{name}' has no columns defined")

        for col_name, col_def in columns.items():
            if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", col_name):
                errors.append(
                    f"Table '{name}': column '{col_name}' is not a valid SQL identifier"
                )

            # Validate path
            if isinstance(col_def, dict):
                path = col_def.get("path", "")
            else:
                path = col_def

            if path and not path.startswith("$"):
                errors.append(
                    f"Table '{name}', column '{col_name}': path must start with '$', got '{path}'"
                )

            # If sample_doc provided, check the path resolves
            if sample_doc and path and path.startswith("$."):
                from schema.mapper import resolve_path

                value = resolve_path(sample_doc, path)
                if value is None:
                    # For child tables with source_array, the path might be relative to array items
                    source_array = table.get("source_array", "")
                    if source_array:
                        array_data = resolve_path(sample_doc, source_array)
                        if isinstance(array_data, list) and array_data:
                            item_val = resolve_path(array_data[0], path)
                            if item_val is None:
                                warnings.append(
                                    f"Table '{name}', column '{col_name}': path '{path}' not found in sample doc or array item"
                                )
                    else:
                        warnings.append(
                            f"Table '{name}', column '{col_name}': path '{path}' not found in sample doc"
                        )

        # Check parent/FK consistency
        if parent:
            if parent not in table_names:
                # Parent might be defined later
                found = any(t.get("name") == parent for t in tables)
                if not found:
                    errors.append(
                        f"Table '{name}': parent '{parent}' not found in mapping"
                    )

            fk = table.get("foreign_key", {})
            if not fk.get("column"):
                errors.append(
                    f"Table '{name}': has parent '{parent}' but no foreign_key.column"
                )
            if not fk.get("references"):
                errors.append(
                    f"Table '{name}': has parent '{parent}' but no foreign_key.references"
                )
            else:
                # Check that the referenced column exists on the parent
                ref_col = fk["references"]
                parent_table = next(
                    (t for t in tables if t.get("name") == parent), None
                )
                if parent_table and ref_col not in parent_table.get("columns", {}):
                    errors.append(
                        f"Table '{name}': foreign_key references '{ref_col}' which is not a column in parent '{parent}'"
                    )

            if not table.get("source_array"):
                warnings.append(
                    f"Table '{name}': has parent but no source_array — how will child rows be generated?"
                )

        # on_delete check
        on_delete = table.get("on_delete", "delete")
        if on_delete not in ("delete", "ignore"):
            errors.append(
                f"Table '{name}': on_delete must be 'delete' or 'ignore', got '{on_delete}'"
            )

    return warnings, errors


def validate_file(path: str | Path) -> tuple[list[str], list[str]]:
    """Validate a mapping file on disk."""
    try:
        with open(path) as f:
            mapping = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        return [], [f"Cannot read mapping file: {exc}"]
    return validate_schema(mapping)
