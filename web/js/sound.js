// Sound effects: recorded clips from UI SFX (uisfx.com), CC0 / public domain, in web/audio.
// "Sci-fi" pack for the small interface sounds and the laser, "Cinematic" pack for the big
// moments (panels, camera moves, found, added). Each export is one named sound; the rest of
// the site only calls these, so swapping a clip is just replacing its file in web/audio.

const CLIPS = ["hover", "press", "tick", "toggle-on", "toggle-off", "open", "close", "whoosh", "scan",
  "charge", "lock", "found", "added", "chime", "low", "decode", "hum", "delete"];

let ctx = null;
let master = null;
let muted = false;
const buffers = {};
let lastHover = 0;

try { muted = localStorage.getItem("ctrlf-muted") === "1"; } catch {}

// Decode every clip up front, without an AudioContext (so no autoplay warning before a click).
const loading = (async () => {
  const decoder = new OfflineAudioContext(2, 1, 44100);
  await Promise.all(CLIPS.map(async (name) => {
    try {
      const data = await (await fetch(new URL(`../audio/${name}.mp3`, import.meta.url))).arrayBuffer();
      buffers[name] = await decoder.decodeAudioData(data);
    } catch {}
  }));
})();
export const ready = () => loading;

export function audio() {
  if (!ctx) {
    ctx = new (window.AudioContext || window.webkitAudioContext)();
    master = ctx.createGain();
    master.gain.value = muted ? 0 : 0.9;
    // A gentle limiter so stacked sounds never clip.
    const limit = ctx.createDynamicsCompressor();
    limit.threshold.value = -6; limit.knee.value = 6; limit.ratio.value = 8;
    limit.attack.value = 0.002; limit.release.value = 0.12;
    master.connect(limit).connect(ctx.destination);
  }
  return ctx;
}

// Resolves true if sound can play right now (autoplay allowed or after a click).
export async function unlock() {
  const c = audio();
  if (c.state !== "running") {
    // Without a click, Chrome can leave resume() pending forever instead of
    // rejecting, so don't wait on it for more than a moment.
    await Promise.race([
      c.resume().catch(() => {}),
      new Promise((r) => setTimeout(r, 400)),
    ]);
  }
  return c.state === "running";
}

export function isMuted() { return muted; }

export function setMuted(value) {
  muted = value;
  try { localStorage.setItem("ctrlf-muted", value ? "1" : "0"); } catch {}
  if (master) master.gain.setTargetAtTime(value ? 0 : 0.9, audio().currentTime, 0.05);
}

// Play one clip. rate: playback speed (also pitch). Returns { stop(fade) } or null.
function play(name, { volume = 0.6, rate = 1, loop = false, pan = 0, delay = 0 } = {}) {
  const buf = buffers[name];
  if (!buf) return null;
  const c = audio();
  if (c.state !== "running") return null;
  const src = c.createBufferSource();
  src.buffer = buf; src.loop = loop; src.playbackRate.value = rate;
  const g = c.createGain(); g.gain.value = volume;
  let out = src.connect(g);
  if (pan && c.createStereoPanner) { const p = c.createStereoPanner(); p.pan.value = pan; out = out.connect(p); }
  out.connect(master);
  const t = c.currentTime + delay;
  src.start(t);
  return {
    stop(fade = 0.25) {
      const now = c.currentTime;
      g.gain.cancelScheduledValues(now);
      g.gain.setValueAtTime(g.gain.value, now);
      g.gain.linearRampToValueAtTime(0.0001, now + fade);
      try { src.stop(now + fade + 0.05); } catch {}
    },
  };
}

const jitter = (amount) => 1 + (Math.random() * 2 - 1) * amount;

/* ---------------- the sounds ---------------- */

// A low cinematic bed under the intro. Returns a stop() function.
export function hum() {
  const h = play("hum", { volume: 0.22, rate: 0.5, loop: true });
  return () => h && h.stop(1.2);
}

// Small UI tick (typing, countdowns, camera shots). pitch shifts it up or down.
export function tick(pitch = 1) {
  play("tick", { volume: 0.32, rate: Math.min(2, Math.max(0.5, pitch)) * jitter(0.03) });
}

// The laser sweeping across the desk.
export function sweep(duration = 1.6) {
  const s = play("scan", { volume: 0.5, rate: 1.4 / Math.max(0.6, duration) * 1.1 });
  if (s) setTimeout(() => s.stop(0.3), duration * 1000);
}

// The laser powering up before it aims.
export function laserCharge() {
  play("charge", { volume: 0.55 });
  play("scan", { volume: 0.25, rate: 1.3, delay: 0.1 });
}

// The laser locking onto the item.
export function laserSnap() {
  play("lock", { volume: 0.6 });
}

// Big camera move.
export function whoosh(duration = 0.9) {
  play("whoosh", { volume: 0.7, rate: Math.min(1.2, Math.max(0.6, 0.7 / duration)) });
}

// Something new to look at (an item came into view, a teaching step finished).
export function chime() {
  play("chime", { volume: 0.5 });
}

// Out of sight, not found, something went wrong.
export function lowTone() {
  play("low", { volume: 0.55 });
}

// Hovering a button, link or path.
export function hover() {
  const now = performance.now();
  if (now - lastHover < 60) return;
  lastHover = now;
  play("hover", { volume: 0.22, rate: jitter(0.04) });
}

// Pressing a button.
export function press() {
  play("press", { volume: 0.45, rate: jitter(0.03) });
}

export function toggle(on = true) {
  play(on ? "toggle-on" : "toggle-off", { volume: 0.45 });
}

// A panel opening ("in") or closing ("out"). duration > 0.5 = a bigger move: adds the whoosh.
export function swoosh(dir = "in", duration = 0.42) {
  play(dir === "in" ? "open" : "close", { volume: 0.5 });
  if (duration >= 0.5) play("whoosh", { volume: 0.45, rate: 0.8, pan: dir === "in" ? 0.15 : -0.15 });
}

// Found it.
export function shing() {
  play("found", { volume: 0.7 });
}

// A new key item was added.
export function added() {
  play("added", { volume: 0.7 });
}

// A key item was deleted.
export function remove() {
  play("delete", { volume: 0.6 });
}

// Text decoding in (classic dashboard).
export function decode() {
  play("decode", { volume: 0.3, rate: jitter(0.05) });
}
