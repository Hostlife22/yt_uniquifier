"use strict";
// yt-uniquifier web SPA (v0.9.0 R4 / F13).
// Vanilla JS; no framework. Drives /api/run + SSE + /api/profiles.

const $ = (sel) => document.querySelector(sel);
const log = $("#log");
const status = $("#status");
const runBtn = $("#run-btn");
const cancelBtn = $("#cancel-btn");
let activeRunId = null;
let activeSource = null;

function setStatus(text, cls) {
  status.textContent = text;
  status.className = cls ? "status-" + cls : "";
}

function appendLog(line) {
  log.textContent += line + "\n";
  log.scrollTop = log.scrollHeight;
}

async function refreshLocalProfiles() {
  const sel = $("#profile-select");
  sel.innerHTML = "";
  try {
    const res = await fetch("/api/profiles/local");
    const items = await res.json();
    for (const p of items) {
      const opt = document.createElement("option");
      opt.value = p.path;
      opt.textContent = p.name;
      sel.appendChild(opt);
    }
  } catch (err) {
    appendLog("local profile list failed: " + err);
  }
}

async function refreshCatalog() {
  const ul = $("#catalog");
  ul.innerHTML = "loading…";
  try {
    const res = await fetch("/api/profiles/community?refresh=true");
    if (!res.ok) { ul.textContent = "catalog fetch failed: " + res.status; return; }
    const items = await res.json();
    ul.innerHTML = "";
    for (const e of items) {
      const li = document.createElement("li");
      const label = document.createElement("span");
      label.textContent = e.id + " — " + e.name;
      const btn = document.createElement("button");
      btn.textContent = "Install";
      btn.addEventListener("click", async () => {
        btn.disabled = true;
        try {
          const r = await fetch(
            "/api/profiles/community/" + encodeURIComponent(e.id) + "/install",
            { method: "POST" },
          );
          const data = await r.json();
          appendLog("installed " + e.id + " → " + (data.path || JSON.stringify(data)));
          await refreshLocalProfiles();
        } catch (err) { appendLog("install failed: " + err); }
        btn.disabled = false;
      });
      li.appendChild(label);
      li.appendChild(btn);
      ul.appendChild(li);
    }
  } catch (err) { ul.textContent = "catalog fetch failed: " + err; }
}

function attachStream(runId) {
  const src = new EventSource("/api/run/" + runId + "/events");
  activeSource = src;
  src.onmessage = (msg) => {
    try {
      const ev = JSON.parse(msg.data);
      appendLog("[" + ev.kind + "] " + JSON.stringify(ev.payload || {}));
    } catch { appendLog(msg.data); }
  };
  src.addEventListener("end", (msg) => {
    try {
      const data = JSON.parse(msg.data);
      const cls = data.status === "completed" ? "ok"
                : data.status === "failed" ? "err" : "warn";
      setStatus(data.status + (data.error ? " — " + data.error : ""), cls);
    } catch { setStatus("ended"); }
    src.close();
    activeSource = null;
    runBtn.disabled = false;
    cancelBtn.disabled = true;
  });
  src.onerror = () => { setStatus("stream error", "err"); };
}

$("#run-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const form = e.currentTarget;
  const data = {
    input_path: form.input_path.value.trim(),
    profile_path: form.profile_path.value,
    output_name: form.output_name.value.trim() || null,
    workers: parseInt(form.workers.value, 10) || 1,
  };
  log.textContent = "";
  setStatus("starting…", "warn");
  runBtn.disabled = true;
  try {
    const res = await fetch("/api/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    if (!res.ok) {
      const detail = await res.text();
      setStatus("start failed: " + res.status + " " + detail, "err");
      runBtn.disabled = false;
      return;
    }
    const payload = await res.json();
    activeRunId = payload.run_id;
    setStatus("running — " + (payload.output_basename || activeRunId), "warn");
    cancelBtn.disabled = false;
    attachStream(payload.run_id);
  } catch (err) {
    setStatus("error: " + err, "err");
    runBtn.disabled = false;
  }
});

cancelBtn.addEventListener("click", async () => {
  if (!activeRunId) return;
  cancelBtn.disabled = true;
  try {
    await fetch("/api/run/" + activeRunId + "/cancel", { method: "POST" });
    setStatus("cancel requested…", "warn");
  } catch (err) { setStatus("cancel error: " + err, "err"); }
});

$("#catalog-refresh").addEventListener("click", refreshCatalog);

refreshLocalProfiles();
refreshCatalog();
