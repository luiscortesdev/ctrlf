import * as api from "./api.js";
import * as sfx from "./sound.js";
import { initTeach, openTeach } from "./teach.js";

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
  const [x1, y1, x2, y2] = bbox;
  const W = 1000, H = 562.5;
  const box = $("ret-box");
  box.setAttribute("x", x1 * W);
  box.setAttribute("y", y1 * H);
  box.setAttribute("width", (x2 - x1) * W);
  box.setAttribute("height", (y2 - y1) * H);
  const cx = ((x1 + x2) / 2) * W;
  const cy = ((y1 + y2) / 2) * H;
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
    else { ret.setAttribute("opacity", "1"); ret.classList.add("pulse"); }
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

function setHeadline(html, msg) {
  $("status-headline").innerHTML = html;
  $("status-msg").textContent = msg || "";
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
        play(() => sfx.chime());
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
    return `<li class="item${isTarget ? " target" : ""}" data-name="${esc(it.name)}">
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
    `<button type="button" class="chip" data-i="${i}" aria-pressed="${t.enabled}">${esc(t.name)}</button>`
  ).join("");
  const on = state.tracked.filter((t) => t.enabled).length;
  $("tracking-count").textContent = `${on} of ${state.tracked.length} on`;

  const have = new Set(state.tracked.map((t) => t.name));
  // Live: the app already knows its list; new things are added by teaching.
  const left = api.mode === "live" ? [] : SUGGESTIONS.filter((s) => !have.has(s)).slice(0, 5);
  $("suggest").innerHTML = left.length
    ? `<span>Quick add:</span>` + left.map((s) => `<button type="button" data-add="${esc(s)}">+ ${esc(s)}</button>`).join("")
    : "";
}

$("chips").addEventListener("click", (e) => {
  const btn = e.target.closest(".chip");
  if (!btn) return;
  const next = state.tracked.map((t, i) => (i === Number(btn.dataset.i) ? { ...t, enabled: !t.enabled } : t));
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
  li.innerHTML = `<time>${time.toLocaleTimeString([], { hour: "numeric", minute: "2-digit", second: "2-digit" })}</time><span>${esc(text)}</span>`;
  $("log").prepend(li);
  while ($("log").children.length > 8) $("log").lastChild.remove();
});

/* ---------------- Arrival from the intro ---------------- */

// The intro ends on a screen of white. Here that white collapses into the
// red laser dot in the logo, the dot pings, and the panels rise in.
async function arrive() {
  if (!document.body.classList.contains("arriving")) return;
  try { sessionStorage.removeItem("ctrlf-arrive"); } catch {}
  await document.fonts.ready;

  const order = [".topbar", ".feed", ".ask", ".status", ".lastseen", ".tracking", ".activity"];
  order.forEach((sel, i) => document.querySelector(sel)?.style.setProperty("--d", `${0.55 + i * 0.07}s`));

  const dot = document.querySelector(".brand-mark").getBoundingClientRect();
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
    document.querySelector(".brand-mark").classList.add("ping");
  }, 950);
  document.body.classList.add("arrived");
  await anim.finished;
  document.body.classList.remove("arriving");
}

/* ---------------- Start ---------------- */

(async () => {
  initTeach();
  arrive();
  await api.connect();
  await setupFeed();
  setTimeout(() => { state.loaded = true; }, 800);
})();
