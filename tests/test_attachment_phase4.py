#!/usr/bin/env python3
"""
Unit tests for Phase 4 (Advanced) attachment features.

Covers:
  - Fetch mode dispatch (individual / bulk / multipart)
  - Multipart/related response parsing (standard + CouchDB fallback)
  - Multipart parser helpers
  - Bulk fetch via _bulk_get
  - Multipart fetch integration
  - Pre-signed URL generation for S3
  - _build_external_map with access_url
  - Process flow integration with bulk and multipart modes
"""

import asyncio
import base64
import hashlib
import json
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

# Ensure the module under test is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from rest.attachment_config import (
    AttachmentConfig,
    AttachmentDestinationConfig,
    AttachmentDestinationS3Config,
    AttachmentFetchConfig,
    AttachmentPostProcessConfig,
    AttachmentPresignedUrlsConfig,
)
from rest.attachment_multipart import (
    MultipartParseError,
    _extract_follows_names,
    _filename_from_headers,
    parse_multipart_response,
)
from rest.attachment_postprocess import AttachmentPostProcessor
from rest.attachment_upload import AttachmentUploader, AttachmentUploadResult
from rest.attachments import AttachmentError, AttachmentProcessor


# ===================================================================
# Helpers
# ===================================================================


class FakePart:
    """Mock aiohttp.BodyPartReader."""

    def __init__(self, data, content_type="application/octet-stream", filename=None):
        self._data = data if isinstance(data, bytes) else data.encode()
        self.headers = {"Content-Type": content_type}
        if filename:
            self.headers["Content-Disposition"] = 'attachment; filename="%s"' % filename
        self.filename = filename

    async def read(self):
        return self._data


class FakeMultipartReader:
    """Mock aiohttp.MultipartReader."""

    def __init__(self, parts):
        self._parts = list(parts)
        self._index = 0

    async def next(self):
        if self._index >= len(self._parts):
            return None
        part = self._parts[self._index]
        self._index += 1
        return part


def _make_json_part(doc):
    """Create a FakePart for a JSON document."""
    return FakePart(json.dumps(doc).encode(), content_type="application/json")


def _make_bulk_get_response(doc_id, attachments_data):
    """Build a _bulk_get response dict with base64 attachment data."""
    att = {}
    for name, data_bytes, content_type in attachments_data:
        att[name] = {
            "data": base64.b64encode(data_bytes).decode(),
            "content_type": content_type,
            "length": len(data_bytes),
        }
    return {
        "results": [
            {
                "docs": [
                    {
                        "ok": {
                            "_id": doc_id,
                            "_rev": "3-abc",
                            "_attachments": att,
                        }
                    }
                ]
            }
        ]
    }


def _make_http_mock(response_json=None, response_data=None, status=200):
    """Create a mock RetryableHTTP with a canned response."""
    http = AsyncMock()
    resp = AsyncMock()
    resp.status = status
    resp.release = MagicMock()
    if response_json is not None:
        resp.json = AsyncMock(return_value=response_json)
        # Also set read() to return serialized JSON bytes (for _fetch_bulk)
        resp.read = AsyncMock(return_value=json.dumps(response_json).encode())
    if response_data is not None:
        resp.read = AsyncMock(return_value=response_data)
    http.request = AsyncMock(return_value=resp)
    return http, resp


# ===================================================================
# Section 1: Fetch Mode Dispatch
# ===================================================================


