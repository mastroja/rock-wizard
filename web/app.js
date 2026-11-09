// RockWizard web app: the same screens in the desktop window and on the phone.
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const desk = () => window.pywebview && window.pywebview.api;  // only inside the desktop window

async function api(path, opts = {}) {
  const r = await fetch(path, opts);
  let data = null;
  try { data = await r.json(); } catch (e) { /* not json */ }
  if (!r.ok) throw new Error((data && (data.detail || data.error)) || `${r.status} ${r.statusText}`);
  return data;
}
const post = (path, body = {}) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
const qs = (o) => new URLSearchParams(o).toString();

let toastTimer;
function toast(msg) {
  let t = $(".toast");
  if (!t) { t = document.createElement("div"); t.className = "toast"; document.body.append(t); }
  t.textContent = msg;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.remove(), 4200);
}

// ---------- icons (24px line icons) ----------
const P = (d) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">${d}</svg>`;
const ICON = {
  create: P('<path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M18.4 5.6l-2.1 2.1M7.7 16.3l-2.1 2.1"/><circle cx="12" cy="12" r="3"/>'),
  queue: P('<path d="M4 6h11M4 12h11M4 18h7"/><path d="m17 15 3 3-3 3"/>'),
  staging: P('<rect x="3" y="4" width="18" height="16" rx="2"/><path d="m8 12 3 3 5-6"/>'),
  library: P('<path d="M4 4h4v16H4zM10 4h4v16h-4z"/><path d="m16.5 4.5 3.8 1 -3.9 14.9-3.8-1z"/>'),
  settings: P('<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>'),
  upload: P('<path d="M12 16V4M7 9l5-5 5 5"/><path d="M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3"/>'),
  live: P('<path d="M9 18V5l11-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="17" cy="16" r="3"/>'),
  play: P('<path d="m7 4 13 8-13 8z"/>'),
  folder: P('<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>'),
  rocket: P('<path d="M5 15c-1.5 1.5-2 5-2 5s3.5-.5 5-2"/><path d="M14 4c3-1 6-1 6-1s0 3-1 6l-6 6-5-5z"/><path d="M9 10 5 9l-2 2 4 1M14 15l1 4 2-2-1-4"/>'),
};
const VIEWS = [["create", "Create"], ["queue", "Queue"], ["staging", "Staging"], ["live", "Quickplay"], ["library", "Library"], ["settings", "Settings"]];

// ---------- state + shell ----------
const S = { settings: {}, jobs: [], status: {}, staging: null, me: { owner: true, name: "" } };
const owner = () => S.me.owner;
const byChip = (s) => s.added_by ? `<span class="chip by">from ${esc(s.added_by)}</span>` : "";
const view = () => (location.hash.slice(1).split("?")[0] || "create");

function navHTML(cls) {
  const active = S.jobs.filter((j) => j.status === "queued" || j.status === "running").length;
  return VIEWS.filter(([id]) => owner() || id !== "library").map(([id, label]) =>
    `<button class="${cls}${view() === id ? " on" : ""}" data-go="${id}">${ICON[id]}<span>${label}</span>` +
    (id === "queue" && active ? `<span class="count">${active}</span>` : "") + `</button>`).join("");
}
// only touch the page when something changed: the status poll runs every 1.5 s, and rewriting
// the tab bar under an open iPhone keyboard can knock the focus out of a text field
const lastHTML = {};
function setHTML(sel, html) {
  if (lastHTML[sel] === html) return;
  lastHTML[sel] = html;
  $(sel).innerHTML = html;
}
function renderNav() {
  setHTML("#nav", navHTML("nav"));
  setHTML("#tabbar", navHTML(""));
}
document.addEventListener("click", (e) => {
  const go = e.target.closest("[data-go]");
  if (go) { location.hash = go.dataset.go; }
});
window.addEventListener("hashchange", () => { closeDrawer(); renderNav(); render(); });

function statusPills() {
  const st = S.status || {};
  const pills = [];
  if (st.busy) pills.push(["busy", `Converting${st.queued ? ` (+${st.queued} waiting)` : ""}`]);
  else if (st.training && st.training.running) pills.push(["busy", "GPU training a model"]);
  else pills.push(["good", "Ready"]);
  const sy = st.sync;
  if (sy) {
    if (sy.state === "not shared") pills.push(["", "Live folder not synced"]);
    else if (sy.devices && sy.devices.length) {
      const d = sy.devices[0];
      if (!d.connected) pills.push(["warn", `${d.name}: offline`]);
      else if (d.completion >= 100) pills.push(["good", `${d.name}: up to date`]);
      else pills.push(["busy", `${d.name}: syncing ${Math.floor(d.completion)}%`]);
    } else pills.push(["", `Sync: ${sy.state}`]);
  }
  setHTML("#pills", pills.map(([c, t]) => `<div class="pill"><span class="dot ${c}"></span><span>${esc(t)}</span></div>`).join(""));
  const [c, t] = pills[0];
  setHTML("#mpill", `<span class="dot ${c}"></span><span>${esc(t)}</span>`);
}

async function refresh() {
  try {
    const [jobs, status] = await Promise.all([api("/api/jobs"), api("/api/status")]);
    const before = JSON.stringify(S.jobs.map((j) => [j.id, j.status, j.step, Math.round(j.progress * 50)]));
    S.jobs = jobs; S.status = status;
    renderNav(); statusPills();
    if (view() === "queue" && before !== JSON.stringify(jobs.map((j) => [j.id, j.status, j.step, Math.round(j.progress * 50)]))) renderQueue();
  } catch (e) { /* server restarting */ }
}

function page(title, sub, body, actions = "") {
  $("#main").innerHTML = `<div class="top"><div><h1>${title}</h1>${sub ? `<p class="sub">${sub}</p>` : ""}</div><div class="row">${actions}</div></div>${body}`;
}
const render = () => ((!owner() && view() === "library" ? renderCreate : { create: renderCreate, queue: renderQueue, staging: renderStaging, live: renderLive, library: renderLibrary, settings: renderSettings }[view()]) || renderCreate)();

// ---------- create ----------
function namesFromFile(stem) {  // quick guess; the PC looks names up properly while converting
  let s = stem.replace(/_/g, " ").replace(/[\(\[].*?[\)\]]/g, "").trim();
  const parts = s.split(" - ").map((x) => x.trim()).filter(Boolean);
  if (parts.length > 2 && (!parts[parts.length - 1].includes(" ") || parts[parts.length - 1] === parts[parts.length - 1].toUpperCase())) parts.pop();
  return parts.length > 1 ? [parts[0], parts.slice(1).join(" - ")] : ["", parts[0] || stem];
}

function renderCreate() {
  page("Create", "Drop in a song, search for one, or paste a YouTube link. Your PC does the work.", `
    <div class="card">
      <div class="drop" id="drop">
        <div class="icon">${ICON.upload}</div>
        <b>Drop an MP3 or WAV here</b>
        <div class="muted">or click to choose a file</div>
        <input type="file" id="file" accept="audio/*,.mp3,.wav,.flac,.ogg,.m4a,.opus" hidden>
      </div>
      <div id="pending" class="hidden stack" style="margin-top:14px"></div>
    </div>
    <div class="card">
      <h2>Find a song</h2>
      <div class="searchbox">
        <input id="q" type="search" placeholder="Artist - Song title, or paste a YouTube link" autocomplete="off">
        <button class="btn primary" id="go">Search</button>
      </div>
      <div id="results"></div>
    </div>
    <div class="card">
      <div class="row spread"><h2 style="margin:0">Suggested songs</h2><button class="btn small" id="sgmore">Shuffle</button></div>
      <p class="faint" style="margin:4px 0 12px">Picked by hand for how they should play. Anything already in your library is left out.</p>
      <div id="suggest"><div class="empty"><span class="spinner"></span></div></div>
    </div>
    <div class="card desktop-only" id="batchcard">
      <div class="row spread wrap">
        <div><h2 style="margin:0">Convert a whole folder</h2><div class="faint">Every song in a folder, one after another.</div></div>
        <button class="btn" id="pickbatch">${ICON.folder} Choose folder…</button>
      </div>
      <div id="batch"></div>
    </div>`);
  const drop = $("#drop"), file = $("#file");
  drop.onclick = () => file.click();
  file.onchange = () => file.files[0] && pending(file.files[0]);
  drop.ondragover = (e) => { e.preventDefault(); drop.classList.add("over"); };
  drop.ondragleave = () => drop.classList.remove("over");
  drop.ondrop = (e) => { e.preventDefault(); drop.classList.remove("over"); const f = e.dataTransfer.files[0]; if (f) pending(f); };
  $("#go").onclick = doSearch;
  // a block body: an on… handler that returns false throws the keystroke away
  $("#q").onkeydown = (e) => { if (e.key === "Enter") doSearch(); };
  if (!desk()) $("#batchcard").classList.add("hidden");  // needs the desktop window's folder picker
  $("#pickbatch").onclick = pickBatch;
  loadSuggestions();
}

