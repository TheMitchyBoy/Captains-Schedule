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
  const useAi = $("#useAiPredictions")?.checked ? "true" : "false";
  let q = `?days_ahead=${days}&min_confidence=${conf}&use_ai=${useAi}`;
  if (captain) q += `&boat_code=${encodeURIComponent(captain)}`;
  return q;
}

function predictionQueryParams() {
  return queryParams();
}

function dashboardQueryParams() {
  const days = $("#daysAhead").value;
  const useAi = $("#useAiPredictions")?.checked ? "true" : "false";
  return `?days_ahead=${days}&use_ai=${useAi}`;
}

async function ensurePredictionPatterns() {
  try {
    const stats = await fetchJSON("/api/stats");
    if (stats.total_entries > 0 && !stats.patterns_learned) {
      await fetch(API + "/api/patterns/rebuild", { method: "POST" });
    }
  } catch (e) {
    console.error("Pattern rebuild error:", e);
  }
}

function predictionsEmptyMessage(stats) {
  if (stats?.total_entries > 0 && !stats?.patterns_learned) {
    return "Schedule data is saved, but no tour boat patterns were learned yet. Upload dispatch XML or bulk-add tours with boat codes (BW, DrmC, JR).";
  }
  if (stats?.total_entries > 0) {
    return "No predictions matched the current filters. Try lowering min confidence or extending the forecast horizon.";
  }
  return "No predictions yet — upload XML or add tours above to save schedule data to the database";
}

function sortBoatCodes(codes) {
  return [...new Set(codes.map((code) => String(code).trim()).filter(Boolean))].sort((a, b) =>
    a.localeCompare(b, "en", { numeric: true, sensitivity: "base" }),
  );
}

function compareBoatCodes(a, b) {
  return String(a).localeCompare(String(b), "en", { numeric: true, sensitivity: "base" });
}

function sortPredictionsByBoatFlow(rows) {
  return [...rows].sort((a, b) => {
    const dateCmp = String(a.schedule_date).localeCompare(String(b.schedule_date));
    if (dateCmp) return dateCmp;
    const boatCmp = compareBoatCodes(a.boat_code, b.boat_code);
    if (boatCmp) return boatCmp;
    const timeCmp = timeToMinutes(a.checkin_time) - timeToMinutes(b.checkin_time);
    if (timeCmp) return timeCmp;
    const shipCmp = a.ship.localeCompare(b.ship, undefined, { sensitivity: "base" });
    if (shipCmp) return shipCmp;
    return compareBoatCodes(a.return_time, b.return_time);
  });
}

function formatBoatCodes(value) {
  if (!value) return "—";
  const codes = value.split(/[,;/]+|\s+and\s+|\s+/i).map((c) => c.trim()).filter(Boolean);
  if (!codes.length) return "—";
  return sortBoatCodes(codes).map((c) => `<code>${c}</code>`).join(" ");
}

function escapeAttr(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/"/g, "&quot;")
    .replace(/</g, "&lt;");
}

let scheduleRows = [];
let editingScheduleId = null;
let highlightScheduleId = null;
let scheduleTotalEntries = 0;

function scheduleSourceLabel(uploadBatchId) {
  if (uploadBatchId && uploadBatchId.startsWith("manual")) {
    return '<span class="source-badge manual">Manual</span>';
  }
  return '<span class="source-badge upload">Upload</span>';
}

function todayIso() {
  return new Date().toISOString().slice(0, 10);
}

function buildSchedulesQuery() {
  const params = new URLSearchParams({ limit: "2000", order: "desc", through_today: "true" });
  const ship = $("#scheduleFilterShip")?.value.trim();
  const start = $("#scheduleFilterStart")?.value;
  const end = $("#scheduleFilterEnd")?.value;
  const manualOnly = $("#scheduleFilterManual")?.checked;
  if (ship) params.set("ship", ship);
  if (start) params.set("start_date", start);
  if (end) params.set("end_date", end);
  if (manualOnly) params.set("manual_only", "true");
  return `/api/schedules?${params.toString()}`;
}

