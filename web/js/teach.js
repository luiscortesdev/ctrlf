// Teach mode.
// Live (the app is running): the app does the teaching, the same as saying
// "learn this" or pressing T. It opens the laptop camera itself and asks
// questions; this panel shows its camera and what it says, and sends names,
// yes/no and clicks back as commands (see api.command()).
// Demo mode: the browser's own webcam takes a burst of photos instead.

import * as api from "./api.js";
import * as sfx from "./sound.js";

const $ = (id) => document.getElementById(id);

const SHOTS = 6;          // photos per object
const SHOT_GAP = 450;     // ms between photos (time to turn the object)
const CROP_SIZE = 320;    // px, square crop sent to the backend
const GUIDE = 0.56;       // guide box width as a fraction of the preview (matches CSS)

const dialog = $("teach");
const video = $("teach-video");
const camSelect = $("teach-cam-select");
let stream = null;
let shots = [];
let busy = false;

export function initTeach() {
  $("teach-open").addEventListener("click", () => openTeach());
  $("teach-close").addEventListener("click", close);
  initLive();
  dialog.addEventListener("close", () => {
    stopCamera();
    const img = $("teach-live-img");  // stop the video stream while hidden
    img.removeAttribute("src");
    img.dataset.src = "";
  });
  dialog.addEventListener("cancel", () => {  // Esc key
    if (api.mode === "live" && live.active) api.command("cancel").catch(() => {});
  });
  camSelect.addEventListener("change", () => startCamera(camSelect.value));
  $("teach-capture").addEventListener("click", capture);
  $("teach-save").addEventListener("click", save);
  $("teach-name").addEventListener("input", updateSave);
}

export async function openTeach(name) {
  if (api.mode === "live") return openLive(name);
  shots = [];
  $("teach-live").hidden = true;
  $("teach-mock").hidden = false;
  renderShots();
  $("teach-name").value = "";
  $("teach-msg").textContent = "";
  updateSave();
  dialog.showModal();
  sfx.unlock();
  await startCamera(localStorage.getItem("ctrlf-teach-cam") || undefined);
  await listCameras();
}

function close() {
  if (api.mode === "live" && live.active) api.command("cancel").catch(() => {});
  dialog.close();
}

/* ---------------- Live: the app teaches ---------------- */

const live = { active: false, phase: null, closing: null };

function initLive() {
  $("teach-live-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const name = $("teach-live-name").value.trim();
    if (!name) return;
    api.command(name);
    $("teach-live-name").value = "";
  });
  $("teach-yes").addEventListener("click", () => api.command("yes"));
  $("teach-no").addEventListener("click", () => api.command("no"));
  $("teach-cancel").addEventListener("click", () => { api.command("cancel"); dialog.close(); });
  // Clicking the room view while teaching = "it's this one".
  $("teach-live-img").addEventListener("click", (e) => {
    if (live.phase === "looking" && live.laptop) return; // the laptop view isn't clickable yet
    const img = e.currentTarget;
    const r = img.getBoundingClientRect();
    // object-fit: contain: find the picture's box inside the element.
    const scale = Math.min(r.width / img.naturalWidth, r.height / img.naturalHeight);
    const w = img.naturalWidth * scale, h = img.naturalHeight * scale;
    const x = (e.clientX - r.left - (r.width - w) / 2) / w;
    const y = (e.clientY - r.top - (r.height - h) / 2) / h;
    if (x < 0 || x > 1 || y < 0 || y > 1) return;
    api.click(x, y);
    sfx.tick(1.2);
  });
  api.on("teach", onTeach);
}

function openLive(name) {
  $("teach-mock").hidden = true;
  $("teach-live").hidden = false;
  $("teach-live-say").textContent = "Starting…";
  setFeed(true);
  if (!dialog.open) dialog.showModal();
  sfx.unlock();
  clearTimeout(live.closing);
  if (!live.active) api.command(name ? `this is my ${name}` : "learn this");
}

function setFeed(laptop) {
  const img = $("teach-live-img");
  const src = laptop ? api.teachVideoUrl() : api.videoUrl();
  if (img.dataset.src !== src) { img.dataset.src = src; img.src = src; }
  live.laptop = laptop;
  $("teach-live-hint").hidden = laptop;
}

