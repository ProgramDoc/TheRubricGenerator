/**
 * The AI Researcher — PDF Fetcher (popup)
 *
 * - Renders one of two views (pair / connected) based on storage state.
 * - Sends pair / unpair / process_queue messages to the background worker.
 * - Listens on a long-lived port for streaming progress events while a run is in flight.
 */

const $ = (id) => document.getElementById(id);

let port = null;
let progress = { done: 0, skipped: 0, total: 0 };

function showSection(name) {
  document.getElementById("pair-section").classList.toggle("hidden", name !== "pair");
  document.getElementById("connected-section").classList.toggle("hidden", name !== "connected");
}

async function loadState() {
  const reply = await chrome.runtime.sendMessage({ type: "get_state" });
  if (!reply) return;
  $("server-url").value = reply.server_url || "";
  if (reply.paired) {
    showSection("connected");
    const count = reply.queue_count || 0;
    $("queue-count").textContent = count;
    $("queue-plural").textContent = count === 1 ? "" : "s";
    $("process-btn").disabled = count === 0 || reply.processing;
    if (reply.processing) {
      $("process-btn").textContent = "Processing…";
    }
  } else {
    showSection("pair");
  }
}

/* ─── Pair flow ─────────────────────────────────────────────────────────── */

$("pair-btn").addEventListener("click", async () => {
  const code = $("pair-input").value.trim();
  if (!code) return;
  $("pair-btn").disabled = true;
  $("pair-btn").textContent = "Pairing…";
  $("pair-error").classList.add("hidden");
  // Persist server URL first
  const url = $("server-url").value.trim();
  if (url) {
    await chrome.runtime.sendMessage({ type: "set_server_url", url });
  }
  const reply = await chrome.runtime.sendMessage({ type: "pair", code });
  $("pair-btn").disabled = false;
  $("pair-btn").textContent = "Pair";
  if (reply?.ok) {
    await loadState();
  } else {
    $("pair-error").textContent = reply?.error || "Pairing failed";
    $("pair-error").classList.remove("hidden");
  }
});

$("server-url").addEventListener("change", async (ev) => {
  await chrome.runtime.sendMessage({ type: "set_server_url", url: ev.target.value.trim() });
});

$("open-developers")?.addEventListener("click", async (ev) => {
  ev.preventDefault();
  // Best guess: open the developers page on the configured server
  const reply = await chrome.runtime.sendMessage({ type: "get_state" });
  const base = (reply?.server_url || "").replace(/\/+$/, "");
  if (base) chrome.tabs.create({ url: base + "/developers" });
});

/* ─── Connected flow ────────────────────────────────────────────────────── */

$("refresh-btn").addEventListener("click", () => loadState());

$("unpair-btn").addEventListener("click", async () => {
  if (!confirm("Unpair the extension? You'll need a new pairing code to reconnect.")) return;
  await chrome.runtime.sendMessage({ type: "unpair" });
  await loadState();
});

$("process-btn").addEventListener("click", async () => {
  $("process-btn").disabled = true;
  $("process-btn").textContent = "Processing…";
  resetProgress();
  $("progress").classList.remove("hidden");
  // Open a long-lived port so the background worker can stream events
  port = chrome.runtime.connect({ name: "popup" });
  port.onMessage.addListener(handleProgressEvent);
  // Trigger the run; sendResponse arrives only after it completes.
  const result = await chrome.runtime.sendMessage({ type: "process_queue" });
  port?.disconnect();
  port = null;
  $("process-btn").disabled = false;
  $("process-btn").textContent = "Process queue";
  if (result?.ok) {
    appendEvent("ev-done", `✓ Run complete: ${result.done} fetched, ${result.skipped} skipped`);
  } else if (result?.error) {
    appendEvent("ev-err", "✗ " + result.error);
  }
  await loadState();
});

function resetProgress() {
  progress = { done: 0, skipped: 0, total: 0 };
  $("progress-now").textContent = "Starting…";
  $("progress-fill").style.width = "0%";
  $("progress-done").textContent = "0";
  $("progress-skipped").textContent = "0";
  $("progress-total").textContent = "0";
  $("event-log").innerHTML = "";
}

function appendEvent(cls, text) {
  const li = document.createElement("li");
  li.className = cls;
  li.innerHTML = `<span class="ev-icon">${cls === "ev-done" ? "✓" : cls === "ev-skip" ? "↪" : cls === "ev-err" ? "✗" : "•"}</span><span class="ev-title"></span>`;
  li.querySelector(".ev-title").textContent = text;
  $("event-log").prepend(li);
}

function handleProgressEvent(ev) {
  switch (ev.type) {
    case "run_started":
      $("progress-now").textContent = "Loading queue…";
      break;
    case "paper_started":
      $("progress-now").textContent = (ev.title || "(untitled)").slice(0, 60);
      appendEvent("ev-pending", "→ " + (ev.title || ev.landing_url));
      break;
    case "paper_done":
      progress.done++;
      $("progress-done").textContent = progress.done;
      appendEvent("ev-done", `✓ paper ${ev.paper_id}`);
      updateProgressBar();
      break;
    case "paper_skipped":
      progress.skipped++;
      $("progress-skipped").textContent = progress.skipped;
      appendEvent("ev-skip", `↪ paper ${ev.paper_id} — ${ev.reason || "skipped"}`);
      updateProgressBar();
      break;
    case "paper_failed":
      appendEvent("ev-err", `✗ paper ${ev.paper_id} — ${ev.error || "failed"}`);
      break;
    case "run_complete":
      progress.total = ev.total;
      $("progress-total").textContent = progress.total;
      $("progress-now").textContent = `Done: ${ev.done} fetched, ${ev.skipped} skipped`;
      updateProgressBar();
      break;
    case "run_error":
      appendEvent("ev-err", "✗ " + (ev.error || "run error"));
      break;
  }
}

function updateProgressBar() {
  const total = progress.total || (progress.done + progress.skipped);
  if (!total) return;
  const pct = Math.min(100, Math.round(((progress.done + progress.skipped) / total) * 100));
  $("progress-fill").style.width = pct + "%";
}

loadState();
