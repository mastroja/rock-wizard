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

// ---------- song previews: a 30-second clip behind each cover, one playing at a time ----------
const player = new Audio();
const CLIP = 30;  // seconds of a video that are played
let playingBtn = null, clipFrom = 0, clipEnd = 0;
function stopPreview() {
  player.pause();
  if (playingBtn) { playingBtn.classList.remove("playing", "loading"); playingBtn = null; }
}
player.onended = stopPreview;
// a whole video starts a little way in (past the intro) and stops after CLIP seconds
player.onloadedmetadata = () => {
  if (!clipFrom) return;
  const at = Math.min(clipFrom, (player.duration || 0) / 3);
  player.currentTime = at; clipEnd = at + CLIP;
};
player.ontimeupdate = () => { if (clipEnd && player.currentTime >= clipEnd) stopPreview(); };
player.onplaying = () => { if (playingBtn) { playingBtn.classList.remove("loading"); playingBtn.classList.add("playing"); } };
player.onerror = () => { if (playingBtn) { toast("That preview wouldn't play"); stopPreview(); } };
const PLAY_SVG = `<svg class="i-play" viewBox="0 0 24 24"><path d="M8.5 5.5v13l10.5-6.5z"/></svg>`;
const PP_HTML = `<span class="pp">${PLAY_SVG}<span class="spinner"></span><span class="eq"><i></i><i></i><i></i></span></span>`;
// A cover with a play button over it. With no clip known yet it is a plain picture; setSample adds the rest.
const artHTML = (s) => `<button class="art${s.audio ? "" : " none"}" data-audio="${esc(s.audio || "")}" aria-label="Play a preview">
  ${s.art ? `<img src="${esc(s.art)}" alt="" loading="lazy">` : `<span class="ph">${esc((s.title || "?").trim()[0].toUpperCase())}</span>`}
  ${PP_HTML}</button>`;
function setSample(btn, m) {
  if (!m || !m.audio || !btn || !btn.isConnected) return false;
  btn.dataset.audio = m.audio;
  btn.classList.remove("none");
  const ph = $(".ph", btn);
  if (ph) ph.outerHTML = `<img src="${esc(m.art)}" alt="">`;
  return true;
}
async function fillSample(btn, artist, title) {
  try { return setSample(btn, await api("/api/sample?" + qs({ artist, title }))); } catch (e) { return false; }
}
document.addEventListener("click", (e) => {
  const a = e.target.closest("[data-audio]");
  if (!a || !a.dataset.audio) return;
  const mine = playingBtn === a;
  stopPreview();
  if (mine) return;
  playingBtn = a; a.classList.add("loading");
  clipFrom = Number(a.dataset.from || 0); clipEnd = 0;
  player.src = a.dataset.audio;
  player.play().catch(() => {});
});

function confetti() {
  const colors = ["#8b5cff", "#d54cff", "#ff4ca8", "#35d08b", "#ffb547", "#eef0ff"];
  const box = document.createElement("div"); box.className = "confetti";
  box.innerHTML = Array.from({ length: 70 }, (_, i) => `<i style="left:${Math.random() * 100}%;background:${colors[i % colors.length]};--dx:${Math.round(Math.random() * 240 - 120)}px;--r:${Math.round(Math.random() * 900 - 450)}deg;animation-delay:${(Math.random() * 0.35).toFixed(2)}s;animation-duration:${(1.6 + Math.random() * 1.2).toFixed(2)}s"></i>`).join("");
  document.body.append(box);
  setTimeout(() => box.remove(), 3400);
}

// ---------- the Taylor Swift checkpoint (a household joke, dressed up as a system error) ----------
// The sound: this PC's own Windows error sound when the server can hand it over, or else a buzz
// made here. Browsers only allow sound after a tap, so armErrorSound runs inside the tap itself.
let errCtx = null, errBuf = null;
function armErrorSound() {
  try {
    errCtx = errCtx || new (window.AudioContext || window.webkitAudioContext)();
    if (errCtx.state === "suspended") errCtx.resume();
  } catch (e) { /* no sound on this device */ }
}
async function loadErrorSound() {
  if (errBuf || !errCtx) return;
  try { errBuf = await errCtx.decodeAudioData(await (await fetch("/api/errorsound")).arrayBuffer()); } catch (e) { /* the buzz plays instead */ }
}
function errorSound() {
  if (!errCtx) return;
  try {
    if (errBuf) { const src = errCtx.createBufferSource(); src.buffer = errBuf; src.connect(errCtx.destination); src.start(); return; }
    const t = errCtx.currentTime;
    [[330, 0], [220, 0.16]].forEach(([hz, at]) => {  // two falling buzzes
      const o = errCtx.createOscillator(), g = errCtx.createGain();
      o.type = "square"; o.frequency.value = hz;
      g.gain.setValueAtTime(0.12, t + at); g.gain.exponentialRampToValueAtTime(0.001, t + at + 0.22);
      o.connect(g).connect(errCtx.destination); o.start(t + at); o.stop(t + at + 0.24);
    });
  } catch (e) { /* no sound */ }
}

const TAUNTS = ["Nope", "Too slow", "Missed", "So close", "Not today"];
// One fake error box at a time; resolves with the typed answer when there is a field, else with "".
// `level` 1-3 is how badly it glitches, `dodges` how many times the button jumps away before it can be pressed.
// `timer` (seconds) counts down in the box; when it runs out the box closes and resolves with null.
function errorBox({ title, text, button, code, field = false, level = 1, dodges = 0, timer = 0 }) {
  return new Promise((done) => {
    const inner = `<div class="sys-bar"><span>RockWizard.exe - System Error</span><button class="sys-x" aria-label="Close">&#10005;</button></div>
      <div class="sys-body"><div class="sys-icon">&#10005;</div><div class="grow">
        <h2 class="glitch" data-text="${esc(title)}">${esc(title)}</h2><p>${esc(text)}</p>
        ${field ? `<input id="pop-in" type="text" maxlength="40" placeholder="Your answer" autocomplete="off">` : ""}
        ${timer ? `<div class="sys-timer"><div class="sys-timer-bar"></div></div><div class="sys-timer-text" aria-live="off">${timer} seconds left</div>` : ""}</div></div>
      <div class="sys-foot"><code>${esc(code)}</code><button class="btn primary" id="pop-ok">${esc(button)}</button></div>`;
    const scrim = document.createElement("div"); scrim.className = "scrim pop";
    const m = document.createElement("div"); m.className = `modal pop syserr lv${level}`;
    m.innerHTML = inner;
    // copies stacked behind it, the way error boxes pile up when a program falls over
    const ghosts = Array.from({ length: level + 1 }, (_, i) => {
      const g = document.createElement("div"); g.className = "modal pop syserr ghost";
      g.innerHTML = inner.replace(/ id="[^"]*"/g, "");
      g.style.setProperty("--gx", `${(i + 1) * -18}px`); g.style.setProperty("--gy", `${(i + 1) * -16}px`);
      g.style.animationDelay = `${(level - i) * 70}ms`;
      return g;
    });
    document.body.append(scrim, ...ghosts.reverse(), m);
    document.body.classList.add("glitching");
    setTimeout(() => document.body.classList.remove("glitching"), 500);
    errorSound();

    const inp = $("#pop-in", m), ok = $("#pop-ok", m);
    let tick = 0;
    const close = (timedOut = false) => {
      clearInterval(tick);
      scrim.remove(); ghosts.forEach((g) => g.remove()); m.remove();
      done(timedOut ? null : inp ? inp.value : "");
    };
    if (timer) {  // wall-clock, so switching tabs to look the answer up doesn't stop it
      const ends = Date.now() + timer * 1000, bar = $(".sys-timer-bar", m), label = $(".sys-timer-text", m);
      tick = setInterval(() => {
        const left = Math.max(0, ends - Date.now());
        bar.style.width = `${left / (timer * 10)}%`;
        const secs = Math.ceil(left / 1000);
        label.textContent = `${secs} second${secs === 1 ? "" : "s"} left`;
        m.classList.toggle("hurry", left <= 3000);
        if (!left) close(true);
      }, 100);
    }
    $(".sys-x", m).onclick = () => {  // the X does nothing but complain
      errorSound();
      m.classList.remove("shake"); void m.offsetWidth; m.classList.add("shake");
    };
    let left = dodges, home = null, dodgedAt = 0;
    const dodge = (e) => {  // jump somewhere else on the screen, well away from the pointer
      if (left <= 0) return false;
      left--; dodgedAt = Date.now();
      home = home || ok.getBoundingClientRect();
      const pad = 12, w = innerWidth - home.width - 2 * pad, h = innerHeight - home.height - 2 * pad;
      let x, y, n = 0;
      do { x = pad + Math.random() * w; y = pad + Math.random() * h; n++; }
      while (n < 30 && Math.hypot(x + home.width / 2 - e.clientX, y + home.height / 2 - e.clientY) < 170);
      ok.style.transform = `translate(${Math.round(x - home.left)}px, ${Math.round(y - home.top)}px)`;
      ok.textContent = left ? pick(TAUNTS) : button;
      return true;
    };
    ok.addEventListener("pointerenter", (e) => { if (e.pointerType === "mouse") dodge(e); });
    ok.addEventListener("pointerdown", (e) => { if (dodge(e)) e.preventDefault(); });
    ok.onclick = (e) => {
      if (Date.now() - dodgedAt < 400) return;  // the tap that made it jump
      if (!dodge(e)) close();
    };
    if (inp) { inp.focus(); inp.onpaste = (e) => e.preventDefault(); inp.onkeydown = (e) => { if (e.key === "Enter") close(); }; }
    else if (!dodges) ok.focus();
  });
}

const QUIZ_SECONDS = 10;  // long enough to work it out, too short to look it up
const SWIFT_QUIZ = [
  ["What is 17 × 6?", ["102"]],
  ["What is 13 × 12?", ["156"]],
  ["What is 256 ÷ 16?", ["16", "sixteen"]],
  ["What is 15% of 240?", ["36"]],
  ["What is 1000 − 387?", ["613"]],
  ["What is the square root of 169?", ["13", "thirteen"]],
  ["What is 2 to the power of 7?", ["128"]],
  ["What is three quarters of 120?", ["90", "ninety"]],
  ["What is 19 + 28 + 37?", ["84"]],
  ["What is 12 squared minus 44?", ["100", "one hundred", "a hundred"]],
  ["What is the chemical symbol for gold?", ["au"]],
  ["What is the capital of Australia?", ["canberra"]],
  ["How many bones are in the adult human body?", ["206"]],
  ["In what year did the Titanic sink?", ["1912"]],
  ["How many semitones are in an octave?", ["12", "twelve"]],
  ["Which element has the chemical symbol Fe?", ["iron"]],
  ["What is the Roman numeral for 50?", ["l"]],
  ["At how many degrees Fahrenheit does water freeze?", ["32", "32f", "32°", "32°f", "thirty two", "thirty-two"]],
  ["How many sixteenth notes fit in one bar of 4/4?", ["16", "sixteen"]],
  ["Which planet has the most moons?", ["saturn"]],
];
const SWIFT_TIMEOUTS = [
  "Time's up. That took Forever & Always.",
  "Too slow. The clock just struck Midnights.",
  "Out of time. You're On Your Own, Kid.",
  "Too slow. Timeless, you are not.",
  "Time's up. Even All Too Well (10 Minute Version) was quicker.",
];
const SWIFT_FAILS = [
  "Looks like you'll just have to Shake It Off.",
  "This chart is staying a Blank Space.",
  "You and this chart are Never Ever Getting Back Together.",
  "There's some Bad Blood between you and that question.",
  "You Need to Calm Down and pick a different artist.",
  "This Is Why We Can't Have Nice Things.",
  "Look What You Made Me Do. Chart cancelled.",
  "Is It Over Now? Yes. Yes it is.",
  "That answer was a Cruel Summer for maths.",
  "Long Story Short: no.",
  "Karma says no.",
  "I Knew You Were Trouble the moment you typed that.",
  "Not even in your Wildest Dreams.",
  "Guess who's the Anti-Hero of this quiz.",
  "Better luck in your next Era.",
];
const pick = (list) => list[Math.floor(Math.random() * list.length)];
const isSwift = (artist) => /taylor\s*swift/i.test(artist || "");  // same rule as GUARDED_ARTISTS in the server

// True when the song may be charted after all.
async function swiftGate(artist, title) {
  const tell = (event) => post("/api/shame", { event }).catch(() => {});  // the hall of shame keeps count
  tell("gate");
  await loadErrorSound();
  await errorBox({ title: "Warning", code: "ERR_SWIFT_OVERFLOW (0x1989)", level: 1, dodges: 2, button: "Proceed with caution",
    text: "RockWizard has reached max capacity for songs by this artist (Taylor Swift). Proceed with absolute caution." });
  await errorBox({ title: "Consent required", code: "ERR_CONSENT_NOT_FOUND (0x000D)", level: 2, dodges: 3, button: "I have consent",
    text: `Before continuing please get the express written consent of your admin (Jacob Mastroianni) to proceed with the charting of this song [${title || "this song"} - ${artist || "Taylor Swift"}].` });
  await errorBox({ title: "Ok seriously Claire", code: "ERR_TOO_MUCH_TSWIFT (0x0016)", level: 3, dodges: 4, button: "But I do",
    text: "You really don't need any more T Swift." });
  const [question, answers] = pick(SWIFT_QUIZ);
  const typed = await errorBox({ title: "Final check", code: "HUMAN_VERIFICATION_REQUIRED", level: 1, field: true, button: "Submit",
    timer: QUIZ_SECONDS, text: `Answer correctly within ${QUIZ_SECONDS} seconds to chart this song. ${question}` });
  if (typed === null) {
    tell("timeout");
    await errorBox({ title: "Charting failed", code: "FATAL: VERIFICATION_TIMEOUT", level: 3, button: "Fine", text: pick(SWIFT_TIMEOUTS) });
    return false;
  }
  if (answers.includes(typed.trim().toLowerCase().replace(/[.!]+$/, ""))) { tell("pass"); return true; }
  tell("fail");
  await errorBox({ title: "Charting failed", code: "FATAL: ANSWER_INVALID", level: 3, button: "Fine", text: `Wrong answer. ${pick(SWIFT_FAILS)}` });
  return false;
}

// Adds a song to the queue; null when the checkpoint above turned it away.
async function startChart(body) {
  armErrorSound();
  let job = await post("/api/jobs/convert", body);
  if (job.gate) {
    if (!(await swiftGate(job.artist, job.title))) return null;
    job = await post("/api/jobs/convert", { ...body, cleared: true });
  }
  return job;
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
  party: P('<circle cx="9" cy="8" r="3.2"/><path d="M3 20c0-3.3 2.7-5.5 6-5.5s6 2.2 6 5.5"/><circle cx="17" cy="9" r="2.5"/><path d="M16 14.6c2.8.2 5 2 5 4.9"/>'),
  rocket: P('<path d="M5 15c-1.5 1.5-2 5-2 5s3.5-.5 5-2"/><path d="M14 4c3-1 6-1 6-1s0 3-1 6l-6 6-5-5z"/><path d="M9 10 5 9l-2 2 4 1M14 15l1 4 2-2-1-4"/>'),
};
const VIEWS = [["create", "Create"], ["queue", "Queue"], ["staging", "Staging"], ["live", "Quickplay"], ["party", "Party"], ["library", "Library"], ["settings", "Settings"]];

// ---------- state + shell ----------
const S = { settings: {}, jobs: [], status: {}, staging: null, me: { owner: true, name: "" },
  people: {}, peopleList: [], hearts: { mine: new Set(), counts: {} }, actId: -1, socialRev: -1, unread: 0 };
const owner = () => S.me.owner;
const byChip = (s) => s.added_by ? `<span class="chip by link"${tint(s.added_by)} data-user="${esc(s.added_by)}">from ${esc(s.added_by)}</span>` : "";
const view = () => (location.hash.slice(1).split("?")[0] || "create");
const hashArg = (k) => new URLSearchParams(location.hash.split("?")[1] || "").get(k) || "";
const navView = () => (["profile", "band"].includes(view()) ? "party" : view());  // a profile or band lives under Party

function navHTML(cls) {
  const active = S.jobs.filter((j) => j.status === "queued" || j.status === "running").length;
  return VIEWS.filter(([id]) => owner() || id !== "library").map(([id, label]) =>
    `<button class="${cls}${navView() === id ? " on" : ""}" data-go="${id}">${ICON[id]}<span>${label}</span>` +
    (id === "queue" && active ? `<span class="count">${active}</span>` : "") +
    (id === "party" && S.unread ? `<span class="count">${S.unread}</span>` : "") + `</button>`).join("");
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
// a tap on a name or an avatar opens that person's profile (before the card it sits on opens anything)
document.addEventListener("click", (e) => {
  const u = e.target.closest("[data-user]");
  if (!u || !u.dataset.user) return;
  e.stopPropagation(); e.preventDefault();
  closeDrawer();
  location.hash = "profile?" + qs({ name: u.dataset.user });
}, true);
window.addEventListener("hashchange", () => { closeDrawer(); stopPreview(); renderNav(); render(); });

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
    const sig = (list) => JSON.stringify(list.map((j) => [j.id, j.status, j.step, Math.round(j.progress * 50), (j.votes || []).length]));
    const before = sig(S.jobs);
    const was = Object.fromEntries(S.jobs.map((j) => [j.id, j.status]));
    // new songs are announced from the activity feed (socialTick), to whoever added them
    const finished = jobs.filter((j) => j.kind !== "convert" && j.status === "done" && ["running", "queued"].includes(was[j.id]));
    if (finished.length) { confetti(); toast(`${finished[0].title} is ready to check`); }
    S.jobs = jobs; S.status = status;
    renderNav(); statusPills();
    if (view() === "queue" && before !== sig(jobs)) renderQueue();
    socialTick(status);
  } catch (e) { /* server restarting */ }
}

let lastPage = "";
function page(title, sub, body, actions = "") {
  // the entrance animation plays when the screen changes, not each time the same one is redrawn
  $("#main").classList.toggle("enter", lastPage !== title);
  lastPage = title;
  $("#main").innerHTML = `<div class="top"><div><h1>${title}</h1>${sub ? `<p class="sub">${sub}</p>` : ""}</div><div class="row">${actions}</div></div>${body}`;
}
const render = () => ((!owner() && view() === "library" ? renderCreate : { create: renderCreate, queue: renderQueue, staging: renderStaging, live: renderLive, party: renderParty, library: renderLibrary, settings: renderSettings, profile: renderProfile, band: renderBand }[view()]) || renderCreate)();

// ---------- create ----------
function namesFromFile(stem) {  // quick guess; the PC looks names up properly while converting
  let s = stem.replace(/_/g, " ").replace(/[\(\[].*?[\)\]]/g, "").trim();
  const parts = s.split(" - ").map((x) => x.trim()).filter(Boolean);
  if (parts.length > 2 && (!parts[parts.length - 1].includes(" ") || parts[parts.length - 1] === parts[parts.length - 1].toUpperCase())) parts.pop();
  return parts.length > 1 ? [parts[0], parts.slice(1).join(" - ")] : ["", parts[0] || stem];
}