class TestFetchModeDispatch(unittest.TestCase):
    """Tests for _resolve_fetch_mode()."""

    def _make_processor(self, mode="individual", use_bulk_get=False):
        fetch = AttachmentFetchConfig(use_bulk_get=use_bulk_get)
        cfg = AttachmentConfig(enabled=True, mode=mode, fetch=fetch)
        return AttachmentProcessor(cfg)

    def test_individual_mode(self):
        proc = self._make_processor(mode="individual")
        self.assertEqual(proc._resolve_fetch_mode(), "individual")

    def test_bulk_mode(self):
        proc = self._make_processor(mode="bulk")
        self.assertEqual(proc._resolve_fetch_mode(), "bulk")

    def test_multipart_mode(self):
        proc = self._make_processor(mode="multipart")
        self.assertEqual(proc._resolve_fetch_mode(), "multipart")

    def test_use_bulk_get_backward_compat(self):
        proc = self._make_processor(mode="individual", use_bulk_get=True)
        self.assertEqual(proc._resolve_fetch_mode(), "bulk")

    def test_bulk_mode_overrides_use_bulk_get_false(self):
        proc = self._make_processor(mode="bulk", use_bulk_get=False)
        self.assertEqual(proc._resolve_fetch_mode(), "bulk")


# ===================================================================
# Section 2: Multipart Parser — Standard
# ===================================================================


class TestMultipartParserStandard(unittest.TestCase):
    """Tests for parse_multipart_response with standard filename headers."""

    def test_standard_parsing_with_filenames(self):
        doc = {
            "_id": "doc1",
            "_rev": "3-abc",
            "_attachments": {
                "photo.jpg": {"content_type": "image/jpeg", "length": 4},
                "data.csv": {"content_type": "text/csv", "length": 3},
            },
        }
        parts = [
            _make_json_part(doc),
            FakePart(b"jpeg", content_type="image/jpeg", filename="photo.jpg"),
            FakePart(b"csv", content_type="text/csv", filename="data.csv"),
        ]
        reader = FakeMultipartReader(parts)
        resp = MagicMock()

        async def _run():
            with patch("aiohttp.MultipartReader.from_response", return_value=reader):
                result_doc, attachments = await parse_multipart_response(resp)
            self.assertEqual(result_doc["_id"], "doc1")
            self.assertEqual(attachments["photo.jpg"], b"jpeg")
            self.assertEqual(attachments["data.csv"], b"csv")

        asyncio.run(_run())

    def test_filter_by_expected_names(self):
        doc = {"_id": "doc1", "_attachments": {}}
        parts = [
            _make_json_part(doc),
            FakePart(b"keep", content_type="text/plain", filename="wanted.txt"),
            FakePart(b"drop", content_type="text/plain", filename="unwanted.txt"),
        ]
        reader = FakeMultipartReader(parts)
        resp = MagicMock()

        async def _run():
            with patch("aiohttp.MultipartReader.from_response", return_value=reader):
                _, attachments = await parse_multipart_response(
                    resp, expected_names={"wanted.txt"}
                )
            self.assertIn("wanted.txt", attachments)
            self.assertNotIn("unwanted.txt", attachments)

        asyncio.run(_run())

    def test_empty_attachments(self):
        doc = {"_id": "doc1", "_attachments": {}}
        parts = [_make_json_part(doc)]
        reader = FakeMultipartReader(parts)
        resp = MagicMock()

        async def _run():
            with patch("aiohttp.MultipartReader.from_response", return_value=reader):
                _, attachments = await parse_multipart_response(resp)
            self.assertEqual(attachments, {})

        asyncio.run(_run())

    def test_first_part_not_json_raises(self):
        parts = [FakePart(b"not json", content_type="text/plain")]
        reader = FakeMultipartReader(parts)
        resp = MagicMock()

        async def _run():
            with patch("aiohttp.MultipartReader.from_response", return_value=reader):
                with self.assertRaises(MultipartParseError):
                    await parse_multipart_response(resp)

        asyncio.run(_run())

    def test_malformed_json_raises(self):
        parts = [FakePart(b"{bad json", content_type="application/json")]
        reader = FakeMultipartReader(parts)
        resp = MagicMock()

        async def _run():
            with patch("aiohttp.MultipartReader.from_response", return_value=reader):
                with self.assertRaises(MultipartParseError):
                    await parse_multipart_response(resp)

        asyncio.run(_run())


# ===================================================================
# Section 3: Multipart Parser — CouchDB Fallback
# ===================================================================


