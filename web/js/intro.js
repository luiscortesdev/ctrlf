import * as THREE from "three";
import * as sfx from "./sound.js";
import * as fx from "./fx.js";
import * as brand from "./brand.js";

brand.mount();
fx.init({ grid: false }); // the 3D scene is the background here

const gsap = window.gsap;
const $ = (id) => document.getElementById(id);

/* =========================================================
   Scene: a stylized desk built from simple shapes
   ========================================================= */

const canvas = $("scene");
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, powerPreference: "high-performance" });
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
renderer.setSize(window.innerWidth, window.innerHeight);
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.35;
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFShadowMap;

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x0b0f19);
scene.fog = new THREE.Fog(0x0b0f19, 9, 20);

const camera = new THREE.PerspectiveCamera(40, window.innerWidth / window.innerHeight, 0.1, 100);
camera.position.set(0, 7.5, 10);
const lookTarget = new THREE.Vector3(0, 0, 0);

// Lights
scene.add(new THREE.AmbientLight(0xb8c0d0, 0.7));
const key = new THREE.DirectionalLight(0xfff1e0, 2.4);
key.position.set(4, 8, 5);
key.castShadow = true;
key.shadow.mapSize.set(1024, 1024);
key.shadow.camera.left = -6; key.shadow.camera.right = 6;
key.shadow.camera.top = 6; key.shadow.camera.bottom = -6;
key.shadow.radius = 4;
scene.add(key);
const rim = new THREE.DirectionalLight(0x8fa4c8, 0.6);
rim.position.set(-6, 4, -6);
scene.add(rim);

// Materials
const mat = (color, rough = 0.7, metal = 0) => new THREE.MeshStandardMaterial({ color, roughness: rough, metalness: metal });
const M = {
  desk: mat(0x1e293b, 0.8),
  floor: mat(0x080b13, 1),
  laptop: mat(0x2b3650, 0.45, 0.4),
  screen: new THREE.MeshStandardMaterial({ color: 0x14161a, emissive: 0x1d2a3a, emissiveIntensity: 0.6, roughness: 0.3 }),
  metal: mat(0xcfc8bb, 0.3, 0.9),
  wallet: mat(0x4a3428, 0.75),
  mug: mat(0xe4ded3, 0.5),
  white: mat(0xf2efe9, 0.35),
  dark: mat(0x1a2233, 0.6, 0.3),
};

function shadowy(o) { o.traverse((c) => { if (c.isMesh) { c.castShadow = true; c.receiveShadow = true; } }); return o; }

// Floor + desk
const floor = new THREE.Mesh(new THREE.PlaneGeometry(60, 60), M.floor);
floor.rotation.x = -Math.PI / 2;
floor.position.y = -2.4;
floor.receiveShadow = true;
scene.add(floor);

const desk = shadowy(new THREE.Mesh(new THREE.BoxGeometry(9, 0.2, 5), M.desk));
desk.position.y = -0.1;
scene.add(desk);

// Laptop
const laptop = new THREE.Group();
const lapBase = new THREE.Mesh(new THREE.BoxGeometry(2.2, 0.07, 1.5), M.laptop);
lapBase.position.y = 0.035;
const lapScreen = new THREE.Group();
const lid = new THREE.Mesh(new THREE.BoxGeometry(2.2, 1.45, 0.05), M.laptop);
lid.position.y = 0.725;
const glass = new THREE.Mesh(new THREE.PlaneGeometry(2.0, 1.25), M.screen);
glass.position.set(0, 0.725, 0.03);
lapScreen.add(lid, glass);
lapScreen.position.set(0, 0.07, -0.75);
lapScreen.rotation.x = -0.32;
laptop.add(lapBase, lapScreen);
laptop.position.set(0.3, 0, -0.5);
scene.add(shadowy(laptop));

// Keys: ring + two blades
const keys = new THREE.Group();
const ring = new THREE.Mesh(new THREE.TorusGeometry(0.16, 0.025, 12, 32), M.metal);
ring.rotation.x = Math.PI / 2;
ring.position.y = 0.03;
const blade1 = new THREE.Mesh(new THREE.BoxGeometry(0.42, 0.03, 0.09), M.metal);
blade1.position.set(0.32, 0.03, 0.05);
blade1.rotation.y = -0.25;
const blade2 = new THREE.Mesh(new THREE.BoxGeometry(0.36, 0.03, 0.08), M.metal);
blade2.position.set(0.2, 0.05, -0.2);
blade2.rotation.y = 0.7;
keys.add(ring, blade1, blade2);
keys.position.set(-1.5, 0, 0.8);
scene.add(shadowy(keys));

