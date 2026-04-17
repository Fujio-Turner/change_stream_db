#!/usr/bin/env python3
"""
Unit tests for changes_worker.py

Covers:
  - Config validation (validate_config) across all three gateway sources
  - URL / auth / SSL helpers (build_base_url, build_ssl_context, build_auth_headers, build_basic_auth)
  - Checkpoint key derivation & local file fallback
  - Serialization helpers (serialize_doc for json, xml, form)
  - XML & flatten helpers (_dict_to_xml, _flatten_dict)
  - determine_method (PUT vs DELETE)
  - _chunked utility
  - OutputForwarder stdout mode
  - RetryableHTTP retry & error behaviour
"""

import asyncio
import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# Ensure the module under test is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import aiohttp
import main as cw
from rest.output_http import (
    _dict_to_xml, _flatten_dict, _ClientHTTPError, serialize_doc,
)


# ---------------------------------------------------------------------------
# Helper: minimal valid config
# ---------------------------------------------------------------------------

def _base_config(**overrides) -> dict:
    """Return a minimal valid config dict; apply overrides at top level."""
    cfg = {
        "gateway": {
            "src": "sync_gateway",
            "url": "http://localhost:4984",
            "database": "db",
            "scope": "us",
            "collection": "prices",
        },
        "auth": {
            "method": "basic",
            "username": "bob",
            "password": "password",
        },
        "changes_feed": {
            "feed_type": "longpoll",
            "poll_interval_seconds": 10,
            "include_docs": True,
            "timeout_ms": 60000,
            "heartbeat_ms": 30000,
            "http_timeout_seconds": 300,
        },
        "processing": {},
        "checkpoint": {"enabled": True, "client_id": "test"},
        "output": {"mode": "stdout", "output_format": "json"},
        "retry": {"max_retries": 5},
        "logging": {"level": "DEBUG"},
    }
    cfg.update(overrides)
    return cfg


# ===================================================================
# validate_config
# ===================================================================

class TestValidateConfig(unittest.TestCase):
    """Tests for validate_config()."""

    # -- Happy path --

    def test_valid_sync_gateway_config(self):
        src, warnings, errors = cw.validate_config(_base_config())
        self.assertEqual(src, "sync_gateway")
        self.assertEqual(errors, [])

    def test_valid_app_services_config(self):
        cfg = _base_config()
        cfg["gateway"]["src"] = "app_services"
        cfg["gateway"]["url"] = "https://my-cluster.cloud.couchbase.com:4984"
        src, warnings, errors = cw.validate_config(cfg)
        self.assertEqual(src, "app_services")
        self.assertEqual(errors, [])

    def test_valid_edge_server_config(self):
        cfg = _base_config()
        cfg["gateway"]["src"] = "edge_server"
        src, warnings, errors = cw.validate_config(cfg)
        self.assertEqual(src, "edge_server")
        self.assertEqual(errors, [])

    # -- gateway.src validation --

    def test_invalid_src(self):
        cfg = _base_config()
        cfg["gateway"]["src"] = "couchbase_lite"
        src, _, errors = cw.validate_config(cfg)
        self.assertTrue(any("gateway.src" in e for e in errors))

    def test_missing_url(self):
        cfg = _base_config()
        cfg["gateway"]["url"] = ""
        _, _, errors = cw.validate_config(cfg)
        self.assertTrue(any("gateway.url" in e for e in errors))

    def test_missing_database(self):
        cfg = _base_config()
        cfg["gateway"]["database"] = ""
        _, _, errors = cw.validate_config(cfg)
        self.assertTrue(any("gateway.database" in e for e in errors))

    # -- auth validation --

    def test_basic_auth_missing_username(self):
        cfg = _base_config()
        cfg["auth"] = {"method": "basic", "password": "pw"}
        _, _, errors = cw.validate_config(cfg)
        self.assertTrue(any("auth.username" in e for e in errors))

    def test_basic_auth_missing_password(self):
        cfg = _base_config()
        cfg["auth"] = {"method": "basic", "username": "bob"}
        _, _, errors = cw.validate_config(cfg)
        self.assertTrue(any("auth.password" in e for e in errors))

    def test_session_auth_missing_cookie(self):
        cfg = _base_config()
        cfg["auth"] = {"method": "session"}
        _, _, errors = cw.validate_config(cfg)
        self.assertTrue(any("session_cookie" in e for e in errors))

    def test_bearer_auth_missing_token(self):
        cfg = _base_config()
        cfg["auth"] = {"method": "bearer"}
        _, _, errors = cw.validate_config(cfg)
        self.assertTrue(any("bearer_token" in e for e in errors))

    def test_bearer_auth_not_supported_edge_server(self):
        cfg = _base_config()
        cfg["gateway"]["src"] = "edge_server"
        cfg["auth"] = {"method": "bearer", "bearer_token": "tok"}
        _, _, errors = cw.validate_config(cfg)
        self.assertTrue(any("bearer" in e.lower() and "Edge Server" in e for e in errors))

    def test_invalid_auth_method(self):
        cfg = _base_config()
        cfg["auth"] = {"method": "oauth2"}
        _, _, errors = cw.validate_config(cfg)
        self.assertTrue(any("auth.method" in e for e in errors))

    # -- changes_feed validation --

    def test_websocket_not_supported_edge_server(self):
        cfg = _base_config()
        cfg["gateway"]["src"] = "edge_server"
        cfg["changes_feed"]["feed_type"] = "websocket"
        _, _, errors = cw.validate_config(cfg)
        self.assertTrue(any("websocket" in e for e in errors))

    def test_sse_only_supported_by_edge_server(self):
        cfg = _base_config()
        cfg["changes_feed"]["feed_type"] = "sse"
        _, _, errors = cw.validate_config(cfg)
        self.assertTrue(any("sse" in e.lower() for e in errors))

    def test_sse_valid_for_edge_server(self):
        cfg = _base_config()
        cfg["gateway"]["src"] = "edge_server"
        cfg["changes_feed"]["feed_type"] = "sse"
        _, _, errors = cw.validate_config(cfg)
        feed_errors = [e for e in errors if "feed_type" in e]
        self.assertEqual(feed_errors, [])

    def test_invalid_version_type(self):
        cfg = _base_config()
        cfg["changes_feed"]["version_type"] = "hlc"
        _, _, errors = cw.validate_config(cfg)
        self.assertTrue(any("version_type" in e for e in errors))

    def test_version_type_not_supported_edge_server(self):
        cfg = _base_config()
        cfg["gateway"]["src"] = "edge_server"
        cfg["changes_feed"]["version_type"] = "cv"
        _, _, errors = cw.validate_config(cfg)
        self.assertTrue(any("version_type" in e for e in errors))

    # -- warnings --

    def test_app_services_http_warning(self):
        cfg = _base_config()
        cfg["gateway"]["src"] = "app_services"
        cfg["gateway"]["url"] = "http://app.cloud.couchbase.com"
        _, warnings, errors = cw.validate_config(cfg)
        self.assertTrue(any("HTTPS" in w for w in warnings))

    def test_edge_server_timeout_warning(self):
        cfg = _base_config()
        cfg["gateway"]["src"] = "edge_server"
        cfg["changes_feed"]["timeout_ms"] = 1_000_000
        _, warnings, _ = cw.validate_config(cfg)
        self.assertTrue(any("900000" in w for w in warnings))

    def test_edge_server_heartbeat_warning(self):
        cfg = _base_config()
        cfg["gateway"]["src"] = "edge_server"
        cfg["changes_feed"]["heartbeat_ms"] = 5000
        _, warnings, _ = cw.validate_config(cfg)
        self.assertTrue(any("25000" in w for w in warnings))

    def test_low_poll_interval_warning(self):
        cfg = _base_config()
        cfg["changes_feed"]["poll_interval_seconds"] = 0
        _, warnings, _ = cw.validate_config(cfg)
        self.assertTrue(any("poll_interval" in w for w in warnings))

    def test_low_http_timeout_warning(self):
        cfg = _base_config()
        cfg["changes_feed"]["http_timeout_seconds"] = 5
        _, warnings, _ = cw.validate_config(cfg)
        self.assertTrue(any("http_timeout" in w for w in warnings))

    def test_edge_server_include_docs_false_warning(self):
        cfg = _base_config()
        cfg["gateway"]["src"] = "edge_server"
        cfg["changes_feed"]["include_docs"] = False
        _, warnings, _ = cw.validate_config(cfg)
        self.assertTrue(any("bulk_get" in w.lower() or "individually" in w.lower() for w in warnings))

    # -- output validation --

    def test_invalid_output_mode(self):
        cfg = _base_config()
        cfg["output"] = {"mode": "kafka", "output_format": "json"}
        _, _, errors = cw.validate_config(cfg)
        self.assertTrue(any("output.mode" in e for e in errors))

    def test_http_output_missing_target_url(self):
        cfg = _base_config()
        cfg["output"] = {"mode": "http", "target_url": "", "output_format": "json"}
        _, _, errors = cw.validate_config(cfg)
        self.assertTrue(any("target_url" in e for e in errors))

    def test_invalid_output_format(self):
        cfg = _base_config()
        cfg["output"] = {"mode": "stdout", "output_format": "protobuf"}
        _, _, errors = cw.validate_config(cfg)
        self.assertTrue(any("output_format" in e or "output.output_format" in e for e in errors))

    # -- retry validation --

    def test_negative_max_retries(self):
        cfg = _base_config()
        cfg["retry"]["max_retries"] = -1
        _, _, errors = cw.validate_config(cfg)
        self.assertTrue(any("max_retries" in e for e in errors))

    # -- halt_on_failure warning --

    def test_halt_on_failure_false_warning(self):
        cfg = _base_config()
        cfg["output"] = {
            "mode": "http",
            "target_url": "http://example.com",
            "halt_on_failure": False,
            "output_format": "json",
        }
        _, warnings, _ = cw.validate_config(cfg)
        self.assertTrue(any("halt_on_failure" in w for w in warnings))


# ===================================================================
# build_base_url
# ===================================================================

