# cbl_store.py — Couchbase Lite CE storage layer

import datetime
import json
import os
import time
import logging
import threading

from pipeline_logging import log_event

try:
    from icecream import ic
except ImportError:
    ic = lambda *a, **kw: None  # noqa: E731

try:
    from CouchbaseLite.Database import Database, DatabaseConfiguration
    from CouchbaseLite.Document import MutableDocument
    from CouchbaseLite._PyCBL import ffi, lib
    from CouchbaseLite.common import stringParam, gError as _cbl_gError
    from CouchbaseLite.Query import N1QLQuery, Query

    USE_CBL = True
except ImportError:
    USE_CBL = False

CBL_DB_DIR = os.environ.get("CBL_DB_DIR", "/app/data")
CBL_DB_NAME = os.environ.get("CBL_DB_NAME", "changes_worker_db")
CBL_SCOPE = "changes-worker"

# ── Collections (v2.0) ────────────────────────────────────────
# Pipeline Collections (core data model)
COLL_INPUTS_CHANGES = "inputs_changes"
COLL_OUTPUTS_RDBMS = "outputs_rdbms"
COLL_OUTPUTS_HTTP = "outputs_http"
COLL_OUTPUTS_CLOUD = "outputs_cloud"
COLL_OUTPUTS_STDOUT = "outputs_stdout"
COLL_JOBS = "jobs"

# Runtime Collections
COLL_CHECKPOINTS = "checkpoints"
COLL_DLQ = "dlq"
COLL_DATA_QUALITY = "data_quality"
COLL_ENRICHMENTS = "enrichments"

# Infrastructure Collections
COLL_CONFIG = "config"

# Auth & Identity Collections (future)
COLL_USERS = "users"
COLL_SESSIONS = "sessions"

# Observability Collections (future)
COLL_AUDIT_LOG = "audit_log"
COLL_NOTIFICATIONS = "notifications"

# Legacy (phased out)
COLL_MAPPINGS = "mappings"  # Deprecated — mappings now embedded in jobs


def configure_cbl(db_dir: str | None = None, db_name: str | None = None) -> None:
    """Override CBL database directory and name from config. Must be called before get_db()."""
    global CBL_DB_DIR, CBL_DB_NAME
    if db_dir:
        CBL_DB_DIR = db_dir
    if db_name:
        CBL_DB_NAME = db_name


logger = logging.getLogger("changes_worker")

_db = None  # module-level singleton


def _db_file_path() -> str:
    """Return the expected database file path."""
    return os.path.join(CBL_DB_DIR, f"{CBL_DB_NAME}.cblite2")


def _db_size_mb() -> float:
    """Return the total size of the CBL database directory in MB."""
    db_path = _db_file_path()
    if not os.path.exists(db_path):
        return 0.0
    total = 0
    if os.path.isdir(db_path):
        for dirpath, _, filenames in os.walk(db_path):
            for f in filenames:
                fp = os.path.join(dirpath, f)
                try:
                    total += os.path.getsize(fp)
                except OSError:
                    pass
    else:
        total = os.path.getsize(db_path)
    return round(total / (1024 * 1024), 2)


def get_db():
    """Open or return the singleton CBL database handle."""
    global _db
    if _db is None:
        ic("get_db: opening", CBL_DB_NAME, CBL_DB_DIR)
        os.makedirs(CBL_DB_DIR, exist_ok=True)
        config = DatabaseConfiguration(CBL_DB_DIR)
        t0 = time.monotonic()
        _db = Database(CBL_DB_NAME, config)
        elapsed = (time.monotonic() - t0) * 1000
        ic("get_db: opened", CBL_DB_NAME, round(elapsed, 1))
        log_event(
            logger,
            "info",
            "CBL",
            "database opened",
            operation="OPEN",
            db_name=CBL_DB_NAME,
            db_path=CBL_DB_DIR,
            db_size_mb=_db_size_mb(),
            duration_ms=round(elapsed, 1),
        )
    return _db


def close_db():
    """Close the singleton CBL database handle."""
    global _db
    if _db is not None:
        ic("close_db: closing", CBL_DB_NAME)
        t0 = time.monotonic()
        _db.close()
        elapsed = (time.monotonic() - t0) * 1000
        log_event(
            logger,
            "info",
            "CBL",
            "database closed",
            operation="CLOSE",
            db_name=CBL_DB_NAME,
            duration_ms=round(elapsed, 1),
        )
        _collections.clear()
        _db = None


# ---------------------------------------------------------------------------
# Collection helpers (raw CFFI – the Python bindings don't expose collections)
# ---------------------------------------------------------------------------

_collections: dict[tuple[str, str], object] = {}


def _get_collection(db, scope_name: str, collection_name: str):
    """Get or create a collection. Caches the CBLCollection* pointer."""
    key = (scope_name, collection_name)
    if key in _collections:
        return _collections[key]
    coll = lib.CBLDatabase_CreateCollection(
        db._ref, stringParam(collection_name), stringParam(scope_name), _cbl_gError
    )
    if coll == ffi.NULL:
        raise RuntimeError(
            f"Failed to create collection {scope_name}.{collection_name}"
        )
    _collections[key] = coll
    log_event(
        logger,
        "debug",
        "CBL",
        "collection ready",
        scope=scope_name,
        collection=collection_name,
    )
    return coll


def _coll_get_doc(db, collection_name: str, doc_id: str):
    """Get a document from a specific collection. Returns Document or None."""
    coll = _get_collection(db, CBL_SCOPE, collection_name)
    doc_ref = lib.CBLCollection_GetDocument(coll, stringParam(doc_id), _cbl_gError)
    if doc_ref == ffi.NULL:
        return None
    from CouchbaseLite.Document import Document

    doc = Document.__new__(Document)
    doc._ref = doc_ref
    return doc


def _coll_get_mutable_doc(db, collection_name: str, doc_id: str):
    """Get a mutable document from a specific collection. Returns MutableDocument or None."""
    coll = _get_collection(db, CBL_SCOPE, collection_name)
    doc_ref = lib.CBLCollection_GetMutableDocument(
        coll, stringParam(doc_id), _cbl_gError
    )
    if doc_ref == ffi.NULL:
        return None
    doc = MutableDocument.__new__(MutableDocument)
    doc._ref = doc_ref
    return doc


def _coll_save_doc(db, collection_name: str, doc) -> None:
    """Save a document to a specific collection."""
    coll = _get_collection(db, CBL_SCOPE, collection_name)
    doc._prepareToSave()
    ok = lib.CBLCollection_SaveDocumentWithConcurrencyControl(
        coll,
        doc._ref,
        0,
        _cbl_gError,  # 0 = kCBLConcurrencyControlLastWriteWins
    )
    if not ok:
        raise RuntimeError(f"Failed to save document to {collection_name}")


def _coll_purge_doc(db, collection_name: str, doc_id: str) -> None:
    """Purge a document from a specific collection by ID.

    Raises RuntimeError if purge fails.
    """
    coll = _get_collection(db, CBL_SCOPE, collection_name)
    err = ffi.new("CBLError*")
    ok = lib.CBLCollection_PurgeDocumentByID(coll, stringParam(doc_id), err)
    if not ok:
        log_event(
            logger,
            "error",
            "CBL",
            f"Failed to purge document from {collection_name}",
            doc_id=doc_id,
            collection=collection_name,
        )
        raise RuntimeError(f"Failed to purge document {doc_id} from {collection_name}")


def _create_collection_value_index(
    db, collection_name: str, index_name: str, expressions: str
) -> bool:
    """Create a value index on a collection using the CBL C API.

    Args:
        db: CBL database handle.
        collection_name: Collection within CBL_SCOPE.
        index_name: Name of the index (idempotent — recreates if identical).
        expressions: N1QL comma-separated property list, e.g. ``"time"``.
    """
    coll = _get_collection(db, CBL_SCOPE, collection_name)
    err = ffi.new("CBLError*")
    config = (lib.kCBLN1QLLanguage, stringParam(expressions))
    ok = lib.CBLCollection_CreateValueIndex(coll, stringParam(index_name), config, err)
    if ok:
        log_event(
            logger,
            "debug",
            "CBL",
            "index ensured",
            scope=CBL_SCOPE,
            collection=collection_name,
            index=index_name,
        )
    else:
        log_event(
            logger,
            "warn",
            "CBL",
            "index creation failed",
            scope=CBL_SCOPE,
            collection=collection_name,
            index=index_name,
        )
    return bool(ok)


def _run_n1ql(db, sql: str, params: dict | None = None) -> list[dict]:
    """Execute a N1QL (SQL++) query and return all rows as dicts.

    Raises RuntimeError on query failure.
    """
    try:
        q = N1QLQuery(db, sql)
        if params:
            q.setParameters(params)
        return [row.asDictionary() for row in q.execute()]
    except Exception as e:
        log_event(
            logger,
            "error",
            "CBL",
            f"N1QL query failed: {type(e).__name__}: {str(e)[:200]}",
            sql=sql[:200],
            params=str(params)[:100] if params else None,
        )
        raise RuntimeError(f"N1QL query failed: {e}") from e


def _run_n1ql_scalar(db, sql: str, params: dict | None = None):
    """Execute a N1QL (SQL++) query and return the first column of the first row.

    Returns None if no rows match.
    Raises RuntimeError on query failure.
    """
    try:
        q = N1QLQuery(db, sql)
        if params:
            q.setParameters(params)
        for row in q.execute():
            return row[0]
        return None
    except Exception as e:
        log_event(
            logger,
            "error",
            "CBL",
            f"N1QL scalar query failed: {type(e).__name__}: {str(e)[:200]}",
            sql=sql[:200],
            params=str(params)[:100] if params else None,
        )
        raise RuntimeError(f"N1QL scalar query failed: {e}") from e


def _run_n1ql_explain(db, sql: str, params: dict | None = None) -> str:
    """Return the CBLQuery_Explain output for a N1QL (SQL++) query.

    Raises RuntimeError if explain fails.
    """
    try:
        q = N1QLQuery(db, sql)
        if params:
            q.setParameters(params)
        return q.explanation or ""
    except Exception as e:
        log_event(
            logger,
            "error",
            "CBL",
            f"N1QL explain failed: {type(e).__name__}: {str(e)[:200]}",
            sql=sql[:200],
        )
        raise RuntimeError(f"N1QL explain failed: {e}") from e


# N1QL FROM clause for the DLQ collection (scope name needs backtick-quoting)
_DLQ_FROM = "`changes-worker`.dlq"

_DLQ_INDEXES_ENSURED = False


def _ensure_dlq_indexes(db) -> None:
    """Create value indexes on the DLQ collection (idempotent, once per process).

    ``type`` must be the leading column so the planner can use the index
    for ``WHERE d.type = 'dlq'`` which appears in every DLQ query.
    """
    global _DLQ_INDEXES_ENSURED
    if _DLQ_INDEXES_ENSURED:
        return
    _create_collection_value_index(db, COLL_DLQ, "idx_dlq_type_time", "type, time")
    _create_collection_value_index(
        db, COLL_DLQ, "idx_dlq_type_reason_time", "type, reason, time"
    )
    _create_collection_value_index(
        db, COLL_DLQ, "idx_dlq_type_retried", "type, retried"
    )
    _DLQ_INDEXES_ENSURED = True
    log_event(logger, "info", "CBL", "DLQ indexes ensured")