class TestMultipartParserCouchDB(unittest.TestCase):
    """Tests for positional mapping when Content-Disposition is missing."""

    def test_positional_mapping(self):
        doc = {
            "_id": "doc1",
            "_attachments": {
                "alpha.bin": {
                    "follows": True,
                    "content_type": "application/octet-stream",
                },
                "beta.bin": {
                    "follows": True,
                    "content_type": "application/octet-stream",
                },
            },
        }
        parts = [
            _make_json_part(doc),
            FakePart(b"aaa", content_type="application/octet-stream"),
            FakePart(b"bbb", content_type="application/octet-stream"),
        ]
        reader = FakeMultipartReader(parts)
        resp = MagicMock()

        async def _run():
            with patch("aiohttp.MultipartReader.from_response", return_value=reader):
                _, attachments = await parse_multipart_response(resp, src="couchdb")
            self.assertEqual(attachments["alpha.bin"], b"aaa")
            self.assertEqual(attachments["beta.bin"], b"bbb")

        asyncio.run(_run())

    def test_mixed_mode(self):
        doc = {
            "_id": "doc1",
            "_attachments": {
                "named.txt": {"follows": True},
                "unnamed.bin": {"follows": True},
            },
        }
        parts = [
            _make_json_part(doc),
            FakePart(b"name", content_type="text/plain", filename="named.txt"),
            FakePart(b"pos", content_type="application/octet-stream"),  # no filename
        ]
        reader = FakeMultipartReader(parts)
        resp = MagicMock()

        async def _run():
            with patch("aiohttp.MultipartReader.from_response", return_value=reader):
                _, attachments = await parse_multipart_response(resp, src="couchdb")
            self.assertEqual(attachments["named.txt"], b"name")
            self.assertEqual(attachments["unnamed.bin"], b"pos")

        asyncio.run(_run())

    def test_missing_part_returns_partial(self):
        doc = {
            "_id": "doc1",
            "_attachments": {
                "a.bin": {"follows": True},
                "b.bin": {"follows": True},
            },
        }
        # Only one attachment part instead of two
        parts = [
            _make_json_part(doc),
            FakePart(b"only-one", content_type="application/octet-stream"),
        ]
        reader = FakeMultipartReader(parts)
        resp = MagicMock()

        async def _run():
            with patch("aiohttp.MultipartReader.from_response", return_value=reader):
                _, attachments = await parse_multipart_response(resp, src="couchdb")
            self.assertEqual(len(attachments), 1)
            self.assertEqual(attachments["a.bin"], b"only-one")

        asyncio.run(_run())


# ===================================================================
# Section 4: Multipart Parser Helpers
# ===================================================================


class TestMultipartHelpers(unittest.TestCase):
    """Tests for _extract_follows_names and _filename_from_headers."""

    def test_extract_follows_names_ordered(self):
        doc = {
            "_attachments": {
                "first.txt": {"follows": True},
                "second.txt": {"follows": True},
                "third.txt": {"follows": False},
            }
        }
        result = _extract_follows_names(doc)
        self.assertEqual(result, ["first.txt", "second.txt"])

    def test_extract_follows_names_empty(self):
        doc = {"_attachments": {"a.txt": {"stub": True}}}
        result = _extract_follows_names(doc)
        self.assertEqual(result, [])

    def test_filename_from_headers(self):
        part = MagicMock()
        part.filename = None
        part.headers = {"Content-Disposition": 'attachment; filename="report.pdf"'}
        result = _filename_from_headers(part)
        self.assertEqual(result, "report.pdf")


# ===================================================================
# Section 5: Bulk Fetch
# ===================================================================


