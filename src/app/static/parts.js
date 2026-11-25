// Editor 3D de animaciones de piezas: el modelo a la derecha con cada pieza de un color, la lista a la izquierda,
// y una barra para ver cómo se movería. Geometría: GET /api/parts_geometry (marco del KN5: +X izq., +Y arriba, +Z adelante).
// Se guarda con POST /api/model_options (part_animations); la exportación genera los .ksanim.
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";

const PROJECT = new URLSearchParams(location.search).get("project");
const side = document.getElementById("side"), view = document.getElementById("view");
const el = (t, p = {}, ...k) => { const n = Object.assign(document.createElement(t), p); n.append(...k); return n; };
const api = async (u, body) => {
  const r = await fetch(u, body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {});
  const d = await r.json(); if (!r.ok) throw new Error(d.error || r.statusText); return d;
};
const KINDS = [["door_L", "Puerta izquierda"], ["door_R", "Puerta derecha"], ["wiper", "Limpiaparabrisas"], ["shift", "Palanca de cambios"]];
const HINGES = [["front", "Parte delantera de la pieza"], ["back", "Parte trasera de la pieza"], ["left", "Lado izquierdo de la pieza"],
  ["right", "Lado derecho de la pieza"], ["top", "Parte superior de la pieza"], ["bottom", "Parte inferior de la pieza"], ["center", "Centro de la pieza"]];
const DEFAULTS = { door_L: ["front", 60], door_R: ["front", -60], wiper: ["bottom", 70], shift: ["bottom", 12] };
const AXIS = { door_L: [0, 1, 0], door_R: [0, 1, 0], wiper: [0, 0, 1], shift: [1, 0, 0] };  // = part_anim.DEFAULT_AXIS
const kindName = (k) => (KINDS.find((x) => x[0] === k) || [k, k])[1];
const prof = (kind, u) => (kind === "wiper" ? Math.sin(Math.PI * u) : u * u * (3 - 2 * u));  // = part_anim.profile

