// beam2ac · aplicación de escritorio. Sin bibliotecas externas; todo el texto con textContent.
"use strict";

const VERDICT_ES = {
  bloqueado: "no convertible", completo_sin_probar_en_el_juego: "convertido", incompleto: "incompleto",
};
const STAGE_ES = { ok: "hecha", cached: "en caché", failed: "fallida", skipped: "omitida", no_ejecutada: "no ejecutada" };
let WS = null;
let CURRENT = null;
let DETAIL = null;
let DETAILS_BLOCK = null;  // bloque «Detalles del coche» del proyecto abierto (se carga al abrir la pestaña)

function el(tag, attrs = {}, ...children) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") n.className = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else if (v === true) n.setAttribute(k, "");
    else if (v !== undefined && v !== null && v !== false) n.setAttribute(k, v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    n.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return n;
}
// Esqueleto de carga (barras con brillo, estilo YouTube): se sustituye con replaceChildren cuando llega el contenido
function skeleton(lines = 3, label = "Cargando…") {
  return el("div", { class: "skel-box", role: "status", "aria-label": label },
    Array.from({ length: lines }, (_, i) => el("div", { class: "skel-line", style: `width:${[92, 78, 64, 85][i % 4]}%` })));
}
const fmt = (v) => (v === null || v === undefined) ? "—" : (typeof v === "object" ? JSON.stringify(v) : String(v));
const $ = (id) => document.getElementById(id);

async function api(path, body) {
  const opts = body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {};
  const r = await fetch(path, opts);
  let data;
  try { data = await r.json(); } catch (e) { throw new Error(`${r.status} ${r.statusText}`); }
  if (!r.ok) throw new Error(data.error || r.statusText);
  return data;
}
function toast(text, kind = "") {
  const t = $("toast");
  t.textContent = text; t.className = kind; t.hidden = false;
  clearTimeout(toast._t); toast._t = setTimeout(() => { t.hidden = true; }, 6000);
}
function verdict(v) { return el("span", { class: `verdict ${v}` }, VERDICT_ES[v] || v); }
function table(headers, rows) {
  return el("table", {}, el("tr", {}, headers.map((h) => el("th", {}, h))),
    rows.map((r) => el("tr", {}, r.map((c) => el("td", {}, c instanceof Node ? c : fmt(c))))));
}

// ------------------------------------------------------------------ espacio de trabajo
async function loadWorkspace() {
  WS = await api("/api/workspace");
  const list = $("projects");
  list.replaceChildren();
  if (!WS.projects.length) list.append(el("p", { class: "muted small" }, "Sin proyectos todavía."));
  for (const p of WS.projects) {
    const last = p.last_export;
    list.append(el("div", { class: "pwrap" },
      el("button", { class: "pcard", type: "button", "aria-current": String(p.id === CURRENT), onclick: () => openProject(p.id) },
        el("div", { class: "name" }, p.id),
        p.error ? el("div", { class: "line warn" }, p.error) : el("div", { class: "line" }, sourceText(p.source)),
        el("div", { class: "line" }, last ? verdict(last.verdict) : "sin convertir"),
        p.job && p.job.status === "en_curso" ? el("div", { class: "line" }, `⏳ ${p.job.kind} en curso`) : null),
      el("button", { class: "pdel", type: "button", title: "Borrar proyecto", "aria-label": "Borrar proyecto", onclick: () => deleteProject(p.id) }, "✕")));
  }
}
async function deleteProject(id) {
  if (!confirm(`¿Quitar el proyecto «${id}»? Su carpeta se mueve a la carpeta «_papelera» de los datos de beam2ac (recuperable a mano).
Los coches instalados en Assetto Corsa y las copias en builds/ no se tocan.`)) return;
  try {
    await api("/api/delete_project", { project: id, confirm: true });
    toast(`Proyecto ${id} borrado`, "ok");
    if (CURRENT === id) showHome(); else await loadWorkspace();
  } catch (e) { toast(e.message, "warn"); }
}
function sourceText(s) {
  if (!s) return "—";
  const name = s.path.split(/[\\/]/).pop();
  return `${s.kind === "zip" ? "zip" : "carpeta"} ${name}${s.pc ? " · " + s.pc : ""}`;
}

// ------------------------------------------------------------------ inicio: análisis y entorno
const LEVEL_ES = { direct: "directa", approximation: "aproximación", fidelity_loss: "pérdida de fidelidad",
  not_representable: "sin equivalente en AC", pending_research: "sin investigar" };
const COV_ES = { medida: "del origen", referencia: "valor de referencia", formato: "formato", no_aplica: "no aplica",
  propuesta: "propuesta sin aprobar", sin_regla: "sin regla" };
