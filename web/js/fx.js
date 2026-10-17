// wherewolf interface effects, all in one place:
//   - the laser-dot cursor with its spark trail
//   - the living dot-grid background (bends toward the cursor, a slow scan line, ripples)
//   - hover/press sounds for everything clickable
//   - text scramble, word reveals, typing
//   - the spotlight border on cards (CSS .fx-spot reads --x/--y from here)
//   - dialogs opening as a circle out of the button that opened them
//   - the logo's eyes following the cursor
// Everything respects "reduce motion", and the cursor and grid are off on touchscreens.
// To turn one off, remove its line in init() at the bottom.

import * as sfx from "./sound.js";

export const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
const finePointer = matchMedia("(pointer: fine)").matches;

const CLICKABLE = "a[href], button:not(:disabled), [role='button'], select, summary, label[for], .fx-click";
const TYPING = "input:not([type='button']):not([type='submit']):not([type='checkbox']), textarea";

// Where the pointer is, shared by the cursor, grid and eyes.
const P = { x: innerWidth / 2, y: innerHeight / 2, seen: false, moved: 0 };
const listeners = new Set();
addEventListener("pointermove", (e) => {
  if (e.pointerType !== "mouse" && e.pointerType !== "pen") return;
  P.x = e.clientX; P.y = e.clientY; P.seen = true; P.moved = performance.now();
  listeners.forEach((fn) => fn(e));
}, { passive: true });
function onMove(fn) { listeners.add(fn); }

/* ============================ Laser cursor ============================ */

