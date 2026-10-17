// wherewolf world: the page after the intro.
// The fork (two paths) -> The Hunt (teach a key item) or The Pack (your key items),
// each up in the sky at the end of its path. The ask bar is on every screen; asking
// flies up to the live view (the room camera + the laser).

import * as api from "../api.js";
import * as sfx from "../sound.js";
import * as fx from "../fx.js";
import * as brand from "../brand.js";
import * as world from "./forest.js";
import * as hunt from "./hunt.js";

const $ = (id) => document.getElementById(id);
const S = { view: "fork", prev: null, hover: null, items: new Map(), taught: [], loaded: false, moving: false, target: null };

brand.mount();
fx.init({ grid: false });
world.init($("world"));
hunt.init({ onDone: () => renderPack() });

/* ---------------- sound ---------------- */

let stopWind = null, howled = false;
function renderMute() {
  $("mute").textContent = sfx.isMuted() ? "Sound off" : "Sound on";
  $("mute").setAttribute("aria-pressed", String(sfx.isMuted()));
}
$("mute").addEventListener("click", () => { sfx.setMuted(!sfx.isMuted()); renderMute(); });
renderMute();
async function wakeAudio() {
  if (!(await sfx.unlock())) return false;
  if (!stopWind) stopWind = sfx.wind();
  return true;
}
document.addEventListener("pointerdown", async () => {
  if (await wakeAudio() && !howled && S.view === "fork") { howled = true; world.howl(); sfx.howl(); }
}, { once: true });

/* ---------------- the fork ---------------- */

const labels = { hunt: $("label-hunt"), pack: $("label-pack") };

let labelHalf = null;   // half of each label's width (measured once, again on resize)
addEventListener("resize", () => { labelHalf = null; });
function placeLabels() {
  if (!labelHalf) labelHalf = { hunt: labels.hunt.offsetWidth / 2 + 12, pack: labels.pack.offsetWidth / 2 + 12 };
  for (const name of ["hunt", "pack"]) {
    const p = world.screenPoint(name), half = labelHalf[name];
    labels[name].style.left = `${Math.min(Math.max(p.x, half), innerWidth - half)}px`;
    labels[name].style.top = `${p.y}px`;
  }
  requestAnimationFrame(placeLabels);
}
requestAnimationFrame(placeLabels);

function setHover(name) {
  if (S.view !== "fork" || S.moving) name = null;
  if (name === S.hover) return;
  S.hover = name;
  world.setHover(name);
  for (const k of ["hunt", "pack"]) labels[k].classList.toggle("on", k === name);
  if (name) { try { sfx.hover(); } catch {} }
}
world.onHover(setHover);
world.onPick((p) => choose(p));
for (const k of ["hunt", "pack"]) {
  labels[k].addEventListener("pointerenter", () => setHover(k));
  labels[k].addEventListener("pointerleave", () => setHover(null));
  labels[k].addEventListener("click", () => choose(k));
}
addEventListener("keydown", (e) => {
  if (e.target.closest("input, textarea") && e.key !== "Escape") return;
  if (S.view === "fork") {
    if (e.key === "ArrowLeft") setHover("hunt");
    else if (e.key === "ArrowRight") setHover("pack");
    else if ((e.key === "Enter" || e.key === " ") && S.hover) { e.preventDefault(); choose(S.hover); }
  } else if (e.key === "Escape") {
    if (S.view === "live") closeLive(); else goHome();
  }
});

async function choose(name) {
  if (S.view !== "fork" || S.moving) return;
  S.moving = true;
  setHover(null);
  await wakeAudio();
  try { sfx.press(); sfx.swoosh("in", 0.6); } catch {}
  $("fork-ui").classList.add("away");
  await world.go(name);
  S.view = name;
  S.moving = false;
  openPanel(name);
}

