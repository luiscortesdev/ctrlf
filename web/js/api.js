// The ONLY file that talks to the backend.
// Live mode talks to the Python app (src/web.py in the repo): it gets the app's
// state from /api/events and sends commands to /api/command, just like typing in Terminal.
// If the app isn't running, it falls back to mock mode so the dashboard still
// works for design and demos (add ?mock to the URL to force it).

const listeners = { item: [], state: [], log: [], tracked: [], target: [], connection: [], teach: [], voice: [], taught: [], laser: [] };

export function on(event, fn) { listeners[event].push(fn); }
function emit(event, data) { listeners[event].forEach((fn) => fn(data)); }

export let mode = "connecting";

export async function connect() {
  const live = await backendReachable();
  mode = live ? "live" : "mock";
  emit("connection", mode);
  if (live) startLive(); else startMock();
  return mode;
}

async function backendReachable() {
  if (new URLSearchParams(location.search).has("mock")) return false;
  try {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 1200);
    const res = await fetch("/api/state", { signal: ctrl.signal, cache: "no-store" });
    clearTimeout(timer);
    return res.ok && (res.headers.get("content-type") || "").includes("json");
  } catch {
    return false;
  }
}

/* ---------------- Public actions ---------------- */

export function videoUrl() { return mode === "live" ? "/video" : null; }
export function teachVideoUrl() { return mode === "live" ? "/video/teach" : null; }

// Anything you could type in the app's Terminal: questions, "learn this",
// "yes"/"no", "cancel", a name, "forget keys", "wipe"...
export async function command(text) {
  if (mode !== "live") return mockCommand(text);
  const res = await fetch("/api/command", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }) });
  if (!res.ok) throw new Error(`app said ${res.status}`);
}

export function ask(text) { return command(text); }

// Push-to-talk: the app records from its own mic and runs Whisper (like SPACE).
export async function listen() {
  if (mode === "live") await fetch("/api/listen", { method: "POST" });
  else mockAsk("where are my keys?");
}

// While teaching: "it's this one" (x, y as 0-1 of the room camera picture).
export function click(x, y) {
  return fetch("/api/click", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ x, y }) }).catch(() => {});
}

// Which items show on the dashboard. The app always looks for everything it
// knows, so in live mode this is a display filter kept in this browser.
export function setTracked(items) {
  saveHidden(items.filter((i) => !i.enabled).map((i) => i.name));
  if (mode === "live") {
    live.tracked = items;
    emit("tracked", items);
  } else {
    mock.tracked = items;
    emit("tracked", items);
    emit("log", { time: new Date(), text: `Now tracking: ${items.filter((i) => i.enabled).map((i) => i.name).join(", ")}` });
  }
}

function loadHidden() { try { return new Set(JSON.parse(localStorage.getItem("ctrlf-hidden") || "[]")); } catch { return new Set(); } }
function saveHidden(names) { try { localStorage.setItem("ctrlf-hidden", JSON.stringify(names)); } catch {} }

// Mock teach mode only (live teaching runs through command(): see teach.js).
export async function teach(name, images) {
  await wait(700);
  if (!mock.tracked.some((t) => t.name === name)) mock.tracked = [...mock.tracked, { name, enabled: true }];
  if (!mock.taught.includes(name)) { mock.taught = [...mock.taught, name]; saveMockTaught(); emit("taught", mock.taught); }
  mock.photos[name] = images[0];
  emit("tracked", mock.tracked);
  const item = { name, visible: true, last_seen: new Date().toISOString(), where: "on the desk", snapshot: images[0], bbox: [0.42, 0.42, 0.58, 0.58] };
  mock.items[name] = item;
  emit("item", { type: "item_update", ...item });
  emit("log", { time: new Date(), text: `Learned "${name}" from ${images.length} photos` });
  return { ok: true };
}

/* ---------------- Live mode ---------------- */

const live = { items: {}, tracked: [], status: "", target: "", said: 0, heard: "", teach: "", voice: "", known: "", laser: null, failures: 0 };

// The app pushes its state the moment it changes (/api/events), so a new object shows
// up here as soon as the app sees it; polling /api/state is only the fallback.
function startLive() {
  if (!window.EventSource) return poll();
  const events = new EventSource("/api/events");
  events.onmessage = (e) => {
    if (live.failures) emit("connection", "live");
    live.failures = 0;
    applyState(JSON.parse(e.data));
  };
  events.onerror = () => {  // it reconnects by itself
    live.failures += 1;
    if (live.failures === 2) emit("connection", "reconnecting");
  };
}