class TestBuildBaseUrl(unittest.TestCase):

    def test_with_scope_and_collection(self):
        gw = {"url": "http://localhost:4984", "database": "db", "scope": "us", "collection": "prices"}
        self.assertEqual(cw.build_base_url(gw), "http://localhost:4984/db.us.prices")

    def test_without_scope_collection(self):
        gw = {"url": "http://localhost:4984", "database": "mydb", "scope": "", "collection": ""}
        self.assertEqual(cw.build_base_url(gw), "http://localhost:4984/mydb")

    def test_trailing_slash_stripped(self):
        gw = {"url": "http://localhost:4984/", "database": "db", "scope": "s", "collection": "c"}
        self.assertEqual(cw.build_base_url(gw), "http://localhost:4984/db.s.c")


# ===================================================================
# build_ssl_context
# ===================================================================

class TestBuildSslContext(unittest.TestCase):

    def test_http_returns_none(self):
        self.assertIsNone(cw.build_ssl_context({"url": "http://localhost:4984"}))

    def test_https_returns_context(self):
        ctx = cw.build_ssl_context({"url": "https://example.com"})
        self.assertIsNotNone(ctx)

    def test_self_signed_disables_verification(self):
        import ssl
        ctx = cw.build_ssl_context({"url": "https://example.com", "accept_self_signed_certs": True})
        self.assertFalse(ctx.check_hostname)
        self.assertEqual(ctx.verify_mode, ssl.CERT_NONE)


# ===================================================================
# build_auth_headers / build_basic_auth
# ===================================================================

class TestAuthBuilders(unittest.TestCase):

    def test_bearer_headers(self):
        h = cw.build_auth_headers({"method": "bearer", "bearer_token": "tok123"}, "sync_gateway")
        self.assertEqual(h["Authorization"], "Bearer tok123")

    def test_session_headers(self):
        h = cw.build_auth_headers({"method": "session", "session_cookie": "abc"}, "sync_gateway")
        self.assertIn("SyncGatewaySession=abc", h["Cookie"])

    def test_basic_headers_empty(self):
        h = cw.build_auth_headers({"method": "basic", "username": "u", "password": "p"}, "sync_gateway")
        self.assertEqual(h, {})

    def test_build_basic_auth_returns_auth(self):
        import aiohttp
        auth = cw.build_basic_auth({"method": "basic", "username": "u", "password": "p"})
        self.assertIsInstance(auth, aiohttp.BasicAuth)

    def test_build_basic_auth_none_for_session(self):
        self.assertIsNone(cw.build_basic_auth({"method": "session", "session_cookie": "c"}))


# ===================================================================
# Checkpoint – key derivation & local fallback
# ===================================================================

