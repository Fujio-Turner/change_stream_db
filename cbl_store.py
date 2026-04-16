# cbl_store.py — Couchbase Lite CE storage layer

import json
import os
import time
import logging
import threading

from pipeline_logging import log_event

try:
    from CouchbaseLite.Database import Database, DatabaseConfiguration
    from CouchbaseLite.Document import MutableDocument
    USE_CBL = True
except ImportError:
    USE_CBL = False

CBL_DB_DIR = os.environ.get("CBL_DB_DIR", "/app/data")
CBL_DB_NAME = "changes_worker_db"

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
        os.makedirs(CBL_DB_DIR, exist_ok=True)
        config = DatabaseConfiguration(CBL_DB_DIR)
        t0 = time.monotonic()
        _db = Database(CBL_DB_NAME, config)
        elapsed = (time.monotonic() - t0) * 1000
        log_event(logger, "info", "CBL", "database opened",
                  operation="OPEN", db_name=CBL_DB_NAME,
                  db_path=CBL_DB_DIR, db_size_mb=_db_size_mb(),
                  duration_ms=round(elapsed, 1))
    return _db


def close_db():
    """Close the singleton CBL database handle."""
    global _db
    if _db is not None:
        t0 = time.monotonic()
        _db.close()
        elapsed = (time.monotonic() - t0) * 1000
        log_event(logger, "info", "CBL", "database closed",
                  operation="CLOSE", db_name=CBL_DB_NAME,
                  duration_ms=round(elapsed, 1))
        _db = None


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
            "config_exists": self.db.getDocument("config") is not None,
            "mappings_count": len(self._get_manifest("manifest:mappings")),
            "dlq_count": len(self._get_manifest("manifest:dlq")),
            "checkpoint_manifest": len(self._get_manifest("manifest:checkpoints")),
        }
        log_event(logger, "debug", "CBL", "database info retrieved",
                  operation="SELECT", db_name=CBL_DB_NAME,
                  db_size_mb=info["db_size_mb"])
        return info

    # ── Config ────────────────────────────────────────────────

    def load_config(self) -> dict | None:
        t0 = time.monotonic()
        doc = self.db.getDocument("config")
        elapsed = (time.monotonic() - t0) * 1000
        if not doc:
            log_event(logger, "warn", "CBL", "config document not found",
                      operation="SELECT", doc_id="config", doc_type="config",
                      duration_ms=round(elapsed, 1))
            return None
        raw = doc.properties.get("data")
        if raw:
            cfg = json.loads(raw)
            log_event(logger, "debug", "CBL", "config loaded",
                      operation="SELECT", doc_id="config", doc_type="config",
                      duration_ms=round(elapsed, 1))
            return cfg
        log_event(logger, "warn", "CBL", "config document has no data field",
                  operation="SELECT", doc_id="config", doc_type="config",
                  error_detail="missing 'data' property")
        return None

    def save_config(self, cfg: dict) -> None:
        t0 = time.monotonic()
        doc = self.db.getMutableDocument("config")
        if not doc:
            doc = MutableDocument("config")
        doc["type"] = "config"
        doc["data"] = json.dumps(cfg)
        doc["updated_at"] = int(time.time())
        self.db.saveDocument(doc)
        elapsed = (time.monotonic() - t0) * 1000
        log_event(logger, "info", "CBL", "config saved",
                  operation="INSERT" if elapsed else "UPDATE",
                  doc_id="config", doc_type="config",
                  duration_ms=round(elapsed, 1))

    def import_config_file(self, path: str) -> dict:
        with open(path) as f:
            cfg = json.load(f)
        self.save_config(cfg)
        log_event(logger, "info", "CBL", "config imported from file",
                  operation="INSERT", doc_id="config", doc_type="config",
                  db_path=path)
        return cfg

    # ── Checkpoints ───────────────────────────────────────────

    def load_checkpoint(self, uuid: str) -> dict | None:
        doc_id = f"checkpoint:{uuid}"
        t0 = time.monotonic()
        doc = self.db.getDocument(doc_id)
        elapsed = (time.monotonic() - t0) * 1000
        if not doc:
            log_event(logger, "debug", "CBL", "checkpoint not found",
                      operation="SELECT", doc_id=doc_id, doc_type="checkpoint",
                      duration_ms=round(elapsed, 1))
            return None
        props = doc.properties
        result = {
            "client_id": props.get("client_id", ""),
            "SGs_Seq": props.get("SGs_Seq", "0"),
            "time": props.get("time", 0),
            "remote": props.get("remote", 0),
        }
        log_event(logger, "debug", "CBL", "checkpoint loaded",
                  operation="SELECT", doc_id=doc_id, doc_type="checkpoint",
                  seq=result["SGs_Seq"], duration_ms=round(elapsed, 1))
        return result

    def save_checkpoint(self, uuid: str, seq: str, client_id: str,
                        remote: int) -> None:
        doc_id = f"checkpoint:{uuid}"
        t0 = time.monotonic()
        doc = self.db.getMutableDocument(doc_id)
        is_new = doc is None
        if not doc:
            doc = MutableDocument(doc_id)
        doc["type"] = "checkpoint"
        doc["client_id"] = client_id
        doc["SGs_Seq"] = seq
        doc["time"] = int(time.time())
        doc["remote"] = remote
        self.db.saveDocument(doc)
        elapsed = (time.monotonic() - t0) * 1000
        log_event(logger, "info", "CBL", "checkpoint saved",
                  operation="INSERT" if is_new else "UPDATE",
                  doc_id=doc_id, doc_type="checkpoint",
                  seq=seq, duration_ms=round(elapsed, 1))

    # ── Schema Mappings ───────────────────────────────────────

    def _get_manifest(self, manifest_id: str) -> list[str]:
        doc = self.db.getDocument(manifest_id)
        if not doc:
            return []
        raw = doc.properties.get("ids")
        if raw:
            return json.loads(raw)
        return []

    def _save_manifest(self, manifest_id: str, ids: list[str]) -> None:
        doc = self.db.getMutableDocument(manifest_id)
        if not doc:
            doc = MutableDocument(manifest_id)
        doc["type"] = "manifest"
        doc["ids"] = json.dumps(ids)
        self.db.saveDocument(doc)
        log_event(logger, "trace", "CBL", "manifest updated",
                  operation="UPDATE", doc_id=manifest_id,
                  doc_type="manifest", doc_count=len(ids))

    def list_mappings(self) -> list[dict]:
        ids = self._get_manifest("manifest:mappings")
        result = []
        for mid in ids:
            doc = self.db.getDocument(mid)
            if doc:
                props = doc.properties
                result.append({
                    "name": props.get("name", ""),
                    "content": props.get("content", ""),
                })
        log_event(logger, "debug", "CBL", "listed mappings",
                  operation="SELECT", doc_type="mapping",
                  doc_count=len(result))
        return result

    def get_mapping(self, name: str) -> str | None:
        doc_id = f"mapping:{name}"
        doc = self.db.getDocument(doc_id)
        if not doc:
            log_event(logger, "debug", "CBL", "mapping not found",
                      operation="SELECT", doc_id=doc_id, doc_type="mapping")
            return None
        log_event(logger, "debug", "CBL", "mapping loaded",
                  operation="SELECT", doc_id=doc_id, doc_type="mapping")
        return doc.properties.get("content")

    def save_mapping(self, name: str, content: str) -> None:
        doc_id = f"mapping:{name}"
        t0 = time.monotonic()
        doc = self.db.getMutableDocument(doc_id)
        is_new = doc is None
        if not doc:
            doc = MutableDocument(doc_id)
        doc["type"] = "mapping"
        doc["name"] = name
        doc["content"] = content
        self.db.saveDocument(doc)
        elapsed = (time.monotonic() - t0) * 1000

        # Update manifest
        ids = self._get_manifest("manifest:mappings")
        if doc_id not in ids:
            ids.append(doc_id)
            self._save_manifest("manifest:mappings", ids)

        log_event(logger, "info", "CBL", "mapping saved",
                  operation="INSERT" if is_new else "UPDATE",
                  doc_id=doc_id, doc_type="mapping",
                  duration_ms=round(elapsed, 1))

    def delete_mapping(self, name: str) -> None:
        doc_id = f"mapping:{name}"
        doc = self.db.getDocument(doc_id)
        if not doc:
            log_event(logger, "debug", "CBL", "mapping not found for delete",
                      operation="DELETE", doc_id=doc_id, doc_type="mapping")
            return
        self.db.purgeDocument(doc_id)

        # Update manifest
        ids = self._get_manifest("manifest:mappings")
        ids = [i for i in ids if i != doc_id]
        self._save_manifest("manifest:mappings", ids)

        log_event(logger, "info", "CBL", "mapping deleted",
                  operation="DELETE", doc_id=doc_id, doc_type="mapping")

    # ── Dead Letter Queue ─────────────────────────────────────

    def add_dlq_entry(self, doc_id: str, seq: str, method: str,
                      status: int, error: str, doc: dict) -> None:
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
        dlq_doc["doc_data"] = json.dumps(doc)
        self.db.saveDocument(dlq_doc)
        elapsed = (time.monotonic() - t0) * 1000

        # Update manifest
        ids = self._get_manifest("manifest:dlq")
        ids.append(dlq_id)
        self._save_manifest("manifest:dlq", ids)

        log_event(logger, "warn", "DLQ", "entry added",
                  operation="INSERT", doc_id=dlq_id, doc_type="dlq",
                  seq=seq, status=status, duration_ms=round(elapsed, 1))

    def list_dlq(self) -> list[dict]:
        ids = self._get_manifest("manifest:dlq")
        result = []
        for dlq_id in ids:
            doc = self.db.getDocument(dlq_id)
            if doc:
                props = doc.properties
                result.append({
                    "id": dlq_id,
                    "doc_id_original": props.get("doc_id_original", ""),
                    "seq": props.get("seq", ""),
                    "method": props.get("method", ""),
                    "status": props.get("status", 0),
                    "error": props.get("error", ""),
                    "time": props.get("time", 0),
                    "retried": props.get("retried", False),
                })
        log_event(logger, "debug", "DLQ", "listed entries",
                  operation="SELECT", doc_type="dlq", doc_count=len(result))
        return result

    def get_dlq_entry(self, dlq_id: str) -> dict | None:
        doc = self.db.getDocument(dlq_id)
        if not doc:
            log_event(logger, "debug", "DLQ", "entry not found",
                      operation="SELECT", doc_id=dlq_id, doc_type="dlq")
            return None
        props = doc.properties
        doc_data = props.get("doc_data", "{}")
        log_event(logger, "debug", "DLQ", "entry loaded",
                  operation="SELECT", doc_id=dlq_id, doc_type="dlq")
        return {
            "id": dlq_id,
            "doc_id_original": props.get("doc_id_original", ""),
            "seq": props.get("seq", ""),
            "method": props.get("method", ""),
            "status": props.get("status", 0),
            "error": props.get("error", ""),
            "time": props.get("time", 0),
            "retried": props.get("retried", False),
            "doc_data": json.loads(doc_data),
        }

    def mark_dlq_retried(self, dlq_id: str) -> None:
        doc = self.db.getMutableDocument(dlq_id)
        if not doc:
            return
        doc["retried"] = True
        self.db.saveDocument(doc)
        log_event(logger, "info", "DLQ", "entry marked retried",
                  operation="UPDATE", doc_id=dlq_id, doc_type="dlq")

    def delete_dlq_entry(self, dlq_id: str) -> None:
        doc = self.db.getDocument(dlq_id)
        if not doc:
            return
        self.db.purgeDocument(dlq_id)

        # Update manifest
        ids = self._get_manifest("manifest:dlq")
        ids = [i for i in ids if i != dlq_id]
        self._save_manifest("manifest:dlq", ids)

        log_event(logger, "info", "DLQ", "entry purged",
                  operation="DELETE", doc_id=dlq_id, doc_type="dlq")

    def clear_dlq(self) -> None:
        ids = self._get_manifest("manifest:dlq")
        count = 0
        for dlq_id in ids:
            doc = self.db.getDocument(dlq_id)
            if doc:
                self.db.purgeDocument(dlq_id)
                count += 1
        self._save_manifest("manifest:dlq", [])
        log_event(logger, "info", "DLQ", "queue cleared",
                  operation="DELETE", doc_type="dlq", doc_count=count)

    def dlq_count(self) -> int:
        return len(self._get_manifest("manifest:dlq"))

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
                log_event(logger, "warn", "CBL",
                          "maintenance operation not available in this CBL SDK version",
                          maintenance_type=maint_type,
                          error_detail="no performMaintenance or compact method")
                return False

            elapsed = (time.monotonic() - t0) * 1000
            size_after = _db_size_mb()
            log_event(logger, "info", "CBL",
                      "maintenance completed: %s" % maint_type,
                      operation="MAINTENANCE", maintenance_type=maint_type,
                      db_name=CBL_DB_NAME, db_size_mb=size_after,
                      duration_ms=round(elapsed, 1))
            if maint_type == "compact" and size_before > 0:
                saved = size_before - size_after
                if saved > 0.01:
                    log_event(logger, "info", "CBL",
                              "compact freed %.2f MB (%.1f%% reduction)" % (
                                  saved, (saved / size_before) * 100),
                              maintenance_type="compact",
                              db_size_mb=size_after)
            return True

        except Exception as exc:
            elapsed = (time.monotonic() - t0) * 1000
            log_event(logger, "error", "CBL",
                      "maintenance failed: %s – %s" % (maint_type, exc),
                      operation="MAINTENANCE", maintenance_type=maint_type,
                      db_name=CBL_DB_NAME, duration_ms=round(elapsed, 1),
                      error_detail=str(exc)[:200])
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
        log_event(logger, "info", "CBL",
                  "maintenance scheduler started (every %.1f hours)" % (
                      self.interval_seconds / 3600),
                  operation="OPEN")

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        log_event(logger, "info", "CBL", "maintenance scheduler stopped",
                  operation="CLOSE")

    def _run_loop(self) -> None:
        while not self._stop_event.wait(timeout=self.interval_seconds):
            try:
                store = CBLStore()
                info = store.db_info()
                log_event(logger, "info", "CBL",
                          "scheduled maintenance starting",
                          operation="MAINTENANCE",
                          db_size_mb=info["db_size_mb"])
                results = store.run_all_maintenance()
                log_event(logger, "info", "CBL",
                          "scheduled maintenance complete: %s" % results,
                          operation="MAINTENANCE")
            except Exception as exc:
                log_event(logger, "error", "CBL",
                          "scheduled maintenance error: %s" % exc,
                          operation="MAINTENANCE", error_detail=str(exc)[:200])


