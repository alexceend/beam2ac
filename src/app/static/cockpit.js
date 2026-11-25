// beam2ac · editor 3D de cámaras y piloto (G2.32). Geometría real del KN5 del proyecto
// (GET /api/cockpit_geometry), marco del KN5: +X izq., +Y arriba, +Z adelante, suelo y = 0.
// Guardar/restaurar: POST /api/cockpit (versiones de solo añadir); el exportador los aplica.
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { TransformControls } from "three/addons/controls/TransformControls.js";

const PROJECT = new URLSearchParams(location.search).get("project");
const side = document.getElementById("side"), view = document.getElementById("view");
const ITEMS = [
  ["driver_eyes", "Ojos / cámara interior (DRIVEREYES)", 0xffaa00],
  ["bonnet", "Cámara capó (BONNET_CAMERA_POS)", 0x5fc47d],
  ["bumper", "Cámara parachoques (BUMPER_CAMERA_POS)", 0xff7b72],
  ["hips", "Caderas del piloto (driver_base_pos.knh)", 0x6cb6ff],
  ["fuel_tank", "Depósito de combustible ([FUELTANK] POSITION; posición provisional, colócalo)", 0xd2a8ff],
];
const el = (t, p = {}, ...k) => { const n = Object.assign(document.createElement(t), p); n.append(...k); return n; };
const api = async (u, body) => {
  const r = await fetch(u, body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {});
  const d = await r.json(); if (!r.ok) throw new Error(d.error || r.statusText); return d;
};
let D, draft = {}, sel = "driver_eyes", dirty = false;
const cur = (k) => draft[k] || D.original[k];
const same = (a, b) => a.every((v, i) => Math.abs(v - b[i]) < 5e-4);

