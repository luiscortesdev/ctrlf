// Add a key item: teach wherewolf a new thing to keep track of.
// Name it -> show it (laptop camera) -> place it (room camera, "is this it?") -> done.
// Live: the app does the real teaching ("this is my <name>"), and this page follows its
// phases (state.teach) and sends answers back (yes / no / a click / cancel).
// Demo mode (no app): the browser's webcam takes a few photos instead.

import * as api from "../api.js";
import * as sfx from "../sound.js";

const $ = (id) => document.getElementById(id);
const STEPS = ["name", "show", "place", "done"];
const SHOTS = 6, SHOT_GAP = 420, CROP = 320;

const h = { name: "", active: false, phase: null, laptop: false, taught: [], shots: [], stream: null, cancelled: false, onDone: null };

export function init({ onDone }) {
  h.onDone = onDone;
  $("add-name-form").addEventListener("submit", (e) => { e.preventDefault(); start($("add-name").value); });
  $("add-name").addEventListener("input", () => { $("add-error").textContent = ""; });
  $("add-cancel").addEventListener("click", cancel);
  $("add-yes").addEventListener("click", () => answer(true));
  $("add-no").addEventListener("click", () => answer(false));
  $("add-capture").addEventListener("click", captureMock);
  $("add-again").addEventListener("click", () => enter());
  $("add-feed").addEventListener("click", clickFeed);
  api.on("teach", onTeach);
  api.on("taught", (list) => { h.taught = list; });
}

export function enter() {
  stopWebcam();
  h.name = ""; h.active = false; h.phase = null; h.shots = []; h.cancelled = false;
  $("add-name").value = "";
  $("add-error").textContent = "";
  show("name");
  setTimeout(() => $("add-name").focus(), 400);
}

export function leave() {
  if (h.active && api.mode === "live") api.command("cancel").catch(() => {});
  h.active = false;
  stopWebcam();
  feed(null);
}

export const isTeaching = () => h.active;

/* ---------------- steps ---------------- */

const TITLES = { show: "Step 2 of 3: Show it to the camera", place: "Step 3 of 3: Put it down" };

function show(step) {
  $("add-name-form").hidden = step !== "name";
  $("add-live").hidden = !(step === "show" || step === "place");
  $("add-done").hidden = step !== "done";
  if (TITLES[step]) $("add-step-title").textContent = TITLES[step];
  const i = STEPS.indexOf(step);
  document.querySelectorAll("#add-steps li").forEach((li, k) => {
    li.classList.toggle("done", k < i);
    li.classList.toggle("now", k === i);
    if (k === i) li.setAttribute("aria-current", "step"); else li.removeAttribute("aria-current");
  });
}

function say(text, tip = "") {
  $("add-say").textContent = text;
  $("add-tip").textContent = tip;
}

function feed(kind) {
  // kind: "laptop" | "room" | null
  const img = $("add-feed"), cam = $("add-cam");
  const src = kind === "laptop" ? api.teachVideoUrl() : kind === "room" ? api.videoUrl() : null;
  if (!src) { img.removeAttribute("src"); img.dataset.src = ""; }
  else if (img.dataset.src !== src) { img.dataset.src = src; img.src = src; }
  img.hidden = !src;
  cam.classList.toggle("room", kind === "room");
  $("add-cam-tag").textContent = kind === "room" ? "Room camera (click the item if the box is on the wrong thing)" : "Laptop camera";
}

/* ---------------- start ---------------- */

