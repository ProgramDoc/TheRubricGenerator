"""Tests for backend.pdf_fetcher.

Mocks httpx so no real network calls are made. Verifies:
- %PDF magic-byte gate rejects HTML pages even if Content-Type lies
- Each strategy returns (result, outcome, reason) per the new contract
- Unpaywall lookup is consulted when DOI is present
- citation_pdf_url meta tag fallback works
- All-strategies-fail returns None cleanly
- Tier is reported on every hit (free / firecrawl / browser)
- Auto / Firecrawl / browser are gated by use_firecrawl + use_browser
- Per-strategy event callback fires for every attempt
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


def test_strat_direct_rejects_html_disguised_as_pdf():
    """Even if Content-Type says PDF, magic-byte check must reject HTML."""
    client = MagicMock()
    client.get.return_value = _fake_response(
        status=200,
        content=b"<html><body>paywall</body></html>",
        content_type="application/pdf",  # lying!
    )
    result, outcome, reason = pdf_fetcher._strat_direct(
        client, "https://example.com/foo.pdf", attempt=1
    )
    assert result is None
    assert outcome == "miss"
    assert reason == "not_pdf_magic"


def test_strat_direct_accepts_real_pdf(monkeypatch):
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
    result, outcome, reason = pdf_fetcher._strat_direct(
        client, "https://example.com/foo.pdf", attempt=1
    )
    assert outcome == "hit"
    assert reason is None
    assert result is not None
    assert result["sha256"]
    assert captured["bytes"] == b"%PDF-1.4\nfake pdf"


def test_strat_direct_classifies_5xx_as_transient():
    """HTTP 503 should map to transient_error so the retry loop kicks in."""
    client = MagicMock()
    client.get.return_value = _fake_response(status=503, content=b"")
    result, outcome, reason = pdf_fetcher._strat_direct(
        client, "https://flaky.example/foo.pdf", attempt=1
    )
    assert result is None
    assert outcome == "transient_error"
    assert reason == "http_503"


def test_strat_direct_classifies_4xx_as_miss():
    """HTTP 403 (paywall) should be a miss, not retried."""
    client = MagicMock()
    client.get.return_value = _fake_response(status=403, content=b"")
    result, outcome, reason = pdf_fetcher._strat_direct(
        client, "https://paywall.example/foo.pdf", attempt=1
    )
    assert result is None
    assert outcome == "miss"
    assert reason == "http_403"


def test_strat_meta_tag_extracts_citation_pdf_url(monkeypatch):
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

    result, outcome, reason = pdf_fetcher._strat_meta_tag(
        client, "https://publisher.example/article", attempt=1
    )
    assert outcome == "hit"
    assert result is not None


def test_strat_meta_tag_returns_miss_when_no_meta_tag():
    client = MagicMock()
    client.get.return_value = _fake_response(
        status=200,
        content=b"<html>nothing useful</html>",
        content_type="text/html",
        text="<html>nothing useful</html>",
    )
    result, outcome, reason = pdf_fetcher._strat_meta_tag(
        client, "https://example.com", attempt=1
    )
    assert result is None
    assert outcome == "miss"
    assert reason == "no_citation_pdf_url"


def test_strat_firecrawl_returns_permanent_error_when_no_api_key(monkeypatch):
    """No FIRECRAWL_API_KEY → permanent_error so retry doesn't hammer."""
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    client = MagicMock()
    result, outcome, reason = pdf_fetcher._strat_firecrawl(
        client, "https://example.com/article", attempt=1
    )
    assert result is None
    assert outcome == "permanent_error"
    assert reason == "no_firecrawl_api_key"
    client.get.assert_not_called()


def test_strat_firecrawl_extracts_citation_pdf_url_from_metadata(monkeypatch):
    """When Firecrawl returns metadata.citation_pdf_url, fetch the PDF directly."""
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-test-key")
    monkeypatch.setattr(pdf_fetcher.paper_files, "write_paper_file",
                        lambda b, f: f"local/{f}")

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

    direct_client = MagicMock()
    direct_client.get.return_value = _fake_response(
        content=b"%PDF-1.4\nfirecrawl-fetched", content_type="application/pdf",
    )
    result, outcome, reason = pdf_fetcher._strat_firecrawl(
        direct_client, "https://publisher.example/article", attempt=1
    )
    assert outcome == "hit"
    assert result is not None
    assert result["sha256"]


def test_fetch_pdf_for_result_returns_none_when_all_fail(monkeypatch):
    """Result with no PMCID, no DOI, and a URL that doesn't yield a PDF → None."""
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


