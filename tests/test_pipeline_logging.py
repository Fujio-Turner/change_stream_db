#!/usr/bin/env python3
"""
Unit tests for pipeline_logging.py

Covers:
  - Redactor: redact_string, redact_value, redact_dict at all levels
  - LogKeyLevelFilter: allow/reject based on keys and levels
  - infer_operation: DELETE/SELECT/INSERT/UPDATE logic
  - log_event: correct level and extra fields
  - RedactingFormatter: log_key prefix, extra fields, URL redaction
  - configure_logging: legacy mode and full SG-style mode
  - ManagedRotatingFileHandler: directory creation, cleanup
"""

import logging
import os
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

# Ensure the module under test is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pipeline_logging as pl


# ===================================================================
# Redactor
# ===================================================================

class TestRedactorRedactString(unittest.TestCase):
    """Tests for Redactor.redact_string()."""

    def test_none_level_returns_unchanged(self):
        r = pl.Redactor("none")
        s = "http://admin:secret@host:4984/db"
        self.assertEqual(r.redact_string(s), s)

    def test_partial_redacts_url_userinfo(self):
        r = pl.Redactor("partial")
        result = r.redact_string("http://admin:secret@host:4984/db")
        self.assertIn("<ud>***:***</ud>@", result)
        self.assertNotIn("admin", result)
        self.assertNotIn("secret", result)

    def test_full_redacts_url_userinfo(self):
        r = pl.Redactor("full")
        result = r.redact_string("https://user:pass@host/db")
        self.assertIn("<ud>XXXXX</ud>@", result)
        self.assertNotIn("user", result)
        self.assertNotIn("pass@", result)

    def test_partial_redacts_bearer_token(self):
        r = pl.Redactor("partial")
        result = r.redact_string("Bearer abcdefghij1234")
        self.assertNotIn("abcdefghij1234", result)
        # Partial keeps last 4 chars visible
        self.assertIn("1234", result)

    def test_full_redacts_bearer_token(self):
        r = pl.Redactor("full")
        result = r.redact_string("Bearer my-super-secret-token")
        self.assertIn("<ud>XXXXX</ud>", result)
        self.assertNotIn("my-super-secret-token", result)

    def test_no_url_or_bearer_unchanged(self):
        r = pl.Redactor("partial")
        s = "Just a normal log message"
        self.assertEqual(r.redact_string(s), s)


class TestRedactorRedactValue(unittest.TestCase):
    """Tests for Redactor.redact_value()."""

    def test_non_sensitive_key_returns_unchanged(self):
        r = pl.Redactor("partial")
        self.assertEqual(r.redact_value("doc_id", "abc123"), "abc123")

    def test_sensitive_key_partial_redacts(self):
        r = pl.Redactor("partial")
        result = r.redact_value("password", "mysecret")
        self.assertIn("<ud>", result)
        self.assertTrue(result.startswith("<ud>m"))
        self.assertTrue(result.endswith("t</ud>"))

    def test_sensitive_key_full_redacts(self):
        r = pl.Redactor("full")
        result = r.redact_value("api_key", "some-long-key")
        self.assertEqual(result, "<ud>XXXXX</ud>")

    def test_short_value_fully_redacts_even_partial(self):
        r = pl.Redactor("partial")
        result = r.redact_value("password", "ab")
        self.assertEqual(result, "<ud>XXXXX</ud>")

    def test_none_level_returns_value_unchanged(self):
        r = pl.Redactor("none")
        self.assertEqual(r.redact_value("password", "secret"), "secret")

    def test_token_key_is_sensitive(self):
        r = pl.Redactor("full")
        self.assertEqual(r.redact_value("token", "xyz"), "<ud>XXXXX</ud>")

    def test_authorization_key_is_sensitive(self):
        r = pl.Redactor("partial")
        result = r.redact_value("authorization", "Bearer abc")
        self.assertIn("<ud>", result)


class TestRedactorRedactDict(unittest.TestCase):
    """Tests for Redactor.redact_dict()."""

    def test_non_sensitive_keys_unchanged(self):
        r = pl.Redactor("partial")
        d = {"name": "alice", "age": 30}
        result = r.redact_dict(d)
        self.assertEqual(result, {"name": "alice", "age": 30})

    def test_sensitive_keys_redacted(self):
        r = pl.Redactor("partial")
        d = {"username": "alice", "password": "secret123"}
        result = r.redact_dict(d)
        self.assertEqual(result["username"], "alice")
        self.assertIn("<ud>", result["password"])

    def test_nested_dict_redacted(self):
        r = pl.Redactor("partial")
        d = {"auth": {"password": "secret123", "host": "localhost"}}
        result = r.redact_dict(d)
        self.assertEqual(result["auth"]["host"], "localhost")
        self.assertIn("<ud>", result["auth"]["password"])

    def test_none_level_returns_dict_unchanged(self):
        r = pl.Redactor("none")
        d = {"password": "secret"}
        self.assertEqual(r.redact_dict(d), d)


