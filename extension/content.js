/**
 * The AI Researcher — PDF Fetcher (content script)
 *
 * Runs on every page (we narrow the actual logic to landing pages we're
 * processing). The script is idempotent and dormant unless the background
 * service worker is processing this exact tab.
 *
 * Contract: when the background opens a tab for processing, this script
 *   1. Looks for citation_pdf_url meta + common selectors
 *   2. If heuristics miss, harvests anchors and asks the server's LLM picker
 *   3. fetches the PDF URL with credentials (browser cookies / VPN / SSO)
 *   4. POSTs the bytes back via chrome.runtime.sendMessage
 */

(function() {
  // Avoid running on extension/internal pages
  if (location.protocol === "chrome:" || location.protocol === "chrome-extension:") return;

  // Heuristic: the background only listens for messages from tabs it opened
  // and is awaiting. Sending pdf_found / pdf_not_found from a tab the user
  // opened themselves is harmless — the message is dropped (no pending
  // promise to resolve). So we always run, no opt-in handshake needed.

  const PDF_LINK_SELECTORS = [
    'meta[name="citation_pdf_url"]',
    'a[href*="/pdf/"]',
    'a[href$=".pdf"]',
    'a[type="application/pdf"]',
  ];
  const PDF_LINK_TEXT_PATTERNS = [
    /^download\s*pdf$/i,
    /^full\s*text\s*pdf$/i,
    /^view\s*pdf$/i,
    /^get\s*pdf$/i,
    /^pdf$/i,
  ];

  /* ─── PDF link discovery ────────────────────────────────────────────── */

  function findCitationPdfMeta() {
    const m = document.querySelector('meta[name="citation_pdf_url"]');
    return m && m.getAttribute("content") ? m.getAttribute("content") : null;
  }

  function findPdfLinkBySelector() {
    for (const sel of PDF_LINK_SELECTORS) {
      const el = document.querySelector(sel);
      if (el && el.tagName === "META") continue;  // handled above
      const href = el && (el.getAttribute("href") || el.href);
      if (href) return href;
    }
    // Anchor text patterns
    const anchors = document.querySelectorAll("a[href]");
    for (const a of anchors) {
      const text = (a.innerText || "").trim();
      if (PDF_LINK_TEXT_PATTERNS.some(p => p.test(text))) {
        const href = a.getAttribute("href") || a.href;
        if (href) return href;
      }
    }
    return null;
  }

  function harvestAnchors() {
    const out = [];
    const anchors = document.querySelectorAll("a");
    for (let i = 0; i < anchors.length && out.length < 200; i++) {
      const a = anchors[i];
      const href = a.href || "";
      if (!href || !href.startsWith("http")) continue;
      out.push({
        href: href,
        text: (a.innerText || "").trim().slice(0, 120),
        aria: (a.getAttribute("aria-label") || "").slice(0, 120),
        type: a.getAttribute("type") || "",
      });
    }
    return out;
  }

  function normalizePdfUrl(pdfUrl) {
    try {
      // URL constructor handles relative + protocol-relative
      return new URL(pdfUrl, location.href).href;
    } catch {
      return pdfUrl;
    }
  }

  /* ─── PDF fetch ─────────────────────────────────────────────────────── */

  async function fetchPdfBytes(pdfUrl) {
    const r = await fetch(pdfUrl, {
      credentials: "include",
      // Many publisher links 403 a fetch with a default Referer; the browser
      // already attaches the current page as Referer for same-origin
      // requests, which is what we want.
    });
    if (!r.ok) throw new Error("HTTP " + r.status);
    const ab = await r.arrayBuffer();
    if (!ab || ab.byteLength < 5) throw new Error("empty response");
    // Validate %PDF magic bytes
    const head = new Uint8Array(ab.slice(0, 4));
    if (!(head[0] === 0x25 && head[1] === 0x50 && head[2] === 0x44 && head[3] === 0x46)) {
      throw new Error("not a PDF");
    }
    return arrayBufferToBase64(ab);
  }

  function arrayBufferToBase64(buffer) {
    // Chunked to avoid call-stack overflow on large PDFs.
    const bytes = new Uint8Array(buffer);
    const CHUNK = 0x8000;
    let binary = "";
    for (let i = 0; i < bytes.length; i += CHUNK) {
      const slice = bytes.subarray(i, Math.min(i + CHUNK, bytes.length));
      binary += String.fromCharCode.apply(null, slice);
    }
    return btoa(binary);
  }

  /* ─── Main resolve flow ─────────────────────────────────────────────── */

  async function resolvePdfUrl() {
    let url = findCitationPdfMeta();
    if (url) return normalizePdfUrl(url);
    url = findPdfLinkBySelector();
    if (url) return normalizePdfUrl(url);
    // LLM fallback via background → server
    try {
      const anchors = harvestAnchors();
      if (anchors.length === 0) return null;
      const reply = await chrome.runtime.sendMessage({
        type: "resolve_pdf_url",
        landing_url: location.href,
        anchors,
      });
      if (reply && reply.pdf_url) return normalizePdfUrl(reply.pdf_url);
    } catch (e) {
      // background not listening, or server declined — give up
    }
    return null;
  }

  async function tryFetchAndReport() {
    let pdfUrl = null;
    try {
      pdfUrl = await resolvePdfUrl();
    } catch (e) {
      try {
        chrome.runtime.sendMessage({ type: "pdf_not_found", reason: "resolve_error: " + (e.message || e) });
      } catch {}
      return;
    }
    if (!pdfUrl) {
      try {
        chrome.runtime.sendMessage({ type: "pdf_not_found", reason: "no_pdf_link" });
      } catch {}
      return;
    }
    try {
      const pdf_b64 = await fetchPdfBytes(pdfUrl);
      try {
        chrome.runtime.sendMessage({ type: "pdf_found", pdf_b64 });
      } catch {}
    } catch (e) {
      try {
        chrome.runtime.sendMessage({ type: "pdf_not_found", reason: "fetch_error: " + (e.message || e) });
      } catch {}
    }
  }

  // Run once after document_idle (manifest already gates us to that timing).
  // A small delay gives lazy-loaded JS a chance to inject citation_pdf_url.
  setTimeout(tryFetchAndReport, 1500);
})();
