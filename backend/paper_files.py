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
from pathlib import Path

from fastapi import HTTPException

from .storage import delete_file, download_file, upload_file

logger = logging.getLogger("rubricgen")


def _row_key(row, key: str):
    try:
        if key in row.keys():
            return row[key]
    except Exception:
        pass
    return None


def write_paper_file(content: bytes, filename: str) -> str:
    """Persist paper bytes. Returns the storage_path value to store in the DB.

    Prefers S3 (``s3://bucket/key``) when configured. If the S3 write fails
    for ANY reason — missing IAM permissions, wrong region, network — the
    exception is logged and we fall back to a local ``uploads/...`` path so
    the user can still upload. Ephemeral on Render, but at least non-fatal.
    """
    try:
        return upload_file(content, filename, "application/pdf")
    except Exception as e:
        logger.exception(
            "Primary storage write failed for %s — falling back to local uploads/",
            filename,
        )
        # Import lazily so a truly busted storage module can't stop fallback.
        from pathlib import Path
        import uuid
        local_dir = Path("uploads")
        local_dir.mkdir(exist_ok=True)
        ext = Path(filename).suffix.lower() or ".pdf"
        local_path = local_dir / f"{uuid.uuid4().hex}{ext}"
        local_path.write_bytes(content)
        logger.info("Local fallback write succeeded: %s (reason=%s)", local_path, e)
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
