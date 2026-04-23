"""
Schema mapper – reads a mapping definition (as produced by the schema.html UI)
and produces SQL operations (UPSERT / DELETE) for RDBMS targets.

A mapping JSON defines source filters, one or more target tables with column
mappings (JSON-path → SQL column), optional transforms, parent/child
relationships via foreign keys, and array expansion via ``source_array``.
"""

from __future__ import annotations

import json
import logging
import math
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from pipeline.pipeline_logging import log_event

try:
    from icecream import ic
except ImportError:
    ic = lambda *a, **kw: None  # noqa: E731

logger = logging.getLogger("changes_worker")


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------


def resolve_path(doc: Any, path: str) -> Any:
    """Resolve a JSON path like ``$.address.city`` against a dict.

    ``$`` alone returns *doc* itself (useful for scalar array items).
    Returns ``None`` when any intermediate key is missing.
    """
    if path == "$":
        return doc

    if not path.startswith("$."):
        return None

    keys = path[2:].split(".")
    current: Any = doc
    for key in keys:
        if isinstance(current, dict):
            if key not in current:
                return None
            current = current[key]
        else:
            return None
    return current


# ---------------------------------------------------------------------------
# Transforms
# ---------------------------------------------------------------------------

_TRANSFORM_RE = re.compile(r"^(\w+)\(([^)]*)\)$")


def apply_transform(value: Any, transform: str) -> Any:
    """Apply a transform string to *value*.

    Supported transforms (ECMA-262 names):
        trim(), trimStart(), trimEnd(),
        toUpperCase(), toLowerCase(),
        parseInt(), parseFloat(),
        toFixed(,N), toString(),
        coalesce(,default), json_stringify(),
        includes().

    Transform arguments that look like a JSON path (start with ``$``) are
    stripped so that only the function parameters remain (e.g.
    ``toFixed($.total,2)`` → precision ``2``).
    """
    m = _TRANSFORM_RE.match(transform.strip())
    if not m:
        return value

    func = m.group(1).lower()
    raw_args = m.group(2).strip()

    # Strip any leading JSON-path argument (e.g. "$.total,2" → "2")
    parts = [p.strip() for p in raw_args.split(",") if p.strip()] if raw_args else []
    args = [p for p in parts if not p.startswith("$")]

    if func == "trim":
        return str(value).strip() if value is not None else value
    if func == "trimstart":
        return str(value).lstrip() if value is not None else value
    if func == "trimend":
        return str(value).rstrip() if value is not None else value
    if func in ("touppercase", "uppercase", "upper"):
        return str(value).upper() if value is not None else value
    if func in ("tolowercase", "lowercase", "lower"):
        return str(value).lower() if value is not None else value
    if func == "parseint":
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    if func == "parsefloat":
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    if func == "tofixed":
        try:
            d = Decimal(str(value))
            if args:
                places = int(args[0])
                d = round(d, places)
            return d
        except (TypeError, ValueError, InvalidOperation):
            return None
    if func == "tostring":
        return str(value) if value is not None else None
    if func == "coalesce":
        if value is not None:
            return value
        return args[0] if args else None
    if func == "to_date":
        if value is None:
            return None
        try:
            from datetime import date

            return date.fromisoformat(str(value))
        except (TypeError, ValueError):
            return None
    if func == "json_stringify":
        return json.dumps(value) if value is not None else None

    if func == "split":
        if value is None:
            return None
        delimiter = args[0] if args else ","
        # Strip surrounding quotes from the delimiter if present
        if (
            len(delimiter) >= 2
            and delimiter[0] in ('"', "'")
            and delimiter[-1] == delimiter[0]
        ):
            delimiter = delimiter[1:-1]
        return str(value).split(delimiter)
    if func == "left":
        if value is None:
            return None
        n = int(args[0]) if args else 1
        return str(value)[:n]
    if func == "right":
        if value is None:
            return None
        n = int(args[0]) if args else 1
        return str(value)[-n:] if n else ""
    if func == "substring":
        if value is None:
            return None
        s = str(value)
        start = int(args[0]) if len(args) > 0 else 0
        length = int(args[1]) if len(args) > 1 else len(s) - start
        return s[start : start + length]
    if func == "replace":
        if value is None:
            return None
        old = args[0] if len(args) > 0 else ""
        new = args[1] if len(args) > 1 else ""
        # Strip quotes
        if len(old) >= 2 and old[0] in ('"', "'") and old[-1] == old[0]:
            old = old[1:-1]
        if len(new) >= 2 and new[0] in ('"', "'") and new[-1] == new[0]:
            new = new[1:-1]
        return str(value).replace(old, new)
    if func == "startswith":
        if value is None:
            return False
        prefix = args[0] if args else ""
        if len(prefix) >= 2 and prefix[0] in ('"', "'") and prefix[-1] == prefix[0]:
            prefix = prefix[1:-1]
        return str(value).startswith(prefix)
    if func == "endswith":
        if value is None:
            return False
        suffix = args[0] if args else ""
        if len(suffix) >= 2 and suffix[0] in ('"', "'") and suffix[-1] == suffix[0]:
            suffix = suffix[1:-1]
        return str(value).endswith(suffix)
    if func == "includes":
        if value is None:
            return False
        substr = args[0] if args else ""
        if len(substr) >= 2 and substr[0] in ('"', "'") and substr[-1] == substr[0]:
            substr = substr[1:-1]
        return substr in str(value)
    if func == "regex_match":
        if value is None:
            return False
        pattern = args[0] if args else ""
        if len(pattern) >= 2 and pattern[0] in ('"', "'") and pattern[-1] == pattern[0]:
            pattern = pattern[1:-1]
        return bool(re.search(pattern, str(value)))

    # Unrecognised transform – value is written untransformed
    log_event(
        logger,
        "warn",
        "MAPPING",
        "unknown transform '%s' – value written as-is (not transformed). "
        "Check transform name in your mapping config." % func,
        error_detail=transform,
    )
    return value