# ===================================================================
# LogKeyLevelFilter
# ===================================================================

class TestLogKeyLevelFilter(unittest.TestCase):
    """Tests for LogKeyLevelFilter."""

    def _make_record(self, level, log_key=None):
        record = logging.LogRecord(
            name="test", level=level, pathname="", lineno=0,
            msg="msg", args=(), exc_info=None,
        )
        if log_key is not None:
            record.log_key = log_key
        return record

    def test_allow_all_wildcard(self):
        f = pl.LogKeyLevelFilter(["*"], logging.DEBUG, {})
        record = self._make_record(logging.DEBUG, "CHANGES")
        self.assertTrue(f.filter(record))

    def test_reject_key_not_in_allowed(self):
        f = pl.LogKeyLevelFilter(["CHANGES", "HTTP"], logging.DEBUG, {})
        record = self._make_record(logging.DEBUG, "MAPPING")
        self.assertFalse(f.filter(record))

    def test_allow_key_in_set(self):
        f = pl.LogKeyLevelFilter(["CHANGES", "HTTP"], logging.DEBUG, {})
        record = self._make_record(logging.DEBUG, "HTTP")
        self.assertTrue(f.filter(record))

    def test_reject_below_base_level(self):
        f = pl.LogKeyLevelFilter(["*"], logging.WARNING, {})
        record = self._make_record(logging.DEBUG, "CHANGES")
        self.assertFalse(f.filter(record))

    def test_per_key_override(self):
        f = pl.LogKeyLevelFilter(["*"], logging.INFO, {"HTTP": logging.WARNING})
        # HTTP at INFO should be rejected (override requires WARNING)
        record = self._make_record(logging.INFO, "HTTP")
        self.assertFalse(f.filter(record))
        # HTTP at WARNING should pass
        record2 = self._make_record(logging.WARNING, "HTTP")
        self.assertTrue(f.filter(record2))

    def test_no_log_key_passes_at_base_level(self):
        f = pl.LogKeyLevelFilter(["CHANGES"], logging.INFO, {})
        record = self._make_record(logging.INFO)
        self.assertTrue(f.filter(record))

    def test_no_log_key_rejected_below_base_level(self):
        f = pl.LogKeyLevelFilter(["CHANGES"], logging.WARNING, {})
        record = self._make_record(logging.INFO)
        self.assertFalse(f.filter(record))

    def test_case_insensitive_keys(self):
        f = pl.LogKeyLevelFilter(["changes"], logging.DEBUG, {})
        record = self._make_record(logging.DEBUG, "CHANGES")
        self.assertTrue(f.filter(record))


# ===================================================================
# infer_operation
# ===================================================================

class TestInferOperation(unittest.TestCase):
    """Tests for infer_operation()."""

    def test_method_delete(self):
        self.assertEqual(pl.infer_operation(method="DELETE"), "DELETE")

    def test_change_deleted_flag(self):
        self.assertEqual(pl.infer_operation(change={"deleted": True}), "DELETE")

    def test_method_get(self):
        self.assertEqual(pl.infer_operation(method="GET"), "SELECT")

    def test_rev_1_dash_is_insert(self):
        self.assertEqual(
            pl.infer_operation(doc={"_rev": "1-abc123"}),
            "INSERT",
        )

    def test_rev_2_dash_is_update(self):
        self.assertEqual(
            pl.infer_operation(doc={"_rev": "2-abc123"}),
            "UPDATE",
        )

    def test_no_rev_is_update(self):
        self.assertEqual(pl.infer_operation(doc={}), "UPDATE")

    def test_change_with_rev_1_in_changes_list(self):
        change = {"changes": [{"rev": "1-xyz"}]}
        self.assertEqual(pl.infer_operation(change=change), "INSERT")

    def test_change_with_rev_2_in_changes_list(self):
        change = {"changes": [{"rev": "2-xyz"}]}
        self.assertEqual(pl.infer_operation(change=change), "UPDATE")

    def test_none_inputs_returns_update(self):
        self.assertEqual(pl.infer_operation(), "UPDATE")

    def test_delete_method_takes_priority(self):
        # method=DELETE should win even if doc has rev 1-
        self.assertEqual(
            pl.infer_operation(doc={"_rev": "1-abc"}, method="DELETE"),
            "DELETE",
        )