async function main() {
  const r = await fetch(`/api/parts_geometry?project=${encodeURIComponent(PROJECT)}`);
  if (!r.ok) { side.textContent = ((await r.json().catch(() => ({}))).error) || r.statusText; return; }
  const buf = await r.arrayBuffer();
  const hlen = new Uint32Array(buf, 0, 1)[0];
  const head = JSON.parse(new TextDecoder().decode(new Uint8Array(buf, 4, hlen)));
  let off = 4 + hlen;
  const saved = (await api(`/api/model_options?project=${encodeURIComponent(PROJECT)}`)).part_animations || [];

  const renderer = new THREE.WebGLRenderer({ antialias: true });
  view.append(renderer.domElement);
  const scene = new THREE.Scene(); scene.background = new THREE.Color(0x1e1e2e);
  const camera = new THREE.PerspectiveCamera(45, 1, 0.01, 100); camera.position.set(3.4, 2.0, -4.6);
  scene.add(new THREE.GridHelper(10, 20, 0x555555, 0x2a2f36), new THREE.HemisphereLight(0xffffff, 0x334, 1.5));
  const sun = new THREE.DirectionalLight(0xffffff, 1.3); sun.position.set(2, 5, 3); scene.add(sun);
  const root = new THREE.Group(); root.scale.x = -1;  // el marco de AC es zurdo: así se ve como en el juego
  scene.add(root);
  const orbit = new OrbitControls(camera, renderer.domElement); orbit.target.set(0, 0.7, 0);

  const pieces = head.pieces.map((p, i) => {
    const pos = new Float32Array(buf, off, p.nv * 3); off += p.nv * 12;
    const idx = new Uint32Array(buf, off, p.ni); off += p.ni * 4;
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(pos, 3)); g.setIndex(new THREE.BufferAttribute(idx, 1)); g.computeVertexNormals();
    g.computeBoundingBox();
    const color = new THREE.Color().setHSL(((i * 137.508) % 360) / 360, 0.65, 0.55);
    const mat = new THREE.MeshStandardMaterial({ color, side: THREE.DoubleSide, transparent: true, opacity: 0.9 });
    const mesh = new THREE.Mesh(g, mat); mesh.userData.i = i; root.add(mesh);
    const label = p.material.replace(/^material_\d+_/, "").replace(/_[0-9a-f]{5}$/, "") || p.name;
    return { ...p, mesh, color, label, bbox: g.boundingBox };
  });

  let sel = -1, anims = saved.filter((a) => a.hinge && pieces.some((p) => p.ref === a.node)), hideWheels = true, filter = "";
  const dimOthers = () => {
    for (const [i, p] of pieces.entries()) {
      p.mesh.visible = !(hideWheels && p.wheel);
      p.mesh.material.opacity = sel < 0 || i === sel || anims.some((a) => a.node === p.ref) ? 0.95 : 0.25;
      p.mesh.material.emissive.set(i === sel ? 0x444444 : 0x000000);
    }
  };
  const pivotOf = (p, hinge) => {  // = part_anim.pivot_from_hinge (marco del KN5)
    const lo = p.bbox.min, hi = p.bbox.max, c = lo.clone().add(hi).multiplyScalar(0.5);
    const v = c.clone();
    if (hinge === "front") v.z = hi.z; else if (hinge === "back") v.z = lo.z; else if (hinge === "left") v.x = hi.x;
    else if (hinge === "right") v.x = lo.x; else if (hinge === "top") v.y = hi.y; else if (hinge === "bottom") v.y = lo.y;
    return v;
  };
  const pose = (p, kind, hinge, deg, u) => {  // mueve la malla como lo hará el .ksanim
    const q = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(...AXIS[kind]).normalize(), THREE.MathUtils.degToRad(deg) * prof(kind, u));
    const piv = pivotOf(p, hinge);
    p.mesh.quaternion.copy(q); p.mesh.position.copy(piv.clone().sub(piv.clone().applyQuaternion(q)));
  };
  const rest = (p) => { p.mesh.quaternion.identity(); p.mesh.position.set(0, 0, 0); };

  // ---------------------------------------------------------------- panel
  const q = el("input", { type: "search", placeholder: "Buscar pieza (material o nombre)…", oninput: (e) => { filter = e.target.value.toLowerCase(); renderList(); } });
  const listBox = el("div", { id: "list" });
  const kind = el("select", {}, ...KINDS.map(([v, t]) => el("option", { value: v }, t)));
  const hinge = el("select", {}, ...HINGES.map(([v, t]) => el("option", { value: v }, t)));
  const angle = el("input", { type: "number", min: "-180", max: "180", step: "any", value: "60" });
  const slider = el("input", { type: "range", min: "0", max: "100", value: "0" });
  const addedBox = el("div", { id: "added" }), msg = el("div", { id: "msg", className: "muted" });
  const say = (t, c = "") => { msg.textContent = t; msg.className = c || "muted"; };
  const preview = () => { if (sel < 0) return; pose(pieces[sel], kind.value, hinge.value, Number(angle.value) || 0, slider.value / 100); };
  kind.onchange = () => { [hinge.value, angle.value] = DEFAULTS[kind.value]; slider.value = 0; preview(); };
  for (const x of [hinge, angle, slider]) x.oninput = preview;
  const select = (i) => {
    if (sel >= 0) rest(pieces[sel]);
    sel = i; slider.value = 0;
    const a = anims.find((x) => x.node === pieces[i].ref);
    if (a) { kind.value = a.kind; hinge.value = a.hinge; angle.value = a.angle_deg; }
    dimOthers(); renderList(); preview();
    say(`Pieza elegida: «${pieces[i].label}» (${pieces[i].tris} triángulos). Mueve la barra «Probar» para ver el giro.`);
  };
  function renderList() {
    const rows = pieces.map((p, i) => [p, i]).filter(([p]) => !(hideWheels && p.wheel) && (!filter || `${p.label} ${p.name}`.toLowerCase().includes(filter)));
    listBox.replaceChildren(...rows.map(([p, i]) => {
      const a = anims.find((x) => x.node === p.ref);
      return el("div", { className: "piece" + (i === sel ? " sel" : ""), onclick: () => select(i) },
        el("span", { className: "sw", style: `background:#${p.color.getHexString()}` }),
        el("div", { className: "t" }, el("div", { className: "n" }, p.label + (a ? `  ✔ ${kindName(a.kind)}` : "")),
          el("div", { className: "muted" }, `${p.name}${p.ref.includes("#") ? " (" + p.ref.split("#")[1] + ")" : ""} · ${p.tris} triángulos`)));
    }));
  }
  function renderAdded() {
    addedBox.replaceChildren(...anims.map((a) => {
      const p = pieces.find((x) => x.ref === a.node);
      return el("div", {}, el("span", { className: "sw", style: `background:#${p ? p.color.getHexString() : "888"}` }),
        `${kindName(a.kind)} → ${p ? p.label : a.node} (${a.angle_deg}°)`,
        el("button", { className: "secondary", type: "button", style: "margin:0 0 0 auto;padding:2px 8px", onclick: () => { anims = anims.filter((x) => x !== a); renderAdded(); renderList(); dimOthers(); } }, "Quitar"));
    }));
  }
  const add = () => {
    if (sel < 0) { say("Elige primero una pieza de la lista o haz clic en ella en el modelo.", "warn"); return; }
    anims = anims.filter((x) => x.kind !== kind.value && x.node !== pieces[sel].ref);  // una por tipo y por pieza
    anims.push({ kind: kind.value, node: pieces[sel].ref, hinge: hinge.value, angle_deg: Number(angle.value) || 0 });
    renderAdded(); renderList(); dimOthers(); say(`Añadida: ${kindName(kind.value)} → «${pieces[sel].label}». Pulsa «Guardar» al terminar.`, "ok");
  };
  const save = async () => {
    try {
      const r = await api("/api/model_options", { project: PROJECT, part_animations: anims });
      parent.postMessage({ type: "parts-saved", animations: r.part_animations }, location.origin);
      say(`Guardado (${r.part_animations.length}). Se aplican en la próxima exportación (el modelo 3D se reconstruye).`, "ok");
    } catch (e) { say(e.message, "warn"); }
  };
  side.replaceChildren(
    el("div", { id: "top" }, el("h3", {}, "Animaciones de piezas"),
      el("div", { className: "muted" }, "Cada pieza del modelo tiene un color. Elige una (en la lista o con un clic en el coche), dile qué es, de qué lado está su bisagra y cuántos grados gira, y mueve la barra «Probar» para verlo. Automation no separa las puertas ni los limpias: si la pieza que buscas no existe por separado, aquí no aparecerá como pieza propia."),
      q, el("label", { className: "muted", style: "display:block;margin-top:6px" }, el("input", { type: "checkbox", checked: true, onchange: (e) => { hideWheels = e.target.checked; renderList(); dimOthers(); } }), " Ocultar ruedas y suspensión")),
    listBox,
    el("div", { id: "ctl" },
      el("label", {}, "Qué es"), kind, el("label", {}, "Bisagra"), hinge, el("label", {}, "Giro (grados; negativo = al otro lado)"), angle,
      el("label", {}, "Probar el movimiento"), slider,
      el("button", { type: "button", onclick: add }, "Añadir esta animación"), el("button", { type: "button", className: "secondary", onclick: save }, "Guardar"),
      el("div", { className: "muted", style: "margin-top:8px" }, "Animaciones añadidas:"), addedBox, msg));
  kind.onchange(); renderAdded(); renderList(); dimOthers();

  const ray = new THREE.Raycaster(), ptr = new THREE.Vector2();
  renderer.domElement.addEventListener("click", (e) => {
    const b = renderer.domElement.getBoundingClientRect();
    ptr.set(((e.clientX - b.left) / b.width) * 2 - 1, -((e.clientY - b.top) / b.height) * 2 + 1);
    ray.setFromCamera(ptr, camera);
    const hit = ray.intersectObjects(pieces.filter((p) => p.mesh.visible).map((p) => p.mesh), false)[0];
    if (hit) select(hit.object.userData.i);
  });
  const resize = () => { const w = view.clientWidth, h = view.clientHeight; renderer.setSize(w, h); camera.aspect = w / h; camera.updateProjectionMatrix(); };
  new ResizeObserver(resize).observe(view); resize();
  renderer.setAnimationLoop(() => { orbit.update(); renderer.render(scene, camera); });
}
main().catch((e) => { side.textContent = "No se pudo cargar el modelo: " + e.message; });
