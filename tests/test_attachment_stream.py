#!/usr/bin/env python3
"""
Unit tests for Phase 4 — Item 16: Streaming large attachments.

Covers:
  - AttachmentStreamer: filesystem, HTTP, S3 streaming destinations
  - AttachmentProcessor with mode="stream" integration
  - Streaming error handling (missing attachment, upload failure)
  - Chunk-based digest computation during stream
  - S3 multipart upload abort on failure
"""

import asyncio
import base64
import hashlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from rest.attachment_config import (
    AttachmentConfig,
    AttachmentDestinationConfig,
    AttachmentDestinationFilesystemConfig,
    AttachmentDestinationHTTPConfig,
    AttachmentDestinationS3Config,
    AttachmentFetchConfig,
    AttachmentPostProcessConfig,
    AttachmentPresignedUrlsConfig,
)
from rest.attachment_stream import (
    AttachmentStreamer,
    DEFAULT_CHUNK_SIZE,
    S3_MIN_PART_SIZE,
)
from rest.attachment_upload import AttachmentUploadResult
from rest.attachments import AttachmentError, AttachmentProcessor


# ===================================================================
# Helpers
# ===================================================================


class FakeStreamContent:
    """Mock aiohttp response content with iter_chunked support."""

    def __init__(self, data: bytes, chunk_size: int = DEFAULT_CHUNK_SIZE):
        self._data = data
        self._chunk_size = chunk_size

    async def iter_chunked(self, size: int):
        offset = 0
        while offset < len(self._data):
            yield self._data[offset : offset + size]
            offset += size


class FakeStreamResponse:
    """Mock aiohttp.ClientResponse with streaming content."""

    def __init__(self, data: bytes, status: int = 200):
        self.status = status
        self.content = FakeStreamContent(data)
        self._released = False

    def release(self):
        self._released = True


def _make_config(**overrides) -> AttachmentConfig:
    """Build a streaming-mode AttachmentConfig with sensible defaults."""
    defaults = {
        "enabled": True,
        "mode": "stream",
        "halt_on_failure": True,
    }
    defaults.update(overrides)
    return AttachmentConfig(**defaults)


def _md5_digest(data: bytes) -> str:
    return "md5-%s" % base64.b64encode(hashlib.md5(data).digest()).decode("ascii")


# ===================================================================
# Test: AttachmentStreamer — filesystem destination
# ===================================================================


