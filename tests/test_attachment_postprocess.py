#!/usr/bin/env python3
"""
Unit tests for rest.attachment_postprocess (Phase 3 — Post-Processing).

Covers:
  - action=none (noop)
  - action=update_doc (PUT with attachments_external, remove_attachments)
  - action=set_ttl (PUT with _exp)
  - action=delete_doc (DELETE)
  - action=delete_attachments (sequential DELETE per attachment)
  - action=purge (POST _purge to admin port)
  - 404 handling (on_doc_missing: skip vs fail)
  - 409 conflict resolution (re-fetch, stale attachment detection)
  - halt_on_failure error propagation
  - Metrics counter increments
  - Unknown action handling
"""

import asyncio
import json
import os
import sys
import time
import unittest
from unittest.mock import AsyncMock, MagicMock

# Ensure the module under test is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from rest.attachment_config import (
    AttachmentConfig,
    AttachmentPostProcessConfig,
    AttachmentAdminAuthConfig,
)
from rest.attachment_postprocess import AttachmentPostProcessor
from rest.attachment_upload import AttachmentUploadResult
from rest.attachments import AttachmentError
from rest.changes_http import ClientHTTPError


# ── helpers ──────────────────────────────────────────────────────────────────


def _make_upload_result(name="photo.jpg", location="s3://bucket/photo.jpg"):
    return AttachmentUploadResult(
        attachment_name=name,
        destination_type="s3",
        key="attachments/doc1/%s" % name,
        location=location,
        content_type="image/jpeg",
        length=2048,
        digest="md5-abc==",
        uploaded_at="2026-04-19T12:00:00Z",
    )


def _make_http_mock(status=200, json_body=None):
    """Build a mock RetryableHTTP whose .request() returns a mock response."""
    http = AsyncMock()
    resp = AsyncMock()
    resp.status = status
    resp.json = AsyncMock(return_value=json_body or {})
    resp.text = AsyncMock(return_value="")
    resp.release = MagicMock()
    http.request = AsyncMock(return_value=resp)
    return http


def _make_processor(action="none", **pp_kwargs):
    pp = AttachmentPostProcessConfig(action=action, **pp_kwargs)
    cfg = AttachmentConfig(enabled=True, post_process=pp)
    return AttachmentPostProcessor(cfg)


# ===================================================================
# action = none
# ===================================================================


class TestActionNone(unittest.TestCase):
    def test_noop_returns_doc_unchanged(self):
        proc = _make_processor(action="none")
        doc = {"_id": "doc1", "_rev": "1-abc"}
        http = _make_http_mock()

        async def _run():
            result = await proc.post_process(
                doc, {}, "http://sg:4984/db", http, None, {}
            )
            self.assertEqual(result, doc)
            http.request.assert_not_called()

        asyncio.run(_run())


# ===================================================================
# action = update_doc
# ===================================================================