class TestCheckpoint(unittest.TestCase):

    def test_uuid_derivation(self):
        gw = {"url": "http://localhost:4984", "database": "db", "scope": "us", "collection": "prices"}
        channels = ["chan-a", "chan-b"]
        cp = cw.Checkpoint({"client_id": "my_worker"}, gw, channels)

        base_url = cw.build_base_url(gw)
        expected_raw = f"my_worker{base_url}chan-a,chan-b"
        expected_uuid = hashlib.sha1(expected_raw.encode()).hexdigest()
        self.assertEqual(cp._uuid, expected_uuid)
        self.assertEqual(cp.local_doc_path, f"_local/checkpoint-{expected_uuid}")

    def test_uuid_channels_sorted(self):
        gw = {"url": "http://localhost:4984", "database": "db", "scope": "", "collection": ""}
        cp1 = cw.Checkpoint({"client_id": "w"}, gw, ["b", "a"])
        cp2 = cw.Checkpoint({"client_id": "w"}, gw, ["a", "b"])
        self.assertEqual(cp1._uuid, cp2._uuid)

    def test_load_fallback_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"SGs_Seq": "42"}, f)
            f.flush()
            path = f.name
        try:
            gw = {"url": "http://localhost:4984", "database": "db", "scope": "", "collection": ""}
            cp = cw.Checkpoint({"client_id": "w", "file": path}, gw, [])
            seq = cp._load_fallback()
            self.assertEqual(seq, "42")
        finally:
            os.unlink(path)

    def test_load_fallback_missing_file(self):
        gw = {"url": "http://localhost:4984", "database": "db", "scope": "", "collection": ""}
        cp = cw.Checkpoint({"client_id": "w", "file": "/tmp/nonexistent_checkpoint.json"}, gw, [])
        self.assertEqual(cp._load_fallback(), "0")

    def test_save_fallback(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            gw = {"url": "http://localhost:4984", "database": "db", "scope": "", "collection": ""}
            cp = cw.Checkpoint({"client_id": "w", "file": path}, gw, [])
            cp._save_fallback("99")
            data = json.loads(Path(path).read_text())
            self.assertEqual(data["SGs_Seq"], "99")
            self.assertIn("time", data)
            self.assertIsInstance(data["time"], int)
            self.assertIn("remote", data)
            self.assertNotIn("dateTime", data)
            self.assertNotIn("local_internal", data)
        finally:
            os.unlink(path)

    def test_checkpoint_disabled(self):
        gw = {"url": "http://localhost:4984", "database": "db", "scope": "", "collection": ""}
        cp = cw.Checkpoint({"enabled": False, "client_id": "w"}, gw, [])
        seq = asyncio.run(cp.load(MagicMock(), "http://x", None, {}))
        self.assertEqual(seq, "0")


# ===================================================================
# Serialization – serialize_doc
# ===================================================================

class TestSerializeDoc(unittest.TestCase):

    def test_json_format(self):
        doc = {"_id": "doc1", "name": "Alice"}
        body, ct = serialize_doc(doc, "json")
        self.assertEqual(ct, "application/json")
        self.assertEqual(json.loads(body), doc)

    def test_xml_format(self):
        doc = {"_id": "doc1", "name": "Alice"}
        body, ct = serialize_doc(doc, "xml")
        self.assertEqual(ct, "application/xml")
        self.assertIn(b"Alice", body)
        self.assertIn(b"<name>", body)

    def test_form_format(self):
        doc = {"key": "val", "num": 42}
        body, ct = serialize_doc(doc, "form")
        self.assertEqual(ct, "application/x-www-form-urlencoded")
        self.assertIn("key=val", body)

    def test_unknown_format_raises(self):
        with self.assertRaises(ValueError):
            serialize_doc({}, "protobuf")


# ===================================================================
# _dict_to_xml / _flatten_dict
# ===================================================================

class TestXmlHelper(unittest.TestCase):

    def test_nested_dict(self):
        doc = {"a": {"b": "hello"}}
        xml_bytes = _dict_to_xml(doc, "root")
        self.assertIn(b"<a>", xml_bytes)
        self.assertIn(b"<b>hello</b>", xml_bytes)

    def test_list_elements(self):
        doc = {"items": [1, 2]}
        xml_bytes = _dict_to_xml(doc, "root")
        self.assertIn(b"<item>1</item>", xml_bytes)
        self.assertIn(b"<item>2</item>", xml_bytes)

    def test_none_value(self):
        doc = {"empty": None}
        xml_bytes = _dict_to_xml(doc, "root")
        self.assertIn(b"<empty", xml_bytes)


class TestFlattenDict(unittest.TestCase):

    def test_flat(self):
        self.assertEqual(_flatten_dict({"a": "1", "b": "2"}), {"a": "1", "b": "2"})

    def test_nested(self):
        result = _flatten_dict({"a": {"b": 1}})
        self.assertEqual(result, {"a.b": "1"})

    def test_list_value(self):
        result = _flatten_dict({"tags": [1, 2]})
        self.assertEqual(result["tags"], "[1, 2]")

    def test_none_value(self):
        result = _flatten_dict({"x": None})
        self.assertEqual(result["x"], "")


# ===================================================================
# determine_method
# ===================================================================

class TestDetermineMethod(unittest.TestCase):

    def test_normal_change_returns_put(self):
        self.assertEqual(cw.determine_method({"id": "doc1"}), "PUT")

    def test_deleted_returns_delete(self):
        self.assertEqual(cw.determine_method({"id": "doc1", "deleted": True}), "DELETE")

    def test_not_deleted_returns_put(self):
        self.assertEqual(cw.determine_method({"id": "doc1", "deleted": False}), "PUT")

    def test_custom_write_method(self):
        self.assertEqual(cw.determine_method({"id": "doc1"}, write_method="POST"), "POST")

    def test_custom_delete_method(self):
        self.assertEqual(cw.determine_method({"id": "doc1", "deleted": True}, delete_method="PUT"), "PUT")

    def test_custom_both_methods(self):
        self.assertEqual(
            cw.determine_method({"id": "d1"}, write_method="PATCH", delete_method="POST"),
            "PATCH",
        )
        self.assertEqual(
            cw.determine_method({"id": "d1", "deleted": True}, write_method="PATCH", delete_method="POST"),
            "POST",
        )


# ===================================================================
# _chunked
# ===================================================================

class TestChunked(unittest.TestCase):

    def test_even_split(self):
        self.assertEqual(cw._chunked([1, 2, 3, 4], 2), [[1, 2], [3, 4]])

    def test_uneven_split(self):
        self.assertEqual(cw._chunked([1, 2, 3], 2), [[1, 2], [3]])

    def test_empty_list(self):
        self.assertEqual(cw._chunked([], 5), [])

    def test_single_chunk(self):
        self.assertEqual(cw._chunked([1, 2], 10), [[1, 2]])


# ===================================================================
# OutputForwarder – stdout mode
# ===================================================================

def _stdout_out_cfg(**overrides):
    cfg = {
        "mode": "stdout",
        "output_format": "json",
        "target_auth": {"method": "none"},
    }
    cfg.update(overrides)
    return cfg


class TestOutputForwarderStdout(unittest.TestCase):

    def test_send_stdout_json(self):
        session = MagicMock()
        fwd = cw.OutputForwarder(session, _stdout_out_cfg(), dry_run=False)
        doc = {"_id": "doc1", "value": 42}
        with patch("sys.stdout") as mock_stdout:
            mock_stdout.write = MagicMock()
            mock_stdout.flush = MagicMock()
            fwd._send_stdout(doc)
            written = mock_stdout.write.call_args[0][0]
            self.assertEqual(json.loads(written.strip()), doc)

    def test_send_stdout_xml(self):
        session = MagicMock()
        fwd = cw.OutputForwarder(session, _stdout_out_cfg(output_format="xml"), dry_run=False)
        doc = {"_id": "doc1"}
        with patch("sys.stdout") as mock_stdout:
            mock_stdout.buffer = MagicMock()
            fwd._send_stdout(doc)
            mock_stdout.buffer.write.assert_called_once()


# ===================================================================
# RetryableHTTP
# ===================================================================

class TestRetryableHTTP(unittest.TestCase):

    def test_success_on_first_try(self):
        session = MagicMock()
        resp = AsyncMock()
        resp.status = 200
        session.request = AsyncMock(return_value=resp)

        http = cw.RetryableHTTP(session, {"max_retries": 3, "backoff_base_seconds": 0, "backoff_max_seconds": 0})
        result = asyncio.run(http.request("GET", "http://example.com"))
        self.assertEqual(result.status, 200)
        session.request.assert_called_once()

    def test_client_error_raises(self):
        session = MagicMock()
        resp = AsyncMock()
        resp.status = 404
        resp.text = AsyncMock(return_value="Not found")
        resp.content_type = "text/plain"
        session.request = AsyncMock(return_value=resp)

        http = cw.RetryableHTTP(session, {"max_retries": 3, "backoff_base_seconds": 0, "backoff_max_seconds": 0})
        with self.assertRaises(cw.ClientHTTPError) as ctx:
            asyncio.run(http.request("GET", "http://example.com"))
        self.assertEqual(ctx.exception.status, 404)

    def test_retries_on_server_error(self):
        session = MagicMock()
        resp_fail = AsyncMock()
        resp_fail.status = 503
        resp_fail.text = AsyncMock(return_value="Service Unavailable")
        resp_fail.release = MagicMock()

        resp_ok = AsyncMock()
        resp_ok.status = 200

        session.request = AsyncMock(side_effect=[resp_fail, resp_ok])

        http = cw.RetryableHTTP(session, {
            "max_retries": 2,
            "backoff_base_seconds": 0,
            "backoff_max_seconds": 0,
            "retry_on_status": [503],
        })
        result = asyncio.run(http.request("GET", "http://example.com"))
        self.assertEqual(result.status, 200)
        self.assertEqual(session.request.call_count, 2)

    def test_retries_exhausted_raises(self):
        session = MagicMock()
        resp_fail = AsyncMock()
        resp_fail.status = 500
        resp_fail.text = AsyncMock(return_value="Internal error")
        resp_fail.release = MagicMock()

        session.request = AsyncMock(return_value=resp_fail)

        http = cw.RetryableHTTP(session, {
            "max_retries": 2,
            "backoff_base_seconds": 0,
            "backoff_max_seconds": 0,
            "retry_on_status": [500],
        })
        with self.assertRaises(ConnectionError):
            asyncio.run(http.request("GET", "http://example.com"))


# ===================================================================
# HTTP error classes
# ===================================================================

class TestHTTPErrors(unittest.TestCase):

    def test_client_http_error(self):
        e = cw.ClientHTTPError(400, "Bad request body")
        self.assertEqual(e.status, 400)
        self.assertIn("400", str(e))

    def test_redirect_http_error(self):
        e = cw.RedirectHTTPError(301, "Moved")
        self.assertEqual(e.status, 301)

    def test_server_http_error(self):
        e = cw.ServerHTTPError(500, "Server blew up")
        self.assertEqual(e.status, 500)


# ===================================================================
# _sleep_or_shutdown
# ===================================================================

class TestSleepOrShutdown(unittest.TestCase):

    def test_returns_immediately_if_event_set(self):
        async def _run():
            event = asyncio.Event()
            event.set()
            await cw._sleep_or_shutdown(10, event)
        asyncio.run(_run())

    def test_returns_after_timeout(self):
        async def _run():
            event = asyncio.Event()
            await cw._sleep_or_shutdown(0.01, event)
        asyncio.run(_run())


# ===================================================================
# OutputForwarder – response time tracking
# ===================================================================

class TestOutputForwarderRequestOptions(unittest.TestCase):
    """Tests for output.request_options (custom params & headers)."""

    def test_defaults_to_empty(self):
        session = MagicMock()
        fwd = cw.OutputForwarder(session, _stdout_out_cfg(), dry_run=False)
        self.assertEqual(fwd._extra_params, {})
        self.assertEqual(fwd._extra_headers, {})

    def test_picks_up_params_and_headers(self):
        session = MagicMock()
        cfg = _stdout_out_cfg(request_options={
            "params": {"batch": "ok", "source": "cbl"},
            "headers": {"X-Source": "changes-worker"},
        })
        fwd = cw.OutputForwarder(session, cfg, dry_run=False)
        self.assertEqual(fwd._extra_params, {"batch": "ok", "source": "cbl"})
        self.assertEqual(fwd._extra_headers, {"X-Source": "changes-worker"})

    def test_http_send_passes_params_and_headers(self):
        """Verify that send() forwards extra params and headers to the HTTP call."""
        mock_http = MagicMock()
        resp = AsyncMock()
        resp.status = 200
        resp.release = MagicMock()
        mock_http.request = AsyncMock(return_value=resp)

        session = MagicMock()
        cfg = {
            "mode": "http",
            "target_url": "http://example.com/api",
            "output_format": "json",
            "target_auth": {"method": "none"},
            "request_options": {
                "params": {"batch": "ok"},
                "headers": {"X-Region": "us-east-1"},
            },
            "retry": {"max_retries": 1, "backoff_base_seconds": 0,
                      "backoff_max_seconds": 0, "retry_on_status": []},
            "halt_on_failure": True,
        }
        fwd = cw.OutputForwarder(session, cfg, dry_run=False)
        fwd._http = mock_http

        doc = {"_id": "doc123", "val": 1}
        asyncio.run(fwd.send(doc, "PUT"))

        call_kwargs = mock_http.request.call_args
        self.assertEqual(call_kwargs.kwargs.get("params") or call_kwargs[1].get("params"),
                         {"batch": "ok"})
        headers = call_kwargs.kwargs.get("headers") or call_kwargs[1].get("headers")
        self.assertEqual(headers["X-Region"], "us-east-1")
        self.assertEqual(headers["Content-Type"], "application/json")

    def test_http_send_no_params_passes_none(self):
        """When params is empty, None is passed (no query string)."""
        mock_http = MagicMock()
        resp = AsyncMock()
        resp.status = 200
        resp.release = MagicMock()
        mock_http.request = AsyncMock(return_value=resp)

        session = MagicMock()
        cfg = {
            "mode": "http",
            "target_url": "http://example.com/api",
            "output_format": "json",
            "target_auth": {"method": "none"},
            "retry": {"max_retries": 1, "backoff_base_seconds": 0,
                      "backoff_max_seconds": 0, "retry_on_status": []},
            "halt_on_failure": True,
        }
        fwd = cw.OutputForwarder(session, cfg, dry_run=False)
        fwd._http = mock_http

        asyncio.run(fwd.send({"_id": "doc1"}, "PUT"))

        call_kwargs = mock_http.request.call_args
        params = call_kwargs.kwargs.get("params") or call_kwargs[1].get("params")
        self.assertIsNone(params)


class TestDeadLetterQueue(unittest.TestCase):
    """Tests for DeadLetterQueue."""

    def test_disabled_when_no_path(self):
        dlq = cw.DeadLetterQueue("")
        self.assertFalse(dlq.enabled)

    def test_enabled_when_path_set(self):
        dlq = cw.DeadLetterQueue("failed.jsonl")
        self.assertTrue(dlq.enabled)

    def test_write_appends_jsonl(self):
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
            path = f.name
        try:
            dlq = cw.DeadLetterQueue(path)
            doc = {"_id": "doc1", "val": 42}
            result = {"ok": False, "doc_id": "doc1", "method": "PUT", "status": 500, "error": "boom"}

            asyncio.run(dlq.write(doc, result, "15"))

            lines = Path(path).read_text().strip().split("\n")
            self.assertEqual(len(lines), 1)
            entry = json.loads(lines[0])
            self.assertEqual(entry["doc_id"], "doc1")
            self.assertEqual(entry["seq"], "15")
            self.assertEqual(entry["status"], 500)
            self.assertEqual(entry["error"], "boom")
            self.assertEqual(entry["doc"], doc)
            self.assertIn("time", entry)
            self.assertIsInstance(entry["time"], int)

            # Second write appends
            asyncio.run(dlq.write({"_id": "doc2"}, {"ok": False, "doc_id": "doc2", "method": "DELETE", "status": 404, "error": "nope"}, "20"))
            lines = Path(path).read_text().strip().split("\n")
            self.assertEqual(len(lines), 2)
        finally:
            os.unlink(path)

    def test_write_noop_when_disabled(self):
        dlq = cw.DeadLetterQueue("")
        asyncio.run(dlq.write({}, {}, "0"))


class TestSendReturnsResultDict(unittest.TestCase):
    """Tests that send() returns a result dict instead of None."""

    def test_stdout_send_returns_ok(self):
        session = MagicMock()
        fwd = cw.OutputForwarder(session, _stdout_out_cfg(), dry_run=False)
        with patch("sys.stdout") as mock_stdout:
            mock_stdout.write = MagicMock()
            mock_stdout.flush = MagicMock()
            result = asyncio.run(fwd.send({"_id": "doc1"}, "PUT"))
        self.assertTrue(result["ok"])
        self.assertEqual(result["doc_id"], "doc1")

    def test_http_send_returns_ok_on_success(self):
        mock_http = MagicMock()
        resp = AsyncMock()
        resp.status = 200
        resp.release = MagicMock()
        mock_http.request = AsyncMock(return_value=resp)

        session = MagicMock()
        cfg = {
            "mode": "http", "target_url": "http://example.com",
            "output_format": "json", "target_auth": {"method": "none"},
            "retry": {"max_retries": 1, "backoff_base_seconds": 0,
                      "backoff_max_seconds": 0, "retry_on_status": []},
            "halt_on_failure": True,
        }
        fwd = cw.OutputForwarder(session, cfg, dry_run=False)
        fwd._http = mock_http

        result = asyncio.run(fwd.send({"_id": "doc1"}, "PUT"))
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], 200)

    def test_http_send_returns_fail_on_4xx_no_halt(self):
        mock_http = MagicMock()
        mock_http.request = AsyncMock(side_effect=_ClientHTTPError(400, "bad"))

        session = MagicMock()
        cfg = {
            "mode": "http", "target_url": "http://example.com",
            "output_format": "json", "target_auth": {"method": "none"},
            "retry": {"max_retries": 1, "backoff_base_seconds": 0,
                      "backoff_max_seconds": 0, "retry_on_status": []},
            "halt_on_failure": False,
        }
        fwd = cw.OutputForwarder(session, cfg, dry_run=False)
        fwd._http = mock_http

        result = asyncio.run(fwd.send({"_id": "doc1"}, "PUT"))
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], 400)
        self.assertIn("bad", result["error"])