function showHome() {
  CURRENT = null;
  $("welcome").hidden = false; $("project").hidden = true;
  loadWorkspace();
}
// Conflicto de masa con la ficha de Automation: el usuario decide (src/app/mass_choice.py).
// MASS_CHOICE = {path, pc, value}: "measured" | "declared" | kg; se reenvía al reanalizar y al convertir.
let MASS_CHOICE = null;
const massChoiceFor = (path, pc) => (MASS_CHOICE && MASS_CHOICE.path === path && (!pc || !MASS_CHOICE.pc || MASS_CHOICE.pc === pc)) ? MASS_CHOICE.value : null;
const MASS_LABEL = { measured: "continuar con la masa calculada", declared: "usar la masa que declara Automation", manual: "masa introducida a mano" };
async function chooseMass(r, value) {
  MASS_CHOICE = { path: r.path, pc: r.config || null, value };
  await analyze(r.path, r.config, r.driver_mass, document.querySelector("#analysis form"));
}
function massNotice(r) {
  const m = r.mass_conflict, c = m.choice;
  const below = m.difference_kg < 0;
  return el("div", { class: c ? "notice ok" : "notice err" },
    el("strong", {}, below ? "La masa calculada está por debajo de la declarada. " : "La masa calculada está por encima de la declarada. "),
    `${m.measured_kg} kg frente a ${m.declared_kg} kg (${m.difference_kg > 0 ? "+" : ""}${m.difference_kg} kg). `,
    c ? ["Elección: ", MASS_LABEL[c.mode], c.mode === "manual" ? ` (${c.kg} kg). ` : ". "] : "Hay que decidir qué masa usar. ",
    el("button", { class: "secondary", type: "button", onclick: () => massDialog(r) }, c ? "Cambiar la elección" : "Decidir la masa"));
}
function massDialog(r) {
  const m = r.mass_conflict;
  const dlg = el("dialog", { class: "massdlg" });
  const pick = (value) => { dlg.close(); dlg.remove(); chooseMass(r, value); };
  const num = el("input", { type: "number", step: "0.01", min: "1", value: m.declared_kg, "aria-label": "Masa del coche sin piloto (kg)" });
  dlg.append(
    el("h3", {}, m.difference_kg < 0 ? "La masa calculada está por debajo de la declarada" : "La masa calculada está por encima de la declarada"),
    el("p", {}, "Masa calculada en el coche: ", el("strong", {}, `${m.measured_kg} kg`)),
    el("p", {}, "Masa declarada por Automation: ", el("strong", {}, `${m.declared_kg} kg`)),
    el("p", { class: "muted small" }, "Elige qué masa (coche sin piloto) debe usar la conversión. La elección se guarda como decisión del proyecto. El centro de gravedad y la inercia no cambian: la diferencia se trata como masa situada en el centro de gravedad."),
    el("div", { class: "actions" },
      el("button", { class: "primary", type: "button", onclick: () => pick("measured") }, "Continuar con la masa calculada"),
      el("button", { class: "secondary", type: "button", onclick: () => pick("declared") }, "Usar la masa de Automation")),
    el("div", { class: "actions" }, el("label", {}, "Introducir una masa (kg)"), num,
      el("button", { class: "secondary", type: "button", onclick: () => { const v = parseFloat(num.value); if (!(v > 0 && v < 100000)) { num.focus(); return; } pick(String(v)); } }, "Usar esta masa")),
    el("div", { class: "actions" }, el("button", { class: "secondary", type: "button", onclick: () => { dlg.close(); dlg.remove(); } }, "Decidir más tarde")));
  document.body.append(dlg);
  dlg.showModal();
}
// Dos vistas 3D del modal del volante (three.js local, sin importmap por la CSP): el coche con el material elegido
// en rojo y solo ese material. shortcut: órbita propia mínima (arrastrar/rueda), sin pan ni táctil.
let THREE_P;
const loadThree = () => (THREE_P ||= import("/static/vendor/three-0.160.0/three.module.js"));
// Caché de geometrías en memoria (url → Promise<ArrayBuffer>) compartida con las vistas 3D incrustadas (cockpit, animación):
// se precarga al abrir el proyecto y se vacía al abrir otro (o el mismo, tras una tarea que pudo cambiar el modelo).
const GEO = new Map();
window.b2aGeo = (url) => {
  if (!GEO.has(url)) {
    const p = fetch(url).then(async (r) => {
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || r.statusText);
      return r.arrayBuffer();
    });
    p.catch(() => GEO.delete(url));  // un fallo no se cachea
    GEO.set(url, p);
  }
  return GEO.get(url);
};
function prefetchGeometry(pid) {
  if (!hasModel()) return;
  const q = encodeURIComponent(pid);
  for (const u of [`/api/cockpit_geometry?project=${q}`, `/api/steering_geometry?project=${q}`]) window.b2aGeo(u).catch(() => {});
}
async function fetchGeo(T, url) {
  const buf = await window.b2aGeo(url), [nv, ni] = new Uint32Array(buf, 0, 2), g = new T.BufferGeometry();
  g.setAttribute("position", new T.BufferAttribute(new Float32Array(buf, 8, nv * 3), 3));
  g.setIndex(new T.BufferAttribute(new Uint32Array(buf, 8 + nv * 12, ni), 1));
  g.computeVertexNormals();
  return g;
}
function mini3d(T, canvas) {
  const rd = new T.WebGLRenderer({ canvas, antialias: true }), sc = new T.Scene(), cam = new T.PerspectiveCamera(40, 1, 0.01, 100);
  sc.background = new T.Color(0x1e1e2e);
  sc.add(new T.HemisphereLight(0xffffff, 0x334, 1.6)); const sun = new T.DirectionalLight(0xffffff, 1.2); sun.position.set(2, 5, 3); sc.add(sun);
  const v = { az: 2.4, el: 0.4, d: 6, c: new T.Vector3(0, 0.7, 0) };
  const draw = () => {
    const w = canvas.clientWidth || 300, h = canvas.clientHeight || 220;
    rd.setSize(w, h, false); cam.aspect = w / h; cam.updateProjectionMatrix();
    cam.position.set(v.c.x + v.d * Math.cos(v.el) * Math.sin(v.az), v.c.y + v.d * Math.sin(v.el), v.c.z + v.d * Math.cos(v.el) * Math.cos(v.az));
    cam.lookAt(v.c); rd.render(sc, cam);
  };
  canvas.addEventListener("pointermove", (e) => { if (e.buttons & 1) { v.az -= e.movementX * 0.01; v.el = Math.max(-1.5, Math.min(1.5, v.el + e.movementY * 0.01)); draw(); } });
  canvas.addEventListener("wheel", (e) => { e.preventDefault(); v.d = Math.max(0.1, v.d * (e.deltaY > 0 ? 1.1 : 0.9)); draw(); }, { passive: false });
  return { sc, draw, v, rd, frame(g) { g.computeBoundingSphere(); v.c.copy(g.boundingSphere.center); v.d = g.boundingSphere.radius * 3.2; } };
}
// Ventana flotante del volante: detección automática + elección manual del material del aro
// (src/ac/steer_detect.py; se guarda en <proyecto>/steering_wheel.json y entra en la clave de la etapa del modelo).
async function steeringDialog() {
  const pid = CURRENT;
  const dlg = el("dialog", { class: "massdlg" });
  const close = () => { dlg.close(); dlg.remove(); };
  const msg = el("p", { class: "small" });
  const sel = el("select", { "aria-label": "Material del aro del volante" });
  const say = (text, cls = "") => { msg.dataset.anim = ""; msg.textContent = text; msg.className = "small " + cls; };
  // texto con puntos suspensivos animados (. → .. → ...); se detiene al desconectarse el nodo o al llamar a say()
  const animDots = (node, text) => {
    node.dataset.anim = "1"; node.textContent = text + "."; let n = 1;
    const t = setInterval(() => { if (node.dataset.anim !== "1" || !node.isConnected) return clearInterval(t); node.textContent = text + ".".repeat(n++ % 3 + 1); }, 400);
  };
  const loading = el("p", { class: "muted small" }); animDots(loading, "Cargando vista 3D, puede tardar");
  dlg.append(el("h3", {}, "Volante (STEER_HR)"), loading);
  document.body.append(dlg); dlg.showModal();
  let info;
  try { info = await api(`/api/steering?project=${encodeURIComponent(pid)}`); }
  catch (e) { dlg.replaceChildren(el("h3", {}, "Volante (STEER_HR)"), el("div", { class: "notice err" }, e.message), el("div", { class: "actions" }, el("button", { class: "secondary", type: "button", onclick: close }, "Cerrar"))); return; }
  const cvA = el("canvas", { class: "steerview", title: "Coche: el material elegido en rojo" }), cvB = el("canvas", { class: "steerview", title: "Solo el material elegido" });
  const views = el("div", { class: "steerviews" }, cvA, cvB);
  let T, A, B, carMesh, partA, partB, reqId = 0;
  const showMat = async () => {
    if (!A) return;
    const id = ++reqId;
    for (const o of [partA, partB]) if (o) { o.parent.remove(o); o.geometry.dispose(); }
    partA = partB = null;
    if (sel.value) {
      try {
        const g = await fetchGeo(T, `/api/steering_geometry?project=${encodeURIComponent(pid)}&material=${encodeURIComponent(sel.value)}`);
        if (id !== reqId) return;
        partA = new T.Mesh(g, new T.MeshBasicMaterial({ color: 0xff0a28, side: T.DoubleSide, depthTest: false })); partA.renderOrder = 2; A.sc.add(partA);
        partB = new T.Mesh(g.clone(), new T.MeshStandardMaterial({ color: 0xcdd6f4, side: T.DoubleSide })); B.sc.add(partB);
        B.frame(g);
      } catch (e) { say(e.message, "warn"); }
    }
    A.draw(); B.draw();
  };
  sel.addEventListener("change", showMat);
  const label = (m) => `${m.material} · ${m.vertices} vértices · ${m.distance_m} m de los ojos`;
  const opt = (m) => el("option", { value: m.material }, label(m));
  sel.append(el("option", { value: "" }, "— automático (sin elección) —"),
    el("optgroup", { label: "Pequeños y cerca de los ojos" }, info.materials.filter((m) => m.likely).map(opt)),
    el("optgroup", { label: "Resto de materiales" }, info.materials.filter((m) => !m.likely).map(opt)));
  sel.value = info.saved || "";
  const detect = async () => {
    try { const r = await api("/api/steering", { project: pid, action: "detect" }); sel.value = r.rim_material; showMat(); say(`Detectado: ${r.rim_material}. Pulsa «Guardar» para usarlo.`, "ok"); }
    catch (e) { say(e.message, "warn"); }
  };
  const save = async () => {
    try {
      if (sel.value) { await api("/api/steering", { project: pid, action: "save", rim_material: sel.value }); say("Elección guardada: se aplica en la próxima exportación (el modelo se reconstruye).", "ok"); }
      else { await api("/api/steering", { project: pid, action: "clear" }); say("Sin elección: se usará la detección automática en la próxima exportación.", "ok"); }
    } catch (e) { say(e.message, "warn"); }
  };
  dlg.replaceChildren(
    el("h3", {}, "Volante (STEER_HR)"),
    el("p", { class: "muted small" }, "El conversor busca un aro delante de los ojos del piloto. Si no acierta, elige el material del aro; las radios y el resto se calculan alrededor."),
    el("p", { class: "small" }, "Detección automática ahora: ", el("strong", {}, info.auto || "no encuentra volante"), info.auto_error ? ` (${info.auto_error})` : ""),
    el("div", { class: "actions" }, el("button", { class: "primary", type: "button", onclick: detect }, "Detectar automáticamente"), sel),
    views,
    el("p", { class: "muted small" }, "Izquierda: el coche con el material elegido en rojo. Derecha: solo ese material. Arrastra para girar, rueda para acercar."),
    msg,
    el("div", { class: "actions" },
      el("button", { class: "secondary", type: "button", onclick: save }, "Guardar"),
      el("button", { class: "secondary", type: "button", onclick: close }, "Cerrar")));
  try {
    animDots(msg, "Cargando vista 3D, puede tardar"); msg.className = "small muted";
    T = await loadThree();
    A = mini3d(T, cvA); B = mini3d(T, cvB);
    const g = await fetchGeo(T, `/api/steering_geometry?project=${encodeURIComponent(pid)}`);
    carMesh = new T.Mesh(g, new T.MeshStandardMaterial({ color: 0x8895a3, transparent: true, opacity: 0.3, side: T.DoubleSide, depthWrite: false }));
    A.sc.add(carMesh); A.frame(g);
    await showMat();
    if (msg.dataset.anim === "1") say("");
  } catch (e) { say("Vista 3D no disponible: " + e.message, "warn"); }
}
// Detalles del coche en AC (ui_car.json) y portada (skins/<skin>/preview.jpg); src/project/details.py
function coverFromFile(file) {  // recorte centrado 16:9 y 1024×576 en el navegador (el servidor limita el cuerpo a 1 MB)
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => {
      const W = 1024, H = 576, c = document.createElement("canvas"); c.width = W; c.height = H;
      const s = Math.max(W / img.width, H / img.height), w = img.width * s, h = img.height * s;
      c.getContext("2d").drawImage(img, (W - w) / 2, (H - h) / 2, w, h);
      resolve(c.toDataURL("image/jpeg", 0.9));
    };
    img.onerror = () => reject(new Error("no se puede leer la imagen"));
    img.src = URL.createObjectURL(file);
  });
}
// Bloque «Detalles del coche» dentro de Exportar. Se rellena al abrir la pestaña (load) y precarga la portada:
// la subida o, si no hay, la vista previa automática (se guarda por proyecto para no repetir el render).
const COVER_CACHE = {}, PREVIEW_BUSY = {};  // PREVIEW_BUSY: un solo render de Blender por proyecto a la vez
function detailsBlock(pid) {
  const box = el("div", { class: "card detailsbox" }, el("h4", {}, "Detalles del coche"),
    skeleton(6, "Cargando…"));
  // Intro dentro de un campo no debe enviar el formulario de exportación
  box.addEventListener("keydown", (e) => { if (e.key === "Enter" && e.target.tagName !== "TEXTAREA") e.preventDefault(); });
  // Los campos se cargan al crear el bloque (es solo JSON); el render de la portada (Blender, ~20 s) se pide con
  // box.load() cuando la pestaña está a la vista, para no gastarlo si el usuario no la abre.
  box.load = () => {};  // la portada ya se pide al cargar el proyecto
  const init = async () => {
    let info;
    try { info = await api(`/api/details?project=${encodeURIComponent(pid)}`); }
    catch (e) { box.replaceChildren(el("h4", {}, "Detalles del coche"), el("div", { class: "notice err" }, e.message)); return; }
    const s = info.saved || {}, l = info.last_export || {};
    const msg = el("p", { class: "small" });
    const f = (name, label, extra = {}, tip = "") => el("div", {}, el("label", {}, label), el("input", { name: `car_${name}`, value: s[name] ?? "", placeholder: l[name] ?? "", ...extra }), tip ? el("div", { class: "muted small" }, tip) : null);
    const desc = el("textarea", { name: "car_description", rows: "4", placeholder: l.description ?? "" }, s.description ?? "");
    const cls = el("select", { name: "car_class" }, el("option", { value: "" }, "— sin clase —"), info.classes.map((c) => el("option", { value: c, selected: c === (s.class ?? l.class) }, c)));
    const tags = el("input", { name: "car_tags", value: (s.tags || []).join(", "), placeholder: (l.tags || []).join(", ") });
    const preview = el("img", { class: "coverprev", alt: "Portada" });
    // imagen «esqueleto» (16:9, con brillo) mientras la portada no esté cargada
    const skel = el("div", { class: "coverskel" }, el("span", {}, "Cargando…"));
    const prevNote = el("p", { class: "muted small" });
    const showPreview = (url, note, failed = false) => {
      if (url) preview.src = url;
      preview.hidden = !url; skel.hidden = !!url;
      skel.classList.toggle("failed", failed);
      skel.firstChild.textContent = failed ? "Sin vista previa" : "Cargando…";
      prevNote.textContent = note;
    };
    const autoPreview = async (force) => {
      if (!force && COVER_CACHE[pid]) { showPreview(COVER_CACHE[pid], "Vista previa automática: es la que llevará el coche si no subes una imagen."); return; }
      showPreview(null, "Generando la vista previa automática (unos segundos)…");
      try { const r = await (PREVIEW_BUSY[pid] ||= api(`/api/details_preview?project=${encodeURIComponent(pid)}`).finally(() => { delete PREVIEW_BUSY[pid]; })); COVER_CACHE[pid] = r.cover; showPreview(r.cover, "Vista previa automática: es la que llevará el coche si no subes una imagen."); }
      catch (e) { showPreview(null, `Sin vista previa: ${e.message}`, true); }
    };
    let cover = null, clear = false;
    if (info.cover) showPreview(info.cover, "Imagen subida por ti.");
    else {
      showPreview(null, "Generando la vista previa automática (unos segundos)…");
      // se genera al cargar el proyecto (el render cuesta ~20 s; mientras tanto se ve el esqueleto)
      if (!SOLO) autoPreview(false);  // las ventanas de panel no repiten el render
    }
    const file = el("input", { type: "file", accept: "image/*", onchange: async () => {
      if (!file.files[0]) return;
      try { cover = await coverFromFile(file.files[0]); clear = false; showPreview(cover, "Imagen elegida (se aplica al guardar)."); msg.textContent = ""; }
      catch (e) { msg.textContent = e.message; msg.className = "small warn"; } } });
    const save = async () => {
      const body = { project: pid, cover, clear_cover: clear, description: desc.value, class: cls.value, tags: tags.value };
      for (const n of ["name", "brand", "country", "year", "author"]) body[n] = box.querySelector(`[name=car_${n}]`).value;
      try { await api("/api/details", body); cover = null; msg.textContent = "Guardado: se aplica en la próxima exportación."; msg.className = "small ok"; }
      catch (e) { msg.textContent = e.message; msg.className = "small warn"; }
    };
    box.replaceChildren(
      el("h4", {}, "Detalles del coche"),
      el("p", { class: "muted small" }, "Lo que dejes vacío se queda como lo calcula el conversor (en gris, el valor de la última exportación)."),
      f("name", "Nombre del coche", {}, "El nombre que verás en el menú de selección de coches de Assetto Corsa."),
      el("div", {}, el("label", {}, "Descripción"), desc, el("div", { class: "muted small" }, "Texto que aparece en la ficha del coche dentro del juego.")),
      el("div", { class: "row" }, el("div", {}, el("label", {}, "Clase"), cls, el("div", { class: "muted small" }, "Categoría para filtrar el coche en el menú (por ejemplo, «street» o «race»).")), f("brand", "Marca", {}, "Fabricante que se muestra en la ficha.")),
      el("div", { class: "row" }, f("country", "País", {}, "País de origen que se muestra en la ficha."), f("year", "Año", { type: "number", min: "1800", max: "2100" }, "Año del modelo.")),
      el("div", { class: "row" }, f("author", "Autor", {}, "Quién figura como creador del coche."), el("div", {}, el("label", {}, "Etiquetas (separadas por comas)"), tags, el("div", { class: "muted small" }, "Palabras clave para buscar el coche (por ejemplo: turbo, deportivo)."))),
      el("div", {}, el("label", {}, "Imagen de portada"), file, skel, preview, prevNote,
        el("p", { class: "muted small" }, "Es la imagen del coche en el menú del juego. Si no subes ninguna, se genera sola una vista del coche desde arriba en diagonal, recortada a 16:9.")),
      msg,
      el("div", { class: "actions" },
        el("button", { class: "primary", type: "button", title: "Guarda estos datos en el proyecto; se aplican en la próxima exportación", onclick: save }, "Guardar"),
        el("button", { class: "secondary", type: "button", title: "Descarta la imagen que subiste y vuelve a usar la automática", onclick: () => { cover = null; clear = true; file.value = ""; msg.textContent = "Se quitará la imagen subida al guardar."; msg.className = "small"; autoPreview(false); } }, "Quitar la imagen subida"),
        el("button", { class: "secondary", type: "button", title: "Vuelve a crear la imagen automática del coche (unos segundos)", onclick: () => autoPreview(true) }, "Generar vista previa")));
  };
  init();
  return box;
}
function analysisView(r, withCreate) {
  if (!r.ok) return el("div", { class: "notice err" }, el("strong", {}, `No se pudo analizar ${r.vehicle}: `), r.error, el("div", { class: "small" }, r.hint));
  const id = r.identity;
  const cov = r.coverage_totals;
  const out = el("div", {},
    el("h3", {}, id.name || r.vehicle),
    el("p", { class: "muted small" }, `${id.source_tool || "origen desconocido"} · exportador ${id.exporter_version || "—"} · ${r.path}`),
    withCreate
      ? el("div", { class: r.exportable_today ? "notice ok" : "notice err" },
        r.exportable_today ? "Coche compatible: la conversión empieza automáticamente."
          : !r.in_scope ? el("div", {}, el("strong", {}, "Este coche no se puede convertir. "),
            "Solo se convierten coches creados en Automation y exportados a BeamNG; este parece un coche original de BeamNG u otro mod.")
          : el("div", {}, el("strong", {}, "Este coche todavía no se puede convertir. "), `Faltan ${r.blockers.length} datos físicos que el conversor no sabe obtener de él.`))
      : [r.in_scope ? null : el("div", { class: "notice err" }, r.scope_note),
        el("div", { class: r.exportable_today ? "notice ok" : "notice err" },
          r.exportable_today ? "Sin bloqueos de exportación conocidos." : `Hoy no se puede exportar completo: ${r.blockers.length} claves críticas sin valor.`)],
    r.mass_conflict ? massNotice(r) : null,
    el("div", { class: "kpis" }, Object.keys(COV_ES).map((k) => kpi(cov[k], COV_ES[k]))),
    el("p", { class: "muted small" }, r.note),
    el("div", { class: "grid2" },
      el("div", {}, el("h4", {}, "Componentes detectados"),
        table(["Componente", "Valor"], r.components.map((c) => [c.label, c.status === "present" ? fmt(c.value) : (c.status === "not_applicable" ? "no tiene" : "no detectado")]))),
      el("div", {}, el("h4", {}, "Compatibilidad con Assetto Corsa"),
        r.capabilities.map((f) => el("details", {}, el("summary", {}, el("span", { class: `chip ${f.level}` }, LEVEL_ES[f.level] || f.level), " ", f.feature), el("div", { class: "small" }, f.reason))))),
    audioView(r.audio),
    el("h4", {}, "Cobertura por subsistema (claves de AC)"),
    el("div", { class: "cov" }, table(["Subsistema", ...Object.values(COV_ES)], Object.entries(r.coverage).map(([sub, c]) => [sub, ...Object.keys(COV_ES).map((k) => c[k])]))),
    r.blockers.length ? el("details", {}, el("summary", {}, `Claves críticas sin valor (${r.blockers.length})`),
      table(["Clave", "Estado", "Qué pasaría en AC", "Qué hace falta"], r.blockers.map((b) => [b.target, b.state, b.why, b.next]))) : null);
  if (withCreate) {
    const det = el("details", { class: "tech" }, el("summary", {}, "Detalles técnicos del análisis"));
    for (let n = out.querySelector(".kpis"); n;) { const nx = n.nextSibling; det.append(n); n = nx; }
    out.append(det);
  }
  if (withCreate && !r.in_scope) return out;  // sin formulario: no hay nada que convertir
  if (withCreate) {
    // un formulario, dos acciones: convertir de un paso (G2.28) o solo crear el proyecto
    const f = el("form", { class: "card", onsubmit: (e) => { e.preventDefault(); (e.submitter && e.submitter.value === "create" ? createFromAnalysis : startConvert)(r, e.target); } },
      el("div", { class: "row" },
        el("div", {}, el("label", {}, "Nombre del proyecto (vacío al convertir: se elige uno libre)"),
          el("input", { name: "name", type: "text", value: r.vehicle.toLowerCase().replace(/[^a-z0-9_.-]/g, "_").slice(0, 60) })),
        el("div", {}, el("label", {}, "car_id del coche en Assetto Corsa *"),
          el("input", { name: "car_id", type: "text", required: true, pattern: "beam2ac_[a-z0-9_]+", value: r.default_car_id, oninput: () => checkCarId(f, r) }))),
      el("div", { class: "idbox" }),
      el("div", { class: "row" },
        el("div", {}, el("label", {}, "Configuración (.pc)"),
          el("select", { name: "pc" }, r.configs.map((c) => el("option", { value: c, selected: c === r.config }, c)))),
        el("div", {}, el("label", {}, "Piloto (kg) *"), 
          // G2.29.2: cambiar la masa repite el análisis; «Convertir» siempre parte del análisis de la masa mostrada
          el("input", { name: "driver_mass", type: "number", min: "0", max: "150", step: "any", required: true, value: String(r.driver_mass), onchange: () => analyze(r.path, f.pc.value, f.driver_mass.value, f) }))),
      el("label", {}, "Carpeta de destino (opcional; cualquier carpeta que no sea de AC ni de BeamNG). Vacía: el paquete queda en exports/ del proyecto. Nunca se instala en el juego."),
      el("div", { class: "actions" },
        el("input", { name: "dest", type: "text", value: (SETTINGS.saved || {}).export_dir || "", placeholder: "p. ej. D:/beam2ac/entregas", style: "flex:1;min-width:280px" }),
        window.pywebview ? el("button", { class: "secondary", type: "button", onclick: async () => { const p = await window.pywebview.api.pick_folder(); if (p) f.dest.value = p; } }, "Elegir destino…") : null),
      el("p", { class: "muted small" }, "La ruta de Assetto Corsa se toma de Ajustes: de ahí sale el piloto sentado y sus manos (solo se lee). El coche siempre lleva volante y piloto; como usa material del juego original, el paquete es solo para tu equipo: no se puede compartir ni publicar. Después puedes ajustar cámaras y piloto en «Vista 3D»."),
      noBlender() ? el("div", { class: "notice err" }, BLENDER_MSG) : null,
      acRoot() ? null : el("div", { class: "notice err" }, AC_MSG),
      r.audio && r.audio.present ? el("p", { class: "muted small" }, r.audio.fmod.compatible ? "Sonido: el motor sonará con el audio del coche; se genera solo (FMOD 1.08.12 detectado)." : "Sonido: FMOD 1.08.12 no está instalado, así que el coche saldrá sin sonido propio. Indica tu carpeta en Ajustes para incluirlo.") : null,
      r.exportable_today ? null : el("div", { class: "notice err" }, "Conversión desactivada: el análisis encontró errores bloqueantes (ver «Claves críticas sin valor»). Puede crear el proyecto y resolverlos con decisiones."),
      el("div", { class: "actions" },
        el("button", { class: "primary lockable", type: "submit", value: "convert", disabled: !r.exportable_today || noBlender() || !acRoot() }, "Comenzar la conversión"),
        el("button", { class: "secondary", type: "submit", value: "create" }, "Solo crear el proyecto"),
        el("span", { id: "an-msg", class: "small" })));
    const bv = r.exportable_today ? benchView(r, f) : null;
    if (bv) out.append(bv);
    out.append(el("div", { id: "convbox" }), el("details", { open: true }, el("summary", {}, "Opciones de conversión"), f));
    if (r.car_id_check) showCarId(f, r, r.car_id_check);
    // la conversión NO arranca sola: primero se fijan las opciones (piloto, destino, audio…)
    if (r.exportable_today) checkCarId(f, r);
  }
  return out;
}
// Al analizar un origen solo se conoce el estado de los WAV y del plan; el banco FMOD se genera al exportar
const AUDIO_ES = { detectado: "WAV detectados", validado: "WAV validados", plan_generado: "Plan de audio generado" };
function audioView(a) {
  if (!a || !a.stages) return el("p", { class: "muted small" }, "Audio: " + ((a && a.note) || "sin datos"));
  const loads = (a.series || []).filter((s) => s.role === "load"), coast = (a.series || []).filter((s) => s.role === "coast");
  return el("div", { class: "card" }, el("h4", {}, "Audio del motor"),
    el("p", { class: "small" }, a.present ? `Carga: ${loads.reduce((n, s) => n + s.samples, 0)} muestras · retención: ${coast.reduce((n, s) => n + s.samples, 0)} · rpm ${a.rpm_range ? a.rpm_range.map(Math.round).join("–") : "—"} · plan: ${a.status}` : "El origen no trae muestras de motor: se exporta sin sonido propio."),
    el("div", { class: "kpis" }, Object.entries(AUDIO_ES).map(([k, t]) => el("span", { class: `chip ${a.stages[k] ? "ok" : ""}` }, (a.stages[k] ? "✔ " : "✖ ") + t))),
    el("p", { class: "muted small" }, "El banco de sonido de FMOD se genera al exportar (opción «Generar el banco de sonido con FMOD»); aquí solo se comprueban los WAV."),
    (a.warnings || []).length ? el("ul", { class: "small" }, a.warnings.map((w) => el("li", {}, w))) : null,
    (a.dropped || []).length ? el("details", {}, el("summary", {}, `Muestras descartadas (${a.dropped.length})`), table(["Archivo", "rpm", "Motivo"], a.dropped.map((x) => [x.file, x.rpm, x.reason]))) : null);
}
// G2.29.1: el conflicto de car_id se avisa antes de «Convertir»; el servidor lo sigue rechazando igual
function showCarId(f, r, c) {
  if (c.car_id !== f.car_id.value.trim()) return;  // respuesta de un valor que ya cambió
  f.dataset.idConflict = c.ok ? "" : "1";
  f.querySelector("button[value=convert]").disabled = !r.exportable_today || !c.ok;
  f.querySelector(".idbox").replaceChildren(c.ok
    ? el("div", { class: "muted small" }, c.checked ? `Identificador libre en ${c.ac_root}.` : "Assetto Corsa no encontrado: no se pudo comprobar si el identificador ya está instalado.")
    : el("div", { class: "notice err" }, el("strong", {}, "Conversión desactivada por el car_id: "), c.reason,
      c.suggestion ? el("div", { class: "actions" }, el("button", { class: "secondary", type: "button", onclick: () => { f.car_id.value = c.suggestion; checkCarId(f, r); } }, `Usar ${c.suggestion}`)) : null));
}
async function checkCarId(f, r) {
  try { showCarId(f, r, await api(`/api/car_id?car_id=${encodeURIComponent(f.car_id.value.trim())}`)); } catch (e) { /* el bloqueo real está en /api/convert */ }
}
async function startConvert(r, f) {
  const box = $("convbox");
  const body = { path: r.path, pc: f.pc.value || null, name: null, car_id: f.car_id.value.trim(),
    driver_mass: f.driver_mass.value, analysis_driver_mass: r.driver_mass, mass_choice: massChoiceFor(r.path, (f.pc && f.pc.value) || r.config || null), dest: f.dest.value.trim() || null, ac_root: acRoot() };
  let job;
  try { job = await api("/api/convert", body); }
  catch (e) { box.replaceChildren(el("div", { class: "notice err" }, e.message)); return; }
  for (const b of f.querySelectorAll("button[type=submit]")) b.disabled = true;  // una conversión cada vez
  const release = externalBusy("Conversión iniciada");
  const head = el("div", { class: "notice" }, "Conversión en curso… (el modelo 3D con Blender tarda minutos)");
  const log = el("pre", { class: "log" });
  const res = el("div", {});
  // G2.29: cancelar se atiende entre etapas (Blender no se interrumpe)
  const stop = el("button", { class: "secondary", type: "button", onclick: async () => {
    try { await api("/api/cancel", { job: job.id }); stop.disabled = true; stop.textContent = "Cancelando al terminar la etapa en curso…"; } catch (e) { toast(e.message, "warn"); } } }, "Cancelar conversión");
  box.replaceChildren(head, stop, el("details", {}, el("summary", {}, "Registro"), log), res);
  const tick = async () => {
    let j;
    try { j = await api(`/api/job?id=${job.id}`); } catch (e) { head.textContent = e.message; release(); return; }
    log.textContent = j.log.join("\n"); log.scrollTop = log.scrollHeight;
    if (j.log.length) head.textContent = `Conversión en curso… ${j.log.filter((l) => /^\d\/4 /.test(l)).pop() || ""}`;
    if (j.status === "en_curso") { setTimeout(tick, 1000); return; }
    release();
    for (const b of f.querySelectorAll("button[type=submit]")) b.disabled = b.value === "convert" && (!r.exportable_today || f.dataset.idConflict === "1");
    loadWorkspace(); stop.remove();
    if (j.status === "cancelada") { head.className = "notice"; head.textContent = "Conversión cancelada."; return; }
    if (j.status !== "hecha") { head.className = "notice err"; head.textContent = `Conversión fallida: ${j.error}`; return; }
    const x = j.result, ok = x.verdict !== "bloqueado";
    head.className = "notice " + (ok ? "ok" : "err");
    if (ok) {
      head.textContent = `Coche convertido: ${x.car_id}. Ajusta ahora las cámaras y los ojos del piloto.`;
      toast(`Coche convertido en ${x.output}`, "ok");
      await loadWorkspace(); await openProject(x.project); selectTab("vista");
      return;
    }
    head.replaceChildren("No se pudo convertir este coche.",
      el("div", { class: "small" }, "El conversor encontró problemas que impiden generar un coche utilizable."));
    res.replaceChildren(
      x.errors.length ? table(["código", "atributo", "detalle"], x.errors.map((i) => [i.code, i.target, i.detail])) : null,
      el("div", { class: "actions" },
        el("button", { class: "secondary", type: "button", onclick: async () => { await openProject(x.project); selectTab("resultados"); showAttempt(x.attempt); } }, "Ver detalles")));
  };
  setTimeout(tick, 700);
}
// G2.46: bancos de BeamNG (neumáticos, aerodinámica). Misma lógica que `python -m src.bench`.
const BENCH_ES = { tyres: "neumáticos", aero: "aerodinámica" };
const REUSE_ES = {
  "exacta": "medida propia de este origen (reutilizable)",
  "compatible": "medida reutilizada de otro origen: la huella acotada de este tipo coincide (solo cambió algo ajeno a la medida)",
  "obsoleta": "OBSOLETA: hay una medida de una versión anterior de este vehículo y ya no corresponde; hay que medir de nuevo (se conserva)",
  "falta": "falta",
};
// Neumático ESTIMADO sin banco (src/ac/tyre_estimate.py): aviso visible; la medida es opcional.
function tyreEstimateNotice(r) {
  const t = r.tyres;
  if (!t || t.source !== "estimado") return null;
  return el("div", { class: "notice", id: "tyre-estimated" },
    el("b", {}, "Neumático estimado, no medido. "),
    "Los parámetros de agarre salen de la ley de rozamiento recuperada de BeamNG y de una regresión sobre medidas de otros coches " +
    "(error típico ≈ 0,05 en μ; mayor en la forma de la curva). Medirlo en BeamNG con python -m src.bench tyres es opcional y gana siempre.",
    ...(t.warnings || []).map((w) => el("div", { class: "small muted" }, w)));
}
function benchView(r, f) {
  const est = tyreEstimateNotice(r);
  const box0 = benchViewMeasured(r, f);
  if (!est) return box0;
  return box0 ? el("div", {}, est, box0) : est;
}
function benchViewMeasured(r, f) {
  const needs = (r.bench && r.bench.needs) || [];
  const measures = (r.bench && r.bench.measures) || [];
  if (!needs.length && r.bench && r.bench.note) return el("div", { class: "notice" }, r.bench.note);
  if (!needs.length && !measures.length && !(r.bench && r.bench.error)) return null;
  const box = el("div", { class: "card", id: "benchbox" }, el("h4", {}, needs.length ? "Medidas de BeamNG pendientes" : "Medidas de BeamNG"),
    r.bench.error ? el("div", { class: "notice err" }, r.bench.error) : null,
    measures.length ? el("ul", { class: "small", id: "bench-measures" }, ...measures.map((m) =>
      el("li", { "data-kind": m.kind, "data-reuse": m.reuse }, el("b", {}, `${BENCH_ES[m.kind] || m.kind}: `), REUSE_ES[m.reuse] || m.reuse,
        m.measurement ? el("span", { class: "muted" }, ` · ${m.measurement}`) : null))) : null,
    needs.length ? el("p", { class: "muted small" }, "Este origen no tiene medida propia (la huella del contenido es nueva). BeamNG se lanza con una carpeta de usuario aislada, " +
      "sin tocar tu instalación; cierra BeamNG antes. Una medida terminada se guarda en measurements/ y nunca se sobrescribe.") : null);
  const status = el("div", { class: "small" });
  const log = el("pre", { class: "log" }); log.hidden = true;
  const actions = el("div", { class: "actions" });
  for (const n of needs) {
    actions.append(el("button", { class: "secondary lockable", type: "button", onclick: () => benchStart(r, f, n.kind, box, status, log, actions) },
      `Medir ${BENCH_ES[n.kind] || n.kind} en BeamNG (≈ ${n.minutes} min)`),
      el("span", { class: "muted small" }, n.reason));
  }
  box.append(actions, status, log);
  // recuperación: ¿hay ya una tarea de banco en este servidor, o sesiones hechas de un intento anterior?
  api(`/api/bench?path=${encodeURIComponent(r.path)}${f.pc && f.pc.value ? "&pc=" + encodeURIComponent(f.pc.value) : ""}`).then((b) => {
    const done = b.benches.filter((x) => Object.values(x.sessions).some((v) => v === "hecha" || v === "preparada") && !x.measurement);
    const states = b.benches.filter((x) => x.state !== "pendiente" || x.measurement || done.includes(x));
    if (states.length) status.append(el("div", { id: "bench-states", class: "small" }, ...states.map((x) =>
      el("div", { "data-kind": x.kind, "data-state": x.state }, el("b", {}, `${BENCH_ES[x.kind]}: ${x.state}`),
        ` · sesiones ${Object.entries(x.sessions).map(([k, v]) => `${k} ${v}`).join(", ")}`, x.error ? ` · ${x.error}` : ""))));
    if (done.length) status.append(el("div", {}, "Trabajo recuperable: al medir de nuevo se reanuda sin repetir las sesiones hechas; una sesión a medias nunca se registra como válida."));
    if (b.beamng_running) status.append(el("div", { class: "notice err" }, "BeamNG está abierto: ciérrelo antes de medir."));
    if (b.job) followBench(b.job, r, f, box, status, log, actions);
  }).catch((e) => status.append(el("div", { class: "notice err" }, e.message)));
  return box;
}
async function benchStart(r, f, kind, box, status, log, actions) {
  let job;
  const btns = actions.querySelectorAll("button");
  if ([...btns].some((b) => b.disabled)) return;  // doble pulsación: ya hay un banco en marcha
  for (const b of btns) b.disabled = true;
  try { job = await api("/api/bench_start", { kind, path: r.path, pc: (f.pc && f.pc.value) || r.config || null }); }
  catch (e) { for (const b of btns) b.disabled = false; status.replaceChildren(el("div", { class: "notice err" }, e.message)); return; }
  followBench(job, r, f, box, status, log, actions);
}
function followBench(job, r, f, box, status, log, actions) {
  for (const b of actions.querySelectorAll("button")) b.disabled = true;  // un banco cada vez
  const release = externalBusy("Medida de BeamNG iniciada");
  log.hidden = false;
  const head = el("div", { class: "notice" }, `${job.kind} en curso…`);
  const stop = el("button", { class: "secondary", type: "button", onclick: async () => {
    stop.disabled = true;  // un solo clic: la cancelación es idempotente en el servidor, pero el botón no se repite
    try { await api("/api/cancel", { job: job.id }); stop.textContent = "Cerrando BeamNG…"; } catch (e) { toast(e.message, "warn"); } } }, "Cancelar (cierra BeamNG)");
  status.replaceChildren(head, stop);
  const tick = async () => {
    let j;
    try { j = await api(`/api/job?id=${job.id}`); } catch (e) { head.textContent = e.message; release(); return; }
    log.textContent = j.log.join("\n"); log.scrollTop = log.scrollHeight;
    const p = j.progress || {};
    if (j.status === "en_curso") {
      head.textContent = `${j.kind}: ejecutándose… ` + (p.session ? `sesión ${p.session} · ${p.seconds || 0} s de ≈ ${p.expected || "?"} s` + (p.segment ? ` · último registro del juego: ${p.segment}` : "") : "preparando");
      setTimeout(tick, 2000); return;
    }
    stop.remove(); release();
    if (j.status === "cancelada") { head.className = "notice"; head.textContent = "Banco cancelado (estado: incompleto, nada se registró como medida). Las sesiones terminadas se conservan: al medir de nuevo se reanuda."; for (const b of actions.querySelectorAll("button")) b.disabled = false; return; }
    if (j.status !== "hecha") { head.className = "notice err"; head.textContent = `Banco fallido (estado: fallido): ${j.error}`; for (const b of actions.querySelectorAll("button")) b.disabled = false; return; }
    head.className = "notice ok"; head.textContent = `Medida escrita (${j.result.measurement}). Reanalizando el vehículo…`;
    await analyze(r.path, (f.pc && f.pc.value) || r.config, f.driver_mass ? f.driver_mass.value : null, f);
  };
  setTimeout(tick, 700);
}
// Importar (carpeta o zip): crea el proyecto directamente con valores por defecto y lo abre.
// Si el coche está fuera de alcance, hay un conflicto de masa por decidir o falla la creación,
// se queda el análisis en pantalla (con su formulario) como antes.
async function importSource(path) {
  await analyze(path, null, null, null, true);
}
async function autoCreate(r) {
  const base = (r.vehicle.toLowerCase().replace(/[^a-z0-9_.-]/g, "_").slice(0, 56) || "coche");
  for (let n = 1; n <= 20; n++) {
    const name = n === 1 ? base : `${base}-${n}`;
    try {
      const p = await api("/api/projects", { name, kind: r.kind, path: r.path, pc: r.config || null,
        driver_mass: r.driver_mass, mass_choice: massChoiceFor(r.path, r.config || null), notes: "importado" });
      toast(`Proyecto ${p.id} creado`, "ok");
      await loadWorkspace(); await openProject(p.id);
      return true;
    } catch (e) { if (!/ya existe/.test(e.message)) { toast(e.message, "warn"); return false; } }
  }
  return false;
}
async function analyze(path, pc, mass, keep, auto) {
  const box = $("analysis");
  const kept = keep ? ["name", "car_id", "dest"].map((k) => [k, keep[k].value]) : [];  // lo ya escrito sobrevive al reanálisis
  box.replaceChildren(skeleton(4, "Analizando…"));
  try {
    const mc = massChoiceFor(path, pc || null);
    const r = await api(`/api/inspect?path=${encodeURIComponent(path)}${pc ? "&pc=" + encodeURIComponent(pc) : ""}${mass != null ? "&driver_mass=" + encodeURIComponent(mass) : ""}${mc ? "&mass_choice=" + encodeURIComponent(mc) : ""}`);
    if (auto && r.ok && r.in_scope && !(r.mass_conflict && !r.mass_conflict.choice) && await autoCreate(r)) { box.replaceChildren(); return; }
    box.replaceChildren(analysisView(r, r.ok));
    const f = box.querySelector("form");
    if (f) { for (const [k, v] of kept) f[k].value = v; if (keep) checkCarId(f, r); }
    if (r.ok && r.mass_conflict && !r.mass_conflict.choice) massDialog(r);  // aviso flotante: hay que decidir
  } catch (e) { box.replaceChildren(el("div", { class: "notice err" }, e.message)); }
}
async function createFromAnalysis(r, f) {
  const body = { name: f.name.value.trim(), kind: r.kind, path: r.path, pc: (f.pc && f.pc.value) || r.config || null, driver_mass: f.driver_mass.value, mass_choice: massChoiceFor(r.path, (f.pc && f.pc.value) || r.config || null), notes: "creado desde el análisis previo" };
  try {
    const p = await api("/api/projects", body);
    toast(`Proyecto ${p.id} creado`, "ok");
    await loadWorkspace(); openProject(p.id);
  } catch (e) { $("an-msg").textContent = e.message; $("an-msg").className = "small warn"; }
}
// Blender es obligatorio: sin el modelo 3D el coche no se puede jugar en Assetto Corsa
const BLENDER_MSG = "Falta Blender 3.6: sin el modelo 3D el coche no se puede jugar. Indica tu ubicación en Ajustes.";
const noBlender = () => !(SETTINGS.effective || {}).blender_path;
const acRoot = () => (SETTINGS.effective || {}).ac_root || null;  // la ruta de AC se guarda solo en Ajustes
const AC_MSG = "Falta la ruta de Assetto Corsa (de ella se toma el piloto): indícala en Ajustes (rueda de la barra superior).";
// Ajustes del usuario (rutas de AC, Steam, Blender, FMOD, carpeta de entrega, piloto por defecto)
let SETTINGS = { saved: {}, effective: {} };
const DOWNLOADS = { blender_path: ["Descargar Blender 3.6", "https://www.blender.org/download/lts/3-6/"],
  fmod_path: ["Descargar FMOD Studio 1.08.12", "https://www.fmod.com/download"] };