function renderCreate() {
  page("Create", "Search for a song or paste a YouTube link. Your PC does the work.", `
    <div class="card">
      <h2>Find a song</h2>
      <div class="searchbox">
        <input id="q" type="search" placeholder="Artist - Song title, or paste a YouTube link" autocomplete="off">
        <button class="btn primary" id="go">Search</button>
      </div>
      <div id="results"></div>
    </div>
    <div class="card">
      <div class="row spread"><h2 style="margin:0">Suggested songs</h2><button class="btn small" id="sgmore">Show me more</button></div>
      <p class="faint" style="margin:4px 0 12px">Picked by hand for how they should play. Tap a cover to hear the song. Anything already in your library is left out.</p>
      <div id="suggest"><div class="empty"><span class="spinner"></span></div></div>
    </div>
    <details class="card fold" id="moreways">
      <summary><h2>More ways to add a song</h2> <span class="faint">${desk() ? "your own file, or a whole folder" : "use your own file"}</span></summary>
      <div class="drop mini" id="drop">
        <div class="icon">${ICON.upload}</div>
        <div class="grow"><b>Use your own file</b><div class="muted">Drop an MP3 or WAV here, or click to choose one.</div></div>
        <input type="file" id="file" accept="audio/*,.mp3,.wav,.flac,.ogg,.m4a,.opus" hidden>
      </div>
      <div id="pending" class="hidden stack" style="margin-top:14px"></div>
      <div class="desktop-only" id="batchcard" style="margin-top:16px;padding-top:14px;border-top:1px solid var(--border)">
        <div class="row spread wrap">
          <div><b>Convert a whole folder</b><div class="faint">Every song in a folder, one after another.</div></div>
          <button class="btn" id="pickbatch">${ICON.folder} Choose folder…</button>
        </div>
        <div id="batch"></div>
      </div>
    </details>`);
  const drop = $("#drop"), file = $("#file"), more = $("#moreways"), main = $("#main");
  drop.onclick = () => file.click();
  file.onchange = () => file.files[0] && pending(file.files[0]);
  // a file dragged anywhere onto this screen opens the section and lands in it
  const hasFile = (e) => [...(e.dataTransfer ? e.dataTransfer.types : [])].includes("Files");
  main.ondragover = (e) => { if (!drop.isConnected || !hasFile(e)) return; e.preventDefault(); more.open = true; drop.classList.add("over"); };
  main.ondragleave = (e) => { if (!main.contains(e.relatedTarget)) drop.classList.remove("over"); };
  main.ondrop = (e) => {
    if (!drop.isConnected || !hasFile(e)) return;
    e.preventDefault(); drop.classList.remove("over");
    const f = e.dataTransfer.files[0];
    if (f) { more.open = true; pending(f); more.scrollIntoView({ behavior: "smooth", block: "center" }); }
  };
  $("#go").onclick = doSearch;
  // a block body: an on… handler that returns false throws the keystroke away
  $("#q").onkeydown = (e) => { if (e.key === "Enter") doSearch(); };
  if (!desk()) $("#batchcard").classList.add("hidden");  // needs the desktop window's folder picker
  $("#pickbatch").onclick = pickBatch;
  homeTip();
  loadSuggestions();
}

let suggestGroup = "All", suggestRun = 0;
const sgKey = (s) => `${s.artist}|${s.title}`;
function sgSeen() { try { return JSON.parse(localStorage.getItem("sg_seen")) || {}; } catch (e) { return {}; } }
async function loadSuggestions() {
  const box = $("#suggest");
  let songs;
  try { songs = (await api("/api/suggestions")).songs; } catch (e) { box.innerHTML = `<p class="chip red">${esc(e.message)}</p>`; return; }
  if (!box.isConnected) return;  // left the page while it loaded
  const groups = ["All", ...new Set(songs.map((s) => s.group))];
  if (!groups.includes(suggestGroup)) suggestGroup = "All";
  box.innerHTML = `<div class="tabs" style="margin-bottom:10px">${groups.map((g) => `<button data-sg="${esc(g)}">${esc(g)}</button>`).join("")}</div><div id="sglist"></div>`;
  const draw = () => {
    stopPreview();
    $$("[data-sg]", box).forEach((b) => b.classList.toggle("on", b.dataset.sg === suggestGroup));
    const pool = songs.filter((s) => suggestGroup === "All" || s.group === suggestGroup);
    // the songs shown longest ago come first, so every pick gets its turn before any comes round again
    const seen = sgSeen(), turn = Math.max(0, ...Object.values(seen)) + 1;
    const order = pool.map((s) => [seen[sgKey(s)] || 0, Math.random(), s]).sort((a, b) => a[0] - b[0] || a[1] - b[1]);
    const shown = [], artists = new Set();
    for (const [, , s] of order) {  // one song per artist each time
      if (shown.length < 8 && !artists.has(s.artist)) { artists.add(s.artist); shown.push(s); seen[sgKey(s)] = turn; }
    }
    try { localStorage.setItem("sg_seen", JSON.stringify(seen)); } catch (e) { /* not kept */ }
    $("#sglist", box).innerHTML = shown.map((s, i) => `<div class="result">${artHTML(s)}<div class="grow"><div class="ellipsis"><b>${esc(s.artist)} – ${esc(s.title)}</b></div>
      <div class="faint ellipsis">${esc(s.why)}</div></div><button class="btn small" data-find="${i}">Find it</button></div>`).join("") || `<p class="faint">You already have every song in this group.</p>`;
    $$("[data-find]", box).forEach((b) => b.onclick = () => {
      const s = shown[b.dataset.find];
      $("#q").value = `${s.artist} - ${s.title}`;
      $("#q").scrollIntoView({ behavior: "smooth", block: "center" });
      doSearch();
    });
    // covers and clips not fetched yet, one at a time (the PC may only ask for a few a minute)
    const run = ++suggestRun, arts = $$(".art", box);
    (async () => {
      for (const [i, s] of shown.entries()) {
        if (s.sampled) continue;
        if (run !== suggestRun || !box.isConnected) return;
        let m = null;
        try { m = await api("/api/sample?" + qs({ artist: s.artist, title: s.title })); } catch (e) { continue; }
        Object.assign(s, m, { sampled: true });
        if (run === suggestRun) setSample(arts[i], m);
      }
    })();
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
    armErrorSound();
    if (isSwift($("#pa").value) && !(await api("/api/settings")).error_off && !(await swiftGate($("#pa").value, $("#pt").value))) return;
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
  stopPreview();
  if (/^https?:\/\//.test(q)) {
    try { if (await startChart({ url: q })) { toast("Added to the queue"); location.hash = "queue"; } }
    catch (e) { toast(`Couldn't start it: ${e.message}`); }
    return;
  }
  out.innerHTML = `<div class="empty"><span class="spinner"></span><div>Searching charts, song details and YouTube…</div></div>`;
  try {
    const r = await api("/api/search?" + qs({ q }));
    const d = r.details, song = d || r.song || {};
    out.innerHTML = `
      ${song.title ? `<div class="result songcard">${artHTML(song)}<div class="grow"><div class="ellipsis"><b>${esc(song.title)}</b></div>
        <div class="muted ellipsis">${esc([song.artist, d && d.album, d && d.year, d && d.genre].filter(Boolean).join(" · "))}</div>
        <div class="faint" id="clipnote">Finding a clip…</div></div></div>` : ""}
      ${r.errors.length ? `<p class="chip yellow">${esc(r.errors.join("; "))}</p>` : ""}
      <h2 style="margin-top:16px">Already charted by the community</h2>
      ${r.charts.length ? r.charts.map((c, i) => `
        <div class="result"><div class="grow"><div class="ellipsis"><b>${esc(c.artist)} – ${esc(c.name)}</b></div>
          <div class="faint">charted by ${esc(c.charter)}${c.parts ? " · " + esc(c.parts) : ""}</div></div>
          <button class="btn small" data-chart="${i}">Download</button></div>`).join("")
        : `<p class="faint">No human-made chart found, so making one is the way to go.</p>`}
      <h2 style="margin-top:16px">Make a chart from YouTube</h2>
      ${r.videos.length ? `<p class="faint" style="margin:-6px 0 6px">Tap a picture to hear 30 seconds of that upload before you chart it.</p>` : ""}
      ${r.videos.map((v, i) => `
        <div class="result">
          <button class="vthumb" data-audio="/api/video/sample?${esc(qs({ url: v.url }))}" data-from="30" aria-label="Play a sample"><img class="thumb" src="https://i.ytimg.com/vi/${esc(ytId(v.url))}/mqdefault.jpg" alt="" loading="lazy">${PP_HTML}</button>
          <div class="grow"><div class="ellipsis"><b>${esc(v.title)}</b></div>
            <div class="faint">${esc(v.channel)} · ${mmss(v.seconds)} ${v.best ? `<span class="chip green">Best match</span>` : ""}</div></div>
          <button class="btn primary small" data-video="${i}">Chart this</button></div>`).join("") || `<p class="faint">No videos found.</p>`}`;
    if (song.title) fillSample($(".songcard .art", out), song.artist || "", song.title).then((ok) => {
      const note = $("#clipnote", out);
      if (note) note.textContent = ok ? "Tap the cover for a 30-second clip, to check it's the right song." : "No clip found for this one.";
    });
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
        if (await startChart({ url: v.url, artist: v.artist, title: v.song })) { toast(`Charting ${v.artist} – ${v.song}`); location.hash = "queue"; }
        else { b.disabled = false; b.textContent = "Chart this"; }
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
      if (await startChart({ path: f.path, artist: $(".ba", row).value, title: $(".bt", row).value })) n++;
    }
    toast(`${n} songs added to the queue`); if (n) location.hash = "queue";
  };
}

// ---------- queue ----------
const STATUS_CHIP = { queued: ["", "Waiting"], running: ["accent", "Working"], done: ["green", "Done"], failed: ["red", "Failed"], cancelled: ["", "Cancelled"] };
const VERDICT = { green: ["green", "Looks good"], yellow: ["yellow", "Check flagged spots"], red: ["red", "Has problems"] };

const barAt = {};  // where each job's progress bar stood at the last draw
function renderQueue() {
  const jobs = S.jobs;
  page("Queue", "Songs being made, one at a time on your PC's graphics card. Vote for the waiting songs you want first.",
    jobs.length ? `<div class="stack">${jobs.map((j) => {
      const [cc, ct] = STATUS_CHIP[j.status] || ["", j.status];
      const step = j.status === "running" ? (j.steps[j.step] || "Starting") : j.status === "done" ? "Saved to staging" : j.message || "";
      const v = j.result && j.result.verdict && VERDICT[j.result.verdict];
      return `<div class="card">
        <div class="row spread"><div class="grow"><b class="ellipsis" style="display:block">${esc(j.title)}</b>
          <div class="faint">${{ update: "Updating chart · ", rechart: "Recharting · " }[j.kind] || ""}${esc(step)}${j.by ? ` · from ${esc(j.by)}` : ""}</div></div>
          <span class="chip ${cc}">${ct}</span></div>
        ${j.status === "running" || j.status === "queued" ? `<div class="bar" style="margin-top:12px"><i data-bar="${j.id}" data-to="${Math.round(j.progress * 100)}" style="width:${barAt[j.id] ?? 0}%"></i></div>` : ""}
        <div class="row" style="margin-top:12px">
          ${v ? `<span class="chip ${v[0]}">${v[1]}</span>` : ""}
          ${j.status === "queued" ? `<button class="vote${(j.votes || []).includes(S.me.who) ? " on" : ""}" data-vote="${j.id}" title="${esc((j.votes || []).join(", ") || "Vote to chart this one sooner")}">&#9650; <b>${(j.votes || []).length}</b></button>` : ""}<span class="grow"></span>
          ${(j.status === "running" || j.status === "queued") && (owner() || j.by === S.me.name) ? `<button class="btn small" data-cancel="${j.id}">Cancel</button>` : ""}
          ${j.status === "done" ? `<button class="btn primary small" data-review="${esc(j.result.folder)}">Review</button>` : ""}
        </div></div>`;
    }).join("")}</div>` : `<div class="card empty">Nothing in the queue. Make a chart from the Create tab.</div>`,
    owner() && jobs.some((j) => ["done", "failed", "cancelled"].includes(j.status)) ? `<button class="btn" id="clear">Clear finished</button>` : "");
  $$("[data-bar]").forEach((i) => {
    barAt[i.dataset.bar] = Number(i.dataset.to);
    requestAnimationFrame(() => requestAnimationFrame(() => { i.style.width = i.dataset.to + "%"; }));
  });
  $$("[data-cancel]").forEach((b) => b.onclick = async () => { await post(`/api/jobs/${b.dataset.cancel}/cancel`); refresh(); });
  $$("[data-vote]").forEach((b) => b.onclick = async () => { try { await post(`/api/jobs/${b.dataset.vote}/vote`); } catch (e) { toast(e.message); } refresh(); });
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
const DL_CHIP = `<span class="chip">Downloaded</span>`;
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
    const missing = (S.staging ? S.staging.songs : []).filter((x) => (x.ours || x.downloaded) && !x.has_cover).map((x) => x.folder);
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
  // downloaded charts go through staging like RockWizard's own; "My charts" is for hand charts
  const made = r.songs.filter((s) => s.ours || s.downloaded), hand = r.songs.filter((s) => !s.ours && !s.downloaded);
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
      const staged = s.ours || s.downloaded;
      return `<div class="song" data-open="${esc(s.folder)}" ${staged ? "" : `data-hand="1"`}>${coverHTML(s)}
        <div class="meta"><div class="t ellipsis">${esc(s.title || s.name)}</div>
          <div class="faint ellipsis">${esc(s.artist || "Unknown artist")}</div>
          <div class="row wrap" style="margin-top:8px;gap:6px">
            ${s.downloaded ? DL_CHIP : ""}${!staged ? (s.mine ? MY_CHIP : `<span class="chip">Not marked yet</span>`) : v ? `<span class="chip ${v[0]}">${v[0] === "green" ? "✓" : v[0] === "yellow" ? "!" : "✕"} ${v[0] === "green" ? "OK" : v[0] === "yellow" ? "Check" : "Problems"}</span>` : `<span class="chip">Not checked</span>`}
            ${s.status === "reviewed" && staged ? `<span class="chip accent">Reviewed</span>` : ""}${byChip(s)}
            ${owner() && staged ? `<button class="btn small danger" data-del="${esc(s.folder)}" data-title="${esc(s.title || s.name)}" style="margin-left:auto">Delete</button>` : ""}
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
    <div class="row wrap together">
      <button class="btn" id="watch">${ICON.play} Watch the chart</button>
      <button class="btn${S.hearts.mine.has(s.key) ? " hearted" : ""}" id="heartbtn">${HEART}<span>${S.hearts.mine.has(s.key) ? "Favourite" : "Add to favourites"}</span></button>
      ${live ? `<button class="btn" id="setadd">+ Tonight's setlist</button>` : ""}
      <button class="btn" id="bandadd">+ Band set list</button></div>
    ${owner() ? `<div class="card next"><h2>${live ? "Actions" : "Next"}</h2>
      ${live ? "" : `<div class="row">
        <button class="btn primary grow" id="pub">${ICON.rocket} Publish to live</button>
        ${s.status !== "reviewed" ? `<button class="btn" id="rev">Mark reviewed</button>` : ""}
      </div>`}
      ${local ? playHTML() : ""}
      ${s.ours || s.downloaded ? "" : `<label class="check"><input type="checkbox" id="mine" ${s.mine ? "checked" : ""}> I charted this myself</label>`}
      ${moreHTML(s, live, local)}
      <p class="faint" style="margin-bottom:0">${live ? (s.ours ? "Changes here reach the Deck with the next Syncthing sync." : s.rechart ? "Only the parts you pick in Rechart ever change." : "Human-made charts are view-only here, so they're never changed by accident.")
        : "Publishing moves the song into your live folder; Syncthing takes it to the Deck."}</p>
    </div>` : ""}
    <div class="card"><div class="row spread"><h2 style="margin:0">Song details</h2>${editable ? `<button class="btn small" id="editdet">Edit</button>` : ""}</div>
      <div id="det" style="margin-top:10px">
        <div class="kv">${[["Title", s.title], ["Artist", s.artist], ["Album", s.album], ["Year", s.year], ["Genre", s.genre], ["Charter", s.charter]]
          .map(([k, val]) => `<span class="faint">${k}</span><span>${esc(val || "–")}</span>`).join("")}</div></div>
      ${s.version ? versionHTML(s) : ""}
      ${editable ? `<div class="det-cover"><div class="row wrap"><button class="btn small" id="findcover">${s.has_cover ? "Change cover" : "Find cover art"}</button>
        <button class="btn small" id="upcover">Upload image</button><input type="file" id="coverfile" accept="image/*" hidden></div>
        <div id="covers"></div></div>` : ""}</div>
    ${live ? "" : `<details class="card fold" ${allClear ? "" : "open"}><summary><h2>Checks</h2>${allClear ? ` <span class="faint">All good</span>` : ""}</summary>
      ${findings.map((f) => `<div class="finding"><span class="lv ${f.level}"></span><div>${esc(f.text)}</div></div>`).join("")}</details>`}
    <details class="card fold"><summary><h2>Listen</h2></summary><div class="stems">
      ${stems.map((k) =>
        `<span>${k}</span><audio controls preload="none" src="/api/audio?${qs({ folder: s.folder, stem: k })}"></audio>`).join("")}</div></details>
    ${(!live || s.ours) && !s.downloaded ? rateHTML(s) : ""}
    ${owner() && s.rechart ? rechartHTML(s.rechart) : ""}`;
  $("#x", dr).onclick = closeDrawer;
  $("#watch", dr).onclick = () => openHighway(s);
  $("#heartbtn", dr).onclick = async (e) => {
    try {
      const r = await toggleHeart(s.folder), b = e.currentTarget;
      b.classList.toggle("hearted", r.on); $("span", b).textContent = r.on ? "Favourite" : "Add to favourites";
      if (view() === "live") renderLive();
    } catch (err) { toast(err.message); }
  };
  const setadd = $("#setadd", dr);
  if (setadd) setadd.onclick = async () => {
    try { await post("/api/setlist/add", { folder: s.folder }); toast("Added to tonight's setlist"); setadd.disabled = true; setadd.textContent = "On tonight's setlist"; }
    catch (err) { toast(err.message); }
  };
  $("#bandadd", dr).onclick = () => pickSetlist(s);
  const refreshAfter = (folder2) => { openReview(folder2, live); if (view() === "staging") renderStaging(); if (view() === "live") renderLive(); };
  const lrm = $("#liveremove", dr);
  if (lrm) lrm.onclick = async () => {
    const title = s.title || s.name;
    if (!confirm(`Remove “${title}” from Quickplay?

It goes to the Recycle Bin, so you can still get it back. The Deck drops it on the next sync.`)) return;
    try { await post("/api/live/remove", { folder: s.folder }); toast(`Removed ${title} from Quickplay`); closeDrawer(); render(); } catch (e) { toast(e.message); }
  };
  const unpub = $("#unpub", dr);
  if (unpub) unpub.onclick = async () => {
    if (!confirm("Move this song back to staging? It leaves your live library (and the Deck after the next sync).")) return;
    try { await post("/api/live/unpublish", { folder: s.folder }); toast("Moved back to staging"); closeDrawer(); renderLive(); } catch (e) { toast(e.message); }
  };
  if ((!live || s.ours) && !s.downloaded) wireRating(dr, s);
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
    live ? (s.ours || s.mine || s.downloaded ? `<button class="btn small danger" id="liveremove">Remove from Quickplay</button>` : "")
      : `<button class="btn small danger" id="remove">Delete</button>`,
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
  try { [r] = await Promise.all([api("/api/live"), loadHearts()]); } catch (e) { $("#main").innerHTML += `<p class="chip red">${esc(e.message)}</p>`; return; }
  S.live = r;
  $("#main").querySelector(".empty").outerHTML = `
    <div class="row wrap" style="margin-bottom:14px;gap:10px">
      <input id="lq" type="search" class="grow" placeholder="Search ${r.songs.length} songs by title, artist or album" value="${esc(liveQuery)}" style="min-width:200px">
      <div class="tabs">${[["all", "All"], ["fav", "Favourites"], ["ours", "RockWizard"], ["rate", "To rate"], ["mine", "My charts"], ["human", "Human charts"], ["nocover", "No cover"]].map(([k, l]) =>
        `<button class="${liveFilter === k ? "on" : ""}" data-lf="${k}">${l}</button>`).join("")}</div>
      ${sortSelect("live")}
    </div>
    <div id="livegrid"></div>`;
  const draw = () => {
    const q = liveQuery.toLowerCase().trim();
    const songs = sortSongs(r.songs, sortKey("live")).filter((s) => (liveFilter === "all" || (liveFilter === "fav" ? S.hearts.mine.has(s.key) : liveFilter === "ours" ? s.ours : liveFilter === "rate" ? s.ours && !Object.keys(s.ratings || {}).length
        : liveFilter === "mine" ? s.mine : liveFilter === "human" ? !s.ours : !s.has_cover))
      && (!q || `${s.title} ${s.artist} ${s.album} ${s.name}`.toLowerCase().includes(q)));
    $("#livegrid").innerHTML = songs.length ? `<div class="faint" style="margin-bottom:10px">${songs.length} song${songs.length === 1 ? "" : "s"}</div>
      <div class="songs">${songs.slice(0, liveShown).map((s) => `<div class="song" data-live="${esc(s.folder)}">${heartHTML(s)}${coverHTML(s)}
        <div class="meta"><div class="t ellipsis">${esc(s.title || s.name)}</div>
          <div class="faint ellipsis">${esc(s.artist || "Unknown artist")}</div>
          ${s.ours || s.mine || s.added_by ? `<div class="row wrap" style="margin-top:8px;gap:6px">${s.ours ? `<span class="chip accent">RockWizard</span>` : s.mine ? MY_CHIP : ""}${byChip(s)}</div>` : ""}</div></div>`).join("")}</div>
      ${songs.length > liveShown ? `<div style="text-align:center;margin:18px 0"><button class="btn" id="more">Show more (${songs.length - liveShown} left)</button></div>` : ""}`
      : `<div class="card empty">${liveFilter === "fav" && !q ? "No favourites yet. Tap the heart on a song." : "No songs match."}</div>`;
    $$("[data-live]").forEach((c) => c.onclick = () => openReview(c.dataset.live, true));
    $$("[data-heart]", $("#livegrid")).forEach((b) => b.onclick = async (e) => {
      e.stopPropagation();
      try { await toggleHeart(b.dataset.heart); } catch (err) { toast(err.message); }
      draw();
    });
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

// ---------- what charted a song (song.ini chc_models / chc_engine, chc.version) ----------
const BEHIND = { guitar: "new guitar model", bass: "new bass model", drums: "new drum model", beats: "new beat model", charting: "newer charting" };
const STEM_NAME = { sw: "BS-RoFormer guitar stem", s6: "Demucs 6-stem guitar", ft: "Demucs “everything else” stem" };
const BASS_STEM_NAME = { sw: "BS-RoFormer bass stem", ft: "Demucs bass stem" };
const behindChips = (v) => (v.upgrades && v.upgrades.length ? v.upgrades : v.outdated.map((k) => BEHIND[k] || k))
  .map((t) => `<span class="chip yellow">${esc(t)}</span>`).join(" ");

function versionHTML(s) {
  const v = s.version, m = v.models;
  const rows = [["Guitar", m.guitar && (m.guitar + (v.stems ? ` · ${STEM_NAME[v.stems] || v.stems}` : ""))], ["Bass", m.bass && (m.bass + (v.bass_stems ? ` · ${BASS_STEM_NAME[v.bass_stems] || v.bass_stems}` : ""))], ["Drums", m.drums],
    ["Beats", m.beats], ["Vocals", v.vocals], ["Charting", `version ${v.engine}${v.engine_note ? ` · ${v.engine_note}` : ""}`], ["Charted", v.charted]]
    .filter(([, val]) => val);
  return `<details class="card fold" style="margin-top:12px"><summary><h2>Charted with</h2>
      ${v.outdated.length ? ` <span class="chip yellow">behind</span>` : ` <span class="faint">Up to date</span>`}</summary>
    <div class="kv">${rows.map(([k, val]) => `<span class="faint">${k}</span><span>${esc(val)}</span>`).join("")}</div>
    ${v.outdated.length ? `<p class="faint" style="margin-bottom:0">The app today has: ${behindChips(v)}${s.edited ? `<br>This chart was changed by hand, so updating it would lose those changes.` : `<br>Library → Update charts remakes it (the old chart is backed up).`}</p>` : ""}</details>`;
}

function libUpdate() {
  $("#lib").innerHTML = `<div class="card"><p class="muted" style="margin-top:0">Remake charts with the newest models and charting, from the audio already in each song folder. The old chart is backed up first. Songs behind today's app are picked for you; charts changed by hand are left out unless you tick them, and charts you marked as your own are never remade. Each takes a few minutes on the graphics card.</p>
    ${folderField("uf", S.settings.live_dir || S.settings.songs_dir || "")}<div id="ulist" style="margin-top:12px"></div></div>`;
  const load = async () => {
    const box = $("#ulist"); box.innerHTML = `<span class="spinner"></span>`;
    try {
      const r = await api("/api/library/ours?" + qs({ folder: $("#uf").value }));
      const behind = r.songs.filter((s) => s.version.outdated.length);
      const pick = (s) => s.version.outdated.length && !s.edited && !s.mine;
      r.songs.sort((a, b) => b.version.outdated.length - a.version.outdated.length || a.name.localeCompare(b.name));
      box.innerHTML = r.songs.length ? `
        <div class="row wrap" style="margin-bottom:10px;gap:10px"><b>${behind.length} of ${r.songs.length} behind today's app</b><span class="grow"></span>
          <button class="btn small" id="ubehind">Pick the ones behind</button><button class="btn small" id="unone">Pick none</button></div>
        <div class="list">${r.songs.map((s) => `
        <label class="item check"><input type="checkbox" class="us" value="${esc(s.folder)}" ${pick(s) ? "checked" : ""} ${s.mine ? "disabled" : ""} data-behind="${pick(s) ? 1 : 0}">
          <div class="grow" style="min-width:0"><div class="ellipsis">${esc(s.name)}</div>
            <div class="row wrap" style="gap:6px;margin-top:4px">${s.version.outdated.length ? behindChips(s.version) : `<span class="faint">Up to date</span>`}
              ${s.edited ? `<span class="chip red">changed by hand</span>` : ""}${s.mine ? `<span class="chip">your own chart</span>` : ""}</div></div>
          <span class="faint">${s.version.charted || new Date(s.modified * 1000).toLocaleDateString()}</span></label>`).join("")}</div>
        <button class="btn primary" id="urun" style="margin-top:12px">Update selected</button>` : `<div class="empty">No RockWizard songs in that folder.</div>`;
      const count = () => { const n = $$(".us:checked").length; const b = $("#urun"); if (b) { b.disabled = !n; b.textContent = n ? `Update ${n} song${n > 1 ? "s" : ""}` : "Update selected"; } };
      $$(".us").forEach((c) => c.onchange = count); count();
      const all = (on) => $$(".us:not(:disabled)").forEach((c) => c.checked = on && c.dataset.behind === "1");
      if ($("#ubehind")) { $("#ubehind").onclick = () => { all(true); count(); }; $("#unone").onclick = () => { all(false); count(); }; }
      const run = $("#urun"); if (run) run.onclick = async () => {
        const folders = $$(".us:checked").map((c) => c.value);
        const edited = r.songs.filter((s) => s.edited && folders.includes(s.folder)).length;
        if (edited && !confirm(`${edited} of these were changed by hand. Updating them replaces those changes (the old chart is backed up). Go ahead?`)) return;
        const q = await post("/api/jobs/update", { folders }); toast(`${q.length} chart${q.length === 1 ? "" : "s"} queued`); location.hash = "queue";
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
async function loadScoreboard() {
  const box = $("#board");
  let r;
  try { r = await api("/api/scoreboard"); } catch (e) { box.innerHTML = `<p class="chip red">${esc(e.message)}</p>`; return; }
  if (!box.isConnected) return;
  const cell = (c, v) => v === undefined ? `<td class="faint">–</td>`
    : `<td class="${v === c.best ? "best" : ""}">${c.percent ? (v * 100).toFixed(1) + "%" : v.toFixed(3)}</td>`;
  const played = (x) => Object.keys(x).length ? `${x.good || 0} good · ${x.ok || 0} ok · ${x.bad || 0} bad` : "";
  box.innerHTML = r.insts.map((b) => `<h3 class="board-inst">${esc(b.inst)} <span class="faint">${b.songs} test songs</span></h3>
    <div class="board-wrap"><table class="board"><thead><tr><th>Run</th><th>What it tried</th>${b.columns.map((c) =>
      `<th title="${c.higher ? "Higher" : "Lower"} is better">${esc(c.label)} ${c.higher ? "↑" : "↓"}</th>`).join("")}<th>How it played</th></tr></thead>
    <tbody>${b.rows.map((x) => `<tr class="${x.in_app ? "inapp" : ""}"><td>${x.run || ""}</td>
      <td class="what">${esc(x.name)}${x.in_app ? ` <span class="chip accent">In the app</span>` : ""}</td>
      ${b.columns.map((c) => cell(c, x.values[c.key])).join("")}<td class="faint">${played(x.ratings)}</td></tr>`).join("")}</tbody></table></div>`).join("")
    + `<p class="faint" style="margin-bottom:0">↑ higher is better, ↓ lower is better. Human-or-AI is how often a detector tells our chart from a human's: two human studios score ${Math.round(r.studios * 100)}%, so that is the target.</p>`;
}

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
    <details class="card fold" id="boardcard"><summary><h2>Model scoreboard</h2></summary>
      <p class="faint">Every model trained so far, scored on songs it never saw. The best in each column is highlighted.</p>
      <div id="board"><span class="spinner"></span></div></details>
    <div class="card" id="phone"><h2>Use RockWizard on your phone</h2><div class="empty"><span class="spinner"></span></div></div>
    <div class="card"><h2>Steam Deck sync</h2><div id="syncbox" class="muted"></div></div>
    <div class="card"><h2>YouTube downloader</h2><p class="muted" style="margin-top:0">YouTube changes often and older downloader versions stop working. If searching or downloading fails, update it.</p>
      <button class="btn" id="dl">Update downloader</button> <span id="dlmsg" class="faint"></span></div>
    <div class="card"><h2>Advanced</h2>
      <label class="item check" style="border:0;padding:0"><input type="checkbox" id="erroff" ${st.error_off ? "checked" : ""}>
        <div class="grow">Turn off error function<div class="faint">Skips the extra error checks when a song is added.</div></div></label></div>`);
  $("#erroff").onchange = async (e) => {
    try { S.settings = await post("/api/settings", { error_off: e.target.checked }); toast("Saved"); }
    catch (err) { toast(err.message); e.target.checked = !e.target.checked; }
  };
  api("/api/learned").then((r) => {
    const box = $("#learned"); if (!box) return;
    box.innerHTML = r.items.map((x) => `<div class="row spread rc-row"><span><span style="text-transform:capitalize">${esc(x.inst)}</span>: ${ISSUE_TEXT[x.issue][x.net > 0 ? 0 : 1].toLowerCase()}</span>
      <span class="${x.steps ? "chip green" : "faint"}">${x.steps ? `Adjusting · ${Math.abs(x.net)} charts` : `${Math.abs(x.net)} of ${x.needs} charts`}</span></div>`).join("")
      || `<p class="faint" style="margin:0">Nothing yet. Rate a part OK or Bad on a RockWizard song and tap what's off.</p>`;
  }).catch((e) => { const box = $("#learned"); if (box) box.innerHTML = `<p class="chip red">${esc(e.message)}</p>`; });
  $("#boardcard").addEventListener("toggle", () => { if ($("#boardcard").open && !$("#board table")) loadScoreboard(); });
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