class TestMetricsNewCounters(unittest.TestCase):
    """Tests for output_success_total and dead_letter_total metrics."""

    def test_new_counters_in_render(self):
        m = cw.MetricsCollector("sync_gateway", "db")
        m.inc("output_success_total", 5)
        m.inc("dead_letter_total", 2)
        body = m.render()
        self.assertIn("changes_worker_output_success_total", body)
        self.assertIn("} 5", body)
        self.assertIn("changes_worker_dead_letter_total", body)
        self.assertIn("} 2", body)


class TestOutputForwarderStats(unittest.TestCase):

    def test_log_stats_no_times(self):
        session = MagicMock()
        fwd = cw.OutputForwarder(session, _stdout_out_cfg(), dry_run=False)
        fwd.log_stats()

    def test_record_time_and_stats(self):
        session = MagicMock()
        fwd = cw.OutputForwarder(session, _stdout_out_cfg(log_response_times=True), dry_run=False)

        async def _run():
            await fwd._record_time(10.0)
            await fwd._record_time(20.0)
        asyncio.run(_run())

        self.assertEqual(len(fwd._resp_times), 2)
        self.assertEqual(min(fwd._resp_times), 10.0)
        self.assertEqual(max(fwd._resp_times), 20.0)


# ===================================================================
# load_config
# ===================================================================

class TestLoadConfig(unittest.TestCase):

    def test_loads_json_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"gateway": {"src": "sync_gateway"}}, f)
            path = f.name
        try:
            cfg = cw.load_config(path)
            self.assertEqual(cfg["gateway"]["src"], "sync_gateway")
        finally:
            os.unlink(path)


# ===================================================================
# MetricsCollector
# ===================================================================

class TestMetricsCollector(unittest.TestCase):

    def test_initial_state(self):
        m = cw.MetricsCollector("sync_gateway", "db")
        self.assertEqual(m.poll_cycles_total, 0)
        self.assertEqual(m.changes_received_total, 0)
        self.assertEqual(m.checkpoint_seq, "0")
        self.assertEqual(m.output_endpoint_up, 1)

    def test_inc(self):
        m = cw.MetricsCollector("sync_gateway", "db")
        m.inc("poll_cycles_total")
        self.assertEqual(m.poll_cycles_total, 1)
        m.inc("poll_cycles_total", 5)
        self.assertEqual(m.poll_cycles_total, 6)

    def test_set(self):
        m = cw.MetricsCollector("sync_gateway", "db")
        m.set("checkpoint_seq", "1234")
        self.assertEqual(m.checkpoint_seq, "1234")
        m.set("output_endpoint_up", 0)
        self.assertEqual(m.output_endpoint_up, 0)

    def test_record_output_response_time(self):
        m = cw.MetricsCollector("sync_gateway", "db")
        m.record_output_response_time(0.05)
        m.record_output_response_time(0.10)
        self.assertEqual(len(m._output_resp_times), 2)

    def test_render_prometheus_format(self):
        m = cw.MetricsCollector("sync_gateway", "mydb")
        m.inc("poll_cycles_total", 3)
        m.inc("changes_received_total", 100)
        m.inc("changes_processed_total", 95)
        m.inc("changes_filtered_total", 5)
        m.set("checkpoint_seq", "500")
        m.record_output_response_time(0.05)
        m.record_output_response_time(0.10)

        body = m.render()

        # Verify Prometheus text format structure
        self.assertIn('# TYPE changes_worker_poll_cycles_total counter', body)
        self.assertIn('changes_worker_poll_cycles_total{src="sync_gateway",database="mydb"} 3', body)
        self.assertIn('changes_worker_changes_received_total{src="sync_gateway",database="mydb"} 100', body)
        self.assertIn('changes_worker_changes_processed_total{src="sync_gateway",database="mydb"} 95', body)
        self.assertIn('# TYPE changes_worker_uptime_seconds gauge', body)
        self.assertIn('# TYPE changes_worker_output_response_time_seconds summary', body)
        self.assertIn('changes_worker_output_response_time_seconds_count{src="sync_gateway",database="mydb"} 2', body)
        self.assertIn('seq="500"', body)

    def test_render_empty_response_times(self):
        m = cw.MetricsCollector("edge_server", "db")
        body = m.render()
        self.assertIn('changes_worker_output_response_time_seconds_count{src="edge_server",database="db"} 0', body)
        self.assertIn('quantile="0.5"} 0.000000', body)

    def test_labels_contain_src_and_database(self):
        m = cw.MetricsCollector("app_services", "travel-sample")
        body = m.render()
        self.assertIn('src="app_services"', body)
        self.assertIn('database="travel-sample"', body)


# ===================================================================
# Metrics config validation
# ===================================================================

class TestMetricsConfigValidation(unittest.TestCase):

    def test_metrics_disabled_no_validation(self):
        cfg = _base_config()
        cfg["metrics"] = {"enabled": False, "port": -1}
        _, _, errors = cw.validate_config(cfg)
        # port=-1 should not error when metrics is disabled
        port_errors = [e for e in errors if "metrics.port" in e]
        self.assertEqual(port_errors, [])

    def test_metrics_invalid_port(self):
        cfg = _base_config()
        cfg["metrics"] = {"enabled": True, "port": 99999}
        _, _, errors = cw.validate_config(cfg)
        self.assertTrue(any("metrics.port" in e for e in errors))

    def test_metrics_valid_port(self):
        cfg = _base_config()
        cfg["metrics"] = {"enabled": True, "port": 9090}
        _, _, errors = cw.validate_config(cfg)
        port_errors = [e for e in errors if "metrics.port" in e]
        self.assertEqual(port_errors, [])


# ===================================================================
# Metrics server endpoint
# ===================================================================

class TestMetricsServer(unittest.TestCase):

    def test_metrics_endpoint(self):
        async def _run():
            m = cw.MetricsCollector("sync_gateway", "db")
            m.inc("poll_cycles_total", 7)
            runner = await cw.start_metrics_server(m, "127.0.0.1", 19090)
            try:
                async with aiohttp.ClientSession() as session:
                    resp = await session.get("http://127.0.0.1:19090/_metrics")
                    body = await resp.text()
                    self.assertEqual(resp.status, 200)
                    self.assertIn("changes_worker_poll_cycles_total", body)
                    self.assertIn("} 7", body)

                    # Also test /metrics alias
                    resp2 = await session.get("http://127.0.0.1:19090/metrics")
                    self.assertEqual(resp2.status, 200)
            finally:
                await runner.cleanup()

        asyncio.run(_run())



# ===================================================================
# Continuous feed helpers
# ===================================================================

class TestBuildChangesParams(unittest.TestCase):
    """Tests for _build_changes_params()."""

    def test_basic_params(self):
        feed_cfg = {"heartbeat_ms": 30000}
        params = cw._build_changes_params(feed_cfg, "sync_gateway", "100", "longpoll", 60000)
        self.assertEqual(params["feed"], "longpoll")
        self.assertEqual(params["since"], "100")
        self.assertEqual(params["heartbeat"], "30000")
        self.assertEqual(params["timeout"], "60000")
        self.assertEqual(params["version_type"], "rev")

    def test_with_limit(self):
        feed_cfg = {"heartbeat_ms": 30000}
        params = cw._build_changes_params(feed_cfg, "sync_gateway", "0", "normal", 60000, limit=500)
        self.assertEqual(params["limit"], "500")

    def test_no_limit_when_zero(self):
        feed_cfg = {"heartbeat_ms": 30000}
        params = cw._build_changes_params(feed_cfg, "sync_gateway", "0", "normal", 60000, limit=0)
        self.assertNotIn("limit", params)

    def test_edge_server_no_version_type(self):
        feed_cfg = {"heartbeat_ms": 30000}
        params = cw._build_changes_params(feed_cfg, "edge_server", "0", "longpoll", 60000)
        self.assertNotIn("version_type", params)

    def test_channels_filter(self):
        feed_cfg = {"heartbeat_ms": 30000, "channels": ["ch1", "ch2"]}
        params = cw._build_changes_params(feed_cfg, "sync_gateway", "0", "normal", 60000)
        self.assertEqual(params["filter"], "sync_gateway/bychannel")
        self.assertEqual(params["channels"], "ch1,ch2")

    def test_include_docs_and_active_only(self):
        feed_cfg = {"heartbeat_ms": 30000, "include_docs": True, "active_only": True}
        params = cw._build_changes_params(feed_cfg, "sync_gateway", "0", "normal", 60000)
        self.assertEqual(params["include_docs"], "true")
        self.assertEqual(params["active_only"], "true")


class TestSleepWithBackoff(unittest.TestCase):
    """Tests for _sleep_with_backoff()."""

    def test_exponential_delay(self):
        async def _run():
            event = asyncio.Event()
            # failure_count=1 → delay = min(1 * 2^0, 60) = 1s
            # We just verify it returns (event not set, so it times out)
            import time
            start = time.monotonic()
            await cw._sleep_with_backoff(
                {"backoff_base_seconds": 0.01, "backoff_max_seconds": 1},
                1, event
            )
            elapsed = time.monotonic() - start
            self.assertGreaterEqual(elapsed, 0.005)

        asyncio.run(_run())

    def test_respects_max(self):
        async def _run():
            event = asyncio.Event()
            import time
            start = time.monotonic()
            # failure_count=10 → delay = min(0.01 * 2^9, 0.05) = 0.05
            await cw._sleep_with_backoff(
                {"backoff_base_seconds": 0.01, "backoff_max_seconds": 0.05},
                10, event
            )
            elapsed = time.monotonic() - start
            self.assertGreaterEqual(elapsed, 0.04)
            self.assertLess(elapsed, 0.5)

        asyncio.run(_run())

    def test_returns_immediately_if_shutdown(self):
        async def _run():
            event = asyncio.Event()
            event.set()
            await cw._sleep_with_backoff(
                {"backoff_base_seconds": 10, "backoff_max_seconds": 60},
                1, event
            )

        asyncio.run(_run())


# ===================================================================
# validate_config – new HTTP output fields
# ===================================================================

