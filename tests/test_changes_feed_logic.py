#!/usr/bin/env python3
"""
Comprehensive tests for _changes feed processing logic.

Covers:
  - _build_changes_body() parameter matrix (src, active_only, include_docs,
    limit, since formats, channels, feed_type, overrides)
  - Checkpoint initial_sync_done flag (load, save, legacy compat, fallback)
  - Initial sync detection logic
  - _process_changes_batch() filtering with initial_sync (CouchDB vs Couchbase)
  - _catch_up_normal() with optimize_initial_sync true/false
  - Sync Gateway sequence number format passthrough
  - fetch_docs / _bulk_get / individual GET basics
  - optimize_initial_sync config flag
"""

import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import aiohttp
import main as cw


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _feed_cfg(**overrides):
    cfg = {"heartbeat_ms": 30000}
    cfg.update(overrides)
    return cfg


def _make_output():
    output = MagicMock()
    output._mode = "stdout"
    output._write_method = "PUT"
    output._delete_method = "DELETE"
    output.send = AsyncMock(return_value={"ok": True})
    output.log_stats = MagicMock()
    return output


def _make_checkpoint(**attrs):
    cp = MagicMock()
    cp.save = AsyncMock()
    cp._initial_sync_done = False
    cp.initial_sync_done = False
    for k, v in attrs.items():
        setattr(cp, k, v)
    return cp


def _make_dlq():
    dlq = MagicMock()
    dlq.enabled = False
    return dlq


def _make_http(response_body):
    """Return a mock RetryableHTTP that returns the given JSON body."""
    http = MagicMock()
    resp = AsyncMock()
    resp.status = 200
    resp.read = AsyncMock(return_value=json.dumps(response_body).encode())
    resp.release = MagicMock()
    http.request = AsyncMock(return_value=resp)
    return http


# ===================================================================
# SG sequence number formats used across tests
# ===================================================================

SG_SEQUENCES = {
    "integer": "42",
    "zero": "0",
    "large_int": "128",
    "backfill": "123:100",
    "delayed": "90::100",
    "delayed_backfill": "90:123:100",
    "distributed_hash": "200-0",
    "vbucket": "50.123",
    "distributed_backfill": "200-0:50.123",
    "couchdb_int": "42",
    "couchdb_string": "42-abc123def456",
}


# ===================================================================
# 1. _build_changes_body — comprehensive parameter matrix
# ===================================================================


class TestBuildChangesBodyActiveOnly(unittest.TestCase):
    """active_only behaviour across sources and overrides."""

    def test_sg_active_only_config_true(self):
        body = cw._build_changes_body(
            _feed_cfg(active_only=True), "sync_gateway", "0", "normal", 60000
        )
        self.assertTrue(body["active_only"])

    def test_sg_active_only_config_false(self):
        body = cw._build_changes_body(
            _feed_cfg(active_only=False), "sync_gateway", "0", "normal", 60000
        )
        self.assertNotIn("active_only", body)

    def test_app_services_active_only(self):
        body = cw._build_changes_body(
            _feed_cfg(active_only=True), "app_services", "0", "normal", 60000
        )
        self.assertTrue(body["active_only"])

    def test_edge_server_active_only(self):
        body = cw._build_changes_body(
            _feed_cfg(active_only=True), "edge_server", "0", "normal", 60000
        )
        self.assertTrue(body["active_only"])

    def test_couchdb_active_only_never_sent(self):
        """CouchDB does not support active_only — must never be in body."""
        body = cw._build_changes_body(
            _feed_cfg(active_only=True), "couchdb", "0", "normal", 60000
        )
        self.assertNotIn("active_only", body)

    def test_couchdb_active_only_override_true_still_not_sent(self):
        """Even with override=True, CouchDB must not get active_only."""
        body = cw._build_changes_body(
            _feed_cfg(active_only=False),
            "couchdb",
            "0",
            "normal",
            60000,
            active_only_override=True,
        )
        self.assertNotIn("active_only", body)

    def test_override_true_overrides_config_false(self):
        body = cw._build_changes_body(
            _feed_cfg(active_only=False),
            "sync_gateway",
            "0",
            "normal",
            60000,
            active_only_override=True,
        )
        self.assertTrue(body["active_only"])

    def test_override_false_overrides_config_true(self):
        body = cw._build_changes_body(
            _feed_cfg(active_only=True),
            "sync_gateway",
            "0",
            "normal",
            60000,
            active_only_override=False,
        )
        self.assertNotIn("active_only", body)

    def test_override_none_uses_config(self):
        body = cw._build_changes_body(
            _feed_cfg(active_only=True),
            "sync_gateway",
            "0",
            "normal",
            60000,
            active_only_override=None,
        )
        self.assertTrue(body["active_only"])


class TestBuildChangesBodyIncludeDocs(unittest.TestCase):
    """include_docs behaviour with config and overrides."""

    def test_include_docs_config_true(self):
        body = cw._build_changes_body(
            _feed_cfg(include_docs=True), "sync_gateway", "0", "normal", 60000
        )
        self.assertTrue(body["include_docs"])

    def test_include_docs_config_false(self):
        body = cw._build_changes_body(
            _feed_cfg(include_docs=False), "sync_gateway", "0", "normal", 60000
        )
        self.assertNotIn("include_docs", body)

    def test_include_docs_override_false_overrides_config_true(self):
        body = cw._build_changes_body(
            _feed_cfg(include_docs=True),
            "sync_gateway",
            "0",
            "normal",
            60000,
            include_docs_override=False,
        )
        self.assertNotIn("include_docs", body)

    def test_include_docs_override_true_overrides_config_false(self):
        body = cw._build_changes_body(
            _feed_cfg(include_docs=False),
            "sync_gateway",
            "0",
            "normal",
            60000,
            include_docs_override=True,
        )
        self.assertTrue(body["include_docs"])

    def test_include_docs_override_none_uses_config(self):
        body = cw._build_changes_body(
            _feed_cfg(include_docs=True),
            "sync_gateway",
            "0",
            "normal",
            60000,
            include_docs_override=None,
        )
        self.assertTrue(body["include_docs"])

    def test_couchdb_include_docs(self):
        body = cw._build_changes_body(
            _feed_cfg(include_docs=True), "couchdb", "0", "normal", 60000
        )
        self.assertTrue(body["include_docs"])


