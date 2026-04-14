"""Cloud (S3) and local file storage abstraction.

When AWS_S3_BUCKET is set, files are stored in S3.
Otherwise, falls back to local disk (for local dev).
"""

import logging
import os
import uuid
from pathlib import Path

logger = logging.getLogger("rubricgen")

# ─────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────

S3_BUCKET = os.environ.get("AWS_S3_BUCKET", "")
S3_REGION = os.environ.get("AWS_S3_REGION", "us-east-1")
S3_PREFIX = os.environ.get("AWS_S3_PREFIX", "lab-documents/")

_USE_S3 = bool(S3_BUCKET)
_s3_client = None

LOCAL_UPLOAD_DIR = Path("uploads")
LOCAL_UPLOAD_DIR.mkdir(exist_ok=True)


def _get_s3():
    """Lazy-init the S3 client."""
    global _s3_client
    if _s3_client is None:
        import boto3
        _s3_client = boto3.client(
            "s3",
            region_name=S3_REGION,
            aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
        )
    return _s3_client


def _make_key(filename: str) -> str:
    """Generate a unique S3 key (or local filename) preserving extension."""
    ext = Path(filename).suffix.lower()
    return f"{S3_PREFIX}{uuid.uuid4().hex}{ext}"


# ─────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────

def upload_file(data: bytes, filename: str, content_type: str = "") -> str:
    """Store file bytes. Returns the storage path/key.

    - S3 mode: uploads to bucket, returns 's3://{bucket}/{key}'
    - Local mode: writes to uploads/, returns local path string
    """
    key = _make_key(filename)

    if _USE_S3:
        s3 = _get_s3()
        extra = {}
        if content_type:
            extra["ContentType"] = content_type
        s3.put_object(
            Bucket=S3_BUCKET,
            Key=key,
            Body=data,
            **extra,
        )
        path = f"s3://{S3_BUCKET}/{key}"
        logger.info("Uploaded %s to S3: %s", filename, path)
        return path

    # Local fallback
    local_name = key.replace(S3_PREFIX, "")
    dest = LOCAL_UPLOAD_DIR / local_name
    dest.write_bytes(data)
    path = str(dest)
    logger.info("Uploaded %s locally: %s", filename, path)
    return path


def download_file(path: str) -> bytes | None:
    """Retrieve file bytes by storage path. Returns None if not found."""
    if path.startswith("s3://"):
        try:
            s3 = _get_s3()
            # Parse s3://bucket/key
            parts = path[5:].split("/", 1)
            bucket = parts[0]
            key = parts[1] if len(parts) > 1 else ""
            obj = s3.get_object(Bucket=bucket, Key=key)
            return obj["Body"].read()
        except Exception as e:
            logger.error("S3 download failed for %s: %s", path, e)
            return None

    # Local file
    try:
        return Path(path).read_bytes()
    except Exception as e:
        logger.error("Local file read failed for %s: %s", path, e)
        return None


def delete_file(path: str) -> bool:
    """Delete a file from storage. Returns True on success."""
    if path.startswith("s3://"):
        try:
            s3 = _get_s3()
            parts = path[5:].split("/", 1)
            bucket = parts[0]
            key = parts[1] if len(parts) > 1 else ""
            s3.delete_object(Bucket=bucket, Key=key)
            logger.info("Deleted S3 object: %s", path)
            return True
        except Exception as e:
            logger.error("S3 delete failed for %s: %s", path, e)
            return False

    # Local file
    try:
        Path(path).unlink(missing_ok=True)
        return True
    except Exception as e:
        logger.error("Local file delete failed for %s: %s", path, e)
        return False


def get_content_type(filename: str) -> str:
    """Guess content type from filename extension."""
    ext = Path(filename).suffix.lower()
    types = {
        ".pdf": "application/pdf",
        ".doc": "application/msword",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".csv": "text/csv",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }
    return types.get(ext, "application/octet-stream")


def is_cloud_storage() -> bool:
    """Check if we're using cloud (S3) storage."""
    return _USE_S3