# ── Migration helper ─────────────────────────────────────────

def migrate_files_to_cbl(config_path: str = "config.json") -> None:
    """One-time migration of file-based storage to CBL."""
    from pathlib import Path

    store = CBLStore()

    # Config
    if not store.load_config():
        p = Path(config_path)
        if p.exists():
            cfg = json.loads(p.read_text())
            store.save_config(cfg)
            log_event(logger, "info", "CBL", "migrated config to CBL",
                      operation="INSERT", doc_type="config", db_path=config_path)

    # Mappings
    mappings_dir = Path("mappings")
    if mappings_dir.is_dir():
        for f in mappings_dir.iterdir():
            if f.suffix in (".yaml", ".yml", ".json"):
                if not store.get_mapping(f.name):
                    store.save_mapping(f.name, f.read_text())
                    log_event(logger, "info", "CBL",
                              "migrated mapping to CBL",
                              operation="INSERT", doc_type="mapping",
                              doc_id=f"mapping:{f.name}")

    # Checkpoint
    cp_path = Path("checkpoint.json")
    if cp_path.exists():
        data = json.loads(cp_path.read_text())
        log_event(logger, "info", "CBL",
                  "checkpoint.json available for migration",
                  operation="SELECT", doc_type="checkpoint",
                  seq=data.get("SGs_Seq", "0"))