class _transaction:
    """Context manager for CBL database transactions.

    Wraps ``CBLDatabase_BeginTransaction`` / ``CBLDatabase_EndTransaction``.
    Commits on clean exit, rolls back on exception.

    Usage::

        with _transaction(db):
            _coll_save_doc(db, coll, doc1)
            _coll_save_doc(db, coll, doc2)
    """

    __slots__ = ("_db_ref",)

    def __init__(self, db):
        self._db_ref = db._ref

    def __enter__(self):
        err = ffi.new("CBLError*")
        if not lib.CBLDatabase_BeginTransaction(self._db_ref, err):
            raise RuntimeError("Failed to begin CBL transaction")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        commit = exc_type is None
        err = ffi.new("CBLError*")
        ok = lib.CBLDatabase_EndTransaction(self._db_ref, commit, err)
        if not ok:
            log_event(
                logger,
                "error",
                "CBL",
                f"Failed to end CBL transaction (commit={commit})",
                error_code=err.code if err else None,
            )
            # Don't raise — the transaction state may be inconsistent
            # Log the error but allow any original exception to propagate
        return False  # don't suppress exceptions


def _set_doc_expiration(
    db, collection_name: str, doc_id: str, ttl_seconds: int
) -> bool:
    """Set document expiration (TTL) on a specific collection using the CBL C API.

    Args:
        db: CBL database handle.
        collection_name: Collection within CBL_SCOPE.
        doc_id: Document ID.
        ttl_seconds: Seconds from now until the document expires and is auto-purged.
                     Pass 0 to clear expiration.
    """
    if ttl_seconds <= 0:
        return True
    coll = _get_collection(db, CBL_SCOPE, collection_name)
    expiration_ms = int((time.time() + ttl_seconds) * 1000)
    err = ffi.new("CBLError*")
    ok = lib.CBLCollection_SetDocumentExpiration(
        coll, stringParam(doc_id), expiration_ms, err
    )
    return bool(ok)


