import * as api from "./api.js";
import * as sfx from "./sound.js";
import { initTeach, openTeach } from "./teach.js";
import * as fx from "./fx.js";
import * as brand from "./brand.js";

const $ = (id) => document.getElementById(id);

const state = {
  items: new Map(),   // name -> item
  tracked: [],        // [{name, enabled}]
  target: null,       // name currently targeted
  loaded: false,      // ignore sounds during the initial load
};

/* ---------------- Sound ---------------- */

const muteBtn = $("mute");
function renderMute() {
  const m = sfx.isMuted();
  muteBtn.textContent = m ? "Sound off" : "Sound on";
  muteBtn.setAttribute("aria-pressed", String(m));
}
muteBtn.addEventListener("click", () => { sfx.setMuted(!sfx.isMuted()); sfx.unlock(); renderMute(); });
renderMute();
// If autoplay isn't allowed, the first click anywhere unlocks audio.
document.addEventListener("pointerdown", () => sfx.unlock(), { once: true });
sfx.unlock();

function play(fn) { if (state.loaded) { try { fn(); } catch {} } }

/* ---------------- Connection + feed ---------------- */

api.on("connection", (mode) => {
  const el = $("conn");
  el.dataset.mode = mode;
  $("conn-text").textContent =
    mode === "live" ? "Connected · running offline" :
    mode === "mock" ? "Demo data (backend not connected)" :
    mode === "reconnecting" ? "Reconnecting…" : "Connecting…";
});

async function setupFeed() {
  const url = api.videoUrl();
  if (url) {
    const img = $("feed-img");
    img.src = url;
    img.hidden = false;
    $("feed-empty").hidden = true;
    return;
  }
  // Mock mode: use this laptop's webcam as a stand-in feed if there is one.
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ video: true, audio: false });
    const v = $("feed-video");
    v.srcObject = stream;
    v.hidden = false;
    $("feed-empty").hidden = true;
    $("feed-tag").textContent = "WEBCAM PREVIEW";
  } catch {
    $("feed-tag").textContent = "NO SIGNAL";
  }
}

/* ---------------- Target overlay ---------------- */

// The laser rig sits at the bottom-left of the camera's view in our setup.
const RIG = { x: 40, y: 562.5 };
const W = 1000, H = 562.5; // the overlay's coordinates (16:9, like the feed box)

// The feed fills its box (object-fit: cover), so a camera that isn't 16:9 gets
// cropped. These convert between camera fractions (bbox) and overlay units.
function feedMedia() {
  const img = $("feed-img"), vid = $("feed-video");
  if (!img.hidden && img.naturalWidth) return [img.naturalWidth, img.naturalHeight];
  if (!vid.hidden && vid.videoWidth) return [vid.videoWidth, vid.videoHeight];
  return [16, 9];
}
function toOverlay([x1, y1, x2, y2]) {
  const [nw, nh] = feedMedia();
  const s = Math.max(W / nw, H / nh), ox = (W - nw * s) / 2, oy = (H - nh * s) / 2;
  return { x: ox + x1 * nw * s, y: oy + y1 * nh * s, w: (x2 - x1) * nw * s, h: (y2 - y1) * nh * s };
}
function fromOverlay(x, y) {
  const [nw, nh] = feedMedia();
  const s = Math.max(W / nw, H / nh), ox = (W - nw * s) / 2, oy = (H - nh * s) / 2;
  return [(x - ox) / (nw * s), (y - oy) / (nh * s)];
}