// Wallet
const wallet = new THREE.Mesh(new THREE.BoxGeometry(0.95, 0.12, 0.62), M.wallet);
wallet.position.set(2.4, 0.06, 0.9);
wallet.rotation.y = -0.25;
scene.add(shadowy(wallet));

// Mug
const mug = new THREE.Group();
const cup = new THREE.Mesh(new THREE.CylinderGeometry(0.3, 0.27, 0.55, 32), M.mug);
cup.position.y = 0.275;
const handle = new THREE.Mesh(new THREE.TorusGeometry(0.14, 0.035, 10, 24, Math.PI), M.mug);
handle.position.set(0.3, 0.28, 0);
handle.rotation.z = -Math.PI / 2;
mug.add(cup, handle);
mug.position.set(-2.8, 0, -0.9);
scene.add(shadowy(mug));

// Earbuds case
const buds = new THREE.Mesh(new THREE.CapsuleGeometry(0.16, 0.22, 8, 16), M.white);
buds.rotation.z = Math.PI / 2;
buds.position.set(1.1, 0.16, 1.45);
scene.add(shadowy(buds));

// Glasses
const glasses = new THREE.Group();
[-0.2, 0.2].forEach((x) => {
  const lens = new THREE.Mesh(new THREE.TorusGeometry(0.14, 0.02, 10, 28), M.dark);
  lens.rotation.x = Math.PI / 2;
  lens.position.set(x, 0.03, 0);
  glasses.add(lens);
});
const bridge = new THREE.Mesh(new THREE.BoxGeometry(0.12, 0.02, 0.02), M.dark);
bridge.position.y = 0.03;
glasses.add(bridge);
glasses.position.set(2.6, 0, -1.0);
glasses.rotation.y = 0.4;
scene.add(shadowy(glasses));

// Laser rig: base, post, pan/tilt head
const rig = new THREE.Group();
const rigBase = new THREE.Mesh(new THREE.CylinderGeometry(0.28, 0.32, 0.12, 32), M.dark);
rigBase.position.y = 0.06;
const post = new THREE.Mesh(new THREE.CylinderGeometry(0.05, 0.05, 2.1, 16), M.dark);
post.position.y = 1.1;
rig.add(rigBase, post);
rig.position.set(-3.9, 0, -2.0);
scene.add(shadowy(rig));

const head = new THREE.Group();
const headBox = new THREE.Mesh(new THREE.BoxGeometry(0.26, 0.2, 0.42), M.laptop);
const lensRing = new THREE.Mesh(new THREE.CylinderGeometry(0.06, 0.06, 0.04, 20), new THREE.MeshBasicMaterial({ color: 0xff2e2e }));
lensRing.rotation.x = Math.PI / 2;
lensRing.position.z = 0.22;
head.add(headBox, lensRing);
head.position.set(-3.9, 2.25, -2.0);
scene.add(shadowy(head));

const tracked = [
  { name: "keys", obj: keys, time: "1:42 AM", where: "left of laptop" },
  { name: "wallet", obj: wallet, time: "1:38 AM", where: "right side" },
  { name: "glasses", obj: glasses, time: "12:16 AM", where: "behind laptop" },
  { name: "earbuds", obj: buds, time: "1:31 AM", where: "front of desk" },
  { name: "mug", obj: mug, time: "1:40 AM", where: "far left" },
];

