"use strict";

const $ = (id) => document.getElementById(id);
const streams = new Map();      // job id -> EventSource
let options = { presets: [], profiles: [], lan_url: "" };

function human(n) {
  if (n === null || n === undefined) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (Math.abs(n) >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return (i === 0 ? n.toFixed(0) : n.toFixed(1)) + " " + units[i];
}

function ago(ts) {
  if (!ts) return "";
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 60) return Math.round(s) + "s ago";
  if (s < 3600) return Math.round(s / 60) + "m ago";
  if (s < 86400) return Math.round(s / 3600) + "h ago";
  return Math.round(s / 86400) + "d ago";
}

async function api(path, opts) {
  const res = await fetch(path, opts);
  const text = await res.text();
  let body = null;
  try { body = text ? JSON.parse(text) : null; } catch { /* non-JSON */ }
  if (!res.ok) throw new Error((body && body.error) || `${res.status} ${res.statusText}`);
  return body;
}

// ---- form ----------------------------------------------------------------

function renderPresets() {
  const host = $("presets");
  host.innerHTML = "";
  options.presets.forEach((p) => {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = `${p.label} · ${p.size}`;
    b.title = p.hint;
    b.setAttribute("aria-pressed", "false");
    b.addEventListener("click", () => {
      $("size").value = p.size;
      $("photo_fraction").value = p.photo_fraction;
      syncFraction();
      [...host.children].forEach((c) => c.setAttribute("aria-pressed", "false"));
      b.setAttribute("aria-pressed", "true");
    });
    host.appendChild(b);
  });
}

function syncFraction() {
  $("fraction_label").textContent =
    Math.round(parseFloat($("photo_fraction").value) * 100) + "%";
}

async function loadOptions() {
  options = await api("/api/options");
  renderPresets();
  const sel = $("profile");
  sel.innerHTML = "";
  options.profiles.forEach((p) => {
    const o = document.createElement("option");
    o.value = p.key;
    o.textContent = p.label;
    // Match the server's default rather than whatever sorts first, so the
    // form does not quietly disagree with the API about what you asked for.
    if (p.default) o.selected = true;
    sel.appendChild(o);
  });
  if (options.lan_url) {
    $("lan").innerHTML =
      `Devices on this network reach the packs at <code>${options.lan_url}</code>.`;
  }
}

async function submit(ev) {
  ev.preventDefault();
  const btn = $("build");
  $("formError").textContent = "";
  btn.disabled = true;
  try {
    const body = {
      size: $("size").value.trim(),
      profile: $("profile").value,
      photo_fraction: parseFloat($("photo_fraction").value),
      label: $("label").value,
      since: $("since").value.trim() || null,
      until: $("until").value.trim() || null,
    };
    await api("/api/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    await refresh();
  } catch (err) {
    $("formError").textContent = err.message;
  } finally {
    btn.disabled = false;
  }
}

// ---- jobs ----------------------------------------------------------------

function jobCard(job) {
  const el = document.createElement("article");
  el.className = "job";
  el.id = "job-" + job.id;

  const title = job.params.label || `${job.params.size} · ${job.params.profile}`;
  el.innerHTML = `
    <div class="job-head">
      <span class="job-title"></span>
      <span class="status ${job.status}">${job.status}</span>
      <span class="job-meta">${ago(job.created_at)}</span>
    </div>
    <div class="track"><div class="fill"></div></div>
    <div class="job-foot">
      <span class="counts"></span>
      <span class="msg"></span>
      <span class="pair" title="pairing code">${job.token}</span>
      <a class="manifest" href="${job.manifest_url}">manifest</a>
      <button class="act"></button>
    </div>
    <div class="log" hidden></div>`;
  el.querySelector(".job-title").textContent = title;
  el.querySelector(".act").addEventListener("click", () => actOn(job.id, el));
  return el;
}

function paint(el, job) {
  el.querySelector(".fill").style.width = job.percent + "%";
  el.querySelector(".status").className = "status " + job.status;
  el.querySelector(".status").textContent = job.status;
  el.querySelector(".counts").textContent =
    `${human(job.done_bytes)} of ${human(job.target_bytes)}` +
    (job.file_count ? ` · ${job.file_count} files` : "");
  el.querySelector(".msg").textContent = job.message || "";
  const act = el.querySelector(".act");
  if (job.status === "running") { act.textContent = "cancel"; act.hidden = false; }
  else if (job.status === "cancelled" || job.status === "failed") {
    act.textContent = "resume"; act.hidden = false;
  } else { act.hidden = true; }
  el.querySelector(".manifest").hidden = job.status !== "done";
  el.dataset.status = job.status;
}

async function actOn(id, el) {
  const status = el.dataset.status;
  try {
    if (status === "running") await api(`/api/jobs/${id}/cancel`, { method: "POST" });
    else await api(`/api/jobs/${id}/resume`, { method: "POST" });
    await refresh();
  } catch (err) {
    el.querySelector(".msg").textContent = err.message;
  }
}

function watch(job, el) {
  if (streams.has(job.id)) return;
  const src = new EventSource(`/api/jobs/${job.id}/events`);
  streams.set(job.id, src);
  const log = el.querySelector(".log");
  src.onmessage = (ev) => {
    const e = JSON.parse(ev.data);
    if (e.type === "progress") {
      const pct = e.target_bytes ? (100 * e.done_bytes) / e.target_bytes : 0;
      paint(el, {
        percent: Math.min(100, pct), status: "running",
        done_bytes: e.done_bytes, target_bytes: e.target_bytes,
        file_count: e.file_count, message: e.phase ? `${e.phase} phase` : "",
      });
    } else if (e.type === "log") {
      log.hidden = false;
      log.textContent = (log.textContent + "\n" + e.line).trim().split("\n").slice(-40).join("\n");
      log.scrollTop = log.scrollHeight;
    } else if (["done", "failed", "cancelled"].includes(e.type)) {
      close(job.id);
      refresh();
    }
  };
  src.onerror = () => close(job.id);
}

function close(id) {
  const s = streams.get(id);
  if (s) { s.close(); streams.delete(id); }
}

async function refresh() {
  let jobs;
  try { jobs = await api("/api/jobs"); } catch { return; }
  const host = $("jobs");
  if (!jobs.length) {
    host.innerHTML = '<p class="dim">No jobs yet.</p>';
    return;
  }
  if (host.querySelector("p")) host.innerHTML = "";
  const seen = new Set();
  jobs.forEach((job) => {
    seen.add("job-" + job.id);
    let el = $("job-" + job.id);
    if (!el) { el = jobCard(job); host.prepend(el); }
    paint(el, job);
    if (job.status === "running") watch(job, el); else close(job.id);
  });
  [...host.children].forEach((c) => { if (c.id && !seen.has(c.id)) c.remove(); });
}

$("form").addEventListener("submit", submit);
$("photo_fraction").addEventListener("input", syncFraction);
loadOptions().then(refresh).catch((e) => { $("formError").textContent = e.message; });
setInterval(refresh, 5000);