function initCursor() {
  if (!finePointer) return;
  document.documentElement.classList.add("fx-cursor");
  const cv = document.createElement("canvas");
  cv.className = "fx-cursor-layer";
  cv.setAttribute("aria-hidden", "true");
  // A popover lives in the browser's top layer, so the cursor stays above open dialogs.
  if ("popover" in HTMLElement.prototype) cv.setAttribute("popover", "manual");
  document.body.appendChild(cv);
  const raise = () => {
    if (!cv.showPopover) return;
    try { cv.hidePopover(); } catch {}
    try { cv.showPopover(); } catch {}
  };
  raise();
  // Re-raise whenever a dialog opens (it joins the top layer above us).
  new MutationObserver(raise).observe(document.body, { subtree: true, attributes: true, attributeFilter: ["open"] });

  const g = cv.getContext("2d");
  let dpr = 1;
  const size = () => {
    dpr = Math.min(devicePixelRatio || 1, 2);
    cv.width = innerWidth * dpr; cv.height = innerHeight * dpr;
  };
  size();
  addEventListener("resize", size);

  const dot = { x: P.x, y: P.y };
  const trail = [];        // recent dot positions, for the thin streak
  const sparks = [];       // tiny particles shed while moving
  let ring = 0, ringTo = 0; // hover ring (0..1)
  let burst = 0;            // click flash (1 -> 0)
  let hidden = true, typing = false;
  let dist = 0, running = false, last = performance.now();

  function interactiveAt(el) {
    if (!el || !el.closest) return { click: false, type: false };
    return { click: !!el.closest(CLICKABLE), type: !!el.closest(TYPING) };
  }

  addEventListener("pointerover", (e) => {
    const k = interactiveAt(e.target);
    ringTo = k.click ? 1 : 0;
    typing = k.type;
  }, { passive: true });
  addEventListener("pointerdown", () => { burst = 1; kick(); }, { passive: true });
  document.addEventListener("pointerleave", () => { hidden = true; kick(); });
  addEventListener("blur", () => { hidden = true; kick(); });
  onMove(() => { hidden = false; kick(); });

  function kick() { if (!running) { running = true; last = performance.now(); requestAnimationFrame(frame); } }

  function frame(now) {
    const dt = Math.min(48, now - last) / 16.67; // in 60 fps frames
    last = now;
    const k = reduced ? 1 : 1 - Math.pow(1 - 0.42, dt);
    const px = dot.x, py = dot.y;
    dot.x += (P.x - dot.x) * k;
    dot.y += (P.y - dot.y) * k;
    const step = Math.hypot(dot.x - px, dot.y - py);

    if (!reduced && !hidden && !typing) {
      trail.push({ x: dot.x, y: dot.y, t: now });
      dist += step;
      while (dist > 9 && sparks.length < 70) {
        dist -= 9;
        const a = Math.random() * Math.PI * 2;
        const sp = 0.15 + Math.random() * 0.45;
        sparks.push({
          x: dot.x + (Math.random() - 0.5) * 3, y: dot.y + (Math.random() - 0.5) * 3,
          vx: Math.cos(a) * sp - (dot.x - px) * 0.04, vy: Math.sin(a) * sp - (dot.y - py) * 0.04,
          life: 1, decay: 0.035 + Math.random() * 0.03, r: 0.7 + Math.random() * 1.1,
        });
      }
    }
    while (trail.length && now - trail[0].t > 120) trail.shift();
    ring += (ringTo - ring) * (reduced ? 1 : 1 - Math.pow(0.78, dt));
    burst = Math.max(0, burst - 0.06 * dt);

    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, innerWidth, innerHeight);
    g.globalCompositeOperation = "lighter";

    // Streak: a hair-thin line through the last few positions, fading out.
    if (trail.length > 2) {
      for (let i = 1; i < trail.length; i++) {
        const a = i / trail.length;
        g.strokeStyle = `rgba(255, 60, 60, ${0.35 * a})`;
        g.lineWidth = 1.6 * a;
        g.beginPath();
        g.moveTo(trail[i - 1].x, trail[i - 1].y);
        g.lineTo(trail[i].x, trail[i].y);
        g.stroke();
      }
    }
    // Sparks
    for (let i = sparks.length - 1; i >= 0; i--) {
      const s = sparks[i];
      s.x += s.vx * dt; s.y += s.vy * dt; s.vx *= 0.96; s.vy *= 0.96;
      s.life -= s.decay * dt;
      if (s.life <= 0) { sparks.splice(i, 1); continue; }
      g.fillStyle = `rgba(255, ${70 + 120 * s.life | 0}, ${70 + 90 * s.life | 0}, ${s.life * 0.85})`;
      g.beginPath(); g.arc(s.x, s.y, s.r * s.life + 0.2, 0, 6.283); g.fill();
    }

    if (!hidden && !typing) {
      // Glow, then the hot core: a real laser dot is white in the middle.
      const glow = g.createRadialGradient(dot.x, dot.y, 0, dot.x, dot.y, 14 + burst * 10);
      glow.addColorStop(0, "rgba(255, 46, 46, 0.55)");
      glow.addColorStop(0.35, "rgba(255, 46, 46, 0.16)");
      glow.addColorStop(1, "rgba(255, 46, 46, 0)");
      g.fillStyle = glow;
      g.beginPath(); g.arc(dot.x, dot.y, 14 + burst * 10, 0, 6.283); g.fill();
      g.fillStyle = "#FF2E2E";
      g.beginPath(); g.arc(dot.x, dot.y, 3.2 - ring * 0.8, 0, 6.283); g.fill();
      g.fillStyle = "rgba(255, 225, 225, 0.95)";
      g.beginPath(); g.arc(dot.x, dot.y, 1.2, 0, 6.283); g.fill();
      // Over something clickable: a thin ring locks on.
      if (ring > 0.02) {
        g.globalCompositeOperation = "source-over";
        g.strokeStyle = `rgba(255, 107, 107, ${0.75 * ring})`;
        g.lineWidth = 1;
        g.beginPath(); g.arc(dot.x, dot.y, 6 + 9 * ring, 0, 6.283); g.stroke();
      }
      if (burst > 0) {
        g.globalCompositeOperation = "source-over";
        g.strokeStyle = `rgba(255, 46, 46, ${burst * 0.8})`;
        g.lineWidth = 1.2;
        g.beginPath(); g.arc(dot.x, dot.y, 8 + (1 - burst) * 22, 0, 6.283); g.stroke();
      }
    }
    g.globalCompositeOperation = "source-over";

    const settled = Math.abs(P.x - dot.x) < 0.1 && Math.abs(P.y - dot.y) < 0.1;
    if (sparks.length || trail.length || burst > 0 || Math.abs(ring - ringTo) > 0.01 || !settled) {
      requestAnimationFrame(frame);
    } else {
      running = false;
    }
  }
}

/* ============================ Background grid ============================ */

let gridPulse = () => {};
// A ripple through the background grid from (x, y) in page pixels, e.g. when something is found.
export function pulse(x, y, color = [255, 46, 46]) { gridPulse(x, y, color); }