// Detection brackets + label anchors for each object
tracked.forEach((t) => {
  const box = new THREE.Box3().setFromObject(t.obj);
  const size = box.getSize(new THREE.Vector3()).multiplyScalar(1.25).max(new THREE.Vector3(0.3, 0.2, 0.3));
  const center = box.getCenter(new THREE.Vector3());
  const lines = new THREE.LineSegments(
    new THREE.EdgesGeometry(new THREE.BoxGeometry(size.x, size.y, size.z)),
    new THREE.LineBasicMaterial({ color: 0xe2e8f0, transparent: true, opacity: 0 })
  );
  lines.position.copy(center);
  scene.add(lines);
  t.bracket = lines;
  t.anchor = center.clone().add(new THREE.Vector3(0, size.y / 2 + 0.12, 0));
  t.top = new THREE.Vector3(center.x, box.max.y, center.z);

  const el = document.createElement("div");
  el.className = "tag";
  el.innerHTML = `<b>${t.name}</b> · ${t.time}`;
  $("labels").appendChild(el);
  t.label = el;
});
const keysT = tracked[0];

// Scanning plane that sweeps across the desk
const scanMat = new THREE.MeshBasicMaterial({ color: 0xff2e2e, transparent: true, opacity: 0, blending: THREE.AdditiveBlending, depthWrite: false });
const scan = new THREE.Mesh(new THREE.BoxGeometry(0.025, 1.6, 5.2), scanMat);
const scanGlow = new THREE.Mesh(new THREE.PlaneGeometry(0.6, 5.2), new THREE.MeshBasicMaterial({ color: 0xff2e2e, transparent: true, opacity: 0, blending: THREE.AdditiveBlending, depthWrite: false }));
scanGlow.rotation.x = -Math.PI / 2;
scanGlow.position.y = 0.802 - 0.8;
const scanGroup = new THREE.Group();
scan.position.y = 0.8;
scanGroup.add(scan, scanGlow);
scanGroup.position.x = -4.6;
scene.add(scanGroup);

// Laser beam from the rig head to the keys
const beamGroup = new THREE.Group();
const headPos = head.position.clone();
const targetPos = keysT.top.clone().add(new THREE.Vector3(0, 0.01, 0));
const dir = targetPos.clone().sub(headPos);
const beamLen = dir.length();
beamGroup.position.copy(headPos);
beamGroup.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), dir.clone().normalize());
const core = new THREE.Mesh(new THREE.CylinderGeometry(0.012, 0.012, beamLen, 8), new THREE.MeshBasicMaterial({ color: 0xffd6d6 }));
core.position.y = beamLen / 2;
const glow = new THREE.Mesh(new THREE.CylinderGeometry(0.055, 0.055, beamLen, 12), new THREE.MeshBasicMaterial({ color: 0xff2e2e, transparent: true, opacity: 0.35, blending: THREE.AdditiveBlending, depthWrite: false }));
glow.position.y = beamLen / 2;
beamGroup.add(core, glow);
beamGroup.scale.set(1, 0.0001, 1);
beamGroup.visible = false;
scene.add(beamGroup);

const dot = new THREE.Mesh(new THREE.SphereGeometry(0.05, 16, 16), new THREE.MeshBasicMaterial({ color: 0xff2e2e }));
dot.position.copy(targetPos);
dot.scale.setScalar(0.001);
scene.add(dot);
const dotLight = new THREE.PointLight(0xff2e2e, 0, 3, 2);
dotLight.position.copy(targetPos).add(new THREE.Vector3(0, 0.25, 0));
scene.add(dotLight);

// Head aim: from facing forward to pointing at the keys
const qStart = head.quaternion.clone();
const tmp = new THREE.Object3D();
tmp.position.copy(head.position);
tmp.lookAt(targetPos);
const qEnd = tmp.quaternion.clone();
const aim = { p: 0 };

/* =========================================================
   Render loop
   ========================================================= */

let running = true;
const v = new THREE.Vector3();

function frame(t) {
  if (!running) return;
  camera.lookAt(lookTarget);
  head.quaternion.slerpQuaternions(qStart, qEnd, aim.p);

  // Glow flicker on the beam
  glow.material.opacity = 0.3 + Math.sin(t * 0.02) * 0.05;

  // Pin labels to their objects
  const w = window.innerWidth, h = window.innerHeight;
  tracked.forEach((tr) => {
    v.copy(tr.anchor).project(camera);
    tr.label.style.left = `${(v.x * 0.5 + 0.5) * w}px`;
    tr.label.style.top = `${(-v.y * 0.5 + 0.5) * h}px`;
  });

  renderer.render(scene, camera);
  requestAnimationFrame(frame);
}

