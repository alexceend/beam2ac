"""Sistemas de coordenadas BeamNG y Assetto Corsa.

Confirmado con las muestras (docs/research/beamng_formato.md §3 y
assetto_formato.md §2):

========  ==========================  ==========================
Eje       BeamNG                      Assetto Corsa
========  ==========================  ==========================
+X        izquierda                   izquierda
+Y        atrás  (−Y = adelante)      arriba
+Z        arriba                      adelante
========  ==========================  ==========================

Evidencia: rueda FL de BeamNG en x = +0,737 y suspensión delantera en y = −1,200;
``WHEEL_LF`` del KN5 de AC en x = +0,840, z = +1,276.

Transformación (rotación pura de −90° alrededor de X, determinante +1, sin espejo)::

    AC(x, y, z) = (Bx, Bz, −By)          BeamNG(x, y, z) = (ACx, −ACz, ACy)

Solo cambia la orientación de los ejes; el ORIGEN no se traslada. Dónde poner el
origen del coche en AC (suelo, CdG…) es una hipótesis pendiente (fase 5/6).
"""

from __future__ import annotations

Vec3 = tuple[float, float, float]

# Matriz de rotación BeamNG -> AC (filas = ejes de AC expresados en BeamNG).
BEAMNG_TO_AC: tuple[Vec3, Vec3, Vec3] = (
    (1.0, 0.0, 0.0),
    (0.0, 0.0, 1.0),
    (0.0, -1.0, 0.0),
)


def beamng_to_ac(v: Vec3) -> Vec3:
    """Vector o punto de ejes BeamNG a ejes AC."""
    x, y, z = v
    return (x, z, -y)


def ac_to_beamng(v: Vec3) -> Vec3:
    """Inversa de :func:`beamng_to_ac`."""
    x, y, z = v
    return (x, -z, y)


def beamng_tensor_to_ac(t: tuple[Vec3, Vec3, Vec3]) -> tuple[Vec3, Vec3, Vec3]:
    """Tensor 3x3 (p. ej. de inercia): I_ac = R · I_beamng · Rᵀ."""
    r = BEAMNG_TO_AC
    out = [[0.0] * 3 for _ in range(3)]
    for i in range(3):
        for j in range(3):
            out[i][j] = sum(
                r[i][k] * t[k][m] * r[j][m] for k in range(3) for m in range(3)
            )
    return tuple(tuple(row) for row in out)  # type: ignore[return-value]
