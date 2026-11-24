"""Versiones documentales de ``eng.power_lut`` y ``dt.preload`` (2026-10-26).

    python tools/catalog/add_powerlut_preload_doc_versions.py

Crea, sin sobrescribir nada (las reglas son inmutables):

* ``eng.power_lut@2`` (sustituye a ``@1``): misma transformación, entradas, condiciones,
  ámbito, estado y validaciones. Solo cambia el texto del supuesto sobre la
  fricción, que el código del juego contradice, y añade evidencia.
* ``dt.preload@2`` (sustituye a ``@1``): la entrada pasa a estar en N·m (el hecho
  ``bng.diff.lsd_preload`` se corrigió a N·m) con transformación ``identity``. El valor
  (100 en el Ibiza) no cambia. El factor de conversión BeamNG → AC queda
  **sin resolver** y se declara como supuesto explícito (factor 1, el que ya se
  usaba, sin demostrar), de modo que el motor mantiene el estado ``inferred``/E2.
  Declararlo en ``open_issues`` haría el resultado ``ambiguous`` (engine.py, rama
  ``elif out.open_issues``): es una opción que decide el usuario.

Evidencia: ``docs/research/investigacion_powerlut_preload_2026-10-26.md`` y
``output/research_powerlut_preload_2026-10-26/``.
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.attrs import schema as S  # noqa: E402
from src.attrs.store import REPO_CATALOG, Catalog, write_rule  # noqa: E402

REPORT = "docs/research/investigacion_powerlut_preload_2026-10-26.md"
EVID = "output/research_powerlut_preload_2026-10-26"
DATE = "2026-10-26"
AUTHOR = "claude (correcciones documentales power.lut/PRELOAD, autorizadas)"


def power_lut_v2(old: S.Rule) -> S.Rule:
    new = copy.deepcopy(old)
    new.version, new.supersedes = 2, old.key
    wrong = "fricción interpolada linealmente (createCurve no está en la muestra)"
    if wrong not in old.assumptions:
        raise SystemExit(
            f"supuesto esperado no encontrado en {old.key}: {old.assumptions}"
        )
    new.assumptions = [
        (
            "fricción: el juego antepone (0, 0) y usa createCurve (Catmull-Rom, "
            "ve_utils.lua "
            "l. 234-287; camsoEngine.lua l. 1975-1981); la implementación interpola "
            "de forma "
            "lineal y toma power.lut en los puntos de la tabla de par. Coincide con "
            "el juego "
            "en todos los puntos desde 500 rpm (|Δ| ≤ 3e-14 N·m) en los 5 coches "
            "medidos con "
            "este camsoEngine.lua; de 100 a 400 rpm difiere 0,3–1,6 N·m (por debajo "
            "del "
            "ralentí). Entre puntos, AC interpola linealmente (curva de AC): "
            "error máx. "
            "1,45 N·m (0,79 %) en el Ibiza frente a la curva del juego"
            if a == wrong
            else a
        )
        for a in old.assumptions
    ]
    new.evidence = list(old.evidence) + [
        S.EvidenceRef(type="report", path=REPORT, locator="§A"),
        S.EvidenceRef(type="report", path=f"{EVID}/powerlut_study.json"),
        S.EvidenceRef(type="report", path=f"{EVID}/powerlut_between_points.json"),
        S.EvidenceRef(
            type="code",
            path=f"{EVID}/code_evidence.json",
            locator="curva de AC; motor de AC",
        ),
    ]
    new.source_doc = list(old.source_doc) + [
        S.EvidenceRef(
            type="code",
            path=""
            "G:/SteamLibrary/steamapps/common/BeamNG.drive/lua/vehicle/ve_utils.lua",
            locator="l. 234-287 CatMullRomSpline/createCurve",
            sha256="23f2026d27de0092ddb36a78eaa3018a164cb7493c32889e872807d889bf6ac1",
        )
    ]
    new.history = list(old.history) + [
        S.HistoryEntry(
            date=DATE,
            author=AUTHOR,
            action="new_version",
            status_from=old.status,
            status_to=new.status,
            reason="corrige el texto del supuesto de fricción (contradicho por el "
            "código "
            "del juego); transformación, entradas, condiciones y valores sin cambios",
        )
    ]
    return new


def preload_v2(old: S.Rule) -> S.Rule:
    new = copy.deepcopy(old)
    new.version, new.supersedes = 2, old.key
    new.transform = S.Transform(
        id="identity",
        params={},
        input_units={"x": "N·m"},
        output_unit="N·m",
        formula="y = x (factor 1 conservado; ver supuestos)",
    )
    new.assumptions = [
        "unidad del origen: N·m, confirmada en código (differential.lua:57 suma "
        "lsdPreload "
        "a un par); el rótulo «N/m» de BeamNG es un error de rótulo",
        "FACTOR DE CONVERSIÓN SIN RESOLVER: se conserva el factor 1 (PRELOAD = "
        "lsdPreload, "
        "valor ya usado) sin demostrar. AC limita la diferencia de par entre ruedas "
        "(transmisión 2WD de AC); BeamNG limita lockTorque por salida (diferencia = "
        "2·lsdTorque), lo que apunta a un factor 2 solo por inferencia estructural. "
        "Las "
        "dinámicas difieren (muelle frente a umbral). Requiere prueba en AC y BeamNG "
        "(investigacion_powerlut_preload_2026-10-26.md §B.6)",
    ]
    new.evidence = list(old.evidence) + [
        S.EvidenceRef(type="report", path=REPORT, locator="§B"),
        S.EvidenceRef(type="report", path=f"{EVID}/preload_run2/preload_survey.json"),
        S.EvidenceRef(
            type="code",
            path=f"{EVID}/code_evidence.json",
            locator="transmisión 2WD de AC",
        ),
    ]
    new.source_doc = list(old.source_doc) + [
        S.EvidenceRef(
            type="code",
            path="G:/SteamLibrary/steamapps/common/BeamNG.drive/lua/vehicle/powertrain/"
            "differential.lua",
            locator="l. 42-66 LSDUpdateTorque",
            sha256="1b53a79101bdd31cda16b7b00e86ee1494b909149aca6d42d236e964c6e0aa0a",
        )
    ]
    new.notes = (
        "unidad corregida a N·m; factor de conversión sin resolver (no validado)"
    )
    new.history = list(old.history) + [
        S.HistoryEntry(
            date=DATE,
            author=AUTHOR,
            action="new_version",
            status_from=old.status,
            status_to=new.status,
            reason="unidad de entrada N·m (hecho corregido); valor sin cambios; "
            "factor de "
            "conversión declarado sin resolver",
        )
    ]
    return new


def main() -> int:
    cat = Catalog.load(REPO_CATALOG)
    for key in ("eng.power_lut@2", "dt.preload@2"):
        if key in cat.rules:
            raise SystemExit(f"{key} ya existe: no se sobrescribe")
    for new in (
        power_lut_v2(cat.rules["eng.power_lut@1"]),
        preload_v2(cat.rules["dt.preload@1"]),
    ):
        print("escrita", write_rule(REPO_CATALOG, new))
    Catalog.load(REPO_CATALOG).check()
    print("catálogo válido")
    return 0


if __name__ == "__main__":
    sys.exit(main())
