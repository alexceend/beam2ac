// beam2ac · revisión de atributos. Sin bibliotecas externas; todo el texto con textContent.
"use strict";

const STATUS_ES = {
  known: "conocido", inferred: "inferido", ambiguous: "ambiguo", not_applicable: "no aplicable",
  format_requirement: "requisito de formato", manual_required: "intervención manual",
};
const ORDER = ["ambiguous", "manual_required", "inferred", "known", "format_requirement", "not_applicable"];
const ACTIONS = [
  ["accept", "Aceptar la propuesta o el valor mostrado"],
  ["manual_value", "Introducir un valor manual"],
  ["transform", "Definir una transformación"],
  ["no_correspondence", "Declarar sin correspondencia conocida"],
  ["defer", "Dejar pendiente"],
  ["reject", "Rechazar la propuesta"],
  ["revert", "Revertir la decisión vigente"],
];
const SCOPE_ES = { variant: "solo esta variante", vehicle: "este vehículo (todas sus variantes)",
  family: "familia (se guarda; no se aplica sin regla aprobada)",
  architecture: "arquitectura (se guarda; no se aplica sin regla aprobada)" };

let STATE = null;
let FILTER = "";
let CURRENT = null;

function el(tag, attrs = {}, ...children) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") n.className = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else if (v !== undefined && v !== null) n.setAttribute(k, v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined) continue;
    n.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return n;
}
const fmt = (v) => (v === null || v === undefined) ? "—" : (typeof v === "object" ? JSON.stringify(v) : String(v));
const badge = (status) => el("span", { class: `badge s-${status}` }, STATUS_ES[status] || status);

// En la aplicación de escritorio el editor se abre por proyecto: /static/index.html?project=<id>
const PROJECT = new URLSearchParams(location.search).get("project");

async function api(path, body) {
  if (PROJECT) {
    if (body) body = { ...body, project: PROJECT };
    else path += (path.includes("?") ? "&" : "?") + "project=" + encodeURIComponent(PROJECT);
  }
  const opts = body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {};
  const r = await fetch(path, opts);
  const data = await r.json();
  if (!r.ok) throw new Error(data.error || r.statusText);
  return data;
}

async function loadState() {
  STATE = await api("/api/state");
  document.getElementById("vehicle").textContent = ` · ${STATE.vehicle} / ${STATE.variant}`;
  const pv = document.getElementById("preview");
  pv.textContent = `${STATE.source} · vista previa ${STATE.preview_ok ? "válida" : "CON FALLOS: " + STATE.failed_stages.join(", ")}`
    + ` · familias: ${STATE.families.join(", ") || "—"} · decisiones del proyecto: ${STATE.project_dir}`;
  const bar = document.getElementById("statusbar");
  bar.replaceChildren();
  const counts = STATE.summary.by_status;
  bar.append(el("button", { "aria-pressed": String(FILTER === ""), onclick: () => setFilter("") }, `todos (${STATE.summary.targets})`));
  for (const s of ORDER) if (counts[s]) {
    bar.append(el("button", { class: `s-${s}`, "aria-pressed": String(FILTER === s), onclick: () => setFilter(s) },
      `${STATUS_ES[s]} (${counts[s]})`));
  }
}

function setFilter(s) { FILTER = s; loadState().then(loadList); }

async function loadList() {
  const q = document.getElementById("search").value.trim();
  const items = await api(`/api/attributes?status=${encodeURIComponent(FILTER)}&q=${encodeURIComponent(q)}`);
  const list = document.getElementById("list");
  list.replaceChildren();
  for (const status of ORDER) {
    const rows = items.filter((i) => i.status === status);
    if (!rows.length) continue;
    const g = el("div", { class: "group" }, el("h3", {}, `${STATUS_ES[status]} · ${rows.length}`));
    for (const i of rows) {
      g.append(el("button", { class: "item", "aria-current": String(i.target === CURRENT), onclick: () => select(i.target) },
        el("div", { class: "k" }, i.target.slice(3)),
        el("div", { class: "v" }, `${fmt(i.value)} ${i.value === null ? "" : i.unit} · ${i.confidence} · ${i.verifiable}`
          + (i.has_candidates ? " · candidatos" : ""))));
    }
    list.append(g);
  }
}

function table(headers, rows) {
  return el("table", {}, el("tr", {}, headers.map((h) => el("th", {}, h))),
    rows.map((r) => el("tr", {}, r.map((c) => el("td", {}, c instanceof Node ? c : fmt(c))))));
}
function listOf(items) { return items && items.length ? el("ul", {}, items.map((x) => el("li", {}, fmt(x)))) : el("p", { class: "muted" }, "—"); }
function refs(arr) { return (arr || []).map((r) => `${r.type}: ${r.path}${r.locator ? " · " + r.locator : ""}${r.note ? " · " + r.note : ""}`); }