async function goHome() {
  if (S.view === "fork" || S.moving) return;
  S.moving = true;
  if (S.view === "hunt") hunt.leave();
  await closePanels();
  try { sfx.swoosh("out", 0.6); } catch {}
  await world.home();
  S.view = "fork";
  S.moving = false;
  $("back").hidden = true;
  $("fork-ui").classList.remove("away");
}
$("back").addEventListener("click", goHome);

/* ---------------- panels ---------------- */

function openPanel(name) {
  const el = $(name);
  el.hidden = false;
  el.classList.remove("out"); void el.offsetWidth; el.classList.add("in");
  $("back").hidden = false;
  if (name === "hunt") hunt.enter();
  if (name === "pack") renderPack();
}

function closePanels() {
  const open = ["hunt", "pack", "live"].map($).filter((el) => !el.hidden);
  if (!open.length) return Promise.resolve();
  open.forEach((el) => { el.classList.remove("in"); el.classList.add("out"); });
  return new Promise((r) => setTimeout(() => { open.forEach((el) => { el.hidden = true; el.classList.remove("out"); }); r(); }, fx.reduced ? 0 : 320));
}

// Across the sky from one page to the other.
async function flyTo(name) {
  if (S.moving) return;
  S.moving = true;
  if (S.view === "hunt") hunt.leave();
  await closePanels();
  try { sfx.swoosh("in", 0.5); } catch {}
  await world.fly(name);
  S.view = name;
  S.moving = false;
  openPanel(name);
}
$("hunt-to-pack").addEventListener("click", () => flyTo("pack"));
$("pack-to-hunt").addEventListener("click", () => flyTo("hunt"));

/* ---------------- The Pack ---------------- */

function esc(s) { return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
function relTime(iso) {
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 30) return "seen just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} hr ago`;
  return `${Math.round(s / 86400)} days ago`;
}

function renderPack() {
  const names = [...S.taught].sort();
  $("pack-count").textContent = names.length ? `${names.length} ${names.length === 1 ? "item" : "items"}` : "";
  $("pack-empty").hidden = names.length > 0;
  $("pack-grid").hidden = names.length === 0;
  $("pack-grid").innerHTML = names.map((name, i) => {
    const it = S.items.get(name);
    const photo = (it && it.snapshot) || api.taughtPhoto(name);
    const status = !it ? `<span class="pc-status"><i></i>Not spotted yet</span>`
      : it.visible ? `<span class="pc-status seen"><i></i>In view</span>` : `<span class="pc-status hidden"><i></i>Out of sight</span>`;
    const where = it ? esc(it.where || "somewhere in the room") : "Put it where the camera can see it";
    const when = it ? esc(relTime(it.last_seen)) : "";
    return `<button type="button" class="pack-card" data-name="${esc(name)}" style="--i:${i}">
      <div class="pc-photo">${photo ? `<img src="${esc(photo)}" alt="" onerror="this.remove()">` : ""}<span class="pc-mark" data-brand="mark"></span>${status}</div>
      <div class="pc-body"><span class="pc-name">${esc(name)}</span><span class="pc-where">${where}</span><span class="pc-when mono">${when}</span><span class="pc-find mono">Find it →</span></div>
    </button>`;
  }).join("");
  brand.mount($("pack-grid"));
}
$("pack-grid").addEventListener("click", (e) => {
  const card = e.target.closest(".pack-card");
  if (card) ask(`where is my ${card.dataset.name}`);
});
setInterval(() => { if (S.view === "pack") renderPack(); }, 20000);

api.on("taught", (list) => { S.taught = list || []; if (S.view === "pack") renderPack(); });
api.on("item", (it) => {
  if (it.type === "item_remove") S.items.delete(it.name);
  else S.items.set(it.name, { ...S.items.get(it.name), ...it });
  if (S.view === "pack" && S.taught.includes(it.name)) renderPack();
});

/* ---------------- asking + the live view ---------------- */

