// The Hunt: teach wherewolf a new key item.
// Name it -> show it (laptop camera) -> place it (room camera, "is this it?") -> done.
// Live: the app does the real teaching ("this is my <name>"), and this page follows its
// phases (state.teach) and sends answers back (yes / no / a click / cancel).
// Demo mode (no app): the browser's webcam takes a few photos instead.

import * as api from "../api.js";
import * as sfx from "../sound.js";
import * as fx from "../fx.js";

const $ = (id) => document.getElementById(id);
const STEPS = ["name", "show", "place", "done"];
const SHOTS = 6, SHOT_GAP = 420, CROP = 320;

const h = { name: "", active: false, phase: null, laptop: false, taught: [], shots: [], stream: null, cancelled: false, onDone: null };

export function init({ onDone }) {
  h.onDone = onDone;
  $("hunt-name-form").addEventListener("submit", (e) => { e.preventDefault(); start($("hunt-name").value); });
  $("hunt-name").addEventListener("input", () => { $("hunt-error").textContent = ""; });
  $("hunt-cancel").addEventListener("click", cancel);
  $("hunt-yes").addEventListener("click", () => answer(true));
  $("hunt-no").addEventListener("click", () => answer(false));
  $("hunt-capture").addEventListener("click", captureMock);
  $("hunt-again").addEventListener("click", () => enter());
  $("hunt-feed").addEventListener("click", clickFeed);
  api.on("teach", onTeach);
  api.on("taught", (list) => { h.taught = list; });
}

export function enter() {
  stopWebcam();
  h.name = ""; h.active = false; h.phase = null; h.shots = []; h.cancelled = false;
  $("hunt-name").value = "";
  $("hunt-error").textContent = "";
  show("name");
  setTimeout(() => $("hunt-name").focus(), 400);
}

export function leave() {
  if (h.active && api.mode === "live") api.command("cancel").catch(() => {});
  h.active = false;
  stopWebcam();
  feed(null);
}

export const isTeaching = () => h.active;

/* ---------------- steps ---------------- */

function show(step) {
  $("hunt-name-form").hidden = step !== "name";
  $("hunt-live").hidden = !(step === "show" || step === "place");
  $("hunt-done").hidden = step !== "done";
  const i = STEPS.indexOf(step);
  document.querySelectorAll("#hunt-steps li").forEach((li, k) => {
    li.classList.toggle("on", k <= i);
    li.classList.toggle("now", k === i);
  });
}

function say(text, tip = "") {
  fx.typeIn($("hunt-say"), text);
  $("hunt-tip").textContent = tip;
}

function feed(kind) {
  // kind: "laptop" | "room" | null
  const img = $("hunt-feed"), cam = $("hunt-cam");
  const src = kind === "laptop" ? api.teachVideoUrl() : kind === "room" ? api.videoUrl() : null;
  if (!src) { img.removeAttribute("src"); img.dataset.src = ""; }
  else if (img.dataset.src !== src) { img.dataset.src = src; img.src = src; }
  img.hidden = !src;
  cam.classList.toggle("room", kind === "room");
  $("hunt-cam-tag").textContent = kind === "room" ? "Room camera · click it if I pick the wrong thing" : "Laptop camera";
}

/* ---------------- start ---------------- */