// ---------- friends: who's who, what's new, favourites ----------
const pkey = (n) => (n || "").toLowerCase().split(/\s+/).filter(Boolean).join(" ");  // same as social.key_of in the server
const colorOf = (n) => (S.people[pkey(n)] || {}).color || "";
const tint = (n) => { const c = colorOf(n); return c ? ` style="color:${c};border-color:${c}66;background:${c}1f"` : ""; };
const avatarOf = (n) => (S.people[pkey(n)] || {}).avatar || (pkey(n) === pkey(S.me.who) ? S.me.avatar : null);
const photoOf = (n) => (S.people[pkey(n)] || {}).photo || (pkey(n) === pkey(S.me.who) ? S.me.photo : 0);
// link=false for an avatar that shouldn't open a profile (the avatar builder's own previews)
const avatar = (name, color, cls = "", link = true) => {
  const a = avatarOf(name), ph = photoOf(name), bg = color || colorOf(name) || "var(--accent)";
  const inner = ph ? `<img src="${photoURL(name, ph)}" alt="" loading="lazy">` : a ? avatarSVG(a) : esc(initials(name));
  return `<span class="avatar ${cls}${a || ph ? " art" : ""}${link && name ? " link" : ""}" style="background:${bg}"${link && name ? ` data-user="${esc(name)}"` : ""}>${inner}</span>`;
};
const since = (t) => { const m = Math.round((Date.now() / 1000 - t) / 60); return m < 1 ? "just now" : m < 60 ? `${m} min ago` : m < 1440 ? `${Math.round(m / 60)} h ago` : `${Math.round(m / 1440)} d ago`; };
const HEART = `<svg class="i-heart" viewBox="0 0 24 24"><path d="M12 20.5s-7.5-4.6-7.5-10.2A4.3 4.3 0 0 1 12 7.6a4.3 4.3 0 0 1 7.5 2.7c0 5.6-7.5 10.2-7.5 10.2z"/></svg>`;

async function loadPeople() {
  try {
    S.peopleList = await api("/api/people");
    S.people = Object.fromEntries(S.peopleList.map((p) => [pkey(p.name), p]));
  } catch (e) { /* the page works without colours */ }
}
async function loadHearts() {
  try { const h = await api("/api/hearts"); S.hearts = { mine: new Set(h.mine), counts: h.counts }; } catch (e) { /* no hearts shown */ }
}
async function toggleHeart(folder) {
  const r = await post("/api/hearts", { folder });
  r.on ? S.hearts.mine.add(r.key) : S.hearts.mine.delete(r.key);
  S.hearts.counts[r.key] = r.count;
  return r;
}
const heartHTML = (s) => {
  const n = S.hearts.counts[s.key] || 0;
  return `<button class="heart${S.hearts.mine.has(s.key) ? " on" : ""}" data-heart="${esc(s.folder)}" aria-label="Favourite">${HEART}${n ? `<span>${n}</span>` : ""}</button>`;
};

// desktop alerts work where the browser allows them (this PC; phones on plain Wi-Fi addresses don't)
const canNotify = () => "Notification" in window && window.isSecureContext;
function notify(title, body) {
  try { if (canNotify() && Notification.permission === "granted" && document.hidden) new Notification(title, { body, icon: "/static/icon-192.png" }); }
  catch (e) { /* no alert */ }
}

// one line of the feed has just happened: tell this person if it matters to them
function announce(e) {
  const mine = pkey(e.who) === pkey(S.me.who), forMe = e.for && pkey(e.for) === pkey(S.me.who);
  if (e.kind === "ready") {
    if (mine) { confetti(); toast(`Your song is ready: ${e.title}`); notify("Your song is ready", e.title); }
    else toast(`${e.who}'s song is ready: ${e.title}`);
  } else if (e.kind === "published" && forMe) {
    confetti(); toast(`${e.title} is in Quickplay. It reaches the Deck on the next sync.`); notify("Your song is live", e.title);
  } else if (e.kind === "failed") {
    if (mine) toast(`${e.title} couldn't be charted`);
  } else if (!mine) toast(`${e.who} ${e.text}`);
  if (!mine && view() !== "party") S.unread++;
}

async function socialTick(st) {
  try {
    if (st.social !== S.socialRev) {
      const first = S.socialRev < 0;
      S.socialRev = st.social;
      loadPeople().then(() => {
        if (first) return;
        const typing = document.activeElement && document.activeElement.matches("input, textarea, select");
        if (view() === "party") renderParty(true);
        else if (["profile", "band"].includes(view()) && !typing && !$(".modal")) render();
      });
    }
    if (S.actId < 0) { S.actId = st.activity; return; }  // the first look: nothing is news yet
    if (st.activity > S.actId) {
      const after = S.actId;
      S.actId = st.activity;
      (await api("/api/activity?" + qs({ after }))).events.forEach(announce);
      renderNav();
    }
  } catch (e) { /* next tick */ }
}

// ---------- party: feed, tonight's setlist, people, invite ----------
let partyTab = "feed";
const KIND = { added: ["", "Added"], ready: ["green", "Ready"], failed: ["red", "Failed"], published: ["accent", "Live"],
  downloaded: ["", "Download"], shame: ["yellow", "Shame"], setlist: ["accent", "Setlist"], band: ["accent", "Band"] };

async function renderParty(keep = false) {
  if (!keep || !$("#pbody")) {
    page("Party", "What everyone's adding, tonight's setlist, bands and who's who.",
      `<div class="tabs" style="margin-bottom:14px">${[["feed", "Feed"], ["setlist", "Setlist"], ["bands", "Bands"], ["people", "People"], ["invite", "Invite"]].map(([k, l]) =>
        `<button class="${partyTab === k ? "on" : ""}" data-ptab="${k}">${l}</button>`).join("")}</div>
      <div id="pbody"><div class="empty"><span class="spinner"></span></div></div>`);
    $$("[data-ptab]").forEach((b) => b.onclick = () => { partyTab = b.dataset.ptab; renderParty(); });
  }
  const tab = partyTab;
  let html;
  try { html = await { feed: feedHTML, setlist: setlistHTML, bands: bandsHTML, people: peopleHTML, invite: inviteHTML }[tab](); }
  catch (e) { html = `<p class="chip red">${esc(e.message)}</p>`; }
  const body = $("#pbody");
  if (!body || view() !== "party" || tab !== partyTab) return;
  body.innerHTML = html;
  ({ feed: wireFeed, setlist: wireSetlist, bands: wireBands, people: wirePeople, invite: wireInvite })[tab](body);
}

async function feedHTML() {
  const events = (await api("/api/activity")).events.reverse();
  S.unread = 0; renderNav();
  const ask = canNotify() && Notification.permission === "default";
  return `${ask ? `<div class="card row spread wrap" style="gap:10px"><span>Get an alert on this computer when your song is ready.</span><button class="btn small" id="notifyon">Turn on alerts</button></div>` : ""}
    <div class="card feed">${events.length ? events.map((e) => `<div class="item">${avatar(e.who, e.color)}
      <div class="grow"><b class="link" style="color:${e.color}" data-user="${esc(e.who)}">${esc(e.who)}</b> ${esc(e.text)}<div class="faint">${since(e.t)}</div></div>
      <span class="chip ${(KIND[e.kind] || ["", ""])[0]}">${(KIND[e.kind] || ["", e.kind])[1]}</span></div>`).join("")
      : `<div class="empty">Nothing yet. Add a song and it shows up here for everyone.</div>`}</div>`;
}
function wireFeed(body) {
  const b = $("#notifyon", body);
  if (b) b.onclick = async () => { const p = await Notification.requestPermission(); toast(p === "granted" ? "Alerts are on" : "Alerts stay off"); renderParty(true); };
}