async function poll() {
  try {
    const res = await fetch("/api/state", { cache: "no-store" });
    const s = await res.json();
    if (live.failures) emit("connection", "live");
    live.failures = 0;
    applyState(s);
  } catch {
    live.failures += 1;
    if (live.failures === 2) emit("connection", "reconnecting");
  }
  setTimeout(poll, live.failures ? 1000 : 250);
}

function applyState(s) {
  if (!s.items) return;  // {} until the app's first pass
  // Items: send only the ones that changed.
  const names = new Set();
  for (const it of s.items) {
    names.add(it.name);
    const key = JSON.stringify([it.visible, it.where, it.snapshot]);
    const prev = live.items[it.name];
    if (!prev || prev.key !== key || prev.last_seen !== it.last_seen) {
      emit("item", { type: "item_update", ...it });
      if (prev && prev.visible !== it.visible) {
        emit("log", { time: new Date(), text: `${cap(it.name)} ${it.visible ? "back in view" : "went out of view"}` });
      }
    }
    live.items[it.name] = { key, last_seen: it.last_seen, visible: it.visible };
  }
  for (const name of Object.keys(live.items)) {
    if (!names.has(name)) { delete live.items[name]; emit("item", { type: "item_remove", name }); }
  }

  // Key items: what it was taught (the world page shows only these).
  const taughtKey = JSON.stringify(s.taught);
  if (taughtKey !== live.taughtKey) { live.taughtKey = taughtKey; emit("taught", s.taught); }

  // What it can find: everything it knows + what it was taught.
  const known = JSON.stringify([s.can_find, s.taught]);
  if (known !== live.known) {
    live.known = known;
    const hidden = loadHidden();
    const all = [...new Set([...s.taught, ...s.items.map((i) => i.name), ...s.can_find])];
    live.tracked = all.map((name) => ({ name, enabled: !hidden.has(name) }));
    emit("tracked", live.tracked);
  }

  // Status
  const st = JSON.stringify(s.status);
  if (st !== live.status) {
    live.status = st;
    emit("state", { type: "state", ...s.status });
  }
  const tg = JSON.stringify(s.target);
  if (tg !== live.target) {
    live.target = tg;
    emit("target", s.target ? { name: s.target.name, bbox: s.target.bbox } : { name: null, bbox: null });
  }

  // Activity log: what it heard and what it said.
  const heard = s.heard ? s.heard.time + s.heard.text : "";
  if (heard && heard !== live.heard) {
    live.heard = heard;
    emit("log", { time: new Date(), text: `Heard: "${s.heard.text}"` });
  }
  const newSaid = s.said.filter((m) => m.time + m.text > (live.saidKey || ""));
  if (newSaid.length) {
    live.saidKey = newSaid[newSaid.length - 1].time + newSaid[newSaid.length - 1].text;
    if (live.said) newSaid.forEach((m) => emit("log", { time: new Date(), text: m.text }));
  }
  live.said = 1;

  const tc = JSON.stringify(s.teach);
  if (tc !== live.teach) {
    live.teach = tc;
    emit("teach", { ...(s.teach || {}), active: !!s.teach, message: s.said.length ? s.said[s.said.length - 1].text : "" });
  } else if (s.teach && newSaid.length) {
    emit("teach", { ...s.teach, active: true, message: newSaid[newSaid.length - 1].text });
  }
  if (s.voice !== live.voice) { live.voice = s.voice; emit("voice", s.voice); }
  if (s.laser_connected !== live.laser) { live.laser = s.laser_connected; emit("laser", !!s.laser_connected); }
}

/* ---------------- Mock mode ---------------- */

const mock = {
  tracked: [
    { name: "keys", enabled: true },
    { name: "wallet", enabled: true },
    { name: "earbuds case", enabled: true },
    { name: "glasses", enabled: true },
    { name: "water bottle", enabled: true },
    { name: "phone", enabled: false },
  ],
  items: {},
  busy: false,
  taught: loadMockTaught(),   // demo mode remembers what you taught (in this browser)
  photos: {},
};

function loadMockTaught() { try { return JSON.parse(localStorage.getItem("ww-mock-taught") || "[]"); } catch { return []; } }
function saveMockTaught() { try { localStorage.setItem("ww-mock-taught", JSON.stringify(mock.taught)); } catch {} }

// A photo of a taught item: the one from teaching (live: saved by the app).
export function taughtPhoto(name) {
  if (mode === "live") return `/taught/${encodeURIComponent(name.replace(/ /g, "_"))}.jpg`;
  return mock.photos[name] || null;
}