window.addEventListener("resize", () => {
  camera.aspect = window.innerWidth / window.innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(window.innerWidth, window.innerHeight);
});

/* =========================================================
   Text helpers
   ========================================================= */

const line = $("line");

// Builds a line from words; words wrapped in * are red.
function setLine(text) {
  line.innerHTML = text.split(" ").map((w) => {
    const hot = w.startsWith("*");
    const hl = w.startsWith("^");
    const clean = w.replace(/[*^]/g, "");
    const inner = hl ? `<em class="hl">${clean}</em>` : hot ? `<em>${clean}</em>` : clean;
    return `<span class="w">${inner}</span>`;
  }).join(" ");
  fitLine();
  return line.querySelectorAll(".w");
}

// Keep every line on one row: start at the normal size, then shrink the
// font until the text fits inside the screen (7vw margin on each side).
function fitLine() {
  line.style.fontSize = "";
  const max = window.innerWidth * 0.86;
  let size = parseFloat(getComputedStyle(line).fontSize);
  while (line.scrollWidth > max && size > 18) {
    size -= 2;
    line.style.fontSize = `${size}px`;
  }
}
window.addEventListener("resize", fitLine);

// Adds "show this line" to the timeline at `at`, one tick per word.
function lineIn(tl, text, at, stagger = 0.11) {
  tl.call(() => {
    const words = setLine(text);
    gsap.to(words, { opacity: 1, y: 0, duration: 0.55, ease: "expo.out", stagger });
    words.forEach((_, i) => setTimeout(() => sfx.tick(0.9 + i * 0.04), i * stagger * 1000));
  }, null, at);
}

function lineOut(tl, at) {
  tl.call(() => {
    gsap.to(line.querySelectorAll(".w"), { opacity: 0, y: "-0.3em", duration: 0.35, ease: "power2.in", stagger: 0.03 });
  }, null, at);
}

function showBracket(tr, hot = false) {
  gsap.to(tr.bracket.material, { opacity: hot ? 0.95 : 0.55, duration: 0.3 });
  if (hot) tr.bracket.material.color.set(0xff2e2e);
}

/* =========================================================
   Timeline (~17.5 seconds). Edit the times here to retime it.
   ========================================================= */

let stopHum = null;
let finished = false;

