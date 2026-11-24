"""G2.25 (auditoría de r01): clasificación de cada ensayo y política de reintentos del banco.

Lógica pura (sin AC), usada por :mod:`tools.acbench.validate` y probada en
``tests/test_g225_policy.py``.

Resultado de un ensayo (``classify``), por orden de prioridad:

=====================  =================================================================================
``valid_trial``        sin motivos
``telemetry_failure``  la medida no es fiable: telemetría parada, vigilante de Lua, acción no confirmada
``controller_failure`` el banco no llevó el coche a la condición pedida: no recoloca (posición o rumbo),
                       no arranca, no entra la marcha, no se establece, gas no aplicado, embrague que no
                       abre, tiempo máximo, fuera de la explanada sin trompo
``track_failure``      contacto o pérdida de carga con el coche a ≤ ``TRACK_MARGIN_M`` de un elemento de
                       la pista (muro) o fuera de la explanada
``vehicle_failure``    lo demás que describe al coche: trompo, se mueve parado con el freno, rueda sin carga
                       o carrocería contra el suelo lejos de todo elemento, estado no finito
``invalid_trial``      motivo sin clasificar (p. ej. ventana corta)
=====================  =================================================================================

Reintentos (``RetryPolicy.decide``): un fallo externo (telemetría, controlador, pista) solo se reintenta si
hay una condición nueva (motivos distintos del intento anterior o una recuperación que haya funcionado) y
como mucho ``external_retries`` veces; un fallo del coche se repite UNA vez para confirmar que se
reproduce y entonces se da por resultado (no se insiste). La sesión se aborta si, con al menos
``abort_min_trials`` ensayos, la fracción de inválidos supera ``abort_invalid_rate``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

VALID = "valid_trial"
TELEMETRY = "telemetry_failure"
CONTROLLER = "controller_failure"
TRACK = "track_failure"
VEHICLE = "vehicle_failure"
INVALID = "invalid_trial"
EXTERNAL = (TELEMETRY, CONTROLLER, TRACK, INVALID)

TRACK_MARGIN_M = 6.0

TELEMETRY_REASONS = {"telemetria_parada", "vigilante_lua", "accion_no_confirmada"}
CONTROLLER_REASONS = {"no_recoloca", "no_arranca", "no_entra_1a", "no_entra_marcha", "no_entra_punto_muerto",
                      "no_establece", "gas_no_aplicado", "embrague_no_abre", "sin_contacto",
                      "no_esta_en_punto_muerto", "freno"}
VEHICLE_REASONS = {"trompo", "se_mueve", "perdida_de_contacto", "colision_carroceria_pista", "lectura_invalida",
                   "colision_coche"}
CONTACT_REASONS = {"perdida_de_contacto", "colision_carroceria_pista"}


def box_distance(x: float, z: float, lo: tuple, hi: tuple) -> float:
    """Distancia horizontal (m) del punto (x, z) a una caja (0 dentro)."""
    return math.hypot(max(lo[0] - x, 0.0, x - hi[0]), max(lo[2] - z, 0.0, z - hi[2]))


def near_track_element(pos: list[float] | None, elements: dict, pad_half: float,
                       margin: float = TRACK_MARGIN_M) -> str | None:
    """Nombre del elemento de pista a ≤ ``margin`` del punto, ``'borde'`` fuera de la explanada."""
    if pos is None or not all(math.isfinite(v) for v in (pos[0], pos[2])):
        return None
    if max(abs(pos[0]), abs(pos[2])) > pad_half:
        return "borde"
    for name, (lo, hi) in elements.items():
        if box_distance(pos[0], pos[2], lo, hi) <= margin:
            return name
    return None


def classify(reasons: list[str], pos_at_failure: list[float] | None, elements: dict, pad_half: float) -> tuple[str, str]:
    """(resultado, explicación) de un ensayo a partir de sus motivos y de la posición del coche en el
    momento del primer motivo."""
    r = {x.split(":")[0] for x in reasons}
    if not r:
        return VALID, "sin motivos"
    if r & TELEMETRY_REASONS or any(x.startswith("excepcion") for x in reasons):
        return TELEMETRY, f"medida no fiable: {sorted(r & TELEMETRY_REASONS) or 'excepción del banco'}"
    near = near_track_element(pos_at_failure, elements, pad_half)
    if r & CONTACT_REASONS and near:
        return TRACK, f"{sorted(r & CONTACT_REASONS)} a ≤ {TRACK_MARGIN_M} m de {near}"
    tmax = {x for x in r if x.startswith("tiempo_maximo")}
    if r & CONTROLLER_REASONS or tmax or (("fuera_de_explanada" in r) and "trompo" not in r):
        return CONTROLLER, f"condición no alcanzada: {sorted((r & CONTROLLER_REASONS) | tmax | (r & {'fuera_de_explanada'}))}"
    if "fuera_de_explanada" in r and near == "borde":
        return TRACK, "fuera de la explanada"
    if r & VEHICLE_REASONS:
        return VEHICLE, f"comportamiento del coche: {sorted(r & VEHICLE_REASONS)}"
    return INVALID, f"sin clasificar: {sorted(r)}"


@dataclass
class RetryPolicy:
    reps: int = 3
    external_retries: int = 2
    vehicle_confirmations: int = 1
    abort_invalid_rate: float = 0.5
    abort_min_trials: int = 6
    # estado de la maniobra actual
    good: int = 0
    attempts: int = 0
    external: int = 0
    vehicle: int = 0
    last_reasons: tuple = ()
    # estado de la sesión
    session_trials: int = 0
    session_invalid: int = 0
    history: list = field(default_factory=list)

    def start_maneuver(self) -> None:
        self.good = self.attempts = self.external = self.vehicle = 0
        self.last_reasons = ()

    def decide(self, outcome: str, reasons: list[str], recovered: bool = False) -> str:
        """Tras un ensayo: ``'otra'`` (siguiente repetición o reintento), ``'fin'`` (maniobra completa o
        sin sentido seguir) o ``'abortar'`` (sesión)."""
        self.attempts += 1
        self.session_trials += 1
        key = tuple(sorted(reasons))
        if outcome != VALID:
            self.session_invalid += 1
        decision, why = "otra", ""
        if outcome == VALID:
            self.good += 1
            self.vehicle = 0
            if self.good >= self.reps:
                decision, why = "fin", "repeticiones completas"
        elif outcome == VEHICLE:
            self.vehicle += 1
            if self.vehicle > self.vehicle_confirmations:
                decision, why = "fin", "fallo del coche reproducido"
        else:
            self.external += 1
            if self.external > self.external_retries:
                decision, why = "fin", "reintentos externos agotados"
            elif key == self.last_reasons and not recovered:
                decision, why = "fin", "mismo fallo externo sin condición nueva"
        self.last_reasons = key
        if self.attempts >= self.reps + self.external_retries + self.vehicle_confirmations and decision == "otra":
            decision, why = "fin", "tope de intentos"
        if (self.session_trials >= self.abort_min_trials
                and self.session_invalid / self.session_trials > self.abort_invalid_rate):
            decision, why = "abortar", (f"tasa de inválidos {self.session_invalid}/{self.session_trials} > "
                                        f"{self.abort_invalid_rate}")
        self.history.append({"outcome": outcome, "reasons": list(reasons), "decision": decision, "why": why})
        return decision