// Forget a key item (live: the app's "forget" command).
export async function forget(name) {
  if (mode === "live") return command(`forget ${name}`);
  mock.taught = mock.taught.filter((n) => n !== name); saveMockTaught();
  delete mock.items[name]; emit("item", { type: "item_remove", name }); emit("taught", mock.taught);
}

function minutesAgo(m) { return new Date(Date.now() - m * 60000).toISOString(); }

function mockCommand(text) {
  const t = text.toLowerCase();
  if (/^(learn|this is|teach|remember)/.test(t)) {
    emit("log", { time: new Date(), text: "Demo mode: teaching needs the app running" });
    return;
  }
  return mockAsk(text);
}

function startMock() {
  const hidden = loadHidden();
  mock.tracked = mock.tracked.map((t) => ({ ...t, enabled: hidden.size ? !hidden.has(t.name) : t.enabled }));
  const seed = [
    { name: "keys", visible: true, last_seen: minutesAgo(0), where: "left of the laptop", bbox: [0.16, 0.6, 0.27, 0.74] },
    { name: "wallet", visible: true, last_seen: minutesAgo(0), where: "right side of the desk", bbox: [0.72, 0.64, 0.84, 0.75] },
    { name: "earbuds case", visible: false, last_seen: minutesAgo(11), where: "in front of the laptop", bbox: [0.46, 0.72, 0.53, 0.8] },
    { name: "glasses", visible: false, last_seen: minutesAgo(47), where: "behind the monitor", bbox: [0.6, 0.3, 0.7, 0.38] },
    { name: "water bottle", visible: true, last_seen: minutesAgo(0), where: "far left of the desk", bbox: [0.08, 0.22, 0.17, 0.48] },
  ];
  seed.forEach((it) => { mock.items[it.name] = it; emit("item", { type: "item_update", ...it }); });
  emit("tracked", mock.tracked);
  emit("taught", mock.taught);
  emit("state", { type: "state", state: "IDLE" });
  emit("log", { time: new Date(), text: "Mock mode: backend not connected" });

  // Keep "seen now" items fresh and occasionally hide/show the wallet.
  setInterval(() => {
    Object.values(mock.items).forEach((it) => {
      if (it.visible) { it.last_seen = new Date().toISOString(); emit("item", { type: "item_update", ...it }); }
    });
  }, 5000);

  setInterval(() => {
    if (mock.busy) return;
    const w = mock.items.wallet;
    w.visible = !w.visible;
    w.last_seen = new Date().toISOString();
    emit("item", { type: "item_update", ...w });
    emit("log", { time: new Date(), text: w.visible ? "Wallet back in view" : "Wallet went out of view" });
  }, 22000);
}

function resolveTarget(text) {
  const t = text.toLowerCase();
  const names = Object.keys(mock.items).sort((a, b) => b.length - a.length);
  return names.find((n) => t.includes(n)) || names.find((n) => n.split(" ").some((w) => w.length > 3 && t.includes(w)));
}

function wait(ms) { return new Promise((r) => setTimeout(r, ms)); }

async function mockAsk(text) {
  if (mock.busy) return;
  mock.busy = true;
  emit("log", { time: new Date(), text: `Heard: "${text}"` });
  emit("state", { type: "state", state: "QUERYING", heard: text });
  await wait(700);

  const name = resolveTarget(text);
  const item = name && mock.items[name];

  if (!item) {
    emit("state", { type: "state", state: "REPORTING", result: "unknown", message: "I haven't seen that yet." });
    emit("log", { time: new Date(), text: "No match in memory" });
  } else if (!item.visible) {
    const when = new Date(item.last_seen).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
    emit("state", { type: "state", state: "REPORTING", target: name, result: "hidden", message: `Your ${name} ${name.endsWith("s") ? "are" : "is"} out of sight. Last seen at ${when}, ${item.where}.` });
    emit("log", { time: new Date(), text: `${cap(name)} hidden, showing last-seen snapshot` });
  } else {
    emit("state", { type: "state", state: "TRACKING", target: name });
    emit("target", { type: "target", name, bbox: item.bbox });
    emit("log", { time: new Date(), text: `Aiming laser at ${name}` });
    await wait(1600);
    emit("state", { type: "state", state: "REPORTING", target: name, result: "found", message: `Your ${name} ${name.endsWith("s") ? "are" : "is"} ${item.where}.` });
    emit("log", { time: new Date(), text: `Locked on ${name}` });
  }

  await wait(5000);
  emit("target", { type: "target", name: null, bbox: null });
  emit("state", { type: "state", state: "IDLE" });
  mock.busy = false;
}

function cap(s) { return s.charAt(0).toUpperCase() + s.slice(1); }