api.on("target", ({ name, bbox }) => {
  const beam = $("beam");
  const ret = $("reticle");
  state.target = name || null;
  renderItems();
  if (!bbox) {
    beam.classList.remove("on");
    ret.setAttribute("opacity", "0");
    ret.classList.remove("pulse");
    $("feed-laser").hidden = true;
    return;
  }
  const b = toOverlay(bbox);
  const box = $("ret-box");
  box.setAttribute("x", b.x);
  box.setAttribute("y", b.y);
  box.setAttribute("width", b.w);
  box.setAttribute("height", b.h);
  const cx = b.x + b.w / 2;
  const cy = b.y + b.h / 2;
  $("ret-dot").setAttribute("cx", cx);
  $("ret-dot").setAttribute("cy", cy);

  // Animate the beam from the rig to the target.
  beam.setAttribute("x1", RIG.x); beam.setAttribute("y1", RIG.y);
  beam.setAttribute("x2", RIG.x); beam.setAttribute("y2", RIG.y);
  beam.classList.add("on");
  const start = performance.now();
  const dur = 650;
  const stepFn = (now) => {
    const t = Math.min(1, (now - start) / dur);
    const e = 1 - Math.pow(1 - t, 3);
    beam.setAttribute("x2", RIG.x + (cx - RIG.x) * e);
    beam.setAttribute("y2", RIG.y + (cy - RIG.y) * e);
    if (t < 1) requestAnimationFrame(stepFn);
    else {
      ret.setAttribute("opacity", "1"); ret.classList.add("pulse");
      // The lock-on sends a ripple through the background from the dot.
      const r = $("feed").getBoundingClientRect();
      fx.pulse(r.left + (cx / W) * r.width, r.top + (cy / H) * r.height);
    }
  };
  requestAnimationFrame(stepFn);
  $("feed-laser").hidden = false;
});

/* ---------------- Status ---------------- */

const STEP_ORDER = ["heard", "found", "aiming", "done"];

function setSteps(upTo, now) {
  const idx = STEP_ORDER.indexOf(upTo);
  document.querySelectorAll("#steps li").forEach((li, i) => {
    li.classList.toggle("on", i <= idx);
    li.classList.toggle("now", STEP_ORDER[i] === now);
  });
}

let lastHeadline = "";
function setHeadline(html, msg) {
  if (html !== lastHeadline) { lastHeadline = html; fx.wordsIn($("status-headline"), html); }
  fx.typeIn($("status-msg"), msg || "");
}

function esc(s) { return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }

api.on("state", (msg) => {
  $("status").dataset.state = msg.state;
  const name = msg.target ? esc(msg.target) : "";
  switch (msg.state) {
    case "IDLE":
      setHeadline("Watching the room", "Ask where something is.");
      setSteps(null, null);
      break;
    case "QUERYING":
      setHeadline("Listening…", msg.heard ? `“${msg.heard}”` : "");
      setSteps("heard", "heard");
      play(() => sfx.tick(0.8));
      break;
    case "TRACKING":
      setHeadline(`Aiming at <em>${name}</em>`, "Steering the laser onto it.");
      setSteps("aiming", "aiming");
      play(() => sfx.laserCharge());
      break;
    case "REPORTING":
      if (msg.result === "found") {
        setHeadline(`Found <em>${name}</em>`, msg.message);
        setSteps("done", "done");
        play(() => sfx.shing());
      } else if (msg.result === "hidden") {
        setHeadline(`<em>${name.charAt(0).toUpperCase() + name.slice(1)}</em> ${name.endsWith("s") ? "are" : "is"} out of sight`, msg.message);
        setSteps("found", "found");
        play(() => sfx.lowTone());
        flashItem(msg.target);
      } else if (msg.result === "info") {
        // The app answered something that isn't a found/hidden object.
        const unknown = /haven't seen|don't know/i.test(msg.message || "");
        setHeadline(unknown ? "Not in memory yet" : "Done", msg.message);
        setSteps("heard", null);
        if (unknown) play(() => sfx.lowTone());
      } else {
        setHeadline("Not in memory yet", msg.message || "Try adding it under Tracking.");
        setSteps("heard", null);
        play(() => sfx.lowTone());
      }
      break;
    case "TEACHING":
      setHeadline(name ? `Learning <em>${name}</em>` : "Learning something new", msg.message);
      setSteps(null, null);
      break;
    case "ERROR":
      setHeadline("Something went wrong", msg.message || "Check the backend.");
      setSteps(null, null);
      break;
  }
});

setInterval(() => {
  $("status-clock").textContent = new Date().toLocaleTimeString([], { hour: "numeric", minute: "2-digit", second: "2-digit" });
}, 1000);

