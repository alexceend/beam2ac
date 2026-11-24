// beam2ac · editor de animación del piloto. Esqueleto REAL que mueve el juego (misma pose e IK que
// steer.ksanim, GET /api/anim + POST /api/anim_preview), no la malla con piel del piloto.
// Vista desde la cámara del piloto u órbita; ajustes de manos (ciclo, elevación, profundidad de agarre)
// y caderas (altura del piloto). Guardar: POST /api/anim (versiones) y /api/cockpit (caderas).
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";

const PROJECT = new URLSearchParams(location.search).get("project");
const side = document.getElementById("side"), view = document.getElementById("view");
const el = (t, p = {}, ...k) => { const n = Object.assign(document.createElement(t), p); n.append(...k); return n; };
const api = async (u, body) => {
  const r = await fetch(u, body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {});
  const d = await r.json(); if (!r.ok) throw new Error(d.error || r.statusText); return d;
};
const q = encodeURIComponent(PROJECT);
const FOV_DEFAULT = 85, FOV_MIN = 20, FOV_MAX = 110;
// el FOV es una preferencia de la vista, la misma para todos los coches (no se exporta a AC)
const loadFov = () => { try { const v = parseFloat(localStorage.getItem("beam2ac.anim.fov")); return v >= FOV_MIN && v <= FOV_MAX ? v : FOV_DEFAULT; } catch { return FOV_DEFAULT; } };
const storeFov = (v) => { try { localStorage.setItem("beam2ac.anim.fov", String(v)); } catch { /* sin almacenamiento: vale la sesión */ } };
const START = new URLSearchParams(location.search);  // ?steer=40&cam=orbit (pruebas)

let A, C, edits = {}, hips, dirty = false, pv = null, steer = +(START.get("steer") || 0), playing = false, camMode = START.get("cam") || "pilot", seq = 0;

const cyc = () => ({
  left: edits.left || A.defaults.left, right: edits.right || A.defaults.right,
});
const val = (k) => (edits[k] !== undefined ? edits[k] : A.defaults[k]);