# ===================================================================
# log_event
# ===================================================================

class TestLogEvent(unittest.TestCase):
    """Tests for log_event()."""

    def test_correct_level_and_extra(self):
        logger = logging.getLogger("test_log_event")
        logger.setLevel(pl.TRACE)
        with patch.object(logger, "log") as mock_log:
            pl.log_event(logger, "warn", "HTTP", "request failed",
                         status=500, url="http://example.com")
            mock_log.assert_called_once_with(
                logging.WARNING,
                "request failed",
                extra={"log_key": "HTTP", "status": 500,
                       "url": "http://example.com"},
            )

    def test_skips_when_not_enabled(self):
        logger = logging.getLogger("test_log_event_skip")
        logger.setLevel(logging.CRITICAL)
        with patch.object(logger, "log") as mock_log:
            pl.log_event(logger, "debug", "CHANGES", "ignored")
            mock_log.assert_not_called()

    def test_trace_level(self):
        logger = logging.getLogger("test_log_event_trace")
        logger.setLevel(pl.TRACE)
        with patch.object(logger, "log") as mock_log:
            pl.log_event(logger, "trace", "MAPPING", "detail")
            mock_log.assert_called_once()
            self.assertEqual(mock_log.call_args[0][0], pl.TRACE)


# ===================================================================
# RedactingFormatter
# ===================================================================

class TestRedactingFormatter(unittest.TestCase):
    """Tests for RedactingFormatter."""

    def _make_record(self, msg, **extras):
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg=msg, args=(), exc_info=None,
        )
        for k, v in extras.items():
            setattr(record, k, v)
        return record

    def test_log_key_prefix(self):
        fmt = pl.RedactingFormatter(pl.Redactor("none"))
        record = self._make_record("hello", log_key="HTTP")
        output = fmt.format(record)
        self.assertIn("[HTTP]", output)

    def test_extra_fields_appended(self):
        fmt = pl.RedactingFormatter(pl.Redactor("none"))
        record = self._make_record("hello", log_key="OUTPUT",
                                   status=200, doc_id="doc1")
        output = fmt.format(record)
        self.assertIn("status=200", output)
        self.assertIn("doc_id=doc1", output)

    def test_url_field_redacted(self):
        fmt = pl.RedactingFormatter(pl.Redactor("partial"))
        record = self._make_record("req",
                                   log_key="HTTP",
                                   url="http://admin:pass@host/db")
        output = fmt.format(record)
        self.assertIn("<ud>***:***</ud>@", output)
        self.assertNotIn("admin:pass", output)

    def test_message_redacted(self):
        fmt = pl.RedactingFormatter(pl.Redactor("full"))
        record = self._make_record("connecting to http://user:pw@host/db")
        output = fmt.format(record)
        self.assertIn("<ud>XXXXX</ud>@", output)
        self.assertNotIn("user:pw", output)

    def test_no_extras_no_suffix(self):
        fmt = pl.RedactingFormatter(pl.Redactor("none"))
        record = self._make_record("plain message")
        output = fmt.format(record)
        self.assertIn("plain message", output)


# ===================================================================
# configure_logging
# ===================================================================

