#!/usr/bin/env python3
"""
Unit tests for rest.attachment_config and rest.attachments.

Covers:
  - AttachmentConfig parsing and defaults
  - Attachment detection from documents
  - Attachment filtering (content_type, size, name_pattern)
  - Digest verification (md5, sha1)
  - Edge Server skip behaviour
  - Dry-run mode
"""

import asyncio
import base64
import hashlib
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock

# Ensure the module under test is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from rest.attachment_config import (
    AttachmentConfig,
    AttachmentFilterConfig,
    DEFAULT_ATTACHMENT_CONFIG,
    parse_attachment_config,
)
from rest.attachments import AttachmentError, AttachmentProcessor


# ===================================================================
# Config Tests
# ===================================================================


class TestAttachmentConfig(unittest.TestCase):
    """Tests for parse_attachment_config and AttachmentConfig defaults."""

    def test_default_config_disabled(self):
        self.assertFalse(DEFAULT_ATTACHMENT_CONFIG.enabled)

    def test_parse_empty_dict(self):
        cfg = parse_attachment_config({})
        self.assertFalse(cfg.enabled)

    def test_parse_enabled(self):
        cfg = parse_attachment_config({"enabled": True})
        self.assertTrue(cfg.enabled)

    def test_parse_full_config(self):
        raw = {
            "enabled": True,
            "dry_run": True,
            "mode": "bulk",
            "on_missing_attachment": "fail",
            "partial_success": "abort",
            "halt_on_failure": False,
            "skip_on_edge_server": False,
            "filter": {
                "content_types": ["image/*"],
                "min_size_bytes": 100,
                "max_size_bytes": 5000,
            },
            "fetch": {
                "max_concurrent_downloads": 10,
                "verify_digest": False,
            },
        }
        cfg = parse_attachment_config(raw)
        self.assertTrue(cfg.enabled)
        self.assertTrue(cfg.dry_run)
        self.assertEqual(cfg.mode, "bulk")
        self.assertEqual(cfg.on_missing_attachment, "fail")
        self.assertEqual(cfg.partial_success, "abort")
        self.assertFalse(cfg.halt_on_failure)
        self.assertFalse(cfg.skip_on_edge_server)
        self.assertEqual(cfg.filter.content_types, ["image/*"])
        self.assertEqual(cfg.filter.min_size_bytes, 100)
        self.assertEqual(cfg.filter.max_size_bytes, 5000)
        self.assertEqual(cfg.fetch.max_concurrent_downloads, 10)
        self.assertFalse(cfg.fetch.verify_digest)

    def test_parse_filter_content_types(self):
        raw = {"filter": {"content_types": ["image/*", "text/plain"]}}
        cfg = parse_attachment_config(raw)
        self.assertEqual(cfg.filter.content_types, ["image/*", "text/plain"])

    def test_parse_nested_defaults(self):
        raw = {"filter": {"min_size_bytes": 512}}
        cfg = parse_attachment_config(raw)
        self.assertEqual(cfg.filter.min_size_bytes, 512)
        # Missing fields should get defaults
        self.assertEqual(cfg.filter.max_size_bytes, 0)
        self.assertEqual(cfg.filter.content_types, [])
        self.assertEqual(cfg.filter.name_pattern, "")


# ===================================================================
# Detection Tests
# ===================================================================


class TestAttachmentDetection(unittest.TestCase):
    """Tests for AttachmentProcessor._detect()."""

    def test_detect_no_attachments(self):
        self.assertEqual(AttachmentProcessor._detect({"_id": "doc1"}), {})

    def test_detect_empty_attachments(self):
        self.assertEqual(AttachmentProcessor._detect({"_attachments": {}}), {})

    def test_detect_with_attachments(self):
        stubs = {"logo.png": {"content_type": "image/png", "length": 1024}}
        doc = {"_id": "doc1", "_attachments": stubs}
        self.assertEqual(AttachmentProcessor._detect(doc), stubs)

    def test_detect_non_dict_attachments(self):
        self.assertEqual(
            AttachmentProcessor._detect({"_attachments": "not_a_dict"}), {}
        )


# ===================================================================
# Filter Tests
# ===================================================================