async function setlistHTML() {
  const songs = (await api("/api/setlist")).songs, done = songs.filter((s) => s.played).length;
  const mine = (s) => owner() || pkey(s.by) === pkey(S.me.who);
  return `<div class="row spread wrap" style="margin-bottom:12px;gap:10px">
      <b>${songs.length ? `${done} of ${songs.length} played` : "Tonight's setlist"}</b>
      <div class="row wrap" style="gap:8px"><button class="btn small" data-go="live">Add songs</button>
        ${owner() && done ? `<button class="btn small" data-clear="played">Clear played</button>` : ""}
        ${owner() && songs.length ? `<button class="btn small danger" data-clear="all">Clear all</button>` : ""}</div></div>
    <div class="card">${songs.length ? songs.map((s, i) => `<div class="item setrow${s.played ? " played" : ""}">
        <span class="setno">${i + 1}</span>
        ${s.has_cover ? `<img class="setcover" src="/api/cover?${qs({ folder: s.folder, w: 120 })}" alt="" loading="lazy">` : `<span class="setcover ph">${esc(initials(s.title))}</span>`}
        <div class="grow" style="min-width:0"><div class="t ellipsis">${esc(s.title)}</div><div class="faint ellipsis">${esc(s.artist || "Unknown artist")}</div>
          <div style="margin-top:5px"><span class="chip by link" style="color:${s.color};border-color:${s.color}66;background:${s.color}1f" data-user="${esc(s.by)}">from ${esc(s.by)}</span>${s.here ? "" : ` <span class="chip red">No longer in Quickplay</span>`}</div></div>
        <div class="row wrap setbtns">
          ${owner() ? `<button class="btn small" data-move="-1" data-key="${esc(s.key)}" aria-label="Move up" ${i ? "" : "disabled"}>&#8593;</button><button class="btn small" data-move="1" data-key="${esc(s.key)}" aria-label="Move down" ${i < songs.length - 1 ? "" : "disabled"}>&#8595;</button>` : ""}
          <button class="btn small${s.played ? " primary" : ""}" data-played="${s.played ? "" : "1"}" data-key="${esc(s.key)}">${s.played ? "Played" : "Mark played"}</button>
          ${mine(s) ? `<button class="btn small danger" data-remove="1" data-key="${esc(s.key)}" aria-label="Take off the setlist">&#10005;</button>` : ""}</div></div>`).join("")
      : `<div class="empty">Nothing on tonight's setlist yet.<br>Open a song in Quickplay and tap <b>+ Tonight's setlist</b>.</div>`}</div>
    <p class="faint">Everyone sees this list and can add to it. It lives here in RockWizard, not in the game's own menus.</p>`;
}
function wireSetlist(body) {
  const send = async (b) => { try { await post("/api/setlist/update", b); } catch (e) { toast(e.message); } renderParty(true); };
  $$("[data-played]", body).forEach((b) => b.onclick = () => send({ key: b.dataset.key, played: !!b.dataset.played }));
  $$("[data-move]", body).forEach((b) => b.onclick = () => send({ key: b.dataset.key, move: Number(b.dataset.move) }));
  $$("[data-remove]", body).forEach((b) => b.onclick = () => send({ key: b.dataset.key, remove: true }));
  $$("[data-clear]", body).forEach((b) => b.onclick = () => {
    if (confirm(b.dataset.clear === "all" ? "Clear the whole setlist?" : "Take the played songs off the setlist?")) send({ clear: b.dataset.clear });
  });
}

async function peopleHTML() {
  await loadPeople();
  const list = S.peopleList || [], shamed = list.filter((p) => p.shame.gate).sort((a, b) => b.shame.gate - a.shame.gate);
  const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;
  return `<div class="card"><div class="row spread wrap" style="gap:10px"><h2 style="margin:0">Your colour</h2>
        ${S.me.who ? `<div class="row" style="gap:8px"><button class="btn small" id="myavatar">Edit avatar</button><button class="btn small" data-user="${esc(S.me.who)}">My profile</button></div>` : ""}</div>
      <p class="faint">Shows on your name in the feed and on songs you add. Tap anyone below to see their profile.</p>
      <div class="swatches">${(S.me.colors || []).map((c) => `<button class="swatch${c === S.me.color ? " on" : ""}" data-color="${c}" style="background:${c}" aria-label="Pick this colour"></button>`).join("")}</div></div>
    <div class="people">${list.map((p) => `<div class="card person link" data-user="${esc(p.name)}">${avatar(p.name, p.color, "big" + (p.online ? " on" : ""), false)}
      <div class="grow" style="min-width:0"><div class="ellipsis"><b>${esc(p.name)}</b>${p.me ? ` <span class="faint">(you)</span>` : ""}${p.owner ? ` <span class="chip accent">Host</span>` : ""}</div>
        <div class="faint">${p.online ? "Here now" : p.last ? `Last here ${since(p.last)}` : "Not seen yet"}</div>
        <div style="margin-top:6px">${plural(p.added, "song")} added${p.hearts ? ` · ${plural(p.hearts, "favourite")}` : ""}</div>
        ${p.top_artist ? `<div class="faint ellipsis">Most added: ${esc(p.top_artist)} (${p.top_count})</div>` : ""}</div></div>`).join("")}</div>
    <div class="card"><h2>Hall of shame</h2>
      <p class="faint" style="margin-top:0">How it went at the Taylor Swift checkpoint.</p>
      ${shamed.length ? shamed.map((p, i) => `<div class="item">${avatar(p.name, p.color)}<div class="grow"><b>${esc(p.name)}</b>${i ? "" : ` <span class="chip yellow">Most stopped</span>`}
        <div class="faint">Stopped ${plural(p.shame.gate, "time")} · failed the quiz ${p.shame.fail} · too slow ${p.shame.timeout} · got through ${p.shame.pass}</div></div></div>`).join("")
      : `<div class="empty">Nobody has met the checkpoint yet.</div>`}</div>`;
}
function wirePeople(body) {
  const ed = $("#myavatar", body);
  if (ed) ed.onclick = () => editAvatar(() => renderParty(true));
  $$("[data-color]", body).forEach((b) => b.onclick = async () => {
    try { S.me = await post("/api/me", { color: b.dataset.color }); toast("Colour saved"); } catch (e) { toast(e.message); }
    renderParty(true);
  });
}

// ---------- avatars: built from parts and drawn here (the server keeps only the choices), or a photo ----------
const INK = "#2a1d17";
const shade = (hex, f) => { const n = parseInt(hex.slice(1), 16); const c = (v) => Math.round(v * (1 - f)).toString(16).padStart(2, "0"); return `#${c(n >> 16)}${c((n >> 8) & 255)}${c(n & 255)}`; };
const starPts = (cx, cy, r, n = 5, inner = 0.45) => Array.from({ length: n * 2 }, (_, i) => {
  const a = Math.PI * i / n - Math.PI / 2, rr = i % 2 ? r * inner : r;
  return `${(cx + rr * Math.cos(a)).toFixed(1)},${(cy + rr * Math.sin(a)).toFixed(1)}`;
}).join(" ");
const heartAt = (cx, cy, s, fill) => `<path d="M${cx} ${cy + s}l${-s * 1.1} ${-s * 1.1}a${s * .62} ${s * .62} 0 0 1 ${s * 1.1} ${-s * .9}a${s * .62} ${s * .62} 0 0 1 ${s * 1.1} ${s * .9}z" fill="${fill}"/>`;

function avatarSVG(a) {
  const sk = a.skin, hc = a.hair_color, tc = a.top_color || "#1f2937", fh = a.hair === "none" ? "#3a2a20" : hc;
  const line = (w = 2) => `fill="none" stroke="${INK}" stroke-width="${w}" stroke-linecap="round"`;
  const earX = { wide: 13.8, long: 18.3, oval: 17.2 }[a.face] || 16.3;
  const top = `<path d="M15 30c0-12 7-17 17-17s17 5 17 17c-4-6-10-9-17-9s-13 3-17 9z" fill="${hc}"/>`;
  const W = `fill="#fff" opacity=".16"`;
  const pattern = { rays: Array.from({ length: 12 }, (_, i) => { const a1 = i * Math.PI / 6, a2 = a1 + Math.PI / 12, p = (t) => `${(32 + 60 * Math.cos(t)).toFixed(1)},${(32 + 60 * Math.sin(t)).toFixed(1)}`; return `<polygon points="32,32 ${p(a1)} ${p(a2)}" ${W}/>`; }).join(""),
    dots: Array.from({ length: 64 }, (_, i) => `<circle cx="${(i % 8) * 8 + 4}" cy="${Math.floor(i / 8) * 8 + 4}" r="1.5" ${W}/>`).join(""),
    stripes: `<path d="${Array.from({ length: 12 }, (_, i) => `M${i * 12 - 64} 64L${i * 12} 0`).join("")}" stroke="#fff" stroke-width="4" opacity=".14"/>`,
    stars: [[10, 12, 3.4], [54, 10, 2.6], [8, 44, 2.4], [56, 40, 3.2], [20, 4, 2]].map(([x, y, r]) => `<polygon points="${starPts(x, y, r)}" fill="#fff" opacity=".45"/>`).join("") }[a.pattern] || "";
  const back = { long: `<path d="M13 32c0-14 8-20 19-20s19 6 19 20v20H13z" fill="${hc}"/>`, bun: `<circle cx="32" cy="11" r="7" fill="${hc}"/>`,
    afro: `<circle cx="32" cy="25" r="22" fill="${hc}"/>`, ponytail: `<path d="M45 18c9 1 13 10 10 24c-2 5-4 8-6 9c1-8 0-14-6-19z" fill="${hc}"/>`,
    pigtails: `<circle cx="11" cy="36" r="6.5" fill="${hc}"/><circle cx="53" cy="36" r="6.5" fill="${hc}"/>`,
    dreads: [13, 17.5, 42.5, 47].map((x) => `<rect x="${x}" y="20" width="4.5" height="30" rx="2.2" fill="${hc}"/>`).join("") }[a.hair] || "";
  const body = `<path d="M6 66c2-11 13-16 26-16s24 5 26 16z"`, neckline = `<path d="M26.5 50.6q5.5 5 11 0z" fill="${sk}"/>`;
  const shirt = { tee: `${body} fill="${tc}"/>${neckline}`,
    hoodie: `${body} fill="${tc}"/><path d="M18 53c3-5 8-7 14-7s11 2 14 7c-4-2-9-3-14-3s-10 1-14 3z" fill="${shade(tc, .3)}"/><path d="M29 55v6M35 55v6" stroke="#fff" stroke-width="1.2" opacity=".8"/>`,
    jacket: `${body} fill="${tc}"/>${neckline}<path d="M6 66c2-11 13-16 22-16l2 16z" fill="#18181b"/><path d="M58 66c-2-11-13-16-22-16l-2 16z" fill="#18181b"/>`,
    tank: `${body} fill="${shade(sk, .06)}"/><path d="M21 66c0-7 2-12 4-15h14c2 3 4 8 4 15z" fill="${tc}"/><path d="M27 51q5 4 10 0z" fill="${sk}"/>`,
    bandtee: `${body} fill="${tc}"/>${neckline}<path d="M34 54l-5 6h3.5l-2 5.5 6-7.5h-3.5l2.5-4z" fill="#fde047"/>`,
    collar: `${body} fill="${tc}"/>${neckline}<path d="M25 50l7 5-3 3zM39 50l-7 5 3 3z" fill="#fff"/>` }[a.top || "tee"] || "";
  const face = { round: `<circle cx="32" cy="33" r="16" fill="${sk}"/>`, square: `<rect x="16" y="17" width="32" height="32" rx="9" fill="${sk}"/>`,
    long: `<ellipse cx="32" cy="34" rx="14" ry="18" fill="${sk}"/>`, oval: `<ellipse cx="32" cy="33" rx="15" ry="17" fill="${sk}"/>`,
    wide: `<ellipse cx="32" cy="34" rx="18.5" ry="15" fill="${sk}"/>` }[a.face] || "";
  const ears = `<circle cx="${earX}" cy="34" r="3.2" fill="${sk}"/><circle cx="${64 - earX}" cy="34" r="3.2" fill="${sk}"/>`;
  const onFace = { freckles: [[22, 37], [24, 38.6], [21, 39.6], [42, 37], [40, 38.6], [43, 39.6]].map(([x, y]) => `<circle cx="${x}" cy="${y}" r=".75" fill="#8b5a3b" opacity=".75"/>`).join(""),
    blush: `<ellipse cx="22" cy="38.5" rx="3" ry="1.8" fill="#f472b6" opacity=".45"/><ellipse cx="42" cy="38.5" rx="3" ry="1.8" fill="#f472b6" opacity=".45"/>`,
    facepaint: `<polygon points="${starPts(41.5, 38.5, 3)}" fill="#ec4899"/>`,
    scar: `<path d="M23 27.5l5 9M23.6 30.3l3-1.2M25.1 33l3-1.2" stroke="#b91c1c" stroke-width="1.1" opacity=".8" fill="none"/>` }[a.extra] || "";
  const chin = { stubble: `<path d="M18 37c1.5 9 7.5 14 14 14s12.5-5 14-14c-3 4-8 6-14 6s-11-2-14-6z" fill="${fh}" opacity=".3"/>`,
    goatee: `<path d="M29 45.5h6c0 3-1 6.5-3 6.5s-3-3.5-3-6.5z" fill="${fh}"/>`,
    beard: `<path d="M17 33c0 11 7 19 15 19s15-8 15-19c-2 3-4 5-6 6c-2-2-5-3-9-3s-7 1-9 3c-2-1-4-3-6-6z" fill="${fh}"/>` }[a.facial_hair] || "";
  const smile = `<path d="M27 40q5 5 10 0" ${line()}/>`, grin = `<path d="M26 39h12a6 6 0 0 1-12 0z" fill="#fff" stroke="${INK}" stroke-width="1.6" stroke-linejoin="round"/>`;
  const mouth = { smile, grin, open: `<ellipse cx="32" cy="42" rx="3.2" ry="4" fill="${INK}"/>`, flat: `<path d="M27 42h10" ${line()}/>`,
    tongue: `<ellipse cx="33.5" cy="43.2" rx="2.4" ry="2.8" fill="#ff6b8a"/>${smile}`,
    teeth: `${grin}<path d="M26.5 41.3h11M30 39v4.6M34 39v4.6" stroke="${INK}" stroke-width=".9"/>`,
    smirk: `<path d="M27.5 41.5q5 2 9.5-2.5" ${line()}/>`, o: `<circle cx="32" cy="42" r="2.6" ${line()}/>`,
    fangs: `<path d="M28.4 42l1.1 2.8 1.1-2.2M33.4 42.6l1.1 2.2 1.1-2.8" fill="#fff" stroke="${INK}" stroke-width=".6"/>${smile}`,
    sad: `<path d="M27 43.5q5-5 10 0" ${line()}/>` }[a.mouth] || "";
  const tache = a.facial_hair === "moustache" ? `<path d="M25 39.5c3-3.5 5-2.5 7-1.2c2-1.3 4-2.3 7 1.2c-3 1-5 .8-7-.3c-2 1.1-4 1.3-7 .3z" fill="${fh}"/>` : "";
  const dots = `<circle cx="26" cy="32" r="2.2" fill="${INK}"/><circle cx="38" cy="32" r="2.2" fill="${INK}"/>`;
  const eyes = { dots, happy: `<path d="M23.5 33q2.5-3.5 5 0M35.5 33q2.5-3.5 5 0" ${line()}/>`,
    wink: `<circle cx="26" cy="32" r="2.2" fill="${INK}"/><path d="M35.5 32.5h5" ${line()}/>`,
    big: `<circle cx="26" cy="32" r="4" fill="#fff"/><circle cx="38" cy="32" r="4" fill="#fff"/><circle cx="26.8" cy="32.5" r="2" fill="${INK}"/><circle cx="38.8" cy="32.5" r="2" fill="${INK}"/>`,
    sleepy: `<path d="M23.5 32q2.5 2 5 0M35.5 32q2.5 2 5 0" ${line()}/>`,
    stars: `<polygon points="${starPts(26, 32, 3.4)}" fill="${INK}"/><polygon points="${starPts(38, 32, 3.4)}" fill="${INK}"/>`,
    hearts: heartAt(26, 31.5, 2.4, "#e11d48") + heartAt(38, 31.5, 2.4, "#e11d48"),
    angry: `<circle cx="26" cy="33" r="2" fill="${INK}"/><circle cx="38" cy="33" r="2" fill="${INK}"/><path d="M23 29.5l6 2M41 29.5l-6 2" ${line(1.6)}/>`,
    lashes: `${dots}<path d="M23.4 30.4l-1.6-1.4M25.2 29.6l-.5-1.9M40.6 30.4l1.6-1.4M38.8 29.6l.5-1.9" ${line(1.1)}/>`,
    dizzy: `<path d="M24 30l4 4M28 30l-4 4M36 30l4 4M40 30l-4 4" ${line(1.7)}/>` }[a.eyes] || "";
  const brows = { flat: `<path d="M23 26.5h6M35 26.5h6" ${line(1.8)}/>`, raised: `<path d="M23 26.5q3-3 6-1.2M35 25.3q3-1.8 6 1.2" ${line(1.8)}/>`,
    angry: `<path d="M23 25l6 2.2M41 25l-6 2.2" ${line(1.8)}/>`, thick: `<path d="M23 26.3h6M35 26.3h6" ${line(3.2)}/>` }[a.brows] || "";
  const front = { short: top, long: top, bun: top, ponytail: top, pigtails: top, dreads: top,
    buzz: `<path d="M16.5 28c0-9 7-13.5 15.5-13.5S47.5 19 47.5 28c-4-4-9.5-6-15.5-6s-11.5 2-15.5 6z" fill="${hc}" opacity=".9"/>`,
    afro: `<path d="M13 31c0-14 8-21 19-21s19 7 19 21c-4-6-11-9-19-9s-15 3-19 9z" fill="${hc}"/>`,
    spiky: `<path d="M15 30l2-14 5 6 4-11 6 9 6-9 4 11 5-6 2 14c-5-6-10-8-17-8s-12 2-17 8z" fill="${hc}"/>`,
    mohawk: `<path d="M28 23V7c0-2 8-2 8 0v16z" fill="${hc}"/>`,
    curly: [16, 22, 28, 34, 40, 46].map((x, i) => `<circle cx="${x + 1}" cy="${i % 2 ? 16 : 20}" r="6.5" fill="${hc}"/>`).join(""),
    side: `<path d="M15 31c0-13 7-18 17-18s17 5 17 14c-9-3-19-1-26 8c-3 0-6-2-8-4z" fill="${hc}"/>`,
    emo: `<path d="M15 30c0-12 7-17 17-17s17 5 17 15c-5-3-10-4-15-3c-4 3-8 8-10 14c-3-3-6-6-9-9z" fill="${hc}"/>` }[a.hair] || "";
  const bridge = `<path d="M30.6 32h2.8" stroke="#111" stroke-width="1.4"/>`;
  const glasses = { shades: `<g fill="#111"><rect x="21" y="28.5" width="10" height="7" rx="2.5"/><rect x="33" y="28.5" width="10" height="7" rx="2.5"/></g><path d="M31 31h2M17 30l4 .5M47 30l-4 .5" stroke="#111" stroke-width="1.6"/>`,
    round: `<g fill="none" stroke="#111" stroke-width="1.6"><circle cx="26" cy="32" r="4.6"/><circle cx="38" cy="32" r="4.6"/><path d="M30.6 32h2.8M21.4 31.5l-4-1M42.6 31.5l4-1"/></g>`,
    stars: `<polygon points="${starPts(26, 32, 5.4, 5, .5)}" fill="#facc15" stroke="#b45309" stroke-width=".8"/><polygon points="${starPts(38, 32, 5.4, 5, .5)}" fill="#facc15" stroke="#b45309" stroke-width=".8"/>${bridge}`,
    hearts: heartAt(26, 32, 3.6, "#ec4899") + heartAt(38, 32, 3.6, "#ec4899") + bridge,
    visor: `<rect x="17" y="28" width="30" height="7.5" rx="3.75" fill="#22d3ee" opacity=".8"/><rect x="19" y="29" width="10" height="2" rx="1" fill="#fff" opacity=".6"/>`,
    eyepatch: `<path d="M17 25.5L47 29" stroke="#111" stroke-width="1.4"/><ellipse cx="38" cy="32" rx="5" ry="4.4" fill="#111"/>` }[a.glasses] || "";
  const hat = { beanie: `<path d="M15 26c0-11 7-16 17-16s17 5 17 16z" fill="#3b82f6"/><rect x="14" y="23" width="36" height="5" rx="2.5" fill="#2563eb"/><circle cx="32" cy="9" r="3.5" fill="#f8fafc"/>`,
    cap: `<path d="M15.5 27c0-10.5 7-15.5 16.5-15.5s16.5 5 16.5 15.5z" fill="#ef4444"/><path d="M31 25h22c0 3-3 4.5-6 4.5H31z" fill="#b91c1c"/><circle cx="32" cy="11.8" r="1.6" fill="#b91c1c"/>`,
    cowboy: `<ellipse cx="32" cy="22" rx="25" ry="4.5" fill="#92400e"/><path d="M21 22c0-9 3-14 6-14c2 0 3 2 5 2s3-2 5-2c3 0 6 5 6 14z" fill="#a16207"/><rect x="21" y="18" width="22" height="3" fill="#78350f"/>`,
    crown: `<path d="M21 15l2-10 5 6 4-8 4 8 5-6 2 10z" fill="#facc15" stroke="#b45309" stroke-width="1" stroke-linejoin="round"/>`,
    horns: `<path d="M19 22c-5-3-7-9-5-15c2 4 5 6 9 8z" fill="#dc2626"/><path d="M45 22c5-3 7-9 5-15c-2 4-5 6-9 8z" fill="#dc2626"/>`,
    halo: `<ellipse cx="32" cy="8" rx="12" ry="3.2" fill="none" stroke="#fde047" stroke-width="2.6"/>`,
    headband: `<rect x="15" y="21" width="34" height="5" rx="2.5" fill="#ef4444"/>`,
    headphones: `<path d="M14 35v-5a18 18 0 0 1 36 0v5" fill="none" stroke="#222" stroke-width="3.5"/><rect x="10.5" y="29" width="7" height="12" rx="3" fill="#ef4444"/><rect x="46.5" y="29" width="7" height="12" rx="3" fill="#ef4444"/>`,
    tophat: `<rect x="22" y="1" width="20" height="19" rx="2" fill="#111"/><rect x="15" y="18.5" width="34" height="4" rx="2" fill="#111"/><rect x="22" y="14" width="20" height="3.2" fill="#dc2626"/>`,
    wizard: `<path d="M32 -1l13 23H19z" fill="#6d28d9"/><ellipse cx="32" cy="22" rx="19" ry="3.6" fill="#5b21b6"/><polygon points="${starPts(30, 11, 2.6)}" fill="#fde047"/><polygon points="${starPts(36, 17, 1.8)}" fill="#fde047"/>` }[a.hat] || "";
  const bling = { earrings: `<circle cx="${earX}" cy="38.6" r="1.6" fill="#facc15"/><circle cx="${64 - earX}" cy="38.6" r="1.6" fill="#facc15"/>`,
    nosering: `<circle cx="33.6" cy="37.6" r="1.6" fill="none" stroke="#facc15" stroke-width="1"/>`,
    chain: `<path d="M23 52q9 7 18 0" fill="none" stroke="#facc15" stroke-width="1.6" stroke-dasharray="1.6 1"/><circle cx="32" cy="56.6" r="1.8" fill="#facc15"/>` }[a.extra] || "";
  return `<svg viewBox="0 0 64 64" aria-hidden="true">${pattern}${back}<rect x="27" y="42" width="10" height="12" fill="${sk}"/>${shirt}${ears}${face}${onFace}${chin}${mouth}${tache}${eyes}${brows}${front}${glasses}${hat}${bling}</svg>`;
}
const OPTIONAL_PARTS = ["facial_hair", "glasses", "hat", "extra", "pattern"];
const pickOne = (xs) => xs[Math.floor(Math.random() * xs.length)];
const randomAvatar = () => Object.fromEntries(Object.entries(S.me.avatar_parts || {}).map(([k, opts]) =>
  [k, OPTIONAL_PARTS.includes(k) && Math.random() < .55 ? "none" : pickOne(opts)]));
