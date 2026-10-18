// wherewolf app page: one calm screen over the night forest.
//   Home: "What are you looking for?" (type or ask out loud) + your key items (find / remove)
//   Add:  name it -> show it -> place it -> done   (add.js)
//   Find: the answer in one sentence, the room camera with the laser on it
// The forest (forest.js) is only the background now: it drifts slowly, nothing to navigate.

import * as api from "../api.js";
import * as sfx from "../sound.js";
import * as fx from "../fx.js";
import * as brand from "../brand.js";
import * as world from "./forest.js";
import * as add from "./add.js";

const $ = (id) => document.getElementById(id);
let F;
const S = { view: "home", items: new Map(), taught: [], loaded: false, removing: null, lastState: null, asked: false, laserOk: true };   // laserOk: live mode says if the ESP32 is there

brand.mount();
fx.init({ grid: false, cursor: false, sounds: false });   // normal cursor, no hover/click sounds
world.init($("world"));
world.drift(true);
add.init({ onDone: () => renderItems() });

/* ---------------- sound (only the important moments: found, added, removed, not found) ---------------- */

function renderMute() {
  const off = sfx.isMuted();
  $("mute").textContent = off ? "Sound: Off" : "Sound: On";
  $("mute").setAttribute("aria-pressed", String(off));
}
$("mute").addEventListener("click", () => { sfx.setMuted(!sfx.isMuted()); renderMute(); });
renderMute();
document.addEventListener("pointerdown", () => sfx.unlock(), { once: true });

/* ---------------- views ---------------- */

function show(view, { focus = true } = {}) {
  if (S.view === "add" && view !== "add") add.leave();
  S.view = view;
  for (const v of ["home", "add", "find"]) $(v).hidden = v !== view;
  // replay the fade-in
  const el = $(view); el.style.animation = "none"; void el.offsetWidth; el.style.animation = "";
  window.scrollTo({ top: 0, behavior: fx.reduced ? "auto" : "smooth" });
  if (view === "home") { S.removing = null; renderItems(true); }
  if (view === "add") add.enter();
  if (focus) {
    const target = view === "home" ? $("ask-input") : view === "find" ? $("find-h") : null;
    if (target) { if (target.tagName === "H1") target.setAttribute("tabindex", "-1"); target.focus({ preventScroll: true }); }
  }
}
document.querySelectorAll("[data-home]").forEach((b) => b.addEventListener("click", () => show("home")));
$("to-add").addEventListener("click", () => show("add"));
$("empty-add").addEventListener("click", () => show("add"));
$("add-to-items").addEventListener("click", () => show("home"));
addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  if (S.removing) { confirmRemove(null); return; }
  if (S.view !== "home") show("home");
});

/* ---------------- your key items ---------------- */

function esc(s) { return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
function relTime(iso) {
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return "Seen just now";
  if (s < 3600) { const m = Math.round(s / 60); return `Seen ${m} minute${m === 1 ? "" : "s"} ago`; }
  if (s < 86400) { const h = Math.round(s / 3600); return `Seen ${h} hour${h === 1 ? "" : "s"} ago`; }
  const d = Math.round(s / 86400); return `Seen ${d} day${d === 1 ? "" : "s"} ago`;
}
const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);

let lastHtml = "";
function renderItems(animate = false) {
  const names = [...S.taught].sort();
  $("items-count").textContent = names.length ? `(${names.length})` : "";
  $("items-empty").hidden = names.length > 0;
  $("items-grid").hidden = names.length === 0;
  $("to-add").hidden = names.length === 0;          // the empty state has its own big button
  const html = names.map((name, i) => {
    const it = S.items.get(name), n = esc(name);
    const photo = (it && it.snapshot) || api.taughtPhoto(name);
    const status = !it ? `<span class="item-status"><i></i>Not seen yet</span>`
      : it.visible ? `<span class="item-status seen"><i></i>In view now</span>` : `<span class="item-status hidden"><i></i>Out of sight</span>`;
    const where = it ? esc(cap(it.where || "somewhere in the room")) : "Put it where the room camera can see it.";
    const when = it ? esc(relTime(it.last_seen)) : "";
    return `<article class="item" data-name="${n}" style="--i:${i}" aria-label="${n}">
      <div class="item-photo">${photo ? `<img src="${esc(photo)}" alt="" onerror="this.remove()">` : ""}<span class="item-mark" data-brand="mark"></span>${status}</div>
      <div class="item-body">
        <span class="item-name">${n}</span>
        <span class="item-where">${where}</span>
        <span class="item-when">${when}</span>
        <div class="item-actions">
          <button type="button" class="btn btn-primary" data-act="find">Find</button>
          <button type="button" class="btn btn-secondary" data-act="remove">Remove</button>
        </div>
      </div>
      ${S.removing === name ? confirmHtml(n) : ""}
    </article>`;
  }).join("");
  // The app sends updates several times a second; only touch the page when something shown changes.
  // The fade-in class stays out of the HTML, so the next update doesn't rebuild the cards and cut it short.
  if (html === lastHtml && !animate) return;
  lastHtml = html;
  $("items-grid").innerHTML = html;
  if (animate) $("items-grid").querySelectorAll(".item").forEach((el) => el.classList.add("enter"));
  brand.mount($("items-grid"));
}
const confirmHtml = (n) => `<div class="item-confirm" role="alertdialog" aria-label="Remove ${n}?">
  <p>Remove <span class="cap">${n}</span>?</p>
  <p class="hint">wherewolf will forget what it looks like and where it is.</p>
  <div class="row"><button type="button" class="btn btn-primary" data-act="remove-yes">Yes, remove</button><button type="button" class="btn btn-secondary" data-act="remove-no">Cancel</button></div>
</div>`;