# ---------------------------------------------------------------------------
# Expression evaluation (for source matching)
# ---------------------------------------------------------------------------

# Matches expressions like:  split($._id,"::")[0]  or  lowercase($._id)
_EXPR_INDEX_RE = re.compile(r"^(\w+)\(([^)]*)\)\[(\d+)\]$")
_EXPR_PLAIN_RE = re.compile(r"^(\w+)\(([^)]*)\)$")


def evaluate_expression(doc: dict, expression: str) -> Any:
    """Evaluate a transform expression against *doc* and return the result.

    Supports forms like::

        split($._id,"::")[0]    → split the _id field by "::" and take index 0
        lowercase($._id)        → lowercase the _id field
        $._id                   → plain path resolution (no transform)

    The expression is resolved by:
    1. Extracting the JSON-path argument from inside the function call.
    2. Resolving the path against *doc* to get the value.
    3. Applying the transform function.
    4. Optionally indexing into the result with ``[N]``.
    """
    expression = expression.strip()

    # Plain JSON-path with no function wrapper
    if expression.startswith("$"):
        return resolve_path(doc, expression)

    # func(...)[N]  – transform with index access
    m = _EXPR_INDEX_RE.match(expression)
    if m:
        func_name = m.group(1)
        raw_args = m.group(2)
        index = int(m.group(3))

        # Find the JSON-path argument
        parts = [p.strip() for p in raw_args.split(",") if p.strip()]
        path_arg = next((p for p in parts if p.startswith("$")), None)
        value = resolve_path(doc, path_arg) if path_arg else None

        # Rebuild the transform string
        transform_str = f"{func_name}({raw_args})"
        result = apply_transform(value, transform_str)

        if isinstance(result, (list, tuple)):
            return result[index] if index < len(result) else None
        return None

    # func(...)  – transform without index
    m = _EXPR_PLAIN_RE.match(expression)
    if m:
        raw_args = m.group(2)
        parts = [p.strip() for p in raw_args.split(",") if p.strip()]
        path_arg = next((p for p in parts if p.startswith("$")), None)
        value = resolve_path(doc, path_arg) if path_arg else None
        transform_str = expression
        return apply_transform(value, transform_str)

    return None


# ---------------------------------------------------------------------------
# Column resolution
# ---------------------------------------------------------------------------