const SET_FIELDS = [
  ["ac_root", "Assetto Corsa (carpeta de instalación)", "p. ej. D:/SteamLibrary/steamapps/common/assettocorsa"],
  ["steam_root", "Steam (carpeta; sirve para encontrar AC y BeamNG si no se indican arriba)", "p. ej. D:/SteamLibrary"],
  ["beamng_root", "BeamNG.drive (carpeta de instalación; sale de Steam si se deja vacío)", "p. ej. C:/Program Files (x86)/Steam/steamapps/common/BeamNG.drive"],
  ["beamng_mods_dir", "Mods de BeamNG (carpeta con los .zip; por defecto la de usuario de BeamNG)", "p. ej. C:/Users/…/AppData/Local/BeamNG/BeamNG.drive/current/mods"],
  ["blender_path", "Blender (blender.exe o su carpeta)", "p. ej. C:/Program Files/Blender Foundation/Blender 3.6"],
  ["fmod_path", "FMOD Studio 1.08.12 (carpeta «FMOD Studio 1.08.12»)", "p. ej. C:/Program Files (x86)/FMOD SoundSystem/FMOD Studio 1.08.12"],
  ["projects_dir", "Carpeta de proyectos (se aplica al reiniciar la aplicación)", "p. ej. D:/beam2ac/proyectos"],
  ["export_dir", "Carpeta de entrega por defecto (cualquier carpeta que no sea de AC ni de BeamNG)", "p. ej. D:/beam2ac/entregas"],
];
// Propósito e importancia de cada ruta (tooltip del icono ⓘ en Ajustes)
const SET_HELP = {
  ac_root: "Para qué: encontrar tu Assetto Corsa. Se usa solo en LECTURA: comprobar que el id del coche no esté ya instalado, leer coches de referencia de Kunos y detectar Custom Shaders Patch. Importancia: media. Sin ella la conversión funciona, pero no se avisa de ids repetidos. Nunca se instala nada ahí sin tu permiso.",
  steam_root: "Para qué: autodetectar AC y BeamNG a partir de tu biblioteca de Steam. Importancia: baja; si ya indicas las rutas de AC y BeamNG, no hace falta.",
  beamng_root: "Para qué: solo para los bancos de medida (neumáticos y aerodinámica), que lanzan BeamNG con una carpeta de usuario aislada para medir el coche. Importancia: baja para convertir (la conversión NO lee la instalación de BeamNG: parte del zip/carpeta exportado desde Automation). Déjala vacía si no vas a usar esas medidas.",
  beamng_mods_dir: "Para qué: de aquí sale la lista de coches; ahí están los .zip que Automation exporta a BeamNG. Importancia: alta para elegir coches desde la lista. Si está vacía puedes usar «Convertir un coche» y elegir cualquier carpeta o zip.",
  blender_path: "Para qué: construir el modelo 3D (.kn5) con Blender 3.6 en segundo plano. Importancia: obligatoria. Sin Blender el coche se exporta sin modelo y no se puede jugar en Assetto Corsa. Necesita además el complemento «assetto_corsa_tools» (exportador KN5, de terceros, GPL-3.0): se instala desde aquí con el botón «Instalar complemento KN5». El importador de Collada (.dae) ya viene con Blender 3.6.",
  fmod_path: "Para qué: generar un banco de sonido propio del motor (FMOD Studio 1.08.12). Importancia: opcional. Sin él se reutiliza un sonido de coche de Kunos.",
  projects_dir: "Para qué: dónde se guardan tus proyectos (etapas, decisiones y resultados). Importancia: media; cámbiala para usar otro disco. Se aplica al reiniciar la aplicación.",
  export_dir: "Para qué: dónde se deja el paquete del coche exportado. Importancia: baja; vacía, el paquete queda en la carpeta exports/ del proyecto. No puede ser una carpeta de AC ni de BeamNG.",
};
// Complemento KN5 de Blender (assetto_corsa_tools, GPL-3): se descarga del origen solo al pulsar y confirmar
function addonControls() {
  const a = SETTINGS.blender_addon;
  if (!a) return null;  // los ajustes no cargaron: sin estado no hay botón
  const ok = a.installed && a.matches_pinned;
  const msg = el("span", { class: "small " + (ok ? "" : "warn") },
    ok ? "Complemento KN5 de Blender: instalado" : a.installed ? "Complemento KN5: instalado pero distinto de la versión fijada" : "Complemento KN5 de Blender: no instalado");
  if (a.installed) return msg;
  return [msg, el("button", { class: "secondary", type: "button", onclick: async (e) => {
    if (!confirm(`Se descargará assetto_corsa_tools (GPL-3.0, commit ${a.pinned_commit.slice(0, 7)}) de ${a.source} y se copiará a ${a.path}. ¿Continuar?`)) return;
    e.target.disabled = true;
    try { SETTINGS.blender_addon = await api("/api/blender_addon", { confirm: true }); toast("Complemento instalado", "ok"); renderSettings(); }
    catch (err) { toast(err.message, "warn"); e.target.disabled = false; }
  } }, "Instalar complemento KN5")];
}
async function loadSettings() {
  try { SETTINGS = await api("/api/settings"); } catch (e) { toast(e.message, "warn"); }
  renderSettings();
  if (CURRENT && DETAIL) renderAsistente();  // los botones dependen de la ruta de AC y de Blender
}
// Aviso naranja en Inicio: rutas de Ajustes sin guardar y qué dejaría de funcionar sin ellas
const SETUP_NEEDS = [
  ["ac_root", "Assetto Corsa", "Sin esta ruta no se puede tomar el piloto sentado ni sus manos, ni comprobar si el car_id ya está instalado: los botones «Comenzar la conversión» y «Exportar» quedan desactivados."],
  ["blender_path", "Blender 3.6", "Sin Blender no se construye el modelo 3D (.kn5), así que el coche no se podría conducir en Assetto Corsa: la conversión y la exportación quedan desactivadas. Además de Blender hace falta el complemento KN5 (assetto_corsa_tools), que se instala desde Ajustes."],
  ["fmod_path", "FMOD Studio 1.08.12", "Sin FMOD el coche sale sin el sonido propio de su motor."],
  ["beamng_root", "BeamNG.drive", "Solo se usa para medir neumáticos y aerodinámica con BeamNG; sin ella esas medidas no están disponibles. Convertir no la necesita."],
];
function renderSetupWarn() {
  const box = $("setup-warn"), eff = SETTINGS.effective || {};
  const missing = SETUP_NEEDS.filter(([k]) => !eff[k] || (k === "fmod_path" && eff.fmod_compatible === false));
  if (!box) return;
  box.hidden = !missing.length;
  box.replaceChildren(...(missing.length ? [
    el("strong", {}, "Faltan rutas por guardar en Ajustes"),
    el("ul", { class: "small" }, missing.map(([, name, why]) => el("li", {}, el("strong", {}, name + ": "), why))),
    el("button", { class: "secondary small", type: "button", onclick: () => $("dlg-settings").showModal() }, "Abrir Ajustes")] : []));
}
function renderSettings() {
  renderSetupWarn();
  const box = $("settings");
  const s = SETTINGS.saved || {}, eff = SETTINGS.effective || {};
  const inUse = { ac_root: eff.ac_root, beamng_root: eff.beamng_root, blender_path: eff.blender_path,
    fmod_path: eff.fmod_path ? `${eff.fmod_path} (${eff.fmod_compatible ? "FMOD 1.08.12 correcto" : "no válido o no encontrado"})` : null };
  const f = el("form", { onsubmit: async (e) => {
    e.preventDefault();
    const body = Object.fromEntries(new FormData(f).entries());
    try { SETTINGS = await api("/api/settings", body); toast("Ajustes guardados", "ok"); renderSettings(); loadEnvironment(); if (CURRENT && DETAIL) renderAsistente(); }
    catch (err) { $("set-msg").textContent = err.message; $("set-msg").className = "small warn"; }
  } },
    SET_FIELDS.map(([name, label, ph]) => el("div", {},
      el("div", { class: "set-head" }, el("label", {}, label, SET_HELP[name] ? el("span", { class: "info", tabindex: "0", title: SET_HELP[name], "aria-label": SET_HELP[name] }, "ⓘ") : null),
        inUse[name] !== undefined ? el("span", { class: "inuse" + (inUse[name] ? "" : " missing") }, "En uso: " + (inUse[name] || "no encontrado")) : null),
      el("div", { class: "actions" },
        el("input", { name, type: "text", value: s[name] || "", placeholder: ph, style: "flex:1;min-width:260px" }),
        name === "blender_path" ? addonControls() : null,
        DOWNLOADS[name] ? el("button", { class: "secondary", type: "button", onclick: () => (window.pywebview ? window.pywebview.api.open_url(DOWNLOADS[name][1]) : window.open(DOWNLOADS[name][1], "_blank", "noopener")) }, DOWNLOADS[name][0]) : null,
        window.pywebview ? [
          name === "blender_path" ? el("button", { class: "secondary", type: "button", onclick: async () => { const p = await window.pywebview.api.pick_exe(); if (p) f[name].value = p; } }, "Elegir blender.exe…") : null,
          el("button", { class: "secondary", type: "button", onclick: async () => { const p = await window.pywebview.api.pick_folder(); if (p) f[name].value = p; } }, "Elegir carpeta…")] : null))),
    el("div", { class: "actions" }, el("button", { class: "primary", type: "submit" }, "Guardar ajustes"), el("span", { id: "set-msg", class: "small" })));
  box.replaceChildren(f);
}
async function loadEnvironment() {
  const box = $("env");
  box.replaceChildren(skeleton(5, "Comprobando…"));
  try {
    const e = await api("/api/environment");
    document.title = `beam2ac v${e.version}`;  // la versión solo va en el título de la ventana
    if (window.pywebview && window.pywebview.api.set_title) window.pywebview.api.set_title(document.title);
    const ac = e.assetto_corsa;
    const yes = (b) => (b ? "sí" : "no");
    box.replaceChildren(table(["Elemento", "Estado"], [
      ["Assetto Corsa", ac.found ? ac.root : "no encontrado (la exportación funciona; no se podrá comprobar si un id ya está instalado)"],
      ["Custom Shaders Patch", ac.found ? yes(ac.csp) : "—"],
      ["Coches beam2ac instalados", ac.found ? (ac.beam2ac_cars.join(", ") || "ninguno") : "—"],
      ["Circuitos y apps beam2ac instalados", ac.found ? ([...ac.beam2ac_tracks, ...ac.beam2ac_apps].join(", ") || "ninguno") : "—"],
      ["Steam", e.steam.installed ? "instalado" : "no detectado"],
      ["Blender (modelo 3D)", e.blender.found ? e.blender.path : "no encontrado: la exportación saldría sin KN5"],
      ["Complemento KN5 de Blender", e.blender_addon ? (e.blender_addon.installed ? (e.blender_addon.matches_pinned ? "instalado" : "instalado, distinto de la versión fijada") : "no instalado: instálalo en Ajustes o el modelo 3D no se exporta") : "—"],
      ["Catálogo de reglas", e.catalog.ok ? `${e.catalog.rules} reglas, ${e.catalog.attributes} atributos` : `error: ${e.catalog.error}`],
    ]), el("p", { class: "muted small" }, "Esta aplicación no instala coches ni abre los juegos. La validación en el juego se hace aparte."));
  } catch (err) { box.textContent = err.message; }
}
async function renderDiagnostico() {
  const box = $("tab-diagnostico");
  const s = DETAIL && DETAIL.source;
  if (!s) return;
  const pid = CURRENT;
  box.replaceChildren(skeleton(5, "Analizando…"));
  try {
    const r = await api(`/api/inspect?project=${encodeURIComponent(pid)}&path=${encodeURIComponent(s.path)}${s.pc ? "&pc=" + encodeURIComponent(s.pc) : ""}`);
    if (pid !== CURRENT) return; // se cambió de proyecto mientras se analizaba
    box.replaceChildren(el("p", { class: "muted small" }, "Análisis con el catálogo de reglas (sin las decisiones del proyecto; estas se ven en «Atributos y decisiones»)."), analysisView(r, false));
  } catch (e) { if (pid === CURRENT) box.replaceChildren(el("div", { class: "notice err" }, e.message)); }
}

