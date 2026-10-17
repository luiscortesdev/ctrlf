// The wherewolf world: a low-poly night forest with a fork, two paths, a guide wolf,
// and a camera that walks the paths and rises into the sky. Everything is built in
// code (no model files). main.js drives it; this file only knows about the 3D scene.
//
//   init(canvas)            build the scene, start rendering
//   setHover("hunt"|"pack"|null)   light up a path, the wolf looks that way
//   go("hunt"|"pack")       the wolf trots down that path, the camera follows, then rises
//   fly("hunt"|"pack"|"live")  glide through the sky to another sky spot
//   home()                  back to the fork
//   onPick(fn)              fn("hunt"|"pack") when a path is hovered (or null) / clicked
//   screenPoint(name)       where something is on screen ("eye", "hunt", "pack")

import * as THREE from "three";

const gsap = window.gsap;
const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;

let renderer, scene, camera, clock;
const look = new THREE.Vector3();
const paths = {};      // name -> { curve, ribbon, hit, flies, label point }
let wolf, hovered = null, place = "fork", busy = false;
const listeners = { hover: [], pick: [] };

export function onPick(fn) { listeners.pick.push(fn); }
export function onHover(fn) { listeners.hover.push(fn); }
export const where = () => place;
export const isBusy = () => busy;

/* ------------------------------------------------------------------ helpers */

const COL = {
  fog: 0x0e1a28, ground: 0x0e1b22, path: 0x4a5c63, bone: 0xbab8b3, ink: 0x1a191c,
  trees: [0x123a3a, 0x15303d, 0x1b4246, 0x0f2a33, 0x1f474a, 0x173a44],
};

function rand(seed) { // small deterministic RNG so the forest is the same every visit
  let s = seed >>> 0;
  return () => ((s = (s * 1664525 + 1013904223) >>> 0) / 4294967296);
}

function glowTexture(inner = "rgba(255,255,255,1)", outer = "rgba(255,255,255,0)") {
  const c = document.createElement("canvas");
  c.width = c.height = 128;
  const g = c.getContext("2d");
  const grd = g.createRadialGradient(64, 64, 0, 64, 64, 64);
  grd.addColorStop(0, inner); grd.addColorStop(0.25, inner.replace(/[\d.]+\)$/, "0.45)")); grd.addColorStop(1, outer);
  g.fillStyle = grd; g.fillRect(0, 0, 128, 128);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

function curve(points) {
  return new THREE.CatmullRomCurve3(points.map(([x, z, y = 0]) => new THREE.Vector3(x, y, z)), false, "catmullrom", 0.4);
}

function distToCurves(x, z, samples) {
  let best = Infinity;
  for (const p of samples) { const d = (p.x - x) ** 2 + (p.z - z) ** 2; if (d < best) best = d; }
  return Math.sqrt(best);
}