function confirmRemove(name) {
  S.removing = name;
  renderItems();
  if (name) $("items-grid").querySelector("[data-act=remove-no]")?.focus();
}

$("items-grid").addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-act]"), card = e.target.closest(".item");
  if (!btn || !card) return;
  const name = card.dataset.name;
  switch (btn.dataset.act) {
    case "find": ask(`where is my ${name}`); break;
    case "remove": confirmRemove(name); break;
    case "remove-no": confirmRemove(null); $("items-grid").querySelector(`.item[data-name="${CSS.escape(name)}"] [data-act=remove]`)?.focus(); break;
    case "remove-yes": {
      S.removing = null;
      card.classList.add("gone");
      try { await api.forget(name); } catch { card.classList.remove("gone"); renderItems(); return; }
      try { sfx.remove(); } catch {}
      setTimeout(() => { S.taught = S.taught.filter((n) => n !== name); renderItems(); $("ask-input").focus(); }, fx.reduced ? 0 : 300);
      break;
    }
  }
});
setInterval(() => { if (S.view === "home") renderItems(); }, 20000);

api.on("taught", (list) => { S.taught = list || []; if (S.view === "home") renderItems(); });
api.on("item", (it) => {
  if (it.type === "item_remove") S.items.delete(it.name);
  else S.items.set(it.name, { ...S.items.get(it.name), ...it });
  if (S.view === "home" && S.taught.includes(it.name)) renderItems();
});

/* ---------------- asking ---------------- */

function ask(text) {
  sfx.unlock();
  S.asked = true;
  resetFind(text);
  api.ask(text);
  show("find");
}
$("ask-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const text = $("ask-input").value.trim();
  if (!text) { $("ask-hint").textContent = "Please type what you are looking for first, for example \"keys\"."; $("ask-input").focus(); return; }
  $("ask-input").value = "";
  // "keys" works as well as "where are my keys?"
  ask(/\b(where|find|lost)\b/i.test(text) ? text : `where is my ${text}`);
});
$("ask-mic").addEventListener("click", () => { sfx.unlock(); S.asked = true; api.listen(); });
api.on("voice", (v) => {
  const mic = $("ask-mic");
  mic.dataset.voice = v;
  mic.hidden = v === "off";
  mic.disabled = v === "loading";
  $("ask-mic-text").textContent = v === "listening" ? "Listening… press to stop" : v === "loading" ? "Getting ready…" : "Ask out loud";
});

/* ---------------- the find screen: what the laser is doing + the item's details ---------------- */

const LASER = {
  init:   ["Initializing laser…", "Warming up"],
  search: ["Laser ready. Looking…", "Ready"],
  aim:    ["Pointing the laser…", "Moving onto it"],
  on:     ["Laser is on it", "Pointing at it now"],
  hidden: ["Laser standing by", "Off (it can't see it)"],
  done:   ["Laser finished", "Off (done pointing)"],
  none:   ["Laser standing by", "Off"],
  off:    ["Laser not connected", "Not connected"],
};
F = { name: null, timer: null, laser: null };
function laser(phase) {
  F.laser = phase;
  if (!S.laserOk) phase = "off";   // never claim it's pointing when the ESP32 isn't there
  $("laser-status").dataset.phase = phase;
  $("laser-label").textContent = LASER[phase][0];
  $("stat-laser").textContent = LASER[phase][1];
}
function clock(iso) { return new Date(iso).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }); }