/* ---------------- Last seen ---------------- */

function relTime(iso) {
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 30) return "seen now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} hr ago`;
  return `${Math.round(s / 86400)} days ago`;
}

function absTime(iso) {
  const d = new Date(iso);
  return d.toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

api.on("item", (it) => {
  if (it.type === "item_remove") { state.items.delete(it.name); renderItems(); return; }
  const prev = state.items.get(it.name);
  state.items.set(it.name, { ...prev, ...it });
  if (state.loaded && prev && prev.visible !== it.visible) {
    play(() => (it.visible ? sfx.chime() : sfx.lowTone()));
    flashItem(it.name);
  }
  renderItems();
});

function isTracked(name) {
  const t = state.tracked.find((x) => x.name === name);
  return !t || t.enabled; // unknown items still show
}

function renderItems() {
  const list = $("items");
  const items = [...state.items.values()].filter((it) => isTracked(it.name));
  // Target first, then out-of-sight (most useful), then in view.
  items.sort((a, b) => {
    const rank = (x) => (x.name === state.target ? 0 : x.visible ? 2 : 1);
    return rank(a) - rank(b) || new Date(b.last_seen) - new Date(a.last_seen);
  });
  $("lastseen-count").textContent = `${items.length} items`;
  if (!items.length) {
    list.innerHTML = `<li class="empty">Nothing seen yet. Set something on the desk.</li>`;
    return;
  }
  list.innerHTML = items.map((it) => {
    const isTarget = it.name === state.target;
    const badge = isTarget
      ? `<span class="badge target">Target</span>`
      : it.visible ? `<span class="badge seen">In view</span>` : `<span class="badge hidden">Out of sight</span>`;
    const thumb = it.snapshot
      ? `<img src="${esc(it.snapshot)}" alt="Last photo of ${esc(it.name)}">`
      : esc(it.name.charAt(0).toUpperCase());
    const when = it.visible ? relTime(it.last_seen) : `${relTime(it.last_seen)} · ${absTime(it.last_seen)}`;
    return `<li class="item fx-spot${isTarget ? " target" : ""}" data-name="${esc(it.name)}">
      <div class="thumb">${thumb}</div>
      <div class="item-body">
        <div class="item-top"><span class="item-name">${esc(it.name)}</span>${badge}</div>
        <div class="item-where">${esc(it.where || "location unknown")}</div>
        <div class="item-when mono">${esc(when)}</div>
      </div>
    </li>`;
  }).join("");
}
setInterval(renderItems, 15000);

function flashItem(name) {
  const el = document.querySelector(`.item[data-name="${CSS.escape(name)}"]`);
  if (!el) return;
  el.classList.remove("flash");
  void el.offsetWidth;
  el.classList.add("flash");
}

/* ---------------- Tracking ---------------- */

const SUGGESTIONS = ["phone", "charger", "glasses", "remote", "notebook", "headphones", "pill bottle"];

api.on("tracked", (items) => {
  state.tracked = items.map((i) => ({ name: i.name.toLowerCase(), enabled: i.enabled !== false }));
  renderChips();
  renderItems();
});

function renderChips() {
  const chips = $("chips");
  chips.innerHTML = state.tracked.map((t, i) =>
    `<button type="button" class="chip fx-brackets" data-i="${i}" aria-pressed="${t.enabled}">${esc(t.name)}</button>`
  ).join("");
  const on = state.tracked.filter((t) => t.enabled).length;
  $("tracking-count").textContent = `${on} of ${state.tracked.length} on`;

  const have = new Set(state.tracked.map((t) => t.name));
  // Live: the app already knows its list; new things are added by teaching.
  const left = api.mode === "live" ? [] : SUGGESTIONS.filter((s) => !have.has(s)).slice(0, 5);
  $("suggest").innerHTML = left.length
    ? `<span>Quick add:</span>` + left.map((s) => `<button type="button" class="fx-brackets" data-add="${esc(s)}">+ ${esc(s)}</button>`).join("")
    : "";
}

$("chips").addEventListener("click", (e) => {
  const btn = e.target.closest(".chip");
  if (!btn) return;
  const i = Number(btn.dataset.i);
  const next = state.tracked.map((t, j) => (j === i ? { ...t, enabled: !t.enabled } : t));
  try { sfx.toggle(next[i].enabled); } catch {}
  api.setTracked(next);
});

function addTracked(name) {
  const clean = name.trim().toLowerCase();
  if (!clean) return;
  if (api.mode === "live") {
    // Something it doesn't know yet: teach it, starting with the name.
    const known = state.tracked.find((t) => t.name === clean);
    if (known) {
      if (!known.enabled) api.setTracked(state.tracked.map((t) => (t.name === clean ? { ...t, enabled: true } : t)));
      return;
    }
    openTeach(clean);
    return;
  }
  if (state.tracked.some((t) => t.name === clean)) return;
  api.setTracked([...state.tracked, { name: clean, enabled: true }]);
  play(() => sfx.tick(1.2));
}

$("track-form").addEventListener("submit", (e) => {
  e.preventDefault();
  addTracked($("track-input").value);
  $("track-input").value = "";
});

$("suggest").addEventListener("click", (e) => {
  const b = e.target.closest("[data-add]");
  if (b) addTracked(b.dataset.add);
});

/* ---------------- Ask ---------------- */

$("ask-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const text = $("ask-input").value.trim() || $("ask-input").placeholder;
  sfx.unlock();
  api.ask(text);
  $("ask-input").value = "";
});

