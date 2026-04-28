"""Tests for backend.pdf_fetcher.

Mocks httpx so no real network calls are made. Verifies:
- %PDF magic-byte gate rejects HTML pages even if Content-Type lies
- Unpaywall lookup is consulted when DOI is present
- citation_pdf_url meta tag fallback works
- All-strategies-fail returns None cleanly
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, MagicMock

from backend import pdf_fetcher


def _fake_response(status: int = 200, content: bytes = b"",
                   content_type: str = "application/pdf",
                   text: str | None = None):
    r = MagicMock()
    r.status_code = status
    r.headers = {"content-type": content_type}
    r.content = content
    r.text = text if text is not None else content.decode("utf-8", errors="ignore")
    r.json.return_value = {}
    return r


def test_is_pdf_bytes_accepts_pdf_magic():
    assert pdf_fetcher._is_pdf_bytes(b"%PDF-1.4\n...") is True


def test_is_pdf_bytes_rejects_html():
    assert pdf_fetcher._is_pdf_bytes(b"<html>not a pdf</html>") is False
    assert pdf_fetcher._is_pdf_bytes(b"") is False


def test_try_direct_rejects_html_disguised_as_pdf(tmp_path):
    """Even if Content-Type says PDF, magic-byte check must reject HTML."""
    client = MagicMock()
    client.get.return_value = _fake_response(
        status=200,
        content=b"<html><body>paywall</body></html>",
        content_type="application/pdf",  # lying!
    )
    assert pdf_fetcher._try_direct(client, "https://example.com/foo.pdf") is None


def test_try_direct_accepts_real_pdf(tmp_path, monkeypatch):
    """A response with %PDF magic + pdf content-type should succeed."""
    captured = {}

    def fake_write(content, filename):
        captured["bytes"] = content
        captured["filename"] = filename
        return f"local/{filename}"

    monkeypatch.setattr(pdf_fetcher.paper_files, "write_paper_file", fake_write)

    client = MagicMock()
    client.get.return_value = _fake_response(
        status=200,
        content=b"%PDF-1.4\nfake pdf",
        content_type="application/pdf",
    )
    out = pdf_fetcher._try_direct(client, "https://example.com/foo.pdf")
    assert out is not None
    assert out["sha256"]
    assert captured["bytes"] == b"%PDF-1.4\nfake pdf"


def test_try_unpaywall_uses_url_for_pdf(monkeypatch):
    """Unpaywall lookup should chain to _try_direct with the OA pdf URL."""
    monkeypatch.setattr(pdf_fetcher.paper_files, "write_paper_file",
                        lambda b, f: f"local/{f}")

    client = MagicMock()
    # First call: Unpaywall API
    unpaywall_resp = MagicMock()
    unpaywall_resp.status_code = 200
    unpaywall_resp.json.return_value = {
        "best_oa_location": {"url_for_pdf": "https://example.com/oa.pdf"}
    }
    # Second call: the OA PDF itself
    pdf_resp = _fake_response(content=b"%PDF-1.4\noa", content_type="application/pdf")

    client.get.side_effect = [unpaywall_resp, pdf_resp]
    out = pdf_fetcher._try_unpaywall(client, "10.1234/foo")
    assert out is not None
    assert out["sha256"]


def test_try_unpaywall_returns_none_when_no_oa(monkeypatch):
    client = MagicMock()
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"best_oa_location": None}
    client.get.return_value = resp
    assert pdf_fetcher._try_unpaywall(client, "10.1234/closed") is None


def test_try_meta_tag_extracts_citation_pdf_url(monkeypatch):
    """Publishers like Springer / BMJ emit <meta name='citation_pdf_url'>."""
    monkeypatch.setattr(pdf_fetcher.paper_files, "write_paper_file",
                        lambda b, f: f"local/{f}")

    landing_html = """
    <html><head>
      <meta name="citation_pdf_url" content="https://publisher.example/paper.pdf">
    </head><body>Landing page</body></html>
    """
    client = MagicMock()
    landing_resp = _fake_response(
        status=200, content=landing_html.encode(),
        content_type="text/html; charset=utf-8",
        text=landing_html,
    )
    pdf_resp = _fake_response(content=b"%PDF-1.4\nmeta", content_type="application/pdf")
    client.get.side_effect = [landing_resp, pdf_resp]

    out = pdf_fetcher._try_meta_tag(client, "https://publisher.example/article")
    assert out is not None


def test_try_meta_tag_returns_none_when_no_meta_tag():
    client = MagicMock()
    client.get.return_value = _fake_response(
        status=200,
        content=b"<html>nothing useful</html>",
        content_type="text/html",
        text="<html>nothing useful</html>",
    )
    assert pdf_fetcher._try_meta_tag(client, "https://example.com") is None


def test_fetch_pdf_for_result_returns_none_when_all_fail(monkeypatch):
    """Result with no PMCID, no DOI, and a URL that doesn't yield a PDF → None."""
    monkeypatch.setattr(pdf_fetcher, "_try_pmc", lambda *a, **kw: None)

    fake_client = MagicMock()
    fake_client.get.return_value = _fake_response(
        status=404, content=b"", content_type="text/html", text=""
    )

    class CtxClient:
        def __init__(self, *a, **kw):
            self._inner = fake_client
        def __enter__(self):
            return self._inner
        def __exit__(self, *a):
            return False

    monkeypatch.setattr(pdf_fetcher.httpx, "Client", CtxClient)

    out = pdf_fetcher.fetch_pdf_for_result(
        {"pmcid": None, "doi": None, "url": "https://nope.example/foo", "pmid": None, "title": "x"},
        Path("/tmp"),
    )
    assert out is None


