#!/usr/bin/env python3
"""
Unit tests for rest.attachment_upload (Phase 2 — Upload to Destination).

Covers:
  - Key rendering with attachment-specific placeholders
  - Filesystem upload (write, atomic rename, path traversal guard)
  - HTTP upload (success, retry on transient, fail on permanent)
  - S3 upload mocking (put_object call shape, error classification)
  - upload_many orchestration (concurrent uploads, partial failure, halt_on_failure)
  - AttachmentUploadResult dataclass
  - Transient/permanent error classification
  - Retry with backoff
"""

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# Ensure the module under test is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from rest.attachment_config import (
    AttachmentConfig,
    AttachmentDestinationConfig,
    AttachmentDestinationFilesystemConfig,
    AttachmentDestinationHTTPConfig,
    AttachmentDestinationS3Config,
    AttachmentRetryConfig,
    parse_attachment_config,
)
from rest.attachment_upload import (
    AttachmentUploader,
    AttachmentUploadResult,
    _HTTPUploadError,
)


# ===================================================================
# Key Rendering Tests
# ===================================================================


class TestKeyRendering(unittest.TestCase):
    """Tests for _render_attachment_key()."""

    def _make_uploader(self, key_template="{prefix}/{doc_id}/{attachment_name}"):
        dest = AttachmentDestinationConfig(
            type="filesystem",
            key_template=key_template,
            key_prefix="attachments",
            filesystem=AttachmentDestinationFilesystemConfig(base_path="/tmp/test"),
        )
        cfg = AttachmentConfig(enabled=True, destination=dest)
        return AttachmentUploader(
            cfg, gateway_cfg={"scope": "us", "collection": "prices", "database": "db"}
        )

    def test_basic_key_rendering(self):
        uploader = self._make_uploader()
        doc = {"_id": "doc1", "_rev": "3-abc"}
        stub = {"content_type": "image/jpeg", "digest": "md5-abc==", "revpos": 2}
        key = uploader._render_attachment_key(doc, "photo.jpg", stub, b"data")
        self.assertEqual(key, "attachments/doc1/photo.jpg")

    def test_key_with_all_placeholders(self):
        uploader = self._make_uploader(
            key_template="{prefix}/{scope}/{collection}/{doc_id}/{attachment_name}"
        )
        doc = {"_id": "mydoc"}
        stub = {"content_type": "text/plain"}
        key = uploader._render_attachment_key(doc, "readme.txt", stub, b"hello")
        self.assertEqual(key, "attachments/us/prices/mydoc/readme.txt")

    def test_key_with_content_type_placeholder(self):
        uploader = self._make_uploader(
            key_template="{prefix}/{content_type}/{attachment_name}"
        )
        doc = {"_id": "doc1"}
        stub = {"content_type": "image/png"}
        key = uploader._render_attachment_key(doc, "logo.png", stub, b"x")
        # content_type contains "/" which is preserved by render_key
        self.assertIn("image", key)
        self.assertIn("logo.png", key)

    def test_key_with_length_placeholder(self):
        uploader = self._make_uploader(
            key_template="{prefix}/{doc_id}/{length}/{attachment_name}"
        )
        doc = {"_id": "doc1"}
        stub = {}
        data = b"twelve bytes"
        key = uploader._render_attachment_key(doc, "file.bin", stub, data)
        self.assertIn("12", key)
        self.assertIn("file.bin", key)


# ===================================================================
# Filesystem Upload Tests
# ===================================================================