function showSchedulesTab(entryId) {
  switchTab("schedules");
  highlightScheduleId = entryId ?? null;
  renderSchedulesTable();
  if (entryId) {
    const row = document.querySelector(`#schedulesBody tr[data-id="${entryId}"]`);
    row?.scrollIntoView({ behavior: "smooth", block: "center" });
  } else {
    $("#panel-schedules")?.scrollIntoView({ behavior: "smooth", block: "start" });
  }
}

function renderScheduleRow(s) {
  const highlightClass = highlightScheduleId === s.id ? " schedule-row-highlight" : "";
  if (editingScheduleId === s.id) {
    return `
      <tr class="schedule-row-editing${highlightClass}" data-id="${s.id}">
        <td>${formatDate(s.schedule_date)}</td>
        <td>${s.date_header}</td>
        <td>${s.ship}</td>
        <td><input type="text" class="schedule-edit-input" data-field="checkin_time" value="${escapeAttr(s.checkin_time)}" aria-label="Check-in time" /></td>
        <td><input type="text" class="schedule-edit-input" data-field="return_time" value="${escapeAttr(s.return_time)}" aria-label="Return time" /></td>
        <td>${s.berth ? `<code>${s.berth}</code>` : "—"}</td>
        <td><input type="text" class="schedule-edit-input schedule-edit-boats" data-field="boat_codes" value="${escapeAttr(s.boat_codes)}" placeholder="BW, BWA, JR" aria-label="Boat codes" /></td>
        <td>${scheduleSourceLabel(s.upload_batch_id)}</td>
        <td class="schedule-actions">
          <button type="button" class="btn btn-sm schedule-save-btn" data-id="${s.id}">Save</button>
          <button type="button" class="btn secondary btn-sm schedule-cancel-btn" data-id="${s.id}">Cancel</button>
        </td>
      </tr>`;
  }
  return `
    <tr class="${highlightClass.trim()}" data-id="${s.id}">
      <td>${formatDate(s.schedule_date)}</td>
      <td>${s.date_header}</td>
      <td>${s.ship}</td>
      <td>${s.checkin_time}</td>
      <td>${s.return_time}</td>
      <td>${s.berth ? `<code>${s.berth}</code>` : "—"}</td>
      <td>${formatBoatCodes(s.boat_codes)}</td>
      <td>${scheduleSourceLabel(s.upload_batch_id)}</td>
      <td class="schedule-actions">
        <button type="button" class="btn secondary btn-sm schedule-edit-btn" data-id="${s.id}">Edit</button>
      </td>
    </tr>`;
}

function updateScheduleTableMeta() {
  const meta = $("#scheduleTableMeta");
  if (!meta) return;
  if (!scheduleRows.length) {
    meta.textContent = scheduleTotalEntries
      ? `No rows match the current filters (${scheduleTotalEntries.toLocaleString()} total in database)`
      : "No schedules stored yet — add tours above or upload a file";
    return;
  }
  const manualCount = scheduleRows.filter((s) => s.upload_batch_id?.startsWith("manual")).length;
  meta.textContent = `Showing ${scheduleRows.length.toLocaleString()} row(s)${scheduleTotalEntries ? ` of ${scheduleTotalEntries.toLocaleString()} total` : ""}${manualCount ? ` · ${manualCount} manual in this view` : ""}`;
}