class TestConfigureLogging(unittest.TestCase):
    """Tests for configure_logging()."""

    def tearDown(self):
        # Reset root logger after each test
        root = logging.getLogger()
        root.handlers.clear()
        root.setLevel(logging.WARNING)

    def test_legacy_mode(self):
        pl.configure_logging({"level": "WARNING"})
        root = logging.getLogger()
        self.assertEqual(len(root.handlers), 1)
        handler = root.handlers[0]
        self.assertIsInstance(handler, logging.StreamHandler)
        self.assertEqual(handler.level, logging.WARNING)

    def test_legacy_mode_redaction(self):
        pl.configure_logging({"level": "DEBUG", "redaction_level": "full"})
        r = pl.get_redactor()
        self.assertEqual(r.level, "full")

    def test_sg_style_console_only(self):
        cfg = {
            "redaction_level": "partial",
            "console": {
                "enabled": True,
                "log_level": "debug",
                "log_keys": ["*"],
            },
        }
        pl.configure_logging(cfg)
        root = logging.getLogger()
        self.assertEqual(len(root.handlers), 1)
        r = pl.get_redactor()
        self.assertEqual(r.level, "partial")

    def test_sg_style_file_handler(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = os.path.join(tmpdir, "test.log")
            cfg = {
                "redaction_level": "none",
                "console": {"enabled": False},
                "file": {
                    "enabled": True,
                    "path": log_path,
                    "log_level": "info",
                    "log_keys": ["*"],
                    "rotation": {
                        "max_size": 10,
                        "max_age": 1,
                        "rotated_logs_size_limit": 50,
                    },
                },
            }
            pl.configure_logging(cfg)
            root = logging.getLogger()
            self.assertEqual(len(root.handlers), 1)
            self.assertIsInstance(root.handlers[0], pl.ManagedRotatingFileHandler)

    def test_sg_style_both_handlers(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = os.path.join(tmpdir, "test.log")
            cfg = {
                "console": {"enabled": True, "log_level": "info", "log_keys": ["*"]},
                "file": {
                    "enabled": True,
                    "path": log_path,
                    "log_level": "debug",
                    "log_keys": ["*"],
                },
            }
            pl.configure_logging(cfg)
            root = logging.getLogger()
            self.assertEqual(len(root.handlers), 2)

    def test_console_disabled_no_handler(self):
        cfg = {
            "console": {"enabled": False},
        }
        pl.configure_logging(cfg)
        root = logging.getLogger()
        self.assertEqual(len(root.handlers), 0)

    def test_sg_style_key_levels(self):
        cfg = {
            "console": {
                "enabled": True,
                "log_level": "info",
                "log_keys": ["*"],
                "key_levels": {"HTTP": "warn"},
            },
        }
        pl.configure_logging(cfg)
        root = logging.getLogger()
        handler = root.handlers[0]
        filt = handler.filters[0]
        self.assertIsInstance(filt, pl.LogKeyLevelFilter)
        self.assertEqual(filt.key_levels.get("HTTP"), logging.WARNING)


# ===================================================================
# ManagedRotatingFileHandler
# ===================================================================

class TestManagedRotatingFileHandler(unittest.TestCase):
    """Tests for ManagedRotatingFileHandler."""

    def test_creates_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            subdir = os.path.join(tmpdir, "nested", "logs")
            log_path = os.path.join(subdir, "app.log")
            handler = pl.ManagedRotatingFileHandler(log_path, max_size_mb=1)
            self.assertTrue(os.path.isdir(subdir))
            handler.close()

    def test_cleanup_removes_old_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = os.path.join(tmpdir, "app.log")
            handler = pl.ManagedRotatingFileHandler(
                log_path, max_size_mb=1, max_age_days=0,
            )
            # Create fake rotated files
            for i in range(3):
                rotated = f"{log_path}.{i+1}"
                with open(rotated, "w") as f:
                    f.write("x" * 100)
                # Set mtime to 2 days ago
                old_time = time.time() - 2 * 86400
                os.utime(rotated, (old_time, old_time))

            handler._cleanup_rotated_files()

            remaining = [f for f in os.listdir(tmpdir) if f != "app.log"]
            self.assertEqual(len(remaining), 0)
            handler.close()

    def test_cleanup_enforces_size_limit(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = os.path.join(tmpdir, "app.log")
            # 1 byte size limit → should remove excess
            handler = pl.ManagedRotatingFileHandler(
                log_path, max_size_mb=1, max_age_days=365,
                rotated_logs_size_limit_mb=0,
            )
            for i in range(3):
                rotated = f"{log_path}.{i+1}"
                with open(rotated, "w") as f:
                    f.write("x" * 1024)

            handler._cleanup_rotated_files()

            remaining = [f for f in os.listdir(tmpdir) if f.startswith("app.log.")]
            self.assertEqual(len(remaining), 0)
            handler.close()


# ===================================================================
# Constants / module-level
# ===================================================================

class TestConstants(unittest.TestCase):
    """Sanity checks for module-level constants."""

    def test_trace_level_value(self):
        self.assertEqual(pl.TRACE, 5)

    def test_log_keys_contains_expected(self):
        for key in ("CHANGES", "HTTP", "OUTPUT", "CBL", "DLQ"):
            self.assertIn(key, pl.LOG_KEYS)

    def test_levels_mapping(self):
        self.assertEqual(pl.LEVELS["trace"], pl.TRACE)
        self.assertEqual(pl.LEVELS["debug"], logging.DEBUG)
        self.assertEqual(pl.LEVELS["warn"], logging.WARNING)
        self.assertEqual(pl.LEVELS["warning"], logging.WARNING)


if __name__ == "__main__":
    unittest.main()