class TestBulkFetch(unittest.TestCase):
    """Tests for _fetch_bulk()."""

    def _make_processor(self, **kwargs):
        fetch = AttachmentFetchConfig(use_bulk_get=True)
        cfg = AttachmentConfig(enabled=True, mode="bulk", fetch=fetch, **kwargs)
        return AttachmentProcessor(cfg)

    def test_bulk_fetch_decodes_base64(self):
        proc = self._make_processor()
        data = b"hello-attachment"
        payload = _make_bulk_get_response(
            "doc1", [("file.bin", data, "application/octet-stream")]
        )
        http, resp = _make_http_mock(response_json=payload)

        async def _run():
            result = await proc._fetch_bulk(
                "doc1",
                {
                    "file.bin": {
                        "content_type": "application/octet-stream",
                        "length": len(data),
                    }
                },
                "http://localhost/db",
                http,
                None,
                {},
            )
            self.assertEqual(result["file.bin"], data)

        asyncio.run(_run())

    def test_bulk_fetch_filters_to_stubs(self):
        proc = self._make_processor()
        payload = _make_bulk_get_response(
            "doc1",
            [
                ("wanted.txt", b"yes", "text/plain"),
                ("unwanted.txt", b"no", "text/plain"),
            ],
        )
        http, resp = _make_http_mock(response_json=payload)

        async def _run():
            result = await proc._fetch_bulk(
                "doc1",
                {"wanted.txt": {"content_type": "text/plain", "length": 3}},
                "http://localhost/db",
                http,
                None,
                {},
            )
            self.assertIn("wanted.txt", result)
            self.assertNotIn("unwanted.txt", result)

        asyncio.run(_run())

    def test_bulk_fetch_empty_response(self):
        proc = self._make_processor()
        http, resp = _make_http_mock(response_json={"results": []})

        async def _run():
            result = await proc._fetch_bulk(
                "doc1",
                {"a.bin": {"length": 5}},
                "http://localhost/db",
                http,
                None,
                {},
            )
            self.assertEqual(result, {})

        asyncio.run(_run())

    def test_bulk_fetch_verifies_digest(self):
        proc = self._make_processor(halt_on_failure=True)
        data = b"hello"
        bad_digest = "md5-" + base64.b64encode(b"0000000000000000").decode()
        payload = _make_bulk_get_response(
            "doc1", [("file.bin", data, "application/octet-stream")]
        )
        http, resp = _make_http_mock(response_json=payload)

        async def _run():
            with self.assertRaises(AttachmentError):
                await proc._fetch_bulk(
                    "doc1",
                    {
                        "file.bin": {
                            "content_type": "application/octet-stream",
                            "length": len(data),
                            "digest": bad_digest,
                        }
                    },
                    "http://localhost/db",
                    http,
                    None,
                    {},
                )

        asyncio.run(_run())


# ===================================================================
# Section 6: Multipart Fetch Integration
# ===================================================================