let suggestGroup = "All";
async function loadSuggestions() {
  const box = $("#suggest");
  let songs;
  try { songs = (await api("/api/suggestions")).songs; } catch (e) { box.innerHTML = `<p class="chip red">${esc(e.message)}</p>`; return; }
  if (!box.isConnected) return;  // left the page while it loaded
  const groups = ["All", ...new Set(songs.map((s) => s.group))];
  if (!groups.includes(suggestGroup)) suggestGroup = "All";
  box.innerHTML = `<div class="tabs" style="margin-bottom:10px">${groups.map((g) => `<button data-sg="${esc(g)}">${esc(g)}</button>`).join("")}</div><div id="sglist"></div>`;
  const draw = () => {
    $$("[data-sg]", box).forEach((b) => b.classList.toggle("on", b.dataset.sg === suggestGroup));
    const pool = songs.filter((s) => suggestGroup === "All" || s.group === suggestGroup);
    const shown = [], artists = new Set();
    for (const s of [...pool].sort(() => Math.random() - 0.5)) {  // one song per artist each time
      if (shown.length < 8 && !artists.has(s.artist)) { artists.add(s.artist); shown.push(s); }
    }
    $("#sglist", box).innerHTML = shown.map((s, i) => `<div class="result"><div class="grow"><div class="ellipsis"><b>${esc(s.artist)} – ${esc(s.title)}</b></div>
      <div class="faint ellipsis">${esc(s.why)}</div></div><button class="btn small" data-find="${i}">Find it</button></div>`).join("") || `<p class="faint">You already have every song in this group.</p>`;
    $$("[data-find]", box).forEach((b) => b.onclick = () => {
      const s = shown[b.dataset.find];
      $("#q").value = `${s.artist} - ${s.title}`;
      $("#q").scrollIntoView({ behavior: "smooth", block: "center" });
      doSearch();
    });
  };
  $$("[data-sg]", box).forEach((b) => b.onclick = () => { suggestGroup = b.dataset.sg; draw(); });
  $("#sgmore").onclick = draw;
  draw();
}

function pending(f) {
  const [a, t] = namesFromFile(f.name.replace(/\.[^.]+$/, ""));
  const box = $("#pending");
  box.classList.remove("hidden");
  box.innerHTML = `
    <div class="row"><span class="chip accent">${esc(f.name)}</span><span class="faint">${(f.size / 1e6).toFixed(1)} MB</span></div>
    <div class="grid2">
      <div><label class="field">Artist</label><input id="pa" value="${esc(a)}"></div>
      <div><label class="field">Song title</label><input id="pt" value="${esc(t)}"></div>
    </div>
    <button class="btn primary big" id="make">${ICON.rocket} Make my chart</button>`;
  $("#make").onclick = async () => {
    const fd = new FormData();
    fd.append("file", f); fd.append("artist", $("#pa").value); fd.append("title", $("#pt").value);
    $("#make").disabled = true; $("#make").innerHTML = `<span class="spinner"></span> Uploading…`;
    try {
      await api("/api/jobs/upload", { method: "POST", body: fd });
      toast("Added to the queue"); location.hash = "queue";
    } catch (e) { toast(e.message); $("#make").disabled = false; $("#make").textContent = "Make my chart"; }
  };
}

const ytId = (url) => { try { return new URL(url).searchParams.get("v") || url.split("/").pop(); } catch (e) { return ""; } };
const mmss = (s) => s ? `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}` : "";

async function doSearch() {
  const q = $("#q").value.trim();
  if (!q) return;
  const out = $("#results");
  if (/^https?:\/\//.test(q)) {
    try { await post("/api/jobs/convert", { url: q }); toast("Added to the queue"); location.hash = "queue"; }
    catch (e) { toast(`Couldn't start it: ${e.message}`); }
    return;
  }
  out.innerHTML = `<div class="empty"><span class="spinner"></span><div>Searching charts, song details and YouTube…</div></div>`;
  try {
    const r = await api("/api/search?" + qs({ q }));
    const d = r.details;
    out.innerHTML = `
      ${d ? `<p class="muted" style="margin:14px 0 4px">${esc(d.artist)} – <b style="color:var(--text)">${esc(d.title)}</b> · ${esc([d.album, d.year, d.genre].filter(Boolean).join(" · "))}</p>` : ""}
      ${r.errors.length ? `<p class="chip yellow">${esc(r.errors.join("; "))}</p>` : ""}
      <h2 style="margin-top:16px">Already charted by the community</h2>
      ${r.charts.length ? r.charts.map((c, i) => `
        <div class="result"><div class="grow"><div class="ellipsis"><b>${esc(c.artist)} – ${esc(c.name)}</b></div>
          <div class="faint">charted by ${esc(c.charter)}${c.parts ? " · " + esc(c.parts) : ""}</div></div>
          <button class="btn small" data-chart="${i}">Download</button></div>`).join("")
        : `<p class="faint">No human-made chart found, so making one is the way to go.</p>`}
      <h2 style="margin-top:16px">Make a chart from YouTube</h2>
      ${r.videos.map((v, i) => `
        <div class="result">
          <img class="thumb" src="https://i.ytimg.com/vi/${esc(ytId(v.url))}/mqdefault.jpg" alt="" loading="lazy">
          <div class="grow"><div class="ellipsis"><b>${esc(v.title)}</b></div>
            <div class="faint">${esc(v.channel)} · ${mmss(v.seconds)} ${v.best ? `<span class="chip green">Best match</span>` : ""}</div></div>
          <button class="btn primary small" data-video="${i}">Chart this</button></div>`).join("") || `<p class="faint">No videos found.</p>`}`;
    $$("[data-chart]", out).forEach((b) => b.onclick = async () => {
      const c = r.charts[b.dataset.chart];
      b.disabled = true; b.innerHTML = `<span class="spinner"></span>`;
      try { const s = await post("/api/charts/download", c); b.textContent = "Saved ✓"; toast("Saved to " + s.saved); }
      catch (e) { toast(e.message); b.disabled = false; b.textContent = "Download"; }
    });
    $$("[data-video]", out).forEach((b) => b.onclick = async () => {
      const v = r.videos[b.dataset.video];
      b.disabled = true; b.innerHTML = `<span class="spinner"></span>`;
      try {
        await post("/api/jobs/convert", { url: v.url, artist: v.artist, title: v.song });
        toast(`Charting ${v.artist} – ${v.song}`); location.hash = "queue";
      } catch (e) { toast(`Couldn't start it: ${e.message}`); b.disabled = false; b.textContent = "Chart this"; }
    });
  } catch (e) { out.innerHTML = `<p class="chip red">${esc(e.message)}</p>`; }
}

async function pickBatch() {
  const d = await desk().pick_folder();
  if (!d) return;
  const box = $("#batch");
  box.innerHTML = `<div class="empty"><span class="spinner"></span></div>`;
  const r = await api("/api/library/batch?" + qs({ folder: d }));
  box.innerHTML = `
    <div class="faint" style="margin:12px 0 6px">${r.files.length} song${r.files.length === 1 ? "" : "s"} in ${esc(r.folder)} · names are looked up again while converting</div>
    <div class="list">${r.files.map((f, i) => `
      <div class="item"><input type="checkbox" class="bf" data-i="${i}" checked style="width:18px;height:18px;accent-color:var(--accent)">
        <div class="grow"><div class="grid2"><input class="ba" value="${esc(f.artist)}" placeholder="Artist"><input class="bt" value="${esc(f.title)}" placeholder="Song title"></div>
        <div class="faint" style="margin-top:4px">${esc(f.file)}${f.existing ? ` · <span style="color:var(--warn)">already converted: the old version is backed up and replaced</span>` : ""}</div></div></div>`).join("")}</div>
    <button class="btn primary" id="runbatch" style="margin-top:12px" ${r.files.length ? "" : "disabled"}>Convert ${r.files.length} songs</button>`;
  $("#runbatch").onclick = async () => {
    const rows = $$(".item", box);
    let n = 0;
    for (const row of rows) {
      if (!$(".bf", row).checked) continue;
      const f = r.files[$(".bf", row).dataset.i];
      await post("/api/jobs/convert", { path: f.path, artist: $(".ba", row).value, title: $(".bt", row).value });
      n++;
    }
    toast(`${n} songs added to the queue`); location.hash = "queue";
  };
}

// ---------- queue ----------
const STATUS_CHIP = { queued: ["", "Waiting"], running: ["accent", "Working"], done: ["green", "Done"], failed: ["red", "Failed"], cancelled: ["", "Cancelled"] };
const VERDICT = { green: ["green", "Looks good"], yellow: ["yellow", "Check flagged spots"], red: ["red", "Has problems"] };