function renderSchedulesTable() {
  const tbody = $("#schedulesBody");
  if (!scheduleRows.length) {
    tbody.innerHTML = '<tr><td colspan="9" class="empty">No schedules match the current filters</td></tr>';
    updateScheduleTableMeta();
    return;
  }
  tbody.innerHTML = scheduleRows.map(renderScheduleRow).join("");
  updateScheduleTableMeta();
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

async function addTour(event) {
  event.preventDefault();
  const resultEl = $("#addTourResult");
  const submitBtn = $("#addTourBtn");
  const payload = {
    schedule_date: $("#addTourDate").value,
    ship: $("#addTourShip").value.trim(),
    checkin_time: $("#addTourCheckin").value.trim(),
    return_time: $("#addTourReturn").value.trim(),
    boat_codes: $("#addTourBoats").value.trim(),
    berth: $("#addTourBerth").value.trim() || null,
  };

  if (!payload.schedule_date || !payload.ship || !payload.checkin_time || !payload.return_time) {
    resultEl.classList.remove("hidden", "success");
    resultEl.classList.add("error");
    resultEl.textContent = "Date, ship, check-in, and return are required";
    return;
  }

  submitBtn.disabled = true;
  resultEl.classList.remove("hidden", "success", "error");
  resultEl.textContent = "Saving tour...";

  try {
    const res = await fetch(API + "/api/schedules", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) throw new Error(await readErrorDetail(res));
    const created = await res.json();

    resultEl.classList.add("success");
    resultEl.textContent = `Added ${created.ship} on ${formatDate(created.schedule_date)} — opening Raw Schedules`;

    $("#addTourCheckin").value = "";
    $("#addTourReturn").value = "";
    $("#addTourBoats").value = "";
    $("#addTourBerth").value = "";

    await loadSchedules();
    showSchedulesTab(created.id);
    await Promise.all([
      loadStats(),
      loadPredictions(),
      loadCaptainOverview(),
      loadCalendar(),
    ]);
  } catch (e) {
    resultEl.classList.add("error");
    resultEl.textContent = "Could not add tour: " + e.message;
  } finally {
    submitBtn.disabled = false;
  }
}

function setupAddTourForm() {
  const form = $("#addTourForm");
  if (!form) return;
  form.addEventListener("submit", addTour);
}

async function bulkAddTours(event) {
  event.preventDefault();
  const resultEl = $("#bulkTourResult");
  const submitBtn = $("#bulkTourBtn");
  const scheduleDate = $("#bulkTourDate").value;
  const text = $("#bulkTourText").value.trim();

  if (!scheduleDate) {
    resultEl.classList.remove("hidden", "success");
    resultEl.classList.add("error");
    resultEl.textContent = "Choose a date for these tours";
    return;
  }

  if (!text) {
    resultEl.classList.remove("hidden", "success");
    resultEl.classList.add("error");
    resultEl.textContent = "Paste one or more tour lines first";
    return;
  }

  submitBtn.disabled = true;
  resultEl.classList.remove("hidden", "success", "error");
  resultEl.textContent = "Importing tours…";

  try {
    const res = await fetch(API + "/api/schedules/bulk", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        schedule_date: scheduleDate,
        text,
        use_ai: $("#bulkTourUseAi")?.checked !== false,
      }),
    });
    if (!res.ok) throw new Error(await readErrorDetail(res));
    const data = await res.json();

    const parts = [];
    if (data.rows_created) parts.push(`${data.rows_created} added`);
    if (data.rows_merged) parts.push(`${data.rows_merged} merged`);
    if (data.rows_skipped) parts.push(`${data.rows_skipped} skipped`);

    resultEl.classList.add(data.errors?.length ? "error" : "success");
    let message = parts.length ? parts.join(", ") : "No new tours imported";
    if (data.ai_assisted && data.ai_message) {
      message += ` · ${data.ai_message}`;
    }
    if (data.errors?.length) {
      message += ` · ${data.errors.length} issue(s): ${data.errors.slice(0, 2).join("; ")}`;
      if (data.errors.length > 2) message += "…";
    }
    resultEl.textContent = message;

    if (data.rows_created || data.rows_merged) {
      await loadSchedules();
      showSchedulesTab();
      await Promise.all([
        loadStats(),
        loadPredictions(),
        loadCaptainOverview(),
        loadCalendar(),
      ]);
    }
  } catch (e) {
    resultEl.classList.add("error");
    resultEl.textContent = "Bulk import failed: " + e.message;
  } finally {
    submitBtn.disabled = false;
  }
}

