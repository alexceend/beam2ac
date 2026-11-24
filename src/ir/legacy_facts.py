"""Adaptador IR → hechos del motor de reglas (identificadores ``bng.*`` actuales).

Compatibilidad de G1: el catálogo publicado referencia hechos ``bng.*``. Este módulo los
obtiene del IR (no de BeamNG), con la misma procedencia, para que el motor de reglas
consuma el IR sin cambiar ninguna regla. La equivalencia exacta con la construcción
anterior está comprobada en ``tests/test_ir_equivalence.py`` frente a
``output/g1_ir_2026-10-30/hechos_antes.json``.

Dos hechos son del DESTINO (Assetto Corsa) y se derivan aquí, no en el importador:

* ``bng.engine.coast_ref_*``: ajuste lineal de ``COAST_REF``
  (``src.ac.engine.fit_coast_ref``) sobre ``engine.coast_curve`` y
  ``engine.speed_ceiling`` del IR;
* ``bng.suspension.<eje>.ac_type``: tipo de suspensión de AC por la etiqueta de la
  pieza (``AC_SUSPENSION_TYPE_BY_LABEL``, la misma regla que
  ``src.extract.suspension.TYPE_BY_LABEL``).

G2: las reglas migran a hechos nativos ``ir.<ruta>`` (:func:`ir_facts`, también
incluidos aquí); ``eng.power_lut@4`` es la primera. Los ``bng.*`` se retiran cuando
ninguna regla vigente los use.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from src.attrs import schema as S
from src.attrs.facts import Fact

from .param import Param
from .vehicle import VehicleIR

AC_SUSPENSION_TYPE_BY_LABEL = (("macpherson", "STRUT"), ("strut", "STRUT"))

STATIC: dict[str, str] = {
    "identity.name": "bng.info.name",
    "identity.source_tool": "bng.info.type",
    "identity.author": "bng.info.author",
    "formats.exporter_version": "bng.info.exporter_version",
    "declared.weight": "bng.info.weight",
    "declared.power": "bng.info.power",
    "declared.torque": "bng.info.torque",
    "declared.power_peak_speed": "bng.info.power_peak_rpm",
    "declared.torque_peak_speed": "bng.info.torque_peak_rpm",
    "declared.drivetrain": "bng.info.drivetrain",
    "declared.transmission": "bng.info.transmission",
    "declared.implied_power": "bng.info.implied_power",
    "engine.device_type": "bng.engine.device_type",
    "engine.idle_speed": "bng.engine.idle_rpm",
    "engine.inertia": "bng.engine.inertia",
    "engine.clutch_additional_inertia": "bng.engine.clutch_additional_inertia",
    "engine.rev_limiter_mode": "bng.engine.rev_limiter",
    "engine.speed_ceiling": "bng.engine.rpm_ceiling",
    "engine.wot_torque_curve": "bng.engine.net_wot_curve",
    "engine.peak_torque": "bng.engine.peak_torque",
    "engine.peak_torque_speed": "bng.engine.peak_torque_rpm",
    "engine.peak_power": "bng.engine.peak_power",
    "engine.peak_power_speed": "bng.engine.peak_power_rpm",
    "engine.forced_induction": "bng.engine.has_turbo",
    "drivetrain.layout": "bng.drivetrain.traction",
    "drivetrain.gearbox.kind": "bng.gearbox.type",
    "drivetrain.gearbox.reverse_ratio": "bng.gearbox.reverse_ratio",
    "drivetrain.gearbox.forward_count": "bng.gearbox.forward_count",
    "drivetrain.final_ratio": "bng.diff.final_ratio",
    "drivetrain.front_torque_share": "bng.awd.front_share",
    "drivetrain.gearbox.shift_delay": "bng.shift.transmission_shift_delay",
    "drivetrain.clutch.lock_torque": "bng.clutch.lock_torque",
    "drivetrain.clutch.lock_torque_default": "bng.clutch.lock_torque_default",
    "mass.total": "bng.mass.total",
    "mass.wheel_assembly": "bng.mass.wheel_assembly",
    "mass.fuel_capacity": "bng.fuel.capacity",
    "mass.fuel_start": "bng.fuel.start",
    "mass.driver": "project.driver_mass",
    "dimensions.wheelbase": "bng.geometry.wheelbase",
    "dimensions.front_axle_fraction": "bng.geometry.front_axle_fraction",
    "dimensions.track_front": "bng.geometry.track_front",
    "dimensions.track_rear": "bng.geometry.track_rear",
    "dimensions.cog_height": "bng.geometry.cog_height",
    "brakes.front_share_full_pedal": "bng.brakes.front_share_pedal_full",
    "brakes.front_share_below_knee": "bng.brakes.front_share_below_knee",
    "aero.cda": "bng.aero.cda",
    "aero.target_front": "bng.aero.target_front",
    "aero.target_rear": "bng.aero.target_rear",
    "aero.cla_front": "bng.aero.cla_front",
    "aero.cla_rear": "bng.aero.cla_rear",
}
# campo del diferencial -> sufijo (2WD: bng.diff.<x>; AWD: bng.awd.<rol>_<y>)
_DIFF_2WD = {
    "kind": "type",
    "preload": "lsd_preload",
    "lock_coef": "lsd_lock_coef",
    "rev_lock_coef": "lsd_rev_lock_coef",
}
_DIFF_AWD = {
    "kind": "diff_type",
    "preload": "lsd_preload",
    "lock_coef": "lsd_lock_coef",
    "rev_lock_coef": "lsd_rev_lock_coef",
}


# G2.5a: equivalencias hecho ``bng.*`` ↔ hecho ``ir.*`` que las VALIDACIONES deben
# respetar (una validación que cubre uno cubre también el otro). Lista CERRADA: solo
# pares demostrados, nunca por coincidencia de nombres. Demostración:
#   1. por construcción: el hecho ``bng.*`` sale de ``STATIC[<ruta>]``, es decir, del
#      MISMO parámetro del IR que ``ir.<ruta>`` (comprobado abajo al importar);
#   2. en cada vehículo: :func:`validation_fact_equivalents` solo activa el par si los
#      dos hechos existen y son idénticos (valor, unidad, clase, procedencia y nota).
# Único caso con una validación hoy (auditoría G2 §7.5): la de masa
# (``validations_bng`` y validaciones registradas en el catálogo) cubre
# ``bng.mass.total`` y ``car.totalmass@2`` lee ``ir.mass.total``.
VALIDATION_FACT_EQUIVALENTS: tuple[tuple[str, str], ...] = (
    ("bng.mass.total", "ir.mass.total"),
)
for _bng, _ir in VALIDATION_FACT_EQUIVALENTS:
    if not _ir.startswith("ir.") or STATIC.get(_ir[3:]) != _bng:
        raise RuntimeError(f"equivalencia de validación no demostrada: {_bng} ↔ {_ir}")


def validation_fact_equivalents(facts: dict[str, Fact]) -> dict[str, tuple[str, ...]]:
    """Equivalencias de :data:`VALIDATION_FACT_EQUIVALENTS` activas para estos hechos
    (simétricas). Un par no se activa si falta uno de los dos hechos o si difieren en
    algo más que el identificador: entonces no hay equivalencia demostrada y cada
    validación sigue cubriendo solo lo que declara."""
    out: dict[str, list[str]] = {}
    for a, b in VALIDATION_FACT_EQUIVALENTS:
        fa, fb = facts.get(a), facts.get(b)
        if fa is None or fb is None:
            continue
        da, db = fa.to_dict(), fb.to_dict()
        da.pop("id")
        db.pop("id")
        if da != db:
            continue
        out.setdefault(a, []).append(b)
        out.setdefault(b, []).append(a)
    return {k: tuple(v) for k, v in out.items()}


# G2.14: campos del IR de suspensión que NO tienen hecho ``bng.*`` (los hechos heredados
# son los de antes de G1, fixture congelado; lo nuevo solo existe como ``ir.*``)
IR_ONLY_SUSPENSION = frozenset(
    {
        "unsprung_mass_per_wheel",
        # G2.16
        "linkage",
        "anchors",
        "kinematic_motion_ratio",
        "wheel_rate",
        "spring_preload_travel",
        "damper_rate_slow",
        "rest_camber",
        "rest_toe",
        # G2.17
        "rod_inward_shift_per_toe_in",
        "damper_rate_slow_bump",
        "damper_rate_slow_rebound",
        "damper_rate_fast_bump",
        "damper_rate_fast_rebound",
        "damper_fast_threshold_bump",
        "damper_fast_threshold_rebound",
        "bumpstop_travel_up",
        "bumpstop_travel_down",
        "bumpstop_rate",
        "bumpstop_progressive",
        "bumpstop_damping",
        "antiroll_rate",
        # G2.19
        "strut_motion_ratio",
    }
)
# G2.15: lo mismo para los campos nuevos de frenos por eje
IR_ONLY_BRAKES = frozenset(
    {
        "input_split",
        "split_coef",
        "thermal_model",
        "pad_material",
        "disc_type",
        "disc_diameter",
        "disc_mass",
        "abs",
        "abs_slip_target",
        "abs_frequency",
    }
)

# G2.20: campos medidos del neumático (banco de BeamNG), sin hecho bng.* heredado
IR_ONLY_TYRES = frozenset(
    {
        "reference_load",
        "free_rolling_radius",
        "vertical_stiffness",
        "vertical_damping",
        "rotational_inertia",
        "rolling_resistance_vs_speed",
        "force_slip_samples",
        "relaxation_length",
    }
)


def legacy_id(path: str, ir: VehicleIR) -> str | None:
    """Identificador ``bng.*`` de una ruta del IR (``None`` si no tiene hecho)."""
    if path in STATIC:
        return STATIC[path]
    parts = path.split(".")
    if path.startswith("drivetrain.gearbox.forward_ratios."):
        return f"bng.gearbox.forward_ratio_{parts[-1]}"
    if path.startswith("formats.code_versions."):
        return f"bng.code.{parts[-1]}_sha256"
    if parts[0] in ("tyres", "brakes", "suspension") and parts[1] == "axles":
        pos, name = parts[2], parts[3]
        if parts[0] == "suspension" and name == "torsionbar_rates":
            return None  # se trata aparte (el último valor presente)
        if parts[0] == "suspension" and name in IR_ONLY_SUSPENSION:
            return None  # G2.14: dato nativo del IR, sin hecho bng.* heredado
        if parts[0] == "brakes" and name in IR_ONLY_BRAKES:
            return None  # G2.15: dato nativo del IR, sin hecho bng.* heredado
        if parts[0] == "tyres" and name in IR_ONLY_TYRES:
            return None  # G2.20: dato nativo del IR, sin hecho bng.* heredado
        prefix = {"tyres": "tyre", "brakes": "brakes", "suspension": "suspension"}
        return f"bng.{prefix[parts[0]]}.{pos}.{name}"
    if path.startswith("drivetrain.differentials."):
        role, name = parts[2], parts[3]
        if ir.drivetrain.layout.value == "AWD":
            return f"bng.awd.{role}_{_DIFF_AWD[name]}" if name in _DIFF_AWD else None
        return f"bng.diff.{_DIFF_2WD[name]}" if name in _DIFF_2WD else None
    return None


def _fact(fid: str, p: Param) -> Fact:
    return Fact(fid, p.value, p.unit, p.kind, list(p.refs), p.note)


def _config_ref(ir: VehicleIR) -> S.EvidenceRef | None:
    """La referencia a la configuración que el importador pone delante."""
    for p in (ir.mass.total, ir.engine.device_type, ir.engine.speed_ceiling):
        if p.present and p.refs:
            return p.refs[0]
    return None


def ir_facts(ir: VehicleIR) -> dict[str, Fact]:
    """Hechos nativos del IR: ``ir.<ruta>`` por cada parámetro PRESENTE (G2).

    Misma unidad, clase, nota y procedencia que el parámetro. Lo ausente, no aplicable
    o sin interpretar no produce hecho: una regla que lo necesite lo verá como «falta
    el hecho» y no se aplicará (nunca un valor inventado)."""
    return {f"ir.{path}": _fact(f"ir.{path}", p) for path, p in ir.walk() if p.present}


def ir_to_facts(ir: VehicleIR) -> dict[str, Fact]:
    """Hechos ``bng.*`` de compatibilidad + hechos nativos ``ir.*``."""
    facts: dict[str, Fact] = {}
    for path, p in ir.walk():
        if not p.present:
            continue
        fid = legacy_id(path, ir)
        if fid is not None:
            facts[fid] = _fact(fid, p)
    data = _config_ref(ir)
    for pos, axle in ir.suspension.axles.items():
        rates = [p for p in axle.torsionbar_rates if p.present]
        if rates:
            fid = f"bng.suspension.{pos}.torsionbar_rate"
            facts[fid] = _fact(fid, rates[-1])
        label = axle.label.value if axle.label.present else None
        ac_type = (
            next(
                (t for key, t in AC_SUSPENSION_TYPE_BY_LABEL if key in label.lower()),
                None,
            )
            if label
            else None
        )
        if ac_type is not None and data is not None:
            fid = f"bng.suspension.{pos}.ac_type"
            facts[fid] = Fact(
                fid,
                ac_type,
                "enum",
                "computed",
                [
                    data,
                    S.EvidenceRef(
                        type="code",
                        path="src/extract/suspension.py",
                        locator="TYPE_BY_LABEL",
                    ),
                ],
                "tipo AC por la etiqueta declarada de la pieza (MacPherson/strut -> "
                "STRUT)",
            )
    facts.update(_coast_facts(ir, data))
    facts.update(ir_facts(ir))
    return facts


def _coast_facts(ir: VehicleIR, data: Any) -> dict[str, Fact]:
    from src.ac.engine import fit_coast_ref

    e = ir.engine
    if not (e.coast_curve.present and e.speed_ceiling.present) or data is None:
        return {}
    fit = fit_coast_ref(
        SimpleNamespace(
            coast=[tuple(x) for x in e.coast_curve.value],
            rpm_ceiling=e.speed_ceiling.value,
        )
    )
    if fit is None:
        return {}
    basis = e.coast_basis.value if e.coast_basis.present else ""
    ref = S.EvidenceRef(type="code", path="src/ac/engine.py", locator="fit_coast_ref")
    return {
        "bng.engine.coast_ref_rpm": Fact(
            "bng.engine.coast_ref_rpm", fit.rpm, "rpm", "computed", [data, ref]
        ),
        "bng.engine.coast_ref_torque": Fact(
            "bng.engine.coast_ref_torque",
            round(fit.torque, 1),
            "N·m",
            "computed",
            [data, ref],
            f"error máx. {fit.max_abs_error:.1f} N·m; {basis}",
        ),
        "bng.engine.coast_ref_nonlinearity": Fact(
            "bng.engine.coast_ref_nonlinearity",
            fit.non_linearity,
            "1",
            "computed",
            [data, ref],
        ),
    }