class TestValidateConfigHTTPOutputFields(unittest.TestCase):
    """Tests for config validation of the new HTTP output settings."""

    def _http_cfg(self, **output_overrides):
        cfg = _base_config()
        cfg["output"] = {
            "mode": "http",
            "target_url": "http://example.com/api",
            "output_format": "json",
            **output_overrides,
        }
        return cfg

    def test_valid_write_method_put(self):
        _, _, errors = cw.validate_config(self._http_cfg(write_method="PUT"))
        self.assertFalse(any("write_method" in e for e in errors))

    def test_valid_write_method_post(self):
        _, _, errors = cw.validate_config(self._http_cfg(write_method="POST"))
        self.assertFalse(any("write_method" in e for e in errors))

    def test_valid_write_method_patch(self):
        _, _, errors = cw.validate_config(self._http_cfg(write_method="PATCH"))
        self.assertFalse(any("write_method" in e for e in errors))

    def test_invalid_write_method(self):
        _, _, errors = cw.validate_config(self._http_cfg(write_method="OPTIONS"))
        self.assertTrue(any("write_method" in e for e in errors))

    def test_invalid_delete_method(self):
        _, _, errors = cw.validate_config(self._http_cfg(delete_method="OPTIONS"))
        self.assertTrue(any("delete_method" in e for e in errors))

    def test_write_method_case_insensitive(self):
        _, _, errors = cw.validate_config(self._http_cfg(write_method="post"))
        self.assertFalse(any("write_method" in e for e in errors))

    def test_request_timeout_zero_errors(self):
        _, _, errors = cw.validate_config(self._http_cfg(request_timeout_seconds=0))
        self.assertTrue(any("request_timeout" in e for e in errors))

    def test_request_timeout_negative_errors(self):
        _, _, errors = cw.validate_config(self._http_cfg(request_timeout_seconds=-5))
        self.assertTrue(any("request_timeout" in e for e in errors))

    def test_request_timeout_valid(self):
        _, _, errors = cw.validate_config(self._http_cfg(request_timeout_seconds=30))
        self.assertFalse(any("request_timeout" in e for e in errors))

    def test_db_mode_valid(self):
        cfg = _base_config()
        cfg["output"] = {"mode": "db", "output_format": "json"}
        _, _, errors = cw.validate_config(cfg)
        mode_errors = [e for e in errors if "output.mode" in e]
        self.assertEqual(mode_errors, [])


# ===================================================================
# OutputForwarder – new HTTP config fields
# ===================================================================

def _http_out_cfg(**overrides):
    """Minimal HTTP output config for testing."""
    cfg = {
        "mode": "http",
        "target_url": "http://example.com/api",
        "output_format": "json",
        "target_auth": {"method": "none"},
        "retry": {
            "max_retries": 1,
            "backoff_base_seconds": 0,
            "backoff_max_seconds": 0,
            "retry_on_status": [],
        },
        "halt_on_failure": True,
    }
    cfg.update(overrides)
    return cfg


class TestOutputForwarderURLTemplate(unittest.TestCase):
    """Tests for url_template and URL encoding of doc_id."""

    def _make_fwd(self, **cfg_overrides):
        session = MagicMock()
        cfg = _http_out_cfg(**cfg_overrides)
        fwd = cw.OutputForwarder(session, cfg, dry_run=False)
        mock_http = MagicMock()
        resp = AsyncMock()
        resp.status = 200
        resp.release = MagicMock()
        mock_http.request = AsyncMock(return_value=resp)
        fwd._http = mock_http
        return fwd, mock_http

    def test_default_template_appends_doc_id(self):
        fwd, mock = self._make_fwd()
        asyncio.run(fwd.send({"_id": "doc123"}, "PUT"))
        url = mock.request.call_args[0][1]
        self.assertEqual(url, "http://example.com/api/doc123")

    def test_custom_template_no_doc_id(self):
        fwd, mock = self._make_fwd(url_template="{target_url}")
        asyncio.run(fwd.send({"_id": "doc123"}, "POST"))
        url = mock.request.call_args[0][1]
        self.assertEqual(url, "http://example.com/api")

    def test_custom_template_nested_path(self):
        fwd, mock = self._make_fwd(url_template="{target_url}/items/{doc_id}/sync")
        asyncio.run(fwd.send({"_id": "abc"}, "PUT"))
        url = mock.request.call_args[0][1]
        self.assertEqual(url, "http://example.com/api/items/abc/sync")

    def test_doc_id_with_slash_is_encoded(self):
        fwd, mock = self._make_fwd()
        asyncio.run(fwd.send({"_id": "ns/doc1"}, "PUT"))
        url = mock.request.call_args[0][1]
        self.assertIn("ns%2Fdoc1", url)
        self.assertNotIn("ns/doc1/", url.replace("example.com/api/", ""))

    def test_doc_id_with_special_chars_is_encoded(self):
        fwd, mock = self._make_fwd()
        asyncio.run(fwd.send({"_id": "a b?c#d"}, "PUT"))
        url = mock.request.call_args[0][1]
        self.assertNotIn(" ", url)
        self.assertNotIn("?c", url.split("/")[-1])


class TestOutputForwarderDeleteBody(unittest.TestCase):
    """Tests for send_delete_body behaviour."""

    def _make_fwd(self, send_delete_body=False):
        session = MagicMock()
        cfg = _http_out_cfg(send_delete_body=send_delete_body)
        fwd = cw.OutputForwarder(session, cfg, dry_run=False)
        mock_http = MagicMock()
        resp = AsyncMock()
        resp.status = 200
        resp.release = MagicMock()
        mock_http.request = AsyncMock(return_value=resp)
        fwd._http = mock_http
        return fwd, mock_http

    def test_delete_omits_body_by_default(self):
        fwd, mock = self._make_fwd(send_delete_body=False)
        asyncio.run(fwd.send({"_id": "doc1", "val": 1}, "DELETE"))
        kwargs = mock.request.call_args[1]
        self.assertNotIn("data", kwargs)

    def test_delete_sends_body_when_enabled(self):
        fwd, mock = self._make_fwd(send_delete_body=True)
        asyncio.run(fwd.send({"_id": "doc1", "val": 1}, "DELETE"))
        kwargs = mock.request.call_args[1]
        self.assertIn("data", kwargs)

    def test_put_always_sends_body(self):
        fwd, mock = self._make_fwd(send_delete_body=False)
        asyncio.run(fwd.send({"_id": "doc1", "val": 1}, "PUT"))
        kwargs = mock.request.call_args[1]
        self.assertIn("data", kwargs)


class TestOutputForwarderTimeout(unittest.TestCase):
    """Tests for request_timeout_seconds."""

    def test_timeout_passed_to_request(self):
        session = MagicMock()
        cfg = _http_out_cfg(request_timeout_seconds=42)
        fwd = cw.OutputForwarder(session, cfg, dry_run=False)
        mock_http = MagicMock()
        resp = AsyncMock()
        resp.status = 200
        resp.release = MagicMock()
        mock_http.request = AsyncMock(return_value=resp)
        fwd._http = mock_http

        asyncio.run(fwd.send({"_id": "doc1"}, "PUT"))

        kwargs = mock_http.request.call_args[1]
        timeout = kwargs["timeout"]
        self.assertIsInstance(timeout, aiohttp.ClientTimeout)
        self.assertEqual(timeout.total, 42)

    def test_default_timeout_30(self):
        session = MagicMock()
        fwd = cw.OutputForwarder(session, _http_out_cfg(), dry_run=False)
        self.assertEqual(fwd._request_timeout, 30)


class TestOutputForwarderRedirects(unittest.TestCase):
    """Tests for follow_redirects config."""

    def test_default_no_follow(self):
        session = MagicMock()
        fwd = cw.OutputForwarder(session, _http_out_cfg(), dry_run=False)
        self.assertFalse(fwd._follow_redirects)

    def test_follow_redirects_passed_to_request(self):
        session = MagicMock()
        cfg = _http_out_cfg(follow_redirects=True)
        fwd = cw.OutputForwarder(session, cfg, dry_run=False)
        mock_http = MagicMock()
        resp = AsyncMock()
        resp.status = 200
        resp.release = MagicMock()
        mock_http.request = AsyncMock(return_value=resp)
        fwd._http = mock_http

        asyncio.run(fwd.send({"_id": "doc1"}, "PUT"))

        kwargs = mock_http.request.call_args[1]
        self.assertTrue(kwargs["allow_redirects"])


class TestOutputForwarderSSL(unittest.TestCase):
    """Tests for output-specific accept_self_signed_certs."""

    def test_no_ssl_by_default(self):
        session = MagicMock()
        fwd = cw.OutputForwarder(session, _http_out_cfg(), dry_run=False)
        self.assertIsNone(fwd._ssl_ctx)

    def test_ssl_context_created_when_enabled(self):
        import ssl
        session = MagicMock()
        cfg = _http_out_cfg(accept_self_signed_certs=True)
        fwd = cw.OutputForwarder(session, cfg, dry_run=False)
        self.assertIsNotNone(fwd._ssl_ctx)
        self.assertFalse(fwd._ssl_ctx.check_hostname)
        self.assertEqual(fwd._ssl_ctx.verify_mode, ssl.CERT_NONE)

    def test_ssl_ctx_passed_to_request(self):
        session = MagicMock()
        cfg = _http_out_cfg(accept_self_signed_certs=True)
        fwd = cw.OutputForwarder(session, cfg, dry_run=False)
        mock_http = MagicMock()
        resp = AsyncMock()
        resp.status = 200
        resp.release = MagicMock()
        mock_http.request = AsyncMock(return_value=resp)
        fwd._http = mock_http

        asyncio.run(fwd.send({"_id": "doc1"}, "PUT"))

        kwargs = mock_http.request.call_args[1]
        self.assertIs(kwargs["ssl"], fwd._ssl_ctx)