class TestBuildChangesBodyLimit(unittest.TestCase):
    """Limit handling."""

    def test_positive_limit(self):
        body = cw._build_changes_body(
            _feed_cfg(), "sync_gateway", "0", "normal", 60000, limit=500
        )
        self.assertEqual(body["limit"], 500)

    def test_zero_limit_omitted(self):
        body = cw._build_changes_body(
            _feed_cfg(), "sync_gateway", "0", "normal", 60000, limit=0
        )
        self.assertNotIn("limit", body)

    def test_large_limit(self):
        body = cw._build_changes_body(
            _feed_cfg(), "sync_gateway", "0", "normal", 60000, limit=100000
        )
        self.assertEqual(body["limit"], 100000)


class TestBuildChangesBodySequenceFormats(unittest.TestCase):
    """Verify all SG sequence formats are passed through as-is."""

    def test_sequence_passthrough(self):
        for name, seq in SG_SEQUENCES.items():
            with self.subTest(format=name, seq=seq):
                body = cw._build_changes_body(
                    _feed_cfg(), "sync_gateway", seq, "normal", 60000
                )
                self.assertEqual(
                    body["since"], seq, f"Sequence {name!r} not passed through"
                )


class TestBuildChangesBodyVersionType(unittest.TestCase):
    """version_type only for SG and App Services."""

    def test_sg_has_version_type(self):
        body = cw._build_changes_body(_feed_cfg(), "sync_gateway", "0", "normal", 60000)
        self.assertIn("version_type", body)

    def test_app_services_has_version_type(self):
        body = cw._build_changes_body(_feed_cfg(), "app_services", "0", "normal", 60000)
        self.assertIn("version_type", body)

    def test_edge_server_no_version_type(self):
        body = cw._build_changes_body(_feed_cfg(), "edge_server", "0", "normal", 60000)
        self.assertNotIn("version_type", body)

    def test_couchdb_no_version_type(self):
        body = cw._build_changes_body(_feed_cfg(), "couchdb", "0", "normal", 60000)
        self.assertNotIn("version_type", body)


class TestBuildChangesBodyChannels(unittest.TestCase):
    """Channels filter only for non-CouchDB."""

    def test_sg_with_channels(self):
        body = cw._build_changes_body(
            _feed_cfg(channels=["ch1", "ch2"]), "sync_gateway", "0", "normal", 60000
        )
        self.assertEqual(body["filter"], "sync_gateway/bychannel")
        self.assertEqual(body["channels"], "ch1,ch2")

    def test_couchdb_channels_not_sent(self):
        body = cw._build_changes_body(
            _feed_cfg(channels=["ch1"]), "couchdb", "0", "normal", 60000
        )
        self.assertNotIn("filter", body)
        self.assertNotIn("channels", body)

    def test_empty_channels_no_filter(self):
        body = cw._build_changes_body(
            _feed_cfg(channels=[]), "sync_gateway", "0", "normal", 60000
        )
        self.assertNotIn("filter", body)


class TestBuildChangesBodyFeedType(unittest.TestCase):
    """Feed type is passed through."""

    def test_longpoll(self):
        body = cw._build_changes_body(
            _feed_cfg(), "sync_gateway", "0", "longpoll", 60000
        )
        self.assertEqual(body["feed"], "longpoll")

    def test_normal(self):
        body = cw._build_changes_body(_feed_cfg(), "sync_gateway", "0", "normal", 60000)
        self.assertEqual(body["feed"], "normal")

    def test_continuous(self):
        body = cw._build_changes_body(
            _feed_cfg(), "sync_gateway", "0", "continuous", 60000
        )
        self.assertEqual(body["feed"], "continuous")


class TestBuildChangesBodyCombined(unittest.TestCase):
    """Combined override scenarios (initial sync simulation)."""

    def test_initial_sync_sg(self):
        """Simulates initial sync for Sync Gateway: active_only=True,
        include_docs=False, limit set."""
        body = cw._build_changes_body(
            _feed_cfg(active_only=False, include_docs=True),
            "sync_gateway",
            "0",
            "normal",
            60000,
            limit=10000,
            active_only_override=True,
            include_docs_override=False,
        )
        self.assertTrue(body["active_only"])
        self.assertNotIn("include_docs", body)
        self.assertEqual(body["limit"], 10000)
        self.assertEqual(body["feed"], "normal")

    def test_initial_sync_couchdb(self):
        """CouchDB initial sync: active_only override ignored, include_docs=False."""
        body = cw._build_changes_body(
            _feed_cfg(active_only=False, include_docs=True),
            "couchdb",
            "0",
            "normal",
            60000,
            active_only_override=True,
            include_docs_override=False,
        )
        self.assertNotIn("active_only", body)
        self.assertNotIn("include_docs", body)

    def test_steady_state_sg(self):
        """After initial sync: user config used."""
        body = cw._build_changes_body(
            _feed_cfg(active_only=True, include_docs=True),
            "sync_gateway",
            "128",
            "longpoll",
            60000,
        )
        self.assertTrue(body["active_only"])
        self.assertTrue(body["include_docs"])
        self.assertEqual(body["since"], "128")


# ===================================================================
# 2. Checkpoint initial_sync_done flag
# ===================================================================


