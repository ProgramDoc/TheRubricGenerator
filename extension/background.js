/**
 * The AI Researcher — PDF Fetcher (background service worker)
 *
 * Owns:
 *   - Token storage (chrome.storage.local)
 *   - Server URL config
 *   - Queue processing: open landing tabs → wait for content script → POST PDF bytes
 *   - Message routing between popup and content scripts
 *
 * Privacy contract: only fetches URLs that came from the user's queue on the
 * paired server. Never reads other tabs. Never persists PDF bytes locally.
 */

const DEFAULT_SERVER_URL = "https://therubricgenerator.onrender.com";
const PROCESS_THROTTLE_MS = 3000;        // pause between papers
const TAB_TIMEOUT_MS = 45_000;           // give the content script up to 45s per page
const STORAGE_KEYS = {
  token: "tar_ext_token",
  serverUrl: "tar_ext_server_url",
};

let processing = false;

/* ─── Storage helpers ─────────────────────────────────────────────────────── */

async function getToken() {
  const x = await chrome.storage.local.get(STORAGE_KEYS.token);
  return x[STORAGE_KEYS.token] || null;
}
async function setToken(token) {
  await chrome.storage.local.set({ [STORAGE_KEYS.token]: token });
}
async function clearToken() {
  await chrome.storage.local.remove(STORAGE_KEYS.token);
}
async function getServerUrl() {
  const x = await chrome.storage.local.get(STORAGE_KEYS.serverUrl);
  return (x[STORAGE_KEYS.serverUrl] || DEFAULT_SERVER_URL).replace(/\/+$/, "");
}
async function setServerUrl(url) {
  await chrome.storage.local.set({ [STORAGE_KEYS.serverUrl]: url });
}

/* ─── Pending tab callbacks ───────────────────────────────────────────────── */

// Map<tabId, {resolve, reject, paperId, timeoutId}>
const pendingByTab = new Map();

function _resolveTab(tabId, payload) {
  const entry = pendingByTab.get(tabId);
  if (!entry) return;
  pendingByTab.delete(tabId);
  clearTimeout(entry.timeoutId);
  entry.resolve(payload);
}

function _rejectTab(tabId, reason) {
  const entry = pendingByTab.get(tabId);
  if (!entry) return;
  pendingByTab.delete(tabId);
  clearTimeout(entry.timeoutId);
  entry.reject(new Error(reason));
}

/* ─── Server API ──────────────────────────────────────────────────────────── */

async function apiGet(path) {
  const token = await getToken();
  if (!token) throw new Error("Not paired");
  const base = await getServerUrl();
  const r = await fetch(base + path, {
    headers: { "X-API-Key": token },
  });
  if (!r.ok) {
    if (r.status === 401) throw new Error("Token rejected — repair the extension at /developers");
    throw new Error(`API ${path}: HTTP ${r.status}`);
  }
  return r.json();
}

async function apiPost(path, body) {
  const token = await getToken();
  if (!token) throw new Error("Not paired");
  const base = await getServerUrl();
  const r = await fetch(base + path, {
    method: "POST",
    headers: {
      "X-API-Key": token,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body || {}),
  });
  if (!r.ok) {
    if (r.status === 401) throw new Error("Token rejected — repair the extension at /developers");
    let detail = "";
    try { detail = (await r.json()).detail || ""; } catch {}
    throw new Error(`API ${path}: HTTP ${r.status}${detail ? " — " + detail : ""}`);
  }
  return r.json();
}

/* ─── Pairing flow ────────────────────────────────────────────────────────── */

async function pair(code) {
  const base = await getServerUrl();
  const r = await fetch(base + "/api/extension/pair", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ code }),
  });
  if (!r.ok) {
    let detail = "";
    try { detail = (await r.json()).detail || ""; } catch {}
    throw new Error(detail || `Pairing failed (${r.status})`);
  }
  const data = await r.json();
  await setToken(data.token);
  return data;
}

async function unpair() {
  await clearToken();
}

async function pingStatus() {
  return apiGet("/api/extension/status");
}

/* ─── LLM-resolve fallback (server-side helper) ───────────────────────────── */

async function llmResolvePdfUrl(landingUrl, anchors) {
  try {
    const data = await apiPost("/api/extension/resolve-pdf-url", {
      landing_url: landingUrl,
      anchors: anchors,
    });
    return data?.pdf_url || null;
  } catch (e) {
    console.warn("[TAR] llmResolvePdfUrl failed:", e);
    return null;
  }
}

/* ─── Queue processing ────────────────────────────────────────────────────── */

async function _waitForContentScript(tabId, paperId) {
  return new Promise((resolve, reject) => {
    const timeoutId = setTimeout(() => {
      pendingByTab.delete(tabId);
      reject(new Error("timeout"));
    }, TAB_TIMEOUT_MS);
    pendingByTab.set(tabId, { resolve, reject, paperId, timeoutId });
  });
}