function setupBulkTourForm() {
  const form = $("#bulkTourForm");
  if (!form) return;
  form.addEventListener("submit", bulkAddTours);
}

function setupScheduleFilters() {
  const endInput = $("#scheduleFilterEnd");
  if (endInput) {
    endInput.max = todayIso();
  }

  $("#scheduleFilterBtn")?.addEventListener("click", () => {
    highlightScheduleId = null;
    loadSchedules();
  });
  $("#scheduleFilterClearBtn")?.addEventListener("click", () => {
    $("#scheduleFilterShip").value = "";
    $("#scheduleFilterStart").value = "";
    $("#scheduleFilterEnd").value = "";
    $("#scheduleFilterManual").checked = false;
    highlightScheduleId = null;
    loadSchedules();
  });
  $("#scheduleFilterShip")?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      highlightScheduleId = null;
      loadSchedules();
    }
  });
}

function timeToMinutes(value) {
  if (!value) return 9999;
  const match = String(value).match(/^(\d{1,2}):(\d{2})$/);
  if (!match) return 9999;
  return parseInt(match[1], 10) * 60 + parseInt(match[2], 10);
}

function sourceLabel(source) {
  if (source === "ai") return '<span class="source-badge ai">AI</span>';
  if (source === "adjustment") return '<span class="source-badge manual">Override</span>';
  return '<span class="source-badge pattern">Pattern</span>';
}

let predictionChatHistory = [];

function appendPredictionChatMessage(role, content, actions = []) {
  const log = $("#predictionChatLog");
  if (!log) return;
  const wrapper = document.createElement("div");
  wrapper.className = `prediction-chat-message ${role}`;
  const label = role === "user" ? "You" : "Assistant";
  let html = `<strong>${label}</strong><p>${escapeAttr(content).replace(/\n/g, "<br/>")}</p>`;
  if (actions?.length) {
    html += `<ul class="prediction-chat-actions">${actions.map((action) =>
      `<li>${escapeAttr(action.type)} ${escapeAttr(action.schedule_date || "")} ${escapeAttr(action.boat_code || "")} ${escapeAttr(action.ship || "")}</li>`
    ).join("")}</ul>`;
  }
  wrapper.innerHTML = html;
  log.appendChild(wrapper);
  log.scrollTop = log.scrollHeight;
}

async function sendPredictionChatMessage(event) {
  event.preventDefault();
  const input = $("#predictionChatInput");
  const status = $("#predictionChatStatus");
  const sendBtn = $("#predictionChatSendBtn");
  const message = input.value.trim();
  if (!message) return;

  appendPredictionChatMessage("user", message);
  predictionChatHistory.push({ role: "user", content: message });
  input.value = "";
  status.classList.remove("hidden", "success", "error");
  status.textContent = "Thinking…";
  sendBtn.disabled = true;

  try {
    const days = $("#daysAhead").value;
    const resp = await fetch(API + "/api/predictions/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message,
        history: predictionChatHistory.slice(-8),
        days_ahead: Number(days),
      }),
    });
    const data = await resp.json();
    if (!resp.ok) {
      throw new Error(data.detail || "Chat request failed");
    }

    appendPredictionChatMessage("assistant", data.reply, data.actions_applied || []);
    predictionChatHistory.push({ role: "assistant", content: data.reply });
    status.classList.add("success");
    status.textContent = data.predictions_changed
      ? `Applied ${data.predictions_changed} change(s). Refreshing predictions…`
      : "Reply ready.";

    await Promise.all([
      loadPredictions(),
      loadCaptainOverview(),
      loadCalendar(),
      loadCaptainsFilter(),
    ]);
  } catch (e) {
    status.classList.add("error");
    status.textContent = e.message;
    appendPredictionChatMessage("assistant", `Sorry, I couldn't apply that: ${e.message}`);
  } finally {
    sendBtn.disabled = false;
  }
}