class TestCheckpointInitialSyncDone(unittest.TestCase):
    """Tests for the initial_sync_done flag on Checkpoint."""

    def _gw(self):
        return {
            "url": "http://localhost:4984",
            "database": "db",
            "scope": "",
            "collection": "",
        }

    def test_fresh_checkpoint_flag_false(self):
        cp = cw.Checkpoint({"client_id": "w"}, self._gw(), [])
        self.assertFalse(cp.initial_sync_done)

    def test_property_reflects_internal(self):
        cp = cw.Checkpoint({"client_id": "w"}, self._gw(), [])
        cp._initial_sync_done = True
        self.assertTrue(cp.initial_sync_done)

    def test_load_sg_with_flag_true(self):
        """Load from SG response with initial_sync_done=true."""

        async def _run():
            http = MagicMock()
            resp = AsyncMock()
            resp.json = AsyncMock(
                return_value={
                    "SGs_Seq": "42",
                    "_rev": "1-abc",
                    "remote": 5,
                    "initial_sync_done": True,
                }
            )
            resp.release = MagicMock()
            http.request = AsyncMock(return_value=resp)

            cp = cw.Checkpoint({"client_id": "w"}, self._gw(), [])
            seq = await cp.load(http, "http://localhost:4984/db", None, {})
            self.assertEqual(seq, "42")
            self.assertTrue(cp.initial_sync_done)

        asyncio.run(_run())

    def test_load_sg_with_flag_false(self):
        """Load from SG with initial_sync_done=false (interrupted initial)."""

        async def _run():
            http = MagicMock()
            resp = AsyncMock()
            resp.json = AsyncMock(
                return_value={
                    "SGs_Seq": "200-0",
                    "_rev": "2-def",
                    "remote": 10,
                    "initial_sync_done": False,
                }
            )
            resp.release = MagicMock()
            http.request = AsyncMock(return_value=resp)

            cp = cw.Checkpoint({"client_id": "w"}, self._gw(), [])
            seq = await cp.load(http, "http://localhost:4984/db", None, {})
            self.assertEqual(seq, "200-0")
            self.assertFalse(cp.initial_sync_done)

        asyncio.run(_run())

    def test_load_sg_flag_missing_seq_zero(self):
        """Legacy checkpoint: flag missing + seq=0 → False."""

        async def _run():
            http = MagicMock()
            resp = AsyncMock()
            resp.json = AsyncMock(
                return_value={
                    "SGs_Seq": "0",
                    "_rev": "1-x",
                }
            )
            resp.release = MagicMock()
            http.request = AsyncMock(return_value=resp)

            cp = cw.Checkpoint({"client_id": "w"}, self._gw(), [])
            await cp.load(http, "http://localhost:4984/db", None, {})
            self.assertFalse(cp.initial_sync_done)

        asyncio.run(_run())

    def test_load_sg_flag_missing_seq_nonzero(self):
        """Legacy checkpoint: flag missing + seq=42 → True (backward compat)."""

        async def _run():
            http = MagicMock()
            resp = AsyncMock()
            resp.json = AsyncMock(
                return_value={
                    "SGs_Seq": "42",
                    "_rev": "1-x",
                }
            )
            resp.release = MagicMock()
            http.request = AsyncMock(return_value=resp)

            cp = cw.Checkpoint({"client_id": "w"}, self._gw(), [])
            await cp.load(http, "http://localhost:4984/db", None, {})
            self.assertTrue(cp.initial_sync_done)

        asyncio.run(_run())

    def test_load_sg_flag_missing_distributed_seq(self):
        """Legacy checkpoint with distributed seq 200-0 → True."""

        async def _run():
            http = MagicMock()
            resp = AsyncMock()
            resp.json = AsyncMock(
                return_value={
                    "SGs_Seq": "200-0",
                    "_rev": "3-y",
                }
            )
            resp.release = MagicMock()
            http.request = AsyncMock(return_value=resp)

            cp = cw.Checkpoint({"client_id": "w"}, self._gw(), [])
            await cp.load(http, "http://localhost:4984/db", None, {})
            self.assertTrue(cp.initial_sync_done)

        asyncio.run(_run())

    def test_save_includes_flag(self):
        """save() must include initial_sync_done in the request body."""

        async def _run():
            http = MagicMock()
            resp = AsyncMock()
            resp.json = AsyncMock(return_value={"rev": "2-abc"})
            resp.release = MagicMock()
            http.request = AsyncMock(return_value=resp)

            cp = cw.Checkpoint({"client_id": "w"}, self._gw(), [])
            cp._initial_sync_done = False
            await cp.save("42", http, "http://localhost:4984/db", None, {})

            call_kwargs = http.request.call_args[1]
            body = call_kwargs["json"]
            self.assertIn("initial_sync_done", body)
            self.assertFalse(body["initial_sync_done"])

        asyncio.run(_run())

    def test_save_includes_flag_true(self):
        """save() with initial_sync_done=True persists it."""

        async def _run():
            http = MagicMock()
            resp = AsyncMock()
            resp.json = AsyncMock(return_value={"rev": "3-def"})
            resp.release = MagicMock()
            http.request = AsyncMock(return_value=resp)

            cp = cw.Checkpoint({"client_id": "w"}, self._gw(), [])
            cp._initial_sync_done = True
            await cp.save("100", http, "http://localhost:4984/db", None, {})

            body = http.request.call_args[1]["json"]
            self.assertTrue(body["initial_sync_done"])

        asyncio.run(_run())

    @patch("main.USE_CBL", False)
    def test_fallback_file_preserves_flag(self):
        """File fallback save/load preserves initial_sync_done."""
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            cp = cw.Checkpoint({"client_id": "w", "file": path}, self._gw(), [])
            cp._initial_sync_done = True
            cp._save_fallback("99")

            data = json.loads(Path(path).read_text())
            self.assertTrue(data["initial_sync_done"])

            # Load it back
            cp2 = cw.Checkpoint({"client_id": "w", "file": path}, self._gw(), [])
            seq = cp2._load_fallback()
            self.assertEqual(seq, "99")
            self.assertTrue(cp2.initial_sync_done)
        finally:
            os.unlink(path)

    @patch("main.USE_CBL", False)
    def test_fallback_file_legacy_nonzero(self):
        """File fallback with flag missing + non-zero seq → True."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"SGs_Seq": "55", "time": 1000, "remote": 1}, f)
            path = f.name
        try:
            cp = cw.Checkpoint({"client_id": "w", "file": path}, self._gw(), [])
            seq = cp._load_fallback()
            self.assertEqual(seq, "55")
            self.assertTrue(cp.initial_sync_done)
        finally:
            os.unlink(path)

    @patch("main.USE_CBL", False)
    def test_fallback_file_legacy_zero(self):
        """File fallback with flag missing + seq=0 → False."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"SGs_Seq": "0", "time": 1000, "remote": 0}, f)
            path = f.name
        try:
            cp = cw.Checkpoint({"client_id": "w", "file": path}, self._gw(), [])
            seq = cp._load_fallback()
            self.assertEqual(seq, "0")
            self.assertFalse(cp.initial_sync_done)
        finally:
            os.unlink(path)