function buildTimeline() {
  const tl = gsap.timeline({ paused: true });

  // 0.0  Hum starts, a laser line draws across the dark screen
  tl.call(() => { stopHum = sfx.hum(); }, null, 0);
  tl.to("#rule", { width: "62vw", duration: 1.1, ease: "expo.inOut" }, 0.15);
  tl.call(() => sfx.tick(1.4), null, 0.2);

  // 0.9  "Everyone loses things."
  tl.call(() => line.classList.add("center"), null, 0.85);
  lineIn(tl, "^Everyone loses things.", 0.9, 0.14);
  tl.to("#rule", { width: 0, opacity: 0, duration: 0.6, ease: "expo.in" }, 2.2);
  lineOut(tl, 2.6);
  tl.call(() => line.classList.remove("center"), null, 3.1);

  // 2.8  The desk fades in, camera glides down
  tl.to(canvas, { opacity: 1, duration: 1.4, ease: "power2.out" }, 2.8);
  tl.to(camera.position, { x: 0.4, y: 4.4, z: 6.6, duration: 6, ease: "power2.inOut" }, 2.8);

  // 3.3  "Keys. Wallet. Glasses." and each one lights up
  lineIn(tl, "*Keys.* *Wallet.* *Glasses.*", 3.3, 0.45);
  tl.call(() => showBracket(tracked[0]), null, 3.35);
  tl.call(() => showBracket(tracked[1]), null, 3.8);
  tl.call(() => showBracket(tracked[2]), null, 4.25);
  lineOut(tl, 5.3);

  // 5.5  The scan sweeps the desk and finds everything
  tl.call(() => sfx.sweep(1.8), null, 5.5);
  tl.to([scanMat, scan.material], { opacity: 0.6, duration: 0.2 }, 5.5);
  tl.to(scanGlow.material, { opacity: 0.18, duration: 0.2 }, 5.5);
  tl.to(scanGroup.position, {
    x: 4.6, duration: 1.8, ease: "power1.inOut",
    onUpdate: () => {
      tracked.forEach((tr) => {
        if (!tr.scanned && scanGroup.position.x > tr.anchor.x) {
          tr.scanned = true;
          showBracket(tr);
          sfx.tick(1.6);
        }
      });
    },
  }, 5.5);
  tl.to([scanMat, scanGlow.material], { opacity: 0, duration: 0.3 }, 7.2);
  lineIn(tl, "It sees everything you set down,", 5.7, 0.08);

  // 7.6  Labels with times: "and remembers where."
  lineOut(tl, 7.5);
  tl.to(".tag", { opacity: 1, duration: 0.4, stagger: 0.12 }, 7.7);
  lineIn(tl, "and *remembers* where.", 7.9);
  lineOut(tl, 9.6);

  // 9.8  "Then you just ask." + the question types out
  lineIn(tl, "Then you just ask.", 9.8);
  tl.to("#query", { opacity: 1, y: 0, duration: 0.4, ease: "power2.out" }, 10.1);
  tl.call(() => typeQuery("Where are my keys?"), null, 10.2);
  tl.to(camera.position, { x: -1.1, y: 2.9, z: 4.3, duration: 2.6, ease: "power2.inOut" }, 10.2);
  tl.to(lookTarget, { x: -1.6, y: 0.4, z: 0.2, duration: 2.6, ease: "power2.inOut" }, 10.2);
  tl.to(".tag", { opacity: (i) => (i === 0 ? 1 : 0.25), duration: 0.4 }, 10.6);

  // 11.4  Rig turns, laser charges, beam fires and snaps onto the keys
  tl.to(aim, { p: 1, duration: 0.8, ease: "power3.inOut" }, 11.2);
  tl.call(() => sfx.laserCharge(0.7), null, 11.4);
  tl.call(() => { beamGroup.visible = true; }, null, 12.05);
  tl.to(beamGroup.scale, { y: 1, duration: 0.14, ease: "power4.out" }, 12.05);
  tl.to(dot.scale, { x: 1, y: 1, z: 1, duration: 0.2, ease: "back.out(3)" }, 12.15);
  tl.to(dotLight, { intensity: 8, duration: 0.15 }, 12.15);
  tl.call(() => {
    showBracket(keysT, true);
    keysT.label.classList.add("hot");
    keysT.label.innerHTML = `<b>keys</b> · ${keysT.where}`;
  }, null, 12.15);
  lineOut(tl, 11.9);
  tl.to("#query", { opacity: 0, duration: 0.3 }, 12.6);

  // 12.6  "It points."
  lineIn(tl, "It *points.*", 12.7);
  lineOut(tl, 14.3);

  // 14.4  Title card: the wolf, its red eye lights, then "wherewolf." wipes in
  tl.to(canvas, { opacity: 0.25, duration: 0.8 }, 14.4);
  tl.to(".tag", { opacity: 0, duration: 0.4 }, 14.4);
  tl.to(camera.position, { x: 0, y: 6, z: 8, duration: 4.2, ease: "power1.inOut" }, 14.4);
  tl.to(lookTarget, { x: 0, y: 0, z: 0, duration: 4.2, ease: "power1.inOut" }, 14.4);
  tl.set("#title", { opacity: 1 }, 14.5);
  tl.set([".title-word .ww-dot", ".title-mark .ww-eye-red"], { scale: 0, transformOrigin: "50% 50%" }, 14.5);
  tl.set(".title-word", { clipPath: "inset(0 100% 0 0)" }, 14.5);
  tl.set([".title-sub", ".title-tag"], { opacity: 0, y: 10 }, 14.5);
  tl.fromTo(".title-mark", { opacity: 0, y: 14, scale: 0.9 }, { opacity: 1, y: 0, scale: 1, duration: 0.7, ease: "expo.out" }, 14.55);
  tl.to(".title-mark .ww-eye-red", { scale: 1, duration: 0.35, ease: "back.out(3)" }, 14.95);
  tl.call(() => sfx.shing(), null, 14.85);
  tl.to(".title-word", { clipPath: "inset(0 0% 0 0)", duration: 0.7, ease: "expo.inOut" }, 15.05);
  tl.to(".title-word .ww-dot", { scale: 1, duration: 0.3, ease: "back.out(4)" }, 15.7);
  tl.call(() => sfx.laserSnap(), null, 15.7);
  tl.to([".title-sub", ".title-tag"], { opacity: 1, y: 0, duration: 0.6, ease: "expo.out", stagger: 0.12 }, 15.9);

  // 17.3  Dive into the red period of "wherewolf." until it fills the screen
  tl.to([".title-sub", ".title-tag", ".title-mark"], { opacity: 0, y: 12, duration: 0.35, ease: "power2.in" }, 17.2);
  tl.call(() => {
    aimZoomAtDot();
    sfx.whoosh(0.7);
    if (stopHum) stopHum(0.6);
  }, null, 17.45);
  tl.to(canvas, { opacity: 0, duration: 0.4, ease: "power2.in" }, 17.45);
  // Fast dive (0.55s), then hold on the red for a beat before the dashboard.
  tl.to(".title-word", { x: () => zoomShift.x, y: () => zoomShift.y, duration: 0.6, ease: "power3.inOut" }, 17.5);
  tl.to(".title-word", { scale: 160, duration: 0.6, ease: "expo.in" }, 17.5);
  tl.to("#flash", { opacity: 1, duration: 0.08 }, 18.05);
  tl.call(finish, null, 18.8);

  return tl;
}