$("ask-mic").addEventListener("click", () => { sfx.unlock(); api.listen(); });

api.on("voice", (v) => {
  const mic = $("ask-mic");
  mic.dataset.voice = v;
  mic.setAttribute("aria-label", v === "listening" ? "Stop listening" : "Ask out loud (push to talk)");
  mic.disabled = v === "off" || v === "loading";
});

/* ---------------- Activity log ---------------- */

api.on("log", ({ time, text }) => {
  const li = document.createElement("li");
  li.innerHTML = `<time>${time.toLocaleTimeString([], { hour: "numeric", minute: "2-digit", second: "2-digit" })}</time><span></span>`;
  $("log").prepend(li);
  const span = li.querySelector("span");
  if (state.loaded) { fx.scramble(span, text, Math.min(500, 150 + text.length * 8)); play(() => sfx.decode()); }
  else span.textContent = text;
  while ($("log").children.length > 8) $("log").lastChild.remove();
});

/* ---------------- Arrival from the intro ---------------- */

// The intro ends by diving into the red period of "wherewolf." Here that red
// collapses back into the period in the logo, it pings, and the panels rise in.
async function arrive() {
  if (!document.body.classList.contains("arriving")) return;
  try { sessionStorage.removeItem("ctrlf-arrive"); } catch {}
  await document.fonts.ready;

  const order = [".topbar", ".feed", ".ask", ".status", ".lastseen", ".tracking", ".activity"];
  order.forEach((sel, i) => document.querySelector(sel)?.style.setProperty("--d", `${0.55 + i * 0.07}s`));

  const dotEl = document.querySelector(".brand .ww-dot");
  const dot = dotEl.getBoundingClientRect();
  const x = dot.left + dot.width / 2;
  const y = dot.top + dot.height / 2;
  const cover = $("arrive");
  const anim = cover.animate(
    [
      { clipPath: `circle(150% at ${x}px ${y}px)` },
      { clipPath: `circle(0px at ${x}px ${y}px)` },
    ],
    { duration: 1100, easing: "cubic-bezier(0.75, 0, 0.2, 1)", fill: "forwards" }
  );
  setTimeout(() => {
    try { sfx.laserSnap(); } catch {}
    dotEl.classList.add("ping");
    fx.pulse(x, y);
  }, 950);
  document.body.classList.add("arrived");
  await anim.finished;
  document.body.classList.remove("arriving");
}

/* ---------------- Feed: crosshair, hover an object, click to find it ---------------- */

