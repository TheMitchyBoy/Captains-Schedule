/**
 * Captain Schedule Predictor — frontend dashboard logic.
 *
 * Workflow:
 *   - Upload XML once (or a few times) — data is saved to SQLite permanently
 *   - Return anytime: predictions load from the database without re-uploading
 *   - Optional: clean/repair raw XML before import
 */
const API = "";

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

let lastCleanedXml = "";

function formatDate(iso) {
  const d = new Date(iso + "T00:00:00");
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

function busyClass(score) {
  if (score >= 0.65) return "busy-high";
  if (score >= 0.35) return "busy-med";
  return "busy-low";
}

function busyLabel(score) {
  if (score >= 0.65) return "High";
  if (score >= 0.35) return "Medium";
  return "Low";
}

function confidenceBar(pct) {
  const width = Math.round(pct * 100);
  return `<div class="confidence-bar">
    <div class="confidence-track"><div class="confidence-fill" style="width:${width}%"></div></div>
    <span>${width}%</span>
  </div>`;
}

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

async function fetchJSON(path) {
  const res = await fetch(API + path);
  if (!res.ok) {
    throw new Error(await readErrorDetail(res));
  }
  return res.json();
}

/** Switch to a dashboard tab programmatically. */
function switchTab(tabName) {
  $$(".tab").forEach((t) => {
    t.classList.toggle("active", t.dataset.tab === tabName);
  });
  $$(".tab-panel").forEach((p) => {
    p.classList.toggle("active", p.id === `panel-${tabName}`);
  });
  const panel = $(`#panel-${tabName}`);
  if (panel) {
    panel.scrollIntoView({ behavior: "smooth", block: "start" });
  }
}

/** Enable or disable cleaned-output action buttons. */
function setCleanedOutputActions(enabled) {
  $("#copyCleanXmlBtn").disabled = !enabled;
  $("#pushPredictionsBtn").disabled = !enabled;
}

async function loadStats() {
  try {
    const s = await fetchJSON("/api/stats");
    $("#statEntries").textContent = s.total_entries.toLocaleString();
    $("#statShips").textContent = s.unique_ships;
    $("#statCaptains").textContent = s.unique_captains;
    $("#statUploads").textContent = s.uploads ?? "—";
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

async function loadStorageStatus() {
  const banner = $("#storageBanner");
  if (!banner) return;
  try {
    const s = await fetchJSON("/api/storage");
    banner.classList.remove("hidden", "ready", "empty");
    if (s.ready_for_predictions) {
      banner.classList.add("ready");
      const last = s.last_upload_at
        ? `Last upload: ${new Date(s.last_upload_at).toLocaleString()}`
        : "";
      banner.innerHTML = `<strong>Database ready.</strong> ${s.total_entries.toLocaleString()} schedule rows saved · ${s.patterns_learned} patterns learned · ${s.uploads} upload(s). ${last} — open anytime for predictions without re-uploading.`;
    } else {
      banner.classList.add("empty");
      banner.innerHTML = `<strong>No saved data yet.</strong> ${s.message}`;
    }
  } catch (e) {
    banner.classList.add("hidden");
  }
}

function queryParams() {
  const captain = $("#captainFilter").value;
  const days = $("#daysAhead").value;
  const conf = $("#minConfidence").value;
  let q = `?days_ahead=${days}&min_confidence=${conf}`;
  if (captain) q += `&boat_code=${encodeURIComponent(captain)}`;
  return q;
}

function formatBoatCodes(value) {
  if (!value) return "—";
  const codes = value.split(/[,;/]+|\s+and\s+/i).map((c) => c.trim()).filter(Boolean);
  if (!codes.length) return "—";
  return codes.map((c) => `<code>${c}</code>`).join(" ");
}

function escapeAttr(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/"/g, "&quot;")
    .replace(/</g, "&lt;");
}

let scheduleRows = [];
let editingScheduleId = null;

function renderScheduleRow(s) {
  if (editingScheduleId === s.id) {
    return `
      <tr class="schedule-row-editing" data-id="${s.id}">
        <td>${formatDate(s.schedule_date)}</td>
        <td>${s.date_header}</td>
        <td>${s.ship}</td>
        <td><input type="text" class="schedule-edit-input" data-field="checkin_time" value="${escapeAttr(s.checkin_time)}" aria-label="Check-in time" /></td>
        <td><input type="text" class="schedule-edit-input" data-field="return_time" value="${escapeAttr(s.return_time)}" aria-label="Return time" /></td>
        <td>${s.berth ? `<code>${s.berth}</code>` : "—"}</td>
        <td><input type="text" class="schedule-edit-input schedule-edit-boats" data-field="boat_codes" value="${escapeAttr(s.boat_codes)}" placeholder="BW, BWA, JR" aria-label="Boat codes" /></td>
        <td class="schedule-actions">
          <button type="button" class="btn btn-sm schedule-save-btn" data-id="${s.id}">Save</button>
          <button type="button" class="btn secondary btn-sm schedule-cancel-btn" data-id="${s.id}">Cancel</button>
        </td>
      </tr>`;
  }
  return `
    <tr data-id="${s.id}">
      <td>${formatDate(s.schedule_date)}</td>
      <td>${s.date_header}</td>
      <td>${s.ship}</td>
      <td>${s.checkin_time}</td>
      <td>${s.return_time}</td>
      <td>${s.berth ? `<code>${s.berth}</code>` : "—"}</td>
      <td>${formatBoatCodes(s.boat_codes)}</td>
      <td class="schedule-actions">
        <button type="button" class="btn secondary btn-sm schedule-edit-btn" data-id="${s.id}">Edit</button>
      </td>
    </tr>`;
}

function renderSchedulesTable() {
  const tbody = $("#schedulesBody");
  if (!scheduleRows.length) {
    tbody.innerHTML = '<tr><td colspan="8" class="empty">No schedules stored</td></tr>';
    return;
  }
  tbody.innerHTML = scheduleRows.map(renderScheduleRow).join("");
}

async function saveScheduleEdit(id) {
  const row = document.querySelector(`tr.schedule-row-editing[data-id="${id}"]`);
  if (!row) return;

  const payload = {};
  row.querySelectorAll(".schedule-edit-input").forEach((input) => {
    payload[input.dataset.field] = input.value.trim();
  });

  const saveBtn = row.querySelector(".schedule-save-btn");
  saveBtn.disabled = true;
  try {
    const res = await fetch(API + `/api/schedules/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) throw new Error(await readErrorDetail(res));
    const updated = await res.json();
    scheduleRows = scheduleRows.map((s) => (s.id === id ? updated : s));
    editingScheduleId = null;
    renderSchedulesTable();
    await Promise.all([loadStats(), loadPredictions(), loadCaptainOverview()]);
  } catch (e) {
    alert("Could not save changes: " + e.message);
  } finally {
    saveBtn.disabled = false;
  }
}

function setupScheduleEditing() {
  const tbody = $("#schedulesBody");
  if (!tbody) return;
  tbody.addEventListener("click", (e) => {
    const editBtn = e.target.closest(".schedule-edit-btn");
    if (editBtn) {
      editingScheduleId = Number(editBtn.dataset.id);
      renderSchedulesTable();
      const input = tbody.querySelector(`tr[data-id="${editingScheduleId}"] .schedule-edit-input`);
      input?.focus();
      return;
    }
    const cancelBtn = e.target.closest(".schedule-cancel-btn");
    if (cancelBtn) {
      editingScheduleId = null;
      renderSchedulesTable();
      return;
    }
    const saveBtn = e.target.closest(".schedule-save-btn");
    if (saveBtn) {
      saveScheduleEdit(Number(saveBtn.dataset.id));
    }
  });
}

function groupPredictionsByShip(rows) {
  const groups = new Map();
  for (const row of rows) {
    const key = `${row.schedule_date}|${row.ship}|${row.checkin_time}|${row.return_time}`;
    if (!groups.has(key)) {
      groups.set(key, { ...row, boats: [] });
    }
    groups.get(key).boats.push(row);
  }
  return [...groups.values()];
}

function sourceLabel(source) {
  if (source === "ai") return '<span class="source-badge ai">AI</span>';
  return '<span class="source-badge pattern">Pattern</span>';
}

function showAiPredictionBanner(meta) {
  const banner = $("#predictionsAiBanner");
  if (!banner) return;
  if (!meta || !meta.ai_assisted) {
    banner.classList.add("hidden");
    return;
  }
  banner.classList.remove("hidden");
  const parts = [];
  if (meta.ai_ship_forecasts) parts.push(`${meta.ai_ship_forecasts} ship forecasts`);
  if (meta.ai_captain_suggestions) parts.push(`${meta.ai_captain_suggestions} AI assignments`);
  banner.innerHTML = `<strong>AI-enhanced predictions.</strong> ${parts.join(" · ") || meta.message || "OpenAI contributed to this forecast."}`;
}

async function loadCaptainsFilter() {
  try {
    const resp = await fetchJSON("/api/captains?days_ahead=90");
    const captains = resp.captains || resp;
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

async function loadPredictions() {
  const tbody = $("#predictionsBody");
  try {
    const resp = await fetchJSON("/api/predictions" + queryParams());
    const data = resp.predictions || resp;
    showAiPredictionBanner(resp.ai);
    if (!data.length) {
      tbody.innerHTML = '<tr><td colspan="9" class="empty">No predictions yet — upload XML above to save schedule data to the database</td></tr>';
      return;
    }
    const grouped = groupPredictionsByShip(data);
    tbody.innerHTML = grouped.slice(0, 200).map((group) => {
      const boats = group.boats
        .sort((a, b) => a.boat_code.localeCompare(b.boat_code))
        .map((p) => `<code>${p.boat_code}</code>`)
        .join(" ");
      const top = group.boats.reduce((best, p) => (p.confidence > best.confidence ? p : best), group.boats[0]);
      const hasAi = group.boats.some((p) => p.source === "ai");
      return `
      <tr>
        <td>${formatDate(group.schedule_date)}</td>
        <td>${group.day_of_week}</td>
        <td>${boats}</td>
        <td>${group.ship}</td>
        <td>${group.checkin_time}</td>
        <td>${group.return_time}</td>
        <td>${confidenceBar(top.confidence)}</td>
        <td>${hasAi ? sourceLabel("ai") : sourceLabel("pattern")}</td>
        <td><span class="busy-badge ${busyClass(top.busy_score)}">${busyLabel(top.busy_score)}</span></td>
      </tr>
    `;
    }).join("");
    if (grouped.length > 200) {
      tbody.innerHTML += `<tr><td colspan="9" class="empty">Showing first 200 of ${grouped.length} ship assignments</td></tr>`;
    }
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="9" class="empty">Error loading predictions</td></tr>`;
  }
}

async function loadCaptainOverview() {
  const grid = $("#captainGrid");
  try {
    const days = $("#daysAhead").value;
    const resp = await fetchJSON(`/api/captains?days_ahead=${days}`);
    const data = resp.captains || resp;
    showAiPredictionBanner(resp.ai);
    if (!data.length) {
      grid.innerHTML = '<p class="empty">No captain data yet</p>';
      return;
    }
    grid.innerHTML = data.map((c) => {
      const next = c.next_shift;
      const nextHtml = next
        ? `<strong>${formatDate(next.schedule_date)}</strong> · ${next.ship}<br/>
           ${next.checkin_time} – ${next.return_time}<br/>
           Confidence: ${Math.round(next.confidence * 100)}% · ${next.source === "ai" ? "AI" : "Pattern"}`
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

async function loadCalendar() {
  const grid = $("#calendarGrid");
  try {
    const days = $("#daysAhead").value;
    const resp = await fetchJSON(`/api/busy-calendar?days_ahead=${days}`);
    const data = resp.calendar || resp;
    grid.innerHTML = data.map((d) => {
      const dateObj = new Date(d.date + "T00:00:00");
      const bg = `rgba(59, 130, 246, ${0.08 + d.busy_score * 0.45})`;
      const aiHint = d.ai_forecast ? " · AI ship forecast" : "";
      return `
        <div class="cal-day${d.has_actual_data ? " actual" : ""}${d.ai_forecast ? " ai-forecast" : ""}" style="background:${bg}" title="${d.passenger_estimate.toLocaleString()} passengers est.${aiHint}">
          <div class="date-num">${dateObj.getDate()}</div>
          <div class="dow">${d.day_of_week.slice(0, 3)}</div>
          <div class="ships">${d.ship_count} ships</div>
        </div>`;
    }).join("");
  } catch (e) {
    grid.innerHTML = '<p class="empty">Error loading calendar</p>';
  }
}

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

async function loadSchedules() {
  try {
    scheduleRows = await fetchJSON("/api/schedules?limit=200");
    editingScheduleId = null;
    renderSchedulesTable();
  } catch (e) {
    $("#schedulesBody").innerHTML = '<tr><td colspan="8" class="empty">Error loading schedules</td></tr>';
  }
}

async function loadAiStatus() {
  const badge = $("#aiStatusBadge");
  if (!badge) return;
  try {
    const data = await fetchJSON("/api/health");
    const ai = data.ai || {};
    badge.classList.remove("connected", "disabled", "error");

    if (ai.enabled && ai.connected) {
      badge.classList.add("connected");
      badge.textContent = `AI active · ${ai.model} · XML + predictions`;
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

/** Upload CSV schedule file to the database. */
async function uploadCsvSchedule(file, replaceExisting) {
  const form = new FormData();
  form.append("file", file, file.name || "schedule.csv");
  const url = `/api/upload-csv?replace=${replaceExisting ? "true" : "false"}`;
  const res = await fetch(API + url, { method: "POST", body: form });
  if (!res.ok) {
    throw new Error(await readErrorDetail(res));
  }
  return res.json();
}

async function handleCsvUpload(fileList) {
  const resultEl = $("#csvUploadResult");
  const files = Array.from(fileList || []);
  if (!files.length) return;

  const replaceExisting = $("#csvReplaceExisting").checked;
  resultEl.classList.remove("hidden", "success", "error");
  resultEl.textContent = "Uploading CSV schedule to database...";
  $("#csvUploadBtn").disabled = true;

  try {
    const file = files[0];
    const data = await uploadCsvSchedule(file, replaceExisting);
    resultEl.classList.add("success");
    const parts = [`✓ ${file.name}: ${data.rows_imported} rows saved`];
    if (data.rows_skipped) parts.push(`${data.rows_skipped} unchanged`);
    if (data.notes) parts.push(data.notes);
    resultEl.textContent = parts.join(" · ");
    await refreshAll();
    switchTab("schedules");
  } catch (e) {
    resultEl.classList.add("error");
    resultEl.textContent = "CSV upload failed: " + e.message;
  } finally {
    $("#csvUploadBtn").disabled = false;
    $("#csvUploadInput").value = "";
  }
}

function setupCsvUpload() {
  $("#csvUploadBtn").addEventListener("click", () => $("#csvUploadInput").click());
  $("#csvUploadInput").addEventListener("change", () => {
    handleCsvUpload($("#csvUploadInput").files);
  });
}

/** Upload a File object directly to the database (skips clean step). */
async function uploadFileToDatabase(file) {
  const form = new FormData();
  form.append("file", file, file.name || "upload.xml");
  const res = await fetch(API + "/api/upload", { method: "POST", body: form });
  if (!res.ok) {
    throw new Error(await readErrorDetail(res));
  }
  return res.json();
}

/** Upload one or more XML files straight to the persistent database. */
async function directUploadFiles(fileList) {
  const resultEl = $("#directUploadResult");
  const files = Array.from(fileList || []);
  if (!files.length) return;

  resultEl.classList.remove("hidden", "success", "error");
  resultEl.textContent = `Uploading ${files.length} file(s) to database...`;
  $("#directUploadBtn").disabled = true;

  const summaries = [];
  try {
    for (const file of files) {
      const data = await uploadFileToDatabase(file);
      summaries.push(`${file.name}: +${data.rows_imported} new, ${data.rows_skipped} existing`);
    }
    resultEl.classList.add("success");
    resultEl.textContent = "✓ Saved to database — " + summaries.join(" · ");
    await refreshAll();
    switchTab("predictions");
  } catch (e) {
    resultEl.classList.add("error");
    resultEl.textContent = "Upload failed: " + e.message;
  } finally {
    $("#directUploadBtn").disabled = false;
    $("#directUploadInput").value = "";
  }
}

async function refreshAll() {
  await Promise.all([
    loadStorageStatus(),
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

/** Upload XML text content to the database. */
async function uploadXmlText(xmlText, filename = "cleaned_schedule.xml") {
  const blob = new Blob([xmlText], { type: "application/xml" });
  const form = new FormData();
  form.append("file", blob, filename);
  const res = await fetch(API + "/api/upload", { method: "POST", body: form });
  if (!res.ok) {
    throw new Error(await readErrorDetail(res));
  }
  return res.json();
}

/** Send cleaned XML to the prediction generator (database import + forecast refresh). */
async function pushToPredictions() {
  const outputEl = $("#cleanedXmlOutput");
  const xml = (outputEl.value || lastCleanedXml).trim();
  const resultEl = $("#pushResult");

  if (!xml) {
    alert("No cleaned XML to send. Paste raw XML and click Clean & Analyze first.");
    return;
  }

  resultEl.classList.remove("hidden", "success", "error");
  resultEl.textContent = "Sending to prediction generator...";
  $("#pushPredictionsBtn").disabled = true;

  try {
    const data = await uploadXmlText(xml, "cleaned_schedule.xml");
    resultEl.classList.add("success");
    const summary = data.rows_imported
      ? `✓ Imported ${data.rows_imported} rows — predictions updated`
      : `✓ Updated ${data.rows_skipped} existing rows — predictions refreshed`;
    resultEl.textContent = summary + (data.notes ? ` (${data.notes})` : "");

    await refreshAll();
    switchTab("predictions");
  } catch (e) {
    resultEl.classList.add("error");
    resultEl.textContent = "Failed to send: " + e.message;
  } finally {
    setCleanedOutputActions(Boolean((outputEl.value || lastCleanedXml).trim()));
  }
}

/** Send raw XML to the clean/repair API and display results. */
async function cleanRawXml(xmlText) {
  const summaryEl = $("#repairSummary");
  const repairsBody = $("#repairsBody");
  const outputEl = $("#cleanedXmlOutput");

  summaryEl.classList.add("hidden");
  repairsBody.innerHTML = '<tr><td colspan="6" class="empty">Analyzing...</td></tr>';
  setCleanedOutputActions(false);

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
    outputEl.value = lastCleanedXml;
    outputEl.readOnly = false;
    setCleanedOutputActions(Boolean(lastCleanedXml));

    summaryEl.classList.remove("hidden");
    summaryEl.innerHTML = `
      <strong>Analysis complete.</strong>
      ${data.entries_processed} entries ·
      ${data.times_normalized} times normalized ·
      ${data.boat_fields_repaired} boat fields repaired ·
      parser: ${data.parse_method}${data.ai_assisted ? " (AI-assisted)" : ""}
      — review cleaned output below, then send to predictions
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
    outputEl.value = "";
    lastCleanedXml = "";
    setCleanedOutputActions(false);
  }
}

/** Paste clipboard contents into the raw XML input. */
async function pasteFromClipboard() {
  try {
    const text = await navigator.clipboard.readText();
    if (!text.trim()) {
      alert("Clipboard is empty");
      return;
    }
    $("#rawXmlInput").value = text;
    $("#rawXmlInput").focus();
  } catch (e) {
    alert("Could not read clipboard. Paste manually with Ctrl+V / Cmd+V.");
  }
}

/** Merge duplicate schedule rows (same ship/date/times from repeated uploads). */
async function dedupeSchedules() {
  const resultEl = $("#directUploadResult");
  resultEl.classList.remove("hidden", "success", "error");
  resultEl.textContent = "Removing duplicate rows...";
  $("#dedupeSchedulesBtn").disabled = true;
  try {
    const res = await fetch(API + "/api/schedules/deduplicate", { method: "POST" });
    if (!res.ok) throw new Error(await readErrorDetail(res));
    const data = await res.json();
    resultEl.classList.add("success");
    if (data.rows_deleted) {
      resultEl.textContent = `Removed ${data.rows_deleted} duplicate rows — ${data.rows_remaining} schedule entries remaining`;
    } else {
      resultEl.textContent = "No duplicates found";
    }
    await Promise.all([loadStats(), loadStorageStatus(), loadSchedules(), loadPredictions(), loadCaptainOverview()]);
  } catch (e) {
    resultEl.classList.add("error");
    resultEl.textContent = "Dedupe failed: " + e.message;
  } finally {
    $("#dedupeSchedulesBtn").disabled = false;
  }
}

function setupDirectUpload() {
  $("#directUploadBtn").addEventListener("click", () => $("#directUploadInput").click());
  $("#dedupeSchedulesBtn").addEventListener("click", dedupeSchedules);
  $("#directUploadInput").addEventListener("change", () => {
    directUploadFiles($("#directUploadInput").files);
  });
}

function setupRepair() {
  $("#cleanXmlBtn").addEventListener("click", () => {
    const xml = $("#rawXmlInput").value.trim();
    if (!xml) {
      alert("Paste raw XML first");
      return;
    }
    cleanRawXml(xml);
  });

  $("#pasteXmlBtn").addEventListener("click", pasteFromClipboard);

  $("#copyCleanXmlBtn").addEventListener("click", async () => {
    const xml = $("#cleanedXmlOutput").value.trim() || lastCleanedXml;
    if (!xml) return;
    await navigator.clipboard.writeText(xml);
    $("#copyCleanXmlBtn").textContent = "Copied!";
    setTimeout(() => { $("#copyCleanXmlBtn").textContent = "Copy Cleaned XML"; }, 1500);
  });

  $("#pushPredictionsBtn").addEventListener("click", pushToPredictions);

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

  // Allow drag-and-drop onto the raw input area
  const rawInput = $("#rawXmlInput");
  rawInput.addEventListener("dragover", (e) => e.preventDefault());
  rawInput.addEventListener("drop", (e) => {
    e.preventDefault();
    const file = e.dataTransfer.files[0];
    if (file) {
      const reader = new FileReader();
      reader.onload = () => {
        rawInput.value = reader.result;
        cleanRawXml(String(reader.result));
      };
      reader.readAsText(file);
    }
  });

  // Keep push/copy enabled if user edits cleaned output manually
  $("#cleanedXmlOutput").addEventListener("input", () => {
    lastCleanedXml = $("#cleanedXmlOutput").value.trim();
    setCleanedOutputActions(Boolean(lastCleanedXml));
  });
}

function setupTabs() {
  $$(".tab").forEach((tab) => {
    tab.addEventListener("click", () => switchTab(tab.dataset.tab));
  });
}

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
  setupCsvUpload();
  setupDirectUpload();
  setupRepair();
  setupTabs();
  setupControls();
  setupScheduleEditing();
  refreshAll();
  // Focus raw input so users can paste immediately
  $("#rawXmlInput").focus();
});
