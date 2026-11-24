// beam2ac · vista 3D esquemática de un paquete exportado (ejes AC: +X izq., +Y arriba, +Z adelante).
// Datos: GET /api/scene?project=&attempt= (nulos WHEEL_xx del KN5, collider, DRIVEREYES y cámaras).
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";

const hud = document.getElementById("hud");
const q = new URLSearchParams(location.search);
window.__scene = { ready: false, error: null, objects: 0 };

function text(tag, s) { const n = document.createElement(tag); n.textContent = s; return n; }
const f3 = (v) => v ? v.map((x) => x.toFixed(3)).join(", ") : "—";

async function main() {
  const r = await fetch(`/api/scene?project=${encodeURIComponent(q.get("project"))}&attempt=${encodeURIComponent(q.get("attempt"))}`);
  const d = await r.json();
  if (!r.ok) throw new Error(d.error || r.statusText);

  const renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setSize(window.innerWidth, window.innerHeight);
  document.body.appendChild(renderer.domElement);
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x1e1e2e);
  const camera = new THREE.PerspectiveCamera(45, window.innerWidth / window.innerHeight, 0.01, 200);
  camera.position.set(4.5, 2.6, 5);
  scene.add(new THREE.GridHelper(8, 16, 0x555555, 0x2a2f36));
  scene.add(new THREE.AxesHelper(0.8));
  scene.add(new THREE.HemisphereLight(0xffffff, 0x334, 2.2));
  const orbit = new OrbitControls(camera, renderer.domElement);
  let n = 0;
  const add = (o) => { scene.add(o); n++; return o; };

  // ruedas: cilindros con radio y ancho de tyres.ini en cada nulo WHEEL_xx
  for (const [name, p] of Object.entries(d.wheels)) {
    const t = name.endsWith("F") ? d.tyre.front : d.tyre.rear;
    const r0 = t.radius || 0.3, w = t.width || 0.2;
    const g = new THREE.CylinderGeometry(r0, r0, w, 32);
    g.rotateZ(Math.PI / 2);
    const m = add(new THREE.Mesh(g, new THREE.MeshStandardMaterial({ color: 0x3a3f46, roughness: 0.9 })));
    m.position.set(...p);
    add(new THREE.LineSegments(new THREE.EdgesGeometry(g), new THREE.LineBasicMaterial({ color: 0x9aa4ae }))).position.set(...p);
  }
  // caja del collider
  if (d.collider_box) {
    const [a, b] = [d.collider_box.min, d.collider_box.max];
    const g = new THREE.BoxGeometry(b[0] - a[0], b[1] - a[1], b[2] - a[2]);
    const box = add(new THREE.LineSegments(new THREE.EdgesGeometry(g), new THREE.LineBasicMaterial({ color: 0x7aa2ff })));
    box.position.set((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, (a[2] + b[2]) / 2);
  }
  const point = (p, color) => { if (!p) return; const s = add(new THREE.Mesh(new THREE.SphereGeometry(0.05, 16, 12), new THREE.MeshStandardMaterial({ color }))); s.position.set(...p); };
  point(d.driver_eyes, 0xffaa00);
  point(d.cameras.BONNET_CAMERA_POS, 0x5fc47d);
  point(d.cameras.BUMPER_CAMERA_POS, 0xff7b72);
  if (d.driver_eyes) orbit.target.set(0, d.driver_eyes[1] * 0.6, 0);
  orbit.update();

  hud.replaceChildren(text("strong", `${d.car_id} · intento ${d.attempt}`), text("div", d.axes));
  const tbl = document.createElement("table");
  const row = (color, label, v) => { const tr = document.createElement("tr"); const a = document.createElement("td"); const sw = document.createElement("span"); sw.className = "sw"; sw.style.background = color; a.append(sw, label); tr.append(a, text("td", v)); tbl.append(tr); };
  row("#9aa4ae", "ruedas", `${Object.keys(d.wheels).length} nulos WHEEL_xx`);
  row("#7aa2ff", "collider", d.collider_box ? "caja" : "—");
  row("#ffaa00", "ojos del piloto", f3(d.driver_eyes));
  row("#5fc47d", "cámara capó", f3(d.cameras.BONNET_CAMERA_POS));
  row("#ff7b72", "cámara parachoques", f3(d.cameras.BUMPER_CAMERA_POS));
  hud.append(tbl, text("div", `pose del piloto: ${d.driver_pose ? "driver_base_pos.knh presente" : "pose por defecto"}`));

  window.addEventListener("resize", () => {
    camera.aspect = window.innerWidth / window.innerHeight; camera.updateProjectionMatrix();
    renderer.setSize(window.innerWidth, window.innerHeight);
  });
  (function loop() { renderer.render(scene, camera); requestAnimationFrame(loop); })();
  window.__scene = { ready: true, error: null, objects: n, wheels: Object.keys(d.wheels).length };
}
main().catch((e) => { hud.textContent = "Error: " + e.message; window.__scene = { ready: false, error: String(e) }; });
