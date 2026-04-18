"""
Production logging module for changes_worker.

Inspired by Couchbase Sync Gateway's logging configuration, this module
provides:
  - Multiple log levels including TRACE
  - Per-handler log_key filtering (CHANGES, PROCESSING, MAPPING, OUTPUT, etc.)
  - Per-key level overrides (e.g. HTTP→warn, MAPPING→debug)
  - File rotation with max_size, max_age, and rotated_logs_size_limit
  - Redaction of sensitive data (none / partial / full)
  - Operation tagging (INSERT, UPDATE, DELETE, SELECT)
"""

import glob as _glob
import logging
import os
import re
import time
from logging.handlers import RotatingFileHandler

# ---------------------------------------------------------------------------
# Custom TRACE level (below DEBUG)
# ---------------------------------------------------------------------------
TRACE = 5
logging.addLevelName(TRACE, "TRACE")


def _trace(self, message, *args, **kwargs):
    if self.isEnabledFor(TRACE):
        self._log(TRACE, message, args, **kwargs)


logging.Logger.trace = _trace  # type: ignore[attr-defined]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
LOG_KEYS = frozenset(
    {
        "CHANGES",  # _changes feed input
        "PROCESSING",  # filtering, routing
        "MAPPING",  # schema mapping
        "OUTPUT",  # stdout / HTTP / DB / cloud output
        "HTTP",  # HTTP requests / responses
        "CHECKPOINT",  # checkpoint load / save
        "RETRY",  # retry / backoff decisions
        "METRICS",  # metrics server
        "CBL",  # Couchbase Lite operations (read/write/open/close/maintenance)
        "DLQ",  # dead letter queue operations (add/retry/purge/list)
    }
)

LEVELS = {
    "trace": TRACE,
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warn": logging.WARNING,
    "warning": logging.WARNING,
    "error": logging.ERROR,
    "critical": logging.CRITICAL,
}

# ---------------------------------------------------------------------------
# Redactor
# ---------------------------------------------------------------------------
_SENSITIVE_FIELDS = re.compile(
    r"(password|passwd|pass|token|bearer_token|session_cookie|"
    r"authorization|cookie|secret|api_key|access_token|refresh_token)",
    re.IGNORECASE,
)

_URL_USERINFO_RE = re.compile(r"(https?://)([^@]+)@")
_HEADER_BEARER_RE = re.compile(r"(Bearer\s+)\S+", re.IGNORECASE)


class Redactor:
    """Redacts sensitive data from strings, dicts, and log messages."""

    def __init__(self, level: str = "partial"):
        self.level = level  # none | partial | full

    def redact_string(self, value: str) -> str:
        if self.level == "none":
            return value
        # Redact URL userinfo
        value = _URL_USERINFO_RE.sub(
            r"\1<ud>***:***</ud>@" if self.level == "partial" else r"\1<ud>XXXXX</ud>@",
            value,
        )
        # Redact Bearer tokens in strings
        if self.level == "partial":
            value = _HEADER_BEARER_RE.sub(
                lambda m: (
                    m.group(1)
                    + m.group(0)[-4:].rjust(len(m.group(0)) - len(m.group(1)), "*")
                ),
                value,
            )
        elif self.level == "full":
            value = _HEADER_BEARER_RE.sub(r"\1<ud>XXXXX</ud>", value)
        return value

    def redact_value(self, key: str, value) -> str:
        if self.level == "none":
            return str(value)
        if not _SENSITIVE_FIELDS.search(key):
            return str(value)
        s = str(value)
        if self.level == "full":
            return "<ud>XXXXX</ud>"
        # partial: show first and last char
        if len(s) <= 2:
            return "<ud>XXXXX</ud>"
        return f"<ud>{s[0]}{'*' * (len(s) - 2)}{s[-1]}</ud>"

    def redact_dict(self, d: dict) -> dict:
        if self.level == "none":
            return d
        out = {}
        for k, v in d.items():
            if isinstance(v, dict):
                out[k] = self.redact_dict(v)
            elif _SENSITIVE_FIELDS.search(k):
                out[k] = self.redact_value(k, v)
            else:
                out[k] = v
        return out


# Module-level redactor (configured during setup)
_redactor = Redactor("none")


def get_redactor() -> Redactor:
    return _redactor