class TestOutputForwarderHealthCheck(unittest.TestCase):
    """Tests for health_check config and heartbeat lifecycle."""

    def test_health_check_defaults(self):
        session = MagicMock()
        fwd = cw.OutputForwarder(session, _http_out_cfg(), dry_run=False)
        self.assertFalse(fwd._hc_enabled)
        self.assertEqual(fwd._hc_interval, 30)
        self.assertEqual(fwd._hc_method, "GET")
        self.assertEqual(fwd._hc_timeout, 5)
        self.assertEqual(fwd._hc_url, "http://example.com/api")

    def test_health_check_custom_url(self):
        session = MagicMock()
        cfg = _http_out_cfg(health_check={
            "enabled": True,
            "interval_seconds": 15,
            "url": "http://example.com/health",
            "method": "HEAD",
            "timeout_seconds": 3,
        })
        fwd = cw.OutputForwarder(session, cfg, dry_run=False)
        self.assertTrue(fwd._hc_enabled)
        self.assertEqual(fwd._hc_interval, 15)
        self.assertEqual(fwd._hc_url, "http://example.com/health")
        self.assertEqual(fwd._hc_method, "HEAD")
        self.assertEqual(fwd._hc_timeout, 3)

    def test_start_heartbeat_noop_when_disabled(self):
        session = MagicMock()
        fwd = cw.OutputForwarder(session, _http_out_cfg(), dry_run=False)

        async def _run():
            event = asyncio.Event()
            await fwd.start_heartbeat(event)
            self.assertIsNone(fwd._hc_task)
        asyncio.run(_run())

    def test_start_heartbeat_noop_for_stdout(self):
        session = MagicMock()
        fwd = cw.OutputForwarder(
            session,
            _stdout_out_cfg(health_check={"enabled": True, "interval_seconds": 1}),
            dry_run=False,
        )

        async def _run():
            event = asyncio.Event()
            await fwd.start_heartbeat(event)
            self.assertIsNone(fwd._hc_task)
        asyncio.run(_run())

    def test_start_and_stop_heartbeat(self):
        session = MagicMock()
        cfg = _http_out_cfg(health_check={
            "enabled": True,
            "interval_seconds": 60,
            "url": "",
            "method": "GET",
            "timeout_seconds": 5,
        })
        fwd = cw.OutputForwarder(session, cfg, dry_run=False)
        # Mock _http so heartbeat probe doesn't fail
        mock_http = MagicMock()
        resp = AsyncMock()
        resp.status = 200
        resp.release = MagicMock()
        mock_http.request = AsyncMock(return_value=resp)
        fwd._http = mock_http

        async def _run():
            event = asyncio.Event()
            await fwd.start_heartbeat(event)
            self.assertIsNotNone(fwd._hc_task)
            self.assertFalse(fwd._hc_task.done())
            await fwd.stop_heartbeat()
            self.assertTrue(fwd._hc_task.done())
        asyncio.run(_run())

    def test_health_check_returns_true_on_success(self):
        session = MagicMock()
        cfg = _http_out_cfg(health_check={
            "enabled": True, "interval_seconds": 30,
            "url": "http://example.com/health",
            "method": "GET", "timeout_seconds": 5,
        })
        fwd = cw.OutputForwarder(session, cfg, dry_run=False)
        mock_http = MagicMock()
        resp = AsyncMock()
        resp.status = 200
        resp.release = MagicMock()
        mock_http.request = AsyncMock(return_value=resp)
        fwd._http = mock_http

        result = asyncio.run(fwd._health_check())
        self.assertTrue(result)

    def test_health_check_returns_false_on_5xx(self):
        session = MagicMock()
        cfg = _http_out_cfg(health_check={
            "enabled": True, "interval_seconds": 30,
            "url": "http://example.com/health",
            "method": "GET", "timeout_seconds": 5,
        })
        fwd = cw.OutputForwarder(session, cfg, dry_run=False)
        mock_http = MagicMock()
        resp = AsyncMock()
        resp.status = 503
        resp.release = MagicMock()
        mock_http.request = AsyncMock(return_value=resp)
        fwd._http = mock_http

        result = asyncio.run(fwd._health_check())
        self.assertFalse(result)

    def test_health_check_returns_false_on_exception(self):
        session = MagicMock()
        cfg = _http_out_cfg(health_check={
            "enabled": True, "interval_seconds": 30,
            "url": "http://example.com/health",
            "method": "GET", "timeout_seconds": 5,
        })
        fwd = cw.OutputForwarder(session, cfg, dry_run=False)
        mock_http = MagicMock()
        mock_http.request = AsyncMock(side_effect=ConnectionError("refused"))
        fwd._http = mock_http

        result = asyncio.run(fwd._health_check())
        self.assertFalse(result)


class TestOutputForwarderEndpointUpRecovery(unittest.TestCase):
    """Tests that output_endpoint_up is set back to 1 on success."""

    def test_metric_recovers_after_success(self):
        metrics = cw.MetricsCollector("sync_gateway", "db")
        metrics.set("output_endpoint_up", 0)

        session = MagicMock()
        cfg = _http_out_cfg()
        fwd = cw.OutputForwarder(session, cfg, dry_run=False, metrics=metrics)
        mock_http = MagicMock()
        resp = AsyncMock()
        resp.status = 200
        resp.release = MagicMock()
        mock_http.request = AsyncMock(return_value=resp)
        fwd._http = mock_http

        asyncio.run(fwd.send({"_id": "doc1"}, "PUT"))
        self.assertEqual(metrics.output_endpoint_up, 1)


class TestOutputForwarderConfigDefaults(unittest.TestCase):
    """Tests that new config fields have correct defaults."""

    def test_defaults(self):
        session = MagicMock()
        fwd = cw.OutputForwarder(session, _http_out_cfg(), dry_run=False)
        self.assertEqual(fwd._url_template, "{target_url}/{doc_id}")
        self.assertEqual(fwd._write_method, "PUT")
        self.assertEqual(fwd._delete_method, "DELETE")
        self.assertFalse(fwd._send_delete_body)
        self.assertEqual(fwd._request_timeout, 30)
        self.assertFalse(fwd._follow_redirects)
        self.assertIsNone(fwd._ssl_ctx)

    def test_custom_methods_stored(self):
        session = MagicMock()
        cfg = _http_out_cfg(write_method="POST", delete_method="PUT")
        fwd = cw.OutputForwarder(session, cfg, dry_run=False)
        self.assertEqual(fwd._write_method, "POST")
        self.assertEqual(fwd._delete_method, "PUT")


class TestRespTimesDeque(unittest.TestCase):
    """Tests that response time tracking uses bounded deque."""

    def test_forwarder_deque_capped(self):
        from collections import deque
        session = MagicMock()
        fwd = cw.OutputForwarder(session, _stdout_out_cfg(log_response_times=True), dry_run=False)
        self.assertIsInstance(fwd._resp_times, deque)
        self.assertEqual(fwd._resp_times.maxlen, 10000)

    def test_metrics_deque_capped(self):
        from collections import deque
        m = cw.MetricsCollector("sync_gateway", "db")
        self.assertIsInstance(m._output_resp_times, deque)
        self.assertEqual(m._output_resp_times.maxlen, 10000)


# ===================================================================
# _consume_websocket_stream
# ===================================================================

def _ws_msg(msg_type, data=None):
    """Create a mock WebSocket message."""
    m = MagicMock()
    m.type = msg_type
    m.data = data
    return m


def _make_ws_params(**overrides):
    """Return minimal kwargs dict for _consume_websocket_stream."""
    shutdown = asyncio.Event()
    params = dict(
        since="0",
        changes_url="http://localhost:4984/db/_changes",
        feed_cfg={"include_docs": True, "heartbeat_ms": 30000},
        proc_cfg={},
        retry_cfg={"backoff_base_seconds": 0.01, "backoff_max_seconds": 0.05},
        src="sync_gateway",
        http=MagicMock(),
        session=MagicMock(),
        basic_auth=None,
        auth_headers={},
        base_url="http://localhost:4984/db",
        output=MagicMock(),
        dlq=MagicMock(),
        checkpoint=MagicMock(),
        semaphore=asyncio.Semaphore(5),
        shutdown_event=shutdown,
        metrics=None,
        every_n_docs=100,
        max_concurrent=5,
        timeout_ms=60000,
    )
    params.update(overrides)
    return params