class CBLStore:
    """High-level API for all CBL storage operations."""

    def __init__(self):
        self.db = get_db()
        _ensure_dlq_indexes(self.db)

    # ── Info / diagnostics ────────────────────────────────────

    def dlq_explain_queries(self) -> dict[str, str]:
        """Return EXPLAIN output for the key DLQ queries to verify index usage."""
        f = _DLQ_FROM
        return {
            "list_page_by_time": _run_n1ql_explain(
                self.db,
                f"SELECT META(d).id AS id, d.doc_id_original, d.seq, d.method,"
                f" d.status, d.error, d.reason, d.time, d.expires_at,"
                f" d.retried, d.replay_attempts, d.target_url"
                f" FROM {f} AS d WHERE d.type = 'dlq'"
                f" ORDER BY d.time DESC, META(d).id DESC"
                f" LIMIT 20 OFFSET 0",
            ),
            "list_page_by_reason_filter": _run_n1ql_explain(
                self.db,
                f"SELECT META(d).id AS id, d.doc_id_original"
                f" FROM {f} AS d"
                f" WHERE d.type = 'dlq' AND LOWER(d.reason) LIKE $reason_like"
                f" ORDER BY d.time DESC, META(d).id DESC"
                f" LIMIT 20 OFFSET 0",
                {"reason_like": "data_error%"},
            ),
            "count_total": _run_n1ql_explain(
                self.db, f"SELECT COUNT(*) FROM {f} AS d WHERE d.type = 'dlq'"
            ),
            "count_retried": _run_n1ql_explain(
                self.db,
                f"SELECT COUNT(*) FROM {f} AS d"
                f" WHERE d.type = 'dlq' AND d.retried = true",
            ),
            "stats_totals": _run_n1ql_explain(
                self.db,
                f"SELECT COUNT(*) AS total, MIN(d.time) AS oldest_time"
                f" FROM {f} AS d WHERE d.type = 'dlq'",
            ),
            "stats_reason_group": _run_n1ql_explain(
                self.db,
                f"SELECT d.reason AS reason, COUNT(*) AS count"
                f" FROM {f} AS d WHERE d.type = 'dlq'"
                f" GROUP BY d.reason",
            ),
            "purge_expired": _run_n1ql_explain(
                self.db,
                f"SELECT META(d).id AS id FROM {f} AS d"
                f" WHERE d.type = 'dlq' AND d.time > 0 AND d.time < $cutoff",
                {"cutoff": 1000000000},
            ),
        }

    def db_info(self) -> dict:
        """Return database path, size, and document counts."""
        info = {
            "db_name": CBL_DB_NAME,
            "db_path": _db_file_path(),
            "db_size_mb": _db_size_mb(),
            "scope": CBL_SCOPE,
            "collections": [
                COLL_CONFIG,
                COLL_INPUTS_CHANGES,
                COLL_OUTPUTS_RDBMS,
                COLL_OUTPUTS_HTTP,
                COLL_OUTPUTS_CLOUD,
                COLL_OUTPUTS_STDOUT,
                COLL_JOBS,
                COLL_CHECKPOINTS,
                COLL_DLQ,
                COLL_DATA_QUALITY,
                COLL_ENRICHMENTS,
                COLL_SESSIONS,
                COLL_USERS,
                COLL_AUDIT_LOG,
                COLL_NOTIFICATIONS,
                COLL_MAPPINGS,
            ],
            "config_exists": _coll_get_doc(self.db, COLL_CONFIG, "config") is not None,
            "mappings_count": len(
                self._get_manifest(COLL_MAPPINGS, "manifest:mappings")
            ),
            "jobs_count": len(self.list_jobs()),
            "dlq_count": self.dlq_count(),
            "checkpoint_manifest": len(
                self._get_manifest(COLL_CHECKPOINTS, "manifest:checkpoints")
            ),
        }
        log_event(
            logger,
            "debug",
            "CBL",
            "database info retrieved",
            operation="SELECT",
            db_name=CBL_DB_NAME,
            db_size_mb=info["db_size_mb"],
        )
        return info

    # ── Config ────────────────────────────────────────────────

    def load_config(self) -> dict | None:
        ic("load_config: entry")
        t0 = time.monotonic()
        doc = _coll_get_doc(self.db, COLL_CONFIG, "config")
        elapsed = (time.monotonic() - t0) * 1000
        if not doc:
            log_event(
                logger,
                "warn",
                "CBL",
                "config document not found",
                operation="SELECT",
                doc_id="config",
                doc_type="config",
                duration_ms=round(elapsed, 1),
            )
            return None
        raw = doc.properties.get("data")
        if raw:
            cfg = json.loads(raw)
            log_event(
                logger,
                "debug",
                "CBL",
                "config loaded",
                operation="SELECT",
                doc_id="config",
                doc_type="config",
                duration_ms=round(elapsed, 1),
            )
            return cfg
        log_event(
            logger,
            "warn",
            "CBL",
            "config document has no data field",
            operation="SELECT",
            doc_id="config",
            doc_type="config",
            error_detail="missing 'data' property",
        )
        return None

    def save_config(self, cfg: dict) -> None:
        ic("save_config: entry")
        t0 = time.monotonic()
        doc = _coll_get_mutable_doc(self.db, COLL_CONFIG, "config")
        if not doc:
            doc = MutableDocument("config")
        doc["type"] = "config"
        doc["data"] = json.dumps(cfg)
        doc["schema_version"] = "2.0"
        doc["updated_at"] = int(time.time())
        _coll_save_doc(self.db, COLL_CONFIG, doc)
        elapsed = (time.monotonic() - t0) * 1000
        log_event(
            logger,
            "info",
            "CBL",
            "config saved",
            operation="INSERT" if elapsed else "UPDATE",
            doc_id="config",
            doc_type="config",
            schema_version="2.0",
            duration_ms=round(elapsed, 1),
        )

    def import_config_file(self, path: str) -> dict:
        with open(path) as f:
            cfg = json.load(f)
        self.save_config(cfg)
        log_event(
            logger,
            "info",
            "CBL",
            "config imported from file",
            operation="INSERT",
            doc_id="config",
            doc_type="config",
            db_path=path,
        )
        return cfg

    # ── Checkpoints ───────────────────────────────────────────

    def load_checkpoint(self, uuid: str) -> dict | None:
        doc_id = f"checkpoint:{uuid}"
        ic("load_checkpoint: entry", uuid, doc_id)
        t0 = time.monotonic()
        doc = _coll_get_doc(self.db, COLL_CHECKPOINTS, doc_id)
        elapsed = (time.monotonic() - t0) * 1000
        if not doc:
            log_event(
                logger,
                "debug",
                "CBL",
                "checkpoint not found",
                operation="SELECT",
                doc_id=doc_id,
                doc_type="checkpoint",
                duration_ms=round(elapsed, 1),
            )
            return None
        props = doc.properties
        result = {
            "client_id": props.get("client_id", ""),
            "SGs_Seq": props.get("SGs_Seq", "0"),
            "time": props.get("time", 0),
            "remote": props.get("remote", 0),
        }
        log_event(
            logger,
            "debug",
            "CBL",
            "checkpoint loaded",
            operation="SELECT",
            doc_id=doc_id,
            doc_type="checkpoint",
            seq=result["SGs_Seq"],
            duration_ms=round(elapsed, 1),
        )
        return result

    def save_checkpoint(self, uuid: str, seq: str, client_id: str, remote: int) -> None:
        doc_id = f"checkpoint:{uuid}"
        ic("save_checkpoint: entry", uuid, seq)
        t0 = time.monotonic()
        doc = _coll_get_mutable_doc(self.db, COLL_CHECKPOINTS, doc_id)
        is_new = doc is None
        if not doc:
            doc = MutableDocument(doc_id)
        doc["type"] = "checkpoint"
        doc["client_id"] = client_id
        doc["SGs_Seq"] = seq
        doc["time"] = int(time.time())
        doc["remote"] = remote
        _coll_save_doc(self.db, COLL_CHECKPOINTS, doc)
        elapsed = (time.monotonic() - t0) * 1000
        log_event(
            logger,
            "info",
            "CBL",
            "checkpoint saved",
            operation="INSERT" if is_new else "UPDATE",
            doc_id=doc_id,
            doc_type="checkpoint",
            seq=seq,
            duration_ms=round(elapsed, 1),
        )

    # ── Schema Mappings ───────────────────────────────────────

    def _get_manifest(self, collection_name: str, manifest_id: str) -> list[str]:
        doc = _coll_get_doc(self.db, collection_name, manifest_id)
        if not doc:
            return []
        raw = doc.properties.get("ids")
        if raw:
            return json.loads(raw)
        return []

    def _save_manifest(
        self, collection_name: str, manifest_id: str, ids: list[str]
    ) -> None:
        doc = _coll_get_mutable_doc(self.db, collection_name, manifest_id)
        if not doc:
            doc = MutableDocument(manifest_id)
        doc["type"] = "manifest"
        doc["ids"] = json.dumps(ids)
        _coll_save_doc(self.db, collection_name, doc)
        log_event(
            logger,
            "trace",
            "CBL",
            "manifest updated",
            operation="UPDATE",
            doc_id=manifest_id,
            doc_type="manifest",
            doc_count=len(ids),
        )

    def list_mappings(self) -> list[dict]:
        ids = self._get_manifest(COLL_MAPPINGS, "manifest:mappings")
        result = []
        for mid in ids:
            doc = _coll_get_doc(self.db, COLL_MAPPINGS, mid)
            if doc:
                props = doc.properties
                result.append(
                    {
                        "name": props.get("name", ""),
                        "content": props.get("content", ""),
                        "active": props.get("active", True),
                        "updated_at": props.get("updated_at", ""),
                    }
                )
        log_event(
            logger,
            "debug",
            "CBL",
            "listed mappings",
            operation="SELECT",
            doc_type="mapping",
            doc_count=len(result),
        )
        return result

    def get_mapping(self, name: str) -> str | None:
        doc_id = f"mapping:{name}"
        doc = _coll_get_doc(self.db, COLL_MAPPINGS, doc_id)
        if not doc:
            log_event(
                logger,
                "debug",
                "CBL",
                "mapping not found",
                operation="SELECT",
                doc_id=doc_id,
                doc_type="mapping",
            )
            return None
        log_event(
            logger,
            "debug",
            "CBL",
            "mapping loaded",
            operation="SELECT",
            doc_id=doc_id,
            doc_type="mapping",
        )
        return doc.properties.get("content")

    def save_mapping(self, name: str, content: str) -> None:
        doc_id = f"mapping:{name}"
        ic("save_mapping: entry", doc_id)
        t0 = time.monotonic()
        doc = _coll_get_mutable_doc(self.db, COLL_MAPPINGS, doc_id)
        is_new = doc is None
        if not doc:
            doc = MutableDocument(doc_id)
        doc["type"] = "mapping"
        doc["name"] = name
        doc["content"] = content
        doc["updated_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        if is_new:
            doc["active"] = True
        _coll_save_doc(self.db, COLL_MAPPINGS, doc)
        elapsed = (time.monotonic() - t0) * 1000

        # Update manifest
        ids = self._get_manifest(COLL_MAPPINGS, "manifest:mappings")
        if doc_id not in ids:
            ids.append(doc_id)
            self._save_manifest(COLL_MAPPINGS, "manifest:mappings", ids)

        log_event(
            logger,
            "info",
            "CBL",
            "mapping saved",
            operation="INSERT" if is_new else "UPDATE",
            doc_id=doc_id,
            doc_type="mapping",
            duration_ms=round(elapsed, 1),
        )

    def set_mapping_active(self, name: str, active: bool) -> bool:
        """Set the active status of a mapping. Returns True if found."""
        doc_id = f"mapping:{name}"
        doc = _coll_get_mutable_doc(self.db, COLL_MAPPINGS, doc_id)
        if not doc:
            return False
        doc["active"] = active
        doc["updated_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        _coll_save_doc(self.db, COLL_MAPPINGS, doc)
        log_event(
            logger,
            "info",
            "CBL",
            "mapping active status changed",
            operation="UPDATE",
            doc_id=doc_id,
            doc_type="mapping",
        )
        return True

    def delete_mapping(self, name: str) -> None:
        doc_id = f"mapping:{name}"
        ic("delete_mapping: entry", doc_id)
        doc = _coll_get_doc(self.db, COLL_MAPPINGS, doc_id)
        if not doc:
            log_event(
                logger,
                "debug",
                "CBL",
                "mapping not found for delete",
                operation="DELETE",
                doc_id=doc_id,
                doc_type="mapping",
            )
            return
        _coll_purge_doc(self.db, COLL_MAPPINGS, doc_id)

        # Update manifest
        ids = self._get_manifest(COLL_MAPPINGS, "manifest:mappings")
        ids = [i for i in ids if i != doc_id]
        self._save_manifest(COLL_MAPPINGS, "manifest:mappings", ids)

        log_event(
            logger,
            "info",
            "CBL",
            "mapping deleted",
            operation="DELETE",
            doc_id=doc_id,
            doc_type="mapping",
        )

    # ── RDBMS Schema ──────────────────────────────────────────

    def load_schema(self) -> dict | None:
        """Load RDBMS schema definitions from 'rdbms_schema' document."""
        doc_id = "rdbms_schema"
        ic("load_schema: entry", doc_id)
        doc = _coll_get_doc(self.db, COLL_MAPPINGS, doc_id)
        if not doc:
            log_event(
                logger,
                "debug",
                "CBL",
                "rdbms schema not found",
                operation="SELECT",
                doc_id=doc_id,
                doc_type="schema",
            )
            return None
        schema = {
            "type": doc.get("type", "rdbms"),
            "dialect": doc.get("dialect", "sql"),
            "data": doc.get("data", {}),
        }
        if "_meta" in doc:
            schema["_meta"] = dict(doc["_meta"])
        return schema

    def save_schema(self, schema: dict) -> None:
        """Save RDBMS schema definitions to 'rdbms_schema' document."""
        doc_id = "rdbms_schema"
        ic("save_schema: entry", doc_id)
        t0 = time.monotonic()
        doc = _coll_get_mutable_doc(self.db, COLL_MAPPINGS, doc_id)
        is_new = doc is None
        if not doc:
            doc = MutableDocument(doc_id)
        doc["type"] = schema.get("type", "rdbms")
        doc["dialect"] = schema.get("dialect", "sql")
        doc["data"] = schema.get("data", {})
        if "_meta" in schema:
            doc["_meta"] = schema["_meta"]
        _coll_save_doc(self.db, COLL_MAPPINGS, doc)
        elapsed = (time.monotonic() - t0) * 1000

        log_event(
            logger,
            "info",
            "CBL",
            "rdbms schema saved",
            operation="INSERT" if is_new else "UPDATE",
            doc_id=doc_id,
            doc_type="schema",
            duration_ms=round(elapsed, 1),
        )

    def delete_schema(self) -> None:
        """Delete saved RDBMS schema definitions."""
        doc_id = "rdbms_schema"
        ic("delete_schema: entry", doc_id)
        doc = _coll_get_doc(self.db, COLL_MAPPINGS, doc_id)
        if not doc:
            log_event(
                logger,
                "debug",
                "CBL",
                "rdbms schema not found for delete",
                operation="DELETE",
                doc_id=doc_id,
                doc_type="schema",
            )
            return
        _coll_purge_doc(self.db, COLL_MAPPINGS, doc_id)
        log_event(
            logger,
            "info",
            "CBL",
            "rdbms schema deleted",
            operation="DELETE",
            doc_id=doc_id,
            doc_type="schema",
        )

    # ── Source Configuration ───────────────────────────────────

    def load_sources(self) -> dict:
        """Load all saved data source configurations."""
        ic("load_sources: entry")
        try:
            # Read the index document that tracks all source names
            index_doc = _coll_get_doc(self.db, COLL_MAPPINGS, "_source_index")
            if not index_doc:
                return {}
            names = index_doc.get("names") or []
            sources = {}
            for name in names:
                doc = _coll_get_doc(self.db, COLL_MAPPINGS, name)
                if doc:
                    sources[name] = {
                        "type": doc.get("type", "source"),
                        "system": doc.get("system"),
                        "config": doc.get("config", {}),
                        "_meta": dict(doc.get("_meta", {})) if doc.get("_meta") else {},
                    }
            return sources
        except Exception as e:
            log_event(
                logger,
                "warning",
                "CBL",
                f"Failed to load sources: {e}",
                operation="SELECT",
                doc_type="source",
            )
            return {}

    def save_source(self, source_name: str, source_doc: dict) -> None:
        """Save a data source configuration."""
        ic("save_source: entry", source_name)
        t0 = time.monotonic()
        doc = _coll_get_mutable_doc(self.db, COLL_MAPPINGS, source_name)
        is_new = doc is None
        if not doc:
            doc = MutableDocument(source_name)
        doc["type"] = "source"
        doc["system"] = source_doc.get("system")
        doc["config"] = source_doc.get("config", {})
        if "_meta" in source_doc:
            doc["_meta"] = source_doc["_meta"]
        _coll_save_doc(self.db, COLL_MAPPINGS, doc)

        # Update the source index document
        index_doc = _coll_get_mutable_doc(self.db, COLL_MAPPINGS, "_source_index")
        if not index_doc:
            index_doc = MutableDocument("_source_index")
            index_doc["names"] = []
        names = list(index_doc.get("names") or [])
        if source_name not in names:
            names.append(source_name)
            index_doc["names"] = names
            _coll_save_doc(self.db, COLL_MAPPINGS, index_doc)

        elapsed = (time.monotonic() - t0) * 1000

        log_event(
            logger,
            "info",
            "CBL",
            "source configuration saved",
            operation="INSERT" if is_new else "UPDATE",
            doc_id=source_name,
            doc_type="source",
            system=source_doc.get("system"),
            duration_ms=round(elapsed, 1),
        )

    def delete_source(self, source_name: str) -> None:
        """Delete a saved source configuration."""
        ic("delete_source: entry", source_name)
        doc = _coll_get_doc(self.db, COLL_MAPPINGS, source_name)
        if not doc:
            log_event(
                logger,
                "debug",
                "CBL",
                "source not found for delete",
                operation="DELETE",
                doc_id=source_name,
                doc_type="source",
            )
            return
        _coll_purge_doc(self.db, COLL_MAPPINGS, source_name)

        # Remove from the source index document
        index_doc = _coll_get_mutable_doc(self.db, COLL_MAPPINGS, "_source_index")
        if index_doc:
            names = list(index_doc.get("names") or [])
            if source_name in names:
                names.remove(source_name)
                index_doc["names"] = names
                _coll_save_doc(self.db, COLL_MAPPINGS, index_doc)

        log_event(
            logger,
            "info",
            "CBL",
            "source configuration deleted",
            operation="DELETE",
            doc_id=source_name,
            doc_type="source",
        )

    def clear_all_sources(self) -> None:
        """Delete all saved source configurations."""
        ic("clear_all_sources: entry")
        try:
            sources = self.load_sources()
            for source_name in sources.keys():
                self.delete_source(source_name)
            log_event(
                logger,
                "info",
                "CBL",
                f"cleared all sources ({len(sources)} deleted)",
                operation="DELETE",
                doc_type="source",
                count=len(sources),
            )
        except Exception as e:
            log_event(
                logger,
                "error",
                "CBL",
                f"Failed to clear all sources: {e}",
                operation="DELETE",
                doc_type="source",
            )

        # ── Dead Letter Queue ─────────────────────────────────────

    def add_dlq_entry(
        self,
        doc_id: str,
        seq: str,
        method: str,
        status: int,
        error: str,
        doc: dict,
        target_url: str = "",
        ttl_seconds: int = 0,
        reason: str = "",
    ) -> None:
        ic("add_dlq_entry: entry", doc_id)
        ts = int(time.time())
        dlq_id = f"dlq:{doc_id}:{ts}"
        t0 = time.monotonic()
        dlq_doc = MutableDocument(dlq_id)
        dlq_doc["type"] = "dlq"
        dlq_doc["doc_id_original"] = doc_id
        dlq_doc["seq"] = seq
        dlq_doc["method"] = method
        dlq_doc["status"] = status
        dlq_doc["error"] = error
        dlq_doc["reason"] = reason
        dlq_doc["time"] = ts
        dlq_doc["expires_at"] = (ts + ttl_seconds) if ttl_seconds > 0 else 0
        dlq_doc["retried"] = False
        dlq_doc["replay_attempts"] = 0
        dlq_doc["target_url"] = target_url
        dlq_doc["doc_data"] = json.dumps(doc)
        _coll_save_doc(self.db, COLL_DLQ, dlq_doc)
        if ttl_seconds > 0:
            _set_doc_expiration(self.db, COLL_DLQ, dlq_id, ttl_seconds)
        elapsed = (time.monotonic() - t0) * 1000

        log_event(
            logger,
            "warn",
            "DLQ",
            "entry added",
            operation="INSERT",
            doc_id=dlq_id,
            doc_type="dlq",
            seq=seq,
            status=status,
            duration_ms=round(elapsed, 1),
        )

    def list_dlq(self) -> list[dict]:
        sql = (
            f"SELECT META(d).id AS id, d.doc_id_original, d.seq, d.method,"
            f" d.status, d.error, d.reason, d.time, d.expires_at,"
            f" d.retried, d.replay_attempts, d.target_url"
            f" FROM {_DLQ_FROM} AS d WHERE d.type = 'dlq'"
            f" ORDER BY d.time DESC, META(d).id DESC"
        )
        result = _run_n1ql(self.db, sql)
        log_event(
            logger,
            "debug",
            "DLQ",
            "listed entries",
            operation="SELECT",
            doc_type="dlq",
            doc_count=len(result),
        )
        return result

    # Whitelist of allowed sort columns for N1QL ORDER BY
    _DLQ_SORT_COLS = {
        "time": "d.time",
        "expires_at": "d.expires_at",
        "replay_attempts": "d.replay_attempts",
        "status": "d.status",
        "reason": "d.reason",
        "method": "d.method",
        "doc_id_original": "d.doc_id_original",
        "error": "d.error",
        "retried": "d.retried",
    }

    def list_dlq_page(
        self,
        limit: int = 20,
        offset: int = 0,
        sort: str = "time",
        order: str = "desc",
        reason_filter: str = "",
    ) -> dict:
        """Return a page of DLQ entries with server-side sort/filter/pagination.

        Uses N1QL queries with collection-level indexes.  Returns
        ``{"entries": [...], "total": N, "filtered": N}``.
        Returns empty page on query error.
        """
        try:
            sort_col = self._DLQ_SORT_COLS.get(sort, "d.time")
            direction = "ASC" if order.lower() == "asc" else "DESC"

            # Build WHERE clause
            where = "d.type = 'dlq'"
            params: dict | None = None
            if reason_filter:
                where += " AND LOWER(d.reason) LIKE $reason_like"
                params = {"reason_like": reason_filter.lower() + "%"}

            # Page query (LIMIT/OFFSET must be literal ints in CBL N1QL)
            sql = (
                f"SELECT META(d).id AS id, d.doc_id_original, d.seq, d.method,"
                f" d.status, d.error, d.reason, d.time, d.expires_at,"
                f" d.retried, d.replay_attempts, d.target_url"
                f" FROM {_DLQ_FROM} AS d WHERE {where}"
                f" ORDER BY {sort_col} {direction}, META(d).id {direction}"
                f" LIMIT {int(limit)} OFFSET {int(offset)}"
            )
            entries = _run_n1ql(self.db, sql, params)

            # Count queries
            total_sql = f"SELECT COUNT(*) FROM {_DLQ_FROM} AS d WHERE d.type = 'dlq'"
            total_count = _run_n1ql_scalar(self.db, total_sql) or 0

            if reason_filter:
                filter_sql = f"SELECT COUNT(*) FROM {_DLQ_FROM} AS d WHERE {where}"
                filtered_count = _run_n1ql_scalar(self.db, filter_sql, params) or 0
            else:
                filtered_count = total_count

            log_event(
                logger,
                "debug",
                "DLQ",
                "listed page",
                operation="SELECT",
                doc_type="dlq",
                offset=offset,
                limit=limit,
                filtered=filtered_count,
                total=total_count,
            )
            return {
                "entries": entries,
                "total": total_count,
                "filtered": filtered_count,
            }
        except Exception as e:
            log_event(
                logger,
                "error",
                "DLQ",
                f"Failed to list DLQ page: {type(e).__name__}: {str(e)[:200]}",
                operation="SELECT",
                doc_type="dlq",
                offset=offset,
                limit=limit,
            )
            # Return empty page on error
            return {
                "entries": [],
                "total": 0,
                "filtered": 0,
            }

    def dlq_stats(self) -> dict:
        """Return lightweight aggregation data for DLQ charts and summary cards.

        Uses N1QL aggregation queries instead of scanning all documents.
        Returns defaults on query error.
        """
        try:
            f = _DLQ_FROM

            # Totals + oldest in one query
            row = (
                _run_n1ql(
                    self.db,
                    f"SELECT COUNT(*) AS total, MIN(d.time) AS oldest_time"
                    f" FROM {f} AS d WHERE d.type = 'dlq'",
                )
                or [{}]
            )[0]
            total = row.get("total", 0)
            oldest_time = row.get("oldest_time")

            # Retried count
            retried = (
                _run_n1ql_scalar(
                    self.db,
                    f"SELECT COUNT(*) FROM {f} AS d"
                    f" WHERE d.type = 'dlq' AND d.retried = true",
                )
                or 0
            )
            pending = total - retried

            # Reason breakdown
            reason_rows = _run_n1ql(
                self.db,
                f"SELECT d.reason AS reason, COUNT(*) AS count"
                f" FROM {f} AS d WHERE d.type = 'dlq'"
                f" GROUP BY d.reason",
            )
            reason_counts: dict[str, int] = {}
            for r in reason_rows:
                key = r.get("reason", "") or "unknown"
                reason_counts[key] = r.get("count", 0)

            # Timeline by reason (stacked bar chart) — grouped by time buckets
            timeline_rows = _run_n1ql(
                self.db,
                f"SELECT d.reason AS reason, COUNT(*) AS count,"
                f" FLOOR(d.time / 300) * 300 AS time_bucket"
                f" FROM {f} AS d WHERE d.type = 'dlq' AND d.time > 0"
                f" GROUP BY d.reason, time_bucket"
                f" ORDER BY time_bucket",
            )
            timeline: dict[str, dict[str, int]] = {}  # {time_key: {reason: count}}
            for r in timeline_rows:
                bucket = r.get("time_bucket", 0)
                reason = r.get("reason", "") or "unknown"
                count = r.get("count", 0)
                if bucket:
                    time_key = time.strftime("%Y-%m-%d %H:%M", time.gmtime(bucket))
                    if time_key not in timeline:
                        timeline[time_key] = {}
                    timeline[time_key][reason] = count

            return {
                "total": total,
                "pending": pending,
                "retried": retried,
                "oldest_time": oldest_time,
                "reason_counts": reason_counts,
                "timeline": timeline,
            }
        except Exception as e:
            log_event(
                logger,
                "error",
                "DLQ",
                f"DLQ stats query failed: {type(e).__name__}: {str(e)[:200]}",
                operation="SELECT",
            )
            # Return empty stats on error
            return {
                "total": 0,
                "pending": 0,
                "retried": 0,
                "oldest_time": None,
                "reason_counts": {},
                "timeline": {},
            }

    def get_dlq_entry(self, dlq_id: str) -> dict | None:
        """Get a DLQ entry by ID. Returns dict or None if not found.

        Handles JSON parsing errors gracefully.
        """
        try:
            doc = _coll_get_doc(self.db, COLL_DLQ, dlq_id)
            if not doc:
                log_event(
                    logger,
                    "debug",
                    "DLQ",
                    "entry not found",
                    operation="SELECT",
                    doc_id=dlq_id,
                    doc_type="dlq",
                )
                return None
            props = doc.properties
            doc_data = props.get("doc_data", "{}")

            # Parse JSON with error handling
            try:
                parsed_data = json.loads(doc_data)
            except json.JSONDecodeError as e:
                log_event(
                    logger,
                    "warn",
                    "DLQ",
                    f"doc_data is malformed JSON: {e}",
                    operation="SELECT",
                    doc_id=dlq_id,
                    doc_type="dlq",
                )
                parsed_data = {}

            log_event(
                logger,
                "debug",
                "DLQ",
                "entry loaded",
                operation="SELECT",
                doc_id=dlq_id,
                doc_type="dlq",
            )
            return {
                "id": dlq_id,
                "doc_id_original": props.get("doc_id_original", ""),
                "seq": props.get("seq", ""),
                "method": props.get("method", ""),
                "status": props.get("status", 0),
                "error": props.get("error", ""),
                "reason": props.get("reason", ""),
                "time": props.get("time", 0),
                "expires_at": props.get("expires_at", 0),
                "retried": props.get("retried", False),
                "replay_attempts": props.get("replay_attempts", 0),
                "target_url": props.get("target_url", ""),
                "doc_data": parsed_data,
            }
        except Exception as e:
            log_event(
                logger,
                "error",
                "DLQ",
                f"Failed to get DLQ entry: {type(e).__name__}: {str(e)[:200]}",
                operation="SELECT",
                doc_id=dlq_id,
                doc_type="dlq",
            )
            return None

    def mark_dlq_retried(self, dlq_id: str) -> None:
        doc = _coll_get_mutable_doc(self.db, COLL_DLQ, dlq_id)
        if not doc:
            return
        doc["retried"] = True
        _coll_save_doc(self.db, COLL_DLQ, doc)
        log_event(
            logger,
            "info",
            "DLQ",
            "entry marked retried",
            operation="UPDATE",
            doc_id=dlq_id,
            doc_type="dlq",
        )

    def increment_dlq_replay_attempts(self, dlq_id: str) -> int:
        """Increment the replay_attempts counter on a DLQ entry. Returns new count."""
        doc = _coll_get_mutable_doc(self.db, COLL_DLQ, dlq_id)
        if not doc:
            return 0
        attempts = doc.properties.get("replay_attempts", 0) + 1
        doc["replay_attempts"] = attempts
        _coll_save_doc(self.db, COLL_DLQ, doc)
        return attempts

    def delete_dlq_entry(self, dlq_id: str) -> None:
        ic("delete_dlq_entry: entry", dlq_id)
        doc = _coll_get_doc(self.db, COLL_DLQ, dlq_id)
        if not doc:
            return
        _coll_purge_doc(self.db, COLL_DLQ, dlq_id)

        log_event(
            logger,
            "info",
            "DLQ",
            "entry purged",
            operation="DELETE",
            doc_id=dlq_id,
            doc_type="dlq",
        )

    def clear_dlq(self) -> None:
        rows = _run_n1ql(
            self.db,
            f"SELECT META(d).id AS id FROM {_DLQ_FROM} AS d WHERE d.type = 'dlq'",
        )
        count = 0
        with _transaction(self.db):
            for row in rows:
                dlq_id = row.get("id")
                if dlq_id:
                    _coll_purge_doc(self.db, COLL_DLQ, dlq_id)
                    count += 1
        if count > 0:
            self.update_dlq_meta("last_drained_at")
        log_event(
            logger,
            "info",
            "DLQ",
            "queue cleared",
            operation="DELETE",
            doc_type="dlq",
            doc_count=count,
        )

    def dlq_count(self) -> int:
        return (
            _run_n1ql_scalar(
                self.db, f"SELECT COUNT(*) FROM {_DLQ_FROM} AS d WHERE d.type = 'dlq'"
            )
            or 0
        )

    def purge_expired_dlq(self, max_age_seconds: int) -> int:
        """Purge DLQ entries older than max_age_seconds. Returns count purged."""
        if max_age_seconds <= 0:
            return 0
        cutoff = int(time.time()) - max_age_seconds
        rows = _run_n1ql(
            self.db,
            f"SELECT META(d).id AS id FROM {_DLQ_FROM} AS d"
            f" WHERE d.type = 'dlq' AND d.time > 0 AND d.time < $cutoff",
            {"cutoff": cutoff},
        )
        purged = 0
        with _transaction(self.db):
            for row in rows:
                dlq_id = row.get("id")
                if dlq_id:
                    _coll_purge_doc(self.db, COLL_DLQ, dlq_id)
                    purged += 1
        if purged > 0:
            log_event(
                logger,
                "info",
                "DLQ",
                "purged %d expired entries (older than %ds)"
                % (purged, max_age_seconds),
                operation="DELETE",
                doc_type="dlq",
                doc_count=purged,
            )
        return purged

    def get_dlq_meta(self) -> dict:
        """Return DLQ metadata (last_inserted_at, last_drained_at as epoch)."""
        doc = _coll_get_doc(self.db, COLL_DLQ, "dlq:meta")
        if not doc:
            return {
                "last_inserted_at": None,
                "last_drained_at": None,
                "last_inserted_job": None,
                "last_drained_job": None,
            }
        props = doc.properties
        return {
            "last_inserted_at": props.get("last_inserted_at", None),
            "last_drained_at": props.get("last_drained_at", None),
            "last_inserted_job": props.get("last_inserted_job", None),
            "last_drained_job": props.get("last_drained_job", None),
        }

    def update_dlq_meta(self, field: str, job_id: str = "") -> None:
        """Record a batch-level DLQ timestamp.

        Call once per batch — not per document — to avoid excessive writes.

        Args:
            field: ``"last_inserted_at"`` or ``"last_drained_at"``.
            job_id: checkpoint client_id or other job identifier.
        """
        now = int(time.time())
        doc = _coll_get_mutable_doc(self.db, COLL_DLQ, "dlq:meta")
        if doc:
            doc[field] = now
            if job_id:
                doc[field.replace("_at", "_job")] = job_id
        else:
            doc = MutableDocument("dlq:meta")
            doc["type"] = "dlq_meta"
            doc[field] = now
            if job_id:
                doc[field.replace("_at", "_job")] = job_id
        _coll_save_doc(self.db, COLL_DLQ, doc)
        log_event(
            logger,
            "debug",
            "DLQ",
            "meta updated",
            operation="UPDATE",
            doc_type="dlq_meta",
            field=field,
            epoch=now,
            job_id=job_id or None,
        )

    # ── Maintenance ───────────────────────────────────────────
    # Mirrors Couchbase Lite MaintenanceType:
    #   COMPACT        – remove empty pages, delete unreferenced blobs
    #   REINDEX        – rebuild all indexes
    #   INTEGRITY_CHECK – check for database corruption
    #   OPTIMIZE       – quick index stats update
    #   FULL_OPTIMIZE  – full index scan for stats

    def compact(self) -> bool:
        """Compact the database: remove empty pages and unreferenced blobs."""
        return self._run_maintenance("compact", "performMaintenance")

    def reindex(self) -> bool:
        """Rebuild all database indexes."""
        return self._run_maintenance("reindex", "performMaintenance")

    def integrity_check(self) -> bool:
        """Check database for corruption."""
        return self._run_maintenance("integrity_check", "performMaintenance")

    def optimize(self) -> bool:
        """Quick update of index statistics for query optimization."""
        return self._run_maintenance("optimize", "performMaintenance")

    def full_optimize(self) -> bool:
        """Full index scan to gather comprehensive query statistics."""
        return self._run_maintenance("full_optimize", "performMaintenance")

    # Map maintenance type names to CBL C API enum constants
    _MAINT_TYPES = {
        "compact": "kCBLMaintenanceTypeCompact",
        "reindex": "kCBLMaintenanceTypeReindex",
        "integrity_check": "kCBLMaintenanceTypeIntegrityCheck",
        "optimize": "kCBLMaintenanceTypeOptimize",
        "full_optimize": "kCBLMaintenanceTypeFullOptimize",
    }

    def _run_maintenance(self, maint_type: str, method_name: str = "") -> bool:
        """Run a CBL maintenance operation via the C API directly."""
        enum_name = self._MAINT_TYPES.get(maint_type)
        if not enum_name or not hasattr(lib, enum_name):
            log_event(
                logger,
                "warn",
                "CBL",
                "unknown maintenance type: %s" % maint_type,
                maintenance_type=maint_type,
            )
            return False

        size_before = _db_size_mb()
        t0 = time.monotonic()

        try:
            err = ffi.new("CBLError*")
            ok = lib.CBLDatabase_PerformMaintenance(
                self.db._ref, getattr(lib, enum_name), err
            )
            elapsed = (time.monotonic() - t0) * 1000

            if not ok:
                log_event(
                    logger,
                    "error",
                    "CBL",
                    "maintenance failed: %s" % maint_type,
                    operation="MAINTENANCE",
                    maintenance_type=maint_type,
                    db_name=CBL_DB_NAME,
                    duration_ms=round(elapsed, 1),
                )
                return False

            size_after = _db_size_mb()
            log_event(
                logger,
                "info",
                "CBL",
                "maintenance completed: %s" % maint_type,
                operation="MAINTENANCE",
                maintenance_type=maint_type,
                db_name=CBL_DB_NAME,
                db_size_mb=size_after,
                duration_ms=round(elapsed, 1),
            )
            if maint_type == "compact" and size_before > 0:
                saved = size_before - size_after
                if saved > 0.01:
                    log_event(
                        logger,
                        "info",
                        "CBL",
                        "compact freed %.2f MB (%.1f%% reduction)"
                        % (saved, (saved / size_before) * 100),
                        maintenance_type="compact",
                        db_size_mb=size_after,
                    )
            return True

        except Exception as exc:
            elapsed = (time.monotonic() - t0) * 1000
            log_event(
                logger,
                "error",
                "CBL",
                "maintenance failed: %s – %s" % (maint_type, exc),
                operation="MAINTENANCE",
                maintenance_type=maint_type,
                db_name=CBL_DB_NAME,
                duration_ms=round(elapsed, 1),
                error_detail=str(exc)[:200],
            )
            return False

    def run_all_maintenance(self) -> dict[str, bool]:
        """Run the recommended maintenance suite: compact + optimize."""
        results = {}
        for op in ("compact", "optimize"):
            results[op] = getattr(self, op)()
        return results

    # ── Inputs/Outputs (v2.0) ──────────────────────────────────

    def load_inputs_changes(self) -> dict | None:
        """Load input source definitions from 'inputs_changes' document."""
        doc_id = "inputs_changes"
        ic("load_inputs_changes: entry", doc_id)
        t0 = time.monotonic()
        doc = _coll_get_doc(self.db, COLL_INPUTS_CHANGES, doc_id)
        elapsed = (time.monotonic() - t0) * 1000
        if not doc:
            log_event(
                logger,
                "debug",
                "CBL",
                "inputs_changes not found",
                operation="SELECT",
                doc_id=doc_id,
                doc_type="inputs_changes",
                duration_ms=round(elapsed, 1),
            )
            return None
        result = {
            "type": doc.get("type", "inputs_changes"),
            "src": list(doc.get("src") or []),
        }
        if "_meta" in doc:
            result["_meta"] = dict(doc["_meta"])
        log_event(
            logger,
            "debug",
            "CBL",
            "inputs_changes loaded",
            operation="SELECT",
            doc_id=doc_id,
            doc_type="inputs_changes",
            duration_ms=round(elapsed, 1),
        )
        return result

    def save_inputs_changes(self, data: dict) -> None:
        """Save input source definitions to 'inputs_changes' document."""
        doc_id = "inputs_changes"
        ic("save_inputs_changes: entry", doc_id)
        t0 = time.monotonic()
        doc = _coll_get_mutable_doc(self.db, COLL_INPUTS_CHANGES, doc_id)
        is_new = doc is None
        if not doc:
            doc = MutableDocument(doc_id)
        doc["type"] = "inputs_changes"
        doc["src"] = data.get("src", [])
        doc["updated_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        if "_meta" in data:
            doc["_meta"] = data["_meta"]
        _coll_save_doc(self.db, COLL_INPUTS_CHANGES, doc)
        elapsed = (time.monotonic() - t0) * 1000
        log_event(
            logger,
            "info",
            "CBL",
            "inputs_changes saved",
            operation="INSERT" if is_new else "UPDATE",
            doc_id=doc_id,
            doc_type="inputs_changes",
            duration_ms=round(elapsed, 1),
        )

    def load_outputs(self, output_type: str) -> dict | None:
        """Load output definitions for a given type (rdbms/http/cloud/stdout)."""
        coll_map = {
            "rdbms": COLL_OUTPUTS_RDBMS,
            "http": COLL_OUTPUTS_HTTP,
            "cloud": COLL_OUTPUTS_CLOUD,
            "stdout": COLL_OUTPUTS_STDOUT,
        }
        if output_type not in coll_map:
            raise ValueError(f"Invalid output_type: {output_type}")
        coll_name = coll_map[output_type]
        doc_id = f"outputs_{output_type}"
        ic("load_outputs: entry", output_type, doc_id)
        t0 = time.monotonic()
        doc = _coll_get_doc(self.db, coll_name, doc_id)
        elapsed = (time.monotonic() - t0) * 1000
        if not doc:
            log_event(
                logger,
                "debug",
                "CBL",
                f"outputs_{output_type} not found",
                operation="SELECT",
                doc_id=doc_id,
                output_type=output_type,
                duration_ms=round(elapsed, 1),
            )
            return None
        result = {
            "type": doc.get("type", f"outputs_{output_type}"),
            "src": list(doc.get("src") or []),
        }
        if "_meta" in doc:
            result["_meta"] = dict(doc["_meta"])
        log_event(
            logger,
            "debug",
            "CBL",
            f"outputs_{output_type} loaded",
            operation="SELECT",
            doc_id=doc_id,
            output_type=output_type,
            duration_ms=round(elapsed, 1),
        )
        return result

    def save_outputs(self, output_type: str, data: dict) -> None:
        """Save output definitions for a given type (rdbms/http/cloud/stdout)."""
        coll_map = {
            "rdbms": COLL_OUTPUTS_RDBMS,
            "http": COLL_OUTPUTS_HTTP,
            "cloud": COLL_OUTPUTS_CLOUD,
            "stdout": COLL_OUTPUTS_STDOUT,
        }
        if output_type not in coll_map:
            raise ValueError(f"Invalid output_type: {output_type}")
        coll_name = coll_map[output_type]
        doc_id = f"outputs_{output_type}"
        ic("save_outputs: entry", output_type, doc_id)
        t0 = time.monotonic()
        doc = _coll_get_mutable_doc(self.db, coll_name, doc_id)
        is_new = doc is None
        if not doc:
            doc = MutableDocument(doc_id)
        doc["type"] = f"outputs_{output_type}"
        doc["src"] = data.get("src", [])
        doc["updated_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        if "_meta" in data:
            doc["_meta"] = data["_meta"]
        _coll_save_doc(self.db, coll_name, doc)
        elapsed = (time.monotonic() - t0) * 1000
        log_event(
            logger,
            "info",
            "CBL",
            f"outputs_{output_type} saved",
            operation="INSERT" if is_new else "UPDATE",
            doc_id=doc_id,
            output_type=output_type,
            duration_ms=round(elapsed, 1),
        )

    # ── Jobs (v2.0) ────────────────────────────────────────────

    def load_job(self, job_id: str) -> dict | None:
        """Load a job definition."""
        doc_id = f"job::{job_id}"
        ic("load_job: entry", job_id, doc_id)
        t0 = time.monotonic()
        doc = _coll_get_doc(self.db, COLL_JOBS, doc_id)
        elapsed = (time.monotonic() - t0) * 1000
        if not doc:
            log_event(
                logger,
                "debug",
                "CBL",
                "job not found",
                operation="SELECT",
                doc_id=doc_id,
                job_id=job_id,
                duration_ms=round(elapsed, 1),
            )
            return None
        result = doc.properties.copy() if hasattr(doc, "properties") else dict(doc)
        log_event(
            logger,
            "debug",
            "CBL",
            "job loaded",
            operation="SELECT",
            doc_id=doc_id,
            job_id=job_id,
            duration_ms=round(elapsed, 1),
        )
        return result

    def save_job(self, job_id: str, job_data: dict) -> None:
        """Save a job definition."""
        doc_id = f"job::{job_id}"
        ic("save_job: entry", job_id, doc_id)
        t0 = time.monotonic()
        doc = _coll_get_mutable_doc(self.db, COLL_JOBS, doc_id)
        is_new = doc is None
        if not doc:
            doc = MutableDocument(doc_id)
        doc["type"] = "job"
        doc["id"] = job_id
        doc["inputs"] = job_data.get("inputs", [])
        doc["outputs"] = job_data.get("outputs", [])
        doc["output_type"] = job_data.get("output_type")
        doc["system"] = job_data.get("system", {})
        doc["state"] = job_data.get("state", {})
        doc["created_at"] = job_data.get(
            "created_at",
            datetime.datetime.now(datetime.timezone.utc).isoformat(),
        )
        doc["updated_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        if "_meta" in job_data:
            doc["_meta"] = job_data["_meta"]
        _coll_save_doc(self.db, COLL_JOBS, doc)
        elapsed = (time.monotonic() - t0) * 1000
        log_event(
            logger,
            "info",
            "CBL",
            "job saved",
            operation="INSERT" if is_new else "UPDATE",
            doc_id=doc_id,
            job_id=job_id,
            duration_ms=round(elapsed, 1),
        )

    def delete_job(self, job_id: str) -> None:
        """Delete a job definition."""
        doc_id = f"job::{job_id}"
        ic("delete_job: entry", job_id, doc_id)
        t0 = time.monotonic()
        doc = _coll_get_doc(self.db, COLL_JOBS, doc_id)
        elapsed = (time.monotonic() - t0) * 1000
        if not doc:
            log_event(
                logger,
                "debug",
                "CBL",
                "job not found for delete",
                operation="DELETE",
                doc_id=doc_id,
                job_id=job_id,
                duration_ms=round(elapsed, 1),
            )
            return
        _coll_purge_doc(self.db, COLL_JOBS, doc_id)
        log_event(
            logger,
            "info",
            "CBL",
            "job deleted",
            operation="DELETE",
            doc_id=doc_id,
            job_id=job_id,
        )

    def list_jobs(self) -> list[dict]:
        """List all jobs with their IDs and types."""
        ic("list_jobs: entry")
        t0 = time.monotonic()
        query = f"""
            SELECT _id, type, id, state, created_at, updated_at
            FROM {CBL_SCOPE}.{COLL_JOBS}
            WHERE type = 'job'
            ORDER BY updated_at DESC
        """
        results = _run_n1ql(self.db, query)
        elapsed = (time.monotonic() - t0) * 1000
        jobs = []
        for row in results:
            jobs.append(
                {
                    "doc_id": row.get("_id"),
                    "type": row.get("type"),
                    "id": row.get("id"),
                    "state": row.get("state", {}),
                    "created_at": row.get("created_at"),
                    "updated_at": row.get("updated_at"),
                }
            )
        log_event(
            logger,
            "debug",
            "CBL",
            f"listed {len(jobs)} jobs",
            operation="SELECT",
            doc_type="job",
            count=len(jobs),
            duration_ms=round(elapsed, 1),
        )
        return jobs

    def update_job_state(self, job_id: str, state: dict) -> None:
        """Update the runtime state of a job."""
        doc_id = f"job::{job_id}"
        ic("update_job_state: entry", job_id, doc_id)
        t0 = time.monotonic()
        doc = _coll_get_mutable_doc(self.db, COLL_JOBS, doc_id)
        elapsed = (time.monotonic() - t0) * 1000
        if not doc:
            log_event(
                logger,
                "error",
                "CBL",
                "job not found for state update",
                operation="UPDATE",
                doc_id=doc_id,
                job_id=job_id,
            )
            raise RuntimeError(f"Job {job_id} not found")
        doc["state"] = state
        doc["updated_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        _coll_save_doc(self.db, COLL_JOBS, doc)
        elapsed = (time.monotonic() - t0) * 1000
        log_event(
            logger,
            "info",
            "CBL",
            "job state updated",
            operation="UPDATE",
            doc_id=doc_id,
            job_id=job_id,
            duration_ms=round(elapsed, 1),
        )

    # ── Checkpoints (v2.0) ─────────────────────────────────────

    def load_checkpoint(self, job_id: str) -> dict | None:
        """Load checkpoint for a specific job."""
        doc_id = f"checkpoint::{job_id}"
        ic("load_checkpoint: entry", job_id, doc_id)
        t0 = time.monotonic()
        doc = _coll_get_doc(self.db, COLL_CHECKPOINTS, doc_id)
        elapsed = (time.monotonic() - t0) * 1000
        if not doc:
            log_event(
                logger,
                "debug",
                "CBL",
                "checkpoint not found",
                operation="SELECT",
                doc_id=doc_id,
                job_id=job_id,
                duration_ms=round(elapsed, 1),
            )
            return None
        result = doc.properties.copy() if hasattr(doc, "properties") else dict(doc)
        log_event(
            logger,
            "debug",
            "CBL",
            "checkpoint loaded",
            operation="SELECT",
            doc_id=doc_id,
            job_id=job_id,
            duration_ms=round(elapsed, 1),
        )
        return result

    def save_checkpoint(self, job_id: str, data: dict) -> None:
        """Save checkpoint for a specific job."""
        doc_id = f"checkpoint::{job_id}"
        ic("save_checkpoint: entry", job_id, doc_id)
        t0 = time.monotonic()
        doc = _coll_get_mutable_doc(self.db, COLL_CHECKPOINTS, doc_id)
        is_new = doc is None
        if not doc:
            doc = MutableDocument(doc_id)
        doc["type"] = "checkpoint"
        doc["job_id"] = job_id
        doc["last_seq"] = data.get("last_seq", "0")
        doc["remote_counter"] = data.get("remote_counter", 0)
        doc["updated_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        if "_meta" in data:
            doc["_meta"] = data["_meta"]
        _coll_save_doc(self.db, COLL_CHECKPOINTS, doc)
        elapsed = (time.monotonic() - t0) * 1000
        log_event(
            logger,
            "info",
            "CBL",
            "checkpoint saved",
            operation="INSERT" if is_new else "UPDATE",
            doc_id=doc_id,
            job_id=job_id,
            last_seq=data.get("last_seq"),
            duration_ms=round(elapsed, 1),
        )

    def delete_checkpoint(self, job_id: str) -> None:
        """Delete checkpoint for a specific job."""
        doc_id = f"checkpoint::{job_id}"
        ic("delete_checkpoint: entry", job_id, doc_id)
        t0 = time.monotonic()
        doc = _coll_get_doc(self.db, COLL_CHECKPOINTS, doc_id)
        elapsed = (time.monotonic() - t0) * 1000
        if not doc:
            log_event(
                logger,
                "debug",
                "CBL",
                "checkpoint not found for delete",
                operation="DELETE",
                doc_id=doc_id,
                job_id=job_id,
                duration_ms=round(elapsed, 1),
            )
            return
        _coll_purge_doc(self.db, COLL_CHECKPOINTS, doc_id)
        log_event(
            logger,
            "info",
            "CBL",
            "checkpoint deleted",
            operation="DELETE",
            doc_id=doc_id,
            job_id=job_id,
        )

    # ── Sessions (v2.0) ────────────────────────────────────────

    def save_session(self, session_id: str, data: dict) -> None:
        """Save a session document."""
        doc_id = f"session::{session_id}"
        ic("save_session: entry", session_id, doc_id)
        t0 = time.monotonic()
        doc = _coll_get_mutable_doc(self.db, COLL_SESSIONS, doc_id)
        is_new = doc is None
        if not doc:
            doc = MutableDocument(doc_id)
        doc["type"] = "session"
        doc["session_id"] = session_id
        doc["cookie"] = data.get("cookie")
        doc["expires_at"] = data.get("expires_at")
        doc["created_at"] = data.get("created_at", int(time.time()))
        doc["updated_at"] = int(time.time())
        if "_meta" in data:
            doc["_meta"] = data["_meta"]
        _coll_save_doc(self.db, COLL_SESSIONS, doc)
        elapsed = (time.monotonic() - t0) * 1000
        log_event(
            logger,
            "info",
            "CBL",
            "session saved",
            operation="INSERT" if is_new else "UPDATE",
            doc_id=doc_id,
            session_id=session_id,
            duration_ms=round(elapsed, 1),
        )

    def load_session(self, session_id: str) -> dict | None:
        """Load a session document."""
        doc_id = f"session::{session_id}"
        ic("load_session: entry", session_id, doc_id)
        t0 = time.monotonic()
        doc = _coll_get_doc(self.db, COLL_SESSIONS, doc_id)
        elapsed = (time.monotonic() - t0) * 1000
        if not doc:
            log_event(
                logger,
                "debug",
                "CBL",
                "session not found",
                operation="SELECT",
                doc_id=doc_id,
                session_id=session_id,
                duration_ms=round(elapsed, 1),
            )
            return None
        result = doc.properties.copy() if hasattr(doc, "properties") else dict(doc)
        log_event(
            logger,
            "debug",
            "CBL",
            "session loaded",
            operation="SELECT",
            doc_id=doc_id,
            session_id=session_id,
            duration_ms=round(elapsed, 1),
        )
        return result

    def list_sessions(self) -> list[dict]:
        """List all active sessions."""
        ic("list_sessions: entry")
        t0 = time.monotonic()
        query = f"""
            SELECT _id, type, session_id, expires_at, created_at
            FROM {CBL_SCOPE}.{COLL_SESSIONS}
            WHERE type = 'session'
            ORDER BY updated_at DESC
        """
        results = _run_n1ql(self.db, query)
        elapsed = (time.monotonic() - t0) * 1000
        sessions = []
        for row in results:
            sessions.append(
                {
                    "doc_id": row.get("_id"),
                    "type": row.get("type"),
                    "session_id": row.get("session_id"),
                    "expires_at": row.get("expires_at"),
                    "created_at": row.get("created_at"),
                }
            )
        log_event(
            logger,
            "debug",
            "CBL",
            f"listed {len(sessions)} sessions",
            operation="SELECT",
            doc_type="session",
            count=len(sessions),
            duration_ms=round(elapsed, 1),
        )
        return sessions

    def delete_expired_sessions(self) -> int:
        """Delete all sessions that have expired."""
        ic("delete_expired_sessions: entry")
        now = int(time.time())
        t0 = time.monotonic()
        query = f"""
            SELECT _id FROM {CBL_SCOPE}.{COLL_SESSIONS}
            WHERE type = 'session' AND expires_at < {now}
        """
        results = _run_n1ql(self.db, query)
        count = 0

        def _delete_expired_in_txn():
            nonlocal count
            for row in results:
                doc_id = row.get("_id")
                if doc_id:
                    _coll_purge_doc(self.db, COLL_SESSIONS, doc_id)
                    count += 1

        with _transaction(self.db):
            _delete_expired_in_txn()

        elapsed = (time.monotonic() - t0) * 1000
        log_event(
            logger,
            "info",
            "CBL",
            f"deleted {count} expired sessions",
            operation="DELETE",
            doc_type="session",
            count=count,
            duration_ms=round(elapsed, 1),
        )
        return count

    # ── Data Quality (v2.0) ────────────────────────────────────

    def add_data_quality_entry(self, job_id: str, entry: dict) -> None:
        """Log a data quality issue (e.g., value coercion)."""
        timestamp = int(time.time() * 1000)
        doc_id = f"dq::{job_id}::{entry.get('doc_id')}::{timestamp}"
        ic("add_data_quality_entry: entry", job_id, doc_id)
        t0 = time.monotonic()
        doc = MutableDocument(doc_id)
        doc["type"] = "data_quality"
        doc["job_id"] = job_id
        doc["doc_id"] = entry.get("doc_id")
        doc["table_name"] = entry.get("table_name")
        doc["column_name"] = entry.get("column_name")
        doc["original_value"] = entry.get("original_value")
        doc["coerced_value"] = entry.get("coerced_value")
        doc["coerce_type"] = entry.get("coerce_type")
        doc["timestamp"] = timestamp
        doc["created_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        _coll_save_doc(self.db, COLL_DATA_QUALITY, doc)
        elapsed = (time.monotonic() - t0) * 1000
        log_event(
            logger,
            "debug",
            "CBL",
            "data quality entry logged",
            operation="INSERT",
            doc_id=doc_id,
            job_id=job_id,
            coerce_type=entry.get("coerce_type"),
            duration_ms=round(elapsed, 1),
        )

    def list_data_quality(self, job_id: str | None = None) -> list[dict]:
        """List data quality entries, optionally filtered by job."""
        ic("list_data_quality: entry", job_id)
        t0 = time.monotonic()
        if job_id:
            query = f"""
                SELECT _id, type, job_id, doc_id, table_name, column_name,
                       original_value, coerced_value, coerce_type, timestamp
                FROM {CBL_SCOPE}.{COLL_DATA_QUALITY}
                WHERE type = 'data_quality' AND job_id = '{job_id}'
                ORDER BY timestamp DESC
            """
        else:
            query = f"""
                SELECT _id, type, job_id, doc_id, table_name, column_name,
                       original_value, coerced_value, coerce_type, timestamp
                FROM {CBL_SCOPE}.{COLL_DATA_QUALITY}
                WHERE type = 'data_quality'
                ORDER BY timestamp DESC
            """
        results = _run_n1ql(self.db, query)
        elapsed = (time.monotonic() - t0) * 1000
        entries = []
        for row in results:
            entries.append(
                {
                    "doc_id": row.get("_id"),
                    "type": row.get("type"),
                    "job_id": row.get("job_id"),
                    "source_doc_id": row.get("doc_id"),
                    "table_name": row.get("table_name"),
                    "column_name": row.get("column_name"),
                    "original_value": row.get("original_value"),
                    "coerced_value": row.get("coerced_value"),
                    "coerce_type": row.get("coerce_type"),
                    "timestamp": row.get("timestamp"),
                }
            )
        log_event(
            logger,
            "debug",
            "CBL",
            f"listed {len(entries)} data quality entries",
            operation="SELECT",
            doc_type="data_quality",
            job_id=job_id,
            count=len(entries),
            duration_ms=round(elapsed, 1),
        )
        return entries

    # ── Enrichments (v2.0) ─────────────────────────────────────

    def add_enrichment(self, job_id: str, enrichment: dict) -> None:
        """Log an enrichment result (e.g., ML analysis, external API)."""
        timestamp = int(time.time() * 1000)
        doc_id = f"enrich::{job_id}::{enrichment.get('doc_id')}::{timestamp}"
        ic("add_enrichment: entry", job_id, doc_id)
        t0 = time.monotonic()
        doc = MutableDocument(doc_id)
        doc["type"] = "enrichment"
        doc["job_id"] = job_id
        doc["doc_id"] = enrichment.get("doc_id")
        doc["source"] = enrichment.get("source")
        doc["status"] = enrichment.get("status")
        doc["result"] = enrichment.get("result")
        doc["timestamp"] = timestamp
        doc["created_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        _coll_save_doc(self.db, COLL_ENRICHMENTS, doc)
        elapsed = (time.monotonic() - t0) * 1000
        log_event(
            logger,
            "debug",
            "CBL",
            "enrichment logged",
            operation="INSERT",
            doc_id=doc_id,
            job_id=job_id,
            source=enrichment.get("source"),
            status=enrichment.get("status"),
            duration_ms=round(elapsed, 1),
        )

    def list_enrichments(
        self, job_id: str | None = None, source: str | None = None
    ) -> list[dict]:
        """List enrichments, optionally filtered by job and/or source."""
        ic("list_enrichments: entry", job_id, source)
        t0 = time.monotonic()
        query_parts = [
            f"SELECT _id, type, job_id, doc_id, source, status, result, timestamp",
            f"FROM {CBL_SCOPE}.{COLL_ENRICHMENTS}",
            "WHERE type = 'enrichment'",
        ]
        if job_id:
            query_parts.append(f"AND job_id = '{job_id}'")
        if source:
            query_parts.append(f"AND source = '{source}'")
        query_parts.append("ORDER BY timestamp DESC")
        query = " ".join(query_parts)
        results = _run_n1ql(self.db, query)
        elapsed = (time.monotonic() - t0) * 1000
        enrichments = []
        for row in results:
            enrichments.append(
                {
                    "doc_id": row.get("_id"),
                    "type": row.get("type"),
                    "job_id": row.get("job_id"),
                    "source_doc_id": row.get("doc_id"),
                    "source": row.get("source"),
                    "status": row.get("status"),
                    "result": row.get("result"),
                    "timestamp": row.get("timestamp"),
                }
            )
        log_event(
            logger,
            "debug",
            "CBL",
            f"listed {len(enrichments)} enrichments",
            operation="SELECT",
            doc_type="enrichment",
            job_id=job_id,
            source=source,
            count=len(enrichments),
            duration_ms=round(elapsed, 1),
        )
        return enrichments

    # ── Migration (v1.x → v2.0) ────────────────────────────────────

    def migrate_v1_to_v2(self) -> bool:
        """Migrate v1.x config to v2.0 schema.

        Reads the 'config' document. If it has v1.x structure (contains 'gateway', 'output', etc.),
        extract into v2.0 documents:
          - inputs_changes: from config.gateway + config.auth + config.changes_feed
          - outputs_{type}: from config.output (split by mode)
          - job: from the above + config.processing/checkpoint/retry/attachments
          - checkpoint: from config.checkpoint (renamed to checkpoint::{job_uuid})

        Sets config.schema_version = "2.0" to prevent re-migration.

        Returns:
            True if migration was performed; False if config is already v2.0 or doesn't exist.
        """
        import uuid
        from pathlib import Path

        ic("migrate_v1_to_v2: entry")
        t0 = time.monotonic()

        # Load config
        config = self.load_config()
        if not config:
            log_event(
                logger,
                "debug",
                "CBL",
                "no config found — skipping v1→v2 migration",
                operation="MIGRATE",
            )
            return False

        # Check if already migrated
        if config.get("schema_version") == "2.0":
            log_event(
                logger,
                "debug",
                "CBL",
                "config already migrated to v2.0",
                operation="MIGRATE",
            )
            return False

        # Check if this is actually a v1.x config (has 'gateway' or 'output')
        if "gateway" not in config and "output" not in config:
            # Looks like a v2.0 config (or unknown format) — mark as migrated
            config["schema_version"] = "2.0"
            self.save_config(config)
            log_event(
                logger,
                "info",
                "CBL",
                "config doesn't match v1.x or v2.0 pattern — marked v2.0",
                operation="MIGRATE",
            )
            return False

        # ─────────────────────────────────────────────────────────────
        # Extract v1.x inputs → v2.0 inputs_changes
        # ─────────────────────────────────────────────────────────────
        inputs_changes_doc = None
        if "gateway" in config or "auth" in config or "changes_feed" in config:
            gateway = config.get("gateway", {})
            auth = config.get("auth", {})
            changes_feed = config.get("changes_feed", {})

            src_id = (
                gateway.get("database", "db")
                + "_"
                + gateway.get("collection", "collection")
            )
            src_entry = {
                "id": src_id,
                "name": "Migrated from v1.x",
                "enabled": True,
                "source_type": gateway.get("src", "sync_gateway"),
                "host": gateway.get("url", ""),
                "database": gateway.get("database", "db"),
                "scope": gateway.get("scope", ""),
                "collection": gateway.get("collection", ""),
                "accept_self_signed_certs": gateway.get(
                    "accept_self_signed_certs", False
                ),
                "auth": {
                    "method": auth.get("method", "basic"),
                    "username": auth.get("username", ""),
                    "password": auth.get("password", ""),
                    "session_cookie": auth.get("session_cookie", ""),
                    "bearer_token": auth.get("bearer_token", ""),
                },
                "changes_feed": {
                    "feed_type": changes_feed.get("feed_type", "longpoll"),
                    "poll_interval_seconds": changes_feed.get(
                        "poll_interval_seconds", 10
                    ),
                    "active_only": changes_feed.get("active_only", True),
                    "include_docs": changes_feed.get("include_docs", False),
                    "since": changes_feed.get("since", "0"),
                    "channels": changes_feed.get("channels", []),
                    "limit": changes_feed.get("limit", 0),
                    "heartbeat_ms": changes_feed.get("heartbeat_ms", 30000),
                    "timeout_ms": changes_feed.get("timeout_ms", 60000),
                    "http_timeout_seconds": changes_feed.get(
                        "http_timeout_seconds", 300
                    ),
                    "throttle_feed": changes_feed.get("throttle_feed", 5000),
                    "continuous_catchup_limit": changes_feed.get(
                        "continuous_catchup_limit", 5000
                    ),
                    "flood_threshold": changes_feed.get("flood_threshold", 10000),
                    "optimize_initial_sync": changes_feed.get(
                        "optimize_initial_sync", False
                    ),
                },
            }
            inputs_changes_doc = {
                "type": "inputs_changes",
                "src": [src_entry],
            }
            self.save_inputs_changes(inputs_changes_doc)
            log_event(
                logger,
                "info",
                "CBL",
                "migrated v1.x gateway → inputs_changes",
                operation="MIGRATE",
                src_id=src_id,
            )

        # ─────────────────────────────────────────────────────────────
        # Extract v1.x output → v2.0 outputs_{type}
        # ─────────────────────────────────────────────────────────────
        output_type = None
        output_entry = None
        if "output" in config:
            output_cfg = config["output"]
            mode = output_cfg.get("mode", "postgres")

            # Determine output type from mode
            if mode in ("postgres", "mysql", "mssql", "oracle", "db"):
                output_type = "rdbms"
                output_entry = {
                    "id": f"output_{mode}",
                    "name": f"Migrated {mode} output",
                    "enabled": True,
                    "engine": mode,
                    "host": output_cfg[mode].get("host", "")
                    if mode in output_cfg
                    else "",
                    "port": output_cfg[mode].get("port", 5432)
                    if mode in output_cfg
                    else 5432,
                    "database": output_cfg[mode].get("database", "")
                    if mode in output_cfg
                    else "",
                    "user": output_cfg[mode].get("user", "")
                    if mode in output_cfg
                    else "",
                    "password": output_cfg[mode].get("password", "")
                    if mode in output_cfg
                    else "",
                    "schema": output_cfg[mode].get("schema", "public")
                    if mode in output_cfg
                    else "public",
                    "ssl": output_cfg[mode].get("ssl", False)
                    if mode in output_cfg
                    else False,
                    "pool_min": output_cfg[mode].get("pool_min", 2)
                    if mode in output_cfg
                    else 2,
                    "pool_max": output_cfg[mode].get("pool_max", 10)
                    if mode in output_cfg
                    else 10,
                }
            elif mode == "http":
                output_type = "http"
                output_entry = {
                    "id": "output_http",
                    "name": "Migrated HTTP output",
                    "enabled": True,
                    "target_url": output_cfg.get("target_url", ""),
                    "url_template": output_cfg.get(
                        "url_template", "{target_url}/{doc_id}"
                    ),
                    "write_method": output_cfg.get("write_method", "PUT"),
                    "delete_method": output_cfg.get("delete_method", "DELETE"),
                    "send_delete_body": output_cfg.get("send_delete_body", False),
                    "request_timeout_seconds": output_cfg.get(
                        "request_timeout_seconds", 30
                    ),
                    "accept_self_signed_certs": output_cfg.get(
                        "accept_self_signed_certs", False
                    ),
                    "follow_redirects": output_cfg.get("follow_redirects", False),
                    "auth": output_cfg.get("target_auth", {"method": "none"}),
                    "retry": output_cfg.get("retry", {"max_retries": 3}),
                    "halt_on_failure": output_cfg.get("halt_on_failure", True),
                    "request_options": output_cfg.get("request_options", {}),
                }
            elif mode == "s3":
                output_type = "cloud"
                output_entry = {
                    "id": "output_s3",
                    "name": "Migrated S3 output",
                    "enabled": True,
                    "provider": "s3",
                    **output_cfg.get("s3", {}),
                }
            elif mode == "stdout":
                output_type = "stdout"
                output_entry = {
                    "id": "output_stdout",
                    "name": "Migrated stdout output",
                    "enabled": True,
                    "output_format": output_cfg.get("output_format", "json"),
                    "pretty_print": False,
                }

            if output_type and output_entry:
                outputs_doc = {
                    "type": f"outputs_{output_type}",
                    "src": [output_entry],
                }
                self.save_outputs(output_type, outputs_doc)
                log_event(
                    logger,
                    "info",
                    "CBL",
                    f"migrated v1.x output → outputs_{output_type}",
                    operation="MIGRATE",
                    output_type=output_type,
                )

        # ─────────────────────────────────────────────────────────────
        # Extract existing checkpoint + mappings
        # ─────────────────────────────────────────────────────────────
        job_uuid = str(uuid.uuid4())

        # Load checkpoint from file if it exists (will be saved per-job)
        checkpoint_data = {}
        cp_path = Path("checkpoint.json")
        if cp_path.exists():
            try:
                cp_file_data = json.loads(cp_path.read_text())
                checkpoint_data = {
                    "last_seq": cp_file_data.get("SGs_Seq", "0"),
                    "remote_counter": cp_file_data.get("remote_counter", 0),
                }
            except Exception as e:
                log_event(
                    logger,
                    "warn",
                    "CBL",
                    f"failed to read checkpoint.json: {e}",
                    operation="MIGRATE",
                )

        # Load mappings from mappings collection (if any)
        schema_mapping = {}
        try:
            mapping_entries = self.list_mappings()
            if mapping_entries:
                # Take the first mapping (v1.x assumed one mapping per config)
                first = mapping_entries[0]
                mapping_content = self.get_mapping(first.get("name", ""))
                if mapping_content:
                    try:
                        schema_mapping = json.loads(mapping_content)
                    except json.JSONDecodeError:
                        schema_mapping = {"raw": mapping_content}
        except Exception as e:
            log_event(
                logger,
                "warn",
                "CBL",
                f"failed to read mappings: {e}",
                operation="MIGRATE",
            )

        # ─────────────────────────────────────────────────────────────
        # Create job document (v2.0)
        # ─────────────────────────────────────────────────────────────
        job_data = {
            "id": job_uuid,
            "name": "Migrated Job",
            "enabled": True,
            "inputs": [inputs_changes_doc["src"][0]] if inputs_changes_doc else [],
            "outputs": [output_entry] if output_entry else [],
            "output_type": output_type or "stdout",
            "system": {
                "threads": config.get("threads", 4),
                "processing": config.get("processing", {}),
                "retry": config.get("retry", {}),
                "attachments": config.get("attachments", {}),
                "middleware": [],
                "middleware_threads": 2,
            },
            "schema_mapping": schema_mapping,
            "state": {
                "status": "stopped",
            },
        }

        self.save_job(job_uuid, job_data)
        log_event(
            logger,
            "info",
            "CBL",
            "created v2.0 job from v1.x config",
            operation="MIGRATE",
            job_id=job_uuid,
            output_type=output_type,
        )

        # Save checkpoint for the job
        if checkpoint_data:
            self.save_checkpoint(job_uuid, checkpoint_data)
            log_event(
                logger,
                "info",
                "CBL",
                f"migrated checkpoint to job {job_uuid}",
                operation="MIGRATE",
                job_id=job_uuid,
            )

        # ─────────────────────────────────────────────────────────────
        # Slim down the config document (remove pipeline sections)
        # ─────────────────────────────────────────────────────────────
        slimmed_config = {
            "schema_version": "2.0",
            "admin_ui": config.get("admin_ui", {}),
            "metrics": config.get("metrics", {}),
            "logging": config.get("logging", {}),
            "couchbase_lite": config.get("couchbase_lite", {}),
            "shutdown": config.get("shutdown", {}),
        }
        self.save_config(slimmed_config)
        log_event(
            logger,
            "info",
            "CBL",
            "slimmed config to v2.0 (infra-only)",
            operation="MIGRATE",
        )

        elapsed = (time.monotonic() - t0) * 1000
        log_event(
            logger,
            "info",
            "CBL",
            "migration v1.x → v2.0 complete",
            operation="MIGRATE",
            job_id=job_uuid,
            duration_ms=round(elapsed, 1),
        )

        return True


# ---------------------------------------------------------------------------
# Scheduled maintenance
# ---------------------------------------------------------------------------
class CBLMaintenanceScheduler:
    """
    Periodically runs CBL maintenance (compact + optimize) in a background
    thread.

    Usage:
        scheduler = CBLMaintenanceScheduler(interval_hours=24)
        scheduler.start()
        ...
        scheduler.stop()   # on shutdown
    """

    def __init__(self, interval_hours: float = 24.0):
        self.interval_seconds = interval_hours * 3600
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run_loop, daemon=True, name="cbl-maintenance"
        )
        self._thread.start()
        log_event(
            logger,
            "info",
            "CBL",
            "maintenance scheduler started (every %.1f hours)"
            % (self.interval_seconds / 3600),
            operation="OPEN",
        )

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        log_event(
            logger, "info", "CBL", "maintenance scheduler stopped", operation="CLOSE"
        )

    def _run_loop(self) -> None:
        while not self._stop_event.wait(timeout=self.interval_seconds):
            try:
                ic("_run_loop: before maintenance")
                store = CBLStore()
                info = store.db_info()
                log_event(
                    logger,
                    "info",
                    "CBL",
                    "scheduled maintenance starting",
                    operation="MAINTENANCE",
                    db_size_mb=info["db_size_mb"],
                )
                results = store.run_all_maintenance()
                ic("_run_loop: after maintenance", results)
                log_event(
                    logger,
                    "info",
                    "CBL",
                    "scheduled maintenance complete: %s" % results,
                    operation="MAINTENANCE",
                )
            except Exception as exc:
                log_event(
                    logger,
                    "error",
                    "CBL",
                    "scheduled maintenance error: %s" % exc,
                    operation="MAINTENANCE",
                    error_detail=str(exc)[:200],
                )


# ── Migration helpers ────────────────────────────────────────


def migrate_default_to_collections() -> None:
    """One-time migration: move docs from _default._default to scoped collections."""
    if not USE_CBL:
        return
    db = get_db()

    # Check if migration is needed — if config already exists in the new collection, skip
    if _coll_get_doc(db, COLL_CONFIG, "config") is not None:
        log_event(logger, "debug", "CBL", "collection migration: already done")
        return

    log_event(
        logger, "info", "CBL", "migrating documents from _default to scoped collections"
    )
    migrated = 0

    with _transaction(db):
        # Config
        old_doc = db.getDocument("config")
        if old_doc:
            new_doc = MutableDocument("config")
            new_doc["type"] = "config"
            new_doc["data"] = old_doc.properties.get("data", "{}")
            new_doc["updated_at"] = old_doc.properties.get(
                "updated_at", int(time.time())
            )
            _coll_save_doc(db, COLL_CONFIG, new_doc)
            migrated += 1

        # Manifests + their referenced docs
        for manifest_id, coll_name in [
            ("manifest:mappings", COLL_MAPPINGS),
            ("manifest:dlq", COLL_DLQ),
            ("manifest:checkpoints", COLL_CHECKPOINTS),
        ]:
            old_manifest = db.getDocument(manifest_id)
            if old_manifest:
                raw_ids = old_manifest.properties.get("ids")
                ids = json.loads(raw_ids) if raw_ids else []
                # Copy each referenced doc
                for doc_id in ids:
                    old = db.getDocument(doc_id)
                    if old:
                        new_doc = MutableDocument(doc_id)
                        for key, val in old.properties.items():
                            new_doc[key] = val
                        _coll_save_doc(db, coll_name, new_doc)
                        migrated += 1
                # Copy manifest itself
                new_manifest = MutableDocument(manifest_id)
                new_manifest["type"] = "manifest"
                new_manifest["ids"] = raw_ids or "[]"
                _coll_save_doc(db, coll_name, new_manifest)
                migrated += 1

    log_event(
        logger,
        "info",
        "CBL",
        "migration complete",
        operation="MIGRATE",
        docs_migrated=migrated,
    )


def migrate_files_to_cbl(config_path: str = "config.json") -> None:
    """Sync file-based storage into CBL.

    Strategy:
      - **Config**: ``config.json`` is a seed file. It is imported into CBL
        only when CBL has no config (first start or fresh volume). After that,
        CBL is the single source of truth and ``config.json`` is ignored.
        To re-seed, delete the CBL volume.
      - **Mappings**: The ``mappings/`` directory is the edit surface (bind-
        mounted). On every startup, disk files are synced into CBL (new,
        changed, and deleted). The worker reads from CBL at runtime.
    """
    from pathlib import Path

    store = CBLStore()

    # ── Config: seed-only (CBL wins if it already has config) ─────────
    if not store.load_config():
        p = Path(config_path)
        if p.exists():
            cfg = json.loads(p.read_text())
            store.save_config(cfg)
            log_event(
                logger,
                "info",
                "CBL",
                "seeded config into CBL from %s (first start)" % config_path,
                operation="INSERT",
                doc_type="config",
                db_path=config_path,
            )
    else:
        log_event(
            logger,
            "debug",
            "CBL",
            "config already in CBL — ignoring %s" % config_path,
            operation="SELECT",
            doc_type="config",
        )

    # ── Migration: v1.x → v2.0 ────────────────────────────────────────
    # Check if config needs migration (one-time operation)
    migrated = store.migrate_v1_to_v2()
    if migrated:
        log_event(
            logger,
            "info",
            "CBL",
            "v1.x → v2.0 schema migration completed",
            operation="MIGRATE",
        )

    # ── Mappings: disk is edit surface, CBL is runtime store ────────
    # On every startup, sync mappings/ → CBL (disk always wins).
    mappings_dir = Path("mappings")
    mappings_dir.mkdir(exist_ok=True)

    disk_names: set[str] = set()
    added = updated = removed = 0

    if mappings_dir.is_dir():
        for f in mappings_dir.iterdir():
            if f.suffix in (".yaml", ".yml", ".json"):
                disk_names.add(f.name)
                disk_content = f.read_text()
                cbl_content = store.get_mapping(f.name)
                if cbl_content is None:
                    store.save_mapping(f.name, disk_content)
                    added += 1
                    log_event(
                        logger,
                        "info",
                        "CBL",
                        "imported mapping to CBL: %s" % f.name,
                        operation="INSERT",
                        doc_type="mapping",
                        doc_id=f"mapping:{f.name}",
                    )
                elif disk_content.strip() != cbl_content.strip():
                    store.save_mapping(f.name, disk_content)
                    updated += 1
                    log_event(
                        logger,
                        "info",
                        "CBL",
                        "updated mapping in CBL from disk: %s" % f.name,
                        operation="UPDATE",
                        doc_type="mapping",
                        doc_id=f"mapping:{f.name}",
                    )

    # Remove CBL mappings that no longer exist on disk
    cbl_entries = store.list_mappings()
    for entry in cbl_entries:
        name = entry.get("name", "")
        if name and name not in disk_names:
            store.delete_mapping(name)
            removed += 1
            log_event(
                logger,
                "info",
                "CBL",
                "removed mapping from CBL (not on disk): %s" % name,
                operation="DELETE",
                doc_type="mapping",
                doc_id=f"mapping:{name}",
            )

    log_event(
        logger,
        "info",
        "CBL",
        "mappings sync complete: %d on disk, %d added, %d updated, %d removed"
        % (len(disk_names), added, updated, removed),
        operation="SYNC",
        doc_type="mapping",
    )

    # Checkpoint
    cp_path = Path("checkpoint.json")
    if cp_path.exists():
        data = json.loads(cp_path.read_text())
        log_event(
            logger,
            "info",
            "CBL",
            "checkpoint.json available for migration",
            operation="SELECT",
            doc_type="checkpoint",
            seq=data.get("SGs_Seq", "0"),
        )

    ic("migrate_files_to_cbl: done", len(disk_names), added, updated, removed)