// Point the title's zoom at the middle of the period, so scaling it up
// fills the screen with its red.
// ...and slide it to the middle of the screen on the way in.
const zoomShift = { x: 0, y: 0 };
function aimZoomAtDot() {
  const word = document.querySelector(".title-word");
  const dot = word.querySelector(".ww-dot");
  const a = word.getBoundingClientRect();
  const b = dot.getBoundingClientRect();
  gsap.set(word, { transformOrigin: `${b.left - a.left + b.width / 2}px ${b.top - a.top + b.height / 2}px` });
  zoomShift.x = innerWidth / 2 - (b.left + b.width / 2);
  zoomShift.y = innerHeight / 2 - (b.top + b.height / 2);
}

function typeQuery(text) {
  const el = $("query-text");
  el.textContent = "";
  [...text].forEach((ch, i) => setTimeout(() => {
    el.textContent += ch;
    if (ch !== " ") sfx.tick(1.1 + Math.random() * 0.2);
  }, i * 45));
}

/* =========================================================
   Start, skip, finish
   ========================================================= */

let tl = null;

function finish() {
  if (finished) return;
  finished = true;
  try {
    localStorage.setItem("ctrlf-intro-seen", "1");
    sessionStorage.setItem("ctrlf-arrive", "1");
  } catch {}
  running = false;
  // Free the GPU before the dashboard (the vision model needs it).
  renderer.dispose();
  renderer.forceContextLoss();
  location.href = "dashboard.html";
}

$("skip").addEventListener("click", () => {
  if (tl) tl.kill();
  sfx.whoosh(0.6);
  if (stopHum) stopHum(0.4);
  gsap.to("#flash", { opacity: 1, duration: 0.35, onComplete: finish });
});

const muteBtn = $("mute");
function renderMute() {
  muteBtn.textContent = sfx.isMuted() ? "Sound off" : "Sound on";
  muteBtn.setAttribute("aria-pressed", String(sfx.isMuted()));
}
muteBtn.addEventListener("click", () => { sfx.setMuted(!sfx.isMuted()); renderMute(); });
renderMute();

async function begin() {
  await document.fonts.ready;
  requestAnimationFrame(frame);
  tl = buildTimeline();
  // Debug: ?at=12.5 jumps to that second and freezes there.
  const at = parseFloat(new URLSearchParams(location.search).get("at"));
  if (!Number.isNaN(at)) { tl.seek(at, false); tl.pause(); return; }
  tl.play();
}

(async () => {
  const canPlay = await sfx.unlock();
  if (canPlay) {
    begin();
  } else {
    // Browser blocked autoplay audio: one click to start (launch Chrome
    // with --autoplay-policy=no-user-gesture-required to skip this).
    $("start").hidden = false;
    $("start-btn").addEventListener("click", async () => {
      await sfx.unlock();
      gsap.to("#start", { opacity: 0, duration: 0.4, onComplete: () => { $("start").hidden = true; } });
      begin();
    }, { once: true });
  }
})();