function initGrid() {
  const cv = document.createElement("canvas");
  cv.className = "fx-grid";
  cv.setAttribute("aria-hidden", "true");
  document.body.prepend(cv);
  const g = cv.getContext("2d");
  const GAP = 28, R = 190;               // dot spacing; cursor's reach
  const BASE = [125, 136, 156], BG = "#0B0F19";
  let dpr = 1, W = 0, H = 0, cols = 0, rows = 0, ox = 0, oy = 0;
  let base = null;                        // the plain grid, drawn once per resize
  const lens = { x: P.x, y: P.y, s: 0 };  // smoothed cursor + strength
  const ripples = [];
  let scan = null, running = false, last = 0;

  function size() {
    dpr = Math.min(devicePixelRatio || 1, 1.5);
    W = innerWidth; H = innerHeight;
    cv.width = W * dpr; cv.height = H * dpr;
    cols = Math.ceil(W / GAP) + 1; rows = Math.ceil(H / GAP) + 1;
    ox = (W - (cols - 1) * GAP) / 2; oy = (H - (rows - 1) * GAP) / 2;
    base = document.createElement("canvas");
    base.width = cv.width; base.height = cv.height;
    const b = base.getContext("2d");
    b.setTransform(dpr, 0, 0, dpr, 0, 0);
    b.fillStyle = BG; b.fillRect(0, 0, W, H);
    b.fillStyle = `rgba(${BASE}, 0.17)`;
    for (let r = 0; r < rows; r++) for (let c = 0; c < cols; c++) b.fillRect(ox + c * GAP - 0.75, oy + r * GAP - 0.75, 1.5, 1.5);
    draw(performance.now());
  }

  function dotAt(x, y, a, rgb, s = 1.5) {
    g.fillStyle = `rgba(${rgb[0] | 0}, ${rgb[1] | 0}, ${rgb[2] | 0}, ${a})`;
    g.fillRect(x - s / 2, y - s / 2, s, s);
  }
  const mix = (a, b, t) => [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];

  function draw(now) {
    g.setTransform(1, 0, 0, 1, 0, 0);
    g.drawImage(base, 0, 0);
    g.setTransform(dpr, 0, 0, dpr, 0, 0);

    // Around the cursor: dots lean toward it and warm up.
    if (lens.s > 0.01) {
      g.fillStyle = BG;
      g.beginPath(); g.arc(lens.x, lens.y, R + 4, 0, 6.283); g.fill();
      const c0 = Math.max(0, Math.floor((lens.x - R - ox) / GAP)), c1 = Math.min(cols - 1, Math.ceil((lens.x + R - ox) / GAP));
      const r0 = Math.max(0, Math.floor((lens.y - R - oy) / GAP)), r1 = Math.min(rows - 1, Math.ceil((lens.y + R - oy) / GAP));
      for (let r = r0; r <= r1; r++) for (let c = c0; c <= c1; c++) {
        const x = ox + c * GAP, y = oy + r * GAP;
        const dx = lens.x - x, dy = lens.y - y, d = Math.hypot(dx, dy);
        if (d > R + 4) continue;
        const f = Math.max(0, 1 - d / R);
        const pull = f * f * 9 * lens.s;
        const nx = d ? dx / d : 0, ny = d ? dy / d : 0;
        dotAt(x + nx * pull, y + ny * pull, 0.17 + 0.5 * f * f * lens.s, mix(BASE, [255, 70, 70], f * 0.75 * lens.s), 1.5 + f * lens.s);
      }
    }
    // The slow scan line: a band that lights the dots it passes.
    if (scan) {
      const t = (now - scan.t0) / scan.dur;
      const y = -60 + t * (H + 120);
      const r0 = Math.max(0, Math.floor((y - 40 - oy) / GAP)), r1 = Math.min(rows - 1, Math.ceil((y + 40 - oy) / GAP));
      for (let r = r0; r <= r1; r++) {
        const yy = oy + r * GAP, f = Math.max(0, 1 - Math.abs(yy - y) / 40);
        for (let c = 0; c < cols; c++) dotAt(ox + c * GAP, yy, 0.28 * f, [255, 90, 90], 1.5);
      }
      g.fillStyle = "rgba(255, 46, 46, 0.05)";
      g.fillRect(0, y - 0.5, W, 1);
    }
    // Ripples: a ring of light running outward.
    for (const rp of ripples) {
      const t = (now - rp.t0) / 1400;
      const rad = t * Math.hypot(W, H) * 0.7, band = 46, a = (1 - t) * 0.6;
      const c0 = Math.max(0, Math.floor((rp.x - rad - band - ox) / GAP)), c1 = Math.min(cols - 1, Math.ceil((rp.x + rad + band - ox) / GAP));
      const r0 = Math.max(0, Math.floor((rp.y - rad - band - oy) / GAP)), r1 = Math.min(rows - 1, Math.ceil((rp.y + rad + band - oy) / GAP));
      for (let r = r0; r <= r1; r++) for (let c = c0; c <= c1; c++) {
        const x = ox + c * GAP, y = oy + r * GAP, d = Math.hypot(x - rp.x, y - rp.y);
        const f = Math.max(0, 1 - Math.abs(d - rad) / band);
        if (f <= 0) continue;
        const push = f * 5;
        dotAt(x + ((x - rp.x) / (d || 1)) * push, y + ((y - rp.y) / (d || 1)) * push, a * f, rp.color, 1.5 + f);
      }
    }
  }

  function frame(now) {
    const dt = Math.min(48, now - last) / 16.67; last = now;
    const k = 1 - Math.pow(1 - 0.12, dt);
    lens.x += (P.x - lens.x) * k; lens.y += (P.y - lens.y) * k;
    const want = P.seen && now - P.moved < 2500 ? 1 : 0;   // fades when the mouse rests
    lens.s += (want - lens.s) * (1 - Math.pow(1 - 0.06, dt));
    for (let i = ripples.length - 1; i >= 0; i--) if (now - ripples[i].t0 > 1400) ripples.splice(i, 1);
    if (scan && now - scan.t0 > scan.dur) scan = null;
    draw(now);
    const busy = ripples.length || scan || Math.abs(want - lens.s) > 0.005 || Math.hypot(P.x - lens.x, P.y - lens.y) > 0.3;
    if (busy && !document.hidden) requestAnimationFrame(frame); else running = false;
  }
  function kick() { if (!running && !reduced) { running = true; last = performance.now(); requestAnimationFrame(frame); } }

  size();
  addEventListener("resize", size);
  if (reduced || !finePointer) return; // still a nice static grid
  onMove(kick);
  document.addEventListener("visibilitychange", kick);
  gridPulse = (x, y, color) => { ripples.push({ x, y, t0: performance.now(), color }); kick(); };
  // Every so often, a slow scan sweeps the room.
  setInterval(() => { if (!document.hidden) { scan = { t0: performance.now(), dur: 2600 }; kick(); } }, 11000);
  setTimeout(() => { scan = { t0: performance.now(), dur: 2600 }; kick(); }, 1800);
}