const AV_TABS = [["face", "Face", ["face", "skin", "eyes", "brows", "mouth", "facial_hair", "extra"]], ["hair", "Hair & hats", ["hair", "hair_color", "hat"]],
  ["style", "Style", ["glasses", "top", "top_color", "pattern", "bg"]], ["photo", "Photo", []]];
const PART_LABEL = { face: "Face shape", skin: "Skin", hair: "Hair", hair_color: "Hair colour", brows: "Eyebrows", eyes: "Eyes", mouth: "Mouth",
  facial_hair: "Facial hair", glasses: "Glasses", hat: "Hat", top: "Top", top_color: "Top colour", extra: "Extras", pattern: "Background pattern", bg: "Background colour" };
const SWATCH_PARTS = ["skin", "hair_color", "top_color"];
const photoURL = (name, v) => `/api/avatar/photo?${qs({ name, v })}`;

// the avatar builder: the preview stays at the top while the parts scroll underneath
function editAvatar(after) {
  const parts = S.me.avatar_parts || {};
  let cur = { ...randomAvatar(), ...(avatarOf(S.me.who) || {}) }, bg = S.me.color || (S.me.colors || [])[0];
  let tab = S.me.photo ? "photo" : "face", busy = false;
  const scrim = document.createElement("div"); scrim.className = "scrim";
  const m = document.createElement("div"); m.className = "modal card avmodal";
  const close = () => { scrim.remove(); m.remove(); };
  scrim.onclick = close;
  const opt = (k, v) => SWATCH_PARTS.includes(k)
    ? `<button class="swatch${cur[k] === v ? " on" : ""}" data-part="${k}" data-v="${v}" style="background:${v}" aria-label="${PART_LABEL[k]}"></button>`
    : `<button class="avopt${cur[k] === v ? " on" : ""}" data-part="${k}" data-v="${v}" title="${esc(v)}"><span class="avatar art" style="background:${bg}">${avatarSVG({ ...cur, ...(k === "eyes" || k === "brows" ? { glasses: "none" } : {}), [k]: v })}</span></button>`;
  const section = (k) => k === "bg"
    ? `<div class="avsec"><div class="faint">${PART_LABEL.bg}</div><div class="swatches">${(S.me.colors || []).map((c) =>
        `<button class="swatch${c === bg ? " on" : ""}" data-bg="${c}" style="background:${c}" aria-label="Background colour"></button>`).join("")}</div></div>`
    : `<div class="avsec"><div class="faint">${PART_LABEL[k] || k}</div><div class="swatches">${(parts[k] || []).map((v) => opt(k, v)).join("")}</div></div>`;
  const photoBody = () => `<div class="avsec"><p style="margin-top:0">Use a picture instead of a built avatar. It's cropped to a square and kept on ${S.me.local ? "this" : "the host's"} PC.</p>
      <div class="row wrap" style="gap:8px"><button class="btn primary" id="avup" ${busy ? "disabled" : ""}>${busy ? "Uploading…" : S.me.photo ? "Upload a different photo" : "Upload a photo"}</button>
        ${S.me.photo ? `<button class="btn" id="avphotox">Remove photo</button>` : ""}</div>
      <input type="file" id="avfile" accept="image/*" hidden>
      ${S.me.photo ? `<p class="faint">Your photo shows everywhere your avatar does. Pick a tab above and press Save to go back to a built avatar.</p>` : ""}</div>`;
  const draw = () => {
    const keep = $(".avbody", m) ? $(".avbody", m).scrollTop : 0, sameTab = m.dataset.tab === tab;
    m.dataset.tab = tab;
    const preview = tab === "photo"
      ? (S.me.photo ? `<span class="avatar art"><img src="${photoURL(S.me.who, S.me.photo)}" alt=""></span>` : `<span class="avatar" style="background:${bg}">${esc(initials(S.me.who))}</span>`)
      : `<span class="avatar art" style="background:${bg}">${avatarSVG(cur)}</span>`;
    m.innerHTML = `<div class="avtop"><div class="row spread"><h2 style="margin:0">Your avatar</h2><button class="btn small" id="avx">Close</button></div>
        <div class="avpreview">${preview}</div>
        <div class="tabs avtabs">${AV_TABS.map(([k, l]) => `<button class="${tab === k ? "on" : ""}" data-avtab="${k}">${l}</button>`).join("")}</div></div>
      <div class="avbody">${tab === "photo" ? photoBody() : AV_TABS.find(([k]) => k === tab)[2].map(section).join("")}</div>
      <div class="avfoot">${tab === "photo" ? `<button class="btn primary" id="avdone">Done</button>` : `<button class="btn" id="avrand">Randomise</button>
        ${avatarOf(S.me.who) || S.me.photo ? `<button class="btn" id="avnone">Initials</button>` : ""}<span class="grow"></span>
        <button class="btn" id="avcancel">Cancel</button><button class="btn primary" id="avsave">Save</button>`}</div>`;
    if (sameTab) $(".avbody", m).scrollTop = keep;
    const on = (sel, fn) => { const el = $(sel, m); if (el) el.onclick = fn; };
    on("#avx", close); on("#avcancel", close); on("#avdone", () => { close(); after && after(); });
    on("#avrand", () => { cur = randomAvatar(); draw(); });
    $$("[data-avtab]", m).forEach((b) => b.onclick = () => { tab = b.dataset.avtab; draw(); });
    $$("[data-bg]", m).forEach((b) => b.onclick = () => { bg = b.dataset.bg; draw(); });
    $$("[data-part]", m).forEach((b) => b.onclick = () => { cur[b.dataset.part] = b.dataset.v; draw(); });
    const save = async (avatar) => {
      try {
        const body = { avatar, photo: null };
        if (bg !== S.me.color) body.color = bg;
        S.me = await post("/api/me", body);
        await loadPeople(); close(); toast(avatar ? "Avatar saved" : "Back to your initials"); after && after();
      } catch (e) { toast(e.message); }
    };
    on("#avsave", () => save(cur));
    on("#avnone", () => save(null));
    on("#avup", () => $("#avfile", m).click());
    const file = $("#avfile", m);
    if (file) file.onchange = async (e) => {
      const f = e.target.files[0]; if (!f) return;
      const fd = new FormData(); fd.append("file", f);
      busy = true; draw();
      try { S.me = await api("/api/me/photo", { method: "POST", body: fd }); await loadPeople(); toast("Photo saved"); }
      catch (err) { toast(err.message); }
      busy = false; draw();
    };
    on("#avphotox", async () => {
      try { S.me = await post("/api/me", { photo: null }); await loadPeople(); toast("Photo removed"); draw(); } catch (e) { toast(e.message); }
    });
  };
  draw();
  document.body.append(scrim, m);
}

// ---------- band logos: an emblem, an icon and the band's name, drawn here; or an uploaded picture ----------
let logoN = 0;
const LOGO_FONTS = {  // family, weight, capitals, letter spacing: phones fall back along each list
  block: ["Impact, 'Arial Black', 'Helvetica Neue', sans-serif", 400, true, 1], metal: ["'Old English Text MT', 'UnifrakturMaguntia', 'Times New Roman', serif", 700, false, 0],
  serif: ["Georgia, 'Times New Roman', serif", 700, true, 2], script: ["'Brush Script MT', 'Segoe Script', 'Snell Roundhand', cursive", 700, false, 0],
  mono: ["'Courier New', Courier, monospace", 700, true, 1], round: ["'Trebuchet MS', 'Arial Rounded MT Bold', 'Avenir Next', sans-serif", 700, false, 0],
  stencil: ["Stencil, 'Stencil Std', Impact, sans-serif", 400, true, 3], horror: ["Chiller, 'Papyrus', 'Bradley Hand', fantasy", 700, true, 1],
  comic: ["'Comic Sans MS', 'Chalkboard SE', 'Marker Felt', cursive", 700, false, 0], western: ["'Rockwell Extra Bold', Rockwell, 'American Typewriter', serif", 700, true, 2],
  tech: ["'OCR A Extended', Consolas, Menlo, monospace", 700, true, 3], marker: ["'Ink Free', 'Segoe Print', 'Marker Felt', cursive", 700, false, 0],
  slab: ["'Arial Black', 'Rockwell Extra Bold', 'Helvetica Neue', sans-serif", 900, true, 0],
};
const LOGO_ICONS = {  // each drawn in a 48 x 48 box: c = icon colour, bg = what's behind it
  lightning: (c) => `<path d="M28 0L6 28h14l-6 20 26-30H26l8-18z" fill="${c}"/>`,
  guitar: (c, bg) => `<path d="M34 4l10 10-4 4-3-1-11 11c2 5 1 10-3 14-5 5-14 5-19 0s-5-14 0-19c4-4 9-5 14-3l11-11-1-3z" fill="${c}"/><circle cx="15" cy="33" r="4" fill="${bg}"/>`,
  skull: (c, bg) => `<path d="M24 2C12 2 4 10 4 21c0 7 3 11 7 14v9h26v-9c4-3 7-7 7-14C44 10 36 2 24 2z" fill="${c}"/><circle cx="15" cy="22" r="5" fill="${bg}"/><circle cx="33" cy="22" r="5" fill="${bg}"/><path d="M24 28l-3 5h6zM18 38v6M24 38v6M30 38v6" fill="${bg}" stroke="${bg}" stroke-width="2"/>`,
  flame: (c) => `<path d="M24 0c3 10 14 14 14 28 0 11-7 20-14 20S10 39 10 28c0-6 3-10 6-13 0 6 3 9 6 9-3-8 0-18 2-24z" fill="${c}"/>`,
  star: (c) => `<polygon points="${starPts(24, 26, 25)}" fill="${c}"/>`,
  crown: (c) => `<path d="M2 38L5 10l11 12 8-18 8 18 11-12 3 28z" fill="${c}"/><rect x="3" y="40" width="42" height="6" rx="2" fill="${c}"/>`,
  wings: (c) => `<path d="M22 26C14 10 4 8-2 10c4 3 6 6 6 9-3 0-5 1-6 3 4 1 7 3 8 6-3 0-5 2-5 4 6 0 13-1 21-6z" fill="${c}"/><path d="M26 26c8-16 18-18 24-16-4 3-6 6-6 9 3 0 5 1 6 3-4 1-7 3-8 6 3 0 5 2 5 4-6 0-13-1-21-6z" fill="${c}"/><circle cx="24" cy="27" r="5" fill="${c}"/>`,
  drums: (c, bg) => `<path d="M4 22v15c0 4 9 7 20 7s20-3 20-7V22" fill="${c}"/><ellipse cx="24" cy="22" rx="20" ry="7" fill="${c}" stroke="${bg}" stroke-width="2"/><path d="M6 2l18 18M42 2L24 20" stroke="${c}" stroke-width="3.5" stroke-linecap="round"/>`,
  mic: (c) => `<rect x="16" y="0" width="16" height="27" rx="8" fill="${c}"/><path d="M9 20c0 9 7 15 15 15s15-6 15-15M24 35v9M15 46h18" fill="none" stroke="${c}" stroke-width="3.5" stroke-linecap="round"/>`,
  note: (c) => `<path d="M16 7l28-7v35a7 6 0 1 1-4-5.5V9L20 14v26a7 6 0 1 1-4-5.5z" fill="${c}"/>`,
  eye: (c, bg) => `<path d="M0 24C8 12 16 8 24 8s16 4 24 16c-8 12-16 16-24 16S8 36 0 24z" fill="${c}"/><circle cx="24" cy="24" r="9" fill="${bg}"/><circle cx="24" cy="24" r="4" fill="${c}"/>`,
  moon: (c) => `<path d="M32 3a22 22 0 1 0 14 35A18 18 0 1 1 32 3z" fill="${c}"/>`,
  planet: (c) => `<circle cx="24" cy="24" r="13" fill="${c}"/><ellipse cx="24" cy="24" rx="24" ry="7" fill="none" stroke="${c}" stroke-width="3" transform="rotate(-20 24 24)"/>`,
  heart: (c) => heartAt(24, 25, 17, c),
  sword: (c) => `<path d="M24 0l4 6v24h-8V6z" fill="${c}"/><rect x="11" y="29" width="26" height="5" rx="2.5" fill="${c}"/><rect x="21.5" y="34" width="5" height="9" fill="${c}"/><circle cx="24" cy="45" r="3" fill="${c}"/>`,
  cassette: (c, bg) => `<rect x="1" y="7" width="46" height="32" rx="4" fill="${c}"/><rect x="8" y="12" width="32" height="13" rx="6.5" fill="${bg}"/><circle cx="15" cy="18.5" r="3.5" fill="${c}"/><circle cx="33" cy="18.5" r="3.5" fill="${c}"/><path d="M12 39l3-7h18l3 7z" fill="${bg}"/>`,
  amp: (c, bg) => `<rect x="4" y="2" width="40" height="44" rx="4" fill="${c}"/><rect x="8" y="6" width="32" height="6" rx="1.5" fill="${bg}"/><circle cx="13" cy="9" r="1.6" fill="${c}"/><circle cx="19" cy="9" r="1.6" fill="${c}"/><circle cx="25" cy="9" r="1.6" fill="${c}"/><circle cx="24" cy="30" r="12" fill="${bg}"/><circle cx="24" cy="30" r="4" fill="${c}"/>`,
  ghost: (c, bg) => `<path d="M24 2C13 2 6 10 6 21v25l6-5 6 5 6-5 6 5 6-5 6 5V21C42 10 35 2 24 2z" fill="${c}"/><circle cx="18" cy="20" r="3.5" fill="${bg}"/><circle cx="30" cy="20" r="3.5" fill="${bg}"/>`,
  cat: (c, bg) => `<path d="M6 4l10 10h16L42 4v24c0 10-8 18-18 18S6 38 6 28z" fill="${c}"/><circle cx="17" cy="26" r="3" fill="${bg}"/><circle cx="31" cy="26" r="3" fill="${bg}"/><path d="M22 33h4l-2 3z" fill="${bg}"/>`,
  alien: (c, bg) => `<path d="M24 2C11 2 4 11 4 20c0 13 12 26 20 26s20-13 20-26C44 11 37 2 24 2z" fill="${c}"/><path d="M9 20c5 0 11 3 12 9-6 1-11-3-12-9zM39 20c-5 0-11 3-12 9 6 1 11-3 12-9z" fill="${bg}"/>`,
  horns: (c) => `<path d="M4 46C1 30 5 14 16 3c-3 13-2 24 7 33zM44 46c3-16-1-32-12-43 3 13 2 24-7 33z" fill="${c}"/>`,
  rocket: (c, bg) => `<path d="M24 0c8 6 12 16 12 28l6 8v8l-8-4H14l-8 4v-8l6-8C12 16 16 6 24 0z" fill="${c}"/><circle cx="24" cy="20" r="5" fill="${bg}"/>`,
  gem: (c, bg) => `<path d="M12 4h24l10 12-22 30L2 16z" fill="${c}"/><path d="M2 16h44M12 4l6 12 6-12 6 12 6-12M18 16l6 30 6-30" fill="none" stroke="${bg}" stroke-width="1.6" opacity=".55"/>`,
  pick: (c) => `<path d="M24 46C14 36 4 22 4 12 4 5 12 2 24 2s20 3 20 10c0 10-10 24-20 34z" fill="${c}"/>`,
  peace: (c) => `<circle cx="24" cy="24" r="20" fill="none" stroke="${c}" stroke-width="5"/><path d="M24 4v40M24 24L10 38M24 24l14 14" fill="none" stroke="${c}" stroke-width="5"/>`,
};
const OCT = Array.from({ length: 8 }, (_, i) => { const a = Math.PI / 8 + i * Math.PI / 4; return `${(80 + 66 * Math.cos(a)).toFixed(1)},${(72 + 66 * Math.sin(a)).toFixed(1)}`; }).join(" ");
const LOGO_SHAPES = {  // the emblem, about 140 across, centred near (80, 72)
  circle: `<circle cx="80" cy="72" r="62"/>`, ring: `<circle cx="80" cy="72" r="62"/>`, shield: `<path d="M80 8l58 17v38c0 37-26 62-58 76c-32-14-58-39-58-76V25z"/>`,
  hexagon: `<polygon points="80,6 138,39 138,105 80,138 22,105 22,39"/>`, diamond: `<polygon points="80,2 150,72 80,142 10,72"/>`,
  star: `<polygon points="${starPts(80, 80, 78, 5, .5)}"/>`, star6: `<polygon points="${starPts(80, 74, 72, 6, .62)}"/>`,
  banner: `<path d="M6 24h148l-14 48 14 48H6l14-48z"/>`, burst: `<polygon points="${starPts(80, 72, 72, 14, .8)}"/>`,
  pick: `<path d="M80 140C52 114 18 74 18 42 18 18 44 6 80 6s62 12 62 36c0 32-34 72-62 98z"/>`, octagon: `<polygon points="${OCT}"/>`,
  triangle: `<polygon points="80,2 154,132 6,132"/>`, coffin: `<polygon points="54,2 106,2 132,40 114,142 46,142 28,40"/>`,
  heart: `<path d="M80 142C38 114 6 84 6 48 6 24 24 8 46 8c15 0 27 8 34 20 7-12 19-20 34-20 22 0 40 16 40 40 0 36-32 66-74 94z"/>`, none: "",
};
const hashOf = (s) => [...String(s)].reduce((h, c) => (h * 31 + c.charCodeAt(0)) >>> 0, 7);
const LOGO_DEFAULT_PARTS = { shape: ["circle", "shield", "hexagon", "star6", "pick", "octagon"], icon: ["lightning", "skull", "flame", "star", "guitar", "crown"],
  font: ["block", "slab", "stencil", "western"], color1: ["#ef4444", "#f97316", "#8b5cf6", "#3b82f6", "#14b8a6", "#ec4899", "#111827"] };