async function select(target) {
  CURRENT = target;
  const d = await api(`/api/attribute?target=${encodeURIComponent(target)}`);
  const r = d.result;
  const pane = document.getElementById("detail");
  pane.replaceChildren(
    el("h2", {}, target.slice(3)),
    el("div", {}, badge(r.status), el("span", { class: "badge" }, r.confidence),
      el("span", { class: `badge ${d.verifiable === "verificado" ? "ok" : "warn"}` }, d.verifiable),
      r.requires_approval ? el("span", { class: "badge warn" }, "requiere aprobación") : null),
    el("div", { class: "card" },
      el("div", {}, "Valor: ", el("span", { class: "mono" }, `${fmt(r.value)} ${r.value === null ? "" : r.unit}`)),
      el("div", { class: "muted" }, `Origen: ${r.source || "—"} ${r.rule || r.decision || ""}`),
      d.attribute ? el("div", { class: "muted" }, `Destino: unidad ${d.attribute.unit} · ${d.attribute.dtype}`
        + (d.attribute.required_for_load ? " · necesario para cargar en AC" : "") + (d.attribute.description ? ` · ${d.attribute.description}` : "")) : null),
    el("h4", {}, "Motivo"), listOf(r.reasons),
    el("h4", {}, "Supuestos"), listOf(r.assumptions),
    el("h4", {}, "Problemas abiertos"), listOf(r.open_issues),
    el("h4", {}, "Confianza (criterios)"), listOf(r.confidence_criteria),
    el("h4", {}, "Entradas de origen (valor, unidad, evidencia)"),
    r.inputs && r.inputs.length ? table(["entrada", "hecho/atributo", "valor", "unidad", "tipo", "evidencia"],
      r.inputs.map((i) => [i.name, i.fact || i.attribute, i.value, i.unit, i.kind || i.status || "", refs(i.refs).join(" | ") + (i.note ? " · " + i.note : "")]))
      : el("p", { class: "muted" }, "—"),
    r.transform && r.transform.id ? el("p", { class: "mono" }, `Transformación: ${r.transform.id} ${JSON.stringify(r.transform.params)} → ${r.transform.output_unit} ${r.transform.formula || ""}`) : null,
    el("h4", {}, "Reglas descartadas"),
    r.rejected && r.rejected.length ? table(["regla", "motivo", "ámbito"], r.rejected.map((x) => [x.rule, x.reason, x.scope])) : el("p", { class: "muted" }, "—"),
    r.alternatives && r.alternatives.length ? el("div", {}, el("h4", {}, "Alternativas"),
      table(["regla", "valor", "nota"], r.alternatives.map((a) => [a.rule, a.value, a.note || a.scope || ""]))) : null,
    r.promotion && r.promotion.why ? el("p", { class: "muted" }, `Ampliación de ámbito: ${r.promotion.family_scope_eligible ? "cumpliría" : "no cumple"} el plan §6.1 — ${r.promotion.why} (no se aplica)`) : null,
    el("h4", {}, "Candidatos medidos (fase 5, no aplicados)"), candidates(d.candidates),
    el("h4", {}, `Referencias de Assetto Corsa (${d.ac_references_total} contenidos independientes)`),
    d.ac_references.length ? table(["valor", "coches", "origen"], d.ac_references.map((g) => [g.value, `${g.cars.length}: ${g.cars.slice(0, 4).join(", ")}${g.cars.length > 4 ? "…" : ""}`, g.origins.join(", ")]))
      : el("p", { class: "muted" }, STATE.reference_index_loaded ? "sin referencias para esta clave" : "índice de referencias no cargado (--refs)"),
    el("h4", {}, "Reglas del catálogo"),
    d.rules.length ? table(["regla", "estado", "ámbito", "evidencia", "historial"], d.rules.map((x) => [x.key, x.status,
      `${x.scope.level} ${x.scope.vehicle || x.scope.family || ""}`, refs(x.evidence).join(" | "), (x.history || []).map((h) => `${h.date} ${h.action}: ${h.reason}`).join(" | ")]))
      : el("p", { class: "muted" }, "—"),
    el("h4", {}, "Historial de decisiones de este vehículo"),
    d.decisions.length ? table(["id", "acción", "valor", "estado", "ámbito", "justificación", "sustituye a"], d.decisions.map((x) => [x.id, x.action, `${fmt(x.value)} ${x.unit}`, x.status, x.scope.level, x.justification, x.supersedes || "—"]))
      : el("p", { class: "muted" }, "—"),
    decisionForm(target, d),
  );
  loadList();
}

