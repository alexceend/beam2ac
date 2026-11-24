"""Protocolo del banco de neumáticos de BeamNG (G2.20; copia literal de ``protocol()``).

Segmentos (V en m/s, β en grados, κ adimensional, kload = fracción del peso añadida):
``tri`` = 0 → a → −a → 0 a velocidad constante. Usado por ``src.bench.tyre_runner``."""

from __future__ import annotations

V0 = 20.0
# s1b (piloto) usó ±15° y ±0,3; desde s2, ±20° y ±0,5 (más allá del máximo)
LAT = {"kind": "tri", "a": 20.0}
LON = {"kind": "tri", "a": 0.5}


def _sweeps(tag: str, V: float, kload: float = 0.0) -> list[dict]:
    return [
        {"name": f"rf_{tag}", "dur": 3, "V": V, "kload": kload, "rfree": True},
        {"name": f"lat_{tag}", "dur": 24, "V": V, "kload": kload, "beta": LAT},
        {"name": f"rf2_{tag}", "dur": 3, "V": V, "kload": kload, "rfree": True},
        {"name": f"lon_{tag}", "dur": 24, "V": V, "kload": kload, "kappa": LON},
    ]


def protocol(kind: str) -> list[dict]:
    s: list[dict] = [
        {
            "name": "accel",
            "dur": 9,
            "V": {"kind": "ramp", "from": 0, "to": V0},
            "record": False,
        },
        {"name": "settle", "dur": 4, "V": V0, "rfree": True},
        # rigidez vertical y radio libre frente a la carga (rodando, par 0)
        {
            "name": "load_steps",
            "dur": 12,
            "V": V0,
            "kload": {"kind": "steps", "values": [0, -0.35, 0.5, 1.0]},
        },
        {
            "name": "load_back",
            "dur": 2,
            "V": V0,
            "kload": {"kind": "ramp", "from": 1.0, "to": 0},
        },
        # rigidez y amortiguación verticales (excitación senoidal en el buje, registro a
        # 400 Hz)
        {
            "name": "vosc_3hz",
            "dur": 3,
            "V": V0,
            "vosc": {"a": 0.25, "f": 3.0},
            "win": 5,
        },
        {
            "name": "vosc_12hz",
            "dur": 2,
            "V": V0,
            "vosc": {"a": 0.25, "f": 12.0},
            "win": 5,
        },
    ]
    if kind == "humo":  # prueba de humo del Lua (no es una medida)
        return (
            s[:2]
            + [x for x in s if x["name"].startswith("vosc")]
            + [
                {
                    "name": "lat_humo",
                    "dur": 8,
                    "V": V0,
                    "beta": {"kind": "tri", "a": 6.0},
                },
                {
                    "name": "lon_humo",
                    "dur": 8,
                    "V": V0,
                    "kappa": {"kind": "tri", "a": 0.15},
                },
            ]
        )
    s += _sweeps("base", V0)
    if kind == "base":
        # s3: además, amortiguación vertical con segmentos largos (6 s), 8 y 12 Hz y dos
        # amplitudes (repetibilidad y dependencia de la amplitud; ver informe G2.20
        # §2.4)
        s += [
            {
                "name": "voscl_8hz",
                "dur": 6,
                "V": V0,
                "vosc": {"a": 0.25, "f": 8.0},
                "win": 5,
            },
            {
                "name": "voscl_12hz",
                "dur": 6,
                "V": V0,
                "vosc": {"a": 0.25, "f": 12.0},
                "win": 5,
            },
            {
                "name": "voscl_12hz_a12",
                "dur": 6,
                "V": V0,
                "vosc": {"a": 0.12, "f": 12.0},
                "win": 5,
            },
            {
                "name": "voscl_3hz",
                "dur": 6,
                "V": V0,
                "vosc": {"a": 0.25, "f": 3.0},
                "win": 5,
            },
        ]
        s += _sweeps("rep", V0)
        return s
    # deslizamiento combinado: β = 5° y barrido de κ
    s += [
        {
            "name": "comb_in",
            "dur": 3,
            "V": V0,
            "beta": {"kind": "ramp", "from": 0, "to": 5},
            "rfree": True,
        },
        {"name": "comb_b5", "dur": 24, "V": V0, "beta": 5, "kappa": LON},
        {
            "name": "comb_out",
            "dur": 3,
            "V": V0,
            "beta": {"kind": "ramp", "from": 5, "to": 0},
        },
    ]
    # carga
    for tag, k in (("kp", 0.6), ("km", -0.35)):
        s.append(
            {
                "name": f"{tag}_in",
                "dur": 2,
                "V": V0,
                "kload": {"kind": "ramp", "from": 0, "to": k},
            }
        )
        s += _sweeps(tag, V0, k)
        s.append(
            {
                "name": f"{tag}_out",
                "dur": 2,
                "V": V0,
                "kload": {"kind": "ramp", "from": k, "to": 0},
            }
        )
    # velocidad
    s.append({"name": "v10_in", "dur": 4, "V": {"kind": "ramp", "from": V0, "to": 10}})
    s += _sweeps("v10", 10.0)
    s.append({"name": "v30_in", "dur": 6, "V": {"kind": "ramp", "from": 10, "to": 30}})
    s += _sweeps("v30", 30.0)
    s.append({"name": "v20_in", "dur": 4, "V": {"kind": "ramp", "from": 30, "to": V0}})
    # presión (manométrica × pfac)
    for tag, pf in (("p08", 0.8), ("p12", 1.2)):
        s.append({"name": f"{tag}_in", "dur": 4, "V": V0, "pfac": pf, "rfree": True})
        s += [dict(x, pfac=pf) for x in _sweeps(tag, V0)]
    s.append({"name": "p10_in", "dur": 4, "V": V0, "pfac": 1.0, "rfree": True})
    # repetición del ensayo base al final de la sesión
    s += _sweeps("rep", V0)
    return s