function defaultLogo(id) {  // every band has a logo; this one stands in until someone makes theirs
  const h = hashOf(id || "band"), at = (k, n) => LOGO_DEFAULT_PARTS[k][(h >>> n) % LOGO_DEFAULT_PARTS[k].length];
  return { shape: at("shape", 1), fill: "gradient", border: "solid", backdrop: "none", icon: at("icon", 5), layout: "middle", font: at("font", 9),
    effect: "shadow", tilt: "none", color1: at("color1", 13), color2: "#facc15", text_color: "#f8fafc" };
}
const seeded = (seed) => { let x = seed || 1; return () => ((x = (x * 1103515245 + 12345) >>> 0) % 10000) / 10000; };

function logoSVG(b, logo) {
  const base = defaultLogo(b.id), L = { ...base, ...(logo || b.logo || {}) }, id = "lg" + (++logoN);
  const [family, weight, upper, spacing] = LOGO_FONTS[L.font] || LOGO_FONTS.block;
  const c1 = L.color1, c2 = L.color2, tc = L.text_color, dark = shade(c1, .45), INKD = "#0b0c18";
  const shapeEl = LOGO_SHAPES[L.shape] ?? LOGO_SHAPES.circle, hasShape = !!shapeEl;
  // what sits behind the emblem
  const rnd = seeded(hashOf(b.id || b.name));
  const backdrop = { rays: `<g opacity=".45">${Array.from({ length: 16 }, (_, i) => { const a1 = i * Math.PI / 8, a2 = a1 + Math.PI / 16, p = (t) => `${(80 + 110 * Math.cos(t)).toFixed(1)},${(72 + 110 * Math.sin(t)).toFixed(1)}`; return `<polygon points="80,72 ${p(a1)} ${p(a2)}" fill="${c2}"/>`; }).join("")}</g>`,
    glow: `<circle cx="80" cy="72" r="70" fill="${c2}" opacity=".75" filter="url(#${id}b)"/>`,
    sparkles: [[14, 20, 9], [146, 26, 7], [8, 110, 6], [150, 100, 9], [30, 150, 5], [130, 148, 6], [80, 0, 6]].map(([x, y, r]) => `<polygon points="${starPts(x, y, r, 4, .3)}" fill="${c2}"/>`).join(""),
    flames: `<g opacity=".95"><path d="M8 150c-6-30 8-46 14-62 2 16 8 20 12 26 0-22 8-40 20-52-2 22 8 30 12 40 4-14 10-24 14-30 4 6 10 16 14 30 4-10 14-18 12-40 12 12 20 30 20 52 4-6 10-10 12-26 6 16 20 32 14 62z" fill="#f97316"/><path d="M26 150c-4-18 4-28 8-36 2 10 6 12 8 16 2-14 6-24 12-30 0 14 6 18 8 24 4-10 8-16 12-20 4 4 8 10 12 20 2-6 8-10 8-24 6 6 10 16 12 30 2-4 6-6 8-16 4 8 12 18 8 36z" fill="#facc15"/></g>`,
    bolts: `<path d="M22 4L2 70h18L8 140 50 52H30L44 4z" fill="${c2}"/><path d="M138 4l20 66h-18l12 70-42-88h20L116 4z" fill="${c2}"/>`,
    wings: `<g fill="${c2}" opacity=".95"><path d="M60 60C40 30 14 22 0 26c8 6 12 12 12 18-6 0-10 2-12 6 8 2 14 6 16 12-6 0-10 4-10 8 8 0 14 2 18 6-4 2-6 6-6 10 16-2 32-8 46-22z"/><path d="M100 60c20-30 46-38 60-34-8 6-12 12-12 18 6 0 10 2 12 6-8 2-14 6-16 12 6 0 10 4 10 8-8 0-14 2-18 6 4 2 6 6 6 10-16-2-32-8-46-22z"/></g>` }[L.backdrop] || "";
  // the emblem: a fill (and texture) clipped to the shape, then its border
  const textures = { stripes: `<rect width="160" height="160" fill="url(#${id}ps)"/>`, dots: `<rect width="160" height="160" fill="url(#${id}pd)"/>`,
    checker: `<rect width="160" height="160" fill="url(#${id}pc)"/>`, grunge: `<rect width="160" height="160" filter="url(#${id}n)" opacity=".55"/>`,
    split: `<polygon points="160,0 160,160 0,160" fill="${shade(c1, .28)}"/>`,
    rays: `<g opacity=".25">${Array.from({ length: 12 }, (_, i) => { const a1 = i * Math.PI / 6, a2 = a1 + Math.PI / 12, p = (t) => `${(80 + 120 * Math.cos(t)).toFixed(1)},${(72 + 120 * Math.sin(t)).toFixed(1)}`; return `<polygon points="80,72 ${p(a1)} ${p(a2)}" fill="#fff"/>`; }).join("")}</g>` };
  const fillBase = L.fill === "gradient" ? `url(#${id}s)` : c1;
  const border = { solid: `stroke="${dark}" stroke-width="4"`, double: null, thick: `stroke="${INKD}" stroke-width="8"`,
    dashed: `stroke="${c2}" stroke-width="3.5" stroke-dasharray="9 6"`, studs: null, none: "" }[L.border] ?? `stroke="${dark}" stroke-width="4"`;
  const outline = (attrs) => shapeEl.replace("/>", ` fill="none" ${attrs} stroke-linejoin="round"/>`);
  const emblem = !hasShape ? "" : `<clipPath id="${id}c">${shapeEl}</clipPath><g clip-path="url(#${id}c)"><rect width="160" height="160" fill="${fillBase}"/>${textures[L.fill] || ""}</g>
    ${L.shape === "ring" ? `<circle cx="80" cy="72" r="50" fill="none" stroke="${c2}" stroke-width="3"/>` : ""}
    ${L.border === "double" ? outline(`stroke="${c2}" stroke-width="10"`) + outline(`stroke="${INKD}" stroke-width="4"`)
      : L.border === "studs" ? outline(`stroke="${dark}" stroke-width="4"`) + outline(`stroke="${c2}" stroke-width="5" stroke-dasharray="0 13" stroke-linecap="round"`)
      : border ? outline(border) : ""}`;
  // where the icon and the name go
  const lay = L.layout || "middle", hasIcon = L.icon !== "none" && LOGO_ICONS[L.icon];
  const iconAt = { middle: [80, 46, .9], arc: [80, 92, 1.05], ribbon: [80, 50, 1], top: [80, 92, 1.05], below: [80, 66, 1.45] }[lay] || [80, 46, .9];
  if (!b.name) { iconAt[1] = 72; iconAt[2] = 1.5; }
  const icon = hasIcon ? `<g transform="translate(${iconAt[0]} ${iconAt[1]}) scale(${iconAt[2]}) translate(-24 -24)">${LOGO_ICONS[L.icon](c2, hasShape ? c1 : INKD)}</g>` : "";
  const text = upper ? (b.name || "").toUpperCase() : (b.name || "");
  let lines = [text];
  if (lay !== "arc" && text.length > 13 && text.includes(" ")) {  // a long name on two lines, split near the middle
    const gaps = [...text.matchAll(/ /g)].map((m) => m.index), mid = gaps.reduce((x, g) => (Math.abs(g - text.length / 2) < Math.abs(x - text.length / 2) ? g : x));
    lines = [text.slice(0, mid), text.slice(mid + 1)];
  }
  const longest = Math.max(...lines.map((l) => l.length), 1), wide = upper ? .64 : .56;
  const room = lay === "ribbon" ? 138 : 150, cap = lay === "arc" ? 28 : lay === "ribbon" ? (lines.length > 1 ? 22 : 28) : lines.length > 1 ? 34 : 42;
  const fs = Math.max(lay === "arc" ? 11 : 13, Math.min(cap, room / (longest * wide + longest * spacing / 30)));
  const midY = { middle: 104, ribbon: 112, top: 34, below: 158 - fs * (lines.length > 1 ? 1.15 : .6) }[lay] ?? 104;
  const ys = lines.length > 1 ? [midY - fs * .55, midY + fs * .55] : [midY];
  const fitOf = (l, w) => (l.length * fs * wide + l.length * spacing > w ? ` textLength="${w}" lengthAdjust="spacingAndGlyphs"` : "");
  const tilt = { up: `rotate(-8 80 ${midY})`, down: `rotate(8 80 ${midY})`, italic: `translate(0 ${midY}) skewX(-14) translate(0 ${-midY})` }[L.tilt] || "";
  const font = `font-family="${family}" font-weight="${weight}" font-size="${fs.toFixed(1)}" letter-spacing="${spacing}"`;
  const t = (extra, dx = 0, dy = 0) => lay === "arc"
    ? `<text ${font} text-anchor="middle" ${extra} ${dx || dy ? `transform="translate(${dx} ${dy})"` : ""}><textPath href="#${id}a" startOffset="50%"${fitOf(text, 120)}>${esc(text)}</textPath></text>`
    : lines.map((l, i) => `<text x="${80 + dx}" y="${(ys[i] + dy).toFixed(1)}" text-anchor="middle" dominant-baseline="central" ${font}${fitOf(l, room)} ${extra}>${esc(l)}</text>`).join("");
  const drips = () => {  // paint running off the bottom of the letters
    const w = Math.min(room, longest * fs * wide), y0 = ys[ys.length - 1] + fs * .3;
    return Array.from({ length: Math.max(3, Math.round(w / 16)) }, () => { const x = 80 - w / 2 + rnd() * w, len = 5 + rnd() * 14, r = 1.6 + rnd() * 1.4;
      return `<path d="M${(x - r).toFixed(1)} ${y0.toFixed(1)}v${len.toFixed(1)}a${r.toFixed(1)} ${r.toFixed(1)} 0 0 0 ${(2 * r).toFixed(1)} 0v${(-len).toFixed(1)}z" fill="${tc}"/>`; }).join("");
  };
  const words = !text ? "" : { plain: t(`fill="${tc}"`),
    outline: t(`fill="${tc}" stroke="${INKD}" stroke-width="5" paint-order="stroke" stroke-linejoin="round"`),
    shadow: t(`fill="#000" opacity=".6"`, 3, 3) + t(`fill="${tc}" stroke="${INKD}" stroke-width="2" paint-order="stroke"`),
    glow: t(`fill="${tc}" filter="url(#${id}g)"`) + t(`fill="${tc}" stroke="${INKD}" stroke-width="1.5" paint-order="stroke"`),
    gradient: t(`fill="url(#${id}t)" stroke="${INKD}" stroke-width="3" paint-order="stroke"`),
    extrude: [5, 4, 3, 2, 1].map((d) => t(`fill="${shade(tc, .55)}" stroke="${INKD}" stroke-width="1"`, d, d)).join("") + t(`fill="${tc}" stroke="${INKD}" stroke-width="1.5" paint-order="stroke"`),
    neon: t(`fill="none" stroke="${tc}" stroke-width="5" filter="url(#${id}g)"`) + t(`fill="#fff" stroke="${tc}" stroke-width="1.6" paint-order="stroke"`),
    chrome: t(`fill="url(#${id}m)" stroke="${INKD}" stroke-width="3" paint-order="stroke" stroke-linejoin="round"`),
    fire: t(`fill="#f97316" filter="url(#${id}g)"`) + t(`fill="url(#${id}f)" stroke="#7c2d12" stroke-width="2" paint-order="stroke"`),
    drip: (lay === "arc" ? "" : drips()) + t(`fill="${tc}" stroke="${INKD}" stroke-width="1.5" paint-order="stroke"`),
    double: t(`fill="${tc}" stroke="${c2}" stroke-width="9" paint-order="stroke" stroke-linejoin="round"`) + t(`fill="${tc}" stroke="${INKD}" stroke-width="3.5" paint-order="stroke" stroke-linejoin="round"`) }[L.effect] || t(`fill="${tc}"`);
  const rh = Math.max(17, fs * (lines.length > 1 ? 1.15 : .8) + 5);  // the ribbon grows with the name on it
  const ribbon = lay === "ribbon" && text ? `<path d="M2 ${(midY - rh).toFixed(1)}h156l-11 ${rh.toFixed(1)} 11 ${rh.toFixed(1)}H2l11-${rh.toFixed(1)}z" fill="${c2}" stroke="${INKD}" stroke-width="3" stroke-linejoin="round"/>` : "";
  return `<svg viewBox="0 0 160 160" aria-hidden="true"><defs>
      <linearGradient id="${id}s" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${c1}"/><stop offset="1" stop-color="${dark}"/></linearGradient>
      <linearGradient id="${id}t" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${tc}"/><stop offset="1" stop-color="${c2}"/></linearGradient>
      <linearGradient id="${id}m" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#f8fafc"/><stop offset=".48" stop-color="#94a3b8"/><stop offset=".52" stop-color="#475569"/><stop offset="1" stop-color="#e2e8f0"/></linearGradient>
      <linearGradient id="${id}f" x1="0" y1="1" x2="0" y2="0"><stop offset="0" stop-color="#dc2626"/><stop offset=".55" stop-color="#f97316"/><stop offset="1" stop-color="#fde047"/></linearGradient>
      <pattern id="${id}ps" width="12" height="12" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="6" height="12" fill="${shade(c1, .22)}"/></pattern>
      <pattern id="${id}pd" width="10" height="10" patternUnits="userSpaceOnUse"><circle cx="5" cy="5" r="2.4" fill="${shade(c1, .3)}"/></pattern>
      <pattern id="${id}pc" width="20" height="20" patternUnits="userSpaceOnUse"><rect width="10" height="10" fill="${shade(c1, .25)}"/><rect x="10" y="10" width="10" height="10" fill="${shade(c1, .25)}"/></pattern>
      <filter id="${id}n" x="0" y="0" width="100%" height="100%"><feTurbulence type="fractalNoise" baseFrequency=".85" numOctaves="2" seed="${hashOf(b.id) % 97}"/><feColorMatrix values="0 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 -2.4 1.25"/></filter>
      <filter id="${id}g" x="-30%" y="-60%" width="160%" height="220%"><feGaussianBlur stdDeviation="3.5"/></filter>
      <filter id="${id}b" x="-40%" y="-40%" width="180%" height="180%"><feGaussianBlur stdDeviation="10"/></filter>
      <path id="${id}a" d="M36 ${hasShape ? 74 : 70}A44 44 0 0 1 124 ${hasShape ? 74 : 70}" fill="none"/></defs>
    ${backdrop}${emblem}${icon}${ribbon}${tilt ? `<g transform="${tilt}">${words}</g>` : words}</svg>`;
}
const bandArt = (b) => (b.image ? `<img src="/api/bands/${encodeURIComponent(b.id)}/image?v=${b.image}" alt="">` : logoSVG(b));
const LOGO_LABEL = { shape: "Shape", fill: "Fill and texture", border: "Border", backdrop: "Behind it", icon: "Icon", layout: "Where the name goes", font: "Lettering",
  effect: "Letter effect", tilt: "Tilt", color1: "Shape colour", color2: "Icon and trim colour", text_color: "Letter colour" };
const LOGO_TABS = [["emblem", "Emblem", ["shape", "fill", "border", "backdrop", "color1"]], ["icon", "Icon", ["icon", "color2"]],
  ["words", "Lettering", ["layout", "font", "effect", "tilt", "text_color"]], ["picture", "Picture", []]];
const randomLogo = () => {
  const P = S.me.logo_parts || {}, r = Object.fromEntries(Object.entries(P).map(([k, o]) => [k, pickOne(o)]));
  if (Math.random() < .5) r.backdrop = "none";  // busy enough most of the time without one
  while (r.text_color === r.color1) r.text_color = pickOne(P.text_color || ["#f8fafc"]);
  return r;
};

// the logo builder (any member): same layout as the avatar builder, with a Picture tab for an upload
function editLogo(b, after) {
  const parts = S.me.logo_parts || {};
  let cur = { ...defaultLogo(b.id), ...(b.logo || {}) }, tab = b.image ? "picture" : "emblem", busy = false, changed = false;
  const scrim = document.createElement("div"); scrim.className = "scrim";
  const m = document.createElement("div"); m.className = "modal card avmodal";
  const close = () => { scrim.remove(); m.remove(); if (changed && after) after(); };
  scrim.onclick = close;
  const draw = () => {
    const keep = $(".avbody", m) ? $(".avbody", m).scrollTop : 0, sameTab = m.dataset.tab === tab;
    m.dataset.tab = tab;
    const preview = tab === "picture" && b.image ? bandArt(b) : logoSVG(b, cur);
    const body = tab === "picture"
      ? `<div class="avsec"><p style="margin-top:0">Use a picture as the band's logo instead: a PNG with a see-through background looks best. It's kept on ${S.me.local ? "this" : "the host's"} PC.</p>
          <div class="row wrap" style="gap:8px"><button class="btn primary" id="lgup" ${busy ? "disabled" : ""}>${busy ? "Uploading…" : b.image ? "Upload a different picture" : "Upload a picture"}</button>
            ${b.image ? `<button class="btn" id="lgimgx">Remove picture</button>` : ""}</div><input type="file" id="lgfile" accept="image/*" hidden>
          ${b.image ? `<p class="faint">Pick another tab and press Save to go back to a built logo.</p>` : ""}</div>`
      : LOGO_TABS.find(([k]) => k === tab)[2].map((k) => `<div class="avsec"><div class="faint">${LOGO_LABEL[k] || k}</div><div class="swatches">${(parts[k] || []).map((v) =>
          k.includes("color") ? `<button class="swatch${cur[k] === v ? " on" : ""}" data-lp="${k}" data-v="${v}" style="background:${v}" aria-label="${LOGO_LABEL[k]}"></button>`
            : `<button class="logoopt${cur[k] === v ? " on" : ""}" data-lp="${k}" data-v="${v}" title="${esc(v)}"><span class="bandlogo">${logoSVG(b, { ...cur, [k]: v })}</span></button>`).join("")}</div></div>`).join("");
    m.innerHTML = `<div class="avtop"><div class="row spread"><h2 style="margin:0" class="ellipsis">${esc(b.name)} logo</h2><button class="btn small" id="lgx">Close</button></div>
        <div class="avpreview"><span class="bandlogo huge">${preview}</span></div>
        <div class="tabs avtabs">${LOGO_TABS.map(([k, l]) => `<button class="${tab === k ? "on" : ""}" data-lgtab="${k}">${l}</button>`).join("")}</div></div>
      <div class="avbody">${body}</div>
      <div class="avfoot">${tab === "picture" ? `<button class="btn primary" id="lgdone">Done</button>` : `<button class="btn" id="lgrand">Randomise</button>
        ${b.logo || b.image ? `<button class="btn" id="lgreset">Default</button>` : ""}<span class="grow"></span>
        <button class="btn" id="lgcancel">Cancel</button><button class="btn primary" id="lgsave">Save</button>`}</div>`;
    if (sameTab) $(".avbody", m).scrollTop = keep;
    const on = (sel, fn) => { const el = $(sel, m); if (el) el.onclick = fn; };
    on("#lgx", close); on("#lgcancel", close); on("#lgdone", () => { changed = true; close(); });
    on("#lgrand", () => { cur = randomLogo(); draw(); });
    $$("[data-lgtab]", m).forEach((x) => x.onclick = () => { tab = x.dataset.lgtab; draw(); });
    $$("[data-lp]", m).forEach((x) => x.onclick = () => { cur[x.dataset.lp] = x.dataset.v; draw(); });
    const save = async (logo) => { try { await bandOp(b.id, { op: "logo", logo }); changed = true; close(); toast("Logo saved"); } catch (e) { toast(e.message); } };
    on("#lgsave", () => save(cur));
    on("#lgreset", () => save(null));
    on("#lgup", () => $("#lgfile", m).click());
    const file = $("#lgfile", m);
    if (file) file.onchange = async (e) => {
      const f = e.target.files[0]; if (!f) return;
      const fd = new FormData(); fd.append("file", f);
      busy = true; draw();
      try { b = await api(`/api/bands/${encodeURIComponent(b.id)}/image`, { method: "POST", body: fd }); changed = true; toast("Picture saved"); }
      catch (err) { toast(err.message); }
      busy = false; draw();
    };
    on("#lgimgx", async () => { try { b = await bandOp(b.id, { op: "image_remove" }); changed = true; toast("Picture removed"); draw(); } catch (e) { toast(e.message); } });
  };
  draw();
  document.body.append(scrim, m);
}