def resolve_column(doc: Any, col_def: str | dict) -> Any:
    """Resolve a column definition against *doc*.

    *col_def* is either a plain JSON-path string (``"$.status"``) or a dict
    with ``path`` and optional ``transform`` keys.
    """
    if isinstance(col_def, str):
        return resolve_path(doc, col_def)

    path: str = col_def.get("path", "$")
    value = resolve_path(doc, path)
    transform: str | None = col_def.get("transform")
    if transform:
        value = apply_transform(value, transform)
    return value


# ---------------------------------------------------------------------------
# SqlOperation
# ---------------------------------------------------------------------------


class SqlOperation:
    """Represents a single SQL statement (INSERT / UPSERT / DELETE)."""

    __slots__ = ("op_type", "table", "data", "where", "conflict_column")

    def __init__(
        self,
        op_type: str,
        table: str,
        *,
        data: dict[str, Any] | None = None,
        where: dict[str, Any] | None = None,
        conflict_column: str | None = None,
    ) -> None:
        self.op_type = op_type
        self.table = table
        self.data = data
        self.where = where
        self.conflict_column = conflict_column

    # ------------------------------------------------------------------ #
    # SQL generation (asyncpg $1, $2, … placeholders)
    # ------------------------------------------------------------------ #

    def to_sql(self) -> tuple[str, list[Any]]:
        """Return ``(sql_string, params_list)`` with ``$1, $2, …`` placeholders.

        All identifiers are double-quoted.
        """
        if self.op_type == "DELETE":
            return self._to_delete()
        if self.op_type == "INSERT":
            return self._to_insert()
        if self.op_type == "UPSERT":
            return self._to_upsert()
        raise ValueError(f"Unknown op_type: {self.op_type!r}")

    def _to_delete(self) -> tuple[str, list[Any]]:
        where = self.where or {}
        cols = list(where.keys())
        vals = [where[c] for c in cols]
        clauses = " AND ".join(f'"{c}" = ${i}' for i, c in enumerate(cols, 1))
        sql = f'DELETE FROM "{self.table}" WHERE {clauses}'
        return sql, vals

    def _to_insert(self) -> tuple[str, list[Any]]:
        data = self.data or {}
        cols = list(data.keys())
        vals = [data[c] for c in cols]
        col_list = ", ".join(f'"{c}"' for c in cols)
        val_list = ", ".join(f"${i}" for i in range(1, len(cols) + 1))
        sql = f'INSERT INTO "{self.table}" ({col_list}) VALUES ({val_list})'
        return sql, vals

    def _to_upsert(self) -> tuple[str, list[Any]]:
        data = self.data or {}
        cols = list(data.keys())
        vals = [data[c] for c in cols]
        pk = self.conflict_column or cols[0]

        col_list = ", ".join(f'"{c}"' for c in cols)
        val_list = ", ".join(f"${i}" for i in range(1, len(cols) + 1))
        update_set = ", ".join(f'"{c}" = EXCLUDED."{c}"' for c in cols if c != pk)
        sql = (
            f'INSERT INTO "{self.table}" ({col_list}) VALUES ({val_list}) '
            f'ON CONFLICT ("{pk}") DO UPDATE SET {update_set}'
        )
        return sql, vals

    # ------------------------------------------------------------------ #

    def __repr__(self) -> str:
        parts = [f"SqlOperation({self.op_type!r}, {self.table!r}"]
        if self.data:
            parts.append(f", data={self.data!r}")
        if self.where:
            parts.append(f", where={self.where!r}")
        if self.conflict_column:
            parts.append(f", conflict_column={self.conflict_column!r}")
        parts.append(")")
        return "".join(parts)


# ---------------------------------------------------------------------------
# SchemaMapper
# ---------------------------------------------------------------------------


class MappingDiagnostics:
    """Collects field-level issues found during document mapping."""

    __slots__ = ("missing", "type_mismatches")

    def __init__(self) -> None:
        self.missing: list[str] = []  # "table.column"
        self.type_mismatches: list[str] = []  # "table.column: expected str, got list"

    def add_missing(self, table: str, column: str) -> None:
        self.missing.append(f"{table}.{column}")

    def add_type_mismatch(
        self, table: str, column: str, expected: str, got: str
    ) -> None:
        self.type_mismatches.append(f"{table}.{column}: expected {expected}, got {got}")

    @property
    def has_issues(self) -> bool:
        return bool(self.missing or self.type_mismatches)

    def summary(self) -> str:
        parts: list[str] = []
        if self.missing:
            parts.append(f"missing=[{', '.join(self.missing)}]")
        if self.type_mismatches:
            parts.append(f"type_mismatches=[{', '.join(self.type_mismatches)}]")
        return "; ".join(parts)