// ------------------------------------------------------------------ proyecto
// Vista simple (por defecto): solo el pipeline, etapas en fila, sin resumen/diagnóstico/resultados ni editor de atributos.
function setSimple(on) {
  document.body.classList.toggle("simple", on);
  $("view-toggle").textContent = on ? "Vista avanzada" : "Vista simple";
  $("view-toggle").title = on ? "Muestra también el resumen, el diagnóstico, los resultados y el editor de atributos" : "Vuelve a mostrar solo el pipeline, sin los detalles técnicos";
  if (on && !$("tab-atributos").hidden) selectTab("conversion");
}
$("view-toggle").addEventListener("click", () => setSimple(!document.body.classList.contains("simple")));
async function openProject(id) {
  CURRENT = id;
  setSimple(true);  // al abrir un proyecto siempre empieza la vista simple
  $("welcome").hidden = true; $("project").hidden = false;
  $("proj-title").textContent = id;
  $("proj-meta").replaceChildren(skeleton(1, "Cargando…"));
  let d;
  try { d = await api(`/api/project?id=${encodeURIComponent(id)}`); }
  catch (e) { if (id === CURRENT) $("proj-meta").textContent = e.message; return; }
  if (id !== CURRENT) return; // otra selección más reciente
  DETAIL = d;
  GEO.clear();  // libera la memoria del proyecto anterior
  prefetchGeometry(id);
  $("proj-meta").textContent = `${sourceText(DETAIL.source)} · creado ${DETAIL.created || "—"} · ${DETAIL.decisions} decisiones del proyecto`;
  loadWorkspace();
  renderResumen(); renderAsistente(); renderResultados(); renderVista();
  $("tab-atributos").replaceChildren(el("p", { class: "muted" }, "Abre esta pestaña para cargar el editor (tarda unos segundos la primera vez)."));
  $("tab-atributos").dataset.loaded = "";
  // la pestaña Diagnóstico no debe conservar el análisis del proyecto anterior
  $("tab-diagnostico").dataset.loaded = "";
  $("tab-diagnostico").replaceChildren();
  if (!$("tab-conversion").hidden && (!SOLO || SOLO === "diagnostico")) { $("tab-diagnostico").dataset.loaded = id; renderDiagnostico(); }
  // si la pestaña Vista 3D ya estaba abierta, renderVista la vació y nadie la recarga
  if (!$("tab-vista").hidden) selectTab("vista");
}
const TAB_ALIAS = { resumen: "conversion", diagnostico: "conversion", asistente: "conversion", resultados: "conversion" };
// el modelo 3D de la etapa «model» basta para ajustar cámaras y piloto: no hace falta haber exportado
const hasModel = () => !!DETAIL && (DETAIL.has_model || (DETAIL.stages || []).some((s) => s.name === "model" && (s.status === "ok" || s.status === "cached")));
function selectTab(name) {
  name = TAB_ALIAS[name] || name;
  if (SOLO && name !== "conversion") { toast("Disponible en la ventana principal.", "warn"); return; }
  for (const b of document.querySelectorAll(".tabs button")) b.setAttribute("aria-selected", String(b.dataset.tab === name));
  for (const t of document.querySelectorAll(".tab")) t.hidden = t.id !== `tab-${name}`;
  if (name === "conversion" && DETAILS_BLOCK) DETAILS_BLOCK.load();  // precarga detalles y portada al abrir la pestaña
  if (name === "vista" && CURRENT && DETAIL && $("tab-vista").dataset.loaded !== CURRENT && hasModel()) {
    $("tab-vista").dataset.loaded = CURRENT; loadCockpit();
  }
  if (name === "conversion" && CURRENT && $("tab-diagnostico").dataset.loaded !== CURRENT && (!SOLO || SOLO === "diagnostico")) {
    $("tab-diagnostico").dataset.loaded = CURRENT; renderDiagnostico();
  }
  if (name === "atributos" && CURRENT && $("tab-atributos").dataset.loaded !== CURRENT) {
    $("tab-atributos").dataset.loaded = CURRENT;
    $("tab-atributos").replaceChildren(
      el("p", { class: "muted small" }, "Editor de atributos del proyecto: solo admite decisiones válidas para el modelo de datos (unidades compatibles, tablas LUT con x creciente, justificación obligatoria). Cada decisión se añade al historial; nada se sobrescribe."),
      el("iframe", { class: "embed", src: `/static/index.html?project=${encodeURIComponent(CURRENT)}`, title: "Editor de atributos" }));
  }
}