async function start(raw) {
  const name = raw.trim().toLowerCase().replace(/\s+/g, " ");
  if (!name) { $("add-error").textContent = "Please type a name first."; $("add-name").focus(); return; }
  if (!/^[a-z0-9][a-z0-9 '\-]*$/.test(name)) { $("add-error").textContent = "Please use only letters, numbers and spaces."; return; }
  if (h.taught.includes(name)) { $("add-error").textContent = `"${name}" is already one of your key items.`; return; }
  h.name = name; h.cancelled = false;
  try { sfx.unlock(); } catch {}
  show("show");
  $("add-yesno").hidden = true;

  if (api.mode === "live") {
    $("add-capture").hidden = true;
    say("Starting the camera…");
    feed("laptop");
    h.active = true;
    try { await api.command(`this is my ${name}`); } catch { enter(); $("add-error").textContent = "Couldn't reach the app."; }
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
      if (!h.cancelled) { enter(); $("add-error").textContent = t.message || "Teaching stopped. Try again?"; }
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
  $("add-yesno").hidden = !(p === "confirm" || p === "learning");
}

function tipFor(phase, laptop) {
  if (phase === "looking") return laptop ? "Hold it inside the white corners and turn it slowly so every side shows." : "Hold it up to the room camera, or click on it in the picture.";
  if (phase === "learning") return "Keep holding it still for a moment. If it picked the wrong thing, press \"No, try again\".";
  if (phase === "placing") return "Put it where the room camera can see it, then step back.";
  if (phase === "confirm") return "Is the red box around the right thing?";
  if (phase === "click") return "Click on the item in the picture.";
  return "";
}

function answer(yes) {
  if (api.mode === "live") { api.command(yes ? "yes" : "no").catch(() => {}); return; }
  if (yes) completeMock(); else { h.shots = []; startWebcam(); }
}

function clickFeed(e) {
  if (api.mode !== "live" || !h.active || !$("add-cam").classList.contains("room")) return;
  const img = e.currentTarget, r = img.getBoundingClientRect();
  if (!img.naturalWidth) return;
  // object-fit: cover: find where the click is on the camera's picture
  const s = Math.max(r.width / img.naturalWidth, r.height / img.naturalHeight);
  const w = img.naturalWidth * s, hh = img.naturalHeight * s;
  const x = (e.clientX - r.left - (r.width - w) / 2) / w, y = (e.clientY - r.top - (r.height - hh) / 2) / hh;
  if (x < 0 || x > 1 || y < 0 || y > 1) return;
  api.click(x, y);
}

function cancel() {
  h.cancelled = true;
  if (api.mode === "live" && h.active) api.command("cancel").catch(() => {});
  enter();
}

/* ---------------- demo mode: the browser's webcam ---------------- */

async function startWebcam() {
  show("show");
  feed(null);
  $("add-yesno").hidden = true;
  $("add-capture").hidden = false;
  const v = $("add-video");
  try {
    h.stream = h.stream || await navigator.mediaDevices.getUserMedia({ video: { width: 1280, height: 720 }, audio: false });
    v.srcObject = h.stream; v.hidden = false;
    say(`Hold your ${h.name} up to the camera.`, "Hold it inside the white corners, press \"Take the photos\", then turn it slowly.");
  } catch {
    v.hidden = true;
    say("No camera found on this computer.", "Press \"Take the photos\" to add it without a photo.");
  }
}

function stopWebcam() {
  if (h.stream) h.stream.getTracks().forEach((t) => t.stop());
  h.stream = null;
  $("add-video").hidden = true;
}

function grab() {
  const v = $("add-video");
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
  $("add-capture").hidden = true;
  const count = $("add-count");
  for (let n = 3; n > 0; n--) { count.textContent = n; await wait(700); }
  count.textContent = "";
  $("add-cam").classList.add("capturing");
  say("Taking photos. Turn it slowly…");
  h.shots = [];
  for (let i = 0; i < SHOTS; i++) { const s = grab(); if (s) h.shots.push(s); await wait(SHOT_GAP); }
  $("add-cam").classList.remove("capturing");
  // "Place it": pretend to find it in the room, then ask.
  show("place");
  stopWebcam();
  const img = $("add-feed");
  if (h.shots[0]) { img.src = h.shots[0]; img.hidden = false; img.dataset.src = ""; }
  $("add-cam-tag").textContent = "Your photo";
  say("Now put it down where the room camera can see it…");
  await wait(1400);
  say(`Is this your ${h.name}?`, "Demo mode: the room camera isn't connected, so this is the photo you just took.");
  $("add-yesno").hidden = false;
}

async function completeMock() {
  $("add-yesno").hidden = true;
  await api.teach(h.name, h.shots.length ? h.shots : [null]);
  finish(h.name, h.shots[0] || null);
}

/* ---------------- done ---------------- */

function finish(name, photo) {
  h.active = false;
  stopWebcam(); feed(null);
  show("done");
  $("add-done-name").textContent = name;
  $("add-done-name2").textContent = name;
  const box = $("add-done").querySelector(".done-photo"), img = $("add-done-img");
  box.classList.remove("no-photo");
  img.onerror = () => { box.classList.add("no-photo"); img.removeAttribute("src"); };
  if (photo) img.src = photo; else img.onerror();
  try { sfx.added(); } catch {}
  if (h.onDone) h.onDone(name);
}