async function start(raw) {
  const name = raw.trim().toLowerCase().replace(/\s+/g, " ");
  if (!name) { $("hunt-error").textContent = "Give it a name first."; return; }
  if (!/^[a-z0-9][a-z0-9 '\-]*$/.test(name)) { $("hunt-error").textContent = "Letters, numbers and spaces only."; return; }
  if (h.taught.includes(name)) { $("hunt-error").textContent = `"${name}" is already in your pack.`; return; }
  h.name = name; h.cancelled = false;
  try { sfx.unlock(); sfx.swoosh("in", 0.3); } catch {}
  show("show");
  $("hunt-yesno").hidden = true;

  if (api.mode === "live") {
    $("hunt-capture").hidden = true;
    say("Waking up the camera…");
    feed("laptop");
    h.active = true;
    try { await api.command(`this is my ${name}`); } catch { $("hunt-error").textContent = "Couldn't reach the app."; enter(); }
  } else {
    await startWebcam();
  }
}

/* ---------------- live: follow the app ---------------- */

function onTeach(t) {
  if (api.mode !== "live") return;
  if (!t.active) {
    if (!h.active) return;
    h.active = false;
    feed(null);
    // Finished: it's in the taught list (allow a moment for the state to arrive).
    const check = (n) => {
      if (h.taught.includes(h.name)) return finish(h.name, api.taughtPhoto(h.name));
      if (n > 0) return setTimeout(() => check(n - 1), 300);
      if (!h.cancelled) { enter(); $("hunt-error").textContent = t.message || "Teaching stopped. Try again?"; }
    };
    check(6);
    return;
  }
  h.active = true;
  h.phase = t.phase;
  if (t.message) say(t.message, tipFor(t.phase, t.laptop_feed));
  const p = t.phase;
  if (p === "first name" || p === "naming") {
    // It wants the name: we already have it.
    if (h.name) api.command(h.name).catch(() => {});
    show("show");
  } else if (p === "looking" || p === "learning") {
    show("show");
    feed(p === "looking" && t.laptop_feed ? "laptop" : "room");
  } else {   // placing, confirm, click
    show("place");
    feed("room");
  }
  $("hunt-yesno").hidden = !(p === "confirm" || p === "learning");
}

function tipFor(phase, laptop) {
  if (phase === "looking") return laptop ? "Hold it inside the brackets and slowly turn it so every side shows." : "Hold it up to the room camera, or click on it in the picture.";
  if (phase === "learning") return "Keep it in view a moment. Wrong thing? Press No.";
  if (phase === "placing") return "Put it down where the room camera can see it, then step back.";
  if (phase === "confirm") return "Is the box around the right thing?";
  if (phase === "click") return "Click on it in the picture.";
  return "";
}

function answer(yes) {
  try { sfx.press(); } catch {}
  if (api.mode === "live") { api.command(yes ? "yes" : "no").catch(() => {}); return; }
  if (yes) completeMock(); else { h.shots = []; startWebcam(); }
}

function clickFeed(e) {
  if (api.mode !== "live" || !h.active || !$("hunt-cam").classList.contains("room")) return;
  const img = e.currentTarget, r = img.getBoundingClientRect();
  if (!img.naturalWidth) return;
  // object-fit: cover: find where the click is on the camera's picture
  const s = Math.max(r.width / img.naturalWidth, r.height / img.naturalHeight);
  const w = img.naturalWidth * s, hh = img.naturalHeight * s;
  const x = (e.clientX - r.left - (r.width - w) / 2) / w, y = (e.clientY - r.top - (r.height - hh) / 2) / hh;
  if (x < 0 || x > 1 || y < 0 || y > 1) return;
  fetch("/api/click", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ x, y }) });
  try { sfx.tick(1.2); } catch {}
}

function cancel() {
  h.cancelled = true;
  if (api.mode === "live" && h.active) api.command("cancel").catch(() => {});
  try { sfx.swoosh("out", 0.3); } catch {}
  enter();
}

/* ---------------- demo mode: the browser's webcam ---------------- */

async function startWebcam() {
  show("show");
  feed(null);
  $("hunt-yesno").hidden = true;
  $("hunt-capture").hidden = false;
  const v = $("hunt-video");
  try {
    h.stream = h.stream || await navigator.mediaDevices.getUserMedia({ video: { width: 1280, height: 720 }, audio: false });
    v.srcObject = h.stream; v.hidden = false;
    say(`Show me your ${h.name}.`, "Hold it inside the brackets, press Capture, then slowly turn it.");
  } catch {
    v.hidden = true;
    say("No webcam here.", "Demo mode: press Capture to add it without photos.");
  }
}