# ISO date/datetime regex patterns for auto-coercion
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ISO_DATETIME_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2})?(\.\d+)?"
    r"([Zz]|[+\-]\d{2}:\d{2})?$"
)


def _maybe_coerce_date(value: str) -> str | date | datetime:
    """Convert ISO-format date/datetime strings to Python objects for asyncpg."""
    if _ISO_DATE_RE.match(value):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return value
    if _ISO_DATETIME_RE.match(value):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
    return value


# SQL type keyword → Python types that are acceptable
_SQL_TYPE_EXPECT: dict[str, tuple[str, tuple[type, ...]]] = {
    "text": ("str", (str,)),
    "varchar": ("str", (str,)),
    "char": ("str", (str,)),
    "int": ("int", (int,)),
    "integer": ("int", (int,)),
    "bigint": ("int", (int,)),
    "smallint": ("int", (int,)),
    "float": ("number", (int, float)),
    "double": ("number", (int, float)),
    "real": ("number", (int, float)),
    "numeric": ("number", (int, float, Decimal)),
    "decimal": ("number", (int, float, Decimal)),
    "boolean": ("bool", (bool,)),
    "bool": ("bool", (bool,)),
    "date": ("date", (str, date)),
    "timestamp": ("datetime", (str, datetime)),
    "timestamptz": ("datetime", (str, datetime)),
}