function renderQueue() {
  const jobs = S.jobs;
  page("Queue", "Songs being made, one at a time on your PC's graphics card.",
    jobs.length ? `<div class="stack">${jobs.map((j) => {
      const [cc, ct] = STATUS_CHIP[j.status] || ["", j.status];
      const step = j.status === "running" ? (j.steps[j.step] || "Starting") : j.status === "done" ? "Saved to staging" : j.message || "";
      const v = j.result && j.result.verdict && VERDICT[j.result.verdict];
      return `<div class="card">
        <div class="row spread"><div class="grow"><b class="ellipsis" style="display:block">${esc(j.title)}</b>
          <div class="faint">${{ update: "Updating chart · ", rechart: "Recharting · " }[j.kind] || ""}${esc(step)}${j.by ? ` · from ${esc(j.by)}` : ""}</div></div>
          <span class="chip ${cc}">${ct}</span></div>
        ${j.status === "running" || j.status === "queued" ? `<div class="bar" style="margin-top:12px"><i style="width:${Math.round(j.progress * 100)}%"></i></div>` : ""}
        <div class="row" style="margin-top:12px">
          ${v ? `<span class="chip ${v[0]}">${v[1]}</span>` : ""}<span class="grow"></span>
          ${(j.status === "running" || j.status === "queued") && (owner() || j.by === S.me.name) ? `<button class="btn small" data-cancel="${j.id}">Cancel</button>` : ""}
          ${j.status === "done" ? `<button class="btn primary small" data-review="${esc(j.result.folder)}">Review</button>` : ""}
        </div></div>`;
    }).join("")}</div>` : `<div class="card empty">Nothing in the queue. Make a chart from the Create tab.</div>`,
    owner() && jobs.some((j) => ["done", "failed", "cancelled"].includes(j.status)) ? `<button class="btn" id="clear">Clear finished</button>` : "");
  $$("[data-cancel]").forEach((b) => b.onclick = async () => { await post(`/api/jobs/${b.dataset.cancel}/cancel`); refresh(); });
  $$("[data-review]").forEach((b) => b.onclick = () => openReview(b.dataset.review));
  const c = $("#clear"); if (c) c.onclick = async () => { await post("/api/jobs/clear"); refresh().then(renderQueue); };
}

// ---------- sorting (shared by Staging and Quickplay) ----------
const SORTS = [["artist", "Artist"], ["title", "Title"], ["album", "Album"], ["year", "Year"], ["recent", "Recently added"]];
const sortKey = (v) => { try { return localStorage.getItem("sort_" + v) || "artist"; } catch (e) { return "artist"; } };
const sortName = (t) => (t || "").replace(/^the\s+/i, "").toLowerCase();
function sortSongs(list, key) {
  const by = (f) => (a, b) => {
    const x = f(a), y = f(b);
    if (!x !== !y) return x ? -1 : 1;  // blanks last
    return x < y ? -1 : x > y ? 1 : 0;
  };
  const t = by((s) => sortName(s.title || s.name)), ar = by((s) => sortName(s.artist));
  const cmp = {
    artist: (a, b) => ar(a, b) || t(a, b),
    title: (a, b) => t(a, b) || ar(a, b),
    album: (a, b) => by((s) => sortName(s.album))(a, b) || ar(a, b) || t(a, b),
    year: (a, b) => by((s) => s.year)(a, b) || ar(a, b) || t(a, b),
    recent: (a, b) => b.modified - a.modified,
  }[key] || ((a, b) => ar(a, b) || t(a, b));
  return [...list].sort(cmp);
}
const sortSelect = (v) => `<label class="sortsel faint">Sort <select data-sort="${v}">${SORTS.map(([k, l]) =>
  `<option value="${k}" ${sortKey(v) === k ? "selected" : ""}>${l}</option>`).join("")}</select></label>`;
function wireSort(v, redraw) {
  const sel = $(`[data-sort="${v}"]`);
  if (sel) sel.onchange = () => { try { localStorage.setItem("sort_" + v, sel.value); } catch (e) { /* not kept */ } redraw(); };
}

// ---------- staging ----------
let stagingFilter = "all";
const MY_CHIP = `<span class="chip green">My chart</span>`;
const initials = (s) => (s || "?").split(/\s+/).slice(0, 2).map((w) => w[0]).join("").toUpperCase();
const coverHTML = (s, cls = "cover", w = 300) => s.has_cover
  ? `<img class="${cls}" src="/api/cover?${qs({ folder: s.folder, v: s.cover_v || 0, w })}" alt="" loading="lazy">`
  : `<div class="${cls} ph">${esc(initials(s.title || s.name))}</div>`;

async function renderStaging(reviewAll = false) {
  page("Staging", "New charts land here first. Check them, then publish to your live library.",
    `<div class="empty"><span class="spinner"></span>${reviewAll ? "<div>Checking every song…</div>" : ""}</div>`,
    `${owner() ? `<button class="btn" id="fillcovers">Find missing covers</button> ` : ""}<button class="btn" id="checkall">Check all</button>`);
  $("#checkall").onclick = () => renderStaging(true);
  if (owner()) $("#fillcovers").onclick = async (e) => {
    const missing = (S.staging ? S.staging.songs : []).filter((x) => x.ours && !x.has_cover).map((x) => x.folder);
    if (!missing.length) { toast("Every song already has a cover"); return; }
    e.target.disabled = true; e.target.textContent = `Finding ${missing.length} cover${missing.length === 1 ? "" : "s"}…`;
    try {
      const r = await post("/api/cover/fill", { folders: missing });
      toast(`Added ${r.done.length} cover${r.done.length === 1 ? "" : "s"}` + (r.missed.length ? ` · no clear match for ${r.missed.length} (open the song to pick one)` : ""));
    } catch (err) { toast(err.message); }
    renderStaging();
  };
  let r;
  try { r = await api("/api/staging?" + qs({ review_all: reviewAll })); } catch (e) { $("#main").innerHTML += `<p class="chip red">${esc(e.message)}</p>`; return; }
  S.staging = r;
  const made = r.songs.filter((s) => s.ours), hand = r.songs.filter((s) => !s.ours);
  const songs = sortSongs(stagingFilter === "mine" ? hand : made, sortKey("staging")).filter((s) => stagingFilter === "all" || stagingFilter === "mine" || s.status === stagingFilter);
  const counts = { all: made.length, draft: made.filter((s) => s.status === "draft").length, reviewed: made.filter((s) => s.status === "reviewed").length, mine: hand.length };
  $("#main").querySelector(".empty").outerHTML = `
    <div class="row spread wrap" style="margin-bottom:14px">
      <div class="tabs">${[["all", "All"], ["draft", "To review"], ["reviewed", "Reviewed"], ["mine", "My charts"]].map(([k, l]) =>
        `<button class="${stagingFilter === k ? "on" : ""}" data-filter="${k}">${l} <span class="faint">${counts[k]}</span></button>`).join("")}</div>
      <div class="row" style="gap:12px;min-width:0">${sortSelect("staging")}<span class="faint ellipsis">${esc(r.folder)}</span></div>
    </div>
    ${songs.length ? `<div class="songs">${songs.map((s) => {
      const v = s.verdict && VERDICT[s.verdict];
      return `<div class="song" data-open="${esc(s.folder)}" ${s.ours ? "" : `data-hand="1"`}>${coverHTML(s)}
        <div class="meta"><div class="t ellipsis">${esc(s.title || s.name)}</div>
          <div class="faint ellipsis">${esc(s.artist || "Unknown artist")}</div>
          <div class="row wrap" style="margin-top:8px;gap:6px">
            ${!s.ours ? (s.mine ? MY_CHIP : `<span class="chip">Not marked yet</span>`) : v ? `<span class="chip ${v[0]}">${v[0] === "green" ? "✓" : v[0] === "yellow" ? "!" : "✕"} ${v[0] === "green" ? "OK" : v[0] === "yellow" ? "Check" : "Problems"}</span>` : `<span class="chip">Not checked</span>`}
            ${s.status === "reviewed" && s.ours ? `<span class="chip accent">Reviewed</span>` : ""}${byChip(s)}
            ${owner() && s.ours ? `<button class="btn small danger" data-del="${esc(s.folder)}" data-title="${esc(s.title || s.name)}" style="margin-left:auto">Delete</button>` : ""}
          </div></div></div>`;
    }).join("")}</div>` : `<div class="card empty">No songs here yet.</div>`}`;
  $$("[data-filter]").forEach((b) => b.onclick = () => { stagingFilter = b.dataset.filter; renderStaging(); });
  wireSort("staging", () => renderStaging());
  $$("[data-open]").forEach((c) => c.onclick = () => openReview(c.dataset.open, !!c.dataset.hand));
  $$("[data-del]").forEach((b) => b.onclick = (e) => { e.stopPropagation(); deleteFromStaging(b.dataset.del, b.dataset.title); });
}

async function deleteFromStaging(folder, title) {
  if (!confirm(`Delete “${title}” from staging?\n\nIt goes to the Recycle Bin, so you can still get it back.`)) return;
  try { await post("/api/staging/remove", { folder }); toast("Moved to the Recycle Bin"); closeDrawer(); renderStaging(); } catch (e) { toast(e.message); }
}

function closeDrawer() { $$(".scrim, .drawer").forEach((e) => e.remove()); }

// how a chart plays, rated per part after playing it (friends too); the training runs read these
const RATE = [["good", "Good"], ["ok", "OK"], ["bad", "Bad"]];
const ISSUE_TEXT = { notes: ["Too many notes", "Too few notes"], chords: ["Too many chords", "Too few chords"],
  sustains: ["Holds too long", "Holds too short"], hopos: ["Too many hammer-ons", "Too few hammer-ons"] };
