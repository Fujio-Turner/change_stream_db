"""
Attachment post-processing (Phase 3).

After Phase 2 uploads attachments to a destination, the post-processor
performs an action on the source document before it continues to the
RIGHT (output) stage of the pipeline.

Supported actions:
  - ``none``               — do nothing (default)
  - ``update_doc``         — PUT the doc with external URLs added
  - ``set_ttl``            — PUT the doc with ``_exp`` set
  - ``delete_doc``         — DELETE the document
  - ``delete_attachments`` — DELETE individual attachments from the doc
  - ``purge``              — POST to ``_purge`` on the admin port
"""

from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING
from urllib.parse import quote

import aiohttp

try:
    from icecream import ic
except ImportError:  # pragma: no cover
    ic = lambda *a, **kw: None  # noqa: E731

from pipeline_logging import log_event
from rest.attachment_config import AttachmentConfig
from rest.attachment_upload import AttachmentUploadResult
from rest.changes_http import ClientHTTPError, RetryableHTTP

if TYPE_CHECKING:
    from main import MetricsCollector

logger = logging.getLogger("changes_worker")


# ---------------------------------------------------------------------------
# Post-processor
# ---------------------------------------------------------------------------


class AttachmentPostProcessor:
    """Runs a post-process action on the source document after upload."""

    def __init__(
        self,
        config: AttachmentConfig,
        metrics: MetricsCollector | None = None,
    ):
        self._config = config
        self._pp = config.post_process
        self._metrics = metrics

    # -- public entry point -------------------------------------------------

    async def post_process(
        self,
        doc: dict,
        uploaded: dict[str, AttachmentUploadResult],
        base_url: str,
        http: RetryableHTTP,
        auth: aiohttp.BasicAuth | None,
        headers: dict,
    ) -> dict:
        """Execute the configured post-process action.

        Returns the (possibly modified) document.
        """
        action = self._pp.action
        if action == "none":
            return doc

        doc_id = doc.get("_id", doc.get("id", "<unknown>"))
        self._inc("attachments_post_process_total")

        try:
            if action == "update_doc":
                return await self._action_update_doc(
                    doc, uploaded, base_url, http, auth, headers
                )
            elif action == "set_ttl":
                return await self._action_set_ttl(doc, base_url, http, auth, headers)
            elif action == "delete_doc":
                return await self._action_delete_doc(doc, base_url, http, auth, headers)
            elif action == "delete_attachments":
                return await self._action_delete_attachments(
                    doc, uploaded, base_url, http, auth, headers
                )
            elif action == "purge":
                return await self._action_purge(doc, base_url, http, auth, headers)
            else:
                log_event(
                    logger,
                    "warn",
                    "PROCESSING",
                    "unknown post_process action: %s" % action,
                    doc_id=doc_id,
                )
                return doc
        except Exception as exc:
            self._inc("attachments_post_process_errors_total")
            if self._config.halt_on_failure:
                from rest.attachments import AttachmentError

                raise AttachmentError(
                    "post-process '%s' failed for doc %s: %s" % (action, doc_id, exc)
                ) from exc
            log_event(
                logger,
                "warn",
                "PROCESSING",
                "post-process '%s' error (continuing): %s" % (action, exc),
                doc_id=doc_id,
            )
            return doc

    # -- action: update_doc -------------------------------------------------

    async def _action_update_doc(
        self,
        doc: dict,
        uploaded: dict[str, AttachmentUploadResult],
        base_url: str,
        http: RetryableHTTP,
        auth: aiohttp.BasicAuth | None,
        headers: dict,
    ) -> dict:
        doc_id = doc.get("_id", doc.get("id", "<unknown>"))
        rev = doc.get("_rev", doc.get("rev", ""))

        # Build the external attachments map
        ext = self._build_external_map(uploaded)

        body = dict(doc)
        body[self._pp.update_field] = ext
        if self._pp.remove_attachments_after_upload:
            body.pop("_attachments", None)

        for attempt in range(1, self._pp.max_conflict_retries + 1):
            ok, new_rev = await self._put_doc(
                doc_id, rev, body, base_url, http, auth, headers
            )
            if ok:
                body["_rev"] = new_rev
                log_event(
                    logger,
                    "info",
                    "PROCESSING",
                    "post-process update_doc succeeded",
                    doc_id=doc_id,
                )
                return body

            # _put_doc returns (False, "") for missing doc (already handled)
            if not new_rev:
                return doc

            # Conflict – re-fetch and retry
            refreshed, fresh_rev = await self._handle_conflict(
                doc_id, uploaded, base_url, http, auth, headers
            )
            if refreshed is None:
                return doc
            rev = fresh_rev
            body = dict(refreshed)
            body[self._pp.update_field] = ext
            if self._pp.remove_attachments_after_upload:
                body.pop("_attachments", None)

        self._inc("attachments_post_process_errors_total")
        log_event(
            logger,
            "warn",
            "PROCESSING",
            "post-process update_doc exhausted conflict retries",
            doc_id=doc_id,
        )
        return doc

    # -- action: set_ttl ----------------------------------------------------

    async def _action_set_ttl(
        self,
        doc: dict,
        base_url: str,
        http: RetryableHTTP,
        auth: aiohttp.BasicAuth | None,
        headers: dict,
    ) -> dict:
        doc_id = doc.get("_id", doc.get("id", "<unknown>"))
        rev = doc.get("_rev", doc.get("rev", ""))

        body = dict(doc)
        body["_exp"] = int(time.time()) + self._pp.ttl_seconds

        for attempt in range(1, self._pp.max_conflict_retries + 1):
            ok, new_rev = await self._put_doc(
                doc_id, rev, body, base_url, http, auth, headers
            )
            if ok:
                body["_rev"] = new_rev
                log_event(
                    logger,
                    "info",
                    "PROCESSING",
                    "post-process set_ttl succeeded (_exp=%d)" % body["_exp"],
                    doc_id=doc_id,
                )
                return body

            if not new_rev:
                return doc

            # Conflict
            refreshed, fresh_rev = await self._handle_conflict(
                doc_id, {}, base_url, http, auth, headers
            )
            if refreshed is None:
                return doc
            rev = fresh_rev
            body = dict(refreshed)
            body["_exp"] = int(time.time()) + self._pp.ttl_seconds

        self._inc("attachments_post_process_errors_total")
        log_event(
            logger,
            "warn",
            "PROCESSING",
            "post-process set_ttl exhausted conflict retries",
            doc_id=doc_id,
        )
        return doc

    # -- action: delete_doc -------------------------------------------------

    async def _action_delete_doc(
        self,
        doc: dict,
        base_url: str,
        http: RetryableHTTP,
        auth: aiohttp.BasicAuth | None,
        headers: dict,
    ) -> dict:
        doc_id = doc.get("_id", doc.get("id", "<unknown>"))
        rev = doc.get("_rev", doc.get("rev", ""))

        for attempt in range(1, self._pp.max_conflict_retries + 1):
            url = "%s/%s?rev=%s" % (
                base_url.rstrip("/"),
                quote(doc_id, safe=""),
                quote(rev, safe=""),
            )
            try:
                resp = await http.request("DELETE", url, auth=auth, headers=headers)
                resp.release()
                log_event(
                    logger,
                    "info",
                    "PROCESSING",
                    "post-process delete_doc succeeded",
                    doc_id=doc_id,
                )
                return doc
            except ClientHTTPError as exc:
                if exc.status == 404:
                    self._handle_missing_doc(doc_id, "delete_doc")
                    return doc
                if exc.status == 409:
                    self._inc("attachments_conflict_retries_total")
                    refreshed, fresh_rev = await self._handle_conflict(
                        doc_id, {}, base_url, http, auth, headers
                    )
                    if refreshed is None:
                        return doc
                    rev = fresh_rev
                    continue
                raise

        self._inc("attachments_post_process_errors_total")
        log_event(
            logger,
            "warn",
            "PROCESSING",
            "post-process delete_doc exhausted conflict retries",
            doc_id=doc_id,
        )
        return doc

    # -- action: delete_attachments -----------------------------------------

    async def _action_delete_attachments(
        self,
        doc: dict,
        uploaded: dict[str, AttachmentUploadResult],
        base_url: str,
        http: RetryableHTTP,
        auth: aiohttp.BasicAuth | None,
        headers: dict,
    ) -> dict:
        doc_id = doc.get("_id", doc.get("id", "<unknown>"))
        current_rev = doc.get("_rev", doc.get("rev", ""))

        for name in uploaded:
            deleted = False
            for attempt in range(1, self._pp.max_conflict_retries + 1):
                url = "%s/%s/%s?rev=%s" % (
                    base_url.rstrip("/"),
                    quote(doc_id, safe=""),
                    quote(name, safe=""),
                    quote(current_rev, safe=""),
                )
                try:
                    resp = await http.request("DELETE", url, auth=auth, headers=headers)
                    resp_body = await resp.json()
                    current_rev = resp_body.get("rev", current_rev)
                    resp.release()
                    deleted = True
                    break
                except ClientHTTPError as exc:
                    if exc.status == 404:
                        log_event(
                            logger,
                            "debug",
                            "PROCESSING",
                            "attachment already missing: %s" % name,
                            doc_id=doc_id,
                        )
                        deleted = True
                        break
                    if exc.status == 409:
                        self._inc("attachments_conflict_retries_total")
                        refreshed, fresh_rev = await self._handle_conflict(
                            doc_id, uploaded, base_url, http, auth, headers
                        )
                        if refreshed is None:
                            break
                        current_rev = fresh_rev
                        continue
                    raise

            if not deleted:
                self._inc("attachments_post_process_errors_total")
                log_event(
                    logger,
                    "warn",
                    "PROCESSING",
                    "failed to delete attachment %s after retries" % name,
                    doc_id=doc_id,
                )

        log_event(
            logger,
            "info",
            "PROCESSING",
            "post-process delete_attachments completed",
            doc_id=doc_id,
        )
        doc["_rev"] = current_rev
        return doc

    # -- action: purge ------------------------------------------------------

    async def _action_purge(
        self,
        doc: dict,
        base_url: str,
        http: RetryableHTTP,
        auth: aiohttp.BasicAuth | None,
        headers: dict,
    ) -> dict:
        doc_id = doc.get("_id", doc.get("id", "<unknown>"))

        admin_url = self._pp.admin_url
        if not admin_url:
            raise RuntimeError("post_process.admin_url is required for purge action")

        # Extract keyspace from base_url (last path component)
        keyspace = base_url.rstrip("/").rsplit("/", 1)[-1]
        purge_url = "%s/%s/_purge" % (
            admin_url.rstrip("/"),
            quote(keyspace, safe=""),
        )

        admin_auth = None
        if self._pp.admin_auth.username:
            admin_auth = aiohttp.BasicAuth(
                self._pp.admin_auth.username,
                self._pp.admin_auth.password,
            )

        req_headers = dict(headers)
        req_headers["Content-Type"] = "application/json"
        purge_body = json.dumps({doc_id: ["*"]})

        resp = await http.request(
            "POST",
            purge_url,
            data=purge_body,
            auth=admin_auth,
            headers=req_headers,
        )
        resp.release()

        log_event(
            logger,
            "info",
            "PROCESSING",
            "post-process purge succeeded",
            doc_id=doc_id,
        )
        return doc

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _build_external_map(
        uploaded: dict[str, AttachmentUploadResult],
    ) -> dict[str, dict]:
        ext: dict[str, dict] = {}
        for name, result in uploaded.items():
            ext[name] = {
                "url": result.location,
                "content_type": result.content_type,
                "length": result.length,
                "digest": result.digest,
                "uploaded_at": result.uploaded_at,
            }
        return ext

    async def _put_doc(
        self,
        doc_id: str,
        rev: str,
        body: dict,
        base_url: str,
        http: RetryableHTTP,
        auth: aiohttp.BasicAuth | None,
        headers: dict,
    ) -> tuple[bool, str]:
        """PUT a document back to the source.

        Returns ``(True, new_rev)`` on success, ``(False, "conflict")``
        on 409, or ``(False, "")`` when the doc is missing and
        ``on_doc_missing`` is ``"skip"``.

        Raises on other HTTP errors or if ``on_doc_missing`` is
        ``"fail"`` and the doc is 404.
        """
        url = "%s/%s?rev=%s" % (
            base_url.rstrip("/"),
            quote(doc_id, safe=""),
            quote(rev, safe=""),
        )
        req_headers = dict(headers)
        req_headers["Content-Type"] = "application/json"

        try:
            resp = await http.request(
                "PUT", url, data=json.dumps(body), auth=auth, headers=req_headers
            )
            resp_body = await resp.json()
            resp.release()
            return True, resp_body.get("rev", rev)
        except ClientHTTPError as exc:
            if exc.status == 404:
                self._handle_missing_doc(doc_id, "put_doc")
                return False, ""
            if exc.status == 409:
                self._inc("attachments_conflict_retries_total")
                return False, "conflict"
            raise

    async def _handle_conflict(
        self,
        doc_id: str,
        uploaded: dict[str, AttachmentUploadResult],
        base_url: str,
        http: RetryableHTTP,
        auth: aiohttp.BasicAuth | None,
        headers: dict,
    ) -> tuple[dict | None, str]:
        """Re-fetch a doc after a 409 conflict.

        Returns ``(doc, rev)`` on success.  If the doc is gone or
        attachments have disappeared (stale), returns ``(None, "")``.
        """
        url = "%s/%s" % (base_url.rstrip("/"), quote(doc_id, safe=""))

        try:
            resp = await http.request("GET", url, auth=auth, headers=headers)
            refreshed = await resp.json()
            resp.release()
        except ClientHTTPError as exc:
            if exc.status == 404:
                self._handle_missing_doc(doc_id, "conflict_refetch")
                return None, ""
            log_event(
                logger,
                "warn",
                "PROCESSING",
                "conflict re-fetch returned %d" % exc.status,
                doc_id=doc_id,
            )
            return None, ""
        except Exception as exc:
            log_event(
                logger,
                "warn",
                "PROCESSING",
                "conflict re-fetch failed: %s" % exc,
                doc_id=doc_id,
            )
            return None, ""

        fresh_rev = refreshed.get("_rev", "")

        # Check that uploaded attachments still exist as stubs
        if uploaded:
            current_stubs = refreshed.get("_attachments", {})
            for name in uploaded:
                if name not in current_stubs:
                    self._inc("attachments_stale_total")
                    log_event(
                        logger,
                        "warn",
                        "PROCESSING",
                        "attachment %s no longer present after conflict" % name,
                        doc_id=doc_id,
                    )
                    if self._pp.cleanup_orphaned_uploads:
                        self._inc("attachments_orphaned_uploads_total")
                    return None, ""

        log_event(
            logger,
            "debug",
            "PROCESSING",
            "conflict re-fetch ok, new rev=%s" % fresh_rev,
            doc_id=doc_id,
        )
        return refreshed, fresh_rev

    def _handle_missing_doc(self, doc_id: str, action: str) -> None:
        """Handle a 404 for a document based on ``on_doc_missing`` config."""
        if self._pp.on_doc_missing == "fail":
            from rest.attachments import AttachmentError

            raise AttachmentError(
                "document %s not found during post-process %s" % (doc_id, action)
            )

        # "skip" (default)
        self._inc("attachments_post_process_skipped_total")
        log_event(
            logger,
            "warn",
            "PROCESSING",
            "document not found during post-process %s (skipping)" % action,
            doc_id=doc_id,
        )

    def _inc(self, counter: str, amount: int = 1) -> None:
        if self._metrics:
            self._metrics.inc(counter, amount)
