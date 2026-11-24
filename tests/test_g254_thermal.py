from src.ac import thermal_templates as tt

TY = "[FRONT]\nRADIUS=0.3\n\n[REAR]\nRADIUS=0.3\n"


def _facts(kind="vented-disc", pad="sport", d=0.3135, m=5.9339):
    f = {}
    for axle in ("front", "rear"):
        for k, v in (
            ("disc_diameter", d),
            ("disc_mass", m),
            ("disc_type", kind),
            ("pad_material", pad),
        ):
            f[f"ir.brakes.axles.{axle}.{k}"] = {"value": v}
    return f


def test_tyres_inserta_wear_y_thermal():
    out = tt.apply_tyres(TY, "street")
    assert out.count("WEAR_CURVE=thermal_street_wear.lut") == 2
    assert "[THERMAL_FRONT]" in out and "[THERMAL_REAR]" in out
    assert tt.apply_tyres(out, "slick") == out  # no pisa
    assert "thermal_street_tcurve.lut" in tt.tyre_files("street")


def test_frenos_desde_el_coche_no_valores_fijos():
    big = tt.apply_brakes("[DATA]\nX=1\n", **tt.brake_params(_facts(m=12.0)))
    small = tt.apply_brakes("[DATA]\nX=1\n", **tt.brake_params(_facts(m=3.0)))
    assert big != small  # depende de la masa del disco del coche
    assert "[TEMPS_FRONT]" in big and "PERF_CURVE=(|0=" in big
    lo, hi = tt.COOL_TRANSFER_RANGE
    assert lo <= tt.cool_transfer(0.3135, 5.9339) <= hi
    # η real de wheels.lua: sport a 200 °C ≈ 1, semi-race a 850 °C ≈ 0,71
    assert tt.pad_efficiency("sport", 200) > 0.99
    assert abs(tt.pad_efficiency("semi-race", 850) - 0.71) < 0.02


def test_sin_datos_o_tambor_no_inventa():
    assert tt.brake_params({}) is None
    assert tt.brake_params(_facts(kind="drum")) is None
    assert tt.brake_params(_facts(pad="godmode")) is None