const flagged = (s, k) => s.ratings[k] === "ok" || s.ratings[k] === "bad";  // tags are for parts with something wrong
function issuesHTML(s, k) {
  const opts = owner() && s.issue_options[k];
  if (!opts) return "";
  return `<div class="issues ${flagged(s, k) ? "" : "hidden"}" data-issues="${k}">${opts.flatMap((i) => ["+", "-"].map((sg, n) =>
    `<button class="issue ${s.issues[k].includes(i + sg) ? "on" : ""}" data-issue="${i + sg}" data-inst="${k}">${ISSUE_TEXT[i][n]}</button>`)).join("")}</div>`;
}

function rateHTML(s) {
  const parts = ["guitar", "bass", "drums", "vocals"].filter((k) => s.tiers[k] !== "-1" && (s.tiers[k] || (k !== "vocals" && s.stems.includes(k))));
  if (!parts.length) return "";
  return `<div class="card"><h2>How does it play?</h2>
    <p class="faint" style="margin-top:0">Played it? Rate each part. If something's off, tap what. Once two charts agree, new charts adjust.</p>
    ${parts.map((k) => `<div class="row spread rate"><span>${k}</span><div class="tabs">${RATE.map(([v, l]) =>
      `<button class="${s.ratings[k] === v ? "on" : ""}" data-rate="${k}" data-v="${v}">${l}</button>`).join("")}</div></div>${issuesHTML(s, k)}`).join("")}
    <div class="row" style="margin-top:12px;gap:8px"><input id="ratenote" class="grow" placeholder="What's off? e.g. solo too sparse, chorus wrong" value="${esc(s.rating_note || "")}">
      <button class="btn small" id="savenote">Save</button></div>
    <div class="faint" id="ratedby" style="margin-top:6px">${s.rated_by ? `Last rated by ${esc(s.rated_by)}` : ""}</div></div>`;
}

function wireRating(dr, s) {
  const send = async (body) => {
    const r = await post("/api/song/rating", { folder: s.folder, ...body });
    Object.assign(s, { ratings: r.ratings, issues: r.issues, rating_note: r.rating_note, rated_by: r.rated_by });
    $$("[data-rate]", dr).forEach((b) => b.classList.toggle("on", s.ratings[b.dataset.rate] === b.dataset.v));
    $$("[data-issues]", dr).forEach((b) => b.classList.toggle("hidden", !flagged(s, b.dataset.issues)));
    $$("[data-issue]", dr).forEach((b) => b.classList.toggle("on", s.issues[b.dataset.inst].includes(b.dataset.issue)));
    $("#ratedby", dr).textContent = s.rated_by ? `Last rated by ${s.rated_by}` : "";
  };
  $$("[data-issue]", dr).forEach((b) => b.onclick = async () => {
    const k = b.dataset.inst, t = b.dataset.issue, had = s.issues[k].includes(t);
    const tags = s.issues[k].filter((x) => x.slice(0, -1) !== t.slice(0, -1)).concat(had ? [] : [t]);  // one direction per issue
    try { await send({ inst: k, tags }); } catch (e) { toast(e.message); }
  });
  $$("[data-rate]", dr).forEach((b) => b.onclick = async () => {
    const k = b.dataset.rate, v = s.ratings[k] === b.dataset.v ? "" : b.dataset.v;  // tap again to clear
    try { await send({ inst: k, rating: v }); toast(v ? `${k}: ${v === "ok" ? "OK" : v}` : `${k} rating cleared`); } catch (e) { toast(e.message); }
  });
  const note = $("#ratenote", dr);
  if (!note) return;
  const saveNote = async () => { try { await send({ note: note.value }); toast("Note saved"); } catch (e) { toast(e.message); } };
  $("#savenote", dr).onclick = saveNote;
  note.onkeydown = (e) => { if (e.key === "Enter") saveNote(); };
}

// keep or redo a song part by part
const RC_LEVEL = { expert: "Expert", hard: "Hard", medium: "Medium", easy: "Easy" };
function rcLabel(v, c) {
  return { keep: c.by === "ai" ? "Keep (AI)" : "Keep (yours)", ai: c.have ? "Redo with AI" : "Make with AI", make: "From Expert",
    words: "AI melody, your words", empty: c.have ? "Remove" : "Leave empty", none: c.have ? "Remove" : "Leave empty" }[v];
}
const rcSelect = (c, attrs) => `<select ${attrs}>${c.choices.map((v) => `<option value="${v}"${v === c.pick ? " selected" : ""}>${rcLabel(v, c)}</option>`).join("")}</select>`;

function rechartHTML(o) {
  if (o.blocked) return `<div class="card"><h2>Rechart</h2><p class="faint" style="margin:0">Can't rechart this song: ${esc(o.blocked)}.</p></div>`;
  return `<details class="card fold"><summary><h2>Rechart</h2></summary>
    <p class="faint">Choose what stays and what gets redone. Anything you keep stays note for note.</p>
    ${o.ai ? `<button class="btn small" id="rc-full">Full rechart: set everything to AI</button>` : ""}
    ${Object.entries(o.parts).map(([inst, row]) => `<div class="rc-row"><div class="rc-name">${inst}</div><div class="rc-levels">
      ${o.levels.map((lv) => `<label><span class="faint">${RC_LEVEL[lv]}</span>${rcSelect(row[lv], `data-rc="${inst}" data-lv="${lv}"`)}</label>`).join("")}</div></div>`).join("")}
    <div class="rc-row"><div class="rc-name">vocals</div><div class="rc-levels"><label><span class="faint">${o.vocals.have === "words" ? "Your lyrics" : "Part"}</span>${rcSelect(o.vocals, `data-rc="vocals"`)}</label></div></div>
    <div class="rc-row rc-levels" id="rc-more">
      <label id="rc-stylebox"><span class="faint">AI guitar style</span><select id="rc-style">${o.styles.map((v) => `<option value="${v}">${{ normal: "Normal", cleaner: "Cleaner (fewer notes)", busier: "Busier (more notes)" }[v]}</option>`).join("")}</select></label>
      ${o.sections.length ? `<label id="rc-sectionbox"><span class="faint">Redo with AI in</span><select id="rc-section"><option value="">Whole song</option>${o.sections.map((x) => `<option value="${x.from}">${esc(x.name)} only</option>`).join("")}</select></label>` : ""}
    </div>
    <div class="row wrap" style="margin-top:12px"><button class="btn primary" id="rc-go" disabled>Rechart</button><span class="faint" id="rc-sum"></span></div>
    ${o.ai ? "" : `<p class="faint" style="margin-bottom:0">AI parts need one whole-song audio file, and this song's audio is split up or missing.</p>`}</details>`;
}

function wireRechart(dr, s) {
  const o = s.rechart;
  if (!o || o.blocked) return;
  const go = $("#rc-go", dr), sum = $("#rc-sum", dr), picks = $$("[data-rc]", dr);
  const sel = (inst, lv) => picks.find((p) => p.dataset.rc === inst && p.dataset.lv === lv);
  const allow = (p, v, ok, fallback) => {
    const opt = [...p.options].find((x) => x.value === v);
    if (!opt) return;
    opt.disabled = !ok;
    if (!ok && p.value === v) p.value = fallback;
  };
  const onePart = () => { const x = $("#rc-section", dr); return x && x.value !== "" && Object.keys(o.parts).some((inst) => sel(inst, "expert").value === "ai") ? x.selectedOptions[0].text : ""; };
  const changes = () => picks.flatMap((p) => {
    const inst = p.dataset.rc, lv = p.dataset.lv, c = lv ? o.parts[inst][lv] : o.vocals;
    if (onePart() && !lv) return [];  // vocals stay out of a one-section redo
    if (p.value === "keep" || ((p.value === "empty" || p.value === "none") && !c.have)) return [];
    return [`${inst}${lv ? " " + RC_LEVEL[lv] : ""}: ${rcLabel(p.value, c)}`];
  });
  const update = () => {
    Object.keys(o.parts).forEach((inst) => {
      const top = sel(inst, "expert").value;
      o.levels.slice(1).forEach((lv) => {
        allow(sel(inst, lv), "make", top !== "empty", "empty");
        if (inst === "drums") allow(sel(inst, lv), "keep", top === "keep", top === "empty" ? "empty" : "make");  // cymbals and toms are shared
      });
    });
    const anyAi = Object.keys(o.parts).some((inst) => sel(inst, "expert").value === "ai");
    $("#rc-more", dr).style.display = anyAi ? "" : "none";
    $("#rc-stylebox", dr).style.display = sel("guitar", "expert").value === "ai" ? "" : "none";
    const n = changes().length;
    go.disabled = !n;
    sum.textContent = n ? `${n} change${n === 1 ? "" : "s"}` : "Nothing to change yet";
  };
  picks.forEach((p) => p.onchange = update);
  if ($("#rc-section", dr)) $("#rc-section", dr).onchange = update;
  const full = $("#rc-full", dr);
  if (full) full.onclick = () => {
    Object.keys(o.parts).forEach((inst) => o.levels.forEach((lv) => { sel(inst, lv).value = lv === "expert" ? "ai" : "make"; }));
    const sung = picks.find((p) => p.dataset.rc === "vocals");
    if ([...sung.options].some((x) => x.value === "ai")) sung.value = "ai";
    if ($("#rc-section", dr)) $("#rc-section", dr).value = "";
    update();
    toast("Everything is set to AI. Press Rechart to start");
  };
  update();
  go.onclick = async () => {
    const where = onePart() ? `${onePart()}: AI parts change only there, and their lower levels are rebuilt to match.\n\n` : "";
    if (!confirm(`${where}${changes().join("\n")}\n\nEverything else stays exactly as it is. Your original chart is backed up first.`)) return;
    const plan = { vocals: picks.find((p) => p.dataset.rc === "vocals").value };
    Object.keys(o.parts).forEach((inst) => { plan[inst] = Object.fromEntries(o.levels.map((lv) => [lv, sel(inst, lv).value])); });
    plan.guitar_style = $("#rc-style", dr).value;
    const part = $("#rc-section", dr);
    if (part && part.value !== "" && Object.keys(o.parts).some((inst) => sel(inst, "expert").value === "ai")) plan.section = Number(part.value);
    try { await post("/api/jobs/rechart", { folder: s.folder, plan }); toast("Recharting"); closeDrawer(); location.hash = "queue"; } catch (e) { toast(e.message); }
  };
}