/* ============================ Sounds on hover/press ============================ */

function initSounds() {
  let current = null;
  addEventListener("pointerover", (e) => {
    const el = e.target.closest?.(CLICKABLE);
    if (el && el !== current) { try { sfx.hover(); } catch {} }
    current = el || null;
  }, { passive: true });
  addEventListener("pointerdown", (e) => {
    if (e.target.closest?.(CLICKABLE)) { try { sfx.press(); } catch {} }
  }, { passive: true });
}

/* ============================ Text effects ============================ */

const GLYPHS = "ABCDEFGHJKLMNPQRSTUVWXYZ0123456789#%&*+/<>";

// Letters scramble, then settle left to right into `text`.
export function scramble(el, text = el.textContent, duration = 420) {
  if (reduced) { el.textContent = text; return; }
  clearInterval(el._scr);
  const start = performance.now();
  el._scr = setInterval(() => {
    const t = Math.min(1, (performance.now() - start) / duration);
    const settled = Math.floor(t * text.length);
    el.textContent = [...text].map((ch, i) =>
      i < settled || ch === " " ? ch : GLYPHS[(Math.random() * GLYPHS.length) | 0]).join("");
    if (t >= 1) { clearInterval(el._scr); el.textContent = text; }
  }, 32);
}

// Puts html into el and brings it in word by word (rise + unblur).
export function wordsIn(el, html) {
  el.innerHTML = html;
  if (reduced) return;
  let i = 0;
  [...el.childNodes].forEach((n) => {
    if (n.nodeType === 3) {
      const frag = document.createDocumentFragment();
      n.textContent.split(/(\s+)/).forEach((w) => {
        if (!w) return;
        if (/^\s+$/.test(w)) { frag.append(w); return; }
        const s = document.createElement("span");
        s.className = "fx-w"; s.style.setProperty("--i", i++); s.textContent = w;
        frag.append(s);
      });
      n.replaceWith(frag);
    } else if (n.nodeType === 1) {
      n.classList.add("fx-w"); n.style.setProperty("--i", i++);
    }
  });
}