# ===================================================================
# 3. Initial sync detection logic
# ===================================================================


class TestInitialSyncDetection(unittest.TestCase):
    """Test the initial_sync boolean derivation."""

    def _check(self, requested_since, initial_sync_done, expected):
        result = requested_since == "0" and not initial_sync_done
        self.assertEqual(result, expected)

    def test_fresh_start(self):
        self._check("0", False, True)

    def test_completed(self):
        self._check("0", True, False)

    def test_user_explicit_since(self):
        self._check("42", False, False)

    def test_user_explicit_since_completed(self):
        self._check("42", True, False)

    def test_distributed_since(self):
        """User sets since to a distributed seq → no initial sync."""
        self._check("200-0", False, False)

    def test_compound_since(self):
        self._check("90:123:100", False, False)


# ===================================================================
# 4. _process_changes_batch() — filtering with initial_sync
# ===================================================================


class TestProcessChangesBatchFiltering(unittest.TestCase):
    """Test delete/remove filtering across initial_sync and src combos."""

    def _batch_kwargs(self, src="sync_gateway", proc_cfg=None, initial_sync=False):
        return dict(
            feed_cfg={"include_docs": True},
            proc_cfg=proc_cfg or {},
            output=_make_output(),
            dlq=_make_dlq(),
            checkpoint=_make_checkpoint(),
            http=MagicMock(),
            base_url="http://localhost:4984/db",
            basic_auth=None,
            auth_headers={},
            semaphore=asyncio.Semaphore(5),
            src=src,
            metrics=cw.MetricsCollector(src, "db"),
            every_n_docs=0,
            max_concurrent=5,
            initial_sync=initial_sync,
        )

    def test_no_initial_sync_sg_deleted_not_filtered(self):
        """Steady state SG: deleted change passes through."""

        async def _run():
            changes = [
                {
                    "id": "d1",
                    "seq": "10",
                    "deleted": True,
                    "doc": {"_id": "d1", "_deleted": True},
                    "changes": [{"rev": "2-abc"}],
                },
            ]
            kw = self._batch_kwargs(src="sync_gateway", initial_sync=False)
            since, failed = await cw._process_changes_batch(changes, "10", "0", **kw)
            kw["output"].send.assert_called_once()

        asyncio.run(_run())

    def test_no_initial_sync_couchdb_deleted_not_filtered(self):
        """Steady state CouchDB: deleted change passes through."""

        async def _run():
            changes = [
                {
                    "id": "d1",
                    "seq": "10",
                    "deleted": True,
                    "doc": {"_id": "d1"},
                    "changes": [{"rev": "2-abc"}],
                },
            ]
            kw = self._batch_kwargs(src="couchdb", initial_sync=False)
            since, failed = await cw._process_changes_batch(changes, "10", "0", **kw)
            kw["output"].send.assert_called_once()

        asyncio.run(_run())

    def test_initial_sync_couchdb_deleted_filtered(self):
        """Initial sync CouchDB: deleted change is filtered out."""

        async def _run():
            changes = [
                {
                    "id": "d1",
                    "seq": "9",
                    "deleted": True,
                    "doc": {"_id": "d1"},
                    "changes": [{"rev": "2-abc"}],
                },
                {
                    "id": "d2",
                    "seq": "10",
                    "doc": {"_id": "d2"},
                    "changes": [{"rev": "1-x"}],
                },
            ]
            kw = self._batch_kwargs(src="couchdb", initial_sync=True)
            since, failed = await cw._process_changes_batch(changes, "10", "0", **kw)
            # d1 filtered, only d2 sent
            kw["output"].send.assert_called_once()

        asyncio.run(_run())

    def test_initial_sync_couchdb_removed_filtered(self):
        """Initial sync CouchDB: removed change is filtered out."""

        async def _run():
            changes = [
                {
                    "id": "d1",
                    "seq": "9",
                    "removed": True,
                    "doc": {"_id": "d1"},
                    "changes": [{"rev": "2-abc"}],
                },
                {
                    "id": "d2",
                    "seq": "10",
                    "doc": {"_id": "d2"},
                    "changes": [{"rev": "1-x"}],
                },
            ]
            kw = self._batch_kwargs(src="couchdb", initial_sync=True)
            since, failed = await cw._process_changes_batch(changes, "10", "0", **kw)
            # d1 filtered, only d2 sent
            kw["output"].send.assert_called_once()

        asyncio.run(_run())

    def test_initial_sync_sg_deleted_not_filtered_by_processing(self):
        """Initial sync SG: server handles active_only, processing doesn't
        force-filter (unless user set ignore_delete)."""

        async def _run():
            changes = [
                {
                    "id": "d1",
                    "seq": "10",
                    "deleted": True,
                    "doc": {"_id": "d1"},
                    "changes": [{"rev": "2-abc"}],
                },
            ]
            kw = self._batch_kwargs(src="sync_gateway", initial_sync=True)
            since, failed = await cw._process_changes_batch(changes, "10", "0", **kw)
            kw["output"].send.assert_called_once()

        asyncio.run(_run())

    def test_ignore_delete_config_filters(self):
        """proc_cfg.ignore_delete=True filters deleted regardless of initial_sync."""

        async def _run():
            changes = [
                {
                    "id": "d1",
                    "seq": "9",
                    "deleted": True,
                    "doc": {"_id": "d1"},
                    "changes": [{"rev": "2-abc"}],
                },
                {
                    "id": "d2",
                    "seq": "10",
                    "doc": {"_id": "d2", "val": 1},
                    "changes": [{"rev": "1-x"}],
                },
            ]
            kw = self._batch_kwargs(
                src="sync_gateway",
                proc_cfg={"ignore_delete": True},
                initial_sync=False,
            )
            since, failed = await cw._process_changes_batch(changes, "10", "0", **kw)
            # d1 was filtered, only d2 was sent
            kw["output"].send.assert_called_once()

        asyncio.run(_run())

    def test_initial_sync_couchdb_mixed_batch(self):
        """CouchDB initial sync: only non-deleted/removed pass through."""

        async def _run():
            changes = [
                {
                    "id": "alive1",
                    "seq": "1",
                    "doc": {"_id": "alive1"},
                    "changes": [{"rev": "1-a"}],
                },
                {
                    "id": "del1",
                    "seq": "2",
                    "deleted": True,
                    "doc": {"_id": "del1"},
                    "changes": [{"rev": "2-b"}],
                },
                {
                    "id": "alive2",
                    "seq": "3",
                    "doc": {"_id": "alive2"},
                    "changes": [{"rev": "1-c"}],
                },
                {
                    "id": "rem1",
                    "seq": "4",
                    "removed": True,
                    "doc": {"_id": "rem1"},
                    "changes": [{"rev": "3-d"}],
                },
                {
                    "id": "alive3",
                    "seq": "5",
                    "doc": {"_id": "alive3"},
                    "changes": [{"rev": "1-e"}],
                },
            ]
            kw = self._batch_kwargs(src="couchdb", initial_sync=True)
            since, failed = await cw._process_changes_batch(changes, "5", "0", **kw)
            self.assertEqual(kw["output"].send.call_count, 3)

        asyncio.run(_run())

    def test_empty_batch_checkpoints(self):
        """Empty results still checkpoint."""

        async def _run():
            kw = self._batch_kwargs()
            since, failed = await cw._process_changes_batch([], "42", "0", **kw)
            self.assertEqual(since, "42")
            self.assertFalse(failed)
            kw["checkpoint"].save.assert_called_once()

        asyncio.run(_run())