// ---------- random band names ----------
const BN = {
  adj: ["Electric", "Velvet", "Midnight", "Screaming", "Cosmic", "Neon", "Rusty", "Atomic", "Savage", "Silent", "Burning", "Frozen", "Hollow", "Golden",
    "Feral", "Lunar", "Toxic", "Wild", "Broken", "Crimson", "Static", "Infinite", "Psychic", "Haunted", "Turbo", "Chrome", "Sonic", "Lucky", "Mighty",
    "Dirty", "Rabid", "Analog", "Radioactive", "Sleepy", "Furious", "Glitter", "Iron", "Holographic", "Unstoppable", "Suspicious"],
  plural: ["Wizards", "Raccoons", "Pickles", "Vipers", "Ghosts", "Wolves", "Robots", "Tacos", "Lizards", "Hornets", "Comets", "Bandits", "Goblins",
    "Sharks", "Llamas", "Pirates", "Cassettes", "Thunderbirds", "Gremlins", "Unicorns", "Toasters", "Skeletons", "Moths", "Dragons", "Fuzzboxes",
    "Crows", "Possums", "Astronauts", "Vampires", "Waffles", "Cowboys", "Ninjas", "Hamsters", "Mammoths", "Satellites"],
  noun: ["Thunder", "Riot", "Mayhem", "Static", "Venom", "Chaos", "Echo", "Voltage", "Doom", "Fury", "Gravity", "Nebula", "Rumble", "Noise",
    "Smoke", "Lava", "Fuzz", "Distortion", "Tornado", "Eclipse", "Feedback", "Overdrive", "Thunderclap", "Wreckage", "Moonlight", "Avalanche"],
  place: ["Mars", "the Basement", "the Garage", "Nowhere", "Sector 7", "the Void", "Saturn", "the Swamp", "Planet X", "the Moon", "Area 51",
    "the Abyss", "the Parking Lot", "Neptune", "the Upside Down", "Room 404"],
};
const BAND_FORMS = [(w) => `The ${w.adj} ${w.plural}`, (w) => `${w.adj} ${w.noun}`, (w) => `${w.noun} ${w.plural}`, (w) => `${w.plural} of ${w.place}`,
  (w) => `${w.noun} from ${w.place}`, (w) => `${w.adj} ${w.plural}`, (w) => `The ${w.plural}`, (w) => `${w.noun} & the ${w.plural}`,
  (w) => `Dr. ${w.noun} and the ${w.adj} ${w.plural}`, (w) => `${w.adj} ${w.noun} ${w.plural}`];
const bandName = () => { let n; do { n = pickOne(BAND_FORMS)(Object.fromEntries(Object.entries(BN).map(([k, v]) => [k, pickOne(v)]))); } while (n.length > 40); return n; };
const DICE = `<svg class="dice" viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="3" width="18" height="18" rx="4.5" fill="none" stroke="currentColor" stroke-width="2"/><g fill="currentColor"><circle cx="8.2" cy="8.2" r="1.6"/><circle cx="15.8" cy="8.2" r="1.6"/><circle cx="12" cy="12" r="1.6"/><circle cx="8.2" cy="15.8" r="1.6"/><circle cx="15.8" cy="15.8" r="1.6"/></g></svg>`;

// a small box for a name, with the dice for a random band name
function askBandName(title, value = "") {
  return new Promise((done) => {
    const scrim = document.createElement("div"); scrim.className = "scrim pop";
    const m = document.createElement("div"); m.className = "modal card pop";
    m.innerHTML = `<h2 style="margin-top:0">${esc(title)}</h2>
      <div class="row" style="gap:8px"><input id="bnin" class="grow" maxlength="40" value="${esc(value)}" placeholder="Band name"><button class="btn" id="bndice" title="Random band name" aria-label="Random band name">${DICE}</button></div>
      <div class="row" style="margin-top:14px;justify-content:flex-end;gap:8px"><button class="btn" id="bnno">Cancel</button><button class="btn primary" id="bnok">Save</button></div>`;
    document.body.append(scrim, m);
    const inp = $("#bnin", m), finish = (v) => { scrim.remove(); m.remove(); done(v); };
    inp.focus(); inp.select();
    $("#bndice", m).onclick = () => { inp.value = bandName(); };
    $("#bnno", m).onclick = () => finish(null); scrim.onclick = () => finish(null);
    const ok = () => finish(inp.value.trim() || null);
    $("#bnok", m).onclick = ok;
    inp.onkeydown = (e) => { if (e.key === "Enter") ok(); };
  });
}

// ---------- profiles ----------
const PARTS4 = ["guitar", "bass", "drums", "vocals"];
const tierOf = (v) => (v && v !== "-1" ? v : "–");
const tiersHTML = (t) => `<div class="tiers">${PARTS4.map((k) => `<span>${k} <b>${esc(tierOf((t || {})[k]))}</b></span>`).join("")}</div>`;
const WHERE = { quickplay: `<span class="chip accent">Quickplay</span>`, staging: `<span class="chip">Staging</span>` };
const fmtLen = (sec) => { const h = Math.floor(sec / 3600), m = Math.floor(sec / 60) % 60, s = String(sec % 60).padStart(2, "0"); return h ? `${h}:${String(m).padStart(2, "0")}:${s}` : `${m}:${s}`; };
const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;
document.addEventListener("click", (e) => {
  const b = e.target.closest("[data-band]");
  if (b && !e.defaultPrevented) location.hash = "band?" + qs({ id: b.dataset.band });
});
let prof = { name: "", shown: 60, q: "", where: "all" };

async function renderProfile() {
  const name = hashArg("name");
  if (pkey(name) !== pkey(prof.name)) prof = { name, shown: 60, q: "", where: "all" };
  if (lastPage !== name) page(esc(name), "", `<div class="empty"><span class="spinner"></span></div>`);
  let p;
  try { [p] = await Promise.all([api("/api/profile?" + qs({ name })), S.peopleList.length ? null : loadPeople()]); }
  catch (e) { page(esc(name), "", `<div class="card empty">${esc(e.message)}</div>`, `<button class="btn small" data-go="party">&larr; Party</button>`); return; }
  if (view() !== "profile" || hashArg("name") !== name) return;
  const n = (w) => p.songs.filter((s) => s.where === w).length;
  page(esc(p.name), p.me ? "Your profile" : `${esc(p.name)}'s profile`, `
    <div class="card profhead">${avatar(p.name, p.color, "huge" + (p.online ? " on" : ""), false)}
      <div class="grow" style="min-width:0">
        <div class="row wrap" style="gap:8px"><h2 style="margin:0">${esc(p.name)}</h2>${p.me ? `<span class="faint">(you)</span>` : ""}${p.owner ? `<span class="chip accent">Host</span>` : ""}</div>
        <div class="faint">${p.online ? "Here now" : p.last ? `Last here ${since(p.last)}` : "Not seen yet"}</div>
        <div class="stats"><span><b>${p.songs.length}</b> songs added</span><span><b>${p.hearts}</b> favourites</span><span><b>${p.bands.length}</b> ${p.bands.length === 1 ? "band" : "bands"}</span></div>
        ${p.me ? `<div class="row wrap" style="gap:8px;margin-top:12px"><button class="btn small" id="profav">${p.avatar || p.photo ? "Edit avatar" : "Make an avatar"}</button></div>` : ""}
      </div></div>
    <div class="card"><h2>Bands</h2>${p.bands.length ? `<div class="row wrap" style="gap:10px">${p.bands.map((b) =>
      `<button class="bandchip" data-band="${esc(b.id)}"><span class="bandlogo tiny">${bandArt(b)}</span><b>${esc(b.name)}</b><span class="avstack">${b.members.slice(0, 5).map((m) => avatar(m, "", "", false)).join("")}</span></button>`).join("")}</div>`
      : `<div class="faint">${p.me ? "Not in a band yet." : `${esc(p.name)} isn't in a band yet.`} <a href="#party" id="tobands">${p.me ? "Start one in Party &rarr; Bands" : "See the bands"}</a></div>`}</div>
    <div class="card"><div class="row spread wrap" style="gap:10px;margin-bottom:6px"><h2 style="margin:0">Songs added</h2>
        <div class="tabs">${[["all", `All ${p.songs.length}`], ["quickplay", `Quickplay ${n("quickplay")}`], ["staging", `Staging ${n("staging")}`]].map(([k, l]) =>
          `<button class="${prof.where === k ? "on" : ""}" data-pw="${k}">${l}</button>`).join("")}</div></div>
      <input id="profq" type="search" placeholder="Search ${esc(p.name)}'s songs" value="${esc(prof.q)}" style="width:100%;margin:8px 0 4px">
      <div id="profsongs"></div></div>`, `<button class="btn small" data-go="party">&larr; Party</button>`);
  const draw = () => {
    const q = prof.q.toLowerCase().trim();
    const songs = p.songs.filter((s) => (prof.where === "all" || s.where === prof.where) && (!q || `${s.title} ${s.artist} ${s.name}`.toLowerCase().includes(q)));
    $("#profsongs").innerHTML = songs.length ? songs.slice(0, prof.shown).map((s) => `<div class="item songrow link" data-song="${esc(s.folder)}" data-where="${s.where}">
        ${coverHTML(s, "setcover", 120)}
        <div class="grow" style="min-width:0"><div class="t ellipsis">${esc(s.title || s.name)}</div><div class="faint ellipsis">${esc(s.artist || "Unknown artist")}</div>${tiersHTML(s.tiers)}</div>
        ${WHERE[s.where]}</div>`).join("") + (songs.length > prof.shown ? `<div style="text-align:center;margin-top:12px"><button class="btn small" id="profmore">Show more (${songs.length - prof.shown} left)</button></div>` : "")
      : `<div class="empty">${p.songs.length ? "No songs match." : `${p.me ? "You haven't" : `${esc(p.name)} hasn't`} added any songs yet.`}</div>`;
    $$("[data-song]", $("#profsongs")).forEach((r) => r.onclick = () => openReview(r.dataset.song, r.dataset.where === "quickplay"));
    const more = $("#profmore"); if (more) more.onclick = () => { prof.shown += 60; draw(); };
  };
  draw();
  $("#profq").oninput = (e) => { prof.q = e.target.value; prof.shown = 60; draw(); };
  $$("[data-pw]").forEach((b) => b.onclick = () => { prof.where = b.dataset.pw; $$("[data-pw]").forEach((x) => x.classList.toggle("on", x === b)); draw(); });
  const av = $("#profav"); if (av) av.onclick = () => editAvatar(renderProfile);
  const tb = $("#tobands"); if (tb) tb.onclick = () => { partyTab = "bands"; };
}

// ---------- bands ----------
const bandOp = (id, body) => post("/api/bands/" + encodeURIComponent(id), body);
const inBand = (b) => b.members.some((m) => pkey(m) === pkey(S.me.who));

