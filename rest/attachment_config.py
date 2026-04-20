"""
Attachment-handling configuration dataclasses.

Defines the typed config schema for the ``attachments`` block in
config.json and a helper to parse raw dicts into dataclass instances
with all defaults applied.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger("changes_worker")


# ---------------------------------------------------------------------------
# Sub-configs
# ---------------------------------------------------------------------------


@dataclass
class AttachmentFilterConfig:
    content_types: list[str] = field(default_factory=list)
    reject_content_types: list[str] = field(default_factory=list)
    min_size_bytes: int = 0
    max_size_bytes: int = 0
    max_total_bytes_per_doc: int = 0
    name_pattern: str = ""
    ignore_revpos: bool = False


@dataclass
class AttachmentFetchConfig:
    use_bulk_get: bool = False
    max_concurrent_downloads: int = 5
    max_concurrent_downloads_global: int = 20
    request_timeout_seconds: int = 120
    temp_dir: str = "/tmp/attachments"
    stream_to_disk_threshold_bytes: int = 10_485_760
    verify_digest: bool = True
    verify_length: bool = True


@dataclass
class AttachmentDestinationS3Config:
    bucket: str = ""
    region: str = "us-east-1"
    endpoint_url: str = ""
    access_key_id: str = ""
    secret_access_key: str = ""
    session_token: str = ""
    storage_class: str = ""
    server_side_encryption: str = ""
    kms_key_id: str = ""
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass
class AttachmentDestinationHTTPConfig:
    url_template: str = ""
    method: str = "PUT"
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class AttachmentDestinationFilesystemConfig:
    base_path: str = ""
    dir_template: str = "{doc_id}"
    preserve_filename: bool = True


@dataclass
class AttachmentPresignedUrlsConfig:
    enabled: bool = False
    expiry_seconds: int = 604_800


@dataclass
class AttachmentDestinationConfig:
    type: str = "s3"
    key_template: str = "{prefix}/{doc_id}/{attachment_name}"
    key_prefix: str = "attachments"
    s3: AttachmentDestinationS3Config = field(
        default_factory=AttachmentDestinationS3Config
    )
    http: AttachmentDestinationHTTPConfig = field(
        default_factory=AttachmentDestinationHTTPConfig
    )
    filesystem: AttachmentDestinationFilesystemConfig = field(
        default_factory=AttachmentDestinationFilesystemConfig
    )
    presigned_urls: AttachmentPresignedUrlsConfig = field(
        default_factory=AttachmentPresignedUrlsConfig
    )


@dataclass
class AttachmentAdminAuthConfig:
    method: str = "basic"
    username: str = ""
    password: str = ""


@dataclass
class AttachmentPostProcessConfig:
    action: str = "none"
    update_field: str = "attachments_external"
    remove_attachments_after_upload: bool = False
    ttl_seconds: int = 86_400
    admin_url: str = ""
    admin_auth: AttachmentAdminAuthConfig = field(
        default_factory=AttachmentAdminAuthConfig
    )
    on_doc_missing: str = "skip"
    max_conflict_retries: int = 3
    cleanup_orphaned_uploads: bool = False


@dataclass
class AttachmentRetryConfig:
    max_retries: int = 3
    backoff_base_seconds: int = 1
    backoff_max_seconds: int = 30
    retry_on_status: list[int] = field(
        default_factory=lambda: [408, 429, 500, 502, 503, 504]
    )


# ---------------------------------------------------------------------------
# Top-level config
# ---------------------------------------------------------------------------


@dataclass
class AttachmentConfig:
    enabled: bool = False
    dry_run: bool = False
    mode: str = "individual"
    filter: AttachmentFilterConfig = field(default_factory=AttachmentFilterConfig)
    fetch: AttachmentFetchConfig = field(default_factory=AttachmentFetchConfig)
    destination: AttachmentDestinationConfig = field(
        default_factory=AttachmentDestinationConfig
    )
    post_process: AttachmentPostProcessConfig = field(
        default_factory=AttachmentPostProcessConfig
    )
    retry: AttachmentRetryConfig = field(default_factory=AttachmentRetryConfig)
    on_missing_attachment: str = "skip"
    partial_success: str = "continue"
    halt_on_failure: bool = True
    skip_on_edge_server: bool = True


# ---------------------------------------------------------------------------
# Default singleton
# ---------------------------------------------------------------------------

DEFAULT_ATTACHMENT_CONFIG = AttachmentConfig()


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def _parse_sub(raw: dict, cls: type, nested: dict[str, type] | None = None):
    """Instantiate *cls* from *raw*, recursing into *nested* sub-dataclasses."""
    kwargs: dict = {}
    for f in cls.__dataclass_fields__:
        if f not in raw:
            continue
        if nested and f in nested:
            kwargs[f] = _parse_sub(
                raw[f] if isinstance(raw[f], dict) else {}, nested[f]
            )
        else:
            kwargs[f] = raw[f]
    return cls(**kwargs)


def parse_attachment_config(raw: dict) -> AttachmentConfig:
    """Parse a raw ``attachments`` dict from config.json into an
    :class:`AttachmentConfig` with all defaults applied.

    Keys absent from *raw* fall back to the dataclass defaults.
    """
    if not raw:
        return AttachmentConfig()

    dest_nested = {
        "s3": AttachmentDestinationS3Config,
        "http": AttachmentDestinationHTTPConfig,
        "filesystem": AttachmentDestinationFilesystemConfig,
        "presigned_urls": AttachmentPresignedUrlsConfig,
    }
    post_nested = {
        "admin_auth": AttachmentAdminAuthConfig,
    }

    sub_parsers: dict[str, tuple[type, dict[str, type] | None]] = {
        "filter": (AttachmentFilterConfig, None),
        "fetch": (AttachmentFetchConfig, None),
        "destination": (AttachmentDestinationConfig, dest_nested),
        "post_process": (AttachmentPostProcessConfig, post_nested),
        "retry": (AttachmentRetryConfig, None),
    }

    kwargs: dict = {}
    for key, value in raw.items():
        if key in sub_parsers:
            cls, nested = sub_parsers[key]
            kwargs[key] = _parse_sub(
                value if isinstance(value, dict) else {}, cls, nested
            )
        elif key in AttachmentConfig.__dataclass_fields__:
            kwargs[key] = value

    return AttachmentConfig(**kwargs)