function ribbon(c, width, y, material) {
  const n = 120, pos = [], idx = [];
  const up = new THREE.Vector3(0, 1, 0), side = new THREE.Vector3();
  for (let i = 0; i <= n; i++) {
    const p = c.getPointAt(i / n), t = c.getTangentAt(i / n);
    side.crossVectors(t, up).normalize().multiplyScalar(width / 2);
    pos.push(p.x - side.x, y, p.z - side.z, p.x + side.x, y, p.z + side.z);
    if (i < n) { const a = i * 2; idx.push(a, a + 2, a + 1, a + 1, a + 2, a + 3); }
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
  g.setIndex(idx); g.computeVertexNormals();
  return new THREE.Mesh(g, material);
}

/* ------------------------------------------------------------------ the wolf */

function buildWolf() {
  const mat = new THREE.MeshStandardMaterial({ color: COL.bone, roughness: 0.85, flatShading: true });
  const ink = new THREE.MeshStandardMaterial({ color: COL.ink, roughness: 0.6, flatShading: true });
  const w = new THREE.Group();           // faces -z
  const body = new THREE.Group(); w.add(body);

  const torso = new THREE.Mesh(new THREE.CylinderGeometry(0.2, 0.27, 0.95, 6), mat);
  torso.rotation.x = Math.PI / 2; torso.position.set(0, 0.62, 0.02); body.add(torso);
  const chest = new THREE.Mesh(new THREE.IcosahedronGeometry(0.27, 0), mat);
  chest.scale.set(1, 1.1, 1); chest.position.set(0, 0.66, -0.42); body.add(chest);

  const head = new THREE.Group(); head.position.set(0, 0.92, -0.62); body.add(head);
  const skull = new THREE.Mesh(new THREE.IcosahedronGeometry(0.19, 0), mat);
  skull.scale.set(1, 0.9, 1.05); head.add(skull);
  const snout = new THREE.Mesh(new THREE.ConeGeometry(0.11, 0.32, 5), mat);
  snout.rotation.x = -Math.PI / 2; snout.position.set(0, -0.05, -0.24); head.add(snout);
  const nose = new THREE.Mesh(new THREE.TetrahedronGeometry(0.04, 0), ink);
  nose.position.set(0, -0.04, -0.4); head.add(nose);
  for (const s of [-1, 1]) {
    const ear = new THREE.Mesh(new THREE.ConeGeometry(0.07, 0.2, 4), mat);
    ear.position.set(0.1 * s, 0.2, 0.02); ear.rotation.z = -0.25 * s; head.add(ear);
  }
  // The logo's eyes: one dark, one red (the red one glows a little).
  const eyeL = new THREE.Mesh(new THREE.SphereGeometry(0.03, 8, 6), ink);
  eyeL.position.set(-0.09, 0.03, -0.14); head.add(eyeL);
  const eye = new THREE.Mesh(new THREE.SphereGeometry(0.034, 10, 8), new THREE.MeshBasicMaterial({ color: 0xff2e2e }));
  eye.position.set(0.09, 0.03, -0.14); head.add(eye);
  const eyeGlow = new THREE.Sprite(new THREE.SpriteMaterial({ map: glowTexture("rgba(255,46,46,1)", "rgba(255,46,46,0)"), blending: THREE.AdditiveBlending, depthWrite: false, transparent: true, opacity: 0.9 }));
  eyeGlow.scale.setScalar(0.32); eye.add(eyeGlow);

  const legs = [];
  for (const [x, z] of [[-0.13, -0.38], [0.13, -0.38], [-0.13, 0.36], [0.13, 0.36]]) {
    const hip = new THREE.Group(); hip.position.set(x, 0.55, z); body.add(hip);
    const leg = new THREE.Mesh(new THREE.CylinderGeometry(0.045, 0.035, 0.55, 5), mat);
    leg.position.y = -0.27; hip.add(leg); legs.push(hip);
  }
  const tailPivot = new THREE.Group(); tailPivot.position.set(0, 0.7, 0.48); body.add(tailPivot);
  const tail = new THREE.Mesh(new THREE.ConeGeometry(0.08, 0.5, 5), mat);
  tail.rotation.x = Math.PI * 0.68; tail.position.set(0, -0.12, 0.2); tailPivot.add(tail);

  w.traverse((o) => { if (o.isMesh) o.castShadow = false; });
  w.userData = { head, legs, tailPivot, eye, body, gait: 0, trot: 0, lookYaw: 0, lookPitch: 0 };
  return w;
}

function animateWolf(t, dt) {
  const u = wolf.userData;
  // Legs swing while trotting; idle breathing otherwise.
  u.gait += dt * (6 + 8 * u.trot);
  const swing = Math.sin(u.gait) * 0.65 * u.trot;
  u.legs[0].rotation.x = swing; u.legs[3].rotation.x = swing;
  u.legs[1].rotation.x = -swing; u.legs[2].rotation.x = -swing;
  u.body.position.y = Math.abs(Math.sin(u.gait)) * 0.05 * u.trot + Math.sin(t * 1.6) * 0.008 * (1 - u.trot);
  u.tailPivot.rotation.y = Math.sin(t * 2.2) * 0.25;
  u.tailPivot.rotation.x = -0.2 * u.trot;
  u.head.rotation.y += (u.lookYaw - u.head.rotation.y) * Math.min(1, dt * 6);
  u.head.rotation.x += (u.lookPitch - u.head.rotation.x) * Math.min(1, dt * 5);
}

/* ------------------------------------------------------------------ build the world */

export function init(canvas) {
  renderer = new THREE.WebGLRenderer({ canvas, antialias: true, powerPreference: "high-performance" });
  renderer.setPixelRatio(Math.min(devicePixelRatio, 1.5));
  renderer.setSize(innerWidth, innerHeight);
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.3;

  scene = new THREE.Scene();
  scene.fog = new THREE.FogExp2(COL.fog, 0.042);
  camera = new THREE.PerspectiveCamera(fitFov(), innerWidth / innerHeight, 0.1, 400);
  clock = new THREE.Clock();

  // Sky: a dome that goes from fog blue at the horizon to near black overhead.
  const sky = new THREE.Mesh(new THREE.SphereGeometry(220, 32, 16), new THREE.ShaderMaterial({
    side: THREE.BackSide, fog: false, depthWrite: false,
    uniforms: { low: { value: new THREE.Color(COL.fog) }, high: { value: new THREE.Color(0x04060c) } },
    vertexShader: "varying float h; void main(){ h = normalize(position).y; gl_Position = projectionMatrix*modelViewMatrix*vec4(position,1.0); }",
    fragmentShader: "uniform vec3 low; uniform vec3 high; varying float h; void main(){ gl_FragColor = vec4(mix(low, high, smoothstep(-0.05, 0.55, h)), 1.0); }",
  }));
  scene.add(sky);

  // Stars
  const r = rand(7), starPos = [];
  for (let i = 0; i < 1800; i++) {
    const th = r() * Math.PI * 2, y = 0.04 + r() * 0.96, rr = Math.sqrt(1 - y * y);
    starPos.push(Math.cos(th) * rr * 200, y * 200, Math.sin(th) * rr * 200);
  }
  const sg = new THREE.BufferGeometry(); sg.setAttribute("position", new THREE.Float32BufferAttribute(starPos, 3));
  const stars = new THREE.Points(sg, new THREE.PointsMaterial({ color: 0xdfe8ff, size: 1.6, sizeAttenuation: false, transparent: true, opacity: 0.85, fog: false, depthWrite: false }));
  scene.add(stars);

  // Moon, with a soft halo
  const moon = new THREE.Mesh(new THREE.IcosahedronGeometry(7, 1), new THREE.MeshBasicMaterial({ color: 0xf1ede2, fog: false }));
  moon.position.set(-64, 58, -150); scene.add(moon);
  const halo = new THREE.Sprite(new THREE.SpriteMaterial({ map: glowTexture("rgba(190,210,255,0.55)", "rgba(190,210,255,0)"), fog: false, depthWrite: false, transparent: true, blending: THREE.AdditiveBlending }));
  halo.scale.setScalar(80); halo.position.copy(moon.position); scene.add(halo);

  scene.add(new THREE.HemisphereLight(0x6f8fb8, 0x05080d, 0.75));
  const rim = new THREE.DirectionalLight(0x9fb8ff, 0.9); rim.position.set(4, 6, -12); scene.add(rim);
  const front = new THREE.DirectionalLight(0xdfe6ff, 0.55); front.position.set(0, 4, 12); scene.add(front);
  const moonLight = new THREE.DirectionalLight(0xcfdcff, 1.5);
  moonLight.position.copy(moon.position).normalize().multiplyScalar(50); scene.add(moonLight);

  // Paths: a short trunk from the camera to the fork, then two branches.
  paths.hunt = { curve: curve([[0, 2.2], [-2.2, -2.5], [-5.6, -9], [-8.5, -16], [-9.5, -24], [-8.5, -32]]) };
  paths.pack = { curve: curve([[0, 2.2], [2.2, -2.5], [5.6, -9], [8.5, -16], [9.5, -24], [8.5, -32]]) };
  const trunk = curve([[0, 12], [0, 6], [0, 2.2]]);
  const samples = [];
  for (const c of [trunk, paths.hunt.curve, paths.pack.curve]) for (let i = 0; i <= 160; i++) samples.push(c.getPointAt(i / 160));

  // Ground: low-poly, bumpy except along the paths.
  const gg = new THREE.PlaneGeometry(160, 160, 80, 80); gg.rotateX(-Math.PI / 2);
  const gp = gg.attributes.position, gr = rand(3);
  for (let i = 0; i < gp.count; i++) {
    const x = gp.getX(i), z = gp.getZ(i), d = distToCurves(x, z, samples);
    gp.setY(i, Math.min(1, Math.max(0, (d - 1.6) / 3)) * gr() * 0.55);
  }
  gg.computeVertexNormals();
  scene.add(new THREE.Mesh(gg, new THREE.MeshStandardMaterial({ color: COL.ground, roughness: 1, flatShading: true })));

  const pathMat = () => new THREE.MeshStandardMaterial({ color: COL.path, roughness: 1, emissive: 0xff2e2e, emissiveIntensity: 0 });
  scene.add(ribbon(trunk, 1.5, 0.04, new THREE.MeshStandardMaterial({ color: COL.path, roughness: 1 })));
  for (const name of ["hunt", "pack"]) {
    const p = paths[name];
    p.ribbon = ribbon(p.curve, 1.4, 0.05, pathMat()); scene.add(p.ribbon);
    p.hit = ribbon(p.curve, 5.5, 0.3, new THREE.MeshBasicMaterial({ visible: false })); p.hit.userData.path = name; scene.add(p.hit);
    p.labelAt = p.curve.getPointAt(0.3).clone().setY(2.6);
    // Red laser-dot fireflies along the path
    const fr = rand(name === "hunt" ? 11 : 13), fpos = [], base = [];
    for (let i = 0; i < 46; i++) {
      const q = p.curve.getPointAt(0.06 + 0.92 * (i / 46)), side = (fr() - 0.5) * 2.4;
      const t = p.curve.getTangentAt(0.06 + 0.92 * (i / 46));
      const x = q.x - t.z * side, z = q.z + t.x * side, y = 0.35 + fr() * 1.3;
      fpos.push(x, y, z); base.push(y, fr() * 6.28);
    }
    const fg = new THREE.BufferGeometry(); fg.setAttribute("position", new THREE.Float32BufferAttribute(fpos, 3));
    p.flies = new THREE.Points(fg, new THREE.PointsMaterial({ map: glowTexture("rgba(255,60,60,1)", "rgba(255,46,46,0)"), size: 0.32, transparent: true, opacity: 0.55, depthWrite: false, blending: THREE.AdditiveBlending }));
    p.flies.userData.base = base; scene.add(p.flies);
  }

  // Trees: two stacked faceted cones + a trunk, instanced. Never on a path.
  const tr = rand(42), mats = [];
  const tierA = new THREE.ConeGeometry(1, 1, 6), tierB = new THREE.ConeGeometry(1, 1, 6), stem = new THREE.CylinderGeometry(0.12, 0.18, 1, 5);
  const N = 720;
  const meshA = new THREE.InstancedMesh(tierA, new THREE.MeshStandardMaterial({ roughness: 0.9, flatShading: true }), N);
  const meshB = new THREE.InstancedMesh(tierB, new THREE.MeshStandardMaterial({ roughness: 0.9, flatShading: true }), N);
  const meshS = new THREE.InstancedMesh(stem, new THREE.MeshStandardMaterial({ color: 0x1c1a1d, roughness: 1, flatShading: true }), N);
  const m4 = new THREE.Matrix4(), q = new THREE.Quaternion(), s3 = new THREE.Vector3(), p3 = new THREE.Vector3(), c = new THREE.Color();
  let n = 0, tries = 0;
  while (n < N && tries++ < 20000) {
    const x = (tr() - 0.5) * 90, z = 16 - tr() * 80;
    const d = distToCurves(x, z, samples);
    if (d < 1.7 + tr() * 0.8) continue;
    if (Math.abs(x) < 3.2 && z > -1 && z < 13) continue;        // keep the fork open
    const h = 3.2 + tr() * 4.2 + Math.min(4, d * 0.15), rad = 0.85 + tr() * 0.7, rot = tr() * Math.PI;
    q.setFromAxisAngle(new THREE.Vector3(0, 1, 0), rot);
    m4.compose(p3.set(x, 0.5, z), q, s3.set(1, 1, 1)); meshS.setMatrixAt(n, m4);
    m4.compose(p3.set(x, 0.9 + h * 0.32, z), q, s3.set(rad, h * 0.62, rad)); meshA.setMatrixAt(n, m4);
    m4.compose(p3.set(x, 0.9 + h * 0.66, z), q, s3.set(rad * 0.68, h * 0.5, rad * 0.68)); meshB.setMatrixAt(n, m4);
    c.setHex(COL.trees[Math.floor(tr() * COL.trees.length)]);
    meshA.setColorAt(n, c); meshB.setColorAt(n, c.offsetHSL(0, 0, 0.025));
    n++;
  }
  for (const m of [meshA, meshB, meshS]) { m.count = n; m.instanceMatrix.needsUpdate = true; if (m.instanceColor) m.instanceColor.needsUpdate = true; scene.add(m); }

  // Low fog drifting between the trunks
  const fogTex = glowTexture("rgba(150,175,205,0.5)", "rgba(150,175,205,0)");
  const fogs = [];
  const fgr = rand(5);
  for (let i = 0; i < 26; i++) {
    const sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: fogTex, transparent: true, opacity: 0.07 + fgr() * 0.05, depthWrite: false }));
    sp.scale.set(14 + fgr() * 10, 3 + fgr() * 2, 1);
    sp.position.set((fgr() - 0.5) * 50, 0.8 + fgr() * 0.8, 10 - fgr() * 50);
    sp.userData.v = 0.2 + fgr() * 0.4; scene.add(sp); fogs.push(sp);
  }

  // The guide wolf, sitting at the fork looking at you.
  wolf = buildWolf();
  wolf.scale.setScalar(1.35);
  wolf.position.set(0, 0, 3.4); wolf.rotation.y = Math.PI; scene.add(wolf);

  setCamera(FORK.pos, FORK.look);

  // Picking
  const ray = new THREE.Raycaster(), ndc = new THREE.Vector2();
  const pick = (e) => {
    ndc.set((e.clientX / innerWidth) * 2 - 1, -(e.clientY / innerHeight) * 2 + 1);
    ray.setFromCamera(ndc, camera);
    const hit = ray.intersectObjects([paths.hunt.hit, paths.pack.hit])[0];
    return hit ? hit.object.userData.path : null;
  };
  canvas.addEventListener("pointermove", (e) => {
    if (place !== "fork" || busy) return;
    const p = pick(e);
    if (p !== hovered) listeners.hover.forEach((fn) => fn(p));
  });
  canvas.addEventListener("click", (e) => {
    if (place !== "fork" || busy) return;
    const p = pick(e);
    if (p) listeners.pick.forEach((fn) => fn(p));
  });
  addEventListener("resize", () => {
    camera.aspect = innerWidth / innerHeight; camera.fov = fitFov(); camera.updateProjectionMatrix();
    renderer.setSize(innerWidth, innerHeight);
  });

  // Render loop (paused when the tab is hidden)
  let last = performance.now();
  renderer.setAnimationLoop((now) => {
    if (document.hidden) return;
    const dt = Math.min(0.05, (now - last) / 1000); last = now;
    const t = now / 1000;
    for (const name of ["hunt", "pack"]) {
      const fl = paths[name].flies, a = fl.geometry.attributes.position, b = fl.userData.base;
      for (let i = 0; i < a.count; i++) a.setY(i, b[i * 2] + Math.sin(t * 1.3 + b[i * 2 + 1]) * 0.18);
      a.needsUpdate = true;
      const on = hovered === name ? 1 : 0;
      fl.material.opacity += ((on ? 1 : 0.5) - fl.material.opacity) * dt * 4;
      fl.material.size += ((on ? 0.46 : 0.3) - fl.material.size) * dt * 4;
      const em = paths[name].ribbon.material;
      em.emissiveIntensity += ((on ? 0.22 : 0) - em.emissiveIntensity) * dt * 4;
    }
    for (const f of fogs) { f.position.x += f.userData.v * dt; if (f.position.x > 28) f.position.x = -28; }
    stars.rotation.y = t * 0.004;
    animateWolf(t, dt);
    renderer.render(scene, camera);
  });
}