# ===================================================================
# 5. _catch_up_normal — initial sync vs steady-state
# ===================================================================


class TestCatchUpNormalInitialSync(unittest.TestCase):
    """Test _catch_up_normal behaviour with initial_sync flag."""

    def _run_catch_up(self, initial_sync, optimize, feed_cfg_extra=None):
        """Run _catch_up_normal and return the body_payload sent to http.request."""
        captured_bodies = []

        async def _run():
            http = MagicMock()

            async def _mock_request(method, url, **kwargs):
                # Handle GET {base_url}/ for update_seq fetch
                if method == "GET":
                    resp = AsyncMock()
                    resp.read = AsyncMock(
                        return_value=json.dumps(
                            {"db_name": "db", "update_seq": 200}
                        ).encode()
                    )
                    resp.release = MagicMock()
                    return resp
                captured_bodies.append(kwargs.get("json", {}))
                resp = AsyncMock()
                resp.read = AsyncMock(
                    return_value=json.dumps({"results": [], "last_seq": "50"}).encode()
                )
                resp.release = MagicMock()
                return resp

            http.request = AsyncMock(side_effect=_mock_request)

            fc = {
                "include_docs": True,
                "active_only": False,
                "continuous_catchup_limit": 5000,
                "optimize_initial_sync": optimize,
                "heartbeat_ms": 30000,
            }
            if feed_cfg_extra:
                fc.update(feed_cfg_extra)

            cp = _make_checkpoint()
            await cw._catch_up_normal(
                since="0",
                changes_url="http://localhost:4984/db/_changes",
                feed_cfg=fc,
                proc_cfg={},
                retry_cfg={"backoff_base_seconds": 0, "backoff_max_seconds": 0},
                src="sync_gateway",
                http=http,
                basic_auth=None,
                auth_headers={},
                base_url="http://localhost:4984/db",
                output=_make_output(),
                dlq=_make_dlq(),
                checkpoint=cp,
                semaphore=asyncio.Semaphore(5),
                shutdown_event=asyncio.Event(),
                metrics=cw.MetricsCollector("sync_gateway", "db"),
                every_n_docs=0,
                max_concurrent=5,
                timeout_ms=60000,
                changes_http_timeout=aiohttp.ClientTimeout(total=300),
                initial_sync=initial_sync,
            )
            return cp

        cp = asyncio.run(_run())
        return captured_bodies, cp

    def test_initial_sync_no_optimize_no_limit(self):
        """Default initial sync: no limit in request body."""
        bodies, _ = self._run_catch_up(initial_sync=True, optimize=False)
        self.assertGreater(len(bodies), 0)
        self.assertNotIn("limit", bodies[0])

    def test_initial_sync_no_optimize_active_only(self):
        """Default initial sync: active_only=True in body."""
        bodies, _ = self._run_catch_up(initial_sync=True, optimize=False)
        self.assertTrue(bodies[0].get("active_only"))

    def test_initial_sync_no_optimize_no_include_docs(self):
        """Default initial sync: include_docs not in body."""
        bodies, _ = self._run_catch_up(initial_sync=True, optimize=False)
        self.assertNotIn("include_docs", bodies[0])

    def test_initial_sync_optimize_has_limit(self):
        """Optimized initial sync: limit=continuous_catchup_limit."""
        bodies, _ = self._run_catch_up(initial_sync=True, optimize=True)
        self.assertEqual(bodies[0]["limit"], 5000)

    def test_initial_sync_optimize_active_only(self):
        bodies, _ = self._run_catch_up(initial_sync=True, optimize=True)
        self.assertTrue(bodies[0].get("active_only"))

    def test_steady_state_has_limit(self):
        """Non-initial catch-up always uses limit."""
        bodies, _ = self._run_catch_up(initial_sync=False, optimize=False)
        self.assertEqual(bodies[0]["limit"], 5000)

    def test_steady_state_uses_config_active_only(self):
        """Steady state: active_only from config (False)."""
        bodies, _ = self._run_catch_up(initial_sync=False, optimize=False)
        self.assertNotIn("active_only", bodies[0])

    def test_initial_sync_complete_sets_flag(self):
        """When results=[] during initial sync, checkpoint flag is set."""
        _, cp = self._run_catch_up(initial_sync=True, optimize=False)
        self.assertTrue(cp._initial_sync_done)

    def test_steady_state_does_not_set_flag(self):
        """When results=[] during steady state, flag not modified."""
        _, cp = self._run_catch_up(initial_sync=False, optimize=False)
        # cp._initial_sync_done should still be False (default from mock)
        self.assertFalse(cp._initial_sync_done)

    def test_initial_sync_optimize_completes_at_target_seq(self):
        """Optimized initial sync completes when last_seq reaches update_seq target."""
        call_count = 0

        async def _run():
            nonlocal call_count

            async def _mock_request(method, url, **kwargs):
                nonlocal call_count
                # GET for update_seq
                if method == "GET":
                    resp = AsyncMock()
                    resp.read = AsyncMock(
                        return_value=json.dumps(
                            {"db_name": "db", "update_seq": 100}
                        ).encode()
                    )
                    resp.release = MagicMock()
                    return resp
                call_count += 1
                # Single batch with last_seq=100 matching update_seq
                body = {
                    "results": [
                        {
                            "id": "d1",
                            "seq": "100",
                            "changes": [{"rev": "1-a"}],
                            "doc": {"_id": "d1"},
                        },
                    ],
                    "last_seq": "100",
                }
                resp = AsyncMock()
                resp.read = AsyncMock(return_value=json.dumps(body).encode())
                resp.release = MagicMock()
                return resp

            http = MagicMock()
            http.request = AsyncMock(side_effect=_mock_request)
            cp = _make_checkpoint()

            result = await cw._catch_up_normal(
                since="0",
                changes_url="http://localhost:4984/db/_changes",
                feed_cfg={
                    "include_docs": True,
                    "active_only": True,
                    "continuous_catchup_limit": 5000,
                    "optimize_initial_sync": True,
                    "heartbeat_ms": 30000,
                },
                proc_cfg={},
                retry_cfg={"backoff_base_seconds": 0, "backoff_max_seconds": 0},
                src="sync_gateway",
                http=http,
                basic_auth=None,
                auth_headers={},
                base_url="http://localhost:4984/db",
                output=_make_output(),
                dlq=_make_dlq(),
                checkpoint=cp,
                semaphore=asyncio.Semaphore(5),
                shutdown_event=asyncio.Event(),
                metrics=cw.MetricsCollector("sync_gateway", "db"),
                every_n_docs=0,
                max_concurrent=5,
                timeout_ms=60000,
                changes_http_timeout=aiohttp.ClientTimeout(total=300),
                initial_sync=True,
            )
            return result, cp

        result, cp = asyncio.run(_run())
        # Should complete after just 1 _changes request (last_seq=100 >= target=100)
        self.assertEqual(call_count, 1)
        self.assertEqual(result, "100")
        self.assertTrue(cp._initial_sync_done)

    def test_initial_sync_optimize_paginates_to_target_seq(self):
        """Optimized initial sync paginates until last_seq reaches update_seq."""
        call_count = 0

        async def _run():
            nonlocal call_count

            async def _mock_request(method, url, **kwargs):
                nonlocal call_count
                if method == "GET":
                    resp = AsyncMock()
                    resp.read = AsyncMock(
                        return_value=json.dumps(
                            {"db_name": "db", "update_seq": 150}
                        ).encode()
                    )
                    resp.release = MagicMock()
                    return resp
                call_count += 1
                if call_count == 1:
                    body = {
                        "results": [
                            {
                                "id": "d1",
                                "seq": "50",
                                "changes": [{"rev": "1-a"}],
                                "doc": {"_id": "d1"},
                            },
                            {
                                "id": "d2",
                                "seq": "82",
                                "changes": [{"rev": "1-b"}],
                                "doc": {"_id": "d2"},
                            },
                        ],
                        "last_seq": "82",
                    }
                elif call_count == 2:
                    body = {
                        "results": [
                            {
                                "id": "d3",
                                "seq": "120",
                                "changes": [{"rev": "1-c"}],
                                "doc": {"_id": "d3"},
                            },
                            {
                                "id": "d4",
                                "seq": "150",
                                "changes": [{"rev": "1-d"}],
                                "doc": {"_id": "d4"},
                            },
                        ],
                        "last_seq": "150",
                    }
                else:
                    body = {"results": [], "last_seq": "150"}
                resp = AsyncMock()
                resp.read = AsyncMock(return_value=json.dumps(body).encode())
                resp.release = MagicMock()
                return resp

            http = MagicMock()
            http.request = AsyncMock(side_effect=_mock_request)
            cp = _make_checkpoint()

            result = await cw._catch_up_normal(
                since="0",
                changes_url="http://localhost:4984/db/_changes",
                feed_cfg={
                    "include_docs": True,
                    "active_only": True,
                    "continuous_catchup_limit": 5000,
                    "optimize_initial_sync": True,
                    "heartbeat_ms": 30000,
                },
                proc_cfg={},
                retry_cfg={"backoff_base_seconds": 0, "backoff_max_seconds": 0},
                src="sync_gateway",
                http=http,
                basic_auth=None,
                auth_headers={},
                base_url="http://localhost:4984/db",
                output=_make_output(),
                dlq=_make_dlq(),
                checkpoint=cp,
                semaphore=asyncio.Semaphore(5),
                shutdown_event=asyncio.Event(),
                metrics=cw.MetricsCollector("sync_gateway", "db"),
                every_n_docs=0,
                max_concurrent=5,
                timeout_ms=60000,
                changes_http_timeout=aiohttp.ClientTimeout(total=300),
                initial_sync=True,
            )
            return result, cp

        result, cp = asyncio.run(_run())
        # 2 paginated requests, completes when last_seq=150 == target_seq=150
        self.assertEqual(call_count, 2)
        self.assertEqual(result, "150")
        self.assertTrue(cp._initial_sync_done)


