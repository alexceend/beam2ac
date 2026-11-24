"""G2.25: ``[FRONT|REAR] DAMP`` y ``RELAXATION_LENGTH`` de ``tyres.ini`` para el 200409.

Decisión del usuario (2026-11-09, ``output/g225_2026-11-09/preflight/autorizacion.md``):
medianas del corpus de AC, regla provisional.

* Hecho en AC (G2.25): sin estas claves AC lee 0 (clave ausente = 0). Parados con el freno a
  fondo, la fuerza longitudinal de cada neumático oscila ±8–9 kN y la carga ±400 N en los
  coches del 200409; en Legion las ruedas giran y el coche se desliza hasta 1,4 km/h.
* G2.20: no determinables desde BeamNG (``DAMP`` no es viscoso en el banco; la relajación no
  tiene equivalente medido). Estas reglas NO las miden: son valores de referencia.
* Corpus (``output/g225_2026-11-09/diagnostico_collider/encuesta_neumatico.json``): los 226
  ejes con ``tyres.ini`` legible tienen las dos claves, ninguna vale 0; con VERSION ≥ 10 (222):
  ``DAMP`` mediana 500 N·s/m (488–600), ``RELAXATION_LENGTH`` mediana 0,07253 m (0,066–0,167).

    python tools/catalog/add_tyre_damp_relax_200409_rules.py [--dry-run]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.attrs import schema as S  # noqa: E402
from src.attrs.store import REPO_CATALOG, Catalog, write_rule  # noqa: E402

DATE = "2026-11-09"
AUTHOR = "claude (G2.25: validación en AC, decisión del usuario «medianas del corpus»)"
SURVEY = "output/g225_2026-11-09/diagnostico_collider/encuesta_neumatico.json"
REPORT = "docs/research/g225_validacion_funcional_ac_2026-11-09.md"
VALUES = {"DAMP": (500.0, "N·s/m"), "RELAXATION_LENGTH": (0.0725, "m")}


def ev(type_: str, path: str, locator: str = "", note: str = "") -> S.EvidenceRef:
    return S.EvidenceRef(type=type_, path=path, locator=locator, sha256="", note=note)


def conditions(axle: str) -> list[S.Condition]:
    # mismo ámbito que el modelo de neumático de G2.20 (VERSION 10 con el banco medido)
    return [
        S.Condition(fact="ir.formats.exporter_version", op="in", value=["200409"]),
        S.Condition(fact=f"ir.tyres.axles.{axle}.force_slip_samples", op="exists", value=None),
        S.Condition(fact=f"ir.tyres.axles.{axle}.reference_load", op="exists", value=None),
    ]


def rule(axle: str, key: str) -> S.Rule:
    value, unit = VALUES[key]
    return S.Rule(
        id=f"tyre.{axle}.{key.lower()}_200409", version=1,
        target=f"ac:tyres.ini/{axle.upper()}/{key}", kind="reference",
        transform=S.Transform(id="constant", params={"value": value}, output_unit=unit,
                              formula=f"{key} = mediana del corpus de AC"),
        scope=S.Scope(level="family", family="automation_export"), status="provisional",
        conditions=conditions(axle),
        assumptions=[
            "valor de referencia (mediana de 222 ejes VERSION ≥ 10 de coches de AC), no medido en el origen",
            "ausente, AC lee 0: carga y fuerza del neumático oscilan parado (G2.25)",
        ],
        source_doc=[ev("report", "docs/research/g220_tyre_grip_model_2026-11-07.md", "§no determinables")],
        target_doc=[ev("file", SURVEY, "resumen.damp_v10 / relax_v10"),
                    ev("code", "output/g220_2026-11-07/acs_tyre/c", "neumático de AC (claves)")],
        evidence=[ev("report", REPORT, "§neumático"),
                  ev("decision", "output/g225_2026-11-09/preflight/autorizacion.md",
                     "Decisión del usuario sobre el neumático")],
        history=[S.HistoryEntry(date=DATE, author=AUTHOR, action="create",
                                reason="G2.25: sin la clave AC lee 0 (oscilación parado medida en AC)",
                                status_from="", status_to="provisional")],
        notes="G2.25",
    )


def rules() -> list[S.Rule]:
    return [rule(a, k) for a in ("front", "rear") for k in VALUES]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    rs = rules()
    have = {p.stem for p in (ROOT / "catalog/rules").glob("*.json")}
    for r in rs:
        S.from_dict(S.to_dict(r))
        if f"{r.id}@{r.version}" in have:
            raise SystemExit(f"ya existe: {r.id}@{r.version}")
    if a.dry_run:
        print(f"ok (sin escribir): {len(rs)} reglas")
        return 0
    for r in rs:
        print("escrita", write_rule(REPO_CATALOG, r))
    Catalog.load(REPO_CATALOG)
    return 0


if __name__ == "__main__":
    sys.exit(main())