async function bandsHTML() {
  const list = await api("/api/bands");
  return `${list.length ? `<div class="people">${list.map((b) => `<div class="card bandcard link" data-band="${esc(b.id)}"><span class="bandlogo">${bandArt(b)}</span>
      <div class="grow" style="min-width:0"><div class="row spread" style="gap:8px"><b class="ellipsis">${esc(b.name)}</b>${inBand(b) ? `<span class="chip green">You're in it</span>` : ""}</div>
      <div class="avstack" style="margin:8px 0">${b.members.map((m) => avatar(m, "", "", false)).join("")}</div>
      <div class="faint">${plural(b.members.length, "member")} · ${plural(b.setlists.length, "set list")} · ${plural(b.setlists.reduce((t, s) => t + s.n, 0), "song")}</div></div></div>`).join("")}</div>`
    : `<div class="card empty">No bands yet. Start one below, then add your friends.</div>`}
    <div class="card"><h2>Start a band</h2>
      <div class="row" style="gap:8px"><input id="bandname" class="grow" maxlength="40" placeholder="Band name">
        <button class="btn" id="banddice" title="Random band name" aria-label="Random band name">${DICE}</button><button class="btn primary" id="bandnew">Start it</button></div>
      <p class="faint" style="margin-bottom:0">Stuck for a name? Roll the dice. You're the first member; add people, a logo and set lists on the band's page.</p></div>`;
}
function wireBands(body) {
  const go = async () => {
    try { const b = await post("/api/bands", { name: $("#bandname", body).value }); location.hash = "band?" + qs({ id: b.id }); }
    catch (e) { toast(e.message); }
  };
  $("#bandnew", body).onclick = go;
  $("#banddice", body).onclick = () => { $("#bandname", body).value = bandName(); };
  $("#bandname", body).onkeydown = (e) => { if (e.key === "Enter") go(); };
}

let bandSel = "", bandQ = "";
async function renderBand() {
  const id = hashArg("id");
  let b;
  try { [b] = await Promise.all([api("/api/bands/" + encodeURIComponent(id)), S.peopleList.length ? null : loadPeople()]); }
  catch (e) { page("Band", "", `<div class="card empty">${esc(e.message)}</div>`, `<button class="btn small" data-go="party">&larr; Party</button>`); return; }
  if (view() !== "band" || hashArg("id") !== id) return;
  const me = pkey(S.me.who), member = inBand(b), lead = owner() || pkey(b.by) === me, edit = member || owner();
  if (!b.setlists.some((s) => s.id === bandSel)) bandSel = (b.setlists[0] || {}).id || "";
  const sl = b.setlists.find((s) => s.id === bandSel);
  const others = (S.peopleList || []).filter((p) => !b.members.some((m) => pkey(m) === pkey(p.name)));
  page(esc(b.name), `Started by <b class="link" data-user="${esc(b.by)}">${esc(b.by)}</b> · ${plural(b.members.length, "member")}`, `
    <div class="card bandhero"><span class="bandlogo big${edit ? " link" : ""}" ${edit ? `id="logoimg" title="Edit the logo"` : ""}>${bandArt(b)}</span>
      <div class="grow" style="min-width:0"><div class="stats" style="margin-top:0"><span><b>${b.members.length}</b> ${b.members.length === 1 ? "member" : "members"}</span>
        <span><b>${b.setlists.length}</b> set ${b.setlists.length === 1 ? "list" : "lists"}</span><span><b>${b.setlists.reduce((t, s) => t + s.songs.length, 0)}</b> songs</span></div>
        ${edit ? `<div class="row wrap" style="gap:8px;margin-top:12px"><button class="btn small" id="logoedit">${b.logo || b.image ? "Edit logo" : "Make a logo"}</button></div>` : ""}</div></div>
    <div class="card"><h2>Members</h2><div class="members">${b.members.map((m) => `<div class="member">${avatar(m, "", "big")}
        <div style="min-width:0"><b class="link ellipsis" data-user="${esc(m)}">${esc(m)}</b>${pkey(m) === pkey(b.by) ? `<div class="faint">Started it</div>` : ""}</div>
        ${lead && pkey(m) !== me ? `<button class="btn small danger" data-kick="${esc(m)}" aria-label="Take ${esc(m)} out of the band">&#10005;</button>` : ""}</div>`).join("")}</div>
      ${edit && others.length ? `<div class="row wrap" style="gap:8px;margin-top:14px"><select id="addmem">${others.map((p) => `<option value="${esc(p.name)}">${esc(p.name)}</option>`).join("")}</select>
        <button class="btn small" id="addmembtn">Add to the band</button></div>` : ""}</div>
    <div class="card"><div class="row spread wrap" style="gap:10px"><h2 style="margin:0">Set lists</h2>${edit ? `<button class="btn small" id="slnew">+ New set list</button>` : ""}</div>
      ${b.setlists.length ? `<div class="tabs" style="margin-top:12px">${b.setlists.map((s) => `<button class="${s.id === bandSel ? "on" : ""}" data-sl="${esc(s.id)}">${esc(s.name)} <span class="faint">${s.songs.length}</span></button>`).join("")}</div>` : ""}
      ${sl ? `<div class="row spread wrap" style="gap:10px;margin:14px 0 4px"><div><b>${esc(sl.name)}</b> <span class="faint">· ${plural(sl.songs.length, "song")}${sl.length ? ` · ${fmtLen(sl.length)}` : ""}</span></div>
          ${edit ? `<div class="row" style="gap:6px"><button class="btn small" id="slren">Rename</button><button class="btn small danger" id="sldel">Delete</button></div>` : ""}</div>
        ${sl.songs.length ? sl.songs.map((s, i) => `<div class="item setrow songrow${s.here ? " link" : ""}" ${s.here ? `data-song="${esc(s.folder)}" data-where="${s.where}"` : ""}>
            <span class="setno">${i + 1}</span>${coverHTML(s, "setcover", 120)}
            <div class="grow" style="min-width:0"><div class="t ellipsis">${esc(s.title)}</div><div class="faint ellipsis">${esc(s.artist || "Unknown artist")}${s.length ? ` · ${fmtLen(s.length)}` : ""}</div>
              ${s.here ? tiersHTML(s.tiers) : `<span class="chip red">Not in your library any more</span>`}</div>
            ${edit ? `<div class="row setbtns"><button class="btn small" data-mv="-1" data-i="${i}" aria-label="Move up" ${i ? "" : "disabled"}>&#8593;</button>
              <button class="btn small" data-mv="1" data-i="${i}" aria-label="Move down" ${i < sl.songs.length - 1 ? "" : "disabled"}>&#8595;</button>
              <button class="btn small danger" data-rm="${i}" aria-label="Take off the set list">&#10005;</button></div>` : ""}</div>`).join("")
          : `<div class="empty">No songs on this set list yet.${edit ? " Search below, or tap <b>+ Band set list</b> on any song." : ""}</div>`}
        ${edit ? `<div class="addsong"><input id="songq" type="search" placeholder="Add a song: search staging and Quickplay" value="${esc(bandQ)}" style="width:100%"><div id="songhits"></div></div>` : ""}`
      : `<div class="empty">No set lists yet.</div>`}</div>`,
    `<button class="btn small" data-go="party">&larr; Party</button>${lead ? `<button class="btn small" id="bren">Rename</button><button class="btn small danger" id="bdel">Delete band</button>` : ""}${member ? `<button class="btn small" id="bleave">Leave</button>` : ""}`);

  const send = async (body, done) => {
    try { const r = await bandOp(id, body); if (r.deleted) { partyTab = "bands"; location.hash = "party"; return; } if (done) done(r); renderBand(); }
    catch (e) { toast(e.message); }
  };
  const ask = (q, v = "") => { const a = prompt(q, v); return a && a.trim() ? a.trim() : null; };
  $$("[data-sl]").forEach((x) => x.onclick = () => { bandSel = x.dataset.sl; renderBand(); });
  $$("[data-song]").forEach((r) => r.onclick = (e) => { if (!e.target.closest("button")) openReview(r.dataset.song, r.dataset.where === "quickplay"); });
  $$("[data-mv]").forEach((x) => x.onclick = () => send({ op: "song_move", setlist: bandSel, index: +x.dataset.i, step: +x.dataset.mv }));
  $$("[data-rm]").forEach((x) => x.onclick = () => send({ op: "song_remove", setlist: bandSel, index: +x.dataset.rm }));
  $$("[data-kick]").forEach((x) => x.onclick = () => { if (confirm(`Take ${x.dataset.kick} out of ${b.name}?`)) send({ op: "remove_member", name: x.dataset.kick }); });
  const on = (sel, fn) => { const el = $(sel); if (el) el.onclick = fn; };
  on("#addmembtn", () => send({ op: "add_member", name: $("#addmem").value }, () => toast("Added to the band")));
  on("#slnew", () => { const n = ask("Name the new set list", "Set " + (b.setlists.length + 1)); if (n) send({ op: "setlist_new", name: n }, (r) => { bandSel = r.setlists[r.setlists.length - 1].id; }); });
  on("#slren", () => { const n = ask("Rename the set list", sl.name); if (n) send({ op: "setlist_rename", setlist: bandSel, name: n }); });
  on("#sldel", () => { if (confirm(`Delete the set list “${sl.name}”?`)) send({ op: "setlist_delete", setlist: bandSel }); });
  on("#bren", async () => { const n = await askBandName("Rename the band", b.name); if (n && n !== b.name) send({ op: "rename", name: n }); });
  on("#logoedit", () => editLogo(b, renderBand)); on("#logoimg", () => editLogo(b, renderBand));
  on("#bdel", () => { if (confirm(`Delete ${b.name} and all its set lists?`)) send({ op: "delete" }); });
  on("#bleave", () => { if (confirm(`Leave ${b.name}?`)) send({ op: "leave" }, () => toast(`You left ${b.name}`)); });

  const q = $("#songq");
  if (!q) return;
  let timer;
  const search = async () => {
    bandQ = q.value;
    const box = $("#songhits");
    if (!bandQ.trim()) { box.innerHTML = ""; return; }
    let hits;
    try { hits = await api("/api/songs/find?" + qs({ q: bandQ, limit: 12 })); } catch (e) { box.innerHTML = `<p class="chip red">${esc(e.message)}</p>`; return; }
    if (q.value !== bandQ) return;
    const on = new Set(sl.songs.map((s) => s.key));
    box.innerHTML = hits.length ? hits.map((s) => `<div class="item">${coverHTML(s, "setcover small", 120)}
        <div class="grow" style="min-width:0"><div class="t ellipsis">${esc(s.title || s.name)}</div><div class="faint ellipsis">${esc(s.artist || "Unknown artist")}</div></div>
        ${WHERE[s.where]}${on.has(s.key) ? `<span class="faint">On the list</span>` : `<button class="btn small" data-addsong="${esc(s.folder)}">+ Add</button>`}</div>`).join("")
      : `<div class="faint" style="padding:8px 4px">No songs match.</div>`;
    $$("[data-addsong]", box).forEach((x) => x.onclick = () => send({ op: "song_add", setlist: bandSel, folder: x.dataset.addsong }, () => toast("Added to " + sl.name)));
  };
  q.oninput = () => { clearTimeout(timer); timer = setTimeout(search, 200); };
  if (bandQ) { search(); q.focus(); }
}

// from a song's drawer: pick one of your bands' set lists
async function pickSetlist(s) {
  let list;
  try { list = (await api("/api/bands")).filter((b) => inBand(b) || owner()); } catch (e) { toast(e.message); return; }
  const scrim = document.createElement("div"); scrim.className = "scrim pop";
  const m = document.createElement("div"); m.className = "modal card pop";
  const close = () => { scrim.remove(); m.remove(); };
  scrim.onclick = close;
  m.innerHTML = `<div class="row spread"><h2 style="margin:0">Add to a set list</h2><button class="btn small" id="pkx">Close</button></div>
    <p class="faint">${esc(s.title || s.name)}</p>
    ${list.length ? list.map((b) => `<div style="margin-top:10px"><b>${esc(b.name)}</b>${b.setlists.length ? b.setlists.map((sl) =>
      `<div class="item"><span class="grow">${esc(sl.name)} <span class="faint">· ${plural(sl.n, "song")}</span></span><button class="btn small" data-pb="${esc(b.id)}" data-ps="${esc(sl.id)}">+ Add</button></div>`).join("")
      : `<div class="faint">No set lists yet.</div>`}</div>`).join("")
      : `<p>You're not in a band yet.</p><button class="btn primary" id="pkstart">Start a band</button>`}`;
  document.body.append(scrim, m);
  $("#pkx", m).onclick = close;
  const st = $("#pkstart", m); if (st) st.onclick = () => { close(); closeDrawer(); partyTab = "bands"; location.hash = "party"; };
  $$("[data-pb]", m).forEach((x) => x.onclick = async () => {
    try { await bandOp(x.dataset.pb, { op: "song_add", setlist: x.dataset.ps, folder: s.folder }); toast("Added to the set list"); close(); }
    catch (e) { toast(e.message); }
  });
}

const isPhone = () => /iPhone|iPad|Android/i.test(navigator.userAgent);
const standalone = () => !!navigator.standalone || matchMedia("(display-mode: standalone)").matches;
const HOME_STEPS = `<ol class="steps"><li><b>iPhone:</b> in Safari tap <b>Share</b>, then <b>Add to Home Screen</b>.</li>
  <li><b>Android:</b> in Chrome tap the <b>&#8942;</b> menu, then <b>Add to Home screen</b>.</li></ol>`;
async function inviteHTML() {
  const inv = await api("/api/invite");
  return `<div class="card"><h2>Bring a friend in</h2>
      ${inv.qr ? `<div class="row wrap" style="align-items:flex-start;gap:20px"><div class="qr">${inv.qr}</div>
        <div class="grow" style="min-width:220px"><p style="margin-top:0">They scan this with their phone's camera, type their name, and they're in. Songs they add carry their name.</p>
          <p class="faint">They need to be on your Wi-Fi, or on your Tailscale network away from home.</p>
          <button class="btn primary" id="qrbig">Show it big</button></div></div>`
      : owner() ? `<p class="muted">No network address found for this PC, so there's no code to show.</p>`
      : `<p style="margin:0">Ask <b>${esc(inv.owner)}</b> to open <b>Party → Invite</b> and show the code. Only the host can let new people in.</p>`}</div>
    <div class="card"><h2>Put RockWizard on your home screen</h2>
      <p class="faint" style="margin-top:0">${standalone() ? "You're already using it from your home screen." : "It opens full screen with its own icon, like any other app."}</p>${HOME_STEPS}</div>`;
}
function wireInvite(body) {
  const b = $("#qrbig", body);
  if (b) b.onclick = () => {
    const el = document.createElement("div"); el.className = "qrbig";
    el.innerHTML = `<h1>Scan to join RockWizard</h1><div class="qr">${$(".qr", body).innerHTML}</div><p class="muted">Point your phone's camera at the code. Tap anywhere to close.</p>`;
    el.onclick = () => el.remove();
    document.body.append(el);
  };
}
// a nudge on phones that are still using it in the browser
function homeTip() {
  let seen = false;
  try { seen = !!localStorage.getItem("a2hs"); } catch (e) { /* asked again next time */ }
  if (!isPhone() || standalone() || seen || !$("#main .top")) return;
  $("#main .top").insertAdjacentHTML("afterend", `<div class="card tip" id="a2hs"><div class="row spread"><b>Add RockWizard to your home screen</b><button class="btn small" id="a2hsx">Got it</button></div>${HOME_STEPS}</div>`);
  $("#a2hsx").onclick = () => { try { localStorage.setItem("a2hs", "1"); } catch (e) { /* not kept */ } $("#a2hs").remove(); };
}

// ---------- watch the chart: a note highway that plays along with the song ----------
const FRET_COLORS = ["#22c55e", "#ef4444", "#eab308", "#3b82f6", "#f97316"], DRUM_COLORS = ["#ef4444", "#eab308", "#3b82f6", "#22c55e"];
const HW_AHEAD = 2.3;  // seconds of notes on screen
let hw = null;
function closeHighway() {
  if (!hw) return;
  cancelAnimationFrame(hw.raf);
  hw.audios.forEach((a) => { a.pause(); a.removeAttribute("src"); a.load(); });
  document.removeEventListener("keydown", hw.onKey);
  hw.el.remove();
  hw = null;
}
async function openHighway(s) {
  closeHighway(); stopPreview();
  const el = document.createElement("div"); el.className = "hw";
  el.innerHTML = `<div class="hw-top"><div class="grow ellipsis"><b>${esc(s.title || s.name)}</b> <span class="faint">${esc(s.artist || "")}</span></div>
      <div class="tabs" id="hwparts"></div><button class="btn small" id="hwx">Close</button></div>
    <div class="hw-stage"><canvas></canvas><div class="hw-msg"><span class="spinner"></span></div></div>
    <div class="hw-ctl"><button class="btn primary" id="hwplay" disabled>Play</button><span id="hwtime" class="faint">0:00</span>
      <input type="range" id="hwseek" min="0" max="1" step="0.05" value="0" aria-label="Position in the song"><span id="hwlen" class="faint"></span></div>`;
  document.body.append(el);
  const st = hw = { el, audios: [], raf: 0, playing: false, at: 0, t0: 0, part: "", notes: [], frame: 0 };
  const msg = $(".hw-msg", el), cv = $("canvas", el), ctx = cv.getContext("2d");
  $("#hwx", el).onclick = closeHighway;
  let data;
  try { data = await api("/api/chart?" + qs({ folder: s.folder })); }
  catch (e) { msg.innerHTML = `<span class="chip red">${esc(e.message)}</span>`; return; }
  if (hw !== st) return;
  const parts = ["guitar", "bass", "drums"].filter((k) => (data.parts[k] || []).length);
  if (!parts.length) { msg.innerHTML = `<span class="chip">This chart has no guitar, bass or drums part to show.</span>`; return; }
  msg.remove();
  const setPart = (k) => {
    st.part = k; st.notes = data.parts[k];
    $("#hwparts", el).innerHTML = parts.map((p) => `<button class="${p === k ? "on" : ""}" data-part="${p}">${p}</button>`).join("");
    $$("[data-part]", el).forEach((b) => b.onclick = () => setPart(b.dataset.part));
  };
  setPart(parts[0]);
  const len = data.length, seek = $("#hwseek", el), play = $("#hwplay", el);
  seek.max = len; $("#hwlen", el).textContent = mmss(len);
  // every audio file of the song plays together; the first one that loads keeps the time
  (s.stems || []).filter((k) => !/^(preview|crowd)/i.test(k)).forEach((k) => {
    const a = new Audio("/api/audio?" + qs({ folder: s.folder, stem: k }));
    a.preload = "auto";
    a.onerror = () => { st.audios = st.audios.filter((x) => x !== a); };
    st.audios.push(a);
  });
  const now = () => {
    if (!st.playing) return st.at;
    const a = st.audios[0];
    return a ? a.currentTime : st.at + (performance.now() - st.t0) / 1000;
  };
  const pause = () => { st.at = now(); st.playing = false; st.audios.forEach((a) => a.pause()); play.textContent = "Play"; };
  const start = () => {
    if (st.at >= len - 0.2) st.at = 0;
    st.audios.forEach((a) => { try { a.currentTime = st.at; } catch (e) { /* not loaded yet */ } a.play().catch(() => {}); });
    st.t0 = performance.now(); st.playing = true; play.textContent = "Pause";
  };
  const toggle = () => (st.playing ? pause() : start());
  play.disabled = false; play.onclick = toggle;
  let dragging = false;
  seek.oninput = () => { dragging = true; st.at = Number(seek.value); st.t0 = performance.now(); st.audios.forEach((a) => { try { a.currentTime = st.at; } catch (e) { /* not loaded yet */ } }); };
  seek.onchange = () => { dragging = false; };
  st.onKey = (e) => {
    if (e.key === "Escape") closeHighway();
    else if (e.key === " " && !["BUTTON", "INPUT"].includes(e.target.tagName)) { e.preventDefault(); toggle(); }
  };
  document.addEventListener("keydown", st.onKey);

  const first = (t) => { let lo = 0, hi = st.notes.length; while (lo < hi) { const m = (lo + hi) >> 1; st.notes[m][0] < t ? lo = m + 1 : hi = m; } return lo; };
  st.draw = () => {
    const dpr = Math.min(window.devicePixelRatio || 1, 2), w = cv.clientWidth, h = cv.clientHeight;
    if (cv.width !== Math.round(w * dpr) || cv.height !== Math.round(h * dpr)) { cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr); }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    const t = now(), drums = st.part === "drums", colors = drums ? DRUM_COLORS : FRET_COLORS, lanes = colors.length;
    // the board in perspective: full width at the strike line, a third of it at the far end
    const strikeY = h - Math.max(46, h * 0.13), topY = Math.max(8, h * 0.04), far = 1 / 3.2;
    const half = Math.min(w * 0.47, (strikeY - topY) * 0.6), cx = w / 2;
    const sc = (dt) => 1 / (1 + (1 / far - 1) * dt / HW_AHEAD);
    const Y = (dt) => topY + (strikeY - topY) * (sc(dt) - far) / (1 - far);
    const X = (lane, dt) => cx + ((lane + 0.5) / lanes * 2 - 1) * half * sc(dt);
    const edge = (side, dt) => cx + side * half * sc(dt);
    ctx.beginPath(); ctx.moveTo(edge(-1, 0), strikeY); ctx.lineTo(edge(1, 0), strikeY); ctx.lineTo(edge(1, HW_AHEAD), topY); ctx.lineTo(edge(-1, HW_AHEAD), topY); ctx.closePath();
    const g = ctx.createLinearGradient(0, topY, 0, strikeY); g.addColorStop(0, "#0d0f22"); g.addColorStop(1, "#1b1f3f");
    ctx.fillStyle = g; ctx.fill();
    ctx.lineWidth = 1; ctx.strokeStyle = "rgba(255,255,255,.09)";
    for (let i = 0; i <= lanes; i++) {  // lane lines
      const f = i / lanes * 2 - 1;
      ctx.beginPath(); ctx.moveTo(cx + f * half, strikeY); ctx.lineTo(cx + f * half * far, topY); ctx.stroke();
    }
    for (let dt = 0.5 - (t % 0.5); dt < HW_AHEAD; dt += 0.5) {  // lines rolling towards you, so it moves even in a quiet bar
      ctx.beginPath(); ctx.moveTo(edge(-1, dt), Y(dt)); ctx.lineTo(edge(1, dt), Y(dt)); ctx.stroke();
    }
    const laneW = half * 2 / lanes, lit = new Array(lanes).fill(false);
    const shown = [];
    for (let i = first(t - 12); i < st.notes.length && st.notes[i][0] <= t + HW_AHEAD; i++) {
      if (st.notes[i][0] + st.notes[i][2] >= t - 0.12) shown.push(st.notes[i]);
    }
    const bits = (mask) => { const out = []; for (let l = 0; l < lanes; l++) if (mask & (1 << (drums ? l + 1 : l))) out.push(l); return out; };
    const wide = (mask) => (drums ? mask & 1 : mask & 32);  // a kick or an open note: a bar across the board
    for (let i = shown.length - 1; i >= 0; i--) {  // far notes first, so near ones sit on top
      const [nt, mask, hold] = shown[i], dt = nt - t, ls = bits(mask);
      if (hold > 0) {  // the tail of a held note
        const d1 = Math.max(dt, 0), d2 = Math.min(dt + hold, HW_AHEAD);
        if (d2 > d1) ls.forEach((l) => {
          const w1 = laneW * 0.13 * sc(d1), w2 = laneW * 0.13 * sc(d2);
          ctx.beginPath(); ctx.moveTo(X(l, d1) - w1, Y(d1)); ctx.lineTo(X(l, d1) + w1, Y(d1)); ctx.lineTo(X(l, d2) + w2, Y(d2)); ctx.lineTo(X(l, d2) - w2, Y(d2)); ctx.closePath();
          ctx.fillStyle = colors[l]; ctx.globalAlpha = dt <= 0 ? 1 : 0.75; ctx.fill(); ctx.globalAlpha = 1;
          if (dt <= 0) lit[l] = true;
        });
      }
      if (dt < -0.1) continue;
      if (dt <= 0.02) { ls.forEach((l) => { lit[l] = true; }); if (wide(mask)) lit.fill(true); continue; }  // it has reached the line
      ctx.globalAlpha = Math.min(1, (HW_AHEAD - dt) / 0.35);
      const k = sc(dt), y = Y(dt);
      if (wide(mask)) {
        const bh = Math.max(3, laneW * 0.16 * k);
        ctx.fillStyle = drums ? "#f59e0b" : "#a855f7";
        ctx.fillRect(edge(-1, dt), y - bh / 2, edge(1, dt) - edge(-1, dt), bh);
        ctx.fillStyle = "rgba(255,255,255,.55)"; ctx.fillRect(edge(-1, dt), y - bh / 2, edge(1, dt) - edge(-1, dt), Math.max(1, bh * 0.25));
      }
      ls.forEach((l) => {
        const rx = laneW * 0.4 * k, ry = rx * 0.55, x = X(l, dt);
        ctx.beginPath(); ctx.ellipse(x, y, rx, ry, 0, 0, 7); ctx.fillStyle = colors[l]; ctx.fill();
        ctx.lineWidth = Math.max(1, 2 * k); ctx.strokeStyle = "rgba(0,0,0,.55)"; ctx.stroke();
        ctx.beginPath(); ctx.ellipse(x, y - ry * 0.12, rx * 0.5, ry * 0.5, 0, 0, 7); ctx.fillStyle = "rgba(255,255,255,.85)"; ctx.fill();
      });
      ctx.globalAlpha = 1;
    }
    for (let l = 0; l < lanes; l++) {  // the targets on the strike line
      const x = X(l, 0), rx = laneW * 0.4, ry = rx * 0.55;
      ctx.beginPath(); ctx.ellipse(x, strikeY, rx, ry, 0, 0, 7);
      ctx.fillStyle = lit[l] ? colors[l] : "rgba(12,13,26,.9)"; ctx.fill();
      ctx.lineWidth = 3; ctx.strokeStyle = colors[l]; ctx.stroke();
      if (lit[l]) { ctx.beginPath(); ctx.ellipse(x, strikeY, rx * 1.35, ry * 1.35, 0, 0, 7); ctx.lineWidth = 2; ctx.strokeStyle = "rgba(255,255,255,.7)"; ctx.stroke(); }
    }
    $("#hwtime", el).textContent = mmss(t) || "0:00";
    if (!dragging) seek.value = t;
    if (st.playing && t >= len) { pause(); st.at = 0; }
    if (st.playing && ++st.frame % 120 === 0 && st.audios.length > 1) {  // keep the other audio files in step
      const lead = st.audios[0].currentTime;
      st.audios.slice(1).forEach((a) => { if (Math.abs(a.currentTime - lead) > 0.09) a.currentTime = lead; });
    }
  };
  const loop = () => { if (hw !== st) return; st.draw(); st.raf = requestAnimationFrame(loop); };
  loop();
}

// ---------- who's using the app ----------
async function askName(again = false) {
  // someone who has been here before taps their name: the same profile, not a new one
  let known = [];
  if (!again) { try { known = await api("/api/names"); } catch (e) { /* they type it */ } }
  return new Promise((done) => {
    const scrim = document.createElement("div"); scrim.className = "scrim";
    const m = document.createElement("div"); m.className = "modal card";
    m.innerHTML = `<h2 style="margin-top:0">${again ? "Change your name" : "Welcome to RockWizard!"}</h2>
      <p class="muted">${again ? "" : "What's your name? Songs you add get your name on them, so everyone knows who suggested them."}</p>
      ${known.length ? `<div class="faint" style="margin-bottom:6px">Been here before? Tap your name.</div>
        <div class="row wrap" style="gap:8px;margin-bottom:12px">${known.map((p) => `<button class="btn small" data-known="${esc(p.name)}" style="border-color:${p.color}">${esc(p.name)}</button>`).join("")}</div>
        <div class="faint" style="margin-bottom:6px">New here? Type it.</div>` : ""}
      <input id="myname" type="text" maxlength="40" placeholder="Your name" value="${esc(again ? S.me.name : "")}" style="width:100%">
      <div class="row" style="margin-top:14px;justify-content:flex-end">${again ? `<button class="btn" id="nmcancel">Cancel</button>` : ""}
        <button class="btn primary" id="nmok">${again ? "Save" : "Let's go"}</button></div>`;
    document.body.append(scrim, m);
    const inp = $("#myname", m);
    if (!known.length) inp.focus();
    const close = () => { scrim.remove(); m.remove(); done(); };
    $$("[data-known]", m).forEach((b) => b.onclick = () => { inp.value = b.dataset.known; save(); });
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
  loadPeople().then(() => { if (["queue", "staging", "live"].includes(view())) render(); });  // names get their colours
  loadHearts();
  renderNav(); render(); refresh();
  setInterval(refresh, 1500);
  const h = location.hash.slice(1);
  if (h.startsWith("review=")) openReview(decodeURIComponent(h.slice(7)));
})();