function renderResumen() {
  const d = DETAIL, last = d.last_export, pc = d.precheck;
  const pane = $("tab-resumen");
  pane.replaceChildren(
    el("div", { class: "kpis" },
      kpi(d.stages.filter((s) => s.status === "ok" || s.status === "cached").length + " / " + d.stages.length, "etapas con resultado"),
      kpi(d.decisions, "decisiones del proyecto"),
      kpi(pc.available ? pc.errors.length : "—", "bloqueos de exportación"),
      kpi(pc.available ? pc.warnings.length : "—", "advertencias"),
      kpi(d.exports, "intentos de exportación")),
    el("h3", {}, "Última exportación"),
    last ? el("div", { class: "notice " + (last.verdict === "bloqueado" ? "err" : "ok") },
      `Intento ${last.attempt} (${last.created || "—"}): `, verdict(last.verdict),
      ` · ${last.errors ?? "—"} errores, ${last.warnings ?? "—"} advertencias`,
      last.distributable === false ? " · NO distribuible (deriva de archivos locales de Kunos)" : "",
      el("div", { class: "small" }, "Validación automática: estructura, referencias y hashes. Probado en Assetto Corsa: ",
        el("strong", {}, last.tested_in_game ? "sí" : "todavía no"), "."))
      : el("p", { class: "muted" }, "Sin exportaciones todavía."),
    el("h3", {}, "Bloqueos antes de exportar"),
    precheckView(pc),
    d.notes ? el("p", { class: "muted" }, `Notas: ${d.notes}`) : null);
}
function kpi(n, l) { return el("div", { class: "kpi" }, el("div", { class: "n" }, fmt(n)), el("div", { class: "l" }, l)); }
function precheckView(pc) {
  if (!pc.available) return el("p", { class: "muted" }, pc.why);
  const rows = [...pc.errors, ...pc.warnings.filter((w) => w.kind !== "decisión_necesaria")];
  const pending = pc.warnings.filter((w) => w.kind === "decisión_necesaria").length;
  return el("div", {},
    rows.length ? table(["gravedad", "tipo", "atributo", "detalle"], rows.map((b) => [
      el("span", { class: `sev-${b.severity}` }, b.severity === "error" ? "error (bloquea)" : "advertencia"), b.kind, b.target.replace(/^ac:/, ""), b.detail]))
      : el("p", { class: "ok" }, "Sin errores en los INI resueltos."),
    el("p", { class: "muted small" }, `${pending} claves sin decisión que no bloquean (AC usaría su valor interno). La exportación vuelve a validar el paquete completo.`));
}