# ---------------------------------------------------------------------------
# Log key / level filter
# ---------------------------------------------------------------------------
class LogKeyLevelFilter(logging.Filter):
    """
    Handler-level filter that checks:
      1. Is record.log_key in the allowed set?
      2. Does record.levelno meet the threshold (base or per-key override)?
    """

    def __init__(
        self, log_keys: list[str], base_level: int, key_levels: dict[str, int]
    ):
        super().__init__()
        self.allow_all = "*" in log_keys
        self.log_keys = set(k.upper() for k in log_keys)
        self.base_level = base_level
        self.key_levels = {k.upper(): v for k, v in key_levels.items()}

    def filter(self, record: logging.LogRecord) -> bool:
        log_key = getattr(record, "log_key", None)
        if log_key is None:
            # Messages without a log_key pass if they meet the base level
            return record.levelno >= self.base_level

        log_key = log_key.upper()
        if not self.allow_all and log_key not in self.log_keys:
            return False

        threshold = self.key_levels.get(log_key, self.base_level)
        return record.levelno >= threshold


# ---------------------------------------------------------------------------
# Redacting formatter
# ---------------------------------------------------------------------------
_EXTRA_FIELDS = (
    "log_key",
    "operation",
    "doc_id",
    "seq",
    "status",
    "url",
    "attempt",
    "elapsed_ms",
    "mode",
    "http_method",
    "bytes",
    "storage",
    "batch_size",
    "input_count",
    "filtered_count",
    "host",
    "port",
    "delay_seconds",
    "field_count",
    # CBL-specific fields
    "db_name",
    "db_path",
    "db_size_mb",
    "doc_count",
    "doc_type",
    "manifest_id",
    "maintenance_type",
    "duration_ms",
    "error_detail",
)


class RedactingFormatter(logging.Formatter):
    """
    Formatter that:
      - Prints structured key=value context from record extras
      - Redacts sensitive data in messages
    """

    def __init__(
        self, redactor: Redactor, fmt: str | None = None, datefmt: str | None = None
    ):
        super().__init__(fmt=fmt, datefmt=datefmt)
        self.redactor = redactor

    def format(self, record: logging.LogRecord) -> str:
        # Build structured suffix from extra fields
        parts: list[str] = []
        log_key = getattr(record, "log_key", None)
        if log_key:
            parts.append(f"[{log_key}]")

        for field in _EXTRA_FIELDS:
            if field == "log_key":
                continue
            val = getattr(record, field, None)
            if val is not None:
                # Redact URL fields
                if field == "url" and isinstance(val, str):
                    val = self.redactor.redact_string(val)
                parts.append(f"{field}={val}")

        # Redact the message itself
        record.msg = self.redactor.redact_string(str(record.msg))

        base = super().format(record)
        if parts:
            return f"{base} {' '.join(parts)}"
        return base


# ---------------------------------------------------------------------------
# Managed rotating file handler
# ---------------------------------------------------------------------------
class ManagedRotatingFileHandler(RotatingFileHandler):
    """
    RotatingFileHandler with SG-style retention:
      - max_size: MB per file before rollover
      - max_age: days to retain rotated files
      - rotated_logs_size_limit: total MB cap for rotated files
    """

    def __init__(
        self,
        filename: str,
        max_size_mb: int = 100,
        max_age_days: int = 7,
        rotated_logs_size_limit_mb: int = 1024,
        **kwargs,
    ):
        self.max_age_days = max_age_days
        self.rotated_logs_size_limit = rotated_logs_size_limit_mb * 1024 * 1024
        os.makedirs(os.path.dirname(filename) or ".", exist_ok=True)
        super().__init__(
            filename,
            maxBytes=max_size_mb * 1024 * 1024,
            backupCount=999,  # we manage cleanup ourselves
            **kwargs,
        )

    def doRollover(self):
        super().doRollover()
        self._cleanup_rotated_files()

    def _cleanup_rotated_files(self):
        base = self.baseFilename
        pattern = f"{base}.*"
        rotated = sorted(_glob.glob(pattern))

        now = time.time()
        max_age_secs = self.max_age_days * 86400

        # Remove files older than max_age
        remaining = []
        for path in rotated:
            try:
                age = now - os.path.getmtime(path)
                if age > max_age_secs:
                    os.remove(path)
                else:
                    remaining.append(path)
            except OSError:
                pass

        # Enforce total size limit (delete oldest first)
        total = sum(os.path.getsize(p) for p in remaining if os.path.exists(p))
        while total > self.rotated_logs_size_limit and remaining:
            oldest = remaining.pop(0)
            try:
                total -= os.path.getsize(oldest)
                os.remove(oldest)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Operation inference
