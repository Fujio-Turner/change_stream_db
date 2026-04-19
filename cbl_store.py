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
    from CouchbaseLite.Query import N1QLQuery

    USE_CBL = True
except ImportError:
    USE_CBL = False

CBL_DB_DIR = os.environ.get("CBL_DB_DIR", "/app/data")
CBL_DB_NAME = os.environ.get("CBL_DB_NAME", "changes_worker_db")
CBL_SCOPE = "changes-worker"
COLL_CONFIG = "config"
COLL_CHECKPOINTS = "checkpoints"
COLL_MAPPINGS = "mappings"
COLL_DLQ = "dlq"


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
            "collections": [COLL_CONFIG, COLL_CHECKPOINTS, COLL_MAPPINGS, COLL_DLQ],
            "config_exists": _coll_get_doc(self.db, COLL_CONFIG, "config") is not None,
            "mappings_count": len(
                self._get_manifest(COLL_MAPPINGS, "manifest:mappings")
            ),
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

            # Timeline — fetch timestamps and bucket in Python
            time_rows = _run_n1ql(
                self.db,
                f"SELECT d.time AS t FROM {f} AS d WHERE d.type = 'dlq' AND d.time > 0",
            )
            timeline: dict[str, int] = {}
            for r in time_rows:
                t = r.get("t", 0)
                if t:
                    minute_key = time.strftime("%Y-%m-%d %H:%M", time.gmtime(t))
                    timeline[minute_key] = timeline.get(minute_key, 0) + 1

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