async function clearPredictionOverrides() {
  const status = $("#predictionChatStatus");
  try {
    const resp = await fetch(API + "/api/predictions/adjustments/clear", { method: "POST" });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || "Clear failed");
    status.classList.remove("hidden", "error");
    status.classList.add("success");
    status.textContent = `Cleared ${data.removed} override(s).`;
    await Promise.all([loadPredictions(), loadCaptainOverview(), loadCalendar()]);
  } catch (e) {
    status.classList.remove("hidden", "success");
    status.classList.add("error");
    status.textContent = e.message;
  }
}

function setupPredictionChat() {
  $("#predictionChatForm")?.addEventListener("submit", sendPredictionChatMessage);
  $("#clearPredictionOverridesBtn")?.addEventListener("click", clearPredictionOverrides);
  $("#predictionChatInput")?.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      $("#predictionChatForm").requestSubmit();
    }
  });
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
    const resp = await fetchJSON("/api/captains" + dashboardQueryParams());
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
  tbody.innerHTML = '<tr><td colspan="9" class="empty">Loading predictions from database…</td></tr>';
  try {
    const stats = await fetchJSON("/api/stats").catch(() => null);
    const resp = await fetchJSON("/api/predictions" + predictionQueryParams());
    const data = resp.predictions || resp;
    showAiPredictionBanner(resp.ai);
    if (!data.length) {
      tbody.innerHTML = `<tr><td colspan="9" class="empty">${predictionsEmptyMessage(stats)}</td></tr>`;
      return;
    }
    const sorted = sortPredictionsByBoatFlow(data);
    tbody.innerHTML = sorted.slice(0, 200).map((row) => `
      <tr>
        <td>${formatDate(row.schedule_date)}</td>
        <td>${row.day_of_week}</td>
        <td><code>${escapeAttr(row.boat_code)}</code></td>
        <td>${row.ship}</td>
        <td>${row.checkin_time}</td>
        <td>${row.return_time}</td>
        <td>${confidenceBar(row.confidence)}</td>
        <td>${sourceLabel(row.source)}</td>
        <td><span class="busy-badge ${busyClass(row.busy_score)}">${busyLabel(row.busy_score)}</span></td>
      </tr>
    `).join("");
    if (sorted.length > 200) {
      tbody.innerHTML += `<tr><td colspan="9" class="empty">Showing first 200 of ${sorted.length} boat assignments</td></tr>`;
    }
  } catch (e) {
    console.error("Predictions load error:", e);
    tbody.innerHTML = `<tr><td colspan="9" class="empty">Error loading predictions: ${escapeAttr(e.message)}</td></tr>`;
  }
}

async function loadCaptainOverview() {
  const grid = $("#captainGrid");
  const days = $("#daysAhead").value;
  try {
    const resp = await fetchJSON("/api/captains" + dashboardQueryParams());
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
    const resp = await fetchJSON("/api/busy-calendar" + dashboardQueryParams());
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
    const [rows, stats] = await Promise.all([
      fetchJSON(buildSchedulesQuery()),
      fetchJSON("/api/stats").catch(() => ({ total_entries: 0 })),
    ]);
    scheduleRows = rows;
    scheduleTotalEntries = stats.total_entries || rows.length;
    renderSchedulesTable();
  } catch (e) {
    $("#schedulesBody").innerHTML = '<tr><td colspan="9" class="empty">Error loading schedules</td></tr>';
    const meta = $("#scheduleTableMeta");
    if (meta) meta.textContent = "Could not load schedules";
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
    loadUploads(),
    loadSchedules(),
  ]);
  await ensurePredictionPatterns();
  await Promise.all([
    loadCaptainsFilter(),
    loadPredictions(),
    loadCaptainOverview(),
    loadCalendar(),
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
  ["captainFilter", "daysAhead", "minConfidence", "useAiPredictions"].forEach((id) => {
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
  setupAddTourForm();
  setupBulkTourForm();
  setupScheduleFilters();
  setupPredictionChat();
  refreshAll();
  // Focus raw input so users can paste immediately
  $("#rawXmlInput").focus();
});