class TestFilesystemUpload(unittest.TestCase):
    """Tests for _upload_filesystem()."""

    def _make_uploader(self, base_path):
        dest = AttachmentDestinationConfig(
            type="filesystem",
            key_template="{prefix}/{doc_id}/{attachment_name}",
            key_prefix="att",
            filesystem=AttachmentDestinationFilesystemConfig(base_path=base_path),
        )
        cfg = AttachmentConfig(enabled=True, destination=dest)
        return AttachmentUploader(cfg)

    def test_filesystem_write_creates_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            uploader = self._make_uploader(tmpdir)

            async def _run():
                await uploader._upload_filesystem("subdir/test.txt", b"hello world")
                target = Path(tmpdir) / "subdir" / "test.txt"
                self.assertTrue(target.exists())
                self.assertEqual(target.read_bytes(), b"hello world")

            asyncio.run(_run())

    def test_filesystem_overwrites_existing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            uploader = self._make_uploader(tmpdir)

            async def _run():
                await uploader._upload_filesystem("test.bin", b"version1")
                await uploader._upload_filesystem("test.bin", b"version2")
                target = Path(tmpdir) / "test.bin"
                self.assertEqual(target.read_bytes(), b"version2")

            asyncio.run(_run())

    def test_filesystem_nested_dirs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            uploader = self._make_uploader(tmpdir)

            async def _run():
                await uploader._upload_filesystem("a/b/c/deep.txt", b"deep")
                target = Path(tmpdir) / "a" / "b" / "c" / "deep.txt"
                self.assertTrue(target.exists())

            asyncio.run(_run())

    def test_filesystem_path_traversal_blocked(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            uploader = self._make_uploader(tmpdir)

            async def _run():
                with self.assertRaises(ValueError) as ctx:
                    await uploader._upload_filesystem("../../etc/passwd", b"bad")
                self.assertIn("outside base_path", str(ctx.exception))

            asyncio.run(_run())

    def test_filesystem_empty_base_path_raises(self):
        uploader = self._make_uploader("")

        async def _run():
            with self.assertRaises(ValueError):
                await uploader._upload_filesystem("test.txt", b"data")

        asyncio.run(_run())


# ===================================================================
# Filesystem end-to-end via upload_many
# ===================================================================


class TestFilesystemUploadMany(unittest.TestCase):
    """Tests for upload_many with filesystem destination."""

    def _make_uploader(self, base_path):
        dest = AttachmentDestinationConfig(
            type="filesystem",
            key_template="{prefix}/{doc_id}/{attachment_name}",
            key_prefix="att",
            filesystem=AttachmentDestinationFilesystemConfig(base_path=base_path),
        )
        cfg = AttachmentConfig(enabled=True, destination=dest)
        return AttachmentUploader(cfg)

    def test_upload_many_filesystem(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            uploader = self._make_uploader(tmpdir)
            doc = {"_id": "doc1"}
            stubs = {
                "a.txt": {"content_type": "text/plain", "length": 5},
                "b.bin": {"content_type": "application/octet-stream", "length": 3},
            }
            fetched = {"a.txt": b"hello", "b.bin": b"xyz"}

            async def _run():
                results = await uploader.upload_many(doc, stubs, fetched)
                self.assertEqual(len(results), 2)
                self.assertIn("a.txt", results)
                self.assertIn("b.bin", results)

                # Check files exist on disk
                a_path = Path(tmpdir) / "att" / "doc1" / "a.txt"
                b_path = Path(tmpdir) / "att" / "doc1" / "b.bin"
                self.assertTrue(a_path.exists())
                self.assertTrue(b_path.exists())
                self.assertEqual(a_path.read_bytes(), b"hello")
                self.assertEqual(b_path.read_bytes(), b"xyz")

                # Check result fields
                r = results["a.txt"]
                self.assertEqual(r.attachment_name, "a.txt")
                self.assertEqual(r.destination_type, "filesystem")
                self.assertEqual(r.content_type, "text/plain")
                self.assertEqual(r.length, 5)
                self.assertIn("doc1", r.location)
                self.assertTrue(r.uploaded_at)

            asyncio.run(_run())


# ===================================================================
# AttachmentUploadResult Tests
# ===================================================================


class TestAttachmentUploadResult(unittest.TestCase):
    """Tests for AttachmentUploadResult dataclass."""

    def test_result_fields(self):
        r = AttachmentUploadResult(
            attachment_name="photo.jpg",
            destination_type="s3",
            key="attachments/doc1/photo.jpg",
            location="s3://bucket/attachments/doc1/photo.jpg",
            content_type="image/jpeg",
            length=2048,
            digest="md5-abc==",
            uploaded_at="2026-04-19T12:00:00Z",
            etag='"abc123"',
        )
        self.assertEqual(r.attachment_name, "photo.jpg")
        self.assertEqual(r.destination_type, "s3")
        self.assertEqual(r.length, 2048)
        self.assertEqual(r.etag, '"abc123"')

    def test_result_defaults(self):
        r = AttachmentUploadResult(
            attachment_name="f",
            destination_type="fs",
            key="k",
            location="l",
            content_type="ct",
            length=0,
            digest="",
            uploaded_at="",
        )
        self.assertEqual(r.etag, "")


# ===================================================================
# Error Classification Tests
# ===================================================================


class TestErrorClassification(unittest.TestCase):
    """Tests for _is_transient() error classification."""

    def _make_uploader(self):
        cfg = AttachmentConfig(enabled=True)
        return AttachmentUploader(cfg)

    def test_connection_error_is_transient(self):
        u = self._make_uploader()
        self.assertTrue(u._is_transient(ConnectionError("refused"), "http"))

    def test_timeout_is_transient(self):
        u = self._make_uploader()
        self.assertTrue(u._is_transient(TimeoutError(), "s3"))

    def test_value_error_is_not_transient(self):
        u = self._make_uploader()
        self.assertFalse(u._is_transient(ValueError("bad config"), "s3"))

    def test_http_upload_error_transient(self):
        u = self._make_uploader()
        exc = _HTTPUploadError(503, "Service Unavailable")
        self.assertTrue(u._is_transient(exc, "http"))

    def test_http_upload_error_permanent(self):
        u = self._make_uploader()
        exc = _HTTPUploadError(403, "Forbidden")
        self.assertFalse(u._is_transient(exc, "http"))

    def test_http_upload_error_429_transient(self):
        u = self._make_uploader()
        exc = _HTTPUploadError(429, "Too Many Requests")
        self.assertTrue(u._is_transient(exc, "http"))


# ===================================================================
# HTTP Upload Tests
# ===================================================================


class TestHTTPUpload(unittest.TestCase):
    """Tests for _upload_http()."""

    def _make_uploader(
        self, url_template="https://cdn.example.com/{doc_id}/{attachment_name}"
    ):
        dest = AttachmentDestinationConfig(
            type="http",
            key_template="{prefix}/{doc_id}/{attachment_name}",
            key_prefix="att",
            http=AttachmentDestinationHTTPConfig(
                url_template=url_template,
                method="PUT",
                headers={"X-Custom": "test"},
            ),
        )
        cfg = AttachmentConfig(enabled=True, destination=dest)
        return AttachmentUploader(cfg)

    def test_http_upload_success(self):
        uploader = self._make_uploader()
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.headers = {"ETag": '"etag123"'}
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.request = MagicMock(return_value=mock_resp)
        uploader._http_session = mock_session

        doc = {"_id": "doc1"}

        async def _run():
            etag = await uploader._upload_http(
                "att/doc1/pic.jpg", b"data", "image/jpeg", doc, "pic.jpg", {}
            )
            self.assertEqual(etag, '"etag123"')
            mock_session.request.assert_called_once()
            call_args = mock_session.request.call_args
            self.assertEqual(call_args[0][0], "PUT")
            self.assertIn("doc1", call_args[0][1])

        asyncio.run(_run())

    def test_http_upload_error_status(self):
        uploader = self._make_uploader()
        mock_resp = AsyncMock()
        mock_resp.status = 500
        mock_resp.headers = {}
        mock_resp.text = AsyncMock(return_value="Internal Server Error")
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.request = MagicMock(return_value=mock_resp)
        uploader._http_session = mock_session

        doc = {"_id": "doc1"}

        async def _run():
            with self.assertRaises(_HTTPUploadError) as ctx:
                await uploader._upload_http(
                    "key", b"data", "text/plain", doc, "file.txt", {}
                )
            self.assertEqual(ctx.exception.status, 500)

        asyncio.run(_run())

    def test_http_missing_url_template(self):
        uploader = self._make_uploader(url_template="")

        async def _run():
            with self.assertRaises(ValueError):
                await uploader._upload_http(
                    "key", b"data", "text/plain", {"_id": "d"}, "f.txt", {}
                )

        asyncio.run(_run())


# ===================================================================
# S3 Upload Tests (mocked)
# ===================================================================


class TestS3Upload(unittest.TestCase):
    """Tests for _upload_s3() with mocked boto3 client."""

    def _make_uploader(self, bucket="test-bucket", region="us-west-2"):
        dest = AttachmentDestinationConfig(
            type="s3",
            key_template="{prefix}/{doc_id}/{attachment_name}",
            key_prefix="att",
            s3=AttachmentDestinationS3Config(bucket=bucket, region=region),
        )
        cfg = AttachmentConfig(enabled=True, destination=dest)
        return AttachmentUploader(cfg)

    def test_s3_upload_call_shape(self):
        uploader = self._make_uploader()
        mock_client = MagicMock()
        mock_client.put_object = MagicMock(
            return_value={
                "ETag": '"etag456"',
                "ResponseMetadata": {"HTTPStatusCode": 200},
            }
        )
        uploader._s3_client = mock_client
        uploader._s3_executor = MagicMock()

        doc = {"_id": "photo_001", "_rev": "2-abc"}
        stub = {"content_type": "image/jpeg", "digest": "md5-xyz=="}

        async def _run():
            # Patch run_in_executor to call the function directly
            loop = asyncio.get_event_loop()
            original_run = loop.run_in_executor

            async def fake_executor(executor, fn):
                return fn()

            with patch.object(loop, "run_in_executor", side_effect=fake_executor):
                etag = await uploader._upload_s3(
                    "att/photo_001/photo.jpg",
                    b"binary_data",
                    "image/jpeg",
                    doc,
                    "photo.jpg",
                    stub,
                )

            self.assertEqual(etag, '"etag456"')
            mock_client.put_object.assert_called_once()
            call_kwargs = mock_client.put_object.call_args[1]
            self.assertEqual(call_kwargs["Bucket"], "test-bucket")
            self.assertEqual(call_kwargs["Key"], "att/photo_001/photo.jpg")
            self.assertEqual(call_kwargs["ContentType"], "image/jpeg")
            self.assertEqual(call_kwargs["ContentLength"], 11)
            self.assertEqual(call_kwargs["Metadata"]["doc_id"], "photo_001")
            self.assertEqual(call_kwargs["Metadata"]["rev"], "2-abc")
            self.assertEqual(call_kwargs["Metadata"]["digest"], "md5-xyz==")

        asyncio.run(_run())


# ===================================================================
# upload_many Orchestration Tests
# ===================================================================


class TestUploadMany(unittest.TestCase):
    """Tests for upload_many() orchestration."""

    def test_upload_many_empty_fetched(self):
        cfg = AttachmentConfig(enabled=True)
        uploader = AttachmentUploader(cfg)

        async def _run():
            results = await uploader.upload_many({"_id": "d"}, {}, {})
            self.assertEqual(results, {})

        asyncio.run(_run())

    def test_upload_many_partial_failure_continue(self):
        """When halt_on_failure=False, partial failures are tolerated."""
        dest = AttachmentDestinationConfig(
            type="filesystem",
            key_template="{doc_id}/{attachment_name}",
            key_prefix="",
            filesystem=AttachmentDestinationFilesystemConfig(base_path=""),
        )
        cfg = AttachmentConfig(enabled=True, destination=dest, halt_on_failure=False)
        uploader = AttachmentUploader(cfg)

        call_count = 0

        async def fake_upload(doc, name, stub, data, dest_type):
            nonlocal call_count
            call_count += 1
            if name == "bad.txt":
                raise ValueError("simulated error")
            return AttachmentUploadResult(
                attachment_name=name,
                destination_type="filesystem",
                key="k",
                location="/tmp/" + name,
                content_type="text/plain",
                length=len(data),
                digest="",
                uploaded_at="2026-01-01T00:00:00Z",
            )

        uploader._upload_single = fake_upload

        doc = {"_id": "doc1"}
        stubs = {
            "good.txt": {"content_type": "text/plain"},
            "bad.txt": {"content_type": "text/plain"},
        }
        fetched = {"good.txt": b"ok", "bad.txt": b"fail"}

        async def _run():
            results = await uploader.upload_many(doc, stubs, fetched)
            self.assertIn("good.txt", results)
            self.assertNotIn("bad.txt", results)
            self.assertEqual(len(results), 1)

        asyncio.run(_run())

    def test_upload_many_partial_failure_halt(self):
        """When halt_on_failure=True, partial failures raise AttachmentError."""
        from rest.attachments import AttachmentError

        dest = AttachmentDestinationConfig(
            type="filesystem",
            key_template="{doc_id}/{attachment_name}",
            key_prefix="",
            filesystem=AttachmentDestinationFilesystemConfig(base_path=""),
        )
        cfg = AttachmentConfig(enabled=True, destination=dest, halt_on_failure=True)
        uploader = AttachmentUploader(cfg)

        async def fake_upload(doc, name, stub, data, dest_type):
            raise ValueError("boom")

        uploader._upload_single = fake_upload

        doc = {"_id": "doc1"}
        stubs = {"file.txt": {"content_type": "text/plain"}}
        fetched = {"file.txt": b"data"}

        async def _run():
            with self.assertRaises(AttachmentError):
                await uploader.upload_many(doc, stubs, fetched)

        asyncio.run(_run())

    def test_upload_many_metrics_incremented(self):
        """Verify metrics are incremented on successful uploads."""
        dest = AttachmentDestinationConfig(
            type="filesystem",
            key_template="{doc_id}/{attachment_name}",
            key_prefix="",
        )
        cfg = AttachmentConfig(enabled=True, destination=dest)
        metrics = MagicMock()
        uploader = AttachmentUploader(cfg, metrics=metrics)

        async def fake_upload(doc, name, stub, data, dest_type):
            return AttachmentUploadResult(
                attachment_name=name,
                destination_type="filesystem",
                key="k",
                location="/tmp/" + name,
                content_type="text/plain",
                length=len(data),
                digest="",
                uploaded_at="now",
            )

        uploader._upload_single = fake_upload

        async def _run():
            results = await uploader.upload_many(
                {"_id": "d"},
                {"f.txt": {}},
                {"f.txt": b"data"},
            )
            self.assertEqual(len(results), 1)
            # Check metrics.inc was called with upload counters
            inc_calls = {c[0][0] for c in metrics.inc.call_args_list}
            self.assertIn("attachments_uploaded_total", inc_calls)
            self.assertIn("attachments_bytes_uploaded_total", inc_calls)

        asyncio.run(_run())


# ===================================================================
# Retry Logic Tests
# ===================================================================


class TestRetryLogic(unittest.TestCase):
    """Tests for retry behaviour in _upload_single()."""

    def test_retry_on_transient_then_succeed(self):
        """Transient error on first attempt, success on second."""
        with tempfile.TemporaryDirectory() as tmpdir:
            dest = AttachmentDestinationConfig(
                type="filesystem",
                key_template="{doc_id}/{attachment_name}",
                key_prefix="",
                filesystem=AttachmentDestinationFilesystemConfig(base_path=tmpdir),
            )
            retry = AttachmentRetryConfig(
                max_retries=3,
                backoff_base_seconds=0,
                backoff_max_seconds=0,
            )
            cfg = AttachmentConfig(enabled=True, destination=dest, retry=retry)
            uploader = AttachmentUploader(cfg)

            attempt_count = 0
            original_fs_upload = uploader._upload_filesystem

            async def flaky_fs_upload(key, data):
                nonlocal attempt_count
                attempt_count += 1
                if attempt_count == 1:
                    raise ConnectionError("transient network blip")
                return await original_fs_upload(key, data)

            uploader._upload_filesystem = flaky_fs_upload

            async def _run():
                result = await uploader._upload_single(
                    {"_id": "doc1"},
                    "file.txt",
                    {"content_type": "text/plain"},
                    b"data",
                    "filesystem",
                )
                self.assertEqual(result.attachment_name, "file.txt")
                self.assertEqual(attempt_count, 2)

            asyncio.run(_run())

    def test_no_retry_on_permanent_error(self):
        """Permanent errors are not retried."""
        dest = AttachmentDestinationConfig(
            type="filesystem",
            key_template="{doc_id}/{attachment_name}",
            key_prefix="",
            filesystem=AttachmentDestinationFilesystemConfig(base_path=""),
        )
        retry = AttachmentRetryConfig(
            max_retries=3,
            backoff_base_seconds=0,
            backoff_max_seconds=0,
        )
        cfg = AttachmentConfig(enabled=True, destination=dest, retry=retry)
        uploader = AttachmentUploader(cfg)

        async def _run():
            with self.assertRaises(ValueError):
                await uploader._upload_single(
                    {"_id": "doc1"},
                    "file.txt",
                    {"content_type": "text/plain"},
                    b"data",
                    "filesystem",
                )

        asyncio.run(_run())

    def test_retries_exhausted_raises(self):
        """After max_retries, the error is raised."""
        with tempfile.TemporaryDirectory() as tmpdir:
            dest = AttachmentDestinationConfig(
                type="filesystem",
                key_template="{doc_id}/{attachment_name}",
                key_prefix="",
                filesystem=AttachmentDestinationFilesystemConfig(base_path=tmpdir),
            )
            retry = AttachmentRetryConfig(
                max_retries=2,
                backoff_base_seconds=0,
                backoff_max_seconds=0,
            )
            cfg = AttachmentConfig(enabled=True, destination=dest, retry=retry)
            uploader = AttachmentUploader(cfg)

            async def always_fail(key, data):
                raise ConnectionError("always fails")

            uploader._upload_filesystem = always_fail

            async def _run():
                with self.assertRaises(ConnectionError):
                    await uploader._upload_single(
                        {"_id": "doc1"},
                        "file.txt",
                        {"content_type": "text/plain"},
                        b"data",
                        "filesystem",
                    )

            asyncio.run(_run())


# ===================================================================
# Unsupported Destination Tests
# ===================================================================


class TestUnsupportedDestination(unittest.TestCase):
    """Tests for unsupported destination types."""

    def test_unsupported_dest_raises(self):
        dest = AttachmentDestinationConfig(type="gcs")
        retry = AttachmentRetryConfig(
            max_retries=1, backoff_base_seconds=0, backoff_max_seconds=0
        )
        cfg = AttachmentConfig(enabled=True, destination=dest, retry=retry)
        uploader = AttachmentUploader(cfg)

        async def _run():
            with self.assertRaises(ValueError) as ctx:
                await uploader._upload_single({"_id": "d"}, "f", {}, b"x", "gcs")
            self.assertIn("unsupported destination", str(ctx.exception))

        asyncio.run(_run())


# ===================================================================
# S3 Config Parsing Tests
# ===================================================================


class TestS3ConfigParsing(unittest.TestCase):
    """Tests that S3 destination config fields are correctly parsed."""

    def test_parse_s3_destination_config(self):
        raw = {
            "enabled": True,
            "destination": {
                "type": "s3",
                "key_template": "{prefix}/{doc_id}/{attachment_name}",
                "key_prefix": "att",
                "s3": {
                    "bucket": "my-bucket",
                    "region": "eu-west-1",
                    "endpoint_url": "https://s3.custom.com",
                    "storage_class": "STANDARD_IA",
                },
            },
        }
        cfg = parse_attachment_config(raw)
        self.assertEqual(cfg.destination.s3.bucket, "my-bucket")
        self.assertEqual(cfg.destination.s3.region, "eu-west-1")
        self.assertEqual(cfg.destination.s3.endpoint_url, "https://s3.custom.com")
        self.assertEqual(cfg.destination.s3.storage_class, "STANDARD_IA")
        # Defaults
        self.assertEqual(cfg.destination.s3.access_key_id, "")
        self.assertEqual(cfg.destination.s3.server_side_encryption, "")

    def test_parse_http_destination_config(self):
        raw = {
            "enabled": True,
            "destination": {
                "type": "http",
                "http": {
                    "url_template": "https://cdn.example.com/{doc_id}/{attachment_name}",
                    "method": "POST",
                    "headers": {"Authorization": "Bearer tok"},
                },
            },
        }
        cfg = parse_attachment_config(raw)
        self.assertEqual(
            cfg.destination.http.url_template,
            "https://cdn.example.com/{doc_id}/{attachment_name}",
        )
        self.assertEqual(cfg.destination.http.method, "POST")
        self.assertEqual(cfg.destination.http.headers, {"Authorization": "Bearer tok"})

    def test_parse_filesystem_destination_config(self):
        raw = {
            "enabled": True,
            "destination": {
                "type": "filesystem",
                "filesystem": {
                    "base_path": "/mnt/data",
                    "dir_template": "{doc_id}",
                    "preserve_filename": False,
                },
            },
        }
        cfg = parse_attachment_config(raw)
        self.assertEqual(cfg.destination.filesystem.base_path, "/mnt/data")
        self.assertFalse(cfg.destination.filesystem.preserve_filename)


# ===================================================================
# Close / Cleanup Tests
# ===================================================================


class TestUploaderCleanup(unittest.TestCase):
    """Tests for AttachmentUploader.close()."""

    def test_close_without_init(self):
        cfg = AttachmentConfig(enabled=True)
        uploader = AttachmentUploader(cfg)

        async def _run():
            await uploader.close()

        asyncio.run(_run())

    def test_close_with_http_session(self):
        cfg = AttachmentConfig(enabled=True)
        uploader = AttachmentUploader(cfg)
        mock_session = AsyncMock()
        uploader._http_session = mock_session

        async def _run():
            await uploader.close()
            mock_session.close.assert_called_once()
            self.assertIsNone(uploader._http_session)

        asyncio.run(_run())


if __name__ == "__main__":
    unittest.main()