async function openLive() {
  if (S.view === "live" || S.moving) return;
  if (S.view === "hunt" && hunt.isTeaching()) return;        // don't interrupt teaching
  S.moving = true;
  S.prev = S.view;
  if (S.view === "hunt") hunt.leave();
  await closePanels();
  $("fork-ui").classList.add("away");
  try { sfx.swoosh("in", 0.5); } catch {}
  await world.fly("live");
  S.view = "live"; S.moving = false;
  $("back").hidden = false;
  openPanel("live");
  setupFeed();
}

async function closeLive() {
  if (S.view !== "live" || S.moving) return;
  S.moving = true;
  await closePanels();
  const prev = S.prev || "fork";
  if (prev === "fork") {
    try { sfx.swoosh("out", 0.6); } catch {}
    S.view = "live";
    S.moving = false;
    await goHomeFromSky();
  } else {
    try { sfx.swoosh("in", 0.5); } catch {}
    await world.fly(prev);
    S.view = prev; S.moving = false;
    openPanel(prev);
  }
}
async function goHomeFromSky() {
  S.moving = true;
  await world.home();
  S.view = "fork"; S.moving = false;
  $("back").hidden = true;
  $("fork-ui").classList.remove("away");
}
$("live-close").addEventListener("click", closeLive);

function ask(text) {
  wakeAudio();
  api.ask(text);
  openLive();
}
$("ask-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const text = $("ask-input").value.trim() || $("ask-input").placeholder;
  $("ask-input").value = "";
  ask(text);
});
$("ask-mic").addEventListener("click", () => { wakeAudio(); api.listen(); });
api.on("voice", (v) => {
  const mic = $("ask-mic");
  mic.dataset.voice = v;
  mic.disabled = v === "off" || v === "loading";
  mic.setAttribute("aria-label", v === "listening" ? "Stop listening" : "Ask out loud (push to talk)");
});

let feedReady = false;
async function setupFeed() {
  if (feedReady) return;
  feedReady = true;
  const url = api.videoUrl();
  if (url) { const img = $("live-feed"); img.src = url; img.hidden = false; $("live-empty").hidden = true; return; }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ video: true, audio: false });
    const v = $("live-video"); v.srcObject = stream; v.hidden = false; $("live-empty").hidden = true;
  } catch { /* "No camera signal" stays */ }
}

// Live status -> the live view (and it opens by itself when someone asks out loud).
const STEP = { QUERYING: 0, TRACKING: 2, REPORTING: 3 };
let lastHead = "";
function headline(html, msg) {
  if (html !== lastHead) { lastHead = html; fx.wordsIn($("live-headline"), html); }
  fx.typeIn($("live-msg"), msg || "");
}
function steps(n) { document.querySelectorAll("#live-steps li").forEach((li, i) => li.classList.toggle("on", i <= n)); }
api.on("state", (m) => {
  const name = m.target ? esc(m.target) : "";
  if (S.loaded && m.state in STEP && S.view !== "live") openLive();
  // Back to idle after an answer: keep the answer on screen until the next question.
  if (m.state === "IDLE" && S.lastState === "REPORTING") { S.lastState = "IDLE"; return; }
  S.lastState = m.state;
  $("live-snap").hidden = true;
  switch (m.state) {
    case "IDLE": headline("Watching the room", "Ask where something is."); steps(-1); break;
    case "QUERYING": { const q = m.message || m.heard; headline("Listening…", q ? `“${q}”` : ""); steps(0); break; }
    case "TRACKING": headline(`Aiming at <em>${name}</em>`, "Steering the laser onto it."); steps(2); try { sfx.laserCharge(); } catch {} break;
    case "REPORTING":
      if (m.result === "found") { headline(`Found <em>${name}</em>`, m.message); steps(3); try { sfx.shing(); } catch {} }
      else if (m.result === "hidden") {
        headline(`<em>${name}</em> is out of sight`, m.message); steps(1);
        const it = S.items.get(m.target);
        if (it && it.snapshot) { $("live-snap").src = it.snapshot; $("live-snap").hidden = false; }
        try { sfx.lowTone(); } catch {}
      } else { headline(/haven't seen|don't know/i.test(m.message || "") ? "Not in memory yet" : "Done", m.message); steps(0); }
      break;
  }
});