// ------------------------------------------------------------------ asistente
function renderAsistente() {
  const d = DETAIL;
  const pane = $("tab-asistente");
  const stages = el("ul", { class: "stages" }, d.stages.map((s) => el("li", {},
    el("span", { class: `dot ${s.status}`, title: STAGE_ES[s.status] || s.status }),
    el("div", {}, el("strong", {}, s.name), el("div", { class: "muted small" }, STAGE_ES[s.status] || s.status)),
    el("div", { class: "small" }, s.description, s.error ? el("div", { class: "warn" }, s.error) : null,
      s.summary ? el("details", { class: "adv-only" }, el("summary", {}, "resumen"), el("pre", { class: "log" }, JSON.stringify(s.summary, null, 1))) : null))));
  DETAILS_BLOCK = detailsBlock(CURRENT);
  if (!$("tab-conversion").hidden) DETAILS_BLOCK.load();  // la pestaña ya está abierta: no habrá selectTab que lo pida
  const exp = el("form", { class: "card", onsubmit: (e) => { e.preventDefault(); startExport(exp); } },
    el("h4", {}, "Exportar"),
    el("div", { class: "row" },
      el("div", {}, el("label", {}, "Masa del piloto (kg) *"),
        el("input", { id: "proj-mass", name: "driver_mass", type: "number", min: "0", max: "150", step: "any", required: true, value: String(d.driver_mass_kg) })),
      el("div", { class: "muted small" }, "Peso del conductor, que se suma al del coche. Cambiarlo modifica el reparto de pesos y, con ello, cómo acelera, frena y gira. Se guarda en el proyecto al exportar.")),
    el("div", { class: "row" },
      el("div", {}, el("label", {}, "car_id (opcional, [a-z0-9_])"), el("input", { name: "car_id", placeholder: "<vehículo> (se antepone beam2ac_ si falta)", autocomplete: "off" })),
      el("div", { class: "muted small" }, "Nombre interno del coche en Assetto Corsa: solo minúsculas, números y «_». Si lo dejas vacío se crea uno a partir del nombre del vehículo. Debe ser distinto del de cualquier coche ya instalado.")),
    el("p", { class: "muted small" }, "El coche siempre lleva volante y piloto (sentado, con manos). Como el piloto sale del juego original, el paquete es solo para tu equipo: no se puede compartir ni publicar."),
    DETAILS_BLOCK,
    extrasBlock(),
    el("p", { class: "muted small" }, (SETTINGS.effective || {}).fmod_compatible ? "Sonido: se genera solo con FMOD 1.08.12 a partir de los WAV del coche." : "Sonido: FMOD 1.08.12 no está instalado; el coche saldrá sin sonido propio."),
    noBlender() ? el("div", { class: "notice err" }, BLENDER_MSG) : null,
    acRoot() ? null : el("div", { class: "notice err" }, AC_MSG),
    el("label", { class: "opt" }, el("input", { type: "checkbox", name: "confirm", required: true }), " Confirmo crear un intento nuevo *",
      el("span", { class: "opt-help" }, "Obligatoria. Marcada: se crea una carpeta nueva con el coche dentro del proyecto. Nunca se borra ni se sobrescribe una exportación anterior. Sin marcar, el botón Exportar no funciona.")),
    el("label", { class: "opt" }, el("input", { type: "checkbox", name: "install_overwrite" }), " Instalar en Assetto Corsa y sobrescribir si ya existe un coche con el mismo car_id",
      el("span", { class: "opt-help" }, "Marcado: además de exportar, el coche se copia a la carpeta de coches de tu Assetto Corsa y ya podrás conducirlo. Si ya había uno con el mismo car_id, el anterior se guarda en la papelera de beam2ac (recuperable). Desmarcado (lo normal): no se toca el juego y copiar el coche a mano lo haces tú. Un coche «bloqueado» nunca se instala.")),
    el("div", { class: "actions" }, el("button", { class: "primary lockable", type: "submit", disabled: noBlender() || !acRoot() }, "Exportar"),
      el("span", { class: "muted small" }, "Crea una exportación nueva con todo lo elegido arriba. Hace falta marcar la confirmación.")));
  pane.replaceChildren(
    el("h3", {}, "1 · Etapas del pipeline"),
    el("p", { class: "muted small" }, "Una etapa es un paso del análisis del coche (leer los archivos, calcular la física, construir el modelo 3D…). Cada una reutiliza su resultado si nada ha cambiado; si una falla, se conserva el error y no se borra nada."),
    stages,
    el("div", { class: "actions" },
      el("button", { class: "secondary lockable", type: "button", disabled: noBlender(), onclick: () => startRun(true) }, "Ejecutar etapas (incluye el modelo 3D)"),
      el("span", { class: "muted small" }, "Analiza el coche y construye su modelo 3D con Blender. Puede tardar varios minutos; lo que ya está calculado se reutiliza.")),
    el("h3", {}, "2 · Decisiones pendientes"),
    el("p", { class: "muted small" }, "Son los datos que el conversor no puede deducir solo del coche (por ejemplo, cómo se reparte la tracción). Hasta que los decidas, la exportación queda «bloqueada»: nunca se rellenan con ceros."),
    precheckView(d.precheck),
    el("div", { class: "actions" }, el("button", { class: "secondary adv-only", type: "button", onclick: () => selectTab("atributos") }, "Abrir el editor de atributos"),
      el("span", { class: "muted small adv-only" }, "Muestra cada valor físico del coche y de dónde sale; aquí tomas las decisiones pendientes."),
      el("button", { class: "secondary", type: "button", onclick: () => selectTab("vista") }, "Abrir Vista 3D"),
      el("span", { class: "muted small" }, "Para colocar a mano las cámaras, los ojos del piloto y la animación del volante.")),
    el("p", { class: "muted small" }, "Antes de exportar: en la Vista 3D puedes configurar las cámaras, la posición del piloto y la animación del volante (manos). Solo hace falta haber ejecutado las etapas con el modelo 3D; no hace falta exportar."),
    el("h3", {}, "3 · Exportación"),
    el("p", { class: "muted small" }, "Exportar crea el coche listo para Assetto Corsa. «Exportado» no significa «probado»: hay que conducirlo en el juego para comprobarlo."), exp);
}
async function startRun(withModel) {
  try { trackJob(await api("/api/run", { project: CURRENT, with_model: withModel }), CURRENT); }
  catch (e) { toast(e.message, "warn"); }
}
// «Extras del coche»: todo opcional; lo marcado se incluye en el coche y en el setup de boxes de AC
function extrasBlock() {
  const box = el("div", { class: "compounds muted small" }, skeleton(2, "Buscando los neumáticos de Assetto Corsa…"));
  const load = async () => {
    try {
      const r = await api(`/api/tyre_compounds?ac_root=${encodeURIComponent(acRoot() || "")}`);
      const count = el("span", { class: "muted" });
      const upd = () => { const n = box.querySelectorAll("input[name=compound]:checked").length; count.textContent = n ? ` (${n} marcados)` : " (ninguno)"; };
      const dd = el("details", { class: "dropdown", onchange: upd }, el("summary", {}, "Tipos de compuesto", count),
        el("div", { class: "dropdown-list" }, r.compounds.map((c) => el("label", { class: "opt", title: c.ok ? `plantilla de ${c.car}` : c.reason },
          el("input", { type: "checkbox", name: "compound", value: c.id, disabled: !c.ok }), ` ${c.label}`,
          c.ok ? null : el("span", { class: "opt-help" }, c.reason)))));
      box.replaceChildren(dd); upd();
    } catch (e) { box.textContent = e.message; }
  };
  load();
  const lv = (name, v) => el("input", { name, type: "number", min: "2", max: "20", step: "1", value: String(v), style: "width:5em", "aria-label": "Niveles" });
  // los niveles solo se muestran con la casilla marcada
  const withLevels = (label, name, help, levelsName, levelsLabel) => {
    const lab = el("label", { class: "small", hidden: true }, levelsLabel, lv(levelsName, 8));
    const cb = el("input", { type: "checkbox", name, onchange: () => { lab.hidden = !cb.checked; } });
    return [el("label", { class: "opt" }, cb, ` ${label}`, el("span", { class: "opt-help" }, help)), lab];
  };
  const hint = (t) => el("p", { class: "muted small hint" }, t);
  return el("fieldset", { class: "extras" }, el("legend", {}, "Extras del coche (opcionales)"),
    hint("Todo lo de este bloque es opcional. Lo que marques se añade al coche y a la pantalla de ajustes («setup») de boxes de Assetto Corsa; lo que no marques no se incluye."),
    el("section", { class: "xsec" }, el("h4", {}, "Volante"),
      el("div", { class: "actions" },
        el("label", { class: "opt", style: "margin:0" }, el("input", { type: "checkbox", id: "no-wheel", onchange: (e) => { $("steer-btn").disabled = e.target.checked; } }), " El coche no tiene volante"),
        el("button", { id: "steer-btn", class: "secondary", type: "button", onclick: () => steeringDialog() }, "Seleccionar volante"),
        el("span", { class: "muted small" }, "El conversor busca solo el volante en el modelo 3D. Si se equivoca (por ejemplo, coge otra pieza redonda), pulsa aquí y elige tú cuál es el aro. El coche siempre lleva volante y piloto."))),
    el("section", { class: "xsec" }, el("h4", {}, "Neumáticos de Assetto Corsa"),
      hint("Marca los tipos de neumático del juego original que quieres tener además del convertido (que se llamará «Exported»). Se leen de tu instalación de AC y hacen que el coche solo sirva para tu equipo."),
      box),
    el("section", { class: "xsec" }, el("h4", {}, "ABS, Control de tracción, Frenada y Tipo de cambio"),
      ...withLevels("ABS en el setup", "x_abs", "Marcado: podrás ajustar el ABS (el sistema que evita que las ruedas se bloqueen al frenar) en boxes, con tantos niveles como indiques. Desmarcado: el coche no lleva ABS regulable. Es una plantilla de diseño, no una copia medida del coche original.", "x_abs_levels", "Niveles de ABS "),
      ...withLevels("Control de tracción en el setup", "x_tc", "Marcado: podrás ajustar el control de tracción (evita que las ruedas motrices patinen al acelerar) en boxes. Desmarcado: sin control de tracción regulable. También es una plantilla de diseño.", "x_tc_levels", "Niveles de control de tracción "),
      el("label", { class: "opt" }, el("input", { type: "checkbox", name: "x_bias" }), " Reparto de frenada en el setup",
        el("span", { class: "opt-help" }, "Marcado: en boxes podrás repartir la fuerza de frenado entre eje delantero y trasero (de −15 a +10 puntos sobre el valor del coche). Desmarcado: el reparto queda fijo.")),
      DETAIL && DETAIL.has_manual_gearbox
        ? el("label", { class: "opt" }, el("input", { type: "checkbox", name: "x_shifter" }), " Palanca de cambios",
          el("span", { class: "opt-help" }, "Marcado: el juego permite usar una palanca de cambios física (volante con palanca). Desmarcado: solo se puede cambiar con las levas o los botones. Solo para cajas manuales o secuenciales."))
        : null,
      DETAIL && DETAIL.has_boost
        ? el("label", { class: "opt" }, el("input", { type: "checkbox", name: "x_turbo" }), " Turbo ajustable del 0 al 100 %",
          el("span", { class: "opt-help" }, "Marcado: el turbo se puede regular desde la cabina y arranca al 100 %. Desmarcado: el turbo funciona siempre igual que en el coche original. Es una aproximación sin medir en el juego."))
        : null));
}
async function startExport(f) {
  try {  // la masa del piloto va con la exportación: se guarda antes si cambió
    if (Number(f.driver_mass.value) !== Number(DETAIL.driver_mass_kg)) await api("/api/driver_mass", { project: CURRENT, driver_mass: f.driver_mass.value });
  } catch (e) { toast(e.message, "warn"); return; }
  const extras = {
    abs: f.x_abs.checked, abs_levels: Number(f.x_abs_levels.value) || 0,
    tc: f.x_tc.checked, tc_levels: Number(f.x_tc_levels.value) || 0,
    tyre_compounds: [...f.querySelectorAll("input[name=compound]:checked")].map((c) => c.value),
    ...(f.x_shifter ? { shifter: f.x_shifter.checked } : {}),
    turbo: !!(f.x_turbo && f.x_turbo.checked), setup_brake_bias: f.x_bias.checked,
  };
  const body = { project: CURRENT, confirm: f.confirm.checked, extras, car_id: f.car_id.value.trim() || null,
    ac_root: acRoot(),
    install_overwrite: f.install_overwrite.checked };
  try { trackJob(await api("/api/export", body), CURRENT); } catch (e) { toast(e.message, "warn"); }
}
// ------------------------------------------------------------------ tareas en ejecución
// Ventana flotante con las tareas (en vez del registro al pie de la página) y bloqueo global de los botones
// de conversión (clase «lockable») mientras haya una tarea en marcha, sea cual sea su origen.
const JOBS = new Map();
let EXT_BUSY = 0;
const isBusy = () => EXT_BUSY > 0 || [...JOBS.values()].some((j) => j.status === "en_curso");
const syncBusy = () => document.body.classList.toggle("busy", isBusy());
// tareas que siguen su propio panel (conversión, bancos): solo participan del bloqueo; devuelve la función que libera
function externalBusy(label) {
  EXT_BUSY++; syncBusy(); if (label) startPopup(label);
  let done = false;
  return () => { if (!done) { done = true; EXT_BUSY--; syncBusy(); } };
}
function guardBusy(e) {
  const t = e.target instanceof Element ? e.target.closest(".lockable") : null;
  if (!t || !isBusy()) return;
  e.preventDefault(); e.stopImmediatePropagation();
  toast("Hay una tarea en curso: espera a que termine.", "warn");
}
document.addEventListener("click", guardBusy, true);
document.addEventListener("submit", guardBusy, true);