function onTeach(t) {
  const was = live.active;
  live.active = t.active;
  live.phase = t.phase;
  if (t.message) $("teach-live-say").textContent = t.message;
  if (t.active) {
    // Started from the app itself (T key or "learn this" out loud)? Show it here too.
    if (!dialog.open) openLive();
    setFeed(t.phase === "looking" && t.laptop_feed);
    $("teach-live-form").hidden = !(t.phase === "naming" || t.phase === "first name" || (t.phase === "looking" && !t.name));
    $("teach-live-yesno").hidden = !(t.phase === "confirm" || t.phase === "learning");
  } else if (was && dialog.open) {
    // Finished or cancelled: leave the last words up for a moment.
    $("teach-live-form").hidden = true;
    $("teach-live-yesno").hidden = true;
    live.closing = setTimeout(() => dialog.close(), 2200);
  }
}

function stopCamera() {
  if (stream) stream.getTracks().forEach((t) => t.stop());
  stream = null;
}

async function startCamera(deviceId) {
  stopCamera();
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      video: deviceId ? { deviceId: { exact: deviceId }, width: 1280, height: 960 } : { width: 1280, height: 960 },
      audio: false,
    });
    video.srcObject = stream;
    $("teach-nocam").hidden = true;
    const id = stream.getVideoTracks()[0].getSettings().deviceId;
    if (id) { try { localStorage.setItem("ctrlf-teach-cam", id); } catch {} }
  } catch {
    $("teach-nocam").hidden = false;
  }
}

// The room camera may also be plugged in, so let people pick the laptop webcam.
async function listCameras() {
  const devices = (await navigator.mediaDevices.enumerateDevices()).filter((d) => d.kind === "videoinput");
  const current = stream && stream.getVideoTracks()[0].getSettings().deviceId;
  camSelect.innerHTML = devices.map((d, i) =>
    `<option value="${d.deviceId}" ${d.deviceId === current ? "selected" : ""}>${d.label || `Camera ${i + 1}`}</option>`
  ).join("");
}

function wait(ms) { return new Promise((r) => setTimeout(r, ms)); }

// Crop the square inside the guide box out of the current video frame.
function grab() {
  const vw = video.videoWidth, vh = video.videoHeight;
  if (!vw || !vh) return null;
  const box = video.getBoundingClientRect();
  const scale = Math.max(box.width / vw, box.height / vh); // object-fit: cover
  const side = (GUIDE * box.width) / scale;
  const sx = (vw - side) / 2, sy = (vh - side) / 2;
  const c = document.createElement("canvas");
  c.width = c.height = CROP_SIZE;
  c.getContext("2d").drawImage(video, sx, sy, side, side, 0, 0, CROP_SIZE, CROP_SIZE);
  return c.toDataURL("image/jpeg", 0.85);
}

async function capture() {
  if (busy || !stream) return;
  busy = true;
  shots = [];
  renderShots();
  updateSave();
  const count = $("teach-count");
  $("teach-msg").textContent = "Get ready…";

  for (let n = 3; n > 0; n--) {
    count.textContent = n;
    sfx.tick(0.8);
    await wait(600);
  }
  count.textContent = "";
  dialog.classList.add("capturing");
  $("teach-msg").textContent = "Slowly turn it…";

  for (let i = 0; i < SHOTS; i++) {
    const img = grab();
    if (img) { shots.push(img); renderShots(); sfx.tick(1.3 + i * 0.05); }
    await wait(SHOT_GAP);
  }

  dialog.classList.remove("capturing");
  sfx.chime();
  $("teach-msg").textContent = `${shots.length} photos taken. Name it and save, or capture again.`;
  busy = false;
  updateSave();
  $("teach-name").focus();
}

function renderShots() {
  $("teach-shots").innerHTML = shots.map((s, i) => `<img src="${s}" alt="Photo ${i + 1}">`).join("");
}

function updateSave() {
  $("teach-save").disabled = busy || shots.length < 3 || !$("teach-name").value.trim();
}

async function save() {
  const name = $("teach-name").value.trim().toLowerCase();
  if (!name || shots.length < 3) return;
  $("teach-save").disabled = true;
  $("teach-msg").textContent = "Learning…";
  try {
    await api.teach(name, shots);
    sfx.laserSnap();
    $("teach-msg").textContent = `Learned "${name}". The room camera is now looking for it.`;
    await wait(1100);
    close();
  } catch (e) {
    $("teach-msg").textContent = `Couldn't save: ${e.message || e}`;
    updateSave();
  }
}