async function openReview(folder, live = false) {
  closeDrawer();
  const scrim = document.createElement("div"); scrim.className = "scrim"; scrim.onclick = closeDrawer;
  const dr = document.createElement("div"); dr.className = "drawer";
  dr.innerHTML = `<div class="empty"><span class="spinner"></span><div>${live ? "Loading…" : "Checking the chart…"}</div></div>`;
  document.body.append(scrim, dr);
  let s;
  try { s = await api(live ? "/api/live/song?" + qs({ folder }) : "/api/staging/review?" + qs({ folder })); }
  catch (e) { dr.innerHTML = `<p class="chip red">${esc(e.message)}</p>`; return; }
  const v = live ? null : VERDICT[s.verdict];
  const order = { red: 0, yellow: 1, info: 2 };
  const findings = live ? [] : [...s.findings].sort((a, b) => order[a.level] - order[b.level]);
  const editable = owner() && (!live || s.ours || s.mine);  // other people's charts are only viewed, never changed
  const allClear = !findings.some((f) => f.level !== "info");
  const stems = live ? s.stems.filter((k) => !/^(preview|crowd)/i.test(k)) : ["guitar", "bass", "drums", "vocals"].filter((k) => s.stems.includes(k));
  const local = S.settings.local;
  dr.innerHTML = `
    <div class="row spread"><span class="faint">${s.mine ? "My chart" : live ? "Quickplay" : "Review"}</span><button class="btn small" id="x">Close</button></div>
    <div class="head" style="margin:14px 0 16px">${coverHTML(s, s.has_cover ? "" : "ph cover", 0)}
      <div class="grow"><h2 style="margin:0">${esc(s.title || s.name)}</h2>
        <div class="muted">${esc(s.artist)}${s.album ? ` · ${esc(s.album)}` : ""}${s.year ? ` · ${esc(s.year)}` : ""}</div>
        <div class="row wrap" style="margin-top:8px;gap:6px">${live
          ? (s.ours ? `<span class="chip accent">RockWizard</span>` : s.mine ? MY_CHIP : `<span class="chip">Human chart</span>`)
          : `<span class="chip ${v[0]}">${v[1]}</span><span class="chip">${esc(s.status)}</span>`}${byChip(s)}</div>
        <div class="tiers" style="margin-top:8px">${["guitar", "bass", "drums"].map((k) => `<span>${k} <b>${esc(s.tiers[k] || "–")}</b></span>`).join("")}</div>
      </div></div>
    ${owner() ? `<div class="card next"><h2>${live ? "Actions" : "Next"}</h2>
      ${live ? "" : `<div class="row">
        <button class="btn primary grow" id="pub">${ICON.rocket} Publish to live</button>
        ${s.status !== "reviewed" ? `<button class="btn" id="rev">Mark reviewed</button>` : ""}
      </div>`}
      ${local ? playHTML() : ""}
      ${s.ours ? "" : `<label class="check"><input type="checkbox" id="mine" ${s.mine ? "checked" : ""}> I charted this myself</label>`}
      ${moreHTML(s, live, local)}
      <p class="faint" style="margin-bottom:0">${live ? (s.ours ? "Changes here reach the Deck with the next Syncthing sync." : s.rechart ? "Only the parts you pick in Rechart ever change." : "Human-made charts are view-only here, so they're never changed by accident.")
        : "Publishing moves the song into your live folder; Syncthing takes it to the Deck."}</p>
    </div>` : ""}
    <div class="card"><div class="row spread"><h2 style="margin:0">Song details</h2>${editable ? `<button class="btn small" id="editdet">Edit</button>` : ""}</div>
      <div id="det" style="margin-top:10px">
        <div class="kv">${[["Title", s.title], ["Artist", s.artist], ["Album", s.album], ["Year", s.year], ["Genre", s.genre], ["Charter", s.charter]]
          .map(([k, val]) => `<span class="faint">${k}</span><span>${esc(val || "–")}</span>`).join("")}</div></div>
      ${editable ? `<div class="det-cover"><div class="row wrap"><button class="btn small" id="findcover">${s.has_cover ? "Change cover" : "Find cover art"}</button>
        <button class="btn small" id="upcover">Upload image</button><input type="file" id="coverfile" accept="image/*" hidden></div>
        <div id="covers"></div></div>` : ""}</div>
    ${live ? "" : `<details class="card fold" ${allClear ? "" : "open"}><summary><h2>Checks</h2>${allClear ? ` <span class="faint">All good</span>` : ""}</summary>
      ${findings.map((f) => `<div class="finding"><span class="lv ${f.level}"></span><div>${esc(f.text)}</div></div>`).join("")}</details>`}
    <details class="card fold"><summary><h2>Listen</h2></summary><div class="stems">
      ${stems.map((k) =>
        `<span>${k}</span><audio controls preload="none" src="/api/audio?${qs({ folder: s.folder, stem: k })}"></audio>`).join("")}</div></details>
    ${!live || s.ours ? rateHTML(s) : ""}
    ${owner() && s.rechart ? rechartHTML(s.rechart) : ""}`;
  $("#x", dr).onclick = closeDrawer;
  const refreshAfter = (folder2) => { openReview(folder2, live); if (view() === "staging") renderStaging(); if (view() === "live") renderLive(); };
  const unpub = $("#unpub", dr);
  if (unpub) unpub.onclick = async () => {
    if (!confirm("Move this song back to staging? It leaves your live library (and the Deck after the next sync).")) return;
    try { await post("/api/live/unpublish", { folder: s.folder }); toast("Moved back to staging"); closeDrawer(); renderLive(); } catch (e) { toast(e.message); }
  };
  if (!live || s.ours) wireRating(dr, s);
  if (owner()) wireRechart(dr, s);
  const mine = $("#mine", dr);
  if (mine) mine.onchange = async () => {
    try { await post("/api/song/mine", { folder: s.folder, mine: mine.checked }); refreshAfter(s.folder); }
    catch (e) { mine.checked = !mine.checked; toast(e.message); }
  };
  if (!editable) { if (owner()) wireOpenPC(dr, s); return; }
  $("#editdet", dr).onclick = () => {
    const f = (id, label, val, type = "text") => `<div><label class="field">${label}</label><input id="${id}" type="${type}" value="${esc(val || "")}"></div>`;
    $("#det", dr).innerHTML = `<div class="form2">
        ${f("d_name", "Title", s.title)}${f("d_artist", "Artist", s.artist)}${f("d_album", "Album", s.album)}
        ${f("d_year", "Year", s.year)}${f("d_genre", "Genre", s.genre)}${f("d_charter", "Charter", s.charter)}</div>
      <label class="item check" style="border:0;padding:10px 0 0"><input type="checkbox" id="d_rename" checked>
        <div class="grow">Tidy it like Tidy names<div class="faint">Fixes capitals, removes (…) and […] tags from the title, and renames the folder “Artist - Song Title”</div></div></label>
      <div class="row" style="margin-top:12px"><button class="btn primary" id="d_save">Save</button><button class="btn" id="d_cancel">Cancel</button></div>`;
    $("#editdet", dr).remove();
    $("#d_cancel", dr).onclick = () => openReview(s.folder, live);
    $("#d_save", dr).onclick = async () => {
      const fields = {}; ["name", "artist", "album", "year", "genre", "charter"].forEach((k) => fields[k] = $("#d_" + k, dr).value);
      try { const r = await post("/api/song/details", { folder: s.folder, fields, rename: $("#d_rename", dr).checked }); toast("Saved to the song folder"); refreshAfter(r.folder); }
      catch (e) { toast(e.message); }
    };
  };
  $("#findcover", dr).onclick = async () => {
    const box = $("#covers", dr);
    box.innerHTML = `<div class="empty"><span class="spinner"></span></div>`;
    try {
      const r = await api("/api/cover/search?" + qs({ folder: s.folder }));
      box.innerHTML = r.covers.length ? `<div class="covergrid">${r.covers.map((c, i) => `<button class="coverpick" data-i="${i}" title="${esc(c.album)}">
          <img src="${esc(c.thumb)}" alt="" loading="lazy"><div class="ellipsis">${esc(c.album || "Unknown album")}</div>
          <div class="faint ellipsis">${esc(c.year)} · ${esc(c.source)}${c.best ? " · best match" : ""}</div></button>`).join("")}</div>`
        : `<p class="muted">No covers found. Check the artist and title in Song details, or upload an image.</p>`;
      $$(".coverpick", box).forEach((b) => b.onclick = async () => {
        b.disabled = true;
        try { await post("/api/cover/set", { folder: s.folder, url: r.covers[b.dataset.i].url }); toast("Cover saved"); refreshAfter(s.folder); }
        catch (e) { toast(e.message); b.disabled = false; }
      });
    } catch (e) { box.innerHTML = `<p class="chip red">${esc(e.message)}</p>`; }
  };
  $("#upcover", dr).onclick = () => $("#coverfile", dr).click();
  $("#coverfile", dr).onchange = async (e) => {
    const file = e.target.files[0]; if (!file) return;
    const fd = new FormData(); fd.append("folder", s.folder); fd.append("file", file);
    try { await api("/api/cover/upload", { method: "POST", body: fd }); toast("Cover saved"); refreshAfter(s.folder); }
    catch (err) { toast(err.message); }
  };
  wireOpenPC(dr, s);
  if (live) return;
  $("#pub", dr).onclick = async () => {
    let r = await post("/api/staging/publish", { folder: s.folder });
    if (!r.ok && confirm(`${r.message}.\n\nPublish anyway?`)) r = await post("/api/staging/publish", { folder: s.folder, force: true });
    toast(r.message);
    if (r.ok) { closeDrawer(); if (view() === "staging") renderStaging(); }
  };
  const rev = $("#rev", dr);
  if (rev) rev.onclick = async () => { await post("/api/staging/status", { folder: s.folder, status: "reviewed" }); toast("Marked as reviewed"); openReview(folder); if (view() === "staging") renderStaging(); };
  $("#remove", dr).onclick = () => deleteFromStaging(s.folder, s.title || s.name);
}