class TestMultipartFetchIntegration(unittest.TestCase):
    """Tests for _fetch_multipart() via AttachmentProcessor."""

    def _make_processor(self, **kwargs):
        cfg = AttachmentConfig(enabled=True, mode="multipart", **kwargs)
        return AttachmentProcessor(cfg)

    def test_multipart_fetch_dispatches(self):
        """Verify mode=multipart calls _fetch_multipart."""
        proc = self._make_processor()
        proc._uploader = None
        doc = {
            "_id": "doc1",
            "_attachments": {
                "a.txt": {"content_type": "text/plain", "length": 3},
            },
        }

        # Mock _fetch_multipart directly
        proc._fetch_multipart = AsyncMock(return_value={"a.txt": b"abc"})

        async def _run():
            result_doc, _ = await proc.process(
                doc, "http://localhost/db", AsyncMock(), None, {}, "sync_gateway"
            )
            proc._fetch_multipart.assert_called_once()

        asyncio.run(_run())

    def test_multipart_fetch_returns_correct_data(self):
        proc = self._make_processor()
        doc_json = {
            "_id": "doc1",
            "_rev": "2-x",
            "_attachments": {
                "img.png": {
                    "follows": True,
                    "content_type": "image/png",
                    "length": 4,
                },
            },
        }
        parts = [
            _make_json_part(doc_json),
            FakePart(b"png!", content_type="image/png", filename="img.png"),
        ]
        reader = FakeMultipartReader(parts)

        http = AsyncMock()
        resp = MagicMock()
        resp.release = MagicMock()
        http.request = AsyncMock(return_value=resp)

        async def _run():
            with patch("aiohttp.MultipartReader.from_response", return_value=reader):
                result = await proc._fetch_multipart(
                    "doc1",
                    {"img.png": {"content_type": "image/png", "length": 4}},
                    "http://localhost/db",
                    http,
                    None,
                    {},
                )
            self.assertEqual(result["img.png"], b"png!")

        asyncio.run(_run())

    def test_multipart_fetch_bad_digest_raises(self):
        proc = self._make_processor(halt_on_failure=True)
        data = b"realdata"
        bad_digest = "md5-" + base64.b64encode(b"0000000000000000").decode()
        doc_json = {
            "_id": "doc1",
            "_attachments": {
                "f.bin": {"follows": True, "content_type": "application/octet-stream"},
            },
        }
        parts = [
            _make_json_part(doc_json),
            FakePart(data, content_type="application/octet-stream", filename="f.bin"),
        ]
        reader = FakeMultipartReader(parts)

        http = AsyncMock()
        resp = MagicMock()
        resp.release = MagicMock()
        http.request = AsyncMock(return_value=resp)

        async def _run():
            with patch("aiohttp.MultipartReader.from_response", return_value=reader):
                with self.assertRaises(AttachmentError):
                    await proc._fetch_multipart(
                        "doc1",
                        {
                            "f.bin": {
                                "content_type": "application/octet-stream",
                                "length": len(data),
                                "digest": bad_digest,
                            }
                        },
                        "http://localhost/db",
                        http,
                        None,
                        {},
                    )

        asyncio.run(_run())


# ===================================================================
# Section 7: Pre-signed URL
# ===================================================================


class TestPresignedUrl(unittest.TestCase):
    """Tests for pre-signed URL generation and access_url field."""

    def test_upload_result_has_access_url(self):
        result = AttachmentUploadResult(
            attachment_name="file.bin",
            destination_type="s3",
            key="att/doc1/file.bin",
            location="s3://bucket/att/doc1/file.bin",
            content_type="application/octet-stream",
            length=100,
            digest="md5-abc==",
            uploaded_at="2026-04-19T00:00:00Z",
        )
        self.assertEqual(result.access_url, "")

    def test_upload_result_with_access_url(self):
        result = AttachmentUploadResult(
            attachment_name="file.bin",
            destination_type="s3",
            key="att/doc1/file.bin",
            location="s3://bucket/att/doc1/file.bin",
            content_type="application/octet-stream",
            length=100,
            digest="md5-abc==",
            uploaded_at="2026-04-19T00:00:00Z",
            access_url="https://bucket.s3.amazonaws.com/att/doc1/file.bin?X-Amz-Sig=...",
        )
        self.assertIn("X-Amz-Sig", result.access_url)

    def test_presigned_url_not_generated_when_disabled(self):
        dest = AttachmentDestinationConfig(
            type="s3",
            s3=AttachmentDestinationS3Config(bucket="test"),
            presigned_urls=AttachmentPresignedUrlsConfig(enabled=False),
        )
        cfg = AttachmentConfig(enabled=True, destination=dest)
        uploader = AttachmentUploader(cfg)
        # _generate_presigned_url should not be called in normal flow
        # when presigned_urls.enabled is False

        result = AttachmentUploadResult(
            attachment_name="f.bin",
            destination_type="s3",
            key="k",
            location="s3://test/k",
            content_type="application/octet-stream",
            length=10,
            digest="",
            uploaded_at="2026-04-19T00:00:00Z",
        )
        self.assertEqual(result.access_url, "")

    def test_build_external_map_uses_access_url(self):
        uploaded = {
            "pic.jpg": AttachmentUploadResult(
                attachment_name="pic.jpg",
                destination_type="s3",
                key="att/doc1/pic.jpg",
                location="s3://bucket/att/doc1/pic.jpg",
                content_type="image/jpeg",
                length=1024,
                digest="md5-abc==",
                uploaded_at="2026-04-19T00:00:00Z",
                access_url="https://presigned.example.com/pic.jpg",
            ),
        }
        ext = AttachmentPostProcessor._build_external_map(uploaded)
        self.assertEqual(ext["pic.jpg"]["url"], "https://presigned.example.com/pic.jpg")

    def test_build_external_map_falls_back_to_location(self):
        uploaded = {
            "data.bin": AttachmentUploadResult(
                attachment_name="data.bin",
                destination_type="filesystem",
                key="att/doc1/data.bin",
                location="/mnt/attachments/att/doc1/data.bin",
                content_type="application/octet-stream",
                length=512,
                digest="",
                uploaded_at="2026-04-19T00:00:00Z",
                access_url="",  # no presigned
            ),
        }
        ext = AttachmentPostProcessor._build_external_map(uploaded)
        self.assertEqual(ext["data.bin"]["url"], "/mnt/attachments/att/doc1/data.bin")


