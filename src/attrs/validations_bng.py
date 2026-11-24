"""Validaciones automáticas de un vehículo BeamNG contra sus valores declarados
(``info_*.json``).

Criterios (explícitos y auditables):

* Los valores declarados son enteros de la interfaz: se aceptan diferencias ≤ 0,5 de su
  unidad (``ROUNDING``). Es el mismo criterio que ``check_reference_mass`` y
  ``engine_camso.compare_with_info``.
* El par declarado (``Torque``) del exportador 40340 está **truncado**, no redondeado
  (``TORQUE_DECLARED_BY_EXPORTER``). Evidencia: el pico continuo de Automation es ≥ el
  pico de la malla de 100 rpm que exporta, y con 335,53 (autist_cuanto) y 89,58
  (sucette_poubelle) en la malla un redondeo daría ≥ 336 y ≥ 90; los declarados son 335
  y 89. Ningún vehículo de esa versión lo contradice. Para él se acepta
  ``−0,5 ≤ calculado − declarado < 1`` (el −0,5 conserva el margen de la malla). En el
  exportador 38022 (Ibiza 307,8 → 308, Porsche 463,62 → 464) y en uno desconocido se
  mantiene ±0,5.
* La potencia declarada (``Power``) es un entero truncado en algunos exportes; si existe
  ``Weight/Power`` se usa la potencia implícita ``Weight / (Weight/Power)``.
* Las rpm de pico declaradas son compatibles si el valor de la curva en esas rpm está a
  ≤ 0,5 de la unidad del pico calculado (empate en la meseta de la malla de rpm).
* La velocidad punta declarada no se valida: falta la definición de BeamNG (radio bajo
carga,
  resistencia) y no hay tolerancia justificada → ``inconclusive``.

``covers`` indica qué hechos o atributos de AC respalda cada validación.

G2.8: las entradas se leen de hechos ``ir.*`` (``engine.*``, ``declared.*``,
``drivetrain.layout``, ``mass.total``, ``formats.exporter_version``), el MISMO
parámetro del IR del que salían los ``bng.*`` (``legacy_facts.STATIC``;
``tests/test_g28_bng_consumers.py``). Pertenece a la capa de validación del ORIGEN
(valores declarados en ``info_*.json`` frente a lo medido) y no conoce el destino.
``formats.exporter_version`` solo lo rellena el perfil de Automation, así que
``TORQUE_DECLARED_BY_EXPORTER`` no puede aplicarse a otro exportador.
El ``covers`` de la validación de masa sigue siendo ``bng.mass.total``: es la identidad
publicada de la validación (las registradas en ``catalog/validations`` también lo
cubren) y la equivalencia cerrada de G2.5a lo enlaza con ``ir.mass.total``.
"""

from __future__ import annotations

from typing import Any

from . import schema as S
from .facts_bng import Fact

ROUNDING = 0.5
MASS_COVERS = ["bng.mass.total"]
WEIGHT_UNDERREPORT_MAX = 60.0  # kg: observado +1,7…+38,8 en los 40340 medidos
WEIGHT_UNDERREPORTED = {"40340"}  # exportadores cuyo Weight < masa del juego (G2.29)
IN_GAME_MASS_TOL = 0.6  # kg: nodos de cámara que añade el juego (0,48 kg en G2.13)
TORQUE_DECLARED_BY_EXPORTER = {"40340": "truncado"}  # resto: redondeado (±0,5)
HP_W = 745.69987158227


def _number(x: Any) -> float | None:
    """El valor si es un número (no booleano); si no, ``None``."""
    return x if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def _interp(table: list[list[float]], x: float) -> float | None:
    for (x0, y0), (x1, y1) in zip(table, table[1:]):
        if x0 <= x <= x1:
            return y0 if x1 == x0 else y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return None


SAWTOOTH_FRACTION = 0.05  # salto mínimo entre pasos, como fracción del par pico
SAWTOOTH_RUN = 3  # cambios de signo seguidos; corpus: agus 3, otros 21 proyectos 0


def source_sawtooth(curve: list[list[float]]) -> int:
    """Mayor racha de cambios de signo consecutivos de la pendiente con saltos grandes
    (> ``SAWTOOTH_FRACTION`` del par pico): zigzag de paso a paso, no un escalón aislado
    (spool del turbo). Es un defecto de la tabla que Automation exporta (el solver no
    converge; ``power_lut_agus_nrt_dientes_2026-11-19.md``), no del conversor."""
    peak = max((t for _, t in curve), default=0.0)
    d = [b[1] - a[1] for a, b in zip(curve, curve[1:])]
    best = run = 0
    for x, y in zip(d, d[1:]):
        big = abs(x) > SAWTOOTH_FRACTION * peak and abs(y) > SAWTOOTH_FRACTION * peak
        run = run + 1 if x * y < 0 and big else 0
        best = max(best, run)
    return best


