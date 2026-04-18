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
    """Purge a document from a specific collection by ID."""
    coll = _get_collection(db, CBL_SCOPE, collection_name)
    lib.CBLCollection_PurgeDocumentByID(coll, stringParam(doc_id), _cbl_gError)


def _set_doc_expiration(db, doc_id: str, ttl_seconds: int) -> bool:
    """Set document expiration (TTL) using the CBL C API.

    Args:
        db: CBL database handle.
        doc_id: Document ID.
        ttl_seconds: Seconds from now until the document expires and is auto-purged.
                     Pass 0 to clear expiration.
    """
    if ttl_seconds <= 0:
        return True
    expiration_ms = int((time.time() + ttl_seconds) * 1000)
    ok = lib.CBLDatabase_SetDocumentExpiration(
        db._ref, stringParam(doc_id), expiration_ms, _cbl_gError
    )
    return bool(ok)


class CBLStore:
    """High-level API for all CBL storage operations."""

    def __init__(self):
        self.db = get_db()

    # ── Info / diagnostics ────────────────────────────────────

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
            "dlq_count": len(self._get_manifest(COLL_DLQ, "manifest:dlq")),
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
        dlq_doc["time"] = ts
        dlq_doc["retried"] = False
        dlq_doc["replay_attempts"] = 0
        dlq_doc["target_url"] = target_url
        dlq_doc["doc_data"] = json.dumps(doc)
        _coll_save_doc(self.db, COLL_DLQ, dlq_doc)
        if ttl_seconds > 0:
            _set_doc_expiration(self.db, dlq_id, ttl_seconds)
        elapsed = (time.monotonic() - t0) * 1000

        # Update manifest
        ids = self._get_manifest(COLL_DLQ, "manifest:dlq")
        ids.append(dlq_id)
        self._save_manifest(COLL_DLQ, "manifest:dlq", ids)

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
        ids = self._get_manifest(COLL_DLQ, "manifest:dlq")
        result = []
        for dlq_id in ids:
            doc = _coll_get_doc(self.db, COLL_DLQ, dlq_id)
            if doc:
                props = doc.properties
                result.append(
                    {
                        "id": dlq_id,
                        "doc_id_original": props.get("doc_id_original", ""),
                        "seq": props.get("seq", ""),
                        "method": props.get("method", ""),
                        "status": props.get("status", 0),
                        "error": props.get("error", ""),
                        "time": props.get("time", 0),
                        "retried": props.get("retried", False),
                        "replay_attempts": props.get("replay_attempts", 0),
                        "target_url": props.get("target_url", ""),
                    }
                )
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

    def get_dlq_entry(self, dlq_id: str) -> dict | None:
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
            "time": props.get("time", 0),
            "retried": props.get("retried", False),
            "replay_attempts": props.get("replay_attempts", 0),
            "target_url": props.get("target_url", ""),
            "doc_data": json.loads(doc_data),
        }

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

        # Update manifest
        ids = self._get_manifest(COLL_DLQ, "manifest:dlq")
        ids = [i for i in ids if i != dlq_id]
        self._save_manifest(COLL_DLQ, "manifest:dlq", ids)

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
        ids = self._get_manifest(COLL_DLQ, "manifest:dlq")
        count = 0
        for dlq_id in ids:
            doc = _coll_get_doc(self.db, COLL_DLQ, dlq_id)
            if doc:
                _coll_purge_doc(self.db, COLL_DLQ, dlq_id)
                count += 1
        self._save_manifest(COLL_DLQ, "manifest:dlq", [])
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
        return len(self._get_manifest(COLL_DLQ, "manifest:dlq"))

    def purge_expired_dlq(self, max_age_seconds: int) -> int:
        """Purge DLQ entries older than max_age_seconds. Returns count purged."""
        if max_age_seconds <= 0:
            return 0
        cutoff = int(time.time()) - max_age_seconds
        ids = self._get_manifest(COLL_DLQ, "manifest:dlq")
        purged = 0
        remaining = []
        for dlq_id in ids:
            doc = _coll_get_doc(self.db, COLL_DLQ, dlq_id)
            if doc:
                entry_time = doc.properties.get("time", 0)
                if entry_time > 0 and entry_time < cutoff:
                    _coll_purge_doc(self.db, COLL_DLQ, dlq_id)
                    purged += 1
                else:
                    remaining.append(dlq_id)
            # If doc doesn't exist, don't keep in manifest
        if purged > 0:
            self._save_manifest(COLL_DLQ, "manifest:dlq", remaining)
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

    def _run_maintenance(self, maint_type: str, method_name: str) -> bool:
        """
        Run a CBL maintenance operation.

        The Python CBL SDK may expose maintenance via different method names
        depending on the version. We try common patterns and degrade gracefully.
        """
        size_before = _db_size_mb()
        t0 = time.monotonic()

        try:
            # Try the standard performMaintenance API
            if hasattr(self.db, "performMaintenance"):
                self.db.performMaintenance(maint_type)
            elif hasattr(self.db, "compact") and maint_type == "compact":
                self.db.compact()
            else:
                log_event(
                    logger,
                    "warn",
                    "CBL",
                    "maintenance operation not available in this CBL SDK version",
                    maintenance_type=maint_type,
                    error_detail="no performMaintenance or compact method",
                )
                return False

            elapsed = (time.monotonic() - t0) * 1000
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

    # Config
    old_doc = db.getDocument("config")
    if old_doc:
        new_doc = MutableDocument("config")
        new_doc["type"] = "config"
        new_doc["data"] = old_doc.properties.get("data", "{}")
        new_doc["updated_at"] = old_doc.properties.get("updated_at", int(time.time()))
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
