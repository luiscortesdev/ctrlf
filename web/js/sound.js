// All sounds are synthesized with the Web Audio API: no audio files,
// no licenses, works offline. Each function is one named sound.

let ctx = null;
let master = null;
let muted = false;

try { muted = localStorage.getItem("ctrlf-muted") === "1"; } catch {}

let room = null;   // shared reverb send: makes the short sounds feel like they're in a space
let lastHover = 0;

export function audio() {
  if (!ctx) {
    ctx = new (window.AudioContext || window.webkitAudioContext)();
    master = ctx.createGain();
    master.gain.value = muted ? 0 : 0.8;
    // A gentle limiter so stacked sounds never clip.
    const limit = ctx.createDynamicsCompressor();
    limit.threshold.value = -6; limit.knee.value = 6; limit.ratio.value = 8;
    limit.attack.value = 0.002; limit.release.value = 0.12;
    master.connect(limit).connect(ctx.destination);
    // Reverb: a synthetic impulse (stereo noise with a fast exponential tail).
    const conv = ctx.createConvolver();
    const len = Math.floor(ctx.sampleRate * 1.6);
    const ir = ctx.createBuffer(2, len, ctx.sampleRate);
    for (let ch = 0; ch < 2; ch++) {
      const d = ir.getChannelData(ch);
      for (let i = 0; i < len; i++) d[i] = (Math.random() * 2 - 1) * Math.pow(1 - i / len, 3.2);
    }
    conv.buffer = ir;
    room = ctx.createGain();
    room.gain.value = 0.22;
    room.connect(conv).connect(master);
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
  if (master) master.gain.setTargetAtTime(value ? 0 : 0.8, audio().currentTime, 0.05);
}

function noiseBuffer(seconds) {
  const c = audio();
  const buf = c.createBuffer(1, Math.floor(c.sampleRate * seconds), c.sampleRate);
  const data = buf.getChannelData(0);
  for (let i = 0; i < data.length; i++) data[i] = Math.random() * 2 - 1;
  return buf;
}

function env(gainNode, t, attack, peak, release) {
  gainNode.gain.setValueAtTime(0.0001, t);
  gainNode.gain.exponentialRampToValueAtTime(peak, t + attack);
  gainNode.gain.exponentialRampToValueAtTime(0.0001, t + attack + release);
}

// Low ambient hum under the intro. Returns a stop() function.
export function hum() {
  const c = audio();
  const t = c.currentTime;
  const out = c.createGain();
  out.gain.setValueAtTime(0.0001, t);
  out.gain.exponentialRampToValueAtTime(0.16, t + 3);
  const filter = c.createBiquadFilter();
  filter.type = "lowpass";
  filter.frequency.value = 380;
  filter.connect(out).connect(master);

  const oscs = [55, 55.4, 110.3].map((f, i) => {
    const o = c.createOscillator();
    o.type = i === 2 ? "triangle" : "sawtooth";
    o.frequency.value = f;
    const g = c.createGain();
    g.gain.value = i === 2 ? 0.25 : 0.5;
    o.connect(g).connect(filter);
    o.start(t);
    return o;
  });

  // Slow filter movement so the hum breathes.
  const lfo = c.createOscillator();
  const lfoGain = c.createGain();
  lfo.frequency.value = 0.15;
  lfoGain.gain.value = 140;
  lfo.connect(lfoGain).connect(filter.frequency);
  lfo.start(t);

  return function stop(fade = 1.2) {
    const now = c.currentTime;
    out.gain.cancelScheduledValues(now);
    out.gain.setValueAtTime(Math.max(out.gain.value, 0.0001), now);
    out.gain.exponentialRampToValueAtTime(0.0001, now + fade);
    [...oscs, lfo].forEach((o) => o.stop(now + fade + 0.1));
  };
}

// Short tick for text appearing.
export function tick(pitch = 1) {
  const c = audio();
  const t = c.currentTime;
  const o = c.createOscillator();
  const g = c.createGain();
  o.type = "square";
  o.frequency.value = 1800 * pitch;
  env(g, t, 0.002, 0.05, 0.04);
  o.connect(g).connect(master);
  o.start(t);
  o.stop(t + 0.08);
}

// Scanning sweep: filtered noise moving across the spectrum.
export function sweep(duration = 1.6) {
  const c = audio();
  const t = c.currentTime;
  const src = c.createBufferSource();
  src.buffer = noiseBuffer(duration);
  const bp = c.createBiquadFilter();
  bp.type = "bandpass";
  bp.Q.value = 8;
  bp.frequency.setValueAtTime(400, t);
  bp.frequency.exponentialRampToValueAtTime(5000, t + duration);
  const g = c.createGain();
  g.gain.setValueAtTime(0.0001, t);
  g.gain.exponentialRampToValueAtTime(0.25, t + duration * 0.3);
  g.gain.exponentialRampToValueAtTime(0.0001, t + duration);
  src.connect(bp).connect(g).connect(master);
  src.start(t);
}

// Laser charge-up (rising) then a sharp snap. The signature sound.
export function laserCharge(chargeTime = 0.7) {
  const c = audio();
  const t = c.currentTime;
  const o = c.createOscillator();
  const g = c.createGain();
  o.type = "sawtooth";
  o.frequency.setValueAtTime(180, t);
  o.frequency.exponentialRampToValueAtTime(1400, t + chargeTime);
  const lp = c.createBiquadFilter();
  lp.type = "lowpass";
  lp.frequency.setValueAtTime(600, t);
  lp.frequency.exponentialRampToValueAtTime(6000, t + chargeTime);
  g.gain.setValueAtTime(0.0001, t);
  g.gain.exponentialRampToValueAtTime(0.12, t + chargeTime * 0.9);
  g.gain.exponentialRampToValueAtTime(0.0001, t + chargeTime + 0.05);
  o.connect(lp).connect(g).connect(master);
  o.start(t);
  o.stop(t + chargeTime + 0.1);
  setTimeout(laserSnap, chargeTime * 1000);
}

export function laserSnap() {
  const c = audio();
  const t = c.currentTime;
  // Click transient
  const src = c.createBufferSource();
  src.buffer = noiseBuffer(0.12);
  const hp = c.createBiquadFilter();
  hp.type = "highpass";
  hp.frequency.value = 2500;
  const g = c.createGain();
  env(g, t, 0.001, 0.4, 0.09);
  src.connect(hp).connect(g).connect(master);
  src.start(t);
  // Bright tone that drops
  const o = c.createOscillator();
  const og = c.createGain();
  o.type = "sine";
  o.frequency.setValueAtTime(2400, t);
  o.frequency.exponentialRampToValueAtTime(900, t + 0.18);
  env(og, t, 0.002, 0.18, 0.25);
  o.connect(og).connect(master);
  o.start(t);
  o.stop(t + 0.3);
}

// Whoosh for transitions.
export function whoosh(duration = 0.9) {
  const c = audio();
  const t = c.currentTime;
  const src = c.createBufferSource();
  src.buffer = noiseBuffer(duration);
  const lp = c.createBiquadFilter();
  lp.type = "lowpass";
  lp.frequency.setValueAtTime(300, t);
  lp.frequency.exponentialRampToValueAtTime(4000, t + duration * 0.5);
  lp.frequency.exponentialRampToValueAtTime(200, t + duration);
  const g = c.createGain();
  g.gain.setValueAtTime(0.0001, t);
  g.gain.exponentialRampToValueAtTime(0.35, t + duration * 0.45);
  g.gain.exponentialRampToValueAtTime(0.0001, t + duration);
  src.connect(lp).connect(g).connect(master);
  src.start(t);
}

// Item found: two quick rising notes.
export function chime() {
  const c = audio();
  const t = c.currentTime;
  [880, 1318.5].forEach((f, i) => {
    const o = c.createOscillator();
    const g = c.createGain();
    o.type = "sine";
    o.frequency.value = f;
    const start = t + i * 0.09;
    g.gain.setValueAtTime(0.0001, start);
    g.gain.exponentialRampToValueAtTime(0.14, start + 0.01);
    g.gain.exponentialRampToValueAtTime(0.0001, start + 0.5);
    o.connect(g).connect(master);
    o.start(start);
    o.stop(start + 0.55);
  });
}

// Item went hidden: one soft falling note.
export function lowTone() {
  const c = audio();
  const t = c.currentTime;
  const o = c.createOscillator();
  const g = c.createGain();
  o.type = "triangle";
  o.frequency.setValueAtTime(330, t);
  o.frequency.exponentialRampToValueAtTime(220, t + 0.35);
  g.gain.setValueAtTime(0.0001, t);
  g.gain.exponentialRampToValueAtTime(0.12, t + 0.02);
  g.gain.exponentialRampToValueAtTime(0.0001, t + 0.6);
  o.connect(g).connect(master);
  o.start(t);
  o.stop(t + 0.65);
}

/* ---------- Interface sounds (sleek, short, a little flashy) ---------- */

// Sends a node to the speakers dry, plus some of it into the reverb.
function out(node, wet = 0.3, pan = 0) {
  const c = audio();
  let n = node;
  if (pan && c.createStereoPanner) {
    const p = c.createStereoPanner();
    p.pan.value = pan;
    n = n.connect(p);
  }
  n.connect(master);
  if (wet) {
    const w = c.createGain();
    w.gain.value = wet;
    n.connect(w).connect(room);
  }
}

function partial(freq, t, peak, decay, type = "sine") {
  const c = audio();
  const o = c.createOscillator();
  const g = c.createGain();
  o.type = type;
  o.frequency.value = freq;
  env(g, t, 0.0015, peak, decay);
  o.connect(g);
  o.start(t);
  o.stop(t + decay + 0.05);
  return g;
}

// Hover: a tiny glassy tick. Throttled so sweeping across buttons doesn't rattle.
export function hover() {
  const c = audio();
  if (c.state !== "running") return;
  const now = performance.now();
  if (now - lastHover < 45) return;
  lastHover = now;
  const t = c.currentTime;
  const f = 3150 * (0.97 + Math.random() * 0.06);
  out(partial(f, t, 0.03, 0.03), 0.12, (Math.random() - 0.5) * 0.3);
  out(partial(f * 2.76, t, 0.012, 0.018), 0.1);
}

// Press: a crisp two-layer click (a bright transient over a short low body).
export function press() {
  const c = audio();
  const t = c.currentTime;
  const src = c.createBufferSource();
  src.buffer = noiseBuffer(0.03);
  const bp = c.createBiquadFilter();
  bp.type = "bandpass"; bp.frequency.value = 4200; bp.Q.value = 1.2;
  const g = c.createGain();
  env(g, t, 0.0008, 0.22, 0.018);
  src.connect(bp).connect(g);
  out(g, 0.18);
  src.start(t);
  const o = c.createOscillator();
  const og = c.createGain();
  o.type = "sine";
  o.frequency.setValueAtTime(190, t);
  o.frequency.exponentialRampToValueAtTime(70, t + 0.06);
  env(og, t, 0.002, 0.2, 0.07);
  o.connect(og);
  out(og, 0);
  o.start(t);
  o.stop(t + 0.1);
}

// Switch flipped: two quick tones, up for on and down for off.
export function toggle(on = true) {
  const c = audio();
  const t = c.currentTime;
  const [a, b] = on ? [1320, 1980] : [1760, 1175];
  out(partial(a, t, 0.06, 0.06, "triangle"), 0.2);
  out(partial(b, t + 0.045, 0.06, 0.09, "triangle"), 0.25);
}

// Swoosh: air moving across the stereo field. dir = "in" (opening) or "out".
export function swoosh(dir = "in", duration = 0.42) {
  const c = audio();
  const t = c.currentTime;
  const src = c.createBufferSource();
  src.buffer = noiseBuffer(duration);
  const bp = c.createBiquadFilter();
  bp.type = "bandpass"; bp.Q.value = 2.2;
  const [f0, f1] = dir === "in" ? [500, 5200] : [4200, 380];
  bp.frequency.setValueAtTime(f0, t);
  bp.frequency.exponentialRampToValueAtTime(f1, t + duration);
  const g = c.createGain();
  g.gain.setValueAtTime(0.0001, t);
  g.gain.exponentialRampToValueAtTime(0.32, t + duration * 0.55);
  g.gain.exponentialRampToValueAtTime(0.0001, t + duration);
  src.connect(bp).connect(g);
  if (c.createStereoPanner) {
    const p = c.createStereoPanner();
    p.pan.setValueAtTime(dir === "in" ? -0.6 : 0.6, t);
    p.pan.linearRampToValueAtTime(dir === "in" ? 0.6 : -0.6, t + duration);
    g.connect(p);
    p.connect(master);
    const w = c.createGain(); w.gain.value = 0.35; p.connect(w).connect(room);
  } else out(g, 0.35);
  src.start(t);
}

// The flashy one: a blade drawn from its sheath. A fast metallic scrape, then
// a bright inharmonic ring that shimmers out. For "found it" and big reveals.
export function shing(intensity = 1) {
  const c = audio();
  const t = c.currentTime;
  const src = c.createBufferSource();
  src.buffer = noiseBuffer(0.2);
  const bp = c.createBiquadFilter();
  bp.type = "bandpass"; bp.Q.value = 6;
  bp.frequency.setValueAtTime(2500, t);
  bp.frequency.exponentialRampToValueAtTime(9000, t + 0.14);
  const sg = c.createGain();
  sg.gain.setValueAtTime(0.0001, t);
  sg.gain.exponentialRampToValueAtTime(0.18 * intensity, t + 0.1);
  sg.gain.exponentialRampToValueAtTime(0.0001, t + 0.16);
  src.connect(bp).connect(sg);
  out(sg, 0.4, -0.2);
  src.start(t);
  const hit = t + 0.12;
  // Metal: partials that aren't whole multiples, each detuned a hair for shimmer.
  [[2093, 0.07, 1.1], [3164, 0.05, 0.9], [4402, 0.04, 0.75], [5961, 0.03, 0.6], [7713, 0.02, 0.45]]
    .forEach(([f, peak, decay], i) => {
      out(partial(f, hit, peak * intensity, decay), 0.55, i % 2 ? 0.25 : -0.25);
      out(partial(f * 1.004, hit, peak * 0.6 * intensity, decay * 0.9), 0.55, i % 2 ? -0.25 : 0.25);
    });
}

// Text decoding (scrambled letters settling): a very soft data chatter.
export function decode(length = 0.25) {
  const c = audio();
  if (c.state !== "running") return;
  const t = c.currentTime;
  for (let i = 0; i < 5; i++) {
    out(partial(2400 + Math.random() * 2600, t + i * (length / 5), 0.012, 0.012, "square"), 0.05);
  }
}