class TestStreamToFilesystem(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.config = _make_config(
            destination=AttachmentDestinationConfig(
                type="filesystem",
                key_template="{prefix}/{doc_id}/{attachment_name}",
                key_prefix="att",
                filesystem=AttachmentDestinationFilesystemConfig(base_path=self.tmpdir),
            )
        )
        self.streamer = AttachmentStreamer(self.config)

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_stream_to_filesystem_basic(self):
        data = b"hello streaming world" * 100
        doc = {"_id": "doc1", "_rev": "1-abc"}
        stub = {"content_type": "text/plain", "length": len(data), "digest": ""}
        resp = FakeStreamResponse(data)

        async def _run():
            return await self.streamer.stream_attachment(doc, "readme.txt", stub, resp)

        result = asyncio.run(_run())

        self.assertEqual(result.attachment_name, "readme.txt")
        self.assertEqual(result.destination_type, "filesystem")
        self.assertEqual(result.length, len(data))
        self.assertTrue(result.digest.startswith("md5-"))
        self.assertIn("doc1", result.location)

        # Verify file was actually written
        written = Path(result.location).read_bytes()
        self.assertEqual(written, data)

    def test_stream_digest_matches(self):
        data = b"digest-test-payload-12345"
        doc = {"_id": "doc2", "_rev": "1-x"}
        stub = {"content_type": "application/octet-stream", "length": len(data)}
        resp = FakeStreamResponse(data)

        async def _run():
            return await self.streamer.stream_attachment(doc, "file.bin", stub, resp)

        result = asyncio.run(_run())

        expected_digest = _md5_digest(data)
        self.assertEqual(result.digest, expected_digest)

    def test_stream_empty_file(self):
        data = b""
        doc = {"_id": "doc3", "_rev": "1-y"}
        stub = {"content_type": "text/plain", "length": 0}
        resp = FakeStreamResponse(data)

        async def _run():
            return await self.streamer.stream_attachment(doc, "empty.txt", stub, resp)

        result = asyncio.run(_run())

        self.assertEqual(result.length, 0)
        written = Path(result.location).read_bytes()
        self.assertEqual(written, b"")

    def test_stream_large_chunked(self):
        """Verify multi-chunk streaming works correctly."""
        data = os.urandom(DEFAULT_CHUNK_SIZE * 3 + 42)
        doc = {"_id": "doc4", "_rev": "1-z"}
        stub = {"content_type": "application/octet-stream", "length": len(data)}
        resp = FakeStreamResponse(data)

        async def _run():
            return await self.streamer.stream_attachment(doc, "big.bin", stub, resp)

        result = asyncio.run(_run())

        self.assertEqual(result.length, len(data))
        written = Path(result.location).read_bytes()
        self.assertEqual(written, data)

    def test_stream_filesystem_path_traversal_rejected(self):
        data = b"evil"
        doc = {"_id": "../../etc", "_rev": "1-x"}
        stub = {"content_type": "text/plain", "length": 4}
        resp = FakeStreamResponse(data)

        async def _run():
            await self.streamer.stream_attachment(doc, "passwd", stub, resp)

        with self.assertRaises(ValueError):
            asyncio.run(_run())


# ===================================================================
# Test: AttachmentStreamer — HTTP destination
# ===================================================================


class TestStreamToHTTP(unittest.TestCase):
    def test_stream_to_http_success(self):
        config = _make_config(
            destination=AttachmentDestinationConfig(
                type="http",
                key_template="{prefix}/{doc_id}/{attachment_name}",
                key_prefix="att",
                http=AttachmentDestinationHTTPConfig(
                    url_template="https://cdn.example.com/upload/{doc_id}/{attachment_name}",
                    method="PUT",
                ),
            )
        )
        streamer = AttachmentStreamer(config)

        data = b"http stream payload"
        doc = {"_id": "httpdoc", "_rev": "1-h"}
        stub = {"content_type": "image/png", "length": len(data)}
        source_resp = FakeStreamResponse(data)

        # Build a mock session whose .request() actually drains the data generator
        mock_resp_obj = AsyncMock()
        mock_resp_obj.status = 200
        mock_resp_obj.headers = {"ETag": '"abc123"'}
        mock_resp_obj.text = AsyncMock(return_value="ok")

        class _FakeCtx:
            def __init__(self, resp, data_gen):
                self._resp = resp
                self._data_gen = data_gen

            async def __aenter__(self):
                # Drain the async generator (simulates aiohttp consuming the body)
                if self._data_gen is not None:
                    async for _ in self._data_gen:
                        pass
                return self._resp

            async def __aexit__(self, *args):
                return False

        class _FakeSession:
            def __init__(self, resp):
                self._resp = resp

            def request(self, method, url, *, data=None, headers=None):
                return _FakeCtx(self._resp, data)

        fake_session = _FakeSession(mock_resp_obj)
        streamer._http_session = fake_session

        async def _run():
            return await streamer.stream_attachment(doc, "photo.png", stub, source_resp)

        result = asyncio.run(_run())

        self.assertEqual(result.attachment_name, "photo.png")
        self.assertEqual(result.destination_type, "http")
        self.assertEqual(result.length, len(data))
        self.assertEqual(result.etag, '"abc123"')
        self.assertIn("httpdoc", result.location)

    def test_stream_to_http_error(self):
        config = _make_config(
            destination=AttachmentDestinationConfig(
                type="http",
                http=AttachmentDestinationHTTPConfig(
                    url_template="https://cdn.example.com/{doc_id}/{attachment_name}",
                ),
            )
        )
        streamer = AttachmentStreamer(config)

        data = b"fail payload"
        doc = {"_id": "doc_err", "_rev": "1-e"}
        stub = {"content_type": "text/plain", "length": len(data)}
        source_resp = FakeStreamResponse(data)

        mock_resp = AsyncMock()
        mock_resp.status = 500
        mock_resp.headers = {}
        mock_resp.text = AsyncMock(return_value="Internal Server Error")

        mock_session = AsyncMock()
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_session.request = MagicMock(return_value=mock_ctx)
        streamer._http_session = mock_session

        async def _run():
            await streamer.stream_attachment(doc, "bad.txt", stub, source_resp)

        with self.assertRaises(RuntimeError):
            asyncio.run(_run())


# ===================================================================
# Test: AttachmentStreamer — S3 multipart upload
# ===================================================================


class TestStreamToS3(unittest.TestCase):
    def test_stream_to_s3_small_file(self):
        """Small file (< 5 MiB) should still complete as a single part."""
        config = _make_config(
            destination=AttachmentDestinationConfig(
                type="s3",
                key_template="{prefix}/{doc_id}/{attachment_name}",
                key_prefix="att",
                s3=AttachmentDestinationS3Config(
                    bucket="test-bucket", region="us-east-1"
                ),
            )
        )
        streamer = AttachmentStreamer(config)

        data = b"small s3 payload"
        doc = {"_id": "s3doc", "_rev": "1-s"}
        stub = {"content_type": "image/jpeg", "length": len(data)}
        source_resp = FakeStreamResponse(data)

        mock_client = MagicMock()
        mock_client.create_multipart_upload.return_value = {"UploadId": "upload-123"}
        mock_client.upload_part.return_value = {"ETag": '"part1"'}
        mock_client.complete_multipart_upload.return_value = {"ETag": '"final"'}

        streamer._s3_client = mock_client
        streamer._s3_executor = None  # run_in_executor with None uses default

        async def _run():
            return await streamer.stream_attachment(doc, "photo.jpg", stub, source_resp)

        result = asyncio.run(_run())

        self.assertEqual(result.attachment_name, "photo.jpg")
        self.assertEqual(result.destination_type, "s3")
        self.assertEqual(result.length, len(data))
        self.assertEqual(result.etag, '"final"')
        self.assertIn("test-bucket", result.location)

        mock_client.create_multipart_upload.assert_called_once()
        mock_client.upload_part.assert_called_once()
        mock_client.complete_multipart_upload.assert_called_once()
        mock_client.abort_multipart_upload.assert_not_called()

    def test_stream_to_s3_abort_on_failure(self):
        """On upload_part failure, multipart upload should be aborted."""
        config = _make_config(
            destination=AttachmentDestinationConfig(
                type="s3",
                s3=AttachmentDestinationS3Config(bucket="test-bucket"),
            )
        )
        streamer = AttachmentStreamer(config)

        data = b"fail data"
        doc = {"_id": "s3fail", "_rev": "1-f"}
        stub = {"content_type": "text/plain", "length": len(data)}
        source_resp = FakeStreamResponse(data)

        mock_client = MagicMock()
        mock_client.create_multipart_upload.return_value = {"UploadId": "upload-456"}
        mock_client.upload_part.side_effect = Exception("S3 error")
        mock_client.abort_multipart_upload.return_value = {}

        streamer._s3_client = mock_client
        streamer._s3_executor = None

        async def _run():
            await streamer.stream_attachment(doc, "fail.txt", stub, source_resp)

        with self.assertRaises(Exception):
            asyncio.run(_run())

        mock_client.abort_multipart_upload.assert_called_once()


# ===================================================================
# Test: AttachmentProcessor integration with mode="stream"
# ===================================================================


class TestProcessorStreamMode(unittest.TestCase):
    def _make_processor(self, dest_type="filesystem", tmpdir=None, **kwargs):
        dest_kwargs = {}
        if dest_type == "filesystem":
            dest_kwargs["filesystem"] = AttachmentDestinationFilesystemConfig(
                base_path=tmpdir or "/tmp/test-stream"
            )
        config = _make_config(
            destination=AttachmentDestinationConfig(type=dest_type, **dest_kwargs),
            **kwargs,
        )
        return AttachmentProcessor(config)

    def test_processor_creates_streamer_in_stream_mode(self):
        proc = self._make_processor()
        self.assertIsNotNone(proc._streamer)
        self.assertIsNone(proc._uploader)

    def test_processor_creates_uploader_in_individual_mode(self):
        config = AttachmentConfig(
            enabled=True,
            mode="individual",
            destination=AttachmentDestinationConfig(
                type="filesystem",
                filesystem=AttachmentDestinationFilesystemConfig(base_path="/tmp/test"),
            ),
        )
        proc = AttachmentProcessor(config)
        self.assertIsNone(proc._streamer)
        self.assertIsNotNone(proc._uploader)

    def test_stream_mode_full_flow(self):
        """Full integration: doc with attachment → stream to filesystem."""
        tmpdir = tempfile.mkdtemp()
        try:
            proc = self._make_processor(tmpdir=tmpdir)

            doc = {
                "_id": "stream_doc",
                "_rev": "1-abc",
                "_attachments": {
                    "image.jpg": {
                        "content_type": "image/jpeg",
                        "length": 10,
                        "stub": True,
                        "digest": "",
                    }
                },
            }

            data = b"0123456789"
            fake_resp = FakeStreamResponse(data)

            mock_http = AsyncMock()
            mock_http.request = AsyncMock(return_value=fake_resp)

            async def _run():
                return await proc.process(
                    doc=doc,
                    base_url="http://sg:4984/db",
                    http=mock_http,
                    auth=None,
                    headers={},
                    src="sync_gateway",
                )

            result_doc, skip = asyncio.run(_run())

            self.assertFalse(skip)
            # Verify file was written
            expected_path = Path(tmpdir) / "attachments" / "stream_doc" / "image.jpg"
            self.assertTrue(expected_path.exists())
            self.assertEqual(expected_path.read_bytes(), data)
        finally:
            import shutil

            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_stream_mode_missing_attachment_skip(self):
        """Missing attachment with on_missing_attachment=skip should not raise."""
        tmpdir = tempfile.mkdtemp()
        try:
            proc = self._make_processor(
                tmpdir=tmpdir,
                on_missing_attachment="skip",
                halt_on_failure=False,
            )

            doc = {
                "_id": "missing_doc",
                "_rev": "1-x",
                "_attachments": {
                    "gone.pdf": {
                        "content_type": "application/pdf",
                        "length": 100,
                        "stub": True,
                    }
                },
            }

            from rest.changes_http import ClientHTTPError

            mock_http = AsyncMock()
            mock_http.request = AsyncMock(side_effect=ClientHTTPError(404, "Not Found"))

            async def _run():
                return await proc.process(
                    doc=doc,
                    base_url="http://sg:4984/db",
                    http=mock_http,
                    auth=None,
                    headers={},
                    src="sync_gateway",
                )

            result_doc, skip = asyncio.run(_run())

            self.assertFalse(skip)
        finally:
            import shutil

            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_stream_mode_halt_on_failure(self):
        """With halt_on_failure=True, streaming errors should raise."""
        tmpdir = tempfile.mkdtemp()
        try:
            proc = self._make_processor(tmpdir=tmpdir, halt_on_failure=True)

            doc = {
                "_id": "fail_doc",
                "_rev": "1-f",
                "_attachments": {
                    "data.bin": {
                        "content_type": "application/octet-stream",
                        "length": 50,
                        "stub": True,
                    }
                },
            }

            mock_http = AsyncMock()
            mock_http.request = AsyncMock(
                side_effect=ConnectionError("Connection refused")
            )

            async def _run():
                await proc.process(
                    doc=doc,
                    base_url="http://sg:4984/db",
                    http=mock_http,
                    auth=None,
                    headers={},
                    src="sync_gateway",
                )

            with self.assertRaises(AttachmentError):
                asyncio.run(_run())
        finally:
            import shutil

            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_stream_mode_disabled_falls_through(self):
        """If not enabled, streaming is skipped."""
        config = AttachmentConfig(enabled=False, mode="stream")
        proc = AttachmentProcessor(config)

        doc = {"_id": "noop", "_attachments": {"a.txt": {"stub": True}}}
        mock_http = AsyncMock()

        async def _run():
            return await proc.process(
                doc=doc,
                base_url="http://sg:4984/db",
                http=mock_http,
                auth=None,
                headers={},
                src="sync_gateway",
            )

        result_doc, skip = asyncio.run(_run())

        self.assertFalse(skip)
        mock_http.request.assert_not_called()


# ===================================================================
# Test: Streamer close
# ===================================================================


class TestStreamerClose(unittest.TestCase):
    def test_close_cleans_up_session(self):
        config = _make_config(
            destination=AttachmentDestinationConfig(
                type="http",
                http=AttachmentDestinationHTTPConfig(
                    url_template="https://example.com/{doc_id}/{attachment_name}"
                ),
            )
        )
        streamer = AttachmentStreamer(config)

        mock_session = AsyncMock()
        streamer._http_session = mock_session

        asyncio.run(streamer.close())

        mock_session.close.assert_called_once()
        self.assertIsNone(streamer._http_session)

    def test_processor_close_cleans_streamer(self):
        config = _make_config(
            destination=AttachmentDestinationConfig(
                type="filesystem",
                filesystem=AttachmentDestinationFilesystemConfig(base_path="/tmp/t"),
            )
        )
        proc = AttachmentProcessor(config)
        self.assertIsNotNone(proc._streamer)

        asyncio.run(proc.close())


# ===================================================================
# Test: Unsupported destination type
# ===================================================================


class TestStreamUnsupportedDestination(unittest.TestCase):
    def test_unsupported_dest_raises(self):
        config = _make_config(destination=AttachmentDestinationConfig(type="gcs"))
        streamer = AttachmentStreamer(config)

        data = b"test"
        doc = {"_id": "d1", "_rev": "1-x"}
        stub = {"content_type": "text/plain", "length": 4}
        resp = FakeStreamResponse(data)

        async def _run():
            await streamer.stream_attachment(doc, "f.txt", stub, resp)

        with self.assertRaises(ValueError):
            asyncio.run(_run())


if __name__ == "__main__":
    unittest.main()
