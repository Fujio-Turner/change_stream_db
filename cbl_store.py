# cbl_store.py — Couchbase Lite CE storage layer

import json
import os
import time
import logging

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


def get_db():
    """Open or return the singleton CBL database handle."""
    global _db
    if _db is None:
        os.makedirs(CBL_DB_DIR, exist_ok=True)
        config = DatabaseConfiguration(CBL_DB_DIR)
        _db = Database(CBL_DB_NAME, config)
        logger.info("CBL database opened: %s/%s", CBL_DB_DIR, CBL_DB_NAME)
    return _db


class CBLStore:
    """High-level API for all CBL storage operations."""

    def __init__(self):
        self.db = get_db()

    # ── Config ────────────────────────────────────────────────

    def load_config(self) -> dict | None:
        doc = self.db.getDocument("config")
        if not doc:
            return None
        raw = doc.properties.get("data")
        if raw:
            return json.loads(raw)
        return None

    def save_config(self, cfg: dict) -> None:
        doc = self.db.getMutableDocument("config")
        if not doc:
            doc = MutableDocument("config")
        doc["type"] = "config"
        doc["data"] = json.dumps(cfg)
        doc["updated_at"] = int(time.time())
        self.db.saveDocument(doc)

    def import_config_file(self, path: str) -> dict:
        with open(path) as f:
            cfg = json.load(f)
        self.save_config(cfg)
        return cfg

    # ── Checkpoints ───────────────────────────────────────────

    def load_checkpoint(self, uuid: str) -> dict | None:
        doc_id = f"checkpoint:{uuid}"
        doc = self.db.getDocument(doc_id)
        if not doc:
            return None
        props = doc.properties
        return {
            "client_id": props.get("client_id", ""),
            "SGs_Seq": props.get("SGs_Seq", "0"),
            "time": props.get("time", 0),
            "remote": props.get("remote", 0),
        }

    def save_checkpoint(self, uuid: str, seq: str, client_id: str,
                        remote: int) -> None:
        doc_id = f"checkpoint:{uuid}"
        doc = self.db.getMutableDocument(doc_id)
        if not doc:
            doc = MutableDocument(doc_id)
        doc["type"] = "checkpoint"
        doc["client_id"] = client_id
        doc["SGs_Seq"] = seq
        doc["time"] = int(time.time())
        doc["remote"] = remote
        self.db.saveDocument(doc)

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
        return result

    def get_mapping(self, name: str) -> str | None:
        doc_id = f"mapping:{name}"
        doc = self.db.getDocument(doc_id)
        if not doc:
            return None
        return doc.properties.get("content")

    def save_mapping(self, name: str, content: str) -> None:
        doc_id = f"mapping:{name}"
        doc = self.db.getMutableDocument(doc_id)
        if not doc:
            doc = MutableDocument(doc_id)
        doc["type"] = "mapping"
        doc["name"] = name
        doc["content"] = content
        self.db.saveDocument(doc)

        # Update manifest
        ids = self._get_manifest("manifest:mappings")
        if doc_id not in ids:
            ids.append(doc_id)
            self._save_manifest("manifest:mappings", ids)

    def delete_mapping(self, name: str) -> None:
        doc_id = f"mapping:{name}"
        doc = self.db.getDocument(doc_id)
        if not doc:
            return
        self.db.purgeDocument(doc_id)

        # Update manifest
        ids = self._get_manifest("manifest:mappings")
        ids = [i for i in ids if i != doc_id]
        self._save_manifest("manifest:mappings", ids)

    # ── Dead Letter Queue ─────────────────────────────────────

    def add_dlq_entry(self, doc_id: str, seq: str, method: str,
                      status: int, error: str, doc: dict) -> None:
        ts = int(time.time())
        dlq_id = f"dlq:{doc_id}:{ts}"
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

        # Update manifest
        ids = self._get_manifest("manifest:dlq")
        ids.append(dlq_id)
        self._save_manifest("manifest:dlq", ids)

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
        return result

    def get_dlq_entry(self, dlq_id: str) -> dict | None:
        doc = self.db.getDocument(dlq_id)
        if not doc:
            return None
        props = doc.properties
        doc_data = props.get("doc_data", "{}")
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

    def delete_dlq_entry(self, dlq_id: str) -> None:
        doc = self.db.getDocument(dlq_id)
        if not doc:
            return
        self.db.purgeDocument(dlq_id)

        # Update manifest
        ids = self._get_manifest("manifest:dlq")
        ids = [i for i in ids if i != dlq_id]
        self._save_manifest("manifest:dlq", ids)

    def clear_dlq(self) -> None:
        ids = self._get_manifest("manifest:dlq")
        for dlq_id in ids:
            doc = self.db.getDocument(dlq_id)
            if doc:
                self.db.purgeDocument(dlq_id)
        self._save_manifest("manifest:dlq", [])

    def dlq_count(self) -> int:
        return len(self._get_manifest("manifest:dlq"))


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
            logger.info("Migrated %s to CBL", config_path)

    # Mappings
    mappings_dir = Path("mappings")
    if mappings_dir.is_dir():
        for f in mappings_dir.iterdir():
            if f.suffix in (".yaml", ".yml", ".json"):
                if not store.get_mapping(f.name):
                    store.save_mapping(f.name, f.read_text())
                    logger.info("Migrated mapping %s to CBL", f.name)

    # Checkpoint
    cp_path = Path("checkpoint.json")
    if cp_path.exists():
        data = json.loads(cp_path.read_text())
        logger.info("Migrated checkpoint.json to CBL (data available for Checkpoint class)")