// Fill in the item's details from what the app remembers about it.
function itemStats(name) {
  if (name) F.name = name;
  const n = F.name, it = n ? S.items.get(n) : null;
  $("find-item").hidden = !n;
  if (!n) return;
  $("find-name").textContent = n;
  const photo = (it && it.snapshot) || api.taughtPhoto(n);
  const img = $("find-photo-img");
  img.onerror = () => { img.hidden = true; };
  if (!photo) img.hidden = true;
  else if (img.getAttribute("src") !== photo) { img.hidden = false; img.src = photo; }
  const st = $("find-status");
  if (!it) {
    st.className = "item-status"; st.innerHTML = "<i></i>Not seen yet";
    $("stat-where").textContent = "It hasn't been seen yet";
    $("stat-seen").textContent = "Never";
    $("stat-now").textContent = "Not in view";
    return;
  }
  st.className = `item-status ${it.visible ? "seen" : "hidden"}`;
  st.innerHTML = `<i></i>${it.visible ? "In view now" : "Out of sight"}`;
  $("stat-where").textContent = cap(it.where || "Somewhere in the room");
  $("stat-seen").textContent = it.last_seen ? `${clock(it.last_seen)} (${relTime(it.last_seen).replace(/^Seen /, "")})` : "Unknown";
  $("stat-now").textContent = it.visible ? "In view of the camera" : "Not in view of the camera";
}
// Which item a question is about: one of the key items (or anything the app knows) named in it.
function itemIn(text) {
  const t = text.toLowerCase();
  const names = [...new Set([...S.taught, ...S.items.keys()])].sort((a, b) => b.length - a.length);
  return names.find((n) => t.includes(n)) || null;
}

function resetFind(text) {
  F.name = null;
  headline("Looking…", text ? `You asked: “${text}”` : "");
  laser("init");
  steps(0);
  itemStats(itemIn(text || ""));
  // The laser takes a moment to wake up; show it as ready shortly after, unless an answer came first.
  clearTimeout(F.timer);
  F.timer = setTimeout(() => { if (F.laser === "init") { laser("search"); steps(1); } }, 900);
}
function headline(html, msg) {
  $("find-headline").innerHTML = html;
  $("find-msg").textContent = msg || "";
}
// Steps 1 ("Laser ready") and 3 ("Pointing at it") stay unticked with no laser connected.
function steps(n) { document.querySelectorAll("#find-steps li").forEach((li, i) => li.classList.toggle("on", i <= n && (S.laserOk || i % 2 === 0))); }
setInterval(() => { if (S.view === "find") itemStats(); }, 15000);
api.on("item", (it) => { if (S.view === "find" && it.name === F.name) itemStats(); });

// Say the answer out loud in demo mode (with the real app, the app's own speaker says it).
function speak(text) {
  if (api.mode === "live" || sfx.isMuted() || !text || !("speechSynthesis" in window)) return;
  try { speechSynthesis.cancel(); const u = new SpeechSynthesisUtterance(text); u.rate = 0.95; speechSynthesis.speak(u); } catch {}
}

const ASKING = new Set(["QUERYING", "TRACKING", "REPORTING"]);
api.on("state", (m) => {
  const name = m.target ? esc(m.target) : "";
  // Asked out loud (or from the app's keyboard): show the find screen by itself.
  if (S.loaded && ASKING.has(m.state) && S.view !== "find" && !add.isTeaching()) { resetFind(m.message || ""); show("find", { focus: false }); }
  // Back to idle after an answer: keep the answer on screen.
  if (m.state === "IDLE" && S.lastState === "REPORTING") { S.lastState = "IDLE"; if (F.laser === "on") laser("done"); return; }
  S.lastState = m.state;
  if (m.target) itemStats(m.target);
  switch (m.state) {
    case "IDLE": if (!S.asked) { headline("Ready", "Ask where something is."); laser("none"); steps(-1); } break;
    case "QUERYING": resetFind(m.message || m.heard); break;   // a new question: forget the last item
    case "TRACKING": clearTimeout(F.timer); headline(`Found your <em>${name}</em>`, S.laserOk ? "Pointing the laser at it now…" : ""); laser("aim"); steps(2); break;
    case "REPORTING":
      clearTimeout(F.timer);
      if (m.result === "found") {
        headline(`Found your <em>${name}</em>`, m.message); laser("on"); steps(3);
        try { sfx.shing(); } catch {} speak(m.message);
      } else if (m.result === "hidden") {
        headline(`Your <em>${name}</em> is out of sight`, m.message); laser("hidden"); steps(1);
        try { sfx.lowTone(); } catch {} speak(m.message);
      } else {
        const unknown = /haven't seen|don't know|wasn't taught|not.*taught/i.test(m.message || "");
        headline(unknown ? "Not found" : "Done", m.message); laser("none"); steps(unknown ? 0 : 1);
        if (unknown) { try { sfx.lowTone(); } catch {} }
        speak(m.message);
      }
      break;
  }
});
api.on("target", ({ bbox }) => { if (bbox && S.view === "find") laser("on"); });
api.on("laser", (ok) => { S.laserOk = ok; if (F.laser) laser(F.laser); });

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
  const p = world.screenPoint();
  const anim = $("arrive").animate(
    [{ clipPath: `circle(150% at ${p.x}px ${p.y}px)` }, { clipPath: `circle(0px at ${p.x}px ${p.y}px)` }],
    { duration: fx.reduced ? 1 : 1100, easing: "cubic-bezier(0.75, 0, 0.2, 1)", fill: "forwards" });
  await anim.finished;
  document.body.classList.remove("arriving");
}

(async () => {
  show("home", { focus: false });
  arrive();
  await api.connect();
  setTimeout(() => { S.loaded = true; }, 1200);
})();
