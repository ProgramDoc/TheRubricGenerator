"""Paper-file storage abstraction.

Papers used to live on local disk at ``PAPERS_DIR / disk_filename``. That
is ephemeral on Render, so new uploads route through
:mod:`backend.storage` (S3 when ``AWS_S3_BUCKET`` is set, local ``uploads/``
otherwise). The ``papers.storage_path`` column records the resulting URI
or path.

Legacy rows (uploaded before the S3 migration) keep working: when
``storage_path`` is NULL we fall back to the old ``PAPERS_DIR / disk_filename``
layout so pre-migration PDFs still open on machines that still have them.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import HTTPException

from .storage import delete_file, download_file, is_cloud_storage, upload_file

logger = logging.getLogger("rubricgen")

# When S3 is configured but a write fails, the local-disk fallback is on
# Render's ephemeral filesystem — every deploy/restart wipes it, orphaning
# the DB row. Set STRICT_STORAGE=1 to re-raise instead of falling back, so
# the upload fails loudly rather than silently losing data.
_STRICT_STORAGE = os.environ.get("STRICT_STORAGE", "").strip() in {"1", "true", "yes"}


def _row_key(row, key: str):
    try:
        if key in row.keys():
            return row[key]
    except Exception:
        pass
    return None


def write_paper_file(content: bytes, filename: str) -> str:
    """Persist paper bytes. Returns the storage_path value to store in the DB.

    When S3 is not configured (local dev), ``upload_file`` writes to
    ``uploads/`` as a first-class store and this function returns normally.

    When S3 IS configured but the put fails (IAM, region, network), behavior
    depends on ``STRICT_STORAGE``:

    - ``STRICT_STORAGE=1`` (recommended for production) — re-raise so the
      upload fails loudly. The user retries, ops fixes IAM, no orphaned rows.
    - default — log a WARNING with explicit ephemerality wording and write
      to local ``uploads/`` so the user isn't blocked. On Render this disk is
      wiped on the next deploy/restart, so this path is a stop-gap that masks
      data loss; the WARNING is your signal to fix the underlying S3 issue.
    """
    s3_configured = is_cloud_storage()
    try:
        return upload_file(content, filename, "application/pdf")
    except Exception as e:
        if s3_configured and _STRICT_STORAGE:
            logger.error(
                "S3 write failed for %s and STRICT_STORAGE=1 is set — "
                "refusing to fall back to ephemeral local disk. Fix the S3 "
                "IAM / network issue and retry the upload. Underlying error: %s",
                filename,
                e,
            )
            raise

        # Import lazily so a truly busted storage module can't stop fallback.
        from pathlib import Path
        import uuid
        local_dir = Path("uploads")
        local_dir.mkdir(exist_ok=True)
        ext = Path(filename).suffix.lower() or ".pdf"
        local_path = local_dir / f"{uuid.uuid4().hex}{ext}"
        local_path.write_bytes(content)

        if s3_configured:
            # S3 was configured but failed — the local write is on an
            # ephemeral disk in production. Make this loud so it isn't
            # missed in the log noise.
            logger.warning(
                "S3 write failed for %s — falling back to local uploads/ at %s. "
                "WARNING: on Render the local disk is EPHEMERAL — this file "
                "WILL BE LOST on the next deploy/restart, orphaning its DB row. "
                "Fix the S3 IAM / network issue (or set STRICT_STORAGE=1 to "
                "fail uploads instead). Underlying error: %s",
                filename,
                local_path,
                e,
            )
        else:
            # No S3 configured at all (local dev) — local is the intended
            # store and this exception path is unexpected. Surface the
            # traceback so the dev sees what actually broke.
            logger.exception(
                "Local storage write via upload_file failed for %s — "
                "wrote to %s as a last-resort fallback. Underlying error: %s",
                filename,
                local_path,
                e,
            )
        return str(local_path)


def read_paper_bytes(row, papers_dir: Path) -> bytes:
    """Load a paper's bytes, preferring the S3/local storage path and
    falling back to the legacy ``PAPERS_DIR/disk_filename`` layout.

    ``row`` must be a DB row exposing at least ``disk_filename``, ``filename``,
    and optionally ``storage_path``.
    """
    storage_path = _row_key(row, "storage_path")
    if storage_path:
        data = download_file(storage_path)
        if data is not None:
            return data
        logger.warning("Paper download failed for storage_path=%s; trying disk fallback", storage_path)

    disk_name = _row_key(row, "disk_filename") or f"{_row_key(row, 'filename') or ''}.pdf"
    path = papers_dir / disk_name
    if path.exists():
        return path.read_bytes()
    raise HTTPException(
        404,
        "PDF file not found. If this paper was uploaded before the S3 "
        "migration and your deployment wiped the local disk, please re-upload it.",
    )


def delete_paper_file(row, papers_dir: Path) -> None:
    """Best-effort delete of a paper's storage object and any legacy local copy."""
    storage_path = _row_key(row, "storage_path")
    if storage_path:
        try:
            delete_file(storage_path)
        except Exception as e:
            logger.warning("Paper storage delete failed for %s: %s", storage_path, e)

    disk_name = _row_key(row, "disk_filename")
    if disk_name:
        try:
            (papers_dir / disk_name).unlink(missing_ok=True)
        except Exception as e:
            logger.warning("Paper local delete failed for %s: %s", disk_name, e)