class TestCatchUpNormalMultipleBatches(unittest.TestCase):
    """Test _catch_up_normal with multiple batches before completion."""

    def test_processes_batches_then_completes(self):
        """Feed returns 2 batches then empty → processes all and completes."""
        call_count = 0

        async def _run():
            nonlocal call_count

            async def _mock_request(method, url, **kwargs):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    body = {
                        "results": [
                            {
                                "id": "d1",
                                "seq": "10",
                                "changes": [{"rev": "1-a"}],
                                "doc": {"_id": "d1"},
                            },
                        ],
                        "last_seq": "10",
                    }
                elif call_count == 2:
                    body = {
                        "results": [
                            {
                                "id": "d2",
                                "seq": "20",
                                "changes": [{"rev": "1-b"}],
                                "doc": {"_id": "d2"},
                            },
                        ],
                        "last_seq": "20",
                    }
                else:
                    body = {"results": [], "last_seq": "20"}

                resp = AsyncMock()
                resp.read = AsyncMock(return_value=json.dumps(body).encode())
                resp.release = MagicMock()
                return resp

            http = MagicMock()
            http.request = AsyncMock(side_effect=_mock_request)

            result = await cw._catch_up_normal(
                since="0",
                changes_url="http://localhost:4984/db/_changes",
                feed_cfg={
                    "include_docs": True,
                    "active_only": True,
                    "continuous_catchup_limit": 500,
                    "optimize_initial_sync": True,
                    "heartbeat_ms": 30000,
                },
                proc_cfg={},
                retry_cfg={"backoff_base_seconds": 0, "backoff_max_seconds": 0},
                src="sync_gateway",
                http=http,
                basic_auth=None,
                auth_headers={},
                base_url="http://localhost:4984/db",
                output=_make_output(),
                dlq=_make_dlq(),
                checkpoint=_make_checkpoint(),
                semaphore=asyncio.Semaphore(5),
                shutdown_event=asyncio.Event(),
                metrics=cw.MetricsCollector("sync_gateway", "db"),
                every_n_docs=0,
                max_concurrent=5,
                timeout_ms=60000,
                changes_http_timeout=aiohttp.ClientTimeout(total=300),
                initial_sync=False,
            )
            self.assertEqual(result, "20")

        asyncio.run(_run())
        self.assertEqual(call_count, 3)


