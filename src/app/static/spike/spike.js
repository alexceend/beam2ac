// Prueba técnica mínima: escena three.js local + puente con Python (pywebview js_api).
// Python -> JS: get_scene() devuelve los centros de rueda del Ibiza (ejes AC, m).
// JS -> Python: report_position() recibe la posición del marcador movido con el gizmo.
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { TransformControls } from "three/addons/controls/TransformControls.js";

const hud = document.getElementById("hud");
const state = { ready: false, result: null, error: null, wheels: 0 };
window.__spike = state;

const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setSize(window.innerWidth, window.innerHeight);
document.body.appendChild(renderer.domElement);
const scene = new THREE.Scene();
scene.background = new THREE.Color(0x14171b);
const camera = new THREE.PerspectiveCamera(50, window.innerWidth / window.innerHeight, 0.01, 100);
camera.position.set(4, 2.5, 4);  // ejes AC: +Y arriba, +Z adelante
scene.add(new THREE.GridHelper(6, 12, 0x555555, 0x333333));
scene.add(new THREE.AxesHelper(1));  // X rojo (izquierda), Y verde (arriba), Z azul (adelante)
scene.add(new THREE.HemisphereLight(0xffffff, 0x333344, 2));

const orbit = new OrbitControls(camera, renderer.domElement);
const marker = new THREE.Mesh(new THREE.SphereGeometry(0.05), new THREE.MeshStandardMaterial({ color: 0xffaa00 }));
scene.add(marker);
const gizmo = new TransformControls(camera, renderer.domElement);
gizmo.attach(marker);
gizmo.addEventListener("dragging-changed", (e) => { orbit.enabled = !e.value; });
scene.add(gizmo);

async function report(name) {
  const p = marker.position;
  return window.pywebview.api.report_position(name, p.x, p.y, p.z);
}
gizmo.addEventListener("mouseUp", () => { report("gizmo").catch((e) => { state.error = String(e); }); });

function render() { renderer.render(scene, camera); requestAnimationFrame(render); }

function bridgeReady() {
  return new Promise((resolve) => {
    if (window.pywebview && window.pywebview.api && window.pywebview.api.get_scene) resolve();
    else window.addEventListener("pywebviewready", () => resolve(), { once: true });
  });
}

async function init() {
  await bridgeReady();
  const data = await window.pywebview.api.get_scene();
  for (const [name, c] of Object.entries(data.wheel_centers_ac)) {
    const wheel = new THREE.Mesh(new THREE.CylinderGeometry(data.radius, data.radius, 0.2, 24),
      new THREE.MeshStandardMaterial({ color: 0x5a7fd6 }));
    wheel.rotation.z = Math.PI / 2;  // eje del cilindro según X (lateral)
    wheel.position.set(c[0], c[1], c[2]);
    wheel.name = name;
    scene.add(wheel);
    state.wheels += 1;
  }
  hud.textContent = `three.js r${THREE.REVISION} · ${data.vehicle}: ${state.wheels} ruedas desde Python · gizmo → Python`;
  render();
  state.ready = true;
}

// Autoprueba llamada desde Python: mueve el marcador por programa (como haría el gizmo) y
// comprueba la ida y vuelta por el puente.
state.selftest = async (x, y, z) => {
  try {
    marker.position.set(x, y, z);
    gizmo.dispatchEvent({ type: "objectChange" });
    const echo = await report("selftest");
    renderer.render(scene, camera);
    const gl = renderer.getContext();
    state.result = {
      revision: THREE.REVISION,
      webgl: gl instanceof WebGL2RenderingContext ? "webgl2" : "webgl1",
      canvas: [renderer.domElement.width, renderer.domElement.height],
      wheels_drawn: state.wheels,
      sent: [x, y, z],
      echo,
      import_map_three: import.meta.resolve ? import.meta.resolve("three") : null,
    };
  } catch (e) {
    state.error = String(e && e.stack || e);
  }
};

init().catch((e) => { state.error = String(e && e.stack || e); hud.textContent = "Error: " + state.error; });