class TestAttachmentFilter(unittest.TestCase):
    """Tests for AttachmentProcessor._apply_filters()."""

    def _make_processor(self, **filter_kwargs):
        filt = AttachmentFilterConfig(**filter_kwargs)
        cfg = AttachmentConfig(enabled=True, filter=filt)
        return AttachmentProcessor(cfg)

    def test_filter_no_filters(self):
        proc = self._make_processor()
        stubs = {"a.txt": {"content_type": "text/plain", "length": 100}}
        self.assertEqual(proc._apply_filters(stubs, "doc1"), stubs)

    def test_filter_content_type_allow(self):
        proc = self._make_processor(content_types=["image/*"])
        stubs = {
            "pic.png": {"content_type": "image/png", "length": 100},
            "data.json": {"content_type": "application/json", "length": 200},
        }
        result = proc._apply_filters(stubs, "doc1")
        self.assertIn("pic.png", result)
        self.assertNotIn("data.json", result)

    def test_filter_content_type_reject(self):
        proc = self._make_processor(reject_content_types=["image/*"])
        stubs = {
            "pic.png": {"content_type": "image/png", "length": 100},
            "data.json": {"content_type": "application/json", "length": 200},
        }
        result = proc._apply_filters(stubs, "doc1")
        self.assertNotIn("pic.png", result)
        self.assertIn("data.json", result)

    def test_filter_min_size(self):
        proc = self._make_processor(min_size_bytes=500)
        stubs = {
            "small.txt": {"content_type": "text/plain", "length": 100},
            "big.txt": {"content_type": "text/plain", "length": 1000},
        }
        result = proc._apply_filters(stubs, "doc1")
        self.assertNotIn("small.txt", result)
        self.assertIn("big.txt", result)

    def test_filter_max_size(self):
        proc = self._make_processor(max_size_bytes=500)
        stubs = {
            "small.txt": {"content_type": "text/plain", "length": 100},
            "big.txt": {"content_type": "text/plain", "length": 1000},
        }
        result = proc._apply_filters(stubs, "doc1")
        self.assertIn("small.txt", result)
        self.assertNotIn("big.txt", result)

    def test_filter_max_total_bytes_per_doc(self):
        proc = self._make_processor(max_total_bytes_per_doc=500)
        stubs = {
            "a.txt": {"content_type": "text/plain", "length": 300},
            "b.txt": {"content_type": "text/plain", "length": 300},
        }
        result = proc._apply_filters(stubs, "doc1")
        self.assertEqual(result, {})

    def test_filter_name_pattern(self):
        proc = self._make_processor(name_pattern=r"\.png$")
        stubs = {
            "logo.png": {"content_type": "image/png", "length": 100},
            "readme.txt": {"content_type": "text/plain", "length": 200},
        }
        result = proc._apply_filters(stubs, "doc1")
        self.assertIn("logo.png", result)
        self.assertNotIn("readme.txt", result)


# ===================================================================
# Digest Verification Tests
# ===================================================================


class TestDigestVerification(unittest.TestCase):
    """Tests for AttachmentProcessor._verify_digest()."""

    def test_verify_md5_correct(self):
        data = b"hello world"
        digest = "md5-" + base64.b64encode(hashlib.md5(data).digest()).decode()
        self.assertTrue(AttachmentProcessor._verify_digest(data, digest))

    def test_verify_md5_incorrect(self):
        data = b"hello world"
        wrong = "md5-" + base64.b64encode(b"0000000000000000").decode()
        self.assertFalse(AttachmentProcessor._verify_digest(data, wrong))

    def test_verify_sha1_correct(self):
        data = b"test payload"
        digest = "sha1-" + base64.b64encode(hashlib.sha1(data).digest()).decode()
        self.assertTrue(AttachmentProcessor._verify_digest(data, digest))

    def test_verify_unknown_algo(self):
        self.assertTrue(AttachmentProcessor._verify_digest(b"data", "sha256-AAAA"))

    def test_verify_no_dash(self):
        self.assertTrue(AttachmentProcessor._verify_digest(b"data", "nodashhere"))


# ===================================================================
# Edge Server Skip Tests
# ===================================================================


class TestEdgeServerSkip(unittest.TestCase):
    """Tests for edge_server skip logic in process()."""

    def _make_processor(self, skip_on_edge_server=True):
        cfg = AttachmentConfig(enabled=True, skip_on_edge_server=skip_on_edge_server)
        return AttachmentProcessor(cfg)

    def test_edge_server_skip(self):
        proc = self._make_processor(skip_on_edge_server=True)
        doc = {"_id": "doc1", "_attachments": {"a.bin": {"length": 10}}}
        http = AsyncMock()

        async def _run():
            result_doc, skip = await proc.process(
                doc, "http://localhost", http, None, {}, src="edge_server"
            )
            self.assertEqual(result_doc, doc)
            self.assertFalse(skip)
            http.request.assert_not_called()

        asyncio.run(_run())

    def test_edge_server_no_skip(self):
        proc = self._make_processor(skip_on_edge_server=False)
        # Disable the uploader so the test only validates fetch behaviour
        proc._uploader = None
        doc = {
            "_id": "doc1",
            "_attachments": {
                "a.bin": {"content_type": "application/octet-stream", "length": 5},
            },
        }
        http = AsyncMock()
        resp = AsyncMock()
        resp.read = AsyncMock(return_value=b"hello")
        resp.release = MagicMock()
        http.request = AsyncMock(return_value=resp)

        async def _run():
            result_doc, skip = await proc.process(
                doc, "http://localhost", http, None, {}, src="edge_server"
            )
            self.assertFalse(skip)
            http.request.assert_called()

        asyncio.run(_run())


# ===================================================================
# Dry Run Tests
# ===================================================================


class TestDryRun(unittest.TestCase):
    """Tests for dry_run mode in process()."""

    def test_dry_run_no_fetch(self):
        cfg = AttachmentConfig(enabled=True, dry_run=True)
        proc = AttachmentProcessor(cfg)
        doc = {
            "_id": "doc1",
            "_attachments": {
                "file.txt": {"content_type": "text/plain", "length": 42},
            },
        }
        http = AsyncMock()

        async def _run():
            result_doc, skip = await proc.process(
                doc, "http://localhost", http, None, {}, src="sync_gateway"
            )
            self.assertEqual(result_doc, doc)
            self.assertFalse(skip)
            http.request.assert_not_called()

        asyncio.run(_run())


if __name__ == "__main__":
    unittest.main()
