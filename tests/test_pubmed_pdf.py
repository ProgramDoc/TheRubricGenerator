"""Tests for backend.pubmed PMC PDF download strategies.

Mocks urllib so they run offline. The real-network probe lives in the
acceptance tests for the worktree branch.

Background: PMC put a JS proof-of-work interstitial on
``/pmc/articles/{pmcid}/pdf/`` — both the legacy bot UA and a Chrome UA now
get HTML back from that endpoint. The fix uses the OA web service tarball as
the primary path.
"""

from __future__ import annotations

import gzip
import io
import tarfile
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from backend import pubmed


# A minimal real-looking PDF (`%PDF-1.4` header is what `_is_pdf_bytes` checks).
PDF_BYTES = b"%PDF-1.4\n%fake\n%%EOF\n"

OA_XML = (
    b"<OA><record id='PMC123' citation='Demo'>"
    b"<link format='tgz' updated='2026-04-29 09:25:17' "
    b"href='ftp://ftp.ncbi.nlm.nih.gov/pub/pmc/oa_package/aa/bb/PMC123.tar.gz' />"
    b"</record></OA>"
)


def _build_tarball() -> bytes:
    """Build a valid PMC-style oa_package tarball that contains a fake PDF."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        info = tarfile.TarInfo(name="PMC123/paper.pdf")
        info.size = len(PDF_BYTES)
        tar.addfile(info, io.BytesIO(PDF_BYTES))
    return gzip.compress(buf.getvalue())


class _FakeResponse:
    def __init__(self, body: bytes, headers: dict | None = None, url: str = "") -> None:
        self._body = body
        self.headers = headers or {}
        self.url = url
        self.status = 200

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def test_oa_package_strategy_pulls_pdf_from_tarball(monkeypatch):
    """OA service returns XML with a tarball URL → fetch tarball → extract PDF."""
    tar_bytes = _build_tarball()

    def fake_urlopen(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else req
        if "oa.fcgi" in url:
            return _FakeResponse(OA_XML)
        # Tarball path — return the tarball bytes
        if url.endswith(".tar.gz"):
            return _FakeResponse(tar_bytes)
        raise AssertionError(f"unexpected url {url}")

    monkeypatch.setattr(pubmed.urllib.request, "urlopen", fake_urlopen)

    out = pubmed._try_oa_package("PMC123")
    assert out == PDF_BYTES


def test_oa_package_falls_back_to_deprecated_path_on_404(monkeypatch):
    """Live oa_package path 404s → we retry under deprecated/oa_package/..."""
    tar_bytes = _build_tarball()
    calls: list[str] = []

    def fake_urlopen(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else req
        calls.append(url)
        if "oa.fcgi" in url:
            return _FakeResponse(OA_XML)
        if "/pub/pmc/oa_package/" in url and "/deprecated/" not in url:
            import urllib.error
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        if "/deprecated/oa_package/" in url:
            return _FakeResponse(tar_bytes)
        raise AssertionError(f"unexpected url {url}")

    monkeypatch.setattr(pubmed.urllib.request, "urlopen", fake_urlopen)

    out = pubmed._try_oa_package("PMC123")
    assert out == PDF_BYTES
    # Verified we tried the live path first, then the deprecated mirror
    assert any("/pub/pmc/oa_package/" in u and "/deprecated/" not in u for u in calls)
    assert any("/deprecated/oa_package/" in u for u in calls)


def test_oa_package_returns_none_when_not_in_index(monkeypatch):
    """Empty OA response (no `<link format=tgz>`) → strategy returns None."""
    empty_xml = b"<OA><records returned-count='0' total-count='0'/></OA>"

    def fake_urlopen(req, timeout=None):
        return _FakeResponse(empty_xml)

    monkeypatch.setattr(pubmed.urllib.request, "urlopen", fake_urlopen)
    assert pubmed._try_oa_package("PMC999") is None


def test_oa_package_rejects_non_pdf_tarball_member(monkeypatch):
    """Tarball without any .pdf member returns None (degrades to next strategy)."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        info = tarfile.TarInfo(name="PMC123/notes.txt")
        info.size = 5
        tar.addfile(info, io.BytesIO(b"hello"))
    tar_bytes = gzip.compress(buf.getvalue())

    def fake_urlopen(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else req
        if "oa.fcgi" in url:
            return _FakeResponse(OA_XML)
        return _FakeResponse(tar_bytes)

    monkeypatch.setattr(pubmed.urllib.request, "urlopen", fake_urlopen)
    assert pubmed._try_oa_package("PMC123") is None


def test_citation_pdf_url_strategy_fetches_pdf(monkeypatch):
    """Strategy 2: scrape `<meta name=citation_pdf_url>` from landing → fetch."""
    landing_html = (
        b"<html><head>"
        b"<meta name=\"citation_pdf_url\" content=\"https://cdn.example/x.pdf\">"
        b"</head></html>"
    )

    def fake_urlopen(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else req
        if "/articles/PMC123/" in url and not url.endswith(".pdf"):
            return _FakeResponse(landing_html, url=url)
        if url == "https://cdn.example/x.pdf":
            return _FakeResponse(PDF_BYTES, headers={"Content-Type": "application/pdf"})
        raise AssertionError(f"unexpected url {url}")

    monkeypatch.setattr(pubmed.urllib.request, "urlopen", fake_urlopen)
    out = pubmed._try_citation_pdf_url("PMC123")
    assert out == PDF_BYTES


def test_citation_pdf_url_returns_none_when_meta_missing(monkeypatch):
    landing_html = b"<html>nothing here</html>"

    def fake_urlopen(req, timeout=None):
        return _FakeResponse(landing_html, url="https://x/")

    monkeypatch.setattr(pubmed.urllib.request, "urlopen", fake_urlopen)
    assert pubmed._try_citation_pdf_url("PMC123") is None


def test_download_pmc_pdf_persists_when_oa_strategy_hits(monkeypatch):
    """End-to-end: download_pmc_pdf returns the standard result dict."""
    tar_bytes = _build_tarball()

    def fake_urlopen(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else req
        if "oa.fcgi" in url:
            return _FakeResponse(OA_XML)
        return _FakeResponse(tar_bytes)

    monkeypatch.setattr(pubmed.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(pubmed.time, "sleep", lambda *a, **kw: None)

    # Stub paper_files.write_paper_file so we don't hit S3/local storage abstraction
    import backend.paper_files as pf
    monkeypatch.setattr(pf, "write_paper_file", lambda data, filename: f"local/{filename}")

    with tempfile.TemporaryDirectory() as tmp:
        result = pubmed.download_pmc_pdf("PMC123", Path(tmp))
        assert result is not None
        assert result["sha256"]
        assert result["filename"].endswith(".pdf")
        assert result["storage_path"].startswith("local/")
        # Local copy should be present and contain the PDF magic
        assert result["path"].read_bytes()[:4] == b"%PDF"


def test_download_pmc_pdf_falls_back_when_oa_misses(monkeypatch):
    """OA service returns nothing → citation_pdf_url path should still find a PDF."""
    empty_xml = b"<OA><records returned-count='0' total-count='0'/></OA>"
    landing_html = (
        b"<html><meta name=\"citation_pdf_url\" content=\"https://cdn.example/p.pdf\">"
        b"</html>"
    )

    def fake_urlopen(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else req
        if "oa.fcgi" in url:
            return _FakeResponse(empty_xml)
        if url.startswith("https://www.ncbi.nlm.nih.gov/pmc/articles/PMC123/"):
            return _FakeResponse(landing_html, url=url)
        if url == "https://cdn.example/p.pdf":
            return _FakeResponse(PDF_BYTES, headers={"Content-Type": "application/pdf"})
        raise AssertionError(f"unexpected url {url}")

    monkeypatch.setattr(pubmed.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(pubmed.time, "sleep", lambda *a, **kw: None)
    import backend.paper_files as pf
    monkeypatch.setattr(pf, "write_paper_file", lambda data, filename: f"local/{filename}")

    with tempfile.TemporaryDirectory() as tmp:
        result = pubmed.download_pmc_pdf("PMC123", Path(tmp))
        assert result is not None
        assert result["path"].read_bytes()[:4] == b"%PDF"


def test_download_pmc_pdf_returns_none_when_both_strategies_miss(monkeypatch):
    """Both strategies miss → graceful None (caller falls through to other tiers)."""
    empty_xml = b"<OA><records returned-count='0' total-count='0'/></OA>"
    landing_html = b"<html>no meta tag here</html>"

    def fake_urlopen(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else req
        if "oa.fcgi" in url:
            return _FakeResponse(empty_xml)
        return _FakeResponse(landing_html, url=url)

    monkeypatch.setattr(pubmed.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(pubmed.time, "sleep", lambda *a, **kw: None)

    with tempfile.TemporaryDirectory() as tmp:
        assert pubmed.download_pmc_pdf("PMC404", Path(tmp)) is None


def test_download_pmc_pdf_normalises_pmcid_prefix(monkeypatch):
    """`12345` should be auto-prefixed to `PMC12345` before any lookups."""
    seen: list[str] = []

    def fake_urlopen(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else req
        seen.append(url)
        return _FakeResponse(b"<OA></OA>")

    monkeypatch.setattr(pubmed.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(pubmed.time, "sleep", lambda *a, **kw: None)
    with tempfile.TemporaryDirectory() as tmp:
        pubmed.download_pmc_pdf("12345", Path(tmp))
    assert any("id=PMC12345" in u for u in seen), seen