def info_validations(
    vehicle: str, variant: str, facts: dict[str, Fact]
) -> list[S.Validation]:
    out: list[S.Validation] = []
    ev = [
        S.EvidenceRef(
            type="file", path=f"info_{variant}.json", locator="valores declarados"
        )
    ]

    def v(
        vid: str,
        covers: list[str],
        result: str,
        expected: Any,
        observed: Any,
        tol: Any,
        note: str,
        method: str = "info_reference",
        evidence: list | None = None,
    ) -> None:
        out.append(
            S.Validation(
                id=f"{vehicle}.{variant}.{vid}",
                vehicle=vehicle,
                variant=variant,
                method=method,
                covers=covers,
                result=result,
                expected=expected,
                observed=observed,
                tolerance=tol,
                evidence=evidence if evidence is not None else ev,
                note=note,
            )
        )

    fv = {k: f.value for k, f in facts.items()}
    curve = fv.get("ir.engine.wot_torque_curve")
    # G2.9: solo con par declarado numérico (antes un texto habría lanzado TypeError)
    if (
        _number(fv.get("ir.declared.torque")) is not None
        and "ir.engine.peak_torque" in fv
        and curve
    ):
        # camsoEngine: Automation declara con la fila 100 del mapa de par y BeamNG solo lee hasta la 99
        # (camsoEngine.lua:725); si el IR trae la referencia (fila 100) se compara con ella. La curva que se
        # convierte sigue siendo la de BeamNG (``wot_torque_curve``).
        use_ref = (
            _number(fv.get("ir.engine.reference_peak_torque")) is not None
            and _number(fv.get("ir.engine.reference_peak_power")) is not None
            and bool(fv.get("ir.engine.reference_wot_torque_curve"))
        )
        rcurve = fv["ir.engine.reference_wot_torque_curve"] if use_ref else curve
        peak_t = fv[
            "ir.engine.reference_peak_torque" if use_ref else "ir.engine.peak_torque"
        ]
        peak_p = fv[
            "ir.engine.reference_peak_power" if use_ref else "ir.engine.peak_power"
        ]
        t_diff = peak_t - fv["ir.declared.torque"]
        t_mode = TORQUE_DECLARED_BY_EXPORTER.get(
            str(fv.get("ir.formats.exporter_version")), "redondeado"
        )
        t_ok = (
            -ROUNDING <= t_diff < 1.0
            if t_mode == "truncado"
            else abs(t_diff) <= ROUNDING
        )
        p_calc = peak_p / HP_W
        p_ref = fv.get("ir.declared.implied_power", fv.get("ir.declared.power"))
        p_ok = _number(p_ref) is not None and abs(p_calc - p_ref) <= ROUNDING
        rpm_notes = []
        rpm_ok = True
        # G2.9: los info_*.json oficiales de BeamNG declaran la rpm de pico como rango
        # de texto («3850 - 5150»); no se compara (resultado «inconclusive» si lo demás
        # cuadra) en vez de suponer un número. Con rpm numérica, nada cambia.
        rpm_uncompared = []
        for key in ("ir.declared.torque_peak_speed", "ir.declared.power_peak_speed"):
            raw = fv.get(key)
            if raw is not None and _number(raw) is None:
                rpm_uncompared.append(key)
                rpm_notes.append(f"{key} no numérica ({raw!r}): no comparada")
        trpm = _number(fv.get("ir.declared.torque_peak_speed"))
        if trpm is not None:
            at = _interp(rcurve, trpm)
            ok = at is not None and abs(at - peak_t) <= ROUNDING
            rpm_ok &= ok
            rpm_notes.append(f"par en {trpm:g} rpm = {at} (pico {peak_t:.2f})")
        prpm = _number(fv.get("ir.declared.power_peak_speed"))
        if prpm is not None:
            at = _interp(rcurve, prpm)
            p_at = None if at is None else at * prpm * 2 * 3.141592653589793 / 60 / HP_W
            ok = p_at is not None and abs(p_at - p_calc) <= ROUNDING
            rpm_ok &= ok
            rpm_notes.append(
                f"potencia en {prpm:g} rpm = {p_at and round(p_at, 3)} hp "
                f"(pico {p_calc:.3f})"
            )
        result = (
            "fail"
            if not (t_ok and p_ok and rpm_ok)
            else "inconclusive" if rpm_uncompared else "pass"
        )
        # Una tabla de origen dentada no puede cuadrar con la ficha y BeamNG la carga
        # tal cual: el conversor la reproduce fiel (decisión del usuario, 2026-11-20).
        # Solo se rebaja si el par pico sí cuadra, para no tapar un fallo real.
        if result == "fail" and t_ok and source_sawtooth(curve) >= SAWTOOTH_RUN:
            result = "inconclusive"
            rpm_notes.append(
                "tabla de par de origen dentada (zigzag entre pasos): la ficha no "
                "es reproducible y se acepta la tabla fiel al juego"
            )
        v(
            "engine_curve",
            ["ac:power.lut"],
            result,
            {
                "torque": fv["ir.declared.torque"],
                "power_hp": p_ref,
                "torque_rpm": fv.get("ir.declared.torque_peak_speed"),
                "power_rpm": fv.get("ir.declared.power_peak_speed"),
            },
            {
                "torque": round(peak_t, 3),
                "power_hp": round(p_calc, 3),
                "basis": "fila 100 (Automation)" if use_ref else "curva convertida",
                **(
                    {
                        "beamng_torque": round(fv["ir.engine.peak_torque"], 3),
                        "beamng_power_hp": round(fv["ir.engine.peak_power"] / HP_W, 3),
                    }
                    if use_ref
                    else {}
                ),
            },
            (
                f"par {t_mode} (exportador {fv.get('ir.formats.exporter_version')}): "
                + ("−0,5 ≤ Δ < 1" if t_mode == "truncado" else f"±{ROUNDING}")
                + f"; potencia ±{ROUNDING}; rpm de pico: empate en la meseta"
            ),
            "; ".join(rpm_notes),
        )
    if "ir.declared.drivetrain" in fv and "ir.drivetrain.layout" in fv:
        # Automation rotula «4WD» el 4x4 con reductora (muestra bisexual_aspiration) y
        # «AWD» el resto; el grafo del powertrain solo distingue tracción total
        total = {"AWD", "4WD"}
        ok = (
            fv["ir.declared.drivetrain"] == fv["ir.drivetrain.layout"]
            or {
                fv["ir.declared.drivetrain"],
                fv["ir.drivetrain.layout"],
            }
            <= total
        )
        v(
            "traction",
            ["ac:drivetrain.ini/TRACTION/TYPE"],
            "pass" if ok else "fail",
            fv["ir.declared.drivetrain"],
            fv["ir.drivetrain.layout"],
            "igualdad",
            "grafo del powertrain",
        )
    if "ir.mass.in_game_total" in fv and "ir.mass.total" in fv:
        # lo MEDIDO en el juego manda sobre la ficha (``engine._vehicle_validations``);
        # la diferencia esperada son los nodos de cámara que añade el juego (G2.13)
        game = fv["ir.mass.in_game_total"]
        diff = fv["ir.mass.total"] - game
        v(
            "mass_in_game",
            MASS_COVERS,
            "pass" if abs(diff) <= IN_GAME_MASS_TOL else "fail",
            round(game, 4),
            round(fv["ir.mass.total"], 4),
            IN_GAME_MASS_TOL,
            f"esperado: Σ obj:getNodeMass en BeamNG (banco de neumáticos); Δ = {diff:+.2f} kg",
            method="in_game_value",
            evidence=list(facts["ir.mass.in_game_total"].refs),
        )
    if "ir.declared.weight" in fv and "ir.mass.total" in fv:
        diff = fv["ir.mass.total"] - fv["ir.declared.weight"]
        result = "pass" if abs(diff) <= ROUNDING else "fail"
        note = f"Δ = {diff:+.2f} kg"
        if (
            result == "fail"
            and 0 < diff <= WEIGHT_UNDERREPORT_MAX
            and str(fv.get("ir.formats.exporter_version")) in WEIGHT_UNDERREPORTED
            and "ir.mass.in_game_total" not in fv
        ):
            # el Weight de la ficha de los 40340 queda por debajo de la masa de BeamNG
            # (G2.29: 6/6 coches medidos en el juego, Δ −0,48 kg con la masa de los nodos
            # y +1,7…+39 kg frente a la ficha): sin medida del juego no confirma ni
            # desmiente la lectura de los nodos
            result = "inconclusive"
            note += (
                "; el Weight de Automation (40340) no incluye toda la masa de BeamNG"
            )
        v(
            "mass",
            MASS_COVERS,  # identidad publicada (ver docstring, G2.8)
            result,
            fv["ir.declared.weight"],
            round(fv["ir.mass.total"], 2),
            ROUNDING,
            note,
        )
    return out