class TestConsumeWebsocketStream(unittest.TestCase):
    """Tests for _consume_websocket_stream()."""

    # ---- helpers ----

    def _make_mock_ws(self, messages):
        """Return a mock WebSocket whose receive() yields *messages* in order."""
        ws = AsyncMock()
        ws.receive = AsyncMock(side_effect=messages)
        ws.send_json = AsyncMock()
        ws.closed = False
        ws.close = AsyncMock()
        return ws

    # ---- tests ----

    @patch("main._sleep_with_backoff", new_callable=AsyncMock)
    @patch("main._process_changes_batch", new_callable=AsyncMock)
    def test_websocket_processes_single_dict_message(self, mock_batch, mock_sleep):
        """Single dict change row followed by last_seq → calls _process_changes_batch."""
        change_msg = _ws_msg(
            aiohttp.WSMsgType.TEXT,
            json.dumps({"seq": "50", "id": "doc1", "changes": [{"rev": "1-abc"}]}),
        )
        last_seq_msg = _ws_msg(
            aiohttp.WSMsgType.TEXT,
            json.dumps({"last_seq": "50"}),
        )

        mock_batch.return_value = ("50", False)

        ws = self._make_mock_ws([change_msg, last_seq_msg])
        params = _make_ws_params()
        call_count = 0

        async def _connect(*a, **kw):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return ws
            params["shutdown_event"].set()
            return self._make_mock_ws([])

        params["session"].ws_connect = AsyncMock(side_effect=_connect)

        result = asyncio.run(cw._consume_websocket_stream(**params))

        mock_batch.assert_called_once()
        args = mock_batch.call_args
        self.assertEqual(args[0][0], [{"seq": "50", "id": "doc1", "changes": [{"rev": "1-abc"}]}])
        self.assertEqual(args[0][1], "50")  # last_seq
        self.assertEqual(result, "50")

    @patch("main._sleep_with_backoff", new_callable=AsyncMock)
    @patch("main._process_changes_batch", new_callable=AsyncMock)
    def test_websocket_processes_array_message(self, mock_batch, mock_sleep):
        """Array of change rows → _process_changes_batch gets both rows."""
        rows = [
            {"seq": "50", "id": "doc1", "changes": [{"rev": "1-abc"}]},
            {"seq": "51", "id": "doc2", "changes": [{"rev": "1-def"}]},
        ]
        change_msg = _ws_msg(aiohttp.WSMsgType.TEXT, json.dumps(rows))
        last_seq_msg = _ws_msg(aiohttp.WSMsgType.TEXT, json.dumps({"last_seq": "51"}))

        mock_batch.return_value = ("51", False)

        ws = self._make_mock_ws([change_msg, last_seq_msg])

        params = _make_ws_params()
        call_count = 0

        async def _connect(*a, **kw):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                params["shutdown_event"].set()
                return self._make_mock_ws([])
            return ws

        params["session"].ws_connect = AsyncMock(side_effect=_connect)

        result = asyncio.run(cw._consume_websocket_stream(**params))

        mock_batch.assert_called_once()
        args = mock_batch.call_args
        self.assertEqual(args[0][0], rows)
        self.assertEqual(args[0][1], "51")
        self.assertEqual(result, "51")

    @patch("main._sleep_with_backoff", new_callable=AsyncMock)
    @patch("main._process_changes_batch", new_callable=AsyncMock)
    def test_websocket_last_seq_ends_loop(self, mock_batch, mock_sleep):
        """A last_seq-only message returns since without calling _process_changes_batch."""
        last_seq_msg = _ws_msg(aiohttp.WSMsgType.TEXT, json.dumps({"last_seq": "99"}))

        ws = self._make_mock_ws([last_seq_msg])

        params = _make_ws_params()
        call_count = 0

        async def _connect(*a, **kw):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                params["shutdown_event"].set()
                return self._make_mock_ws([])
            return ws

        params["session"].ws_connect = AsyncMock(side_effect=_connect)

        result = asyncio.run(cw._consume_websocket_stream(**params))

        mock_batch.assert_not_called()
        self.assertEqual(result, "99")

    @patch("main._sleep_with_backoff", new_callable=AsyncMock)
    @patch("main._process_changes_batch", new_callable=AsyncMock)
    def test_websocket_reconnects_on_close(self, mock_batch, mock_sleep):
        """CLOSED on first connection → reconnect; second has change + last_seq."""
        ws1 = self._make_mock_ws([_ws_msg(aiohttp.WSMsgType.CLOSED)])

        change_msg = _ws_msg(
            aiohttp.WSMsgType.TEXT,
            json.dumps({"seq": "10", "id": "d1", "changes": [{"rev": "1-x"}]}),
        )
        last_seq_msg = _ws_msg(aiohttp.WSMsgType.TEXT, json.dumps({"last_seq": "10"}))
        ws2 = self._make_mock_ws([change_msg, last_seq_msg])

        mock_batch.return_value = ("10", False)

        params = _make_ws_params()
        call_count = 0

        async def _connect(*a, **kw):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return ws1
            if call_count == 2:
                return ws2
            params["shutdown_event"].set()
            return self._make_mock_ws([])

        params["session"].ws_connect = AsyncMock(side_effect=_connect)

        result = asyncio.run(cw._consume_websocket_stream(**params))

        self.assertGreaterEqual(params["session"].ws_connect.call_count, 2)
        self.assertEqual(result, "10")

    @patch("main._sleep_with_backoff", new_callable=AsyncMock)
    @patch("main._process_changes_batch", new_callable=AsyncMock)
    def test_websocket_connect_failure_retries(self, mock_batch, mock_sleep):
        """First ws_connect raises ClientError, second succeeds with last_seq."""
        last_seq_msg = _ws_msg(aiohttp.WSMsgType.TEXT, json.dumps({"last_seq": "5"}))
        ws = self._make_mock_ws([last_seq_msg])

        params = _make_ws_params()
        call_count = 0

        async def _connect(*a, **kw):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise aiohttp.ClientError("connection refused")
            if call_count == 2:
                return ws
            params["shutdown_event"].set()
            return self._make_mock_ws([])

        params["session"].ws_connect = AsyncMock(side_effect=_connect)

        result = asyncio.run(cw._consume_websocket_stream(**params))

        self.assertEqual(result, "5")
        mock_sleep.assert_called()
        self.assertGreaterEqual(params["session"].ws_connect.call_count, 2)


# ===================================================================
# ShutdownRequested exception
# ===================================================================

class TestShutdownRequested(unittest.TestCase):

    def test_is_exception(self):
        exc = cw.ShutdownRequested("test shutdown")
        self.assertIsInstance(exc, Exception)
        self.assertIn("test shutdown", str(exc))


# ===================================================================
# RetryableHTTP – shutdown-aware behaviour
# ===================================================================

class TestRetryableHTTPShutdown(unittest.TestCase):

    def test_raises_shutdown_before_first_attempt(self):
        """If shutdown_event is already set, raises ShutdownRequested immediately."""
        session = MagicMock()
        session.request = AsyncMock()

        http = cw.RetryableHTTP(session, {"max_retries": 3, "backoff_base_seconds": 0, "backoff_max_seconds": 0})
        event = asyncio.Event()
        event.set()
        http.set_shutdown_event(event)

        with self.assertRaises(cw.ShutdownRequested):
            asyncio.run(http.request("GET", "http://example.com"))
        session.request.assert_not_called()

    def test_shutdown_via_kwarg(self):
        """shutdown_event can be passed as kwarg."""
        session = MagicMock()
        session.request = AsyncMock()

        http = cw.RetryableHTTP(session, {"max_retries": 3, "backoff_base_seconds": 0, "backoff_max_seconds": 0})
        event = asyncio.Event()
        event.set()

        with self.assertRaises(cw.ShutdownRequested):
            asyncio.run(http.request("GET", "http://example.com", shutdown_event=event))

    def test_shutdown_during_backoff_aborts(self):
        """If shutdown_event fires during backoff sleep, raises ShutdownRequested."""
        session = MagicMock()
        resp_fail = AsyncMock()
        resp_fail.status = 503
        resp_fail.text = AsyncMock(return_value="Service Unavailable")
        resp_fail.release = MagicMock()
        session.request = AsyncMock(return_value=resp_fail)

        http = cw.RetryableHTTP(session, {
            "max_retries": 5,
            "backoff_base_seconds": 100,  # very long sleep
            "backoff_max_seconds": 100,
            "retry_on_status": [503],
        })

        async def _run():
            event = asyncio.Event()
            http.set_shutdown_event(event)
            # Set shutdown after a tiny delay

            async def _set_later():
                await asyncio.sleep(0.05)
                event.set()
            asyncio.create_task(_set_later())
            await http.request("GET", "http://example.com")

        with self.assertRaises(cw.ShutdownRequested):
            asyncio.run(_run())

    def test_no_backoff_after_last_attempt(self):
        """After the last attempt, no backoff sleep should happen."""
        session = MagicMock()
        resp_fail = AsyncMock()
        resp_fail.status = 500
        resp_fail.text = AsyncMock(return_value="error")
        resp_fail.release = MagicMock()
        session.request = AsyncMock(return_value=resp_fail)

        http = cw.RetryableHTTP(session, {
            "max_retries": 2,
            "backoff_base_seconds": 0,
            "backoff_max_seconds": 0,
            "retry_on_status": [500],
        })

        import time as _time
        t0 = _time.monotonic()
        with self.assertRaises(ConnectionError):
            asyncio.run(http.request("GET", "http://example.com"))
        elapsed = _time.monotonic() - t0
        # Should be very fast – no 60s sleep after last attempt
        self.assertLess(elapsed, 2.0)
        self.assertEqual(session.request.call_count, 2)

    def test_set_shutdown_event_method(self):
        session = MagicMock()
        http = cw.RetryableHTTP(session, {"max_retries": 1})
        self.assertIsNone(http._shutdown_event)
        event = asyncio.Event()
        http.set_shutdown_event(event)
        self.assertIs(http._shutdown_event, event)


# ===================================================================
# Shutdown handler
# ===================================================================

class TestShutdownHandler(unittest.TestCase):

    def _make_app(self, **kwargs):
        """Build a minimal app dict mimic for _shutdown_handler."""
        from aiohttp.web import Application
        app = Application()
        app["metrics"] = kwargs.get("metrics")
        app["shutdown_event"] = kwargs.get("shutdown_event", asyncio.Event())
        app["shutdown_cfg"] = kwargs.get("shutdown_cfg", {})
        return app

    def test_shutdown_sets_event_and_returns_ok(self):
        async def _run():
            shutdown_event = asyncio.Event()
            metrics = cw.MetricsCollector("sg", "db")
            # No active tasks → drains immediately

            request = MagicMock()
            request.app = {
                "shutdown_event": shutdown_event,
                "metrics": metrics,
                "shutdown_cfg": {"drain_timeout_seconds": 1},
            }

            resp = await cw._shutdown_handler(request)
            body = json.loads(resp.body)

            self.assertTrue(shutdown_event.is_set())
            self.assertTrue(body["ok"])
            self.assertTrue(body["drained"])
            self.assertIn("shutdown complete", body["message"])

        asyncio.run(_run())

    def test_shutdown_drain_timeout(self):
        async def _run():
            shutdown_event = asyncio.Event()
            metrics = cw.MetricsCollector("sg", "db")
            metrics.active_tasks = 5  # simulate stuck tasks

            request = MagicMock()
            request.app = {
                "shutdown_event": shutdown_event,
                "metrics": metrics,
                "shutdown_cfg": {"drain_timeout_seconds": 0.1, "dlq_inflight_on_shutdown": True},
            }

            resp = await cw._shutdown_handler(request)
            body = json.loads(resp.body)

            self.assertTrue(body["ok"])
            self.assertFalse(body["drained"])
            self.assertEqual(body["tasks_remaining"], 5)
            self.assertIn("dead-letter queue", body["message"])

        asyncio.run(_run())

    def test_shutdown_no_dlq_policy_message(self):
        async def _run():
            shutdown_event = asyncio.Event()
            metrics = cw.MetricsCollector("sg", "db")
            metrics.active_tasks = 2

            request = MagicMock()
            request.app = {
                "shutdown_event": shutdown_event,
                "metrics": metrics,
                "shutdown_cfg": {"drain_timeout_seconds": 0.1, "dlq_inflight_on_shutdown": False},
            }

            resp = await cw._shutdown_handler(request)
            body = json.loads(resp.body)

            self.assertFalse(body["drained"])
            self.assertIn("re-fetched on next startup", body["message"])

        asyncio.run(_run())

    def test_shutdown_no_event_returns_500(self):
        async def _run():
            request = MagicMock()
            request.app = {}

            resp = await cw._shutdown_handler(request)
            self.assertEqual(resp.status, 500)

        asyncio.run(_run())

    def test_shutdown_does_not_close_cbl(self):
        """The handler should NOT close CBL — main()'s finally does that."""
        async def _run():
            shutdown_event = asyncio.Event()
            metrics = cw.MetricsCollector("sg", "db")

            request = MagicMock()
            request.app = {
                "shutdown_event": shutdown_event,
                "metrics": metrics,
                "shutdown_cfg": {},
            }

            with patch("main.close_db") as mock_close:
                await cw._shutdown_handler(request)
                mock_close.assert_not_called()

        asyncio.run(_run())