/* ------------------------------------------------------------------ camera moves */

const FORK = { pos: new THREE.Vector3(0, 2.1, 10.5), look: new THREE.Vector3(0, 1.1, -3) };
const SKY = {
  hunt: { pos: new THREE.Vector3(-9, 15, -22), look: new THREE.Vector3(-9, 16.5, -60) },
  pack: { pos: new THREE.Vector3(9, 15, -22), look: new THREE.Vector3(9, 16.5, -60) },
  live: { pos: new THREE.Vector3(0, 17, -12), look: new THREE.Vector3(0, 18.5, -50) },
};

function setCamera(pos, at) { camera.position.copy(pos); look.copy(at); camera.lookAt(look); }

function tweenCamera(toPos, toLook, duration, ease = "power2.inOut") {
  const fromPos = camera.position.clone(), fromLook = look.clone(), o = { t: 0 };
  return new Promise((done) => {
    if (reduced) { setCamera(toPos, toLook); return done(); }
    gsap.to(o, { t: 1, duration, ease, onUpdate: () => {
      camera.position.lerpVectors(fromPos, toPos, o.t); look.lerpVectors(fromLook, toLook, o.t); camera.lookAt(look);
    }, onComplete: done });
  });
}

export function setHover(name) {
  if (place !== "fork" || busy) name = null;
  hovered = name;
  const u = wolf.userData;
  u.lookYaw = name === "hunt" ? -0.75 : name === "pack" ? 0.75 : 0;  // (it faces the camera)
  u.lookPitch = 0;
}