class TestActionUpdateDoc(unittest.TestCase):
    def test_update_doc_adds_external_field(self):
        proc = _make_processor(action="update_doc")
        doc = {"_id": "doc1", "_rev": "1-abc", "title": "Test"}
        uploaded = {"photo.jpg": _make_upload_result()}
        http = _make_http_mock(json_body={"ok": True, "rev": "2-def"})

        async def _run():
            result = await proc.post_process(
                doc, uploaded, "http://sg:4984/db", http, None, {}
            )
            self.assertIn("attachments_external", result)
            ext = result["attachments_external"]
            self.assertIn("photo.jpg", ext)
            self.assertEqual(ext["photo.jpg"]["url"], "s3://bucket/photo.jpg")
            self.assertEqual(ext["photo.jpg"]["length"], 2048)
            self.assertEqual(result["_rev"], "2-def")

            # Verify PUT was called
            http.request.assert_called_once()
            call_args = http.request.call_args
            self.assertEqual(call_args[0][0], "PUT")

        asyncio.run(_run())

    def test_update_doc_custom_field_name(self):
        proc = _make_processor(action="update_doc", update_field="external_files")
        doc = {"_id": "doc1", "_rev": "1-abc"}
        uploaded = {"photo.jpg": _make_upload_result()}
        http = _make_http_mock(json_body={"ok": True, "rev": "2-def"})

        async def _run():
            result = await proc.post_process(
                doc, uploaded, "http://sg:4984/db", http, None, {}
            )
            self.assertIn("external_files", result)
            self.assertNotIn("attachments_external", result)

        asyncio.run(_run())

    def test_update_doc_removes_attachments(self):
        proc = _make_processor(
            action="update_doc", remove_attachments_after_upload=True
        )
        doc = {
            "_id": "doc1",
            "_rev": "1-abc",
            "_attachments": {"photo.jpg": {"stub": True}},
        }
        uploaded = {"photo.jpg": _make_upload_result()}
        http = _make_http_mock(json_body={"ok": True, "rev": "2-def"})

        async def _run():
            result = await proc.post_process(
                doc, uploaded, "http://sg:4984/db", http, None, {}
            )
            self.assertNotIn("_attachments", result)
            self.assertIn("attachments_external", result)

        asyncio.run(_run())

    def test_update_doc_preserves_attachments_by_default(self):
        proc = _make_processor(action="update_doc")
        doc = {
            "_id": "doc1",
            "_rev": "1-abc",
            "_attachments": {"photo.jpg": {"stub": True}},
        }
        uploaded = {"photo.jpg": _make_upload_result()}
        http = _make_http_mock(json_body={"ok": True, "rev": "2-def"})

        async def _run():
            result = await proc.post_process(
                doc, uploaded, "http://sg:4984/db", http, None, {}
            )
            self.assertIn("_attachments", result)

        asyncio.run(_run())

    def test_update_doc_multiple_attachments(self):
        proc = _make_processor(action="update_doc")
        doc = {"_id": "doc1", "_rev": "1-abc"}
        uploaded = {
            "a.jpg": _make_upload_result("a.jpg", "s3://bucket/a.jpg"),
            "b.png": _make_upload_result("b.png", "s3://bucket/b.png"),
        }
        http = _make_http_mock(json_body={"ok": True, "rev": "2-def"})

        async def _run():
            result = await proc.post_process(
                doc, uploaded, "http://sg:4984/db", http, None, {}
            )
            ext = result["attachments_external"]
            self.assertEqual(len(ext), 2)
            self.assertIn("a.jpg", ext)
            self.assertIn("b.png", ext)

        asyncio.run(_run())


# ===================================================================
# action = set_ttl
# ===================================================================


class TestActionSetTTL(unittest.TestCase):
    def test_set_ttl_adds_exp_field(self):
        proc = _make_processor(action="set_ttl", ttl_seconds=3600)
        doc = {"_id": "doc1", "_rev": "1-abc"}
        http = _make_http_mock(json_body={"ok": True, "rev": "2-def"})

        async def _run():
            before = int(time.time())
            result = await proc.post_process(
                doc, {}, "http://sg:4984/db", http, None, {}
            )
            after = int(time.time())
            self.assertIn("_exp", result)
            self.assertGreaterEqual(result["_exp"], before + 3600)
            self.assertLessEqual(result["_exp"], after + 3600)
            self.assertEqual(result["_rev"], "2-def")

        asyncio.run(_run())


# ===================================================================
# action = delete_doc
# ===================================================================


class TestActionDeleteDoc(unittest.TestCase):
    def test_delete_doc_sends_delete(self):
        proc = _make_processor(action="delete_doc")
        doc = {"_id": "doc1", "_rev": "1-abc"}
        http = _make_http_mock()

        async def _run():
            result = await proc.post_process(
                doc, {}, "http://sg:4984/db", http, None, {}
            )
            http.request.assert_called_once()
            call_args = http.request.call_args
            self.assertEqual(call_args[0][0], "DELETE")
            self.assertIn("doc1", call_args[0][1])
            self.assertIn("rev=1-abc", call_args[0][1])

        asyncio.run(_run())

    def test_delete_doc_404_skips(self):
        proc = _make_processor(action="delete_doc", on_doc_missing="skip")
        doc = {"_id": "doc1", "_rev": "1-abc"}
        http = AsyncMock()
        http.request = AsyncMock(side_effect=ClientHTTPError(404, "not found"))

        async def _run():
            result = await proc.post_process(
                doc, {}, "http://sg:4984/db", http, None, {}
            )
            self.assertEqual(result, doc)

        asyncio.run(_run())


# ===================================================================
# action = delete_attachments
# ===================================================================