// aviso de inicio: 3 s con entrada y salida suaves (ease-in-out, ver desktop.css)
function startPopup(text) {
  const p = $("start-popup");
  p.hidden = true; p.textContent = text; void p.offsetWidth; p.hidden = false;  // reinicia la animación
  clearTimeout(startPopup._t); startPopup._t = setTimeout(() => { p.hidden = true; }, 3000);
}
const JOB_STATE = { "en curso": ["run", "ejecutándose…"], "caché": ["cache", "en caché"], "hecha": ["hecha", "hecha"], "FALLO": ["fallo", "fallo"], "omitida": ["omitida", "omitida"] };
function jobRows(log) {
  // «[estado] nombre (hash)» → una fila por etapa; gana la última línea de cada una
  const rows = new Map();
  for (const line of log) {
    const m = line.match(/^\[(en curso|caché|hecha|FALLO|omitida)\]\s+([^\s:(]+)(?:\s+\(([0-9a-f]+)\))?/);
    if (m) rows.set(m[2], { state: m[1], hash: m[3] });
  }
  return rows;
}
function trackJob(job, pid, opts = {}) {
  const label = job.kind.charAt(0).toUpperCase() + job.kind.slice(1);
  const j = { id: job.id, status: "en_curso", t0: Date.now() };
  const spin = el("span", { class: "spinner", "aria-hidden": "true" });
  const state = el("span", { class: "jstate" }, "en curso");
  const rows = el("ul", { class: "jrows" });
  const last = el("div", { class: "muted small" });
  const log = el("pre", { class: "log" });
  const stop = el("button", { class: "secondary small", type: "button", onclick: async () => {
    stop.disabled = true;
    try { await api("/api/cancel", { job: job.id }); stop.textContent = "Cancelando al terminar la etapa en curso…"; }
    catch (e) { toast(e.message, "warn"); stop.disabled = false; } } }, "Cancelar");
  const close = el("button", { class: "secondary small", type: "button", hidden: true, onclick: () => {
    box.remove(); JOBS.delete(job.id); if (!JOBS.size) $("jobs-modal").hidden = true; } }, "Cerrar");
  const box = el("div", { class: "job running" }, el("div", { class: "jhead" }, spin, label, state), rows, last,
    el("div", { class: "actions" }, stop, close), el("details", {}, el("summary", {}, "Registro"), log));
  JOBS.set(job.id, j); syncBusy();
  $("jobs-list").prepend(box); $("jobs-modal").hidden = false; $("jobs-modal").classList.remove("min");
  startPopup(`${label}: iniciada`);
  const finish = async (status, error, r) => {
    j.status = status; syncBusy();
    box.className = "job " + (status === "hecha" ? "done" : "fail");
    const res = r && r.result && r.result.verdict ? ` · intento ${r.result.attempt}: ${VERDICT_ES[r.result.verdict]} (${r.result.errors} errores, ${r.result.warnings} advertencias)` : "";
    state.textContent = `${status}${error ? " · " + error : ""}${res}`;
    stop.hidden = true; close.hidden = false;
    toast(`${label}: ${state.textContent}`, status === "hecha" ? "ok" : "warn");
    if (CURRENT !== pid) return;
    if (opts.light) {  // sin re-renderizar: el editor 3D puede tener cambios sin guardar
      try { DETAIL = await api(`/api/project?id=${encodeURIComponent(pid)}`); loadWorkspace(); } catch (e) { toast(e.message, "warn"); }
    } else await openProject(pid);
    if (opts.after) opts.after(status, r);
  };
  let fails = 0;
  const tick = async () => {
    let r;
    try { r = await api(`/api/job?id=${job.id}`); fails = 0; }
    catch (e) { if (++fails < 4) { setTimeout(tick, 1500); return; } finish("fallida", e.message); return; }
    log.textContent = r.log.join("\n"); log.scrollTop = log.scrollHeight;
    rows.replaceChildren(...[...jobRows(r.log)].map(([name, x]) => {
      const [cls, txt] = JOB_STATE[x.state];
      return el("li", { class: cls }, el("span", { class: `dot ${cls}` }), el("strong", {}, name), el("span", { class: "st" }, txt + (x.hash ? ` (${x.hash.slice(0, 8)})` : "")));
    }));
    last.textContent = r.log.length ? r.log[r.log.length - 1] : "iniciando…";
    if (r.status === "en_curso") { state.textContent = `en curso · ${Math.round((Date.now() - j.t0) / 1000)} s`; setTimeout(tick, 1000); return; }
    finish(r.status, r.error, r);
  };
  setTimeout(tick, 400);
}
$("jobs-min").addEventListener("click", () => $("jobs-modal").classList.toggle("min"));

// ------------------------------------------------------------------ resultados
function renderResultados() {
  const list = DETAIL.exports_list || [];
  $("tab-resultados").replaceChildren(
    el("h3", {}, "Intentos de exportación"),
    list.length ? table(["intento", "car_id", "veredicto", "errores", "advertencias", "distribuible", "fecha", ""],
      list.map((a) => [a.attempt, a.car_id || "—", verdict(a.verdict), a.errors ?? "—", a.warnings ?? "—",
        a.distributable === false ? "no" : (a.complete ? "sí" : "—"), a.created || "—",
        a.complete ? el("button", { class: "small", type: "button", onclick: () => showAttempt(a.attempt) }, "ver") : "—"]))
      : el("p", { class: "muted" }, "Sin exportaciones."),
    el("div", { id: "attempt-detail" }));
}
async function showAttempt(id) {
  const box = $("attempt-detail");
  box.replaceChildren(el("p", { class: "muted" }, "cargando…"));
  const d = await api(`/api/export?project=${encodeURIComponent(CURRENT)}&attempt=${id}`);
  const m = d.manifest, r = d.report, ins = d.inspect;
  const files = Object.entries(m.files).map(([path, f]) => [path, f.size, f.sha256.slice(0, 16) + "…",
    f.provenance.stage || f.provenance.generator || f.provenance.source || "—", (f.provenance.decisions || []).length || "—",
    f.provenance.distributable === false ? "no distribuible" : ""]);
  box.replaceChildren(
    el("h3", {}, `Intento ${id}: `, verdict(m.verdict)),
    el("div", { class: "notice " + (ins.intact ? "ok" : "err") },
      ins.intact ? "Hashes verificados: el paquete coincide con su manifiesto." : `Cambios respecto al manifiesto: ${ins.changed.join(", ")} ${ins.unlisted.join(", ")}`),
    el("div", { class: "notice" }, d.validation, ". Probado en Assetto Corsa: ", el("strong", {}, m.tested_in_game ? "sí" : "todavía no"),
      m.distributable === false ? ` · NO distribuible: ${(m.not_distributable_because || []).join("; ")}` : ""),
    el("div", { class: "actions" },
      el("button", { class: "secondary", type: "button", onclick: () => showDoc(`exports/${id}/export_report.md`) }, "Ver informe"),
      el("button", { class: "secondary", type: "button", onclick: () => showDoc(`exports/${id}/manifest.json`) }, "Ver manifiesto"),
      el("button", { class: "secondary", type: "button", onclick: () => openFolder(`exports/${id}`) }, "Abrir carpeta en el explorador"),
      el("button", { class: "secondary", type: "button", onclick: () => { selectTab("vista"); loadScene(id); } }, "Vista 3D")),
    el("div", { id: "doc" }),
    el("h4", {}, `Errores (${r.errors.length})`),
    r.errors.length ? table(["código", "atributo", "detalle"], r.errors.map((i) => [i.code, i.target, i.detail])) : el("p", { class: "ok" }, "ninguno"),
    el("h4", {}, `Advertencias (${r.warnings.length})`),
    r.warnings.length ? el("details", {}, el("summary", {}, "mostrar"), table(["código", "atributo", "detalle"], r.warnings.map((i) => [i.code, i.target, i.detail]))) : el("p", { class: "ok" }, "ninguna"),
    el("h4", {}, `Archivos (${files.length})`),
    table(["ruta", "bytes", "sha256", "origen", "decisiones", ""], files));
}
async function showDoc(rel) {
  try {
    const d = await api(`/api/file?project=${encodeURIComponent(CURRENT)}&path=${encodeURIComponent(rel)}`);
    $("doc").replaceChildren(el("p", { class: "muted small" }, d.path), el("pre", { class: "doc" }, d.text));
  } catch (e) { toast(e.message, "warn"); }
}
async function openFolder(rel) {
  if (!confirm(`¿Abrir ${rel} en el explorador de archivos?`)) return;
  try { const r = await api("/api/open", { project: CURRENT, path: rel }); toast(`Abierto: ${r.opened}`, "ok"); }
  catch (e) { toast(e.message, "warn"); }
}

// ------------------------------------------------------------------ vista 3D
function renderVista() {
  const last = DETAIL.last_export;
  const ready = hasModel();
  const exported = last && last.complete && last.verdict !== "bloqueado";
  $("tab-vista").dataset.loaded = "";
  $("tab-vista").replaceChildren(
    ready ? el("div", { class: "actions" },
      el("span", { class: "small" }, exported
        ? `${last.car_id}: mueve las cámaras y los ojos del piloto; los cambios se guardan y se aplican al coche.`
        : "Mueve las cámaras y los ojos del piloto; los cambios se guardan y se aplicarán en la próxima exportación."),
      exported ? el("button", { class: "secondary small", type: "button", onclick: () => openFolder(`exports/${last.attempt}`) }, "Abrir carpeta del coche") : null) : null,
    el("div", { id: "scene-box" }, ready ? null : el("p", { class: "muted" }, "Ejecuta antes las etapas con el modelo 3D («Ejecutar etapas (incluye el modelo 3D)» en Conversión) para ajustar las cámaras y los ojos; no hace falta exportar.")));
}
// guardar la cabina vuelve a generar el coche (el modelo 3D está en caché: tarda segundos)
async function applyCockpit(version) {
  const last = DETAIL && DETAIL.last_export;
  if (!last || !last.car_id) { toast(`Cabina guardada (v${version}): se aplicará en la próxima exportación.`, "ok"); return; }
  const pid = CURRENT;
  toast(`Cabina guardada (v${version}): aplicando al coche…`, "ok");
  try {
    const job = await api("/api/export", { project: pid, confirm: true, car_id: last.car_id, reexport: true });
    trackJob(job, pid, { light: true, after: (status, r) => toast(status === "hecha" && r.result.verdict !== "bloqueado" ? "Nuevo intento exportado con las posiciones. No se ha reinstalado en AC: usa «Instalar en Assetto Corsa» al exportar." : `No se pudo actualizar el coche: ${(r && r.error) || "errores de validación"}`,
      status === "hecha" && r.result.verdict !== "bloqueado" ? "ok" : "warn") });
  } catch (e) { toast(e.message, "warn"); }
}
window.addEventListener("message", (e) => {
  if (e.origin !== location.origin || !e.data) return;
  if (e.data.type === "cockpit-saved") applyCockpit(e.data.version);
  if (e.data.type === "cockpit-rebuild") { selectTab("conversion"); startRun(true); }  // modelo sin volante: regenerar
});
function loadCockpit() {
  $("scene-box").replaceChildren(el("iframe", { class: "embed", title: "Cámaras y piloto",
    src: `/static/cockpit.html?project=${encodeURIComponent(CURRENT)}` }));
}
function loadScene(attempt) {
  $("scene-box").replaceChildren(el("iframe", { class: "embed", title: "Vista 3D",
    src: `/static/scene.html?project=${encodeURIComponent(CURRENT)}&attempt=${encodeURIComponent(attempt)}` }));
}

// ------------------------------------------------------------------ arranque
// «Abrir en pestaña nueva»: la misma aplicación con ?project=…&panel=… muestra solo ese panel a pantalla completa
const PANELS = ["resumen", "diagnostico", "resultados", "asistente"];
const SOLO = (() => { const p = new URLSearchParams(location.search).get("panel"); return PANELS.includes(p) ? p : null; })();
function openPanel(panel) {
  if (!CURRENT) return;
  const q = `?project=${encodeURIComponent(CURRENT)}&panel=${panel}`;
  if (window.pywebview && window.pywebview.api && window.pywebview.api.open_window) window.pywebview.api.open_window(q);
  else window.open(`${location.origin}/${q}`, "_blank", "noopener");
}
for (const b of document.querySelectorAll("[data-open]")) b.addEventListener("click", () => openPanel(b.dataset.open));
for (const b of document.querySelectorAll(".tabs button")) b.addEventListener("click", () => selectTab(b.dataset.tab));
$("btn-home").addEventListener("click", showHome);
// barra lateral plegable (transición en CSS); el estado se recuerda si el navegador lo permite
function setSidebar(open) {
  document.body.classList.toggle("side-closed", !open);
  $("btn-toggle").setAttribute("aria-expanded", String(open));
  try { localStorage.setItem("beam2ac.side", open ? "1" : "0"); } catch (e) { /* sin almacenamiento */ }
}
$("btn-toggle").addEventListener("click", () => setSidebar(document.body.classList.contains("side-closed")));
try { if (localStorage.getItem("beam2ac.side") === "0") setSidebar(false); } catch (e) { /* por defecto: abierta */ }
$("btn-env").addEventListener("click", loadEnvironment);
$("btn-settings").addEventListener("click", () => $("dlg-settings").showModal());
$("btn-env-open").addEventListener("click", () => { $("dlg-env").showModal(); loadEnvironment(); });
for (const d of document.querySelectorAll("dialog.massdlg[id^=dlg-]")) {
  d.addEventListener("click", (e) => { if (e.target === d || e.target.closest("[data-close]")) d.close(); });  // fuera del cuadro o ✕
}
$("analyze").addEventListener("submit", (e) => { e.preventDefault(); importSource($("an-path").value); });
// ventana nativa (pywebview): selector de carpetas del sistema; en el navegador se escribe la ruta
window.addEventListener("pywebviewready", () => {
  const b = $("btn-pick"); b.hidden = false;
  b.addEventListener("click", async () => { const p = await window.pywebview.api.pick_folder(); if (p) { $("an-path").value = p; importSource(p); } });
  const z = $("btn-pick-zip"); z.hidden = false;
  z.addEventListener("click", async () => { const p = await window.pywebview.api.pick_zip(); if (p) { $("an-path").value = p; importSource(p); } });
});
if (!SOLO) loadEnvironment();  // en una ventana de panel el título y el entorno son los de la principal
loadSettings();
if (SOLO) {
  document.body.classList.add("solo");
  document.querySelector(`.pane[data-pane=${SOLO}]`).classList.add("solo-pane");
  const pid = new URLSearchParams(location.search).get("project") || "";
  document.title = `beam2ac · ${pid} · ${SOLO}`;
  loadWorkspace().then(() => openProject(pid)).catch((e) => { $("projects").textContent = "Error: " + e.message; });
} else {
  const pid = new URLSearchParams(location.search).get("project");  // ?project=ID abre ese proyecto directamente
  loadWorkspace().then(() => { if (pid) openProject(pid); }).catch((e) => { $("projects").textContent = "Error: " + e.message; });
}