const GAMES = [["yarg", "YARG"], ["clonehero", "Clone Hero"]];
const savedGame = () => { try { return localStorage.getItem("play_game") || "yarg"; } catch (e) { return "yarg"; } };
const playHTML = () => `<div class="play"><button class="btn" id="play">${ICON.play} Play</button>
  <select id="game" aria-label="Game to play in">${GAMES.map(([k, l]) => `<option value="${k}" ${savedGame() === k ? "selected" : ""}>${l}</option>`).join("")}</select></div>`;

function moreHTML(s, live, local) {
  const items = [
    live && s.ours ? `<button class="btn small" id="unpub">Move back to staging</button>` : "",
    local ? `<button class="btn small" data-open-pc="moonscraper">Open in Moonscraper</button>` : "",
    local ? `<button class="btn small" data-open-pc="folder">${ICON.folder} Folder</button>` : "",
    live ? "" : `<button class="btn small danger" id="remove">Delete</button>`,
  ].join("");
  return items ? `<details class="more"><summary>More options</summary><div class="row wrap">${items}</div></details>` : "";
}

function wireOpenPC(dr, s) {
  $$("[data-open-pc]", dr).forEach((b) => b.onclick = async () => {
    try { await post("/api/open", { what: b.dataset.openPc, folder: s.folder }); } catch (e) { toast(e.message); }
  });
  const play = $("#play", dr), game = $("#game", dr);
  if (!play) return;
  game.onchange = () => { try { localStorage.setItem("play_game", game.value); } catch (e) { /* not kept */ } };
  play.onclick = async () => {
    try { await post("/api/open", { what: game.value, folder: s.folder }); toast(`Opening ${game.selectedOptions[0].text}`); } catch (e) { toast(e.message); }
  };
}

// ---------- live library (Quickplay) ----------
let liveFilter = "all", liveQuery = "", liveShown = 120;
async function renderLive() {
  page("Quickplay", "Your live library: everything here syncs to the Deck.",
    `<div class="empty"><span class="spinner"></span></div>`);
  let r;
  try { r = await api("/api/live"); } catch (e) { $("#main").innerHTML += `<p class="chip red">${esc(e.message)}</p>`; return; }
  S.live = r;
  $("#main").querySelector(".empty").outerHTML = `
    <div class="row wrap" style="margin-bottom:14px;gap:10px">
      <input id="lq" type="search" class="grow" placeholder="Search ${r.songs.length} songs by title, artist or album" value="${esc(liveQuery)}" style="min-width:200px">
      <div class="tabs">${[["all", "All"], ["ours", "RockWizard"], ["rate", "To rate"], ["mine", "My charts"], ["human", "Human charts"], ["nocover", "No cover"]].map(([k, l]) =>
        `<button class="${liveFilter === k ? "on" : ""}" data-lf="${k}">${l}</button>`).join("")}</div>
      ${sortSelect("live")}
    </div>
    <div id="livegrid"></div>`;
  const draw = () => {
    const q = liveQuery.toLowerCase().trim();
    const songs = sortSongs(r.songs, sortKey("live")).filter((s) => (liveFilter === "all" || (liveFilter === "ours" ? s.ours : liveFilter === "rate" ? s.ours && !Object.keys(s.ratings || {}).length
        : liveFilter === "mine" ? s.mine : liveFilter === "human" ? !s.ours : !s.has_cover))
      && (!q || `${s.title} ${s.artist} ${s.album} ${s.name}`.toLowerCase().includes(q)));
    $("#livegrid").innerHTML = songs.length ? `<div class="faint" style="margin-bottom:10px">${songs.length} song${songs.length === 1 ? "" : "s"}</div>
      <div class="songs">${songs.slice(0, liveShown).map((s) => `<div class="song" data-live="${esc(s.folder)}">${coverHTML(s)}
        <div class="meta"><div class="t ellipsis">${esc(s.title || s.name)}</div>
          <div class="faint ellipsis">${esc(s.artist || "Unknown artist")}</div>
          ${s.ours || s.mine || s.added_by ? `<div class="row wrap" style="margin-top:8px;gap:6px">${s.ours ? `<span class="chip accent">RockWizard</span>` : s.mine ? MY_CHIP : ""}${byChip(s)}</div>` : ""}</div></div>`).join("")}</div>
      ${songs.length > liveShown ? `<div style="text-align:center;margin:18px 0"><button class="btn" id="more">Show more (${songs.length - liveShown} left)</button></div>` : ""}`
      : `<div class="card empty">No songs match.</div>`;
    $$("[data-live]").forEach((c) => c.onclick = () => openReview(c.dataset.live, true));
    const m = $("#more"); if (m) m.onclick = () => { liveShown += 120; draw(); };
  };
  let t;
  $("#lq").oninput = (e) => { clearTimeout(t); t = setTimeout(() => { liveQuery = e.target.value; liveShown = 120; draw(); }, 150); };
  $$("[data-lf]").forEach((b) => b.onclick = () => { liveFilter = b.dataset.lf; liveShown = 120; $$("[data-lf]").forEach((x) => x.classList.toggle("on", x === b)); draw(); });
  wireSort("live", () => { liveShown = 120; draw(); });
  draw();
}

// ---------- library ----------
let libTab = "update";
function folderField(id, value) {
  return `<div class="row"><input id="${id}" value="${esc(value)}" class="grow">
    ${desk() ? `<button class="btn" data-browse="${id}">${ICON.folder}</button>` : ""}
    <button class="btn primary" data-load="${id}">Load</button></div>`;
}
function wireFolder(load) {
  $$("[data-browse]").forEach((b) => b.onclick = async () => {
    const d = await desk().pick_folder(); if (d) { $("#" + b.dataset.browse).value = d; load(); }
  });
  $$("[data-load]").forEach((b) => b.onclick = load);
}

function renderLibrary() {
  page("Library", "Keep your songs tidy and up to date.", `
    <div class="tabs" style="margin-bottom:14px">${[["update", "Update charts"], ["tidy", "Tidy names"], ["dupes", "Duplicates"]].map(([k, l]) =>
      `<button class="${libTab === k ? "on" : ""}" data-lib="${k}">${l}</button>`).join("")}</div>
    <div id="lib"></div>`);
  $$("[data-lib]").forEach((b) => b.onclick = () => { libTab = b.dataset.lib; renderLibrary(); });
  ({ update: libUpdate, tidy: libTidy, dupes: libDupes })[libTab]();
}