# ===================================================================
# DeadLetterQueue – purge, list_pending, get_entry_doc
# ===================================================================

class TestDeadLetterQueuePurge(unittest.TestCase):

    def test_purge_cbl_calls_delete(self):
        """purge() calls store.delete_dlq_entry when CBL is available."""
        async def _run():
            dlq = cw.DeadLetterQueue("")
            dlq._use_cbl = True
            mock_store = MagicMock()
            dlq._store = mock_store

            await dlq.purge("dlq:doc1:12345")
            mock_store.delete_dlq_entry.assert_called_once_with("dlq:doc1:12345")

        asyncio.run(_run())

    def test_purge_file_noop(self):
        """purge() is a no-op for file-based DLQ (just logs)."""
        async def _run():
            dlq = cw.DeadLetterQueue("test.jsonl")
            # Should not raise
            await dlq.purge("dlq:doc1:12345")

        asyncio.run(_run())


class TestDeadLetterQueueListPending(unittest.TestCase):

    def test_list_pending_cbl(self):
        dlq = cw.DeadLetterQueue("")
        dlq._use_cbl = True
        mock_store = MagicMock()
        mock_store.list_dlq.return_value = [
            {"id": "dlq:1", "retried": False},
            {"id": "dlq:2", "retried": True},
            {"id": "dlq:3", "retried": False},
        ]
        dlq._store = mock_store

        pending = dlq.list_pending()
        self.assertEqual(len(pending), 2)
        self.assertEqual(pending[0]["id"], "dlq:1")
        self.assertEqual(pending[1]["id"], "dlq:3")

    def test_list_pending_file(self):
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False, mode="w") as f:
            f.write(json.dumps({"doc_id": "doc1", "seq": "1"}) + "\n")
            f.write(json.dumps({"doc_id": "doc2", "seq": "2"}) + "\n")
            path = f.name
        try:
            dlq = cw.DeadLetterQueue(path)
            pending = dlq.list_pending()
            self.assertEqual(len(pending), 2)
        finally:
            os.unlink(path)

    def test_list_pending_empty(self):
        dlq = cw.DeadLetterQueue("")
        pending = dlq.list_pending()
        self.assertEqual(pending, [])

    def test_get_entry_doc_cbl(self):
        dlq = cw.DeadLetterQueue("")
        dlq._use_cbl = True
        mock_store = MagicMock()
        mock_store.get_dlq_entry.return_value = {
            "id": "dlq:doc1:123",
            "doc_data": {"_id": "doc1", "val": 42},
        }
        dlq._store = mock_store

        entry = dlq.get_entry_doc("dlq:doc1:123")
        self.assertEqual(entry["doc_data"]["_id"], "doc1")
        mock_store.get_dlq_entry.assert_called_once_with("dlq:doc1:123")

    def test_get_entry_doc_file_returns_none(self):
        dlq = cw.DeadLetterQueue("test.jsonl")
        self.assertIsNone(dlq.get_entry_doc("dlq:doc1:123"))


# ===================================================================
# DLQ replay
# ===================================================================

class TestReplayDeadLetterQueue(unittest.TestCase):

    def test_replay_no_pending(self):
        """When DLQ is empty, returns zeros."""
        async def _run():
            dlq = MagicMock()
            dlq.list_pending.return_value = []
            output = MagicMock()
            event = asyncio.Event()

            result = await cw._replay_dead_letter_queue(dlq, output, None, event)
            self.assertEqual(result, {"total": 0, "succeeded": 0, "failed": 0})

        asyncio.run(_run())

    def test_replay_success_purges(self):
        """Successful replay purges the DLQ entry."""
        async def _run():
            dlq = MagicMock()
            dlq.list_pending.return_value = [
                {"id": "dlq:doc1:100", "doc_id_original": "doc1", "method": "PUT"},
            ]
            dlq.get_entry_doc.return_value = {
                "id": "dlq:doc1:100",
                "doc_data": {"_id": "doc1", "val": 1},
            }
            dlq.purge = AsyncMock()

            output = MagicMock()
            output.send = AsyncMock(return_value={"ok": True, "status": 200})
            event = asyncio.Event()

            result = await cw._replay_dead_letter_queue(dlq, output, None, event)

            self.assertEqual(result["succeeded"], 1)
            self.assertEqual(result["failed"], 0)
            output.send.assert_called_once_with({"_id": "doc1", "val": 1}, "PUT")
            dlq.purge.assert_called_once_with("dlq:doc1:100")

        asyncio.run(_run())

    def test_replay_failure_keeps_entry(self):
        """Failed replay does NOT purge – entry stays for next startup."""
        async def _run():
            dlq = MagicMock()
            dlq.list_pending.return_value = [
                {"id": "dlq:doc1:100", "doc_id_original": "doc1", "method": "PUT"},
            ]
            dlq.get_entry_doc.return_value = {
                "id": "dlq:doc1:100",
                "doc_data": {"_id": "doc1"},
            }
            dlq.purge = AsyncMock()

            output = MagicMock()
            output.send = AsyncMock(return_value={"ok": False, "status": 500})
            event = asyncio.Event()

            result = await cw._replay_dead_letter_queue(dlq, output, None, event)

            self.assertEqual(result["succeeded"], 0)
            self.assertEqual(result["failed"], 1)
            dlq.purge.assert_not_called()

        asyncio.run(_run())

    def test_replay_exception_keeps_entry(self):
        """Exception during replay does NOT purge."""
        async def _run():
            dlq = MagicMock()
            dlq.list_pending.return_value = [
                {"id": "dlq:doc1:100", "doc_id_original": "doc1", "method": "PUT"},
            ]
            dlq.get_entry_doc.return_value = {
                "id": "dlq:doc1:100",
                "doc_data": {"_id": "doc1"},
            }
            dlq.purge = AsyncMock()

            output = MagicMock()
            output.send = AsyncMock(side_effect=ConnectionError("boom"))
            event = asyncio.Event()

            result = await cw._replay_dead_letter_queue(dlq, output, None, event)

            self.assertEqual(result["failed"], 1)
            dlq.purge.assert_not_called()

        asyncio.run(_run())

    def test_replay_stops_on_shutdown(self):
        """If shutdown_event is set, replay stops early."""
        async def _run():
            dlq = MagicMock()
            dlq.list_pending.return_value = [
                {"id": "dlq:doc1:100", "doc_id_original": "doc1", "method": "PUT"},
                {"id": "dlq:doc2:200", "doc_id_original": "doc2", "method": "PUT"},
            ]
            dlq.get_entry_doc.return_value = {"id": "dlq:doc1:100", "doc_data": {"_id": "doc1"}}
            dlq.purge = AsyncMock()

            output = MagicMock()
            output.send = AsyncMock(return_value={"ok": True})
            event = asyncio.Event()
            event.set()  # already shutting down

            result = await cw._replay_dead_letter_queue(dlq, output, None, event)

            self.assertEqual(result["total"], 2)
            output.send.assert_not_called()  # never got to send anything

        asyncio.run(_run())

    def test_replay_missing_entry_counts_as_failed(self):
        """If get_entry_doc returns None, counts as failed."""
        async def _run():
            dlq = MagicMock()
            dlq.list_pending.return_value = [
                {"id": "dlq:doc1:100", "doc_id_original": "doc1", "method": "PUT"},
            ]
            dlq.get_entry_doc.return_value = None
            dlq.purge = AsyncMock()

            output = MagicMock()
            output.send = AsyncMock()
            event = asyncio.Event()

            result = await cw._replay_dead_letter_queue(dlq, output, None, event)

            self.assertEqual(result["failed"], 1)
            output.send.assert_not_called()

        asyncio.run(_run())

    def test_replay_multiple_mixed_results(self):
        """Multiple entries: some succeed, some fail."""
        async def _run():
            dlq = MagicMock()
            dlq.list_pending.return_value = [
                {"id": "dlq:doc1:100", "doc_id_original": "doc1", "method": "PUT"},
                {"id": "dlq:doc2:200", "doc_id_original": "doc2", "method": "DELETE"},
                {"id": "dlq:doc3:300", "doc_id_original": "doc3", "method": "PUT"},
            ]
            dlq.get_entry_doc.side_effect = [
                {"id": "dlq:doc1:100", "doc_data": {"_id": "doc1"}},
                {"id": "dlq:doc2:200", "doc_data": {"_id": "doc2"}},
                {"id": "dlq:doc3:300", "doc_data": {"_id": "doc3"}},
            ]
            dlq.purge = AsyncMock()

            output = MagicMock()
            output.send = AsyncMock(side_effect=[
                {"ok": True, "status": 200},
                {"ok": False, "status": 503},
                {"ok": True, "status": 201},
            ])
            event = asyncio.Event()

            result = await cw._replay_dead_letter_queue(dlq, output, None, event)

            self.assertEqual(result["total"], 3)
            self.assertEqual(result["succeeded"], 2)
            self.assertEqual(result["failed"], 1)
            self.assertEqual(dlq.purge.call_count, 2)
            # Verify correct methods were passed
            calls = output.send.call_args_list
            self.assertEqual(calls[1][0][1], "DELETE")  # doc2 used DELETE

        asyncio.run(_run())


# ===================================================================
# active_tasks accounting (try/finally)
# ===================================================================

class TestActiveTasksAccounting(unittest.TestCase):

    def test_active_tasks_decrements_on_success(self):
        """active_tasks should decrement via inc(-1) after successful process_one."""
        metrics = cw.MetricsCollector("sg", "db")
        self.assertEqual(metrics.active_tasks, 0)
        metrics.inc("active_tasks", 1)
        self.assertEqual(metrics.active_tasks, 1)
        metrics.inc("active_tasks", -1)
        self.assertEqual(metrics.active_tasks, 0)

    def test_active_tasks_can_go_negative_safely(self):
        """inc(-1) when already 0 goes negative (no crash)."""
        metrics = cw.MetricsCollector("sg", "db")
        metrics.inc("active_tasks", -1)
        self.assertEqual(metrics.active_tasks, -1)


if __name__ == "__main__":
    unittest.main()