// Types text into el, fast.
export function typeIn(el, text, cps = 70) {
  clearInterval(el._type);
  if (reduced || !text) { el.textContent = text || ""; return; }
  let n = 0;
  el.textContent = "";
  el._type = setInterval(() => {
    n += 2;
    el.textContent = text.slice(0, n);
    if (n >= text.length) clearInterval(el._type);
  }, 2000 / cps);
}

// Primary buttons (.fx-wipe): the label scrambles as the laser wipes across.
function initWipeLabels() {
  addEventListener("pointerover", (e) => {
    const b = e.target.closest?.(".fx-wipe");
    if (!b || b.disabled || b.contains(e.relatedTarget)) return;
    const label = b.querySelector(".fx-label") || b;
    if (!label.dataset.text) label.dataset.text = label.textContent;
    // Hold the width so changing letters don't make the button wobble.
    b.style.minWidth = `${b.offsetWidth}px`;
    scramble(label, label.dataset.text, 380);
  }, { passive: true });
}

/* ============================ Spotlight borders ============================ */

function initSpotlight() {
  onMove((e) => {
    const el = e.target.closest?.(".fx-spot");
    if (!el) return;
    const r = el.getBoundingClientRect();
    el.style.setProperty("--x", `${e.clientX - r.left}px`);
    el.style.setProperty("--y", `${e.clientY - r.top}px`);
  });
}

/* ============================ Dialog reveal ============================ */

// The dialog grows as a circle out of whatever opened it, and shrinks back into it.
export function dialogFx(dialog) {
  let origin = null;
  addEventListener("pointerdown", (e) => { if (!dialog.open) origin = e.target.closest?.("button, a, .fx-click"); }, true);
  const show = dialog.showModal.bind(dialog);
  const close = dialog.close.bind(dialog);
  const at = () => {
    const d = dialog.getBoundingClientRect();
    const o = origin && origin.isConnected ? origin.getBoundingClientRect() : null;
    return o ? `${o.left + o.width / 2 - d.left}px ${o.top + o.height / 2 - d.top}px` : "50% 50%";
  };
  dialog.showModal = () => {
    show();
    try { sfx.swoosh("in"); } catch {}
    if (reduced) return;
    dialog.animate(
      [{ clipPath: `circle(0px at ${at()})` }, { clipPath: `circle(150% at ${at()})` }],
      { duration: 650, easing: "cubic-bezier(0.16, 1, 0.3, 1)" });
  };
  let closing = false;
  dialog.close = (value) => {
    if (!dialog.open || closing) return;
    try { sfx.swoosh("out", 0.34); } catch {}
    if (reduced) { close(value); return; }
    closing = true;
    dialog.classList.add("closing");
    const a = dialog.animate(
      [{ clipPath: `circle(150% at ${at()})` }, { clipPath: `circle(0px at ${at()})` }],
      { duration: 420, easing: "cubic-bezier(0.6, 0, 0.3, 1)", fill: "forwards" });
    a.finished.then(() => { close(value); a.cancel(); dialog.classList.remove("closing"); closing = false; });
  };
  // Esc: close with the same animation instead of instantly.
  dialog.addEventListener("cancel", (e) => { e.preventDefault(); dialog.close(); });
}

/* ============================ Eyes follow the cursor ============================ */

function initEyes() {
  if (reduced || !finePointer) return;
  let raf = 0;
  onMove(() => {
    if (raf) return;
    raf = requestAnimationFrame(() => {
      raf = 0;
      document.querySelectorAll(".fx-eyes .ww-eyes").forEach((eyes) => {
        const r = eyes.ownerSVGElement.getBoundingClientRect();
        const cx = r.left + r.width / 2, cy = r.top + r.height * 0.55;
        const dx = P.x - cx, dy = P.y - cy, d = Math.hypot(dx, dy) || 1;
        const k = Math.min(1, d / 400) * 18;  // SVG units, the eyes are r=36
        eyes.setAttribute("transform", `translate(${(dx / d) * k} ${(dy / d) * k})`);
      });
    });
  });
}

/* ============================ Start ============================ */

export function init({ grid = true, cursor = true, sounds = true } = {}) {
  if (grid) initGrid();
  if (cursor) initCursor();
  if (sounds) initSounds();
  initWipeLabels();
  initSpotlight();
  initEyes();
}