function libUpdate() {
  $("#lib").innerHTML = `<div class="card"><p class="muted" style="margin-top:0">Remake charts with the newest models, from the audio already in each song folder. The old chart is backed up first. Only songs RockWizard made are listed; other charts are never touched.</p>
    ${folderField("uf", S.settings.songs_dir || "")}<div id="ulist" style="margin-top:12px"></div></div>`;
  const load = async () => {
    const box = $("#ulist"); box.innerHTML = `<span class="spinner"></span>`;
    try {
      const r = await api("/api/library/ours?" + qs({ folder: $("#uf").value }));
      box.innerHTML = r.songs.length ? `<div class="list">${r.songs.map((s) => `
        <label class="item check"><input type="checkbox" class="us" value="${esc(s.folder)}" checked><span class="grow ellipsis">${esc(s.name)}</span>
        <span class="faint">${new Date(s.modified * 1000).toLocaleDateString()}</span></label>`).join("")}</div>
        <button class="btn primary" id="urun" style="margin-top:12px">Update selected</button>` : `<div class="empty">No RockWizard songs in that folder.</div>`;
      const run = $("#urun"); if (run) run.onclick = async () => {
        const folders = $$(".us:checked").map((c) => c.value);
        await post("/api/jobs/update", { folders }); toast(`${folders.length} charts queued`); location.hash = "queue";
      };
    } catch (e) { box.innerHTML = `<p class="chip red">${esc(e.message)}</p>`; }
  };
  wireFolder(load); load();
}

function libTidy() {
  const o = S.tidyOpts || (S.tidyOpts = { ini: true, parens: true, brackets: true, caps: true });
  $("#lib").innerHTML = `<div class="card"><p class="muted" style="margin-top:0">Give every song the same “Artist - Song Title” name. Nothing changes until you press Tidy selected, and it can be undone (song.ini too).</p>
    ${folderField("tf", S.settings.tidy_dir || S.settings.live_dir || S.settings.songs_dir || "")}
    <div class="row wrap" style="margin-top:12px;gap:18px">
      <label class="check"><input type="checkbox" id="oini" ${o.ini ? "checked" : ""}> Artist - Song Title (from song.ini)</label>
      <label class="check"><input type="checkbox" id="opar" ${o.parens ? "checked" : ""}> Remove (parentheses)</label>
      <label class="check"><input type="checkbox" id="obra" ${o.brackets ? "checked" : ""}> Remove [brackets]</label>
      <label class="check"><input type="checkbox" id="ocap" ${o.caps ? "checked" : ""}> Fix capitals</label></div>
    <div id="tlist" style="margin-top:12px"></div></div>`;
  const opts = () => ({ ini: $("#oini").checked, parens: $("#opar").checked, brackets: $("#obra").checked, caps: $("#ocap").checked });
  const load = async () => {
    Object.assign(o, opts());
    const box = $("#tlist"); box.innerHTML = `<span class="spinner"></span>`;
    try {
      const r = await api("/api/tidy?" + qs({ folder: $("#tf").value, ...o }));
      box.innerHTML = (r.proposals.length ? `<div class="list">${r.proposals.map((p) => `
        <label class="item check"><input type="checkbox" class="tp" value="${esc(p.path)}" ${p.note ? "disabled" : p.check ? "" : "checked"}>
          <div class="grow"><div class="faint ellipsis">${esc(p.name)}</div><div class="ellipsis">→ ${p.ini_only ? `<span class="muted">same folder name; fixes the title/artist in song.ini</span>` : esc(p.new_name)}${p.note ? ` <span style="color:var(--warn)">(skipped: ${esc(p.note)})</span>` : ""}${p.check ? ` <span style="color:var(--warn)">(${esc(p.check)})</span>` : ""}</div></div></label>`).join("")}</div>`
        : `<div class="empty">All names are already tidy.</div>`) + `
        <div class="row" style="margin-top:12px"><button class="btn primary" id="tapply" ${r.proposals.length ? "" : "disabled"}>Tidy selected</button>
        <button class="btn" id="tundo" ${r.can_undo ? "" : "disabled"}>Undo last rename</button></div>`;
      $("#tapply").onclick = async () => {
        const res = await post("/api/tidy/apply", { folder: r.folder, paths: $$(".tp:checked").map((c) => c.value), ...o });
        toast(`Tidied ${res.renamed}` + (res.errors.length ? ` · ${res.errors[0]}` : "")); load();
      };
      $("#tundo").onclick = async () => { const res = await post("/api/tidy/undo", { folder: r.folder }); toast(`Restored ${res.restored}`); load(); };
    } catch (e) { box.innerHTML = `<p class="chip red">${esc(e.message)}</p>`; }
  };
  $$("#oini, #opar, #obra, #ocap").forEach((c) => c.onchange = load);
  wireFolder(load); load();
}

function libDupes() {
  $("#lib").innerHTML = `<div class="card"><p class="muted" style="margin-top:0">The same song more than once. Copies with exactly the same chart are ticked for you; different versions (live, another charter…) are listed but left alone. Removed copies go to the Recycle Bin.</p>
    ${folderField("df", S.settings.tidy_dir || S.settings.live_dir || S.settings.songs_dir || "")}<div id="dlist" style="margin-top:12px"></div></div>`;
  const load = async () => {
    const box = $("#dlist"); box.innerHTML = `<span class="spinner"></span>`;
    try {
      const r = await api("/api/dupes?" + qs({ folder: $("#df").value }));
      box.innerHTML = r.groups.length ? r.groups.map((g) => `<div style="margin-top:12px"><b>${esc(g[0].artist ? g[0].artist + " - " : "")}${esc(g[0].title)}</b>
        ${g.map((c) => `<label class="item check"><input type="checkbox" class="dp" value="${esc(c.path)}" ${c.identical ? "checked" : ""}>
          <div class="grow"><div class="ellipsis">${esc(c.name)}</div>
          <div class="faint">${c.keep ? `<span style="color:var(--good)">keep (suggested)</span>` : c.identical ? "same chart as the kept copy" : "different version"} · ${esc(c.charter)} · ${esc(c.parts.join(", ") || "parts not listed")} · ${c.size_mb} MB</div></div></label>`).join("")}</div>`).join("")
        + `<button class="btn primary" id="drm" style="margin-top:14px">Move ticked copies to the Recycle Bin</button>`
        : `<div class="empty">No duplicates found.</div>`;
      const b = $("#drm"); if (b) b.onclick = async () => {
        const paths = $$(".dp:checked").map((c) => c.value);
        if (!paths.length || !confirm(`Move ${paths.length} folder${paths.length === 1 ? "" : "s"} to the Recycle Bin?`)) return;
        const res = await post("/api/dupes/remove", { paths }); toast(`Moved ${res.removed} to the Recycle Bin` + (res.errors.length ? ` · ${res.errors[0]}` : "")); load();
      };
    } catch (e) { box.innerHTML = `<p class="chip red">${esc(e.message)}</p>`; }
  };
  wireFolder(load); load();
}