const hoverBox = () => $("hover-box");
function showHoverBox(bbox, ghost) {
  const b = toOverlay(bbox);
  const r = hoverBox();
  r.setAttribute("x", b.x); r.setAttribute("y", b.y);
  r.setAttribute("width", b.w); r.setAttribute("height", b.h);
  r.classList.add("on");
  r.classList.toggle("ghost", !!ghost);
  return b;
}
function hideHoverBox() { hoverBox().classList.remove("on", "ghost"); }

function initFeedHud() {
  const feed = $("feed"), hud = $("feed-hud"), read = $("hud-read"), pick = $("feed-pick");
  let picked = null;
  feed.addEventListener("pointermove", (e) => {
    const r = feed.getBoundingClientRect();
    const px = e.clientX - r.left, py = e.clientY - r.top;
    hud.style.setProperty("--hx", `${px}px`);
    hud.style.setProperty("--hy", `${py}px`);
    const [fx_, fy] = fromOverlay((px / r.width) * W, (py / r.height) * H);
    read.textContent = `X ${fx_.toFixed(3)}  Y ${fy.toFixed(3)}`;
    // Is the cursor on something it can see right now? Smallest box wins.
    const hits = [...state.items.values()].filter((it) => it.visible && it.bbox && isTracked(it.name)
      && fx_ >= it.bbox[0] && fx_ <= it.bbox[2] && fy >= it.bbox[1] && fy <= it.bbox[3])
      .sort((a, b) => (a.bbox[2] - a.bbox[0]) * (a.bbox[3] - a.bbox[1]) - (b.bbox[2] - b.bbox[0]) * (b.bbox[3] - b.bbox[1]));
    const hit = hits[0] || null;
    if (hit?.name === picked) return;
    picked = hit?.name || null;
    feed.classList.toggle("picking", !!hit);
    feed.classList.toggle("fx-click", !!hit);
    if (!hit) { hideHoverBox(); pick.hidden = true; return; }
    const b = showHoverBox(hit.bbox);
    pick.innerHTML = `<b>${esc(hit.name)}</b> · click to find`;
    pick.style.left = `${(b.x / W) * 100}%`;
    pick.style.top = `${(b.y / H) * 100}%`;
    pick.hidden = false;
    try { sfx.hover(); } catch {}
  });
  feed.addEventListener("pointerleave", () => { picked = null; hideHoverBox(); pick.hidden = true; feed.classList.remove("picking", "fx-click"); });
  feed.addEventListener("click", () => {
    if (!picked) return;
    sfx.unlock();
    api.ask(`where is my ${picked}`);
  });
}

/* ---------------- Last seen: hover a row to see it on the feed ---------------- */

function initPeek() {
  const list = $("items"), peek = $("peek");
  let current = null;
  list.addEventListener("mouseover", (e) => {
    const li = e.target.closest(".item");
    if (!li || li.dataset.name === current) return;
    current = li.dataset.name;
    const it = state.items.get(current);
    if (!it) return;
    if (it.bbox) showHoverBox(it.bbox, !it.visible);
    if (it.snapshot) {
      $("peek-img").src = it.snapshot;
      $("peek-tag").textContent = it.visible ? `${it.name} · in view` : `${it.name} · last seen ${relTime(it.last_seen)}`;
      const r = li.getBoundingClientRect();
      peek.style.left = `${Math.max(12, r.left - 332)}px`;
      peek.style.top = `${Math.min(innerHeight - 214, Math.max(12, r.top + r.height / 2 - 100))}px`;
      peek.classList.add("show");
    } else peek.classList.remove("show");
  });
  list.addEventListener("mouseout", (e) => {
    if (e.relatedTarget && e.relatedTarget.closest?.(".item")) return;
    current = null;
    hideHoverBox();
    peek.classList.remove("show");
  });
}

/* ---------------- Start ---------------- */

brand.mount();
fx.init();
fx.dialogFx($("teach"));
initFeedHud();
initPeek();

(async () => {
  initTeach();
  arrive();
  await api.connect();
  await setupFeed();
  setTimeout(() => { state.loaded = true; }, 800);
})();