// The wolf trots down the path; the camera follows a few steps behind, then rises.
export async function go(name) {
  if (busy || place !== "fork") return;
  busy = true; hovered = null; setHover(null);
  const c = paths[name].curve, u = wolf.userData;
  if (reduced) { place = name; setCamera(SKY[name].pos, SKY[name].look); busy = false; return; }

  // 1. The wolf turns toward the path, then sets off.
  const startYaw = wolf.rotation.y, tan0 = c.getTangentAt(0.02);
  const yaw = Math.atan2(-tan0.x, -tan0.z);
  await new Promise((r) => gsap.to(wolf.rotation, { y: yaw, duration: 0.45, ease: "power2.inOut", onComplete: r }));
  u.lookYaw = 0;

  // 2. Walk: the wolf leads by a few meters, the camera follows at eye height.
  const o = { s: 0 }, lead = 0.11, camPos = new THREE.Vector3(), target = new THREE.Vector3(), wp = new THREE.Vector3();
  const start = camera.position.clone(), startLook = look.clone();
  await new Promise((r) => gsap.to(o, { s: 0.78, duration: 3.1, ease: "power1.inOut", onUpdate: () => {
    const ws = Math.min(1, o.s + lead);
    wp.copy(c.getPointAt(ws)); wolf.position.set(wp.x, 0, wp.z);
    const t = c.getTangentAt(ws); wolf.rotation.y = Math.atan2(-t.x, -t.z);
    u.trot = Math.min(1, o.s * 8);
    camPos.copy(c.getPointAt(o.s)).setY(1.7);
    target.copy(c.getPointAt(Math.min(1, o.s + 0.16))).setY(0.9);
    const k = Math.min(1, o.s * 5);                       // ease in from the fork view
    camera.position.lerpVectors(start, camPos, k); look.lerpVectors(startLook, target, k); camera.lookAt(look);
  }, onComplete: r }));

  // 3. The wolf stops and looks up at the sky; the camera rises above the trees.
  u.trot = 0; u.lookPitch = 0.6;
  place = name;
  await tweenCamera(SKY[name].pos, SKY[name].look, 2.0, "power2.inOut");
  busy = false;
}

