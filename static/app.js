/**
 * Captain Schedule Predictor — frontend dashboard logic.
 *
 * Communicates with the FastAPI backend at /api/* to:
 *   - Upload dispatch XML files
 *   - Display captain shift predictions and busy-day calendar
 *   - Show upload history and raw stored schedules
 *
 * All API calls use relative paths so the app works on any host/port.
 */
const API = "";

// DOM helpers
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

/** Format an ISO date string (YYYY-MM-DD) for display. */
function formatDate(iso) {
  const d = new Date(iso + "T00:00:00");
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

/** Map a busy score (0–1) to a CSS class for calendar coloring. */
function busyClass(score) {
  if (score >= 0.65) return "busy-high";
  if (score >= 0.35) return "busy-med";
  return "busy-low";
}

/** Map a busy score (0–1) to a human-readable label. */
function busyLabel(score) {
  if (score >= 0.65) return "High";
  if (score >= 0.35) return "Medium";
  return "Low";
}

/** Render an inline confidence bar for the predictions table. */
function confidenceBar(pct) {
  const width = Math.round(pct * 100);
  return `<div class="confidence-bar">
    <div class="confidence-track"><div class="confidence-fill" style="width:${width}%"></div></div>
    <span>${width}%</span>
  </div>`;
}

/** Extract a readable error message from an API response (JSON or plain text). */
async function readErrorDetail(res) {
  const contentType = res.headers.get("content-type") || "";
  if (contentType.includes("application/json")) {
    const data = await res.json();
    if (Array.isArray(data.detail)) {
      return data.detail.map((d) => d.msg || d).join(", ");
    }
    return data.detail || `Request failed (${res.status})`;
  }
  const text = await res.text();
  return text || `Request failed (${res.status})`;
}

/** Fetch JSON from the API, throwing with a readable message on failure. */
async function fetchJSON(path) {
  const res = await fetch(API + path);
  if (!res.ok) {
    throw new Error(await readErrorDetail(res));
  }
  return res.json();
}

/** Load dashboard header statistics from GET /api/stats. */
async function loadStats() {
  try {
    const s = await fetchJSON("/api/stats");
    $("#statEntries").textContent = s.total_entries.toLocaleString();
    $("#statShips").textContent = s.unique_ships;
    $("#statCaptains").textContent = s.unique_captains;
    if (s.date_range_start && s.date_range_end) {
      $("#statRange").textContent =
        formatDate(s.date_range_start) + " – " + formatDate(s.date_range_end);
    } else {
      $("#statRange").textContent = "—";
    }
  } catch (e) {
    console.error("Stats error:", e);
  }
}

/** Build query string from the forecast filter controls. */
function queryParams() {
  const captain = $("#captainFilter").value;
  const days = $("#daysAhead").value;
  const conf = $("#minConfidence").value;
  let q = `?days_ahead=${days}&min_confidence=${conf}`;
  if (captain) q += `&boat_code=${encodeURIComponent(captain)}`;
  return q;
}

/** Populate the captain dropdown filter from GET /api/captains. */
async function loadCaptainsFilter() {
  try {
    const captains = await fetchJSON("/api/captains?days_ahead=90");
    const sel = $("#captainFilter");
    const current = sel.value;
    sel.innerHTML = '<option value="">All captains</option>';
    const codes = [...new Set(captains.map((c) => c.boat_code))].sort();
    for (const code of codes) {
      const opt = document.createElement("option");
      opt.value = code;
      opt.textContent = code;
      sel.appendChild(opt);
    }
    if (codes.includes(current)) sel.value = current;
  } catch (e) {
    console.error("Captains filter error:", e);
  }
}

/** Load and render the predictions table from GET /api/predictions. */
async function loadPredictions() {
  const tbody = $("#predictionsBody");
  try {
    const data = await fetchJSON("/api/predictions" + queryParams());
    if (!data.length) {
      tbody.innerHTML = '<tr><td colspan="8" class="empty">No predictions — upload historical XML data first</td></tr>';
      return;
    }
    tbody.innerHTML = data.slice(0, 200).map((p) => `
      <tr>
        <td>${formatDate(p.schedule_date)}</td>
        <td>${p.day_of_week}</td>
        <td><code>${p.boat_code}</code></td>
        <td>${p.ship}</td>
        <td>${p.checkin_time}</td>
        <td>${p.return_time}</td>
        <td>${confidenceBar(p.confidence)}</td>
        <td><span class="busy-badge ${busyClass(p.busy_score)}">${busyLabel(p.busy_score)}</span></td>
      </tr>
    `).join("");
    if (data.length > 200) {
      tbody.innerHTML += `<tr><td colspan="8" class="empty">Showing first 200 of ${data.length} predictions</td></tr>`;
    }
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="8" class="empty">Error loading predictions</td></tr>`;
  }
}

/** Load captain overview cards from GET /api/captains. */
async function loadCaptainOverview() {
  const grid = $("#captainGrid");
  try {
    const days = $("#daysAhead").value;
    const data = await fetchJSON(`/api/captains?days_ahead=${days}`);
    if (!data.length) {
      grid.innerHTML = '<p class="empty">No captain data yet</p>';
      return;
    }
    grid.innerHTML = data.map((c) => {
      const next = c.next_shift;
      const nextHtml = next
        ? `<strong>${formatDate(next.schedule_date)}</strong> · ${next.ship}<br/>
           ${next.checkin_time} – ${next.return_time}<br/>
           Confidence: ${Math.round(next.confidence * 100)}%`
        : "No upcoming shifts predicted";
      return `
        <div class="captain-card">
          <h3>${c.boat_code}</h3>
          <div class="meta">${c.total_historical_shifts} historical · ${c.predicted_shifts} predicted (${days}d)</div>
          <div class="next">${nextHtml}</div>
        </div>`;
    }).join("");
  } catch (e) {
    grid.innerHTML = '<p class="empty">Error loading captain overview</p>';
  }
}

/** Load the busy-day calendar grid from GET /api/busy-calendar. */
async function loadCalendar() {
  const grid = $("#calendarGrid");
  try {
    const days = $("#daysAhead").value;
    const data = await fetchJSON(`/api/busy-calendar?days_ahead=${days}`);
    grid.innerHTML = data.map((d) => {
      const dateObj = new Date(d.date + "T00:00:00");
      const intensity = Math.round(d.busy_score * 100);
      const bg = `rgba(59, 130, 246, ${0.08 + d.busy_score * 0.45})`;
      return `
        <div class="cal-day${d.has_actual_data ? " actual" : ""}" style="background:${bg}" title="${d.passenger_estimate.toLocaleString()} passengers est.">
          <div class="date-num">${dateObj.getDate()}</div>
          <div class="dow">${d.day_of_week.slice(0, 3)}</div>
          <div class="ships">${d.ship_count} ships</div>
        </div>`;
    }).join("");
  } catch (e) {
    grid.innerHTML = '<p class="empty">Error loading calendar</p>';
  }
}

/** Load XML upload history from GET /api/uploads. */
async function loadUploads() {
  const tbody = $("#uploadsBody");
  try {
    const data = await fetchJSON("/api/uploads");
    if (!data.length) {
      tbody.innerHTML = '<tr><td colspan="5" class="empty">No uploads yet</td></tr>';
      return;
    }
    tbody.innerHTML = data.map((u) => `
      <tr>
        <td>${new Date(u.uploaded_at).toLocaleString()}</td>
        <td>${u.filename}</td>
        <td>${u.rows_imported}</td>
        <td>${u.rows_skipped}</td>
        <td>${u.notes || "—"}</td>
      </tr>
    `).join("");
  } catch (e) {
    tbody.innerHTML = '<tr><td colspan="5" class="empty">Error loading uploads</td></tr>';
  }
}

/** Load raw stored schedule rows from GET /api/schedules. */
async function loadSchedules() {
  const tbody = $("#schedulesBody");
  try {
    const data = await fetchJSON("/api/schedules?limit=200");
    if (!data.length) {
      tbody.innerHTML = '<tr><td colspan="6" class="empty">No schedules stored</td></tr>';
      return;
    }
    tbody.innerHTML = data.map((s) => `
      <tr>
        <td>${formatDate(s.schedule_date)}</td>
        <td>${s.date_header}</td>
        <td>${s.ship}</td>
        <td>${s.checkin_time}</td>
        <td>${s.return_time}</td>
        <td><code>${s.boat_codes}</code></td>
      </tr>
    `).join("");
  } catch (e) {
    tbody.innerHTML = '<tr><td colspan="6" class="empty">Error loading schedules</td></tr>';
  }
}

/** Refresh all dashboard panels (called on load and after upload). */
/** Load OpenAI integration status from GET /api/health. */
async function loadAiStatus() {
  const badge = $("#aiStatusBadge");
  if (!badge) return;
  try {
    const data = await fetchJSON("/api/health");
    const ai = data.ai || {};
    badge.classList.remove("connected", "disabled", "error");

    if (ai.enabled && ai.connected) {
      badge.classList.add("connected");
      badge.textContent = `AI recovery active · ${ai.model}`;
    } else if (ai.enabled) {
      badge.classList.add("error");
      badge.textContent = `AI configured but unavailable · ${ai.message}`;
    } else {
      badge.classList.add("disabled");
      badge.textContent = "AI recovery disabled · set OPENAI_API_KEY to enable";
    }
  } catch (e) {
    badge.classList.add("error");
    badge.textContent = "Could not check AI status";
  }
}

async function refreshAll() {
  await Promise.all([
    loadStats(),
    loadAiStatus(),
    loadCaptainsFilter(),
    loadPredictions(),
    loadCaptainOverview(),
    loadCalendar(),
    loadUploads(),
    loadSchedules(),
  ]);
}

/** POST an XML file to /api/upload and refresh the dashboard on success. */
async function uploadFile(file) {
  const resultEl = $("#uploadResult");
  resultEl.classList.remove("hidden", "success", "error");

  const form = new FormData();
  const filename = file.name || "upload.xml";
  form.append("file", file, filename);

  try {
    const res = await fetch(API + "/api/upload", { method: "POST", body: form });
    if (!res.ok) {
      throw new Error(await readErrorDetail(res));
    }
    const data = await res.json();

    resultEl.classList.add("success");
    const summary = data.rows_imported
      ? `✓ Imported <strong>${data.rows_imported}</strong> rows from <strong>${data.filename}</strong>`
      : `✓ Updated <strong>${data.rows_skipped}</strong> existing rows from <strong>${data.filename}</strong>`;
    resultEl.innerHTML = summary +
      (data.rows_skipped && data.rows_imported ? ` (${data.rows_skipped} updated)` : "") +
      (data.notes ? `<br><small>${data.notes}</small>` : "");

    await refreshAll();
  } catch (e) {
    resultEl.classList.add("error");
    resultEl.textContent = "Upload failed: " + e.message;
  }
}

let lastCleanedXml = "";

/** Send raw XML to the clean/repair API and display results. */
async function cleanRawXml(xmlText) {
  const summaryEl = $("#repairSummary");
  const repairsBody = $("#repairsBody");
  const outputEl = $("#cleanedXmlOutput");
  const copyBtn = $("#copyCleanXmlBtn");

  summaryEl.classList.add("hidden");
  repairsBody.innerHTML = '<tr><td colspan="6" class="empty">Analyzing...</td></tr>';

  try {
    const res = await fetch(API + "/api/clean-xml/json", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ xml: xmlText }),
    });
    if (!res.ok) {
      throw new Error(await readErrorDetail(res));
    }
    const data = await res.json();

    lastCleanedXml = data.cleaned_xml || "";
    outputEl.textContent = lastCleanedXml || "No output produced.";
    copyBtn.disabled = !lastCleanedXml;

    summaryEl.classList.remove("hidden");
    summaryEl.innerHTML = `
      <strong>Analysis complete.</strong>
      ${data.entries_processed} entries ·
      ${data.times_normalized} times normalized ·
      ${data.boat_fields_repaired} boat fields repaired ·
      parser: ${data.parse_method}${data.ai_assisted ? " (AI-assisted)" : ""}
    `;

    if (!data.repairs.length) {
      repairsBody.innerHTML = '<tr><td colspan="6" class="empty">No repairs needed — XML was already clean</td></tr>';
    } else {
      repairsBody.innerHTML = data.repairs.map((r) => `
        <tr>
          <td>${r.entry_index}</td>
          <td><code>${r.field}</code></td>
          <td>${r.issue}</td>
          <td>${r.before}</td>
          <td>${r.after}</td>
          <td>${Math.round(r.confidence * 100)}%</td>
        </tr>
      `).join("");
    }
  } catch (e) {
    repairsBody.innerHTML = `<tr><td colspan="6" class="empty">Repair failed: ${e.message}</td></tr>`;
    outputEl.textContent = "Error during repair.";
    copyBtn.disabled = true;
    lastCleanedXml = "";
  }
}

/** Wire up the XML repair tab controls. */
function setupRepair() {
  $("#cleanXmlBtn").addEventListener("click", () => {
    const xml = $("#rawXmlInput").value.trim();
    if (!xml) {
      alert("Paste raw XML first");
      return;
    }
    cleanRawXml(xml);
  });

  $("#copyCleanXmlBtn").addEventListener("click", async () => {
    if (!lastCleanedXml) return;
    await navigator.clipboard.writeText(lastCleanedXml);
    $("#copyCleanXmlBtn").textContent = "Copied!";
    setTimeout(() => { $("#copyCleanXmlBtn").textContent = "Copy Cleaned XML"; }, 1500);
  });

  $("#loadRepairFileBtn").addEventListener("click", () => $("#repairFileInput").click());

  $("#repairFileInput").addEventListener("change", () => {
    const file = $("#repairFileInput").files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      $("#rawXmlInput").value = reader.result;
      cleanRawXml(String(reader.result));
    };
    reader.readAsText(file);
    $("#repairFileInput").value = "";
  });
}

/** Wire up drag-and-drop and file picker for XML upload. */
function setupUpload() {
  const zone = $("#uploadZone");
  const input = $("#fileInput");

  $("#browseBtn").addEventListener("click", (e) => {
    e.stopPropagation();
    input.click();
  });

  zone.addEventListener("click", () => input.click());

  input.addEventListener("change", () => {
    if (input.files[0]) uploadFile(input.files[0]);
    input.value = "";
  });

  zone.addEventListener("dragover", (e) => {
    e.preventDefault();
    zone.classList.add("dragover");
  });
  zone.addEventListener("dragleave", () => zone.classList.remove("dragover"));
  zone.addEventListener("drop", (e) => {
    e.preventDefault();
    zone.classList.remove("dragover");
    if (e.dataTransfer.files[0]) uploadFile(e.dataTransfer.files[0]);
  });
}

/** Wire up tab navigation between dashboard panels. */
function setupTabs() {
  $$(".tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      $$(".tab").forEach((t) => t.classList.remove("active"));
      $$(".tab-panel").forEach((p) => p.classList.remove("active"));
      tab.classList.add("active");
      $("#panel-" + tab.dataset.tab).classList.add("active");
    });
  });
}

/** Wire up forecast filter controls and the refresh button. */
function setupControls() {
  ["captainFilter", "daysAhead", "minConfidence"].forEach((id) => {
    $("#" + id).addEventListener("change", () => {
      loadPredictions();
      loadCaptainOverview();
      loadCalendar();
    });
  });
  $("#refreshBtn").addEventListener("click", refreshAll);
}

document.addEventListener("DOMContentLoaded", () => {
  setupUpload();
  setupRepair();
  setupTabs();
  setupControls();
  refreshAll();
});