function candidates(list) {
  if (!list || !list.length) return el("p", { class: "muted" }, "—");
  return table(["#", "valor", "método", "supuestos", "falta", "aceptar"], list.map((c, i) => [i, `${fmt(c.value)} ${c.value === null ? "" : c.unit}`, c.method,
    (c.assumptions || []).join(" · "), (c.missing || []).join(" · "),
    c.value === null ? "—" : el("button", { class: "small", type: "button", onclick: () => prefillCandidate(i) }, "usar")]));
}

function prefillCandidate(i) {
  const f = document.getElementById("decision");
  f.action.value = "accept"; f.source.value = "candidate"; f.candidate_index.value = String(i);
  updateForm(f);
  f.justification.focus();
}

function decisionForm(target, d) {
  const unitOpts = d.compatible_units.map((u) => el("option", { value: u }, u));
  const factOpts = STATE.facts.map((f) => el("option", { value: f }, f));
  const f = el("form", { id: "decision", class: "card", onsubmit: (e) => { e.preventDefault(); submitDecision(target, f); } },
    el("h4", {}, "Decisión"),
    el("label", {}, "Acción"), el("select", { name: "action", onchange: () => updateForm(f) }, ACTIONS.map(([v, t]) => el("option", { value: v }, t))),
    el("div", { "data-for": "accept" },
      el("label", {}, "Qué se acepta"), el("select", { name: "source" }, el("option", { value: "result" }, "la propuesta o el valor resuelto"), el("option", { value: "candidate" }, "un candidato medido")),
      el("label", {}, "Índice del candidato"), el("input", { name: "candidate_index", type: "number", min: "0", value: "0" })),
    el("div", { "data-for": "manual_value transform" }, el("div", { class: "row" },
      el("div", {}, el("label", {}, "Valor (número, texto o lista JSON)"), el("input", { name: "value" })),
      el("div", {}, el("label", {}, "Unidad (solo compatibles)"), el("select", { name: "unit" }, unitOpts)))),
    el("div", { "data-for": "transform" },
      el("label", {}, "Transformación"), el("select", { name: "transform_id" }, STATE.transforms.map((t) => el("option", { value: t }, t))),
      el("label", {}, "Entrada x (hecho del vehículo)"), el("select", { name: "input_x" }, factOpts),
      el("label", {}, "Parámetros (JSON, p. ej. {\"factor\": 2})"), el("input", { name: "params", value: "{}" }),
      el("label", {}, "Fórmula (texto)"), el("input", { name: "formula" })),
    el("div", { class: "row" },
      el("div", {}, el("label", {}, "Ámbito"), el("select", { name: "scope" }, ["variant", "vehicle", "family", "architecture"].map((s) => el("option", { value: s }, SCOPE_ES[s])))),
      el("div", {}, el("label", {}, "Estado"), el("select", { name: "status" }, el("option", { value: "provisional" }, "provisional"), el("option", { value: "validated" }, "validado"), el("option", { value: "pending" }, "pendiente")))),
    el("label", {}, "Justificación (obligatoria)"), el("textarea", { name: "justification", required: "required" }),
    el("label", {}, "Autor"), el("input", { name: "author", value: "usuario" }),
    el("button", { class: "primary", type: "submit" }, "Guardar decisión"),
    el("p", { id: "formmsg", class: "muted" }, "Se añade al historial del proyecto; nunca se sobrescribe una decisión anterior."));
  f.scope.value = "vehicle";
  setTimeout(() => updateForm(f), 0);
  return f;
}

function updateForm(f) {
  const a = f.action.value;
  for (const div of f.querySelectorAll("[data-for]")) div.hidden = !div.dataset.for.split(" ").includes(a);
}

async function submitDecision(target, f) {
  const msg = document.getElementById("formmsg");
  const body = { target, action: f.action.value, scope: f.scope.value, status: f.status.value,
    justification: f.justification.value, author: f.author.value };
  if (body.action === "accept") { body.source = f.source.value; body.candidate_index = Number(f.candidate_index.value); }
  if (body.action === "manual_value" || body.action === "transform") { body.value = f.value.value; body.unit = f.unit.value; }
  if (body.action === "transform") {
    body.transform_id = f.transform_id.value; body.inputs = { x: f.input_x.value }; body.formula = f.formula.value;
    try { body.params = JSON.parse(f.params.value || "{}"); } catch (e) { msg.textContent = "Parámetros: JSON inválido"; msg.className = "warn"; return; }
  }
  try {
    const out = await api("/api/decision", body);
    await loadState();
    await select(target);
    const m = document.getElementById("formmsg");
    m.textContent = `Guardada ${out.decision.id}: ahora «${STATUS_ES[out.result.status]}». ${out.note || ""}`;
    m.className = "ok";
  } catch (e) { msg.textContent = e.message; msg.className = "warn"; }
}

document.getElementById("search").addEventListener("input", () => loadList());
loadState().then(loadList).catch((e) => { document.getElementById("detail").textContent = "Error: " + e.message; });