// Glide through the sky between the two sky spots (and the live view).
export async function fly(to) {
  if (busy) return;
  busy = true;
  if (place === "fork") {
    await tweenCamera(SKY[to].pos, SKY[to].look, reduced ? 0 : 1.8, "power2.inOut");
  } else {
    // up and over, a little higher in the middle
    const mid = SKY[place].pos.clone().lerp(SKY[to].pos, 0.5).add(new THREE.Vector3(0, 3, -2));
    const midLook = SKY[place].look.clone().lerp(SKY[to].look, 0.5).add(new THREE.Vector3(0, 3, 0));
    await tweenCamera(mid, midLook, reduced ? 0 : 0.8, "power2.in");
    await tweenCamera(SKY[to].pos, SKY[to].look, reduced ? 0 : 0.9, "power2.out");
  }
  place = to; busy = false;
}

// Back to the fork: glide over, then come down; the wolf is waiting there again.
export async function home() {
  if (busy || place === "fork") return;
  busy = true;
  const u = wolf.userData;
  wolf.position.set(0, 0, 3.4); wolf.rotation.y = Math.PI; u.lookPitch = 0; u.lookYaw = 0; u.trot = 0;
  await tweenCamera(new THREE.Vector3(0, 13, 14), new THREE.Vector3(0, 8, -20), reduced ? 0 : 1.1, "power2.in");
  await tweenCamera(FORK.pos, FORK.look, reduced ? 0 : 1.3, "power2.out");
  place = "fork"; busy = false;
}

// The wolf lifts its head and howls (main.js plays the sound).
export function howl() {
  if (reduced) return;
  const u = wolf.userData;
  u.lookPitch = 0.75;
  setTimeout(() => { u.lookPitch = 0; }, 1800);
}

// Screen position (CSS pixels) of the wolf's red eye or a path label.
// Narrow (phone) screens: widen the view so both paths still fit.
function fitFov() {
  const a = innerWidth / innerHeight, wide = 1.6;
  if (a >= wide) return 50;
  const v = 2 * Math.atan(Math.tan((25 * Math.PI) / 180) * wide / a) * 180 / Math.PI;
  return Math.min(v, 72);
}

export function screenPoint(what) {
  const v = new THREE.Vector3();
  if (what === "eye") wolf.userData.eye.getWorldPosition(v);
  else v.copy(paths[what].labelAt);
  v.project(camera);
  return { x: (v.x * 0.5 + 0.5) * innerWidth, y: (-v.y * 0.5 + 0.5) * innerHeight, visible: v.z < 1 };
}
