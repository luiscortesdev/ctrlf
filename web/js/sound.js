// All sounds are synthesized with the Web Audio API: no audio files,
// no licenses, works offline. Each function is one named sound.

let ctx = null;
let master = null;
let muted = false;

try { muted = localStorage.getItem("ctrlf-muted") === "1"; } catch {}

export function audio() {
  if (!ctx) {
    ctx = new (window.AudioContext || window.webkitAudioContext)();
    master = ctx.createGain();
    master.gain.value = muted ? 0 : 0.8;
    master.connect(ctx.destination);
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
