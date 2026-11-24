// beam2ac · idioma de la interfaz (es | en). El español es el idioma de origen: el código escribe
// español y este módulo lo traduce en el DOM (texto y atributos) con el diccionario de i18n_en.js.
// Orden: coincidencia exacta → plantilla con {} → sustitución de frases largas conocidas.
// Los mensajes técnicos que vienen del servidor no están en el diccionario y se muestran en español.
"use strict";
(function () {
  const KEY = "beam2ac.lang";
  const EN = window.B2A_EN || {};
  const norm = (s) => s.replace(/\s+/g, " ");
  const esc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const templates = [];   // [RegExp, plantilla inglesa]
  const phrases = [];     // [clave, inglés] (largas, para sustituir dentro de frases)
  for (const [k, v] of Object.entries(EN)) {
    if (k.includes("{}")) {
      templates.push([new RegExp("^" + k.split("{}").map(esc).join("([\\s\\S]*?)") + "$"), v, k.length]);
    } else if (k.length >= 8 || k.includes(" ")) phrases.push([k, v]);
  }
  templates.sort((a, b) => b[2] - a[2]);
  phrases.sort((a, b) => b[0].length - a[0].length);

  let lang = "es";
  try { lang = localStorage.getItem(KEY) || ""; } catch (e) { /* sin almacenamiento */ }
  if (lang !== "es" && lang !== "en") lang = (navigator.language || "es").toLowerCase().startsWith("es") ? "es" : "en";

  function tr(text) {
    if (lang === "es" || !text) return text;
    const m = text.match(/^(\s*)([\s\S]*?)(\s*)$/);
    const core = norm(m[2]);
    if (!core) return text;
    let out = EN[core];
    if (out === undefined) {
      for (const [re, tpl] of templates) {
        const x = core.match(re);
        if (x) { let i = 1; out = tpl.replace(/\{\}/g, () => tr(x[i++] ?? "")); break; }
      }
    }
    if (out === undefined) {
      out = core;
      const parts = core.split(/( · |: |, | — )/);  // frase compuesta: cada trozo por separado
      if (parts.length > 1) out = parts.map((x, i) => (i % 2 ? x : tr(x))).join("");
      if (out === core) {
        for (const [k, v] of phrases) if (out.includes(k)) out = out.split(k).join(v);
        if (out === core) return text;
      }
    }
    return m[1] + out + m[3];
  }

  const orig = new WeakMap();     // nodo de texto → español original
  const applied = new WeakMap();  // nodo de texto → último valor escrito por nosotros
  const ATTRS = ["placeholder", "title", "aria-label"];
  const origAttr = new WeakMap(); // elemento → {atributo: [original, aplicado]}

  function text(n) {
    const cur = n.nodeValue;
    if (applied.get(n) === cur) return;           // es nuestra propia escritura
    orig.set(n, cur);
    const out = tr(cur);
    applied.set(n, out);
    if (out !== cur) n.nodeValue = out;
  }
  function attrs(e) {
    let rec = origAttr.get(e);
    for (const a of ATTRS) {
      if (!e.hasAttribute(a)) continue;
      const cur = e.getAttribute(a);
      rec = rec || {}; origAttr.set(e, rec);
      if (rec[a] && rec[a][1] === cur) continue;
      const out = tr(cur);
      rec[a] = [cur, out];
      if (out !== cur) e.setAttribute(a, out);
    }
  }
  function walk(root) {
    if (root.nodeType === 3) { text(root); return; }
    if (root.nodeType !== 1) return;
    if (/^(SCRIPT|STYLE)$/.test(root.tagName)) return;
    attrs(root);
    for (const c of root.childNodes) walk(c);
  }
  function retranslate(root) {
    if (root.nodeType === 3) {
      if (orig.has(root)) { const o = tr(orig.get(root)); applied.set(root, o); root.nodeValue = o; }
      return;
    }
    if (root.nodeType !== 1 || /^(SCRIPT|STYLE)$/.test(root.tagName)) return;
    const rec = origAttr.get(root);
    if (rec) for (const a of Object.keys(rec)) {
      const o = tr(rec[a][0]); rec[a][1] = o; root.setAttribute(a, o);
    }
    for (const c of root.childNodes) retranslate(c);
  }

  const obs = new MutationObserver((muts) => {
    for (const m of muts) {
      if (m.type === "childList") for (const n of m.addedNodes) walk(n);
      else if (m.type === "characterData") text(m.target);
      else if (m.type === "attributes") attrs(m.target);
    }
  });
  function start() {
    walk(document.body);
    obs.observe(document.body, { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ATTRS });
    document.documentElement.lang = lang;
  }
  function setLang(l) {
    lang = l === "en" ? "en" : "es";
    try { localStorage.setItem(KEY, lang); } catch (e) { /* sin almacenamiento */ }
    document.documentElement.lang = lang;
    obs.disconnect();
    retranslate(document.body);
    obs.observe(document.body, { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ATTRS });
    const sel = document.getElementById("lang");
    if (sel) sel.value = lang;
  }
  // confirm()/alert() también hablan el idioma elegido
  const _confirm = window.confirm.bind(window), _alert = window.alert.bind(window);
  window.confirm = (m) => _confirm(tr(String(m)));
  window.alert = (m) => _alert(tr(String(m)));
  // otras ventanas (el editor 3D va en un iframe del mismo origen) siguen el cambio
  window.addEventListener("storage", (e) => { if (e.key === KEY && e.newValue && e.newValue !== lang) setLang(e.newValue); });

  window.B2A = { tr, setLang, get lang() { return lang; } };
  const init = () => {
    start();
    const sel = document.getElementById("lang");
    if (sel) { sel.value = lang; sel.addEventListener("change", () => setLang(sel.value)); }
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
})();