async function main() {
  A = await api(`/api/anim?project=${q}`);
  C = await api(`/api/cockpit?project=${q}`);
  edits = JSON.parse(JSON.stringify(A.edits)); hips = [...A.hips];
  const geoUrl = `/api/cockpit_geometry?project=${q}`;
  const buf = await (parent !== window && parent.b2aGeo ? parent.b2aGeo(geoUrl) : fetch(geoUrl).then((r) => r.arrayBuffer()));
  const [nv, ni] = new Uint32Array(buf, 0, 2);
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.BufferAttribute(new Float32Array(buf, 8, nv * 3), 3));
  geo.setIndex(new THREE.BufferAttribute(new Uint32Array(buf, 8 + nv * 12, ni), 1));
  geo.computeVertexNormals();

  const renderer = new THREE.WebGLRenderer({ antialias: true });
  view.append(renderer.domElement);
  const scene = new THREE.Scene(); scene.background = new THREE.Color(0x1e1e2e);
  let fov = loadFov();
  const camera = new THREE.PerspectiveCamera(fov, 1, 0.01, 100);
  scene.add(new THREE.HemisphereLight(0xffffff, 0x334, 1.6));
  const sun = new THREE.DirectionalLight(0xffffff, 1.2); sun.position.set(2, 5, -3); scene.add(sun);
  scene.add(new THREE.Mesh(geo, new THREE.MeshStandardMaterial({ color: 0x8895a3, transparent: true, opacity: 0.18, side: THREE.DoubleSide, depthWrite: false })));
  const orbit = new OrbitControls(camera, renderer.domElement);

  // volante: aro con el radio de las manos, en el plano del aro y girado con el ángulo del volante
  const ring = new THREE.Group(); scene.add(ring);
  const hand = new THREE.Group(); scene.add(hand);
    const lines = new THREE.LineSegments(new THREE.BufferGeometry(), new THREE.LineBasicMaterial({ color: 0xcfd8ff })); lines.frustumCulled = false; hand.add(lines);
  const joints = new THREE.Group(); hand.add(joints);
  const handBall = { L: null, R: null };
  for (const s of ["L", "R"]) {
    handBall[s] = new THREE.Mesh(new THREE.SphereGeometry(0.02, 16, 12), new THREE.MeshBasicMaterial({ color: 0xa6e3a1 }));
    hand.add(handBall[s]);
  }
  const head = new THREE.Mesh(new THREE.SphereGeometry(0.11, 20, 14), new THREE.MeshBasicMaterial({ color: 0xffd58a, wireframe: true }));
  hand.add(head);
  const eyes = A.eyes ? new THREE.Vector3(...A.eyes) : null;
  const eyeMark = new THREE.Mesh(new THREE.SphereGeometry(0.02, 10, 8), new THREE.MeshBasicMaterial({ color: 0xffaa00 }));
  if (eyes) { eyeMark.position.copy(eyes); scene.add(eyeMark); }

  let wheelCentre = null, wheelAxis = null;
  function buildRing() {
    ring.clear(); if (!pv) return;
    wheelCentre = new THREE.Vector3(...pv.wheel.centre); wheelAxis = new THREE.Vector3(...pv.wheel.axis).normalize();
    const r = pv.wheel.hand_radius_m;
    const torus = new THREE.Mesh(new THREE.TorusGeometry(r, 0.012, 10, 64), new THREE.MeshBasicMaterial({ color: 0xcba6f7 }));
    const mark = new THREE.Mesh(new THREE.SphereGeometry(0.02, 10, 8), new THREE.MeshBasicMaterial({ color: 0xff7b72 }));
    mark.position.set(r, 0, 0); torus.add(mark);   // marca de las 12 del volante (se ve el giro)
    const g = new THREE.Group(); g.add(torus);
    g.quaternion.setFromUnitVectors(new THREE.Vector3(0, 0, 1), wheelAxis); g.position.copy(wheelCentre);
    ring.add(g); ring.userData.torus = torus;
  }

  function frameAt(deg) {
    const n = pv.frames.length, P = pv.period_deg;
    const t = ((((deg % P) + P) % P) / P) * n, i = Math.floor(t) % n, j = (i + 1) % n, f = t - Math.floor(t);
    return { i, pose: pv.frames[i].map((a, k) => a.map((c, m) => c + (pv.frames[j][k][m] - c) * f)) };
  }
  function pose() {
    if (!pv) return;
    const { i, pose: p } = frameAt(steer);
    const pts = [];
    pv.bones.forEach((b, k) => { if (b.parent >= 0) pts.push(...p[b.parent], ...p[k]); });
    lines.geometry.setAttribute("position", new THREE.Float32BufferAttribute(pts, 3));
    pv.bones.forEach((b, k) => {
      if (b.name === "HAND_L" || b.name === "HAND_R") {
        const s = b.name.slice(-1), g = pv.grips[i][s === "L" ? 0 : 1];
        handBall[s].position.set(...p[k]); handBall[s].material.color.set(g ? 0xa6e3a1 : 0xf9e2af);
      }
      if (b.name === "Head") head.position.set(p[k][0], p[k][1] + 0.1, p[k][2] + 0.04);
    });
    if (ring.userData.torus) ring.userData.torus.rotation.z = THREE.MathUtils.degToRad(steer);
  }
  // el giro positivo de ``deg`` gira lo agarrado +deg alrededor del eje del aro (hand_over_hand)

  let orbitReady = false;
  function camera_update() {
    head.visible = camMode !== "pilot";  // desde los ojos, el casco taparía la vista
    if (camMode === "pilot" && eyes && wheelCentre) {
      orbit.enabled = false; camera.position.copy(eyes);
      camera.lookAt(wheelCentre.x, wheelCentre.y - 0.05, wheelCentre.z);
    } else {
      if (!orbitReady) { orbitReady = true; camera.position.set(1.8, 1.3, 0.3); orbit.target.set(0.34, 0.8, 0.1); }
      orbit.enabled = true; orbit.update();
    }
  }

  // --- panel
  const ui = {}; let timer = 0;
  const say = (t, c) => { ui.msg.textContent = t; ui.msg.className = c || ""; };
  async function refresh() {
    const my = ++seq;
    try {
      const r = await api("/api/anim_preview", { project: PROJECT, edits, hips, frames: 48 });
      if (my !== seq) return; pv = r; buildRing(); pose();
      const m = r.reach_margin_m;
      ui.reach.textContent = `holgura de alcance del brazo: ${(m * 100).toFixed(1)} cm · periodo ${r.period_deg}° · ángulos base L ${r.base_angles_deg.L}° R ${r.base_angles_deg.R}°`;
      ui.reach.className = m < 0.02 ? "warn" : "muted";
      say(m < 0.02 ? "Brazo casi extendido: baja las caderas o acerca el volante." : "", "warn");
    } catch (e) { if (my === seq) say(e.message, "warn"); }
  }
  const later = () => { dirty = true; ui.state.textContent = "cambios sin guardar"; clearTimeout(timer); timer = setTimeout(refresh, 250); };
  function num(label, get, set, lo, hi, step) {
    const n = el("input", { type: "number", step, min: lo, max: hi, value: (+get()).toFixed(3) });
    n.addEventListener("change", () => { const v = parseFloat(n.value); if (!isFinite(v) || v < lo || v > hi) { n.value = (+get()).toFixed(3); return; } set(v); later(); });
    ui[label] = n; return [el("span", {}, label), n];
  }
  const setCycle = (k, idx, v) => { const e = cyc(); const L = [...e.left], R = [...e.right]; ({ left: L, right: R })[k][idx] = v; edits.left = L; edits.right = R; };
  const setPeriod = (idx, v) => { const e = cyc(); const L = [...e.left], R = [...e.right]; L[idx] = R[idx] = v; edits.left = L; edits.right = R; };
  const rows = [
    ...num("Mano izq. a_min (°)", () => cyc().left[0], (v) => setCycle("left", 0, v), -360, 360, "1"),
    ...num("Mano der. a_min (°)", () => cyc().right[0], (v) => setCycle("right", 0, v), -360, 360, "1"),
    ...num("Agarre (°, ambas)", () => cyc().left[1], (v) => setPeriod(1, v), 5, 360, "1"),
    ...num("Traslado (°, ambas)", () => cyc().left[2], (v) => setPeriod(2, v), 5, 360, "1"),
    ...num("Elevación al trasladar (m)", () => val("lift_m"), (v) => { edits.lift_m = v; }, 0, 0.3, "0.005"),
    ...num("Manos Z · nudillo–aro (m)", () => val("knuckle_target_m"), (v) => { edits.knuckle_target_m = v; }, -0.1, 0.15, "0.005"),
    ...num("Caderas X (m)", () => hips[0], (v) => { hips[0] = v; }, -6, 6, "0.005"),
    ...num("Caderas Y — altura (m)", () => hips[1], (v) => { hips[1] = v; }, -6, 6, "0.005"),
    ...num("Caderas Z (m)", () => hips[2], (v) => { hips[2] = v; }, -6, 6, "0.005"),
  ];
  ui.msg = el("div", { id: "msg" }); ui.state = el("div", { className: "muted" }, A.version ? `guardado: versión ${A.version}` : "sin ajustes guardados");
  ui.reach = el("div", { className: "muted" }, "calculando…");
  const slider = el("input", { type: "range", min: -540, max: 540, step: 1, value: steer });
  const angle = el("span", {}, `${steer}°`);
  slider.addEventListener("input", () => { steer = +slider.value; angle.textContent = `${steer}°`; pose(); });
  const fovIn = el("input", { type: "range", min: FOV_MIN, max: FOV_MAX, step: 1, value: fov });
  const fovTxt = el("span", {}, `${fov}°`);
  const setFov = (v) => { fov = Math.max(FOV_MIN, Math.min(FOV_MAX, Math.round(v))); camera.fov = fov; camera.updateProjectionMatrix(); fovIn.value = fov; fovTxt.textContent = `${fov}°`; storeFov(fov); };
  fovIn.addEventListener("input", () => setFov(+fovIn.value));
  // la rueda acerca/aleja también en la vista del piloto (en órbita la usa OrbitControls para el dolly)
  renderer.domElement.addEventListener("wheel", (e) => { if (camMode === "pilot") { e.preventDefault(); setFov(fov + Math.sign(e.deltaY) * 3); } }, { passive: false });
  const playBtn = el("button", { type: "button", className: "secondary", onclick: () => { playing = !playing; playBtn.textContent = playing ? "⏸ Pausa" : "▶ Reproducir"; } }, "▶ Reproducir");
  const camBtn = el("button", { type: "button", className: "secondary", onclick: () => { camMode = camMode === "pilot" ? "orbit" : "pilot"; camBtn.textContent = camMode === "pilot" ? "Vista: piloto" : "Vista: órbita"; } }, "Vista: piloto");
  const save = async (reset) => {
    try {
      const r = await api("/api/anim", { project: PROJECT, key: A.key, base_version: A.version, reset: !!reset, edits: reset ? {} : edits, note: "editor de animación" });
      A.version = r.version; edits = JSON.parse(JSON.stringify(r.edits));
      const hipsChanged = hips.some((v, i) => Math.abs(v - (C.items.hips || C.original.hips)[i]) > 5e-4);
      if (!reset && hipsChanged) {
        const c = await api("/api/cockpit", { project: PROJECT, key: C.key, base_version: C.version, items: { ...C.items, hips }, note: "editor de animación" });
        C.items = c.items; C.version = c.version;
      }
      if (reset) { hips = [...A.hips]; }
      dirty = false; ui.state.textContent = `guardado: versión ${A.version}`; say("Guardado. Se aplica al exportar con piloto local.", "ok"); refresh();
    } catch (e) { say(e.message, "warn"); }
  };
  side.replaceChildren(
    el("strong", {}, "Animación del piloto"), el("div", { className: "muted" }, `Perfil: ${A.profile_origin}. ${A.frame}`),
    el("div", {}, el("a", { href: `/static/cockpit.html?project=${q}`, style: "color:#b4befe" }, "← Cámaras y piloto")),
    el("h3", {}, "Volante"), el("div", { className: "row" }, el("span", {}, "Ángulo "), angle), slider,
    el("div", {}, playBtn, camBtn),
    el("div", { className: "row" }, el("span", {}, "Zoom / FOV "), fovTxt), fovIn,
    el("div", { className: "muted" }, "Rueda del ratón en la vista del piloto = zoom. El FOV es el mismo para todos los coches y no se exporta a AC."),
    el("div", {}, el("button", { className: "secondary", type: "button", onclick: () => setFov(FOV_DEFAULT) }, `FOV ${FOV_DEFAULT}° (por defecto)`)),
    el("h3", {}, "Manos y piloto"), el("div", { className: "row" }, ...rows),
    el("div", { className: "muted" }, "a_min = ángulo del aro (0° izquierda, 90° arriba) donde empieza el agarre. Manos Z: + empuja las manos hacia el salpicadero, − las acerca al piloto. Caderas: altura y posición del piloto. Verde = agarrando, naranja = trasladando."),
    el("div", {}, el("button", { type: "button", onclick: () => save(false) }, "Guardar"),
      el("button", { className: "secondary", type: "button", onclick: () => { if (confirm("¿Restaurar los valores del perfil?")) save(true); } }, "Restaurar")),
    ui.state, ui.reach, ui.msg,
    el("div", { className: "muted" }, "Vista previa: esqueleto con la misma pose e IK que se escribe en steer.ksanim; no es la malla con piel del piloto. Sin probar en AC."));

  const resize = () => { const w = view.clientWidth, h = view.clientHeight; camera.aspect = w / h; camera.updateProjectionMatrix(); renderer.setSize(w, h); };
  window.addEventListener("resize", resize); resize();
  let last = performance.now();
  (function loop(t) {
    const dt = (t - last) / 1000; last = t;
    if (playing) { steer = Math.sin(t / 1600) * 450; slider.value = steer; angle.textContent = `${steer.toFixed(0)}°`; pose(); }
    camera_update(); renderer.render(scene, camera); requestAnimationFrame(loop);
  })(last);
  await refresh();
  window.__anim = { ready: true, get preview() { return pv; }, setSteer: (d) => { steer = d; pose(); },
    snapshot: () => { renderer.render(scene, camera); return renderer.domElement.toDataURL("image/png"); } };
}
main().catch((e) => {
  // sin volante/animación la página fallaba sin salida: siempre se puede volver a las cámaras
  side.replaceChildren(el("div", {}, el("a", { href: `/static/cockpit.html?project=${q}`, style: "color:#b4befe" }, "← Cámaras y piloto")),
    el("p", { className: "warn" }, "Error: " + e.message));
  window.__anim = { ready: false, error: String(e) };
});