def test_firecrawl_skipped_when_api_key_unset(monkeypatch):
    """_try_firecrawl returns None silently if FIRECRAWL_API_KEY is unset."""
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    client = MagicMock()
    out = pdf_fetcher._try_firecrawl(client, "https://example.com/article")
    assert out is None
    client.get.assert_not_called()


def test_firecrawl_extracts_citation_pdf_url_from_metadata(monkeypatch):
    """When Firecrawl returns metadata.citation_pdf_url, fetch the PDF directly."""
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-test-key")
    monkeypatch.setattr(pdf_fetcher.paper_files, "write_paper_file",
                        lambda b, f: f"local/{f}")

    # Stub the Firecrawl scrape call to return a metadata-laden response
    fake_scrape_response = MagicMock()
    fake_scrape_response.status_code = 200
    fake_scrape_response.json.return_value = {
        "success": True,
        "data": {
            "html": "",
            "metadata": {"citation_pdf_url": "https://publisher.example/paper.pdf"},
        },
    }

    class FakeFCClient:
        def __init__(self, *a, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def post(self, url, **kw):
            assert "firecrawl.dev" in url or url.startswith("http")
            return fake_scrape_response

    monkeypatch.setattr(pdf_fetcher.httpx, "Client", FakeFCClient)

    # Now exercise _try_firecrawl. It calls _firecrawl_scrape (which uses
    # httpx.Client → FakeFCClient) and then _try_direct on the PDF URL.
    direct_client = MagicMock()
    direct_client.get.return_value = _fake_response(
        content=b"%PDF-1.4\nfirecrawl-fetched", content_type="application/pdf",
    )
    out = pdf_fetcher._try_firecrawl(direct_client, "https://publisher.example/article")
    assert out is not None
    assert out["sha256"]


def test_fetch_pdf_for_result_uses_firecrawl_only_when_opted_in(monkeypatch):
    """fetch_pdf_for_result(use_firecrawl=False) must not call _try_firecrawl."""
    monkeypatch.setattr(pdf_fetcher, "_try_pmc", lambda *a, **kw: None)

    fc_calls = []
    def boom(*a, **kw):
        fc_calls.append(1)
        return None
    monkeypatch.setattr(pdf_fetcher, "_try_firecrawl", boom)

    fake_client = MagicMock()
    fake_client.get.return_value = _fake_response(
        status=404, content=b"", content_type="text/html", text=""
    )
    class CtxClient:
        def __init__(self, *a, **kw): self._inner = fake_client
        def __enter__(self): return self._inner
        def __exit__(self, *a): return False
    monkeypatch.setattr(pdf_fetcher.httpx, "Client", CtxClient)

    # use_firecrawl=False → no firecrawl call
    pdf_fetcher.fetch_pdf_for_result(
        {"pmcid": None, "doi": None, "url": "https://nope.example/foo", "pmid": None, "title": "x"},
        Path("/tmp"), use_firecrawl=False,
    )
    assert fc_calls == []

    # use_firecrawl=True → _try_firecrawl IS called as a final fallback
    pdf_fetcher.fetch_pdf_for_result(
        {"pmcid": None, "doi": None, "url": "https://nope.example/foo", "pmid": None, "title": "x"},
        Path("/tmp"), use_firecrawl=True,
    )
    assert len(fc_calls) == 1


def test_fetch_pdf_for_result_pmc_short_circuits(monkeypatch):
    """If PMC succeeds, we never touch httpx — saves a roundtrip."""
    monkeypatch.setattr(pdf_fetcher, "_try_pmc",
                        lambda pmcid, dest: {"sha256": "abc", "filename": "x.pdf",
                                             "storage_path": "local/x.pdf"})
    # If httpx.Client is touched, raise to make it obvious in the test failure
    def boom(*a, **kw):
        raise AssertionError("httpx should not be called when PMC succeeds")
    monkeypatch.setattr(pdf_fetcher.httpx, "Client", boom)

    out = pdf_fetcher.fetch_pdf_for_result(
        {"pmcid": "PMC123", "doi": "10.x", "url": "u", "pmid": "1", "title": "t"},
        Path("/tmp"),
    )
    assert out == {"sha256": "abc", "filename": "x.pdf", "storage_path": "local/x.pdf"}
