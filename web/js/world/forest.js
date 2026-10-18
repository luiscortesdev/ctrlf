// The wherewolf world: a low-poly night forest with a fork, two paths and a red beacon,
// the app page's background. Everything is built in code (no model files).
//
//   init(canvas)            build the scene, start rendering
//   drift(on)               the camera drifts slowly, like standing at the edge of the forest
//   screenPoint()           where the beacon is on screen (the intro's red shrinks into it)

import * as THREE from "three";

const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;

let renderer, scene, camera, clock;
const look = new THREE.Vector3();
const paths = {};      // name -> { curve, flies }
let beacon;

let drifting = false, driftStart = null;
export function drift(on = true) { drifting = on; driftStart = null; }

/* ------------------------------------------------------------------ helpers */

const COL = {
  fog: 0x0e1a28, ground: 0x0e1b22, path: 0x4a5c63,
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
  moon.position.set(66, 40, -150); scene.add(moon);
  const halo = new THREE.Sprite(new THREE.SpriteMaterial({ map: glowTexture("rgba(190,210,255,0.55)", "rgba(190,210,255,0)"), fog: false, depthWrite: false, transparent: true, blending: THREE.AdditiveBlending }));
  halo.scale.setScalar(80); halo.position.copy(moon.position); scene.add(halo);

  scene.add(new THREE.HemisphereLight(0x6f8fb8, 0x05080d, 0.75));
  const rim = new THREE.DirectionalLight(0x9fb8ff, 0.9); rim.position.set(4, 6, -12); scene.add(rim);
  const front = new THREE.DirectionalLight(0xdfe6ff, 0.55); front.position.set(0, 4, 12); scene.add(front);
  const moonLight = new THREE.DirectionalLight(0xcfdcff, 1.5);
  moonLight.position.copy(moon.position).normalize().multiplyScalar(50); scene.add(moonLight);

  // Paths: a short trunk from the camera to the fork, then two branches.
  paths.add = { curve: curve([[0, 2.2], [-2.2, -2.5], [-5.6, -9], [-8.5, -16], [-9.5, -24], [-8.5, -32]]) };
  paths.items = { curve: curve([[0, 2.2], [2.2, -2.5], [5.6, -9], [8.5, -16], [9.5, -24], [8.5, -32]]) };
  const trunk = curve([[0, 12], [0, 6], [0, 2.2]]);
  const samples = [];
  for (const c of [trunk, paths.add.curve, paths.items.curve]) for (let i = 0; i <= 160; i++) samples.push(c.getPointAt(i / 160));

  // Ground: low-poly, bumpy except along the paths.
  const gg = new THREE.PlaneGeometry(160, 160, 80, 80); gg.rotateX(-Math.PI / 2);
  const gp = gg.attributes.position, gr = rand(3);
  for (let i = 0; i < gp.count; i++) {
    const x = gp.getX(i), z = gp.getZ(i), d = distToCurves(x, z, samples);
    gp.setY(i, Math.min(1, Math.max(0, (d - 1.6) / 3)) * gr() * 0.55);
  }
  gg.computeVertexNormals();
  scene.add(new THREE.Mesh(gg, new THREE.MeshStandardMaterial({ color: COL.ground, roughness: 1, flatShading: true })));

  const pathMat = new THREE.MeshStandardMaterial({ color: COL.path, roughness: 1 });
  scene.add(ribbon(trunk, 1.5, 0.04, pathMat));
  for (const name of ["add", "items"]) {
    const p = paths[name];
    scene.add(ribbon(p.curve, 1.4, 0.05, pathMat));
    // Red laser-dot fireflies along the path
    const fr = rand(name === "add" ? 11 : 13), fpos = [], base = [];
    for (let i = 0; i < 46; i++) {
      const q = p.curve.getPointAt(0.06 + 0.92 * (i / 46)), side = (fr() - 0.5) * 2.4;
      const t = p.curve.getTangentAt(0.06 + 0.92 * (i / 46));
      const x = q.x - t.z * side, z = q.z + t.x * side, y = 0.35 + fr() * 1.3;
      fpos.push(x, y, z); base.push(y, fr() * 6.28);
    }
    const fg = new THREE.BufferGeometry(); fg.setAttribute("position", new THREE.Float32BufferAttribute(fpos, 3));
    p.flies = new THREE.Points(fg, new THREE.PointsMaterial({ map: glowTexture("rgba(255,60,60,1)", "rgba(255,46,46,0)"), size: 0.3, transparent: true, opacity: 0.5, depthWrite: false, blending: THREE.AdditiveBlending }));
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

  // The beacon: a red laser dot hovering where the paths split (the intro lands in it).
  beacon = new THREE.Group(); beacon.position.set(0, 0.8, 3.2); scene.add(beacon);
  const core = new THREE.Mesh(new THREE.SphereGeometry(0.07, 16, 12), new THREE.MeshBasicMaterial({ color: 0xff3b3b }));
  const glow = new THREE.Sprite(new THREE.SpriteMaterial({ map: glowTexture("rgba(255,46,46,1)", "rgba(255,46,46,0)"), blending: THREE.AdditiveBlending, depthWrite: false, transparent: true, opacity: 0.85 }));
  glow.scale.setScalar(0.9);
  const pool = new THREE.Mesh(new THREE.CircleGeometry(0.6, 32), new THREE.MeshBasicMaterial({ map: glowTexture("rgba(255,46,46,0.5)", "rgba(255,46,46,0)"), transparent: true, depthWrite: false, blending: THREE.AdditiveBlending }));
  pool.rotation.x = -Math.PI / 2; pool.position.y = -0.73;
  beacon.add(core, glow, pool);
  beacon.userData = { glow, pool };

  camera.position.copy(FORK.pos); look.copy(FORK.look); camera.lookAt(look);

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
    for (const name of ["add", "items"]) {
      const fl = paths[name].flies, a = fl.geometry.attributes.position, b = fl.userData.base;
      for (let i = 0; i < a.count; i++) a.setY(i, b[i * 2] + Math.sin(t * 1.3 + b[i * 2 + 1]) * 0.18);
      a.needsUpdate = true;
    }
    for (const f of fogs) { f.position.x += f.userData.v * dt; if (f.position.x > 28) f.position.x = -28; }
    stars.rotation.y = t * 0.004;
    if (drifting && !reduced) {
      if (driftStart === null) driftStart = t;
      const d = t - driftStart;
      camera.position.set(FORK.pos.x + Math.sin(d * 0.06) * 1.4, FORK.pos.y + Math.sin(d * 0.09) * 0.2, FORK.pos.z - Math.sin(d * 0.045) * 0.8);
      look.set(FORK.look.x + Math.sin(d * 0.05) * 2, FORK.look.y, FORK.look.z);
      camera.lookAt(look);
    }
    beacon.children[0].position.y = beacon.children[1].position.y = Math.sin(t * 1.6) * 0.06;
    beacon.userData.glow.material.opacity = 0.7 + Math.sin(t * 3.1) * 0.15;
    renderer.render(scene, camera);
  });
}

// Where the camera stands: at the edge of the forest, looking at the fork.
const FORK = { pos: new THREE.Vector3(0, 2.1, 10.5), look: new THREE.Vector3(0, 1.1, -3) };

// Narrow (phone) screens: widen the view so both paths still fit.
function fitFov() {
  const a = innerWidth / innerHeight, wide = 1.6;
  if (a >= wide) return 50;
  const v = 2 * Math.atan(Math.tan((25 * Math.PI) / 180) * wide / a) * 180 / Math.PI;
  return Math.min(v, 72);
}

// Screen position (CSS pixels) of the beacon.
export function screenPoint() {
  const v = new THREE.Vector3();
  beacon.children[0].getWorldPosition(v);
  v.project(camera);
  return { x: (v.x * 0.5 + 0.5) * innerWidth, y: (-v.y * 0.5 + 0.5) * innerHeight, visible: v.z < 1 };
}