async function processOnePaper(paper, popupPort) {
  const landing = paper.landing_url;
  if (!landing) {
    await apiPost(`/api/extension/papers/${paper.paper_id}/skip`, {});
    popupPort?.postMessage({ type: "paper_skipped", paper_id: paper.paper_id, reason: "no_landing_url" });
    return { status: "skipped", paper_id: paper.paper_id };
  }

  popupPort?.postMessage({
    type: "paper_started",
    paper_id: paper.paper_id,
    title: paper.title,
    landing_url: landing,
  });

  // Open the landing page in a non-active tab so user disruption is minimized.
  const tab = await chrome.tabs.create({ url: landing, active: false });
  let result;
  try {
    result = await _waitForContentScript(tab.id, paper.paper_id);
  } catch (e) {
    result = { ok: false, error: e.message || "timeout" };
  } finally {
    try { await chrome.tabs.remove(tab.id); } catch {}
  }

  if (result?.ok && result.pdf_b64) {
    try {
      await apiPost(`/api/extension/papers/${paper.paper_id}/pdf`, {
        pdf_b64: result.pdf_b64,
      });
      popupPort?.postMessage({ type: "paper_done", paper_id: paper.paper_id });
      return { status: "done", paper_id: paper.paper_id };
    } catch (e) {
      popupPort?.postMessage({ type: "paper_failed", paper_id: paper.paper_id, error: e.message });
      // Fall through to skip so the queue clears
    }
  }
  // Anything else → skip server-side so the queue clears.
  try {
    await apiPost(`/api/extension/papers/${paper.paper_id}/skip`, {});
  } catch (e) {
    console.warn("[TAR] skip failed:", e);
  }
  popupPort?.postMessage({
    type: "paper_skipped",
    paper_id: paper.paper_id,
    reason: result?.error || "no_pdf",
  });
  return { status: "skipped", paper_id: paper.paper_id };
}

async function processQueue(popupPort) {
  if (processing) return { ok: false, error: "already_processing" };
  processing = true;
  popupPort?.postMessage({ type: "run_started" });
  try {
    const data = await apiGet("/api/extension/queue?limit=50");
    const papers = data.papers || [];
    let done = 0, skipped = 0;
    for (let i = 0; i < papers.length; i++) {
      const out = await processOnePaper(papers[i], popupPort);
      if (out.status === "done") done++;
      else skipped++;
      if (i < papers.length - 1) {
        await new Promise(res => setTimeout(res, PROCESS_THROTTLE_MS));
      }
    }
    popupPort?.postMessage({ type: "run_complete", done, skipped, total: papers.length });
    return { ok: true, done, skipped, total: papers.length };
  } catch (e) {
    popupPort?.postMessage({ type: "run_error", error: e.message });
    return { ok: false, error: e.message };
  } finally {
    processing = false;
  }
}

/* ─── Message dispatch ────────────────────────────────────────────────────── */

// Long-lived port from the popup (so we can stream progress events back).
let popupPort = null;

chrome.runtime.onConnect.addListener((port) => {
  if (port.name !== "popup") return;
  popupPort = port;
  port.onDisconnect.addListener(() => { if (popupPort === port) popupPort = null; });
});

// One-shot messages (from popup or content scripts).
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  (async () => {
    try {
      switch (msg?.type) {
        case "get_state": {
          const token = await getToken();
          const serverUrl = await getServerUrl();
          const out = { paired: !!token, server_url: serverUrl, processing };
          if (token) {
            try { Object.assign(out, await pingStatus()); } catch (e) { out.error = e.message; }
          }
          sendResponse(out);
          return;
        }
        case "set_server_url": {
          await setServerUrl(msg.url);
          sendResponse({ ok: true });
          return;
        }
        case "pair": {
          const data = await pair(msg.code);
          sendResponse({ ok: true, paired_at: data.paired_at });
          return;
        }
        case "unpair": {
          await unpair();
          sendResponse({ ok: true });
          return;
        }
        case "process_queue": {
          const out = await processQueue(popupPort);
          sendResponse(out);
          return;
        }
        case "resolve_pdf_url": {
          // Content script asks the server to LLM-pick a PDF link.
          const url = await llmResolvePdfUrl(msg.landing_url, msg.anchors || []);
          sendResponse({ pdf_url: url });
          return;
        }
        case "pdf_found": {
          if (sender.tab && sender.tab.id != null) {
            _resolveTab(sender.tab.id, { ok: true, pdf_b64: msg.pdf_b64 });
          }
          sendResponse({ ack: true });
          return;
        }
        case "pdf_not_found": {
          if (sender.tab && sender.tab.id != null) {
            _resolveTab(sender.tab.id, { ok: false, error: msg.reason || "no_pdf" });
          }
          sendResponse({ ack: true });
          return;
        }
        default: {
          sendResponse({ ok: false, error: "unknown_message" });
        }
      }
    } catch (e) {
      sendResponse({ ok: false, error: e.message || String(e) });
    }
  })();
  return true;  // keep the channel open for the async response
});

// If a tab we were waiting on is closed externally, reject its promise.
chrome.tabs.onRemoved.addListener((tabId) => {
  if (pendingByTab.has(tabId)) _rejectTab(tabId, "tab_closed");
});
