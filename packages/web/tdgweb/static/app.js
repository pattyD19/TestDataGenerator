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
      photo_format: $("photo_format").value,
      video_codec: $("video_codec").value,
      edge_cases: $("edge_cases").checked,
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
      <button class="prune"></button>
    </div>
    <div class="log" hidden></div>`;
  el.querySelector(".job-title").textContent = title;
  el.querySelector(".act").addEventListener("click", () => actOn(job.id, el));
  el.querySelector(".prune").addEventListener("click", () => pruneOn(job.id, el));
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
  } else if (job.status === "pruned") {
    // The job id and seed are still on the row and the generator is
    // deterministic, so this reproduces the same pack byte for byte.
    act.textContent = "rebuild"; act.hidden = false;
    act.title = "Build this pack again — same job id and seed, same bytes";
  } else { act.hidden = true; }

  const pr = el.querySelector(".prune");
  if (job.status === "running") {
    pr.hidden = true;
  } else if (job.status === "pruned") {
    pr.hidden = false;
    pr.textContent = "delete";
    pr.title = "Remove this job from the list";
  } else {
    pr.hidden = false;
    pr.textContent = "prune";
    pr.title = "Delete this pack from disk and keep the job";
  }

  // A pruned pack has nothing to hand a device, so it offers neither.
  const gone = job.status === "pruned";
  el.querySelector(".manifest").hidden = job.status !== "done";
  el.querySelector(".pair").hidden = gone;
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

// How many jobs stay as full cards. Beyond this the history is still there,
// just not occupying a screenful — a long-running lab accumulates dozens.
const RECENT = 4;

function compactRow(job) {
  const el = document.createElement("div");
  el.className = "orow";
  el.id = "job-" + job.id;
  el.innerHTML = `
    <span class="otitle"></span>
    <span class="status ${job.status}">${job.status}</span>
    <span class="ometa"></span>
    <button class="act"></button>
    <button class="prune"></button>
    <span class="msg"></span>`;
  el.querySelector(".act").addEventListener("click", () => actOn(job.id, el));
  el.querySelector(".prune").addEventListener("click", () => pruneOn(job.id, el));
  return el;
}

function paintRow(el, job) {
  el.querySelector(".otitle").textContent =
    job.params.label || `${job.params.size} · ${job.params.profile}`;
  const st = el.querySelector(".status");
  st.className = "status " + job.status;
  st.textContent = job.status;
  el.querySelector(".ometa").textContent =
    (job.file_count ? `${job.file_count} files · ` : "") +
    human(job.done_bytes) + " · " + ago(job.created_at);

  // The same two actions the cards offer, so an old job can still be
  // reclaimed or rebuilt without expanding it into one.
  const act = el.querySelector(".act"), pr = el.querySelector(".prune");
  if (job.status === "pruned") { act.textContent = "rebuild"; act.hidden = false; }
  else if (job.status === "cancelled" || job.status === "failed") {
    act.textContent = "resume"; act.hidden = false;
  } else { act.hidden = true; }
  pr.textContent = job.status === "pruned" ? "delete" : "prune";
  pr.hidden = false;
  el.dataset.status = job.status;
}

async function pruneOn(id, el) {
  const status = el.dataset.status;
  // A failed or cancelled job may still hold a checkpoint, so pruning it
  // throws away a resume. Say so before doing it rather than after.
  const partial = status === "failed" || status === "cancelled";
  let msg, path, opts;
  if (status === "pruned") {
    msg = "Remove this job from the list?\n\nIts pack is already gone.";
    path = `/api/jobs/${id}`;
    opts = { method: "DELETE" };
  } else {
    msg = "Delete this pack's files from disk?\n\n" +
      "The job stays in the list, and any device already loaded from it can " +
      "still be wiped — a wipe reads the receipt, not the pack." +
      (partial ? "\n\nThis build is unfinished: pruning discards its " +
                 "checkpoint, so resuming would restart from zero." : "");
    path = `/api/jobs/${id}/prune` + (partial ? "?force=1" : "");
    opts = { method: "POST" };
  }
  if (!confirm(msg)) return;
  try {
    await api(path, opts);
    await refresh();
    await refreshReclaim(true);
  } catch (err) {
    el.querySelector(".msg").textContent = err.message;
  }
}

// ---- reclaiming disk -----------------------------------------------------

// Surveying stats every file in every pack, and refresh() runs every five
// seconds — so this is throttled, and forced only after something has actually
// changed on disk.
let lastSurvey = 0;
let lastSignature = "";

async function refreshReclaim(force) {
  const now = Date.now();
  if (!force && now - lastSurvey < 30000) return;
  lastSurvey = now;
  const btn = $("reclaim");
  let survey;
  try { survey = await api("/api/prune"); } catch { btn.hidden = true; return; }
  if (!survey.eligible) { btn.hidden = true; return; }
  btn.hidden = false;
  btn.textContent = `Reclaim ${survey.reclaimable_human}`;
  btn.title = `${survey.eligible} pack(s) can be deleted from disk`;
  btn.dataset.count = survey.eligible;
  btn.dataset.size = survey.reclaimable_human;
}

async function reclaim() {
  const btn = $("reclaim");
  // Ask the server again rather than trusting the label: a build may have
  // finished since it was drawn, and the number in a delete confirmation has
  // to be the number that will actually be deleted.
  let survey;
  try { survey = await api("/api/prune"); } catch (err) { return; }
  if (!survey.eligible) { await refreshReclaim(true); return; }
  const n = survey.eligible, size = survey.reclaimable_human;
  if (!confirm(
    `Delete ${n} pack(s) from disk, freeing ${size}?\n\n` +
    "The jobs stay in the list and can be rebuilt. Unfinished builds are " +
    "skipped so their resume survives. Devices already loaded stay wipeable.")) {
    return;
  }
  btn.disabled = true;
  try {
    await api("/api/prune", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    await refresh();
    await refreshReclaim(true);
  } finally {
    btn.disabled = false;
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

  // Jobs arrive newest first. A running build stays a full card however old it
  // is — the one job you actively need to watch must never be the one folded
  // away.
  const recent = [], older = [];
  jobs.forEach((job, i) => {
    (i < RECENT || job.status === "running" ? recent : older).push(job);
  });

  const seen = new Set();
  const oldHost = $("older");
  recent.forEach((job) => {
    seen.add("job-" + job.id);
    let el = $("job-" + job.id);
    // A job crossing the boundary changes shape, so rebuild it rather than
    // repainting a compact row with card markup it does not have.
    if (el && !el.classList.contains("job")) { el.remove(); el = null; }
    if (!el) { el = jobCard(job); }
    host.appendChild(el);                 // appendChild also reorders in place
    paint(el, job);
    if (job.status === "running") watch(job, el); else close(job.id);
  });
  older.forEach((job) => {
    seen.add("job-" + job.id);
    let el = $("job-" + job.id);
    if (el && !el.classList.contains("orow")) { el.remove(); el = null; }
    if (!el) { el = compactRow(job); }
    oldHost.appendChild(el);
    paintRow(el, job);
    close(job.id);
  });
  [...host.children, ...oldHost.children]
    .forEach((c) => { if (c.id && !seen.has(c.id)) c.remove(); });

  const wrap = $("olderWrap");
  wrap.hidden = older.length === 0;
  if (older.length) {
    $("olderSummary").textContent =
      `${older.length} older job${older.length === 1 ? "" : "s"}`;
  }
  // Only a status change alters what can be reclaimed — done_bytes moves
  // constantly during a build and must not trigger a survey. The throttle
  // stays as a floor, so a prune run from the CLI still shows up eventually.
  const signature = jobs.map((j) => j.id + ":" + j.status).join(",");
  const changed = signature !== lastSignature;
  lastSignature = signature;
  refreshReclaim(changed);
}

// Remember whether the history was left open. Wrapped because a browser set
// to block site data throws on access rather than returning null, and a
// preference this small must never break the page.
const OLDER_KEY = "tdg.olderOpen";
try {
  $("olderWrap").open = localStorage.getItem(OLDER_KEY) === "1";
} catch (e) { /* no storage; default closed */ }
$("olderWrap").addEventListener("toggle", () => {
  try { localStorage.setItem(OLDER_KEY, $("olderWrap").open ? "1" : "0"); }
  catch (e) { /* nothing to do */ }
});

$("form").addEventListener("submit", submit);
$("reclaim").addEventListener("click", reclaim);
$("photo_fraction").addEventListener("input", syncFraction);
loadOptions().then(refresh).then(() => refreshReclaim(true))
  .catch((e) => { $("formError").textContent = e.message; });
setInterval(refresh, 5000);
