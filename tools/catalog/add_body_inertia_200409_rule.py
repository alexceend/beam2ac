"""Regla ``[EXPLICIT_INERTIA] INERTIA`` del cuerpo de AC para el 200409 (G2.26,
2026-11-10).

    python tools/catalog/add_body_inertia_200409_rule.py [--dry-run] [--catalog DIR]

Encargo del usuario (G2.26: diagnóstico y corrección de Prothean y Omega, con
autonomía técnica). Solo añade; nunca sobrescribe. Exportadores 38022/40340: nada
(lista cerrada ``["200409"]``).

Defecto demostrado: los cuatro 200409 probados en G2.25 no escribían ninguna inercia;
coche de AC [C] cae entonces en el cálculo de inercia por caja de masa con ``[BASIC] INERTIA``
ausente = (0,0,0) y ``log.txt`` imprime ``LOCAL INERTIA: 0.000000 0.000000 0.000000``
(el Celica de Kunos: 2132,7 / 2184,9 / 367,7). Regla (``provisional``, transformación
``ac_body_inertia``):

* ``car.explicit_inertia_200409_nodriver@1`` → ``[EXPLICIT_INERTIA] INERTIA`` = tensor
  del vehículo entero medido en los nodos menos los ``HUB_MASS`` en los centros de
  rueda, trasladado al CdG del cuerpo (el mismo cuerpo que ``CG_LOCATION``/``BASEY``
  sin piloto).

Condiciones: 200409 **y** ``ir.mass.driver == 0`` (como las reglas del CdG: con piloto
no se sabe dónde está su masa)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.attrs import schema as S  # noqa: E402
from src.attrs.store import REPO_CATALOG, Catalog, write_rule  # noqa: E402

DATE = "2026-11-10"
AUTHOR = (
    "claude (G2.26: diagnóstico y corrección de Prothean y Omega, encargada por el "
    "usuario)"
)
DOC = "docs/research/g226_prothean_omega_2026-11-10.md"
COND = [
    S.Condition(fact="ir.formats.exporter_version", op="in", value=["200409"]),
    S.Condition(fact="ir.mass.driver", op="eq", value=0),
]


def ev(type_: str, path: str, locator: str = "", note: str = "") -> S.EvidenceRef:
    return S.EvidenceRef(type=type_, path=path, locator=locator, sha256="", note=note)


def build() -> list[S.Rule]:
    inputs = {
        "inertia": "ir.mass.inertia_about_cog",
        "total": "ir.mass.total",
        "front_fraction": "ir.dimensions.front_axle_fraction",
        "wheelbase": "ir.dimensions.wheelbase",
        "track_front": "ir.dimensions.track_front",
        "track_rear": "ir.dimensions.track_rear",
        "height_front": "ir.dimensions.wheel_center_height_rel_cog_front",
        "height_rear": "ir.dimensions.wheel_center_height_rel_cog_rear",
        "hub_front": "ac:suspensions.ini/FRONT/HUB_MASS",
        "hub_rear": "ac:suspensions.ini/REAR/HUB_MASS",
    }
    units_in = {
        "inertia": "kg·m²",
        "total": "kg",
        "front_fraction": "1",
        "wheelbase": "m",
        "track_front": "m",
        "track_rear": "m",
        "height_front": "m",
        "height_rear": "m",
        "hub_front": "kg",
        "hub_rear": "kg",
    }
    return [
        S.Rule(
            id="car.explicit_inertia_200409_nodriver",
            version=1,
            target="ac:car.ini/EXPLICIT_INERTIA/INERTIA",
            kind="computed",
            transform=S.Transform(
                id="ac_body_inertia",
                params={},
                input_units=units_in,
                output_unit="kg·m²",
                formula="I_c = I − Σ h_i(|r_i|²1 − r_i r_iᵀ) − M_c(|e|²1 − e eᵀ), "
                "e = −Σ h_i r_i / M_c (diagonal)",
            ),
            scope=S.Scope(level="family", family="automation_export"),
            status="provisional",
            inputs=inputs,
            conditions=list(COND),
            assumptions=[
                "piloto 0 kg (D-G217-CG = A): el cuerpo es el mismo que el de "
                "CG_LOCATION/BASEY sin piloto",
                "masas puntuales en los nodos de BeamNG (G2.13: nodos y masa del "
                "conversor = juego); sin los 2 nodos de cámara (0,48 kg)",
                "AC solo admite la diagonal: los productos de inercia (≤ 116 kg·m² en "
                "los cuatro 200409 probados) se descartan",
            ],
            open_issues=[],
            source_doc=[
                ev(
                    "code",
                    "src/extract/mass_geometry.py",
                    "_inertia, compute_mass_geometry",
                ),
                ev("code", "src/importers/beamng.py", "mass.inertia_about_cog"),
                ev("code", "src/attrs/transforms.py", "t_ac_body_inertia"),
            ],
            target_doc=[
                ev(
                    "code",
                    "output/g211_transmissions_2026-11-02/acs_drivetrain/c/"
                    "",
                    "coche de AC: [EXPLICIT_INERTIA] → setMassExplicitInertia; "
                    "si no, [BASIC] INERTIA → el cálculo de inercia por caja de masa (ausente = 0,0,0)",
                ),
                ev(
                    "doc",
                    "docs/research/ensamblado.md",
                    "§9.13 rama explícita (Ibiza v6)",
                ),
            ],
            evidence=[
                ev("report", DOC, "§2 inercia nula en G2.25 y corrección en AC"),
                ev(
                    "report",
                    "output/g226_2026-11-10/inercia/calcular_inercia.json",
                    "tensor exacto con los centros de rueda medidos",
                ),
            ],
            validations=[],
            history=[
                S.HistoryEntry(
                    date=DATE,
                    author=AUTHOR,
                    action="create",
                    reason="G2.26: AC cargaba los 200409 con inercia de carrocería "
                    "0,0,0",
                    status_from="",
                    status_to="provisional",
                )
            ],
            notes="G2.26: solo exportador 200409, ejes independientes y piloto 0 kg",
        )
    ]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(
        "--catalog",
        type=Path,
        default=REPO_CATALOG,
        help="catálogo de destino (por defecto, el del repositorio)",
    )
    args = ap.parse_args()
    rules = build()
    for r in rules:
        S.from_dict(S.to_dict(r))  # valida
    if args.dry_run:
        for r in rules:
            print("ok (sin escribir):", r.key)
        return 0
    for r in rules:
        print("escrita:", write_rule(args.catalog, r))
    Catalog.load(args.catalog)  # el catálogo completo sigue siendo válido
    return 0


if __name__ == "__main__":
    sys.exit(main())
