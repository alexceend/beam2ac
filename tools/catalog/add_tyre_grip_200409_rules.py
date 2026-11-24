"""Modelo de agarre del neumático del 200409 desde las medidas del banco (G2.20, 2026-11-07).

    python tools/catalog/add_tyre_grip_200409_rules.py [--dry-run]

Encargada por el usuario en G2.20. Solo añade; nunca sobrescribe. Solo el exportador 200409
(lista cerrada) y solo con las medidas del banco presentes en el IR
(``ir.tyres.axles.<eje>.force_slip_samples`` y ``reference_load``; registro
``beamng_tyre_bench`` asociado por la huella del origen).

Añade:

* atributo y objetivo ``ac:tyres.ini/{FRONT,REAR}/CX_MULT`` (neumático de AC lo lee con
  VERSION ≥ 10 [C]; neumático de AC: el máximo longitudinal está en p/CX_MULT);
* aplicabilidad genérica ``app.tyres.<eje>.cx_mult@1``: CX_MULT solo se lee con VERSION ≥ 10
  [C] ⇒ «no aplica» con VERSION < 10 (el Ibiza, VERSION 7, no gana un objetivo sin resolver);
* ``tyre.header_version_200409@1``: ``[HEADER] VERSION = 10`` (modelo SCTM:
  neumático de AC llama a el cálculo de fuerzas del neumático de AC con VERSION ≥ 10 [C]);
* aplicabilidad ``app.tyres.<eje>.<clave>_200409@1`` para DX0, DY0, DX1, DY1 y XMU: con
  VERSION ≥ 5 AC no los lee (DX0…DY1) o los anula (XMU) [C] ⇒ «no aplica». Solo 200409: el
  Ibiza (VERSION 7, decisiones) no cambia;
* por eje (``tyre.<eje>.<clave>_200409@1``, ``provisional``):
  - ``ANGULAR_INERTIA`` ← ``rotational_inertia`` (Σ m·r² de los nodos del conjunto rotatorio);
  - ``RATE`` ← ``vertical_stiffness`` (escalones de carga; neumático de AC:
    carga = (RADIUS − d)·RATE + DAMP·v [C]);
  - ``FZ0`` ← ``reference_load`` (carga de referencia de la parametrización);
  - 13 claves SCTM ← ``ac_sctm_fit`` (DY_REF, DX_REF, LS_EXPY, LS_EXPX,
    FRICTION_LIMIT_ANGLE, FLEX_GAIN, FALLOFF_LEVEL, FALLOFF_SPEED, CX_MULT, BRAKE_DX_MOD,
    SPEED_SENSITIVITY, COMBINED_FACTOR, CAMBER_GAIN), con tolerancia ``MAX_RMS``;
  - ``PRESSURE_IDEAL`` ← presión estática (``pressureRatio`` = p/IDEAL − 1 = 0; con IDEAL = 0
    AC dividiría por cero) [C];
  - ``ROLLING_RESISTANCE_0`` ← ``ac_rolling_resistance``.

No resuelve (quedan ausentes, AC usa su valor interno; documentado en el informe):
``DAMP`` (la amortiguación vertical del neumático de BeamNG no se comporta como un coeficiente
viscoso: 2 000–22 000 N·s/m según frecuencia y duración de la excitación; las dos reglas
``tyre.*.damp_200409@1`` de un primer paso de esta fase se retiraron:
``output/g220_2026-11-07/retiradas/``), ``RELAXATION_LENGTH`` (0 ⇒ deslizamiento instantáneo; no medido), ``DCAMBER_0/1`` (0 ⇒ 0,1 y
−0,8 internos, los mismos que usa el ajuste), ``PRESSURE_*_GAIN`` (0: sin efecto con la presión
constante; medido: ±20 % de presión cambia μ ≤ 1 %), ``ROLLING_RESISTANCE_1/SLIP``,
``WEAR_CURVE`` (sin archivo: AC lo informa y sigue sin desgaste [C]) y la térmica.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.attrs import schema as S  # noqa: E402
from src.attrs.store import REPO_CATALOG, Catalog, write_rule  # noqa: E402

ATTR = ROOT / "catalog/attributes/assettocorsa.json"
TARGETS = ROOT / "catalog/targets/ac_physics.json"
DATE = "2026-11-07"
AUTHOR = "claude (G2.20: modelo de agarre del neumático, encargada por el usuario)"
DOC = "docs/research/g220_tyre_grip_model_2026-11-07.md"
CODE = "output/g220_2026-11-07/acs_tyre/c"
COND = [S.Condition(fact="ir.formats.exporter_version", op="in", value=["200409"])]
# Tolerancia del ajuste: error RMS máximo (en μ) de cada tipo de barrido (lat_y, lon_x,
# comb_y, comb_x). Justificación en el informe G2.20 §6 (repetibilidad entre sesiones y
# resolución de los intervalos).
MAX_RMS = 0.06

SCTM_KEYS = (
    ("DY_REF", "dy_ref", "1"),
    ("DX_REF", "dx_ref", "1"),
    ("LS_EXPY", "ls_expy", "1"),
    ("LS_EXPX", "ls_expx", "1"),
    ("FRICTION_LIMIT_ANGLE", "friction_limit_angle", "deg"),
    ("FLEX_GAIN", "flex_gain", "1"),
    ("FALLOFF_LEVEL", "falloff_level", "1"),
    ("FALLOFF_SPEED", "falloff_speed", "1"),
    ("CX_MULT", "cx_mult", "1"),
    ("BRAKE_DX_MOD", "brake_dx_mod", "1"),
    ("SPEED_SENSITIVITY", "speed_sensitivity", "1"),
    ("COMBINED_FACTOR", "combined_factor", "1"),
    ("CAMBER_GAIN", "camber_gain", "1"),
)


def ev(type_: str, path: str, locator: str = "", note: str = "") -> S.EvidenceRef:
    return S.EvidenceRef(type=type_, path=path, locator=locator, sha256="", note=note)


SRC_DOC = [
    ev("code", "src/extract/tyre_bench.py", "reduce_rows / binned_points / vertical_*"),
    ev("code", "src/importers/beamng.py", "_import_tyre_bench"),
    ev("code", "output/g220_2026-11-07/banco/lua_src/lua/vehicle/extensions/beam2acTyre.lua",
       "banco virtual"),
]
TGT_DOC = [
    ev("code", CODE, "neumático de AC"),
    ev("code", CODE, "neumático de AC (claves; desplazamientos en ../tipos.txt)"),
    ev("code", CODE, "neumático de AC"),
    ev("code", "src/ac/tyre_sctm.py", "transcripción del modelo"),
]
EVID = [ev("report", DOC, "")]


def _hist(reason: str, status: str = "provisional") -> list[S.HistoryEntry]:
    return [S.HistoryEntry(date=DATE, author=AUTHOR, action="create", reason=reason,
                           status_from="", status_to=status)]


def samples_cond(axle: str) -> list[S.Condition]:
    return list(COND) + [
        S.Condition(fact=f"ir.tyres.axles.{axle}.force_slip_samples", op="exists", value=None),
        S.Condition(fact=f"ir.tyres.axles.{axle}.reference_load", op="exists", value=None),
    ]


def identity_rule(axle, key, field, unit, kind, assumptions, reason) -> S.Rule:
    return S.Rule(
        id=f"tyre.{axle}.{key.lower()}_200409", version=1,
        target=f"ac:tyres.ini/{axle.upper()}/{key}", kind=kind,
        transform=S.Transform(id="identity", params={}, input_units={"x": unit},
                              output_unit=unit, formula=""),
        scope=S.Scope(level="family", family="automation_export"), status="provisional",
        inputs={"x": f"ir.tyres.axles.{axle}.{field}"},
        conditions=list(COND) + [S.Condition(fact=f"ir.tyres.axles.{axle}.{field}",
                                             op="exists", value=None)],
        assumptions=list(assumptions) + ["ningún 200409 se ha probado en AC"],
        source_doc=list(SRC_DOC), target_doc=list(TGT_DOC), evidence=list(EVID),
        history=_hist(reason), notes="G2.20: solo 200409 con el neumático medido en el banco",
    )


def sctm_rule(axle: str, key: str, unit: str) -> S.Rule:
    return S.Rule(
        id=f"tyre.{axle}.{key.lower()}_200409", version=1,
        target=f"ac:tyres.ini/{axle.upper()}/{key}", kind="computed",
        transform=S.Transform(
            id="ac_sctm_fit", params={"output": key, "max_rms": MAX_RMS, "round": 4},
            input_units={"samples": "dataset", "fz0": "N"}, output_unit=unit,
            formula="mínimos cuadrados de (F_SCTM − F_medida)/Fz sobre los barridos medidos",
        ),
        scope=S.Scope(level="family", family="automation_export"), status="provisional",
        inputs={"samples": f"ir.tyres.axles.{axle}.force_slip_samples",
                "fz0": f"ir.tyres.axles.{axle}.reference_load"},
        conditions=samples_cond(axle),
        assumptions=[
            "parámetro AJUSTADO (no medido directamente): reproduce las curvas Fx/Fy medidas "
            "con el error del ajuste que figura en los supuestos del valor",
            "superficie: smallgrid de BeamNG; en AC el agarre se multiplica por el de la pista "
            "(gripMod × dynamicGripLevel)",
            "sin térmica ni desgaste (el banco no los activa; temperatura constante 288,15 K)",
            "ningún 200409 se ha probado en AC",
        ],
        source_doc=list(SRC_DOC), target_doc=list(TGT_DOC), evidence=list(EVID),
        history=_hist("G2.20: modelo SCTM ajustado a las curvas medidas en BeamNG"),
        notes="G2.20: solo 200409 con el neumático medido; tolerancia MAX_RMS",
    )


def header_rule() -> S.Rule:
    return S.Rule(
        id="tyre.header_version_200409", version=1, target="ac:tyres.ini/HEADER/VERSION",
        kind="format",
        transform=S.Transform(id="constant", params={"value": 10}, output_unit="1"),
        scope=S.Scope(level="family", family="automation_export"), status="provisional",
        conditions=samples_cond("front") + samples_cond("rear")[1:],
        assumptions=["VERSION 10 = modelo SCTM (neumático de AC → el cálculo de fuerzas del neumático de AC) [C]"],
        source_doc=list(SRC_DOC), target_doc=list(TGT_DOC), evidence=list(EVID),
        history=_hist("G2.20: el ajuste es del modelo SCTM, que solo se usa con VERSION ≥ 10"),
        notes="G2.20",
    )


def app_rule(axle: str, key: str) -> S.Rule:
    return S.Rule(
        id=f"app.tyres.{axle}.{key.lower()}_200409", version=1,
        target=f"ac:tyres.ini/{axle.upper()}/{key}", kind="applicability",
        transform=S.Transform(id="constant", params={"value": None}, output_unit="1"),
        scope=S.Scope(level="family", family="automation_export"), status="validated",
        conditions=list(COND),
        applies_when=[S.Condition(fact="ac:tyres.ini/HEADER/VERSION", op="lt", value=5)],
        source_doc=[ev("code", CODE, "neumático de AC")],
        target_doc=[ev("code", CODE, "neumático de AC")],
        evidence=list(EVID),
        history=_hist(
            "la lectura de compuestos de AC: DX0/DY0/DX1/DY1 solo con VERSION < 5; XMU = 0 con VERSION ≥ 5",
            "validated"),
        notes="G2.20: solo 200409 (el Ibiza mantiene sus decisiones)",
    )


def cx_app_rule(axle: str) -> S.Rule:
    return S.Rule(
        id=f"app.tyres.{axle}.cx_mult", version=1,
        target=f"ac:tyres.ini/{axle.upper()}/CX_MULT", kind="applicability",
        transform=S.Transform(id="constant", params={"value": None}, output_unit="1"),
        scope=S.Scope(level="generic"), status="validated",
        applies_when=[S.Condition(fact="ac:tyres.ini/HEADER/VERSION", op="ge", value=10)],
        source_doc=[ev("code", CODE, "neumático de AC")],
        target_doc=[ev("code", CODE, "neumático de AC")],
        evidence=list(EVID),
        history=_hist("la lectura de compuestos de AC: CX_MULT solo con VERSION ≥ 10", "validated"),
        notes="G2.20: CX_MULT solo existe para AC con VERSION ≥ 10",
    )


ONLY_NEW = ("app.tyres.front.cx_mult", "app.tyres.rear.cx_mult")


def rules() -> list[S.Rule]:
    out = [header_rule(), cx_app_rule("front"), cx_app_rule("rear")]
    for axle in ("front", "rear"):
        for key in ("DX0", "DY0", "DX1", "DY1", "XMU"):
            out.append(app_rule(axle, key))
        out.append(identity_rule(
            axle, "ANGULAR_INERTIA", "rotational_inertia", "kg·m²", "computed",
            ["inercia del conjunto rotatorio de BeamNG (banda + buje) con las masas del juego; "
             "en AC es el divisor de neumático de AC [C]"],
            "G2.20: inercia del conjunto rotatorio calculada en el juego"))
        out.append(identity_rule(
            axle, "RATE", "vertical_stiffness", "N/m", "measured",
            ["rigidez vertical rodando a 20 m/s: regresión de Fz frente a la altura del buje "
             "en 4 escalones de carga (×0,65…×2 de la carga estática); el neumático de "
             "BeamNG no es lineal (la rigidez local a 3 Hz difiere hasta un 40 %) y la altura "
             "estática del buje en AC (RADIUS − Fz/RATE) no coincide con la de BeamNG"],
            "G2.20: rigidez vertical medida rodando (escalones de carga)"))
        out.append(identity_rule(
            axle, "FZ0", "reference_load", "N", "measured",
            ["carga de referencia de la parametrización = carga estática medida de la rueda en "
             "el banco (sin piloto: el banco carga el coche tal cual)"],
            "G2.20: carga de referencia del ajuste"))
        for key, _field, unit in SCTM_KEYS:
            out.append(sctm_rule(axle, key, unit))
        out.append(identity_rule(
            axle, "PRESSURE_IDEAL", "pressure", "psi", "direct",
            ["IDEAL = presión estática ⇒ pressureRatio = 0 (las curvas se midieron a esa "
             "presión); sin PRESSURE_*_GAIN la presión dinámica de AC no cambia el agarre"],
            "G2.20: referencia de presión del modelo (IDEAL = 0 daría una división por cero)"))
        r = S.Rule(
            id=f"tyre.{axle}.rolling_resistance_0_200409", version=1,
            target=f"ac:tyres.ini/{axle.upper()}/ROLLING_RESISTANCE_0", kind="measured",
            transform=S.Transform(id="ac_rolling_resistance", params={},
                                  input_units={"table": "table:m/s->1"}, output_unit="1",
                                  formula="1000·media(−Fx/Fz)"),
            scope=S.Scope(level="family", family="automation_export"), status="provisional",
            inputs={"table": f"ir.tyres.axles.{axle}.rolling_resistance_vs_speed"},
            conditions=list(COND) + [S.Condition(
                fact=f"ir.tyres.axles.{axle}.rolling_resistance_vs_speed", op="exists",
                value=None)],
            assumptions=["ningún 200409 se ha probado en AC"],
            source_doc=list(SRC_DOC), target_doc=list(TGT_DOC), evidence=list(EVID),
            history=_hist("G2.20: resistencia a la rodadura medida"), notes="G2.20",
        )
        out.append(r)
    return out


def cx_attributes() -> list[dict]:
    out = []
    for sec in ("FRONT", "REAR"):
        out.append({
            "schema": "beam2ac.attribute/1",
            "id": f"ac:tyres.ini/{sec}/CX_MULT",
            "game": "assettocorsa",
            "unit": "1",
            "dtype": "number",
            "description": "SCTM (VERSION ≥ 10): el deslizamiento del máximo longitudinal es el "
            "lateral dividido por CX_MULT (neumático de AC: cf·cfXmult)",
            "doc": [{"type": "code", "path": CODE,
                     "locator": "neumático de AC (CX_MULT con VERSION ≥ 10); "
                     "neumático de AC", "sha256": "", "note": ""}],
            "required_for_load": False,
        })
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    rs = rules()
    for r in rs:
        S.from_dict(S.to_dict(r))
    raw = ATTR.read_text(encoding="utf-8")
    data = json.loads(raw)
    assert json.dumps(data, indent=1, ensure_ascii=False) + "\n" == raw
    traw = TARGETS.read_text(encoding="utf-8")
    tg = json.loads(traw)
    assert json.dumps(tg, indent=1, ensure_ascii=False) + "\n" == traw
    new_attrs = cx_attributes()
    for x in new_attrs:
        if any(y["id"] == x["id"] for y in data) or x["id"] in tg["targets"]:
            raise SystemExit(f"ya existe: {x['id']}")
    if a.dry_run:
        print(f"ok (sin escribir): {len(new_attrs)} atributos, {len(rs)} reglas")
        return 0
    for x in new_attrs:
        sec = x["id"].split("/")[1]
        i = next(k for k, y in enumerate(data) if y["id"] == f"ac:tyres.ini/{sec}/COMBINED_FACTOR") + 1
        data.insert(i, x)
        j = tg["targets"].index(f"ac:tyres.ini/{sec}/COMBINED_FACTOR") + 1
        tg["targets"].insert(j, x["id"])
    ATTR.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    TARGETS.write_text(json.dumps(tg, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    for r in rs:
        print("escrita", write_rule(REPO_CATALOG, r))
    Catalog.load(REPO_CATALOG)
    return 0


def add_missing() -> int:
    """Segunda pasada (G2.20): escribe solo las reglas que aún no existen."""
    have = {p.stem for p in (ROOT / "catalog/rules").glob("*.json")}
    n = 0
    for r in rules():
        if f"{r.id}@{r.version}" not in have:
            S.from_dict(S.to_dict(r))
            print("escrita", write_rule(REPO_CATALOG, r))
            n += 1
    Catalog.load(REPO_CATALOG)
    return n


if __name__ == "__main__":
    if "--solo-nuevas" in sys.argv:
        print(add_missing(), "reglas nuevas")
        sys.exit(0)
    sys.exit(main())
