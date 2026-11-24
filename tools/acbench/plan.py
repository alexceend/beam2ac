"""G2.22: plan de pruebas determinista por variante (mismas maniobras que G2.21, automatizadas).

Maniobras (``protocolo.md`` de G2.21 §3): N = curva en punto muerto, D = curva con gas
constante en 2.ª (30/60/100 %), C = curva sin gas en marcha (retención), S = recta a fondo (A5).
Cada REPETICIÓN parte de una condición inicial restablecida (velocidad y radio estables) y es
un segmento con ``seg_uid`` propio: es la unidad de bloque independiente del análisis.

Desviaciones de G2.21 (documentadas en el informe): N a 32 km/h (40 km/h a 12 m son ≈ 1 g
laterales: en F1 el coche no estabilizó el radio); D desde 28 km/h y C hasta 25 km/h (por
debajo de ~2200 rpm el embrague automático de Custom AI desliza, F1); C en R = 25 m desde 42 km/h
(50 km/h en 12 m son ≈ 1,6 g: en F4 el coche se abría con el volante a tope); el radio de la curva N para el central es
7 m a 24 km/h (≈ 0,5 g; con 12 m el desfase cinemático entre ejes, ≈ v·L²/(2R²r), apenas supera 0,5 rad/s); las
repeticiones son cortas (la velocidad cae en punto muerto) y se cuentan como bloques; el número
de repeticiones lo fija ``target`` (bloques saturados) con un tope de intentos.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Item:
    key: str
    kind: str  # N, D, C, S
    direction: float  # +1/−1 (signo del volante); 0 en recta
    radius: float = 12.0
    v_start_kmh: float = 40.0
    gear: int = 2  # marcha de establecimiento (ac.StateCar.gear)
    gas: float = 0.0  # nivel de D
    v_end_kmh: float = 22.0  # fin de la ventana (N, C) por velocidad
    v_max_kmh: float = 75.0  # fin de la ventana (D) por velocidad
    t_max_s: float = 8.0
    target: int = 6  # repeticiones válidas con saturación en ``diffs``
    max_attempts: int = 14
    diffs: tuple[str, ...] = ("front", "rear")  # diferenciales que deben saturar


@dataclass
class VariantPlan:
    car_id: str
    items: list[Item] = field(default_factory=list)


def _both(base: dict) -> list[Item]:
    return [Item(direction=d, key=f"{base['key']}{'+' if d > 0 else '-'}", **{k: v for k, v in base.items() if k != "key"})
            for d in (1.0, -1.0)]


def neutral(diffs=("front", "rear"), radius=12.0, v=32.0, target=4, v_end=18.0) -> list[Item]:
    return _both(dict(key=f"N{radius:g}", kind="N", radius=radius, v_start_kmh=v, gear=2, v_end_kmh=v_end,
                      t_max_s=10.0, target=target, diffs=diffs))


def drive(level: float, target=3, diffs=("front", "rear")) -> list[Item]:
    return _both(dict(key=f"D{int(level * 100)}", kind="D", radius=12.0, v_start_kmh=28.0, gear=2, gas=level,
                      v_max_kmh=60.0, t_max_s=8.0, target=target, diffs=diffs))


def drive_tight(level: float, target=4, diffs=("centre",)) -> list[Item]:
    """D en curva cerrada (R = 7 m) para el central: en R = 12 m el central con POWER no satura (F4)."""
    return _both(dict(key=f"D{int(level * 100)}R7", kind="D", radius=7.0, v_start_kmh=22.0, gear=2, gas=level,
                      v_max_kmh=40.0, t_max_s=6.0, target=target, max_attempts=8, diffs=diffs))


def coast(target=4, diffs=("front", "rear"), gear=2) -> list[Item]:
    return _both(dict(key=f"C{gear}", kind="C", radius=25.0, v_start_kmh=42.0, gear=gear, v_end_kmh=25.0,
                      t_max_s=8.0, target=target, diffs=diffs))


def straight(target=3) -> list[Item]:
    # desde 20 km/h en 1.ª (≈ 3300 rpm): desde 8 km/h el embrague automático deslizaba (F4, A5)
    return [Item(key="S", kind="S", direction=0.0, radius=1e9, v_start_kmh=20.0, gear=1, gas=1.0, v_max_kmh=70.0,
                 t_max_s=4.0, target=target, diffs=("front", "rear"))]


def coast_tight(target=4, diffs=("centre",)) -> list[Item]:
    """C en curva cerrada y en 1.ª para el central (en R = 25 m no satura, F4)."""
    return _both(dict(key="C1R7", kind="C", radius=7.0, v_start_kmh=24.0, gear=1, v_end_kmh=13.0, t_max_s=6.0,
                      target=target, max_attempts=8, diffs=diffs))


# Suplemento (F4 r3): central con POWER y COAST en curva cerrada
EXTRA: dict[str, list[Item]] = {
    "beam2ac_g221lsd_a2": drive_tight(0.3) + drive_tight(0.6),
    "beam2ac_g221lsd_a3": coast_tight(),
}

PLANS: dict[str, list[Item]] = {
    "beam2ac_g221lsd_a0": neutral(target=2) + drive(0.3, target=2) + coast(target=2),
    "beam2ac_g221lsd_a1": neutral(target=4) + neutral(("centre",), radius=7.0, v=24.0, target=4, v_end=12.0),
    "beam2ac_g221lsd_a2": drive(0.3) + drive(0.6) + drive(1.0, target=2) + drive(0.6, diffs=("centre",)),
    "beam2ac_g221lsd_a3": coast(target=4) + coast(target=3, diffs=("centre",)),
    "beam2ac_g221lsd_a4": drive(0.3) + drive(0.6) + drive(1.0, target=2),
    "beam2ac_g221lsd_a5": neutral(target=3) + drive(0.6, target=3) + coast(target=3) + straight(),
    "beam2ac_g221lsd_b0": neutral(("axle2wd",), target=4) + drive(0.6, diffs=("axle2wd",)) + coast(diffs=("axle2wd",)),
}


def plan_for(car_id: str, scale: float = 1.0, extra: bool = False) -> VariantPlan:
    """``scale`` < 1 reduce los objetivos (pruebas cortas de F1–F3); nunca por debajo de 1.
    ``extra``: solo el suplemento ``EXTRA``."""
    src = EXTRA.get(car_id, []) if extra else PLANS[car_id]
    items = [Item(**{**it.__dict__, "target": max(1, round(it.target * scale)),
                     "max_attempts": max(2, round(it.max_attempts * scale))}) for it in src]
    return VariantPlan(car_id, items)