def test_fetch_pdf_for_result_uses_firecrawl_only_when_opted_in(monkeypatch):
    """fetch_pdf_for_result(use_firecrawl=False) must not call _strat_firecrawl."""
    fc_calls = []
    def boom(client, url, attempt):
        fc_calls.append(url)
        return None, "miss", "stubbed"
    monkeypatch.setattr(pdf_fetcher, "_strat_firecrawl", boom)

    fake_client = MagicMock()
    fake_client.get.return_value = _fake_response(
        status=404, content=b"", content_type="text/html", text=""
    )
    class CtxClient:
        def __init__(self, *a, **kw): self._inner = fake_client
        def __enter__(self): return self._inner
        def __exit__(self, *a): return False
    monkeypatch.setattr(pdf_fetcher.httpx, "Client", CtxClient)

    pdf_fetcher.fetch_pdf_for_result(
        {"pmcid": None, "doi": None, "url": "https://nope.example/foo", "pmid": None, "title": "x"},
        Path("/tmp"), use_firecrawl=False,
    )
    assert fc_calls == []

    pdf_fetcher.fetch_pdf_for_result(
        {"pmcid": None, "doi": None, "url": "https://nope.example/foo", "pmid": None, "title": "x"},
        Path("/tmp"), use_firecrawl=True,
    )
    assert len(fc_calls) >= 1


def test_fetch_pdf_for_result_pmc_short_circuits_and_tags_tier(monkeypatch):
    """If PMC succeeds, we never touch httpx, and tier='free' is on the result."""
    monkeypatch.setattr(
        pdf_fetcher, "_strat_pmc",
        lambda pmcid, dest, attempt: (
            {"sha256": "abc", "filename": "x.pdf", "storage_path": "local/x.pdf"},
            "hit", None,
        ),
    )

    def boom(*a, **kw):
        raise AssertionError("httpx should not be called when PMC succeeds")
    monkeypatch.setattr(pdf_fetcher.httpx, "Client", boom)

    out = pdf_fetcher.fetch_pdf_for_result(
        {"pmcid": "PMC123", "doi": "10.x", "url": "u", "pmid": "1", "title": "t"},
        Path("/tmp"),
    )
    assert out is not None
    assert out["sha256"] == "abc"
    assert out["tier"] == "free"


def test_fetch_pdf_for_result_emits_per_strategy_events(monkeypatch):
    """on_event must fire for every strategy attempted, with strategy name + outcome."""
    fake_client = MagicMock()
    fake_client.get.return_value = _fake_response(
        status=404, content=b"", content_type="text/html", text=""
    )

    class CtxClient:
        def __init__(self, *a, **kw): self._inner = fake_client
        def __enter__(self): return self._inner
        def __exit__(self, *a): return False

    monkeypatch.setattr(pdf_fetcher.httpx, "Client", CtxClient)

    events = []
    pdf_fetcher.fetch_pdf_for_result(
        {"pmcid": None, "doi": None, "url": "https://nope.example/foo",
         "pmid": None, "title": "x"},
        Path("/tmp"),
        on_event=lambda payload: events.append(payload),
    )
    # We expect at least the meta_tag and result_url_direct strategies to have
    # emitted events (no PMCID, no DOI → those are skipped at the orchestrator).
    strategies_seen = {e["strategy"] for e in events}
    assert "meta_tag" in strategies_seen
    assert "result_url_direct" in strategies_seen
    # Every event has the required keys
    for e in events:
        assert "strategy" in e
        assert "outcome" in e
        assert "duration_ms" in e
        assert "attempt" in e


def test_run_with_retry_retries_on_transient_error_and_succeeds(monkeypatch):
    """Strategy returns transient_error on attempt 1, hit on attempt 2 → retry wins."""
    # Disable the real backoff sleep so the test is fast.
    monkeypatch.setattr(pdf_fetcher.time, "sleep", lambda *_: None)

    attempts = []
    def fn(attempt_no):
        attempts.append(attempt_no)
        if attempt_no == 1:
            return None, "transient_error", "http_502"
        return {"sha256": "h", "filename": "f.pdf", "storage_path": "p"}, "hit", None

    out = pdf_fetcher._run_with_retry("test_strat", None, fn, attempts=2)
    assert attempts == [1, 2]
    assert out is not None
    assert out["sha256"] == "h"


def test_run_with_retry_does_not_retry_on_permanent_error(monkeypatch):
    """Strategy returns permanent_error → only one attempt."""
    monkeypatch.setattr(pdf_fetcher.time, "sleep", lambda *_: None)

    attempts = []
    def fn(attempt_no):
        attempts.append(attempt_no)
        return None, "permanent_error", "no_api_key"

    out = pdf_fetcher._run_with_retry("test_strat", None, fn, attempts=2)
    assert attempts == [1]
    assert out is None


def test_run_with_retry_classifies_httpx_timeout_as_transient(monkeypatch):
    """A raised httpx.ReadTimeout should be treated as transient."""
    monkeypatch.setattr(pdf_fetcher.time, "sleep", lambda *_: None)

    attempts = []
    def fn(attempt_no):
        attempts.append(attempt_no)
        if attempt_no == 1:
            raise pdf_fetcher.httpx.ReadTimeout("timed out")
        return {"sha256": "h", "filename": "f.pdf", "storage_path": "p"}, "hit", None

    out = pdf_fetcher._run_with_retry("test_strat", None, fn, attempts=2)
    assert attempts == [1, 2]
    assert out is not None