// The laser on the live feed: beam from the rig to the target, reticle on it.
const W = 1000, H = 562.5, RIG = { x: 40, y: 562.5 };
function media() {
  const img = $("live-feed"), v = $("live-video");
  if (!img.hidden && img.naturalWidth) return [img.naturalWidth, img.naturalHeight];
  if (!v.hidden && v.videoWidth) return [v.videoWidth, v.videoHeight];
  return [16, 9];
}
function toOverlay([x1, y1, x2, y2]) {
  const [nw, nh] = media(); const s = Math.max(W / nw, H / nh), ox = (W - nw * s) / 2, oy = (H - nh * s) / 2;
  return { x: ox + x1 * nw * s, y: oy + y1 * nh * s, w: (x2 - x1) * nw * s, h: (y2 - y1) * nh * s };
}
api.on("target", ({ name, bbox }) => {
  const beam = $("beam"), ret = $("reticle");
  S.target = name || null;
  if (!bbox) { beam.classList.remove("on"); ret.setAttribute("opacity", "0"); ret.classList.remove("pulse"); $("live-laser").hidden = true; return; }
  const b = toOverlay(bbox), cx = b.x + b.w / 2, cy = b.y + b.h / 2;
  const box = $("ret-box");
  box.setAttribute("x", b.x); box.setAttribute("y", b.y); box.setAttribute("width", b.w); box.setAttribute("height", b.h);
  $("ret-dot").setAttribute("cx", cx); $("ret-dot").setAttribute("cy", cy);
  beam.setAttribute("x1", RIG.x); beam.setAttribute("y1", RIG.y);
  beam.classList.add("on");
  const t0 = performance.now();
  const step = (now) => {
    const t = Math.min(1, (now - t0) / 650), e = 1 - Math.pow(1 - t, 3);
    beam.setAttribute("x2", RIG.x + (cx - RIG.x) * e); beam.setAttribute("y2", RIG.y + (cy - RIG.y) * e);
    if (t < 1) requestAnimationFrame(step); else { ret.setAttribute("opacity", "1"); ret.classList.add("pulse"); }
  };
  requestAnimationFrame(step);
  $("live-laser").hidden = false;
});

/* ---------------- connection ---------------- */

api.on("connection", (mode) => {
  $("conn").dataset.mode = mode;
  $("conn-text").textContent = mode === "live" ? "Connected" : mode === "mock" ? "Demo mode" : mode === "reconnecting" ? "Reconnecting…" : "Connecting…";
});

/* ---------------- arrival from the intro ---------------- */

async function arrive() {
  if (!document.body.classList.contains("arriving")) return;
  try { sessionStorage.removeItem("ctrlf-arrive"); } catch {}
  await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));   // let the scene draw once
  const eye = world.screenPoint("eye");
  const cover = $("arrive");
  const anim = cover.animate(
    [{ clipPath: `circle(150% at ${eye.x}px ${eye.y}px)` }, { clipPath: `circle(0px at ${eye.x}px ${eye.y}px)` }],
    { duration: fx.reduced ? 1 : 1200, easing: "cubic-bezier(0.75, 0, 0.2, 1)", fill: "forwards" });
  setTimeout(async () => {
    try { sfx.laserSnap(); } catch {}
    if (await wakeAudio()) { howled = true; setTimeout(() => { world.howl(); sfx.howl(); }, 500); }
  }, 1000);
  await anim.finished;
  document.body.classList.remove("arriving");
}

(async () => {
  arrive();
  await api.connect();
  setTimeout(() => { S.loaded = true; }, 1200);
})();