// ---------- settings ----------
async function renderSettings() {
  if (!owner()) {
    page("Settings", "", `<div class="card"><h2>You</h2><p>You're using RockWizard as <b>${esc(S.me.name)}</b>.
      Songs you add are marked as suggested by you.</p><button class="btn" id="rename">Change name</button></div>`);
    $("#rename").onclick = async () => { await askName(true); renderNav(); render(); };
    return;
  }
  const st = S.settings, local = st.local;
  const field = (id, label, hint, kind = "folder") => `<div style="margin-top:14px"><label class="field">${label}</label>
    <div class="row"><input id="${id}" value="${esc(st[id] || "")}" class="grow">${desk() && kind ? `<button class="btn" data-pick="${id}" data-kind="${kind}">Browse</button>` : ""}</div>
    ${hint ? `<div class="faint" style="margin-top:4px">${hint}</div>` : ""}</div>`;
  page("Settings", "", `
    <div class="card"><h2>Folders and programs</h2>
      ${field("songs_dir", "Staging folder", "New charts are saved here for review first.")}
      ${field("live_dir", "Live folder", "Where published songs go (your Quickplay folder, which Syncthing sends to the Deck).")}
      ${field("charter", "Charter name", "Written into each song.", "")}
      ${field("clonehero_exe", "Clone Hero", "Clone Hero.exe, for Play in Clone Hero.", "file")}
      ${field("yarg_exe", "YARG", "YARG.exe, for Play in YARG.", "file")}
      ${field("moonscraper_exe", "Moonscraper", "Moonscraper Chart Editor.exe, for Open in Moonscraper.", "file")}
      <button class="btn primary" id="save" style="margin-top:16px">Save</button></div>
    <div class="card"><h2>Learned from your ratings</h2><p class="faint" style="margin-top:0">New charts adjust once two charts agree. A new model starts fresh.</p><div id="learned"><span class="spinner"></span></div></div>
    <div class="card" id="phone"><h2>Use RockWizard on your phone</h2><div class="empty"><span class="spinner"></span></div></div>
    <div class="card"><h2>Steam Deck sync</h2><div id="syncbox" class="muted"></div></div>
    <div class="card"><h2>YouTube downloader</h2><p class="muted" style="margin-top:0">YouTube changes often and older downloader versions stop working. If searching or downloading fails, update it.</p>
      <button class="btn" id="dl">Update downloader</button> <span id="dlmsg" class="faint"></span></div>`);
  api("/api/learned").then((r) => {
    const box = $("#learned"); if (!box) return;
    box.innerHTML = r.items.map((x) => `<div class="row spread rc-row"><span><span style="text-transform:capitalize">${esc(x.inst)}</span>: ${ISSUE_TEXT[x.issue][x.net > 0 ? 0 : 1].toLowerCase()}</span>
      <span class="${x.steps ? "chip green" : "faint"}">${x.steps ? `Adjusting · ${Math.abs(x.net)} charts` : `${Math.abs(x.net)} of ${x.needs} charts`}</span></div>`).join("")
      || `<p class="faint" style="margin:0">Nothing yet. Rate a part OK or Bad on a RockWizard song and tap what's off.</p>`;
  }).catch((e) => { const box = $("#learned"); if (box) box.innerHTML = `<p class="chip red">${esc(e.message)}</p>`; });
  $$("[data-pick]").forEach((b) => b.onclick = async () => {
    const v = b.dataset.kind === "file" ? await desk().pick_file() : await desk().pick_folder();
    if (v) $("#" + b.dataset.pick).value = v;
  });
  $("#save").onclick = async () => {
    const body = {}; ["songs_dir", "live_dir", "charter", "clonehero_exe", "yarg_exe", "moonscraper_exe"].forEach((k) => body[k] = $("#" + k).value);
    Object.assign(S.settings, await post("/api/settings", body)); toast("Saved");
  };
  $("#dl").onclick = async () => { $("#dlmsg").textContent = "Updating…"; const r = await post("/api/downloader/update"); $("#dlmsg").textContent = r.message; };
  if (!("sync" in S.status)) { try { S.status = await api("/api/status"); } catch (e) { /* shown as not set up */ } }
  const sy = S.status.sync;
  $("#syncbox").innerHTML = !sy ? "Syncthing isn't set up on this PC, or no live folder is set."
    : sy.state === "not shared" ? "The live folder isn't one of Syncthing's shared folders."
    : `Folder “${esc(sy.label)}”: ${esc(sy.state)}. ${(sy.devices || []).map((d) => `${esc(d.name)} ${d.connected ? `${Math.floor(d.completion)}% synced` : "offline"}`).join(" · ")}
       <div style="margin-top:10px"><button class="btn small" id="rescan">Sync now</button></div>`;
  const rs = $("#rescan"); if (rs) rs.onclick = async () => { try { await post("/api/sync/rescan"); toast("Syncthing is checking for changes"); } catch (e) { toast(e.message); } };
  const devs = await api("/api/devices");
  const ago = (t) => { const m = Math.round((Date.now() / 1000 - t) / 60); return m < 2 ? "now" : m < 60 ? `${m} min ago` : m < 1440 ? `${Math.round(m / 60)} h ago` : `${Math.round(m / 1440)} d ago`; };
  $("#phone").insertAdjacentHTML("afterend", `<div class="card"><h2>Devices</h2>
    <p class="muted" style="margin-top:0">Phones and browsers that have opened RockWizard. Yours can do everything; friends can add songs, look and listen, and their songs are marked with their name.</p>
    ${devs.length ? devs.map((d) => `<div class="item"><div class="grow"><b>${esc(d.name || "No name yet")}</b>${d.this ? ` <span class="faint">(this one)</span>` : ""}
        <div class="faint">${esc(d.kind)} · last seen ${ago(d.last)}</div></div>
        <label class="row faint" style="gap:6px;white-space:nowrap"><input type="checkbox" data-mine="${d.id}" ${d.owner ? "checked" : ""}> Mine</label>
        <button class="btn small" data-forget="${d.id}">Forget</button></div>`).join("")
      : `<div class="empty">No phones yet. Scan the QR code above.</div>`}</div>`);
  $$("[data-mine]").forEach((c) => c.onchange = async () => { await post(`/api/devices/${c.dataset.mine}`, { owner: c.checked }); toast(c.checked ? "Marked as yours" : "Now treated as a friend's device"); });
  $$("[data-forget]").forEach((b) => b.onclick = async () => {
    if (!confirm("Forget this device? It stays connected, but asks for a name again next time.")) return;
    await api(`/api/devices/${b.dataset.forget}`, { method: "DELETE" }); renderSettings();
  });
  if (desk()) {
    $("#phone").insertAdjacentHTML("beforebegin", `<div class="card"><h2>Server</h2>
      <div class="row wrap" style="gap:12px"><div class="grow"><span class="chip green">Running</span>
        <span class="muted" style="margin-left:6px">RockWizard's background server, which also lets your phone connect.</span></div>
        <button class="btn" id="stopsrv">Stop server</button></div></div>`);
    $("#stopsrv").onclick = async () => {
      const busy = S.status.busy || S.status.queued;
      if (!confirm(busy ? "A conversion is still running. Stopping the server cancels it. Stop anyway?"
                        : "Stop the server? Your phone won't be able to connect until you start it again.")) return;
      await post("/api/server/stop");
      desk().server_stopped();
    };
  }
  if (local) {
  const su = await api("/api/startup");
  $("#phone").insertAdjacentHTML("beforebegin", `<div class="card"><h2>Start with Windows</h2>
    <label class="item check" style="border:0;padding:0"><input type="checkbox" id="startup" ${su.enabled ? "checked" : ""}>
      <div class="grow">Run RockWizard in the background when you sign in<div class="faint">So your phone can always reach it while the PC is on. No window opens; the desktop app uses the same server.</div></div></label></div>`);
  $("#startup").onchange = async (e) => {
    try { const r = await post("/api/startup", { enabled: e.target.checked }); e.target.checked = r.enabled; toast(r.enabled ? "RockWizard will start when you sign in" : "RockWizard won't start with Windows"); }
    catch (err) { e.target.checked = !e.target.checked; toast(err.message); }
  };
  }
  const p = await api("/api/pairing");
  $("#phone").innerHTML = `<h2>${local ? "Use RockWizard on your phone" : "Share RockWizard"}</h2>
    <div class="row wrap" style="align-items:flex-start;gap:20px">
      ${p.qr ? `<div class="qr">${p.qr}</div>` : ""}
      <div class="grow" style="min-width:240px">
        ${local ? `<p style="margin-top:0">Scan with your iPhone's camera, then in Safari tap <b>Share → Add to Home Screen</b>. Your PC still does the work.</p>
        <p class="faint">At home this works over Wi-Fi. Away from home, install <b>Tailscale</b> (free) on this PC and your iPhone and sign in to both; the link then uses the Tailscale address (100.x.x.x).</p>`
        : `<p style="margin-top:0">Let a friend scan this with their phone's camera. They'll be asked their name, and songs they add are marked with it.</p>
        <p class="faint">They need to be on your Wi-Fi, or on your Tailscale network when away from home.</p>`}
        ${p.urls.map((u) => `<div style="margin-top:6px"><code>${esc(u)}</code></div>`).join("")}
        ${local ? `<button class="btn small" id="newkey" style="margin-top:12px">New pairing key (unpairs other phones)</button>` : ""}</div></div>`;
  if (local) $("#newkey").onclick = async () => { if (confirm("Make a new key? Phones paired before will need to scan again.")) { await post("/api/pairing/reset"); renderSettings(); } };
}

// ---------- who's using the app ----------
function askName(again = false) {
  return new Promise((done) => {
    const scrim = document.createElement("div"); scrim.className = "scrim";
    const m = document.createElement("div"); m.className = "modal card";
    m.innerHTML = `<h2 style="margin-top:0">${again ? "Change your name" : "Welcome to RockWizard!"}</h2>
      <p class="muted">${again ? "" : "What's your name? Songs you add get your name on them, so everyone knows who suggested them."}</p>
      <input id="myname" type="text" maxlength="40" placeholder="Your name" value="${esc(again ? S.me.name : "")}" style="width:100%">
      <div class="row" style="margin-top:14px;justify-content:flex-end">${again ? `<button class="btn" id="nmcancel">Cancel</button>` : ""}
        <button class="btn primary" id="nmok">${again ? "Save" : "Let's go"}</button></div>`;
    document.body.append(scrim, m);
    const inp = $("#myname", m); inp.focus();
    const close = () => { scrim.remove(); m.remove(); done(); };
    const save = async () => {
      try { S.me = await post("/api/me", { name: inp.value }); close(); } catch (e) { toast(e.message); }
    };
    $("#nmok", m).onclick = save;
    inp.onkeydown = (e) => { if (e.key === "Enter") save(); };
    const c = $("#nmcancel", m); if (c) c.onclick = close;
  });
}

// ---------- start ----------
(async () => {
  try { S.settings = await api("/api/settings"); S.me = await api("/api/me"); } catch (e) { $("#main").innerHTML = `<div class="card empty">${esc(e.message)}</div>`; return; }
  if (!S.me.owner && !S.me.name) await askName();
  renderNav(); render(); refresh();
  setInterval(refresh, 1500);
  const h = location.hash.slice(1);
  if (h.startsWith("review=")) openReview(decodeURIComponent(h.slice(7)));
})();