class SchemaMapper:
    """Maps a Couchbase document to a sequence of :class:`SqlOperation` objects
    using a mapping definition produced by the ``schema.html`` UI.
    """

    def __init__(self, mapping: dict) -> None:
        self.mapping = mapping
        self.source: dict = mapping.get("source", {})
        self.match: dict = self.source.get("match", {})
        self.tables: list[dict] = mapping.get("tables", [])

        # Pre-compute whether any column uses a transform function.
        # When False, _resolve_row can take a fast path that skips
        # per-value transform / type-check / date-coercion overhead.
        self.has_transforms: bool = self._scan_for_transforms()

        ic(
            "SchemaMapper.__init__",
            mapping.get("name"),
            self.match,
            self.has_transforms,
        )

    @classmethod
    def from_file(cls, path: str | Path) -> SchemaMapper:
        """Load a mapping from a JSON file."""
        p = Path(path)
        with p.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return cls(data)

    @classmethod
    def from_job(cls, job: dict) -> SchemaMapper:
        """Load a mapping from a job document.

        Phase 9: Mappings are now embedded in job documents under schema_mapping.
        This method extracts the mapping from a job and creates a SchemaMapper.
        """
        mapping = job.get("schema_mapping", {})
        if not mapping:
            raise ValueError(f"Job {job.get('id')} has no schema_mapping")
        return cls(mapping)

    # ------------------------------------------------------------------ #
    # Source matching
    # ------------------------------------------------------------------ #

    def matches(self, doc: dict) -> bool:
        """Return ``True`` if *doc* satisfies the source filter.

        The match block supports two modes:

        **Field mode** (simple)::

            {"field": "type", "value": "order"}

        **Expression mode** (string functions on doc fields)::

            {"expression": "split($._id,\"::\")[0]", "value": "invoice"}

        Expressions can use any supported transform function, optionally with
        an index accessor for functions that return lists (e.g. ``split``).
        """
        if not self.match:
            ic("matches", True)
            return True

        value = self.match.get("value")
        expression = self.match.get("expression")

        if expression is not None:
            resolved = evaluate_expression(doc, expression)
            result = resolved == value
            ic("matches", result)
            return result

        field = self.match.get("field")
        if field is None:
            ic("matches", True)
            return True
        result = doc.get(field) == value
        ic("matches", result)
        return result

    # ------------------------------------------------------------------ #
    # Document → SQL operations
    # ------------------------------------------------------------------ #

    def map_document(
        self, doc: dict, *, is_delete: bool = False
    ) -> tuple[list[SqlOperation], MappingDiagnostics]:
        """Map a single document to a list of :class:`SqlOperation`.

        Returns ``(ops, diagnostics)`` where *diagnostics* contains any
        missing-field or type-mismatch warnings discovered during mapping.

        For **upserts** the order is parents first, then children.
        For **deletes** the order is children first, then parents.

        Tables with ``on_delete: "ignore"`` are skipped when *is_delete* is
        ``True``.  Child tables with ``replace_strategy: "delete_insert"``
        receive a ``DELETE`` before the ``INSERT`` rows.
        """
        ic("map_document", doc.get("_id"), is_delete, len(self.tables))

        parent_tables: list[dict] = []
        child_tables: list[dict] = []

        for tbl in self.tables:
            if tbl.get("parent"):
                child_tables.append(tbl)
            else:
                parent_tables.append(tbl)

        diag = MappingDiagnostics()

        if is_delete:
            return self._map_delete(doc, parent_tables, child_tables), diag
        return self._map_upsert(doc, parent_tables, child_tables, diag), diag

    # ---- upsert ---------------------------------------------------------

    def _map_upsert(
        self,
        doc: dict,
        parent_tables: list[dict],
        child_tables: list[dict],
        diag: MappingDiagnostics,
    ) -> list[SqlOperation]:
        ops: list[SqlOperation] = []

        # Parents first
        for tbl in parent_tables:
            ops.append(self._build_upsert(tbl, doc, diag))

        # Children
        for tbl in child_tables:
            source_array_path = tbl.get("source_array")
            replace_strategy = tbl.get("replace_strategy")
            fk_def = tbl.get("foreign_key", {})

            if source_array_path:
                items = resolve_path(doc, source_array_path)
                if not isinstance(items, list):
                    items = []

                # delete_insert: wipe existing child rows first
                if replace_strategy == "delete_insert" and fk_def:
                    fk_col = fk_def["column"]
                    fk_ref = fk_def["references"]
                    # Resolve the FK value from the parent doc
                    parent_pk_path = self._find_parent_pk_path(tbl, doc)
                    fk_value = (
                        resolve_path(doc, parent_pk_path)
                        if parent_pk_path
                        else doc.get(fk_ref)
                    )
                    ops.append(
                        SqlOperation(
                            "DELETE",
                            tbl["name"],
                            where={fk_col: fk_value},
                        )
                    )

                for item in items:
                    row = self._resolve_row(tbl, item, doc, diag)
                    ops.append(SqlOperation("INSERT", tbl["name"], data=row))
            else:
                ops.append(self._build_upsert(tbl, doc, diag))

        return ops

    # ---- delete ---------------------------------------------------------

    def _map_delete(
        self,
        doc: dict,
        parent_tables: list[dict],
        child_tables: list[dict],
    ) -> list[SqlOperation]:
        ops: list[SqlOperation] = []

        # Children first (reverse dependency order)
        for tbl in child_tables:
            if tbl.get("on_delete") == "ignore":
                continue
            fk_def = tbl.get("foreign_key", {})
            if fk_def:
                fk_col = fk_def["column"]
                fk_ref = fk_def["references"]
                parent_pk_path = self._find_parent_pk_path(tbl, doc)
                fk_value = (
                    resolve_path(doc, parent_pk_path)
                    if parent_pk_path
                    else doc.get(fk_ref)
                )
                ops.append(
                    SqlOperation(
                        "DELETE",
                        tbl["name"],
                        where={fk_col: fk_value},
                    )
                )
            else:
                pk = tbl.get("primary_key", "id")
                pk_path = self._pk_path(tbl)
                pk_value = resolve_path(doc, pk_path)
                ops.append(SqlOperation("DELETE", tbl["name"], where={pk: pk_value}))

        # Parents
        for tbl in parent_tables:
            if tbl.get("on_delete") == "ignore":
                continue
            pk = tbl.get("primary_key", "id")
            pk_path = self._pk_path(tbl)
            pk_value = resolve_path(doc, pk_path)
            ops.append(SqlOperation("DELETE", tbl["name"], where={pk: pk_value}))

        return ops

    # ---- helpers --------------------------------------------------------

    def _build_upsert(
        self, tbl: dict, doc: dict, diag: MappingDiagnostics
    ) -> SqlOperation:
        pk = tbl.get("primary_key", "id")
        row = self._resolve_row(tbl, doc, doc, diag)
        return SqlOperation("UPSERT", tbl["name"], data=row, conflict_column=pk)

    def _resolve_row(
        self, tbl: dict, item: Any, parent_doc: dict, diag: MappingDiagnostics
    ) -> dict[str, Any]:
        """Resolve all columns for a table row.

        For array-expanded children, paths are resolved against *item* first;
        if ``None`` the path is retried against *parent_doc* (so that
        ``$._id`` can reference the parent document).

        Missing values and type mismatches are recorded in *diag*.
        """
        table_name: str = tbl.get("name", "?")
        columns: dict[str, str | dict] = tbl.get("columns", {})

        # --- fast path: no transforms anywhere in this mapping --------
        # When all columns are plain JSON-path strings we can skip
        # transform application, type-mismatch checks, float sanitisation,
        # and date coercion entirely.
        if not self.has_transforms:
            row: dict[str, Any] = {}
            for col_name, col_def in columns.items():
                path = col_def if isinstance(col_def, str) else col_def.get("path", "$")
                value = resolve_path(item, path)
                if value is None and item is not parent_doc:
                    value = resolve_path(parent_doc, path)
                if value is None:
                    diag.add_missing(table_name, col_name)
                row[col_name] = value
            return row

        # --- full path: transforms / diagnostics / coercion -----------
        row = {}
        for col_name, col_def in columns.items():
            value = resolve_column(item, col_def)
            if value is None and item is not parent_doc:
                value = resolve_column(parent_doc, col_def)

            # --- diagnostics: missing field ---
            if value is None:
                diag.add_missing(table_name, col_name)

            # --- diagnostics: type mismatch ---
            if value is not None and isinstance(col_def, dict):
                sql_type = col_def.get("type", "").lower().split("(")[0].strip()
                if sql_type in _SQL_TYPE_EXPECT:
                    expected_label, expected_types = _SQL_TYPE_EXPECT[sql_type]
                    if not isinstance(value, expected_types):
                        actual = type(value).__name__
                        diag.add_type_mismatch(
                            table_name, col_name, expected_label, actual
                        )

            # Sanitize float inf/nan – PostgreSQL integer/numeric columns reject them
            if isinstance(value, float) and (math.isinf(value) or math.isnan(value)):
                log_event(
                    logger,
                    "warn",
                    "MAPPING",
                    "non-finite float value – replacing with None",
                    error_detail=f"column={col_name!r} value={value!r}",
                )
                value = None

            # Coerce ISO-format date/datetime strings to Python objects for asyncpg
            if isinstance(value, str) and value:
                value = _maybe_coerce_date(value)

            row[col_name] = value
        return row

    def _pk_path(self, tbl: dict) -> str:
        """Return the JSON path mapped to the primary-key column."""
        pk = tbl.get("primary_key", "id")
        columns: dict = tbl.get("columns", {})
        col_def = columns.get(pk)
        if col_def is None:
            return f"$.{pk}"
        if isinstance(col_def, str):
            return col_def
        return col_def.get("path", f"$.{pk}")

    def _scan_for_transforms(self) -> bool:
        """Return True if any column in any table defines a transform."""
        for tbl in self.tables:
            for col_def in tbl.get("columns", {}).values():
                if isinstance(col_def, dict) and col_def.get("transform"):
                    return True
        return False

    def _find_parent_pk_path(self, child_tbl: dict, doc: dict) -> str | None:
        """Find the JSON path for the parent's PK referenced by the child FK."""
        fk_def = child_tbl.get("foreign_key", {})
        fk_ref = fk_def.get("references")
        parent_name = child_tbl.get("parent")
        if not fk_ref or not parent_name:
            return None
        for tbl in self.tables:
            if tbl["name"] == parent_name:
                columns: dict = tbl.get("columns", {})
                col_def = columns.get(fk_ref)
                if col_def is None:
                    return f"$.{fk_ref}"
                if isinstance(col_def, str):
                    return col_def
                return col_def.get("path", f"$.{fk_ref}")
        return f"$.{fk_ref}"