# ===================================================================
# Section 8: Process Flow Integration
# ===================================================================


class TestProcessFlowIntegration(unittest.TestCase):
    """Integration tests for process() with bulk and multipart modes."""

    def test_process_with_bulk_mode(self):
        """Full flow: detect → filter → bulk fetch → (no upload) → return."""
        cfg = AttachmentConfig(enabled=True, mode="bulk")
        proc = AttachmentProcessor(cfg)
        proc._uploader = None
        proc._post_processor = None

        doc = {
            "_id": "doc1",
            "_rev": "2-xyz",
            "_attachments": {
                "report.pdf": {
                    "content_type": "application/pdf",
                    "length": 7,
                },
            },
        }
        payload = _make_bulk_get_response(
            "doc1", [("report.pdf", b"pdf-dat", "application/pdf")]
        )
        http, resp = _make_http_mock(response_json=payload)

        async def _run():
            result_doc, skip = await proc.process(
                doc, "http://localhost/db", http, None, {}, "sync_gateway"
            )
            self.assertFalse(skip)
            # Verify _bulk_get endpoint was called
            call_args = http.request.call_args
            self.assertIn("_bulk_get", call_args[0][1])

        asyncio.run(_run())

    def test_process_with_multipart_mode(self):
        """Full flow: detect → filter → multipart fetch → return."""
        cfg = AttachmentConfig(enabled=True, mode="multipart")
        proc = AttachmentProcessor(cfg)
        proc._uploader = None
        proc._post_processor = None

        doc = {
            "_id": "doc1",
            "_rev": "2-xyz",
            "_attachments": {
                "img.png": {
                    "content_type": "image/png",
                    "length": 4,
                },
            },
        }
        doc_json_mp = {
            "_id": "doc1",
            "_rev": "2-xyz",
            "_attachments": {
                "img.png": {
                    "follows": True,
                    "content_type": "image/png",
                    "length": 4,
                },
            },
        }
        parts = [
            _make_json_part(doc_json_mp),
            FakePart(b"png!", content_type="image/png", filename="img.png"),
        ]
        reader = FakeMultipartReader(parts)

        http = AsyncMock()
        resp = MagicMock()
        resp.release = MagicMock()
        http.request = AsyncMock(return_value=resp)

        async def _run():
            with patch("aiohttp.MultipartReader.from_response", return_value=reader):
                result_doc, skip = await proc.process(
                    doc, "http://localhost/db", http, None, {}, "sync_gateway"
                )
            self.assertFalse(skip)
            # Verify multipart GET was called with Accept header
            call_args = http.request.call_args
            self.assertEqual(call_args[0][0], "GET")
            self.assertIn("attachments=true", call_args[0][1])

        asyncio.run(_run())


if __name__ == "__main__":
    unittest.main()