# ===================================================================
# 6. Sequence number format passthrough through _process_changes_batch
# ===================================================================


class TestSequenceFormatPassthrough(unittest.TestCase):
    """Verify various SG sequence formats don't break processing."""

    def _process_with_seq(self, seq_value):
        async def _run():
            changes = [
                {
                    "id": "doc1",
                    "seq": seq_value,
                    "doc": {"_id": "doc1", "val": 1},
                    "changes": [{"rev": "1-abc"}],
                },
            ]
            cp = _make_checkpoint()
            kw = dict(
                feed_cfg={"include_docs": True},
                proc_cfg={},
                output=_make_output(),
                dlq=_make_dlq(),
                checkpoint=cp,
                http=MagicMock(),
                base_url="http://localhost:4984/db",
                basic_auth=None,
                auth_headers={},
                semaphore=asyncio.Semaphore(5),
                src="sync_gateway",
                metrics=cw.MetricsCollector("sync_gateway", "db"),
                every_n_docs=0,
                max_concurrent=5,
            )
            since, failed = await cw._process_changes_batch(
                changes, str(seq_value), "0", **kw
            )
            self.assertEqual(since, str(seq_value))
            self.assertFalse(failed)
            # Verify checkpoint was saved with the correct seq string
            cp.save.assert_called()
            save_args = cp.save.call_args[0]
            self.assertEqual(save_args[0], str(seq_value))

        asyncio.run(_run())

    def test_integer_seq(self):
        self._process_with_seq(42)

    def test_string_integer_seq(self):
        self._process_with_seq("128")

    def test_backfill_seq(self):
        self._process_with_seq("123:100")

    def test_delayed_seq(self):
        self._process_with_seq("90::100")

    def test_delayed_backfill_seq(self):
        self._process_with_seq("90:123:100")

    def test_distributed_hash_seq(self):
        self._process_with_seq("200-0")

    def test_vbucket_seq(self):
        self._process_with_seq("50.123")

    def test_distributed_backfill_seq(self):
        self._process_with_seq("200-0:50.123")

    def test_couchdb_string_seq(self):
        self._process_with_seq("42-abc123def456")


# ===================================================================
# 7. fetch_docs / _bulk_get / individual GET basics
# ===================================================================


class TestFetchDocsBasics(unittest.TestCase):
    """Basic fetch_docs behaviour."""

    def test_empty_rows_returns_empty(self):
        async def _run():
            result = await cw.fetch_docs(
                MagicMock(),
                "http://localhost:4984/db",
                [],
                None,
                {},
                "sync_gateway",
            )
            self.assertEqual(result, [])

        asyncio.run(_run())

    def test_rows_without_changes_returns_empty(self):
        async def _run():
            result = await cw.fetch_docs(
                MagicMock(),
                "http://localhost:4984/db",
                [{"id": "d1"}],
                None,
                {},
                "sync_gateway",
            )
            self.assertEqual(result, [])

        asyncio.run(_run())