function stopWebcam() {
  if (h.stream) h.stream.getTracks().forEach((t) => t.stop());
  h.stream = null;
  $("hunt-video").hidden = true;
}

function grab() {
  const v = $("hunt-video");
  if (!h.stream || !v.videoWidth) return null;
  const box = v.getBoundingClientRect();
  const scale = Math.max(box.width / v.videoWidth, box.height / v.videoHeight);
  const side = (0.46 * box.width) / scale, sx = (v.videoWidth - side) / 2, sy = (v.videoHeight - side) / 2;
  const c = document.createElement("canvas"); c.width = c.height = CROP;
  const g = c.getContext("2d");
  g.translate(CROP, 0); g.scale(-1, 1);                          // the preview is mirrored
  g.drawImage(v, sx, sy, side, side, 0, 0, CROP, CROP);
  return c.toDataURL("image/jpeg", 0.85);
}

const wait = (ms) => new Promise((r) => setTimeout(r, ms));

async function captureMock() {
  $("hunt-capture").hidden = true;
  const count = $("hunt-count");
  for (let n = 3; n > 0; n--) { count.textContent = n; try { sfx.tick(0.8); } catch {} await wait(550); }
  count.textContent = "";
  $("hunt-cam").classList.add("capturing");
  say("Slowly turn it…");
  h.shots = [];
  for (let i = 0; i < SHOTS; i++) { const s = grab(); if (s) h.shots.push(s); try { sfx.tick(1.3 + i * 0.05); } catch {} await wait(SHOT_GAP); }
  $("hunt-cam").classList.remove("capturing");
  // "Place it": pretend to find it in the room, then ask.
  show("place");
  stopWebcam();
  const img = $("hunt-feed");
  if (h.shots[0]) { img.src = h.shots[0]; img.hidden = false; img.dataset.src = ""; }
  say("Now put it down where the room camera can see it…");
  await wait(1400);
  say(`Is this your ${h.name}?`, "Demo mode: the room camera isn't connected, so this is your photo.");
  $("hunt-yesno").hidden = false;
}

async function completeMock() {
  $("hunt-yesno").hidden = true;
  await api.teach(h.name, h.shots.length ? h.shots : [null]);
  finish(h.name, h.shots[0] || null);
}

/* ---------------- done ---------------- */

function finish(name, photo) {
  h.active = false;
  stopWebcam(); feed(null);
  show("done");
  $("hunt-done-name").textContent = name;
  $("hunt-done-name2").textContent = name;
  const box = $("hunt-done").querySelector(".done-photo"), img = $("hunt-done-img");
  box.classList.remove("no-photo");
  img.onerror = () => { box.classList.add("no-photo"); img.removeAttribute("src"); };
  if (photo) img.src = photo; else img.onerror();
  try { sfx.shing(); } catch {}
  spark(box);
  if (h.onDone) h.onDone(name);
}

// A red spark leaves the photo and flies off toward the Pack (to the right).
function spark(from) {
  if (fx.reduced) return;
  const r = from.getBoundingClientRect();
  const s = document.createElement("div");
  s.className = "spark";
  Object.assign(s.style, { position: "fixed", left: `${r.left + r.width / 2}px`, top: `${r.top + r.height / 2}px`, width: "10px", height: "10px",
    borderRadius: "50%", background: "#ff3b3b", boxShadow: "0 0 18px 6px rgba(255,46,46,0.8)", zIndex: 40, pointerEvents: "none" });
  document.body.appendChild(s);
  s.animate([
    { transform: "translate(-50%,-50%) scale(1)", opacity: 1 },
    { transform: `translate(${innerWidth * 0.35}px, ${-innerHeight * 0.45}px) scale(0.3)`, opacity: 0 },
  ], { duration: 1300, easing: "cubic-bezier(0.5, 0, 0.2, 1)", delay: 300 }).finished.then(() => s.remove());
}