# ---------------------------------------------------------------------------
def infer_operation(
    change: dict | None = None, doc: dict | None = None, method: str | None = None
) -> str:
    """
    Infer the logical DB operation from a change/doc/method.

    Returns one of: INSERT, UPDATE, DELETE, SELECT.
    """
    if method == "DELETE":
        return "DELETE"
    if change and change.get("deleted"):
        return "DELETE"
    if method == "GET":
        return "SELECT"

    # Check revision to distinguish INSERT vs UPDATE
    rev = None
    if doc:
        rev = doc.get("_rev", "")
    if change and not rev:
        changes_list = change.get("changes", [])
        if changes_list:
            rev = changes_list[0].get("rev", "")

    if rev and rev.startswith("1-"):
        return "INSERT"
    return "UPDATE"


# ---------------------------------------------------------------------------
# Thin helpers for structured logging
# ---------------------------------------------------------------------------
def log_event(
    logger: logging.Logger, level: str, log_key: str, message: str, **fields
) -> None:
    """Log a structured event with a log_key and extra fields."""
    lvl = LEVELS.get(level, logging.INFO)
    if not logger.isEnabledFor(lvl):
        return
    logger.log(lvl, message, extra={"log_key": log_key, **fields})


# ---------------------------------------------------------------------------
# Main configuration entry point
# ---------------------------------------------------------------------------
def configure_logging(cfg: dict) -> None:
    """
    Configure the logging system from the config.logging dict.

    Supports both the legacy {"level": "DEBUG"} format and the full
    SG-inspired config with console/file/rotation/redaction.
    """
    global _redactor

    root = logging.getLogger()
    # Clear existing handlers
    root.handlers.clear()
    root.setLevel(TRACE)

    # Legacy mode: simple level string
    if "console" not in cfg and "file" not in cfg:
        level_str = cfg.get("level", "DEBUG").lower()
        level = LEVELS.get(level_str, logging.DEBUG)
        _redactor = Redactor(cfg.get("redaction_level", "none"))

        handler = logging.StreamHandler()
        handler.setLevel(level)
        fmt = RedactingFormatter(
            _redactor,
            fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        )
        handler.setFormatter(fmt)
        root.addHandler(handler)

        # Route icecream to TRACE
        try:
            from icecream import ic

            ic.configureOutput(
                prefix="ic| ",
                outputFunction=lambda s: logging.getLogger("changes_worker").log(
                    TRACE, s
                ),
            )
        except ImportError:
            pass
        return

    # Full SG-style config
    redaction_level = cfg.get("redaction_level", "partial")
    _redactor = Redactor(redaction_level)

    # Console handler
    console_cfg = cfg.get("console", {})
    if console_cfg.get("enabled", True):
        base_level = LEVELS.get(
            console_cfg.get("log_level", "info").lower(), logging.INFO
        )
        log_keys = console_cfg.get("log_keys", ["*"])
        key_levels = {
            k: LEVELS.get(v.lower(), logging.INFO)
            for k, v in console_cfg.get("key_levels", {}).items()
        }

        handler = logging.StreamHandler()
        handler.setLevel(TRACE)  # actual filtering via LogKeyLevelFilter
        handler.addFilter(LogKeyLevelFilter(log_keys, base_level, key_levels))
        fmt = RedactingFormatter(
            _redactor,
            fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        )
        handler.setFormatter(fmt)
        root.addHandler(handler)

    # File handler
    file_cfg = cfg.get("file", {})
    if file_cfg.get("enabled", False):
        file_path = file_cfg.get("path", "logs/changes_worker.log")
        base_level = LEVELS.get(
            file_cfg.get("log_level", "debug").lower(), logging.DEBUG
        )
        log_keys = file_cfg.get("log_keys", ["*"])
        key_levels = {
            k: LEVELS.get(v.lower(), logging.DEBUG)
            for k, v in file_cfg.get("key_levels", {}).items()
        }

        rotation = file_cfg.get("rotation", {})
        max_size = rotation.get("max_size", 100)
        max_age = rotation.get("max_age", 7)
        rotated_limit = rotation.get("rotated_logs_size_limit", 1024)

        handler = ManagedRotatingFileHandler(
            file_path,
            max_size_mb=max_size,
            max_age_days=max_age,
            rotated_logs_size_limit_mb=rotated_limit,
        )
        handler.setLevel(TRACE)
        handler.addFilter(LogKeyLevelFilter(log_keys, base_level, key_levels))
        fmt = RedactingFormatter(
            _redactor,
            fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        )
        handler.setFormatter(fmt)
        root.addHandler(handler)

    # Route icecream to TRACE
    try:
        from icecream import ic

        ic.configureOutput(
            prefix="ic| ",
            outputFunction=lambda s: logging.getLogger("changes_worker").log(TRACE, s),
        )
    except ImportError:
        pass