async function main() {
  const geoUrl = `/api/cockpit_geometry?project=${encodeURIComponent(PROJECT)}`;
  // la ventana principal precarga y guarda la geometría (b2aGeo); sin ella (página suelta) se pide directamente
  const geoReq = (parent !== window && parent.b2aGeo ? parent.b2aGeo(geoUrl) : fetch(geoUrl).then(async (r) => {
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || r.statusText);
    return r.arrayBuffer();
  }));
  geoReq.catch(() => {});  // el error se muestra al esperarla (evita un rechazo sin atender si falla antes la ficha)
  D = await api(`/api/cockpit?project=${encodeURIComponent(PROJECT)}`);
  draft = JSON.parse(JSON.stringify(D.items));
  const buf = await geoReq;
  const [nv, ni] = new Uint32Array(buf, 0, 2);
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.BufferAttribute(new Float32Array(buf, 8, nv * 3), 3));
  geo.setIndex(new THREE.BufferAttribute(new Uint32Array(buf, 8 + nv * 12, ni), 1));
  geo.computeVertexNormals();

  const renderer = new THREE.WebGLRenderer({ antialias: true });
  view.append(renderer.domElement);
  const scene = new THREE.Scene(); scene.background = new THREE.Color(0x1e1e2e);
  const camera = new THREE.PerspectiveCamera(45, 1, 0.01, 100); camera.position.set(3.6, 2.0, -4.2);
  scene.add(new THREE.GridHelper(10, 20, 0x555555, 0x2a2f36), new THREE.HemisphereLight(0xffffff, 0x334, 1.6));
  const sun = new THREE.DirectionalLight(0xffffff, 1.4); sun.position.set(2, 5, 3); scene.add(sun);
  const car = new THREE.Mesh(geo, new THREE.MeshStandardMaterial({ color: 0x8895a3, transparent: true, opacity: 0.45, side: THREE.DoubleSide, depthWrite: false }));
  scene.add(car);
  const orbit = new OrbitControls(camera, renderer.domElement); orbit.target.set(0, 0.7, 0);
  const tc = new TransformControls(camera, renderer.domElement); tc.setMode("translate"); tc.setSize(0.8);
  scene.add(tc);
  tc.addEventListener("dragging-changed", (e) => { orbit.enabled = !e.value; });

  // marcadores: actual (esfera) y original (malla fantasma); piloto: casco (estimación)
  const mk = {}, ghost = {};
  for (const [k, , c] of ITEMS) {
    mk[k] = new THREE.Mesh(new THREE.SphereGeometry(0.045, 20, 14), new THREE.MeshBasicMaterial({ color: c, depthTest: false }));
    mk[k].renderOrder = 5; mk[k].userData.key = k; scene.add(mk[k]);
    ghost[k] = new THREE.Mesh(new THREE.SphereGeometry(0.03, 12, 8), new THREE.MeshBasicMaterial({ color: c, wireframe: true, depthTest: false }));
    ghost[k].renderOrder = 4; scene.add(ghost[k]);
  }
  const link = new THREE.Line(new THREE.BufferGeometry(), new THREE.LineBasicMaterial({ color: 0xffffff })); scene.add(link);
  const head = new THREE.Mesh(new THREE.SphereGeometry(0.15, 24, 16), new THREE.MeshBasicMaterial({ color: 0x6cb6ff, wireframe: true, depthTest: false }));
  head.renderOrder = 4; scene.add(head);
  const skelLine = new THREE.LineSegments(new THREE.BufferGeometry(), new THREE.LineBasicMaterial({ color: 0x6cb6ff, depthTest: false }));
  const skelPts = new THREE.Points(new THREE.BufferGeometry(), new THREE.PointsMaterial({ color: 0xffffff, size: 0.035, depthTest: false }));
  for (const o of [skelLine, skelPts]) { o.renderOrder = 6; o.frustumCulled = false; o.visible = false; scene.add(o); }
  let skelSeq = 0, skelTimer = null, skelHips = "", lastPose = null;
  // Aviso: partes del tronco por delante de los ojos y dentro del campo de visión de la cámara interior
  // (casco y cara ya se ocultan en el juego; brazos y manos sobre el volante son normales). Heurística:
  // z > ojos + 3 cm, |x| < 35 cm, de 45 cm por debajo a 25 cm por encima de los ojos (marco del KN5).
  const TORSO = /neck|spine|chest|clav|shoulder/i;
  const checkView = () => {
    if (!lastPose) { ui.viewWarn.textContent = ""; return; }
    const e = cur("driver_eyes");
    const seen = lastPose.filter(([n, q]) => TORSO.test(n) && q[2] > e[2] + 0.03 && Math.abs(q[0] - e[0]) < 0.35 && q[1] > e[1] - 0.45 && q[1] < e[1] + 0.25).map(([n]) => n);
    ui.viewWarn.textContent = seen.length ? `¡Desde la cámara interior verás parte del piloto (${seen.join(", ")}): los ojos quedan por detrás de ellas! Adelanta los ojos o mueve las caderas.` : "";
  };
  // POST /api/anim_preview calcula la pose del piloto con las caderas dadas (sin guardar nada); se pide con
  // un pequeño retardo para no lanzar un cálculo por cada pixel que se arrastra y sin bloquear la vista
  const updateSkeleton = () => {
    clearTimeout(skelTimer);
    skelTimer = setTimeout(async () => {
      const my = ++skelSeq, hips = cur("hips");
            skelHips = JSON.stringify(hips);
      try {
        const pv = await api("/api/anim_preview", { project: PROJECT, hips, frames: 8 });
        if (my !== skelSeq) return;
        const p = pv.frames[0], seg = [];
        pv.bones.forEach((b, k) => { if (b.parent >= 0) seg.push(...p[b.parent], ...p[k]); });
        skelLine.geometry.setAttribute("position", new THREE.Float32BufferAttribute(seg, 3));
        skelPts.geometry.setAttribute("position", new THREE.Float32BufferAttribute(p.flat(), 3));
        skelLine.visible = skelPts.visible = true; ui.skelNote.textContent = "";
        lastPose = pv.bones.map((b, k) => [b.name, p[k]]); checkView();
      } catch (e) {
        if (my !== skelSeq) return;
        skelLine.visible = skelPts.visible = false; lastPose = null; checkView();
        ui.skelNote.textContent = `Esqueleto del piloto no disponible: ${e.message}`;
      }
    }, 250);
  };
  const arrow = new THREE.ArrowHelper(new THREE.Vector3(0, 0, 1), new THREE.Vector3(), 0.3, 0xffaa00); scene.add(arrow);
  const ray = new THREE.Raycaster();
  const roofAt = (x, z) => { ray.set(new THREE.Vector3(x, 4, z), new THREE.Vector3(0, -1, 0)); const h = ray.intersectObject(car)[0]; return h ? h.point.y : null; };

  const ui = {};
  // Vista previa «como la cámara del juego»: se mira desde la posición actual (la del borrador) hacia +Z.
  // El FOV es el de la vista de piloto del editor de animación (85°); capó y parachoques usan 70° (orientativo).
  let viewMode = "orbit";
  const FOVS = { driver_eyes: 85, bonnet: 70, bumper: 70 }, ORBIT_FOV = 45;
  const applyView = () => {
    const through = viewMode !== "orbit";
    orbit.enabled = !through; tc.visible = !through; tc.enabled = !through;
    camera.fov = through ? FOVS[viewMode] : ORBIT_FOV; camera.updateProjectionMatrix();
    if (!through) { camera.position.copy(orbitPos); orbit.target.copy(orbitTarget); }
    for (const k of Object.keys(mk)) mk[k].visible = !(through && k === viewMode);
    arrow.visible = !through; head.visible = !through || viewMode !== "driver_eyes";
    for (const [k, b] of Object.entries(ui.viewBtn)) b.className = k === viewMode ? "" : "secondary";
  };
  const setView = (m) => {
    if (viewMode === "orbit" && m !== "orbit") { orbitPos.copy(camera.position); orbitTarget.copy(orbit.target); }
    viewMode = m; applyView();
  };
  const orbitPos = new THREE.Vector3(), orbitTarget = new THREE.Vector3();
  function refresh() {
    for (const [k] of ITEMS) {
      const p = cur(k); mk[k].position.set(...p); ghost[k].position.set(...D.original[k]);
      ghost[k].visible = !same(p, D.original[k]);
    }
    const p = cur(sel), o = D.original[sel];
    link.geometry.setFromPoints([new THREE.Vector3(...o), new THREE.Vector3(...p)]); link.visible = !same(p, o);
    arrow.position.set(...cur("driver_eyes"));
    const h = cur("hips"); head.position.set(h[0], h[1] + D.head_bone_above_hips + 0.1, h[2] + 0.04);
    for (let i = 0; i < 3; i++) ui.n[i].value = p[i].toFixed(3);
    const roof = roofAt(h[0], h[2]), margin = roof === null ? null : roof - D.validated_roof_minus_head - (h[1] + D.head_bone_above_hips);
    ui.head.textContent = margin === null ? "techo no encontrado sobre las caderas"
      : margin >= -1e-3 ? `holgura de cabeza: ${margin.toFixed(3)} m sobre la referencia validada (techo ${roof.toFixed(3)} m)`
      : `¡cabeza ${(-margin).toFixed(3)} m por encima de la holgura validada (techo ${roof.toFixed(3)} m)!`;
    ui.head.className = margin !== null && margin < -1e-3 ? "warn" : "muted";
    ui.state.textContent = dirty ? "cambios sin guardar" : `guardado: versión ${D.version}`;
    if (JSON.stringify(h) !== skelHips) updateSkeleton(); else checkView();
  }
  function select(k) { sel = k; tc.attach(mk[k]); refresh(); }
  tc.addEventListener("objectChange", () => { draft[sel] = tc.object.position.toArray().map((v) => +v.toFixed(4)); dirty = true; refresh(); });
  renderer.domElement.addEventListener("pointerdown", (e) => {
    if (tc.dragging || viewMode !== "orbit") return; const r = renderer.domElement.getBoundingClientRect();
    ray.setFromCamera(new THREE.Vector2(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1), camera);
    const hit = ray.intersectObjects(Object.values(mk))[0]; if (hit) { ui.radio[hit.object.userData.key].checked = true; select(hit.object.userData.key); }
  });

  // panel
  ui.viewBtn = {};
  for (const [k, t] of [["orbit", "Órbita"], ["driver_eyes", "Ojos (interior)"], ["bonnet", "Capó"], ["bumper", "Parachoques"]]) {
    ui.viewBtn[k] = el("button", { type: "button", className: k === "orbit" ? "" : "secondary", onclick: () => setView(k) }, t);
  }
  ui.n = [0, 1, 2].map(() => el("input", { type: "number", step: "0.005" }));
  ui.n.forEach((n, i) => n.addEventListener("change", () => { const p = [...cur(sel)]; const v = parseFloat(n.value); if (!isFinite(v)) return refresh(); p[i] = v; draft[sel] = p; dirty = true; refresh(); }));
  ui.skelNote = el("div", { className: "muted" });
  ui.radio = {}; ui.head = el("div", { className: "muted" }); ui.viewWarn = el("div", { className: "warn" }); ui.state = el("div", { className: "muted" }); ui.msg = el("div", { id: "msg" });
  // Subir/Bajar: mueve el elemento elegido 0,1 m en ese eje (marco del KN5)
  const nudge = (i, dir) => el("button", { type: "button", className: "secondary nudge", title: `${dir > 0 ? "Subir" : "Bajar"} 0,1 m`, "aria-label": `${dir > 0 ? "Subir" : "Bajar"} ${"XYZ"[i]} 0,1`,
    onclick: () => { const p = [...cur(sel)]; p[i] = +(p[i] + dir * 0.1).toFixed(4); draft[sel] = p; dirty = true; refresh(); } }, dir > 0 ? "▲" : "▼");
  const list = ITEMS.map(([k, label, c]) => {
    const r = el("input", { type: "radio", name: "it", checked: k === sel }); ui.radio[k] = r; r.addEventListener("change", () => select(k));
    return el("label", { className: "item" }, r, el("span", { className: "sw", style: `background:#${c.toString(16).padStart(6, "0")}` }), label);
  });
  const say = (t, c) => { ui.msg.textContent = t; ui.msg.className = c || ""; };
  const itemsToSave = () => Object.fromEntries(Object.entries(draft).filter(([k, v]) => !same(v, D.original[k])));
  const save = async (reset) => {
    try {
      const ids = { project: PROJECT, key: D.key, base_version: D.version };
      const r = await api("/api/cockpit", reset ? { ...ids, reset: true } : { ...ids, items: itemsToSave(), note: "editor 3D" });
      D.items = r.items; D.version = r.version;
      parent.postMessage({ type: "cockpit-saved", version: r.version, items: Object.keys(r.items) }, location.origin); draft = JSON.parse(JSON.stringify(r.items)); dirty = false; refresh();
      say(reset ? `Restaurado a los valores originales (versión ${r.version}).` : `Guardado (versión ${r.version}). Se aplica al coche automáticamente.`, "ok");
    } catch (e) { say(e.message, "warn"); }
  };
  side.replaceChildren(
    el("strong", {}, "Cámaras y piloto"), el("div", { className: "muted" }, D.frame),
    el("h3", {}, "Ver desde"), el("div", {}, ...Object.values(ui.viewBtn)),
    el("div", { className: "muted" }, "Mira desde la posición actual hacia +Z (delante) para ver cómo quedaría la cámara en el juego; «Órbita» vuelve a la vista libre."),
    el("h3", {}, "Elemento"), ...list,
    el("div", { className: "xyz" }, ...["X", "Y", "Z"].flatMap((a, i) => [el("span", {}, a), ui.n[i], nudge(i, 1), nudge(i, -1)])),
    el("div", { className: "muted" }, "Arrastra el gizmo o escribe el valor. Esfera sólida = actual; malla = original."),
    el("div", {}, el("button", { type: "button", onclick: () => save(false) }, "Guardar"),
      el("button", { className: "secondary", type: "button", onclick: () => { delete draft[sel]; dirty = true; select(sel); } }, "Restaurar este"),
      el("button", { className: "secondary", type: "button", onclick: () => { if (confirm("¿Restaurar todos los valores originales?")) save(true); } }, "Restaurar todo")),
    D.model_has_wheel ? null : el("div", { className: "warn" }, "El modelo 3D se generó sin volante, así que no hay manos ni esqueleto que mostrar. ",
      el("button", { type: "button", onclick: () => parent.postMessage({ type: "cockpit-rebuild" }, location.origin) }, "Regenerar modelo 3D con volante")),
    ui.state, ui.head, ui.viewWarn, ui.msg,
    el("div", {}, el("button", { className: "secondary", type: "button", onclick: () => { location.href = `/static/animation.html?project=${encodeURIComponent(PROJECT)}`; } }, "Animación de manos y piloto")),
    el("h3", {}, "Piloto"),
    ui.skelNote, el("div", { className: "muted" }, "Caderas originales: " + D.hips_rule));

  const resize = () => { const w = view.clientWidth, h = view.clientHeight; camera.aspect = w / h; camera.updateProjectionMatrix(); renderer.setSize(w, h); };
  window.addEventListener("resize", resize); resize();
  select("driver_eyes");
  (function loop() {
    if (viewMode === "orbit") orbit.update();
    else { const p = cur(viewMode); camera.position.set(...p); camera.lookAt(p[0], p[1], p[2] + 1); }
    renderer.render(scene, camera); requestAnimationFrame(loop); })();
  window.__cockpit = { ready: true, triangles: ni / 3, select, draft: () => draft, save,
    snapshot: () => { renderer.render(scene, camera); return renderer.domElement.toDataURL("image/png"); } };
}
main().catch((e) => { side.textContent = "Error: " + e.message; window.__cockpit = { ready: false, error: String(e) }; });