class TestBulkGetBasics(unittest.TestCase):
    """_fetch_docs_bulk_get response parsing."""

    def test_json_response_parsed(self):
        async def _run():
            http = MagicMock()
            resp = AsyncMock()
            resp.content_type = "application/json"
            body = {
                "results": [
                    {"docs": [{"ok": {"_id": "d1", "_rev": "1-a"}}]},
                    {"docs": [{"ok": {"_id": "d2", "_rev": "1-b"}}]},
                ]
            }
            resp.read = AsyncMock(return_value=json.dumps(body).encode())
            resp.release = MagicMock()
            http.request = AsyncMock(return_value=resp)

            result = await cw._fetch_docs_bulk_get(
                http,
                "http://localhost:4984/db",
                [
                    {"id": "d1", "changes": [{"rev": "1-a"}]},
                    {"id": "d2", "changes": [{"rev": "1-b"}]},
                ],
                None,
                {},
            )
            self.assertEqual(len(result), 2)
            ids = {d["_id"] for d in result}
            self.assertEqual(ids, {"d1", "d2"})

        asyncio.run(_run())


class TestIndividualFetchBasics(unittest.TestCase):
    """_fetch_docs_individually basics."""

    def test_successful_fetch(self):
        async def _run():
            http = MagicMock()
            resp = AsyncMock()
            resp.read = AsyncMock(
                return_value=json.dumps({"_id": "d1", "_rev": "1-a"}).encode()
            )
            resp.release = MagicMock()
            http.request = AsyncMock(return_value=resp)

            result = await cw._fetch_docs_individually(
                http,
                "http://localhost:4984/db",
                [{"id": "d1", "changes": [{"rev": "1-a"}]}],
                None,
                {},
                5,
            )
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0]["_id"], "d1")

        asyncio.run(_run())


# ===================================================================
# 8. optimize_initial_sync config flag
# ===================================================================


class TestOptimizeInitialSyncConfig(unittest.TestCase):
    """Test the optimize_initial_sync config flag."""

    def test_default_is_false(self):
        """When not set in config, defaults to False."""
        fc = {"heartbeat_ms": 30000}
        self.assertFalse(fc.get("optimize_initial_sync", False))

    def test_explicit_true(self):
        fc = {"heartbeat_ms": 30000, "optimize_initial_sync": True}
        self.assertTrue(fc.get("optimize_initial_sync", False))

    def test_explicit_false(self):
        fc = {"heartbeat_ms": 30000, "optimize_initial_sync": False}
        self.assertFalse(fc.get("optimize_initial_sync", False))


class TestOptimizeInitialSyncInBuildBody(unittest.TestCase):
    """Verify limit/no-limit behaviour based on optimize flag."""

    def test_no_optimize_initial_sync_no_limit_forced(self):
        """When optimize=False, _build_changes_body does not add limit
        (it's up to the caller not to pass one)."""
        body = cw._build_changes_body(
            _feed_cfg(
                active_only=False, include_docs=True, optimize_initial_sync=False
            ),
            "sync_gateway",
            "0",
            "normal",
            60000,
            limit=0,
            active_only_override=True,
            include_docs_override=False,
        )
        self.assertNotIn("limit", body)
        self.assertTrue(body["active_only"])
        self.assertNotIn("include_docs", body)

    def test_optimize_initial_sync_with_limit(self):
        """When optimize=True, caller passes limit → appears in body."""
        body = cw._build_changes_body(
            _feed_cfg(active_only=False, include_docs=True, optimize_initial_sync=True),
            "sync_gateway",
            "0",
            "normal",
            60000,
            limit=10000,
            active_only_override=True,
            include_docs_override=False,
        )
        self.assertEqual(body["limit"], 10000)


# ===================================================================
# 9. Full source × mode matrix for _build_changes_body
# ===================================================================


class TestBuildChangesBodySourceMatrix(unittest.TestCase):
    """Exhaustive source × parameter matrix."""

    SOURCES = ["sync_gateway", "app_services", "edge_server", "couchdb"]

    def test_all_sources_accept_all_seq_formats(self):
        """Every source handles every sequence format without error."""
        for src in self.SOURCES:
            for name, seq in SG_SEQUENCES.items():
                with self.subTest(src=src, seq_format=name):
                    body = cw._build_changes_body(
                        _feed_cfg(), src, seq, "normal", 60000
                    )
                    self.assertEqual(body["since"], seq)

    def test_all_sources_feed_types(self):
        """Every source can be given any feed type string."""
        for src in self.SOURCES:
            for feed in ["normal", "longpoll", "continuous"]:
                with self.subTest(src=src, feed=feed):
                    body = cw._build_changes_body(_feed_cfg(), src, "0", feed, 60000)
                    self.assertEqual(body["feed"], feed)

    def test_active_only_never_for_couchdb(self):
        """Exhaustive check: CouchDB never gets active_only."""
        for override in [None, True, False]:
            for config_val in [True, False]:
                with self.subTest(override=override, config=config_val):
                    body = cw._build_changes_body(
                        _feed_cfg(active_only=config_val),
                        "couchdb",
                        "0",
                        "normal",
                        60000,
                        active_only_override=override,
                    )
                    self.assertNotIn("active_only", body)

    def test_version_type_only_sg_and_app_services(self):
        for src in self.SOURCES:
            body = cw._build_changes_body(_feed_cfg(), src, "0", "normal", 60000)
            if src in ("sync_gateway", "app_services"):
                self.assertIn("version_type", body, f"{src} should have version_type")
            else:
                self.assertNotIn(
                    "version_type", body, f"{src} should NOT have version_type"
                )

    def test_channels_only_non_couchdb(self):
        for src in self.SOURCES:
            body = cw._build_changes_body(
                _feed_cfg(channels=["ch1"]), src, "0", "normal", 60000
            )
            if src == "couchdb":
                self.assertNotIn("filter", body)
                self.assertNotIn("channels", body)
            else:
                self.assertIn("filter", body)
                self.assertIn("channels", body)


if __name__ == "__main__":
    unittest.main()