class TestActionDeleteAttachments(unittest.TestCase):
    def test_delete_attachments_sequential(self):
        proc = _make_processor(action="delete_attachments")
        doc = {"_id": "doc1", "_rev": "1-abc"}
        uploaded = {
            "a.jpg": _make_upload_result("a.jpg"),
            "b.png": _make_upload_result("b.png"),
        }

        call_count = 0

        async def _sequential_delete(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            resp = AsyncMock()
            # Each delete returns a new rev
            resp.json = AsyncMock(
                return_value={"ok": True, "rev": "%d-new" % call_count}
            )
            resp.release = MagicMock()
            return resp

        http = AsyncMock()
        http.request = AsyncMock(side_effect=_sequential_delete)

        async def _run():
            result = await proc.post_process(
                doc, uploaded, "http://sg:4984/db", http, None, {}
            )
            self.assertEqual(call_count, 2)
            # Final rev should be the last delete's rev
            self.assertEqual(result["_rev"], "2-new")

        asyncio.run(_run())

    def test_delete_attachment_404_skips(self):
        proc = _make_processor(action="delete_attachments")
        doc = {"_id": "doc1", "_rev": "1-abc"}
        uploaded = {"gone.jpg": _make_upload_result("gone.jpg")}
        http = AsyncMock()
        http.request = AsyncMock(side_effect=ClientHTTPError(404, "not found"))

        async def _run():
            result = await proc.post_process(
                doc, uploaded, "http://sg:4984/db", http, None, {}
            )
            # Should not raise, attachment already gone
            self.assertEqual(result["_id"], "doc1")

        asyncio.run(_run())


# ===================================================================
# action = purge
# ===================================================================


class TestActionPurge(unittest.TestCase):
    def test_purge_posts_to_admin_url(self):
        proc = _make_processor(
            action="purge",
            admin_url="http://sg:4985",
            admin_auth=AttachmentAdminAuthConfig(
                method="basic", username="admin", password="pass"
            ),
        )
        doc = {"_id": "doc1", "_rev": "1-abc"}
        http = _make_http_mock()

        async def _run():
            result = await proc.post_process(
                doc, {}, "http://sg:4984/db", http, None, {}
            )
            http.request.assert_called_once()
            call_args = http.request.call_args
            self.assertEqual(call_args[0][0], "POST")
            self.assertIn("_purge", call_args[0][1])
            self.assertIn("db", call_args[0][1])
            # Verify body contains doc_id
            body = json.loads(call_args[1]["data"])
            self.assertIn("doc1", body)

        asyncio.run(_run())

    def test_purge_no_admin_url_raises(self):
        proc = _make_processor(action="purge", admin_url="")
        doc = {"_id": "doc1", "_rev": "1-abc"}
        http = _make_http_mock()

        pp = proc._config.post_process
        cfg = AttachmentConfig(enabled=True, post_process=pp, halt_on_failure=True)
        proc_halt = AttachmentPostProcessor(cfg)

        async def _run():
            with self.assertRaises(AttachmentError):
                await proc_halt.post_process(
                    doc, {}, "http://sg:4984/db", http, None, {}
                )

        asyncio.run(_run())


# ===================================================================
# 404 Missing Doc Handling
# ===================================================================


class TestMissingDocHandling(unittest.TestCase):
    def test_on_doc_missing_skip(self):
        proc = _make_processor(action="update_doc", on_doc_missing="skip")
        doc = {"_id": "doc1", "_rev": "1-abc"}
        uploaded = {"photo.jpg": _make_upload_result()}
        http = AsyncMock()
        http.request = AsyncMock(side_effect=ClientHTTPError(404, "not found"))
        metrics = MagicMock()
        proc._metrics = metrics

        async def _run():
            result = await proc.post_process(
                doc, uploaded, "http://sg:4984/db", http, None, {}
            )
            self.assertEqual(result, doc)
            # Should have incremented skipped counter
            inc_calls = {c[0][0] for c in metrics.inc.call_args_list}
            self.assertIn("attachments_post_process_skipped_total", inc_calls)

        asyncio.run(_run())

    def test_on_doc_missing_fail(self):
        pp = AttachmentPostProcessConfig(action="update_doc", on_doc_missing="fail")
        cfg = AttachmentConfig(enabled=True, post_process=pp, halt_on_failure=True)
        proc = AttachmentPostProcessor(cfg)
        doc = {"_id": "doc1", "_rev": "1-abc"}
        uploaded = {"photo.jpg": _make_upload_result()}
        http = AsyncMock()
        http.request = AsyncMock(side_effect=ClientHTTPError(404, "not found"))

        async def _run():
            with self.assertRaises(AttachmentError):
                await proc.post_process(
                    doc, uploaded, "http://sg:4984/db", http, None, {}
                )

        asyncio.run(_run())


# ===================================================================
# 409 Conflict Resolution
# ===================================================================


class TestConflictResolution(unittest.TestCase):
    def test_conflict_retry_succeeds(self):
        proc = _make_processor(action="update_doc", max_conflict_retries=3)
        doc = {"_id": "doc1", "_rev": "1-abc"}
        uploaded = {"photo.jpg": _make_upload_result()}

        call_count = 0

        async def _conflict_then_ok(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            method = args[0]
            if method == "GET":
                # Re-fetch returns refreshed doc with attachment still present
                resp = AsyncMock()
                resp.json = AsyncMock(
                    return_value={
                        "_id": "doc1",
                        "_rev": "3-xyz",
                        "_attachments": {"photo.jpg": {"stub": True}},
                    }
                )
                resp.release = MagicMock()
                return resp
            if method == "PUT" and call_count == 1:
                raise ClientHTTPError(409, "conflict")
            resp = AsyncMock()
            resp.json = AsyncMock(return_value={"ok": True, "rev": "4-new"})
            resp.release = MagicMock()
            return resp

        http = AsyncMock()
        http.request = AsyncMock(side_effect=_conflict_then_ok)

        async def _run():
            result = await proc.post_process(
                doc, uploaded, "http://sg:4984/db", http, None, {}
            )
            self.assertIn("attachments_external", result)
            self.assertEqual(result["_rev"], "4-new")

        asyncio.run(_run())

    def test_conflict_stale_attachment_skips(self):
        proc = _make_processor(action="update_doc", max_conflict_retries=3)
        doc = {"_id": "doc1", "_rev": "1-abc"}
        uploaded = {"photo.jpg": _make_upload_result()}
        metrics = MagicMock()
        proc._metrics = metrics

        call_count = 0

        async def _conflict_then_stale(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            method = args[0]
            if method == "GET":
                # Refreshed doc has NO attachments (stale)
                resp = AsyncMock()
                resp.json = AsyncMock(
                    return_value={
                        "_id": "doc1",
                        "_rev": "5-xyz",
                        "_attachments": {},
                    }
                )
                resp.release = MagicMock()
                return resp
            if method == "PUT":
                raise ClientHTTPError(409, "conflict")
            return AsyncMock()

        http = AsyncMock()
        http.request = AsyncMock(side_effect=_conflict_then_stale)

        async def _run():
            result = await proc.post_process(
                doc, uploaded, "http://sg:4984/db", http, None, {}
            )
            # Should return original doc (stale attachment → skip)
            self.assertNotIn("attachments_external", result)
            inc_calls = {c[0][0] for c in metrics.inc.call_args_list}
            self.assertIn("attachments_stale_total", inc_calls)

        asyncio.run(_run())


# ===================================================================
# halt_on_failure
# ===================================================================


class TestHaltOnFailure(unittest.TestCase):
    def test_halt_on_failure_true_raises(self):
        pp = AttachmentPostProcessConfig(action="delete_doc")
        cfg = AttachmentConfig(enabled=True, post_process=pp, halt_on_failure=True)
        proc = AttachmentPostProcessor(cfg)
        doc = {"_id": "doc1", "_rev": "1-abc"}
        http = AsyncMock()
        http.request = AsyncMock(side_effect=ClientHTTPError(500, "server error"))

        async def _run():
            with self.assertRaises(AttachmentError):
                await proc.post_process(doc, {}, "http://sg:4984/db", http, None, {})

        asyncio.run(_run())

    def test_halt_on_failure_false_continues(self):
        pp = AttachmentPostProcessConfig(action="delete_doc")
        cfg = AttachmentConfig(enabled=True, post_process=pp, halt_on_failure=False)
        proc = AttachmentPostProcessor(cfg)
        doc = {"_id": "doc1", "_rev": "1-abc"}
        http = AsyncMock()
        http.request = AsyncMock(side_effect=ClientHTTPError(500, "server error"))

        async def _run():
            result = await proc.post_process(
                doc, {}, "http://sg:4984/db", http, None, {}
            )
            self.assertEqual(result, doc)

        asyncio.run(_run())


# ===================================================================
# Metrics
# ===================================================================


class TestMetrics(unittest.TestCase):
    def test_post_process_total_incremented(self):
        proc = _make_processor(action="update_doc")
        metrics = MagicMock()
        proc._metrics = metrics
        doc = {"_id": "doc1", "_rev": "1-abc"}
        uploaded = {"photo.jpg": _make_upload_result()}
        http = _make_http_mock(json_body={"ok": True, "rev": "2-def"})

        async def _run():
            await proc.post_process(doc, uploaded, "http://sg:4984/db", http, None, {})
            inc_calls = {c[0][0] for c in metrics.inc.call_args_list}
            self.assertIn("attachments_post_process_total", inc_calls)

        asyncio.run(_run())

    def test_conflict_retry_metric(self):
        proc = _make_processor(action="update_doc", max_conflict_retries=2)
        metrics = MagicMock()
        proc._metrics = metrics
        doc = {"_id": "doc1", "_rev": "1-abc"}
        uploaded = {"photo.jpg": _make_upload_result()}

        call_count = 0

        async def _always_conflict(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            method = args[0]
            if method == "GET":
                resp = AsyncMock()
                resp.json = AsyncMock(
                    return_value={
                        "_id": "doc1",
                        "_rev": "2-xyz",
                        "_attachments": {"photo.jpg": {"stub": True}},
                    }
                )
                resp.release = MagicMock()
                return resp
            raise ClientHTTPError(409, "conflict")

        http = AsyncMock()
        http.request = AsyncMock(side_effect=_always_conflict)

        async def _run():
            await proc.post_process(doc, uploaded, "http://sg:4984/db", http, None, {})
            inc_calls = [c[0][0] for c in metrics.inc.call_args_list]
            self.assertIn("attachments_conflict_retries_total", inc_calls)

        asyncio.run(_run())


# ===================================================================
# Unknown action
# ===================================================================


class TestUnknownAction(unittest.TestCase):
    def test_unknown_action_returns_doc(self):
        proc = _make_processor(action="warp_drive")
        doc = {"_id": "doc1", "_rev": "1-abc"}
        http = _make_http_mock()

        async def _run():
            result = await proc.post_process(
                doc, {}, "http://sg:4984/db", http, None, {}
            )
            self.assertEqual(result, doc)
            http.request.assert_not_called()

        asyncio.run(_run())


# ===================================================================
# Build external map
# ===================================================================


class TestBuildExternalMap(unittest.TestCase):
    def test_build_external_map(self):
        uploaded = {
            "photo.jpg": _make_upload_result("photo.jpg", "s3://bucket/photo.jpg"),
        }
        ext = AttachmentPostProcessor._build_external_map(uploaded)
        self.assertEqual(ext["photo.jpg"]["url"], "s3://bucket/photo.jpg")
        self.assertEqual(ext["photo.jpg"]["content_type"], "image/jpeg")
        self.assertEqual(ext["photo.jpg"]["length"], 2048)
        self.assertEqual(ext["photo.jpg"]["digest"], "md5-abc==")
        self.assertEqual(ext["photo.jpg"]["uploaded_at"], "2026-04-19T12:00:00Z")


# ===================================================================
# Integration: wiring in AttachmentProcessor
# ===================================================================


class TestProcessorIntegration(unittest.TestCase):
    """Verify that AttachmentProcessor creates and invokes the post-processor."""

    def test_processor_creates_post_processor(self):
        from rest.attachments import AttachmentProcessor

        pp = AttachmentPostProcessConfig(action="update_doc")
        cfg = AttachmentConfig(enabled=True, post_process=pp)
        proc = AttachmentProcessor(cfg)
        self.assertIsNotNone(proc._post_processor)

    def test_processor_no_post_processor_when_none(self):
        from rest.attachments import AttachmentProcessor

        cfg = AttachmentConfig(enabled=True)
        proc = AttachmentProcessor(cfg)
        self.assertIsNone(proc._post_processor)


if __name__ == "__main__":
    unittest.main()
