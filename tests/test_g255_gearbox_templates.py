"""Plantillas de caja del colaborador (heurística) y térmica por defecto."""

import re

import pytest

from src.ac import gearbox_templates as gt
from src.ac import thermal_templates as tt

ENG = gt.EngineInputs(idle_rpm=900.0, limiter_rpm=7000.0, peak_torque_nm=400.0)
BASE = "[GEARS]\nCOUNT=5\n\n[GEARBOX]\nVALID_SHIFT_RPM_WINDOW=800\n\n[CLUTCH]\nMAX_TORQUE=1\n"


def _get(text, sec, key):
    cur = ""
    for line in text.splitlines():
        if line.startswith("["):
            cur = line.strip("[]")
        elif cur == sec and line.startswith(key + "="):
            return line.split("=", 1)[1]
    return None


def test_manual_valores_y_reglas():
    dt, ai = gt.apply_gearbox(BASE, None, "manualGearbox", ENG)
    assert _get(dt, "CLUTCH", "MAX_TORQUE") == "500"  # par + 100
    assert _get(dt, "AUTOCLUTCH", "MIN_RPM") == "900"
    assert _get(dt, "AUTOCLUTCH", "MAX_RPM") == "1750"
    assert _get(dt, "GEARBOX", "SUPPORTS_SHIFTER") == "1"
    assert _get(dt, "GEARBOX", "VALID_SHIFT_RPM_WINDOW") == "800"  # conserva
    assert _get(dt, "AUTO_SHIFTER", "UP") == "6800"
    assert _get(dt, "AUTO_SHIFTER", "DOWN") == "3850"
    assert _get(ai, "GEARS", "UP") == "6800" and _get(ai, "GEARS", "DOWN") == "3850"
    assert "[DAMAGE]" not in dt


def test_automatica_y_dct():
    dt, _ = gt.apply_gearbox(BASE, None, "automaticGearbox", ENG)
    assert _get(dt, "AUTOCLUTCH", "MIN_RPM") == "1150"  # ralentí + 250
    assert _get(dt, "DAMAGE", "RPM_WINDOW_K") == "100"
    assert _get(dt, "AUTO_SHIFTER", "SLIP_THRESHOLD") == "1.00"
    dct, _ = gt.apply_gearbox(BASE, None, "dctGearbox", ENG)
    assert _get(dct, "GEARBOX", "CHANGE_UP_TIME") == "40"
    p = [int(_get(dct, "AUTOBLIP", f"POINT_{i}")) for i in range(3)]
    assert p == sorted(p)  # defecto de la plantilla original corregido


def test_idempotente():
    a, ai = gt.apply_gearbox(BASE, None, "dctGearbox", ENG)
    b, ai2 = gt.apply_gearbox(a, ai, "dctGearbox", ENG)
    assert a == b and ai == ai2


def test_engine_inputs():
    ini = "[ENGINE_DATA]\nMINIMUM=900\nLIMITER=7000\n"
    e = gt.engine_inputs(ini, "0|100\n3000|420\n7000|380\n")
    assert e == gt.EngineInputs(900.0, 7000.0, 420.0)
    assert gt.engine_inputs("[ENGINE_DATA]\n", "0|1\n") is None


def test_termica_por_defecto_y_lut_validas():
    assert tt.DEFAULT_TYRES == "colab" and tt.DEFAULT_BRAKES == "auto"
    for kind in tt.TYRE_KINDS:
        for name, body in tt.tyre_files(kind).items():
            xs = [float(re.split(r"\|", ln)[0]) for ln in body.split()]
            assert xs == sorted(set(xs)), f"{kind}/{name}: x no estrictamente creciente"
    out = tt.apply_tyres("[FRONT]\nR=1\n\n[REAR]\nR=1\n", "colab")
    assert "GRAIN_GAIN=0.4" in out and out.count("[THERMAL_") == 2


def test_notas_de_uso_y_avisos():
    from src.project import usage_notes as un

    assert len(un.NOTES) == 9
    facts = {
        "ir.suspension.axles.front.linkage": {"value": "rigid_axle"},
        "ir.drivetrain.differentials.rear.kind": {"value": "locked"},
        "ir.drivetrain.differentials.front.kind": {"value": "lsd"},
    }
    w = un.auto_warnings(facts)
    assert len(w) == 2 and "rear" in w[1]
    assert un.auto_warnings({}) == []
    assert any("## Notas de uso" in x for x in un.report_lines(facts))


def test_deposito_item_y_posicion_por_defecto():
    from src.app.workspace import _default_fuel_tank
    from src.project import cockpit_edits as ce

    assert ce.clean({"fuel_tank": [0, 0.3, -1.2]}) == {"fuel_tank": [0.0, 0.3, -1.2]}
    wheels = {
        "WHEEL_LF": [0.8, 0.3, 1.4],
        "WHEEL_RF": [-0.8, 0.3, 1.4],
        "WHEEL_LR": [0.8, 0.3, -1.2],
        "WHEEL_RR": [-0.8, 0.3, -1.2],
    }
    assert _default_fuel_tank(wheels) == [0.0, 0.3, -0.29]
    assert _default_fuel_tank({}) == [0.0, 0.3, 0.0]
    out = gt.set_ini_keys(
        "[BASIC]\nX=1\n", "FUELTANK", {"POSITION": "0.000,0.300,-1.200"}
    )
    assert out.endswith("[FUELTANK]\nPOSITION=0.000,0.300,-1.200\n")


def test_decision_del_usuario_sobrevive_y_se_registran_sustituciones(tmp_path):
    import json

    from src.project import export as E

    (tmp_path / "report.json").write_text(
        json.dumps(
            {"facts": {"ir.drivetrain.gearbox.kind": {"value": "manualGearbox"}}}
        )
    )
    dt = "[GEARS]\nGEAR_1=3.5\nGEAR_2=2.0\n\n[CLUTCH]\nMAX_TORQUE=999\n"
    texts = {
        "drivetrain.ini": dt,
        "engine.ini": "[ENGINE_DATA]\nMINIMUM=900\nLIMITER=7000\n",
        "power.lut": "0|100\n4000|400\n",
    }
    prov = {"stage": "resolved_ini", "stage_key": "k", "keys": []}
    files = [E.PackageFile("data/drivetrain.ini", dt.encode(), prov)]
    issues: list = []
    keys = [{"target": "ac:drivetrain.ini/CLUTCH/MAX_TORQUE", "decision": "d1"}]
    E._apply_gearbox_template(files, texts, issues, tmp_path, {}, keys)
    assert _get(texts["drivetrain.ini"], "CLUTCH", "MAX_TORQUE") == "999"  # decisión
    assert _get(texts["drivetrain.ini"], "GEARBOX", "CHANGE_UP_TIME") == "280"
    meta = next(f for f in files if f.rel == "data/drivetrain.ini").provenance
    assert meta["stage"] == "resolved_ini"
    assert meta["gearbox_template_kept_decisions"] == ["CLUTCH/MAX_TORQUE"]
    # sin decisión: se sustituye y queda el valor anterior
    texts["drivetrain.ini"] = dt
    files[:] = [E.PackageFile("data/drivetrain.ini", dt.encode(), prov)]
    E._apply_gearbox_template(files, texts, [], tmp_path, {}, [])
    assert _get(texts["drivetrain.ini"], "CLUTCH", "MAX_TORQUE") == "500"
    meta = next(f for f in files if f.rel == "data/drivetrain.ini").provenance
    assert meta["gearbox_template_overrides"] == {"CLUTCH/MAX_TORQUE": "999"}


def test_oscilacion_de_cambio():
    dt = "[GEARS]\nGEAR_1=4.0\nGEAR_2=1.5\nGEAR_3=1.2\n"
    # 6800·1,5/4,0 = 2550 < 3850 → subida 1→2 oscila (marcha 1); 6800·1,2/1,5 = 5440 → 2→3 bien
    assert gt.shift_hunting(dt, 6800, 3850) == [1]
    assert gt.shift_hunting("[GEARS]\nGEAR_1=3\n", 6800, 3850) == []


def test_deposito_se_pasa_a_marco_fisico():
    from src.project import export as E

    car = "[BASIC]\nGRAPHICS_OFFSET=0,-0.55,-0.155\nGRAPHICS_PITCH_ROTATION=0\n"
    issues: list = []
    out, ok = E._place_fuel_tank(car, [0.0, 0.4, -1.3], issues)
    assert ok and "POSITION=0.000,-0.150,-1.455" in out and not issues
    out, ok = E._place_fuel_tank("[BASIC]\n", [0.0, 0.4, -1.3], issues)
    assert not ok and issues and "[FUELTANK]" not in out


def test_cristal_translucido_pasa_a_alphablend(tmp_path):
    import json

    from src.ac import kn5_materials as km

    mats = {
        "m_glass": {
            "translucent": True,
            "alphaTest": True,
            "Stages": [{"diffuseColor": [0, 0.06, 0.05, 0.9]}],
        },
        "m_grille": {"alphaTest": True, "Stages": [{"diffuseColor": [1, 1, 1, 1]}]},
        "m_body": {"Stages": [{"diffuseColor": [1, 0, 0, 1]}]},
    }
    p = tmp_path / "x.materials.json"
    p.write_text(json.dumps(mats))
    src = {
        s.material: s
        for s in km.diffuse_sources(
            ["m_glass", "m_grille", "m_body"],
            p,
            tmp_path,
            None,
            translucent_glass=True,
        )
    }
    assert (src["m_glass"].blend, src["m_glass"].alpha_tested) == (1, 0)
    assert src["m_glass"].texture_name.endswith("_a%02x.dds" % round(0.9 * 255))
    assert src["m_glass"].data[-1] == round(0.9 * 255)  # alfa del color plano
    assert (src["m_grille"].blend, src["m_grille"].alpha_tested) == (
        0,
        0,
    )  # alphaTest no se traslada
    assert (src["m_body"].blend, src["m_body"].alpha_tested) == (0, 0)
    assert src["m_body"].texture_name == "beam2ac_col_ff0000.dds"


def test_down_se_acota_para_no_oscilar():
    dt = "[GEARS]\nGEAR_1=4.0\nGEAR_2=1.5\nGEAR_3=1.2\n"
    out, _ = gt.apply_gearbox(dt, None, "automaticGearbox", ENG)
    down = float(_get(out, "AUTO_SHIFTER", "DOWN"))
    assert down == 2422 and down == int(
        down
    )  # 2422,5 → entero: acotado (0,55·7000 = 3850)
    assert gt.shift_hunting(out, 6800, down) == []
    out2, _ = gt.apply_gearbox(
        BASE, None, "automaticGearbox", ENG
    )  # sin marchas: sin acotar
    assert _get(out2, "AUTO_SHIFTER", "DOWN") == "3850"


def test_vidrio_de_luz_no_se_hace_translucido(tmp_path):
    import json

    from src.ac import kn5_materials as km

    mats = {
        "lens": {"translucent": True, "Stages": [{"diffuseColor": [1, 1, 1, 0.5]}]},
        "win": {"translucent": True, "Stages": [{"diffuseColor": [0, 0, 0, 0.5]}]},
    }
    p = tmp_path / "x.materials.json"
    p.write_text(json.dumps(mats))
    src = {
        s.material: s
        for s in km.diffuse_sources(
            ["lens", "win"],
            p,
            tmp_path,
            None,
            opaque_materials={"lens"},
            translucent_glass=True,
        )
    }
    assert src["lens"].blend == 0 and src["lens"].data[-1] == 255
    assert src["win"].blend == 1 and src["win"].data[-1] == 128


def test_sombras_generadas():
    from src.ac import shadows as S

    f = S.shadow_files(2.4)
    assert f["data/ambient_shadows.ini"] == b"[SETTINGS]\nWIDTH=1.0\nLENGTH=2.80\n"
    assert set(f) == {
        "data/ambient_shadows.ini",
        "body_shadow.png",
        *(f"tyre_{n}_shadow.png" for n in range(4)),
    }
    for k, v in f.items():
        if k.endswith(".png"):
            assert v[:8] == b"\x89PNG\r\n\x1a\n" and v[-12:-8] == b"\x00\x00\x00\x00"


def test_up_down_protegidos_mandan_en_ai_y_en_el_tope():
    dt = "[GEARS]\nGEAR_1=4.0\nGEAR_2=1.5\n\n[AUTO_SHIFTER]\nUP=6000\nDOWN=5000\n"
    prot = frozenset({("AUTO_SHIFTER", "UP"), ("AUTO_SHIFTER", "DOWN")})
    out, ai = gt.apply_gearbox(dt, None, "automaticGearbox", ENG, prot)
    assert _get(out, "AUTO_SHIFTER", "UP") == "6000"
    assert _get(ai, "GEARS", "UP") == "6000"
    assert _get(out, "AUTO_SHIFTER", "DOWN") == "5000"
    assert _get(ai, "GEARS", "DOWN") == "5000"
    dt2 = "[GEARS]\nGEAR_1=10\nGEAR_2=1\n"
    out2, _ = gt.apply_gearbox(dt2, None, "automaticGearbox", ENG)
    assert float(_get(out2, "AUTO_SHIFTER", "DOWN")) >= ENG.idle_rpm


def test_fixtureglass_nunca_translucido(tmp_path):
    import json

    from src.ac import kn5_materials as km

    mats = {
        "m_fixtureglass_headlight_1": {
            "translucent": True,
            "Stages": [{"diffuseColor": [1, 1, 1, 0.07]}],
        }
    }
    p = tmp_path / "x.materials.json"
    p.write_text(json.dumps(mats))
    s = km.diffuse_sources(["m_fixtureglass_headlight_1"], p, tmp_path, None)[0]
    assert s.blend == 0 and s.data[-1] == 255


def test_cristal_opaco_por_defecto(tmp_path):
    import json

    from src.ac import kn5_materials as km

    mats = {
        "w": {"translucent": True, "Stages": [{"diffuseColor": [0, 0.06, 0.05, 0.098]}]}
    }
    p = tmp_path / "x.materials.json"
    p.write_text(json.dumps(mats))
    s = km.diffuse_sources(["w"], p, tmp_path, None)[0]
    assert (
        s.blend == 0 and s.data[-1] == 255
    )  # alfa 0,098 dejaba el habitáculo invisible


def test_llamas_desde_vigas_de_escape():
    from src.ac import flames as F

    pos = {
        "g0": (-0.1, 0.9, 0.8),
        "e1": (-0.2, 1.2, 0.7),
        "e2": (-0.3, 1.47, 0.62),
        "g1": (0.1, 0.9, 0.8),
        "f1": (0.2, 1.2, 0.7),
        "f2": (0.3, 1.47, 0.62),
        "otro": (0.0, 0.0, 0.0),
    }
    beams = [("g0", "e1"), ("e1", "e2"), ("g1", "f1"), ("f1", "f2")]
    out = F.exhaust_outlets(pos, beams)
    assert sorted(p[0] for p, _ in out) == [
        -0.3,
        0.3,
    ]  # dos salidas traseras, no los puertos del motor
    dy = F.vertical_shift({"LF": (0.5, 0.257, 1.07)}, {"FL": (0.5, -1.07, 0.72)})
    assert abs(dy - (0.257 - 0.72)) < 1e-9
    text, info = F.render_flames_ini(out, dy)
    assert text.count("[FLAME_") == 2 and "IS_LEFT=1" in text and "IS_LEFT=0" in text
    assert "POSITION=0.300,0.157,-1.470" in text


def test_reflejos_heuristicos():
    from src.ac import kn5_materials as km

    sh, pr = km.specular_look(
        {"instanceDiffuse": True, "roughnessFactor": 1, "metallicFactor": 1}
    )
    d = dict(pr)
    assert (
        sh == "ksPerPixelReflection"
        and d["ksSpecular"] == 1.0
        and d["fresnelMaxLevel"] == 0.6
    )
    sh, pr = km.specular_look(
        {"roughnessFactor": 0.02, "metallicFactor": 1}
    )  # cristal/cromo
    assert sh == "ksPerPixelReflection" and dict(pr)["ksSpecularEXP"] > 400
    sh, pr = km.specular_look(
        {"roughnessFactor": 1, "metallicFactor": 0}
    )  # goma/plástico mate
    assert sh == "" and dict(pr)["ksSpecular"] == 0.02
    assert km.specular_look({"roughnessFactor": 0.02}, glossy_lens=True) == ("", ())


def test_apply_diffuse_cambia_shader_y_props():
    from src.ac import kn5_materials as km

    m = km.Kn5Material(
        "a", "ksPerPixel", 0, 0, 0, [("ksDiffuse", (0.4,) + (0.0,) * 9)], []
    )
    parts = km.Kn5Parts(5, b"", [], [m], b"")
    src = km.DiffuseSource(
        "a",
        "a",
        "diffuseColor",
        "t.dds",
        b"x",
        shader="ksPerPixelReflection",
        props=(("ksSpecular", 1.0), ("fresnelMaxLevel", 0.6)),
    )
    out = km.apply_diffuse(parts, [src]).materials[0]
    names = {n: v[0] for n, v in out.props}
    assert out.shader == "ksPerPixelReflection"
    assert (
        names["ksDiffuse"] == 0.4
        and names["ksSpecular"] == 1.0
        and names["ksEmissive"] == 0.0
    )
    assert out.slots == [("txDiffuse", 0, "t.dds")]


def test_flame_presets():
    from src.ac import flames as F

    t = F.render_flame_presets_ini()
    for kind in ("START", "END", "LOOP", "FLASH"):
        assert f"[PRESET_{kind}_0]" in t
    assert t.count("RGB=") == 11 and t.startswith("[HEADER]\nSIZE_MULT=1")


def test_mapas_normales_opcionales(tmp_path):
    import json

    from src.ac import kn5_materials as km

    dds = b"DDS " + b"x" * 60
    (tmp_path / "rock_n.dds").write_bytes(dds)
    mats = {
        "rock": {"Stages": [{"normalMap": "rock_n.dds", "roughnessFactor": 1}]},
        "flat": {
            "Stages": [{"normalMap": "camso_col_0xff8080ff.dds", "roughnessFactor": 1}]
        },
        "paint": {
            "Stages": [
                {
                    "normalMap": "rock_n.dds",
                    "instanceDiffuse": True,
                    "roughnessFactor": 1,
                }
            ]
        },
    }
    p = tmp_path / "x.materials.json"
    p.write_text(json.dumps(mats))
    names = ["rock", "flat", "paint"]
    off = {s.material: s for s in km.diffuse_sources(names, p, tmp_path, (1, 1, 1))}
    assert all(not s.normal_name for s in off.values())  # por defecto desactivado
    on = {
        s.material: s
        for s in km.diffuse_sources(names, p, tmp_path, (1, 1, 1), normal_maps=True)
    }
    assert (
        on["rock"].normal_name == "rock_n.dds" and on["rock"].shader == "ksPerPixelNM"
    )
    assert not on["flat"].normal_name  # normal plana virtual
    assert not on["paint"].normal_name and on["paint"].shader == "ksPerPixelReflection"
    m = km.Kn5Material("rock", "ksPerPixel", 0, 0, 0, [], [])
    out = km.apply_diffuse(km.Kn5Parts(5, b"", [], [m], b""), [on["rock"]])
    assert ("txNormal", 1, "rock_n.dds") in out.materials[0].slots
    assert out.materials[0].shader == "ksPerPixelNM"


def test_backfire_valores_y_hook():
    from src.ac import backfire as bf
    from src.project import export as E

    v = bf.backfire_values(6500)
    assert v["MINRPM"] == "4225" and v["MAXRPM"] == "12000" and v["MAXGAS"] == "0.28"
    assert bf.backfire_values(6500, max_gas=0.9)["MAXGAS"] == "0.3"  # AC acota a 0,3
    try:
        bf.backfire_values(6500, min_rpm=9000, max_rpm=8000)
    except ValueError:
        pass
    else:
        raise AssertionError("rango inválido")
    files = [E.PackageFile("data/flames.ini", b"x", {})]
    issues: list = []
    E._apply_backfire(
        {}, files, {"engine.ini": "[ENGINE_DATA]\nLIMITER=6500\n"}, issues
    )
    s = next(f for f in files if f.rel == "data/sounds.ini").data.decode()
    assert (
        "[BACKFIRE]" in s and "MINRPM=4225" in s and issues[0].code == "llamas.backfire"
    )
    # desactivado, o sin flames.ini: no escribe nada
    f2 = [E.PackageFile("data/flames.ini", b"x", {})]
    E._apply_backfire({"backfire": False}, f2, {}, [])
    assert [f.rel for f in f2] == ["data/flames.ini"]
    f3: list = []
    E._apply_backfire({}, f3, {"engine.ini": "[ENGINE_DATA]\nLIMITER=6500\n"}, [])
    assert f3 == []
    # con sounds.ini previo conserva [ENGINE]
    f4 = [
        E.PackageFile("data/flames.ini", b"x", {}),
        E.PackageFile("data/sounds.ini", b"[ENGINE]\nPOSITION=front\n", {}),
    ]
    E._apply_backfire(
        {"backfire_params": {"min_rpm": 3000}},
        f4,
        {"engine.ini": "[ENGINE_DATA]\nLIMITER=6500\n"},
        [],
    )
    s4 = next(f for f in f4 if f.rel == "data/sounds.ini").data.decode()
    assert "POSITION=front" in s4 and "MINRPM=3000" in s4


def test_app_parse_backfire_y_opciones_de_modelo():
    from src.app import workspace as W

    assert W._parse_backfire({}) == {}
    assert W._parse_backfire({"backfire": False}) == {"backfire": False}
    assert W._parse_backfire({"backfire_min_rpm": 4000, "backfire_max_gas": ""}) == {
        "backfire_params": {"min_rpm": 4000.0}
    }
    for bad in ({"backfire_min_rpm": -1}, {"backfire_max_rpm": True}):
        try:
            W._parse_backfire(bad)
        except W.WorkspaceError:
            pass
        else:
            raise AssertionError(bad)
    assert W._parse_model_options({"abs": True}) is None
    anim = {
        "kind": "wiper",
        "node": "WIPER1",
        "pivot": [0, 0, 0],
        "axis": [0, 0, 1],
        "angle_deg": 30,
    }
    ok = {"normal_maps": True, "part_animations": [anim]}
    assert W._parse_model_options(ok)["part_animations"][0]["node"] == "WIPER1"
    try:
        W._parse_model_options({"part_animations": [{"kind": "puerta", "node": "x"}]})
    except W.WorkspaceError:
        pass
    else:
        raise AssertionError("tipo inválido")


def test_animaciones_de_piezas(tmp_path):
    import glob

    import pytest

    from src.ac import part_anim as P
    from src.ac.steer_anim import read_ksanim

    from src.project.export import default_ac_root

    root = default_ac_root()
    ae86 = (
        [
            k
            for k in glob.glob(str(root / "content/cars/ks_toyota_ae86/*.kn5"))
            if "collider" not in k
        ]
        if root
        else []
    )
    if not ae86:
        pytest.skip("sin AE86 de Kunos instalado")
    out = P.build_files(
        ae86[0],
        [
            {
                "kind": "wiper",
                "node": "WIPER1",
                "pivot": [0.012, 0.82, 0.874],
                "axis": [0, 0, 1],
                "angle_deg": 40,
            }
        ],
    )
    ver, bones = read_ksanim(out["car_wiper.ksanim"])
    assert ver == 2 and bones[0][0] == "WIPER1" and len(bones[0][1]) == 100
    first, mid = bones[0][1][0], bones[0][1][50]
    assert max(abs(a - b) for a, b in zip(first, mid)) > 0.01  # se mueve
    with pytest.raises(P.PartAnimError):
        P.build_files(
            ae86[0],
            [
                {
                    "kind": "door_L",
                    "node": "NO_EXISTE",
                    "pivot": [0, 0, 0],
                    "axis": [0, 1, 0],
                    "angle_deg": 30,
                }
            ],
        )


def test_endurecimiento_backfire_animaciones_y_rutas(tmp_path):
    import json

    from src.ac import backfire as bf
    from src.ac import kn5_materials as km
    from src.app import workspace as W
    from src.project import model as M

    for bad in (
        dict(max_rpm=float("inf")),
        dict(max_gas=float("nan")),
        dict(max_gas=-1.0),
        dict(max_gas=0.0),
    ):
        try:
            bf.backfire_values(7000.0, **bad)
        except ValueError:
            pass
        else:
            raise AssertionError(bad)
    try:
        W._parse_backfire({"backfire_max_rpm": float("inf")})
    except W.WorkspaceError:
        pass
    else:
        raise AssertionError("inf")
    ok = {
        "kind": "wiper",
        "node": "W",
        "pivot": [0, 0, 0],
        "axis": [0, 0, 1],
        "angle_deg": 30,
    }
    assert W._parse_model_options({"part_animations": [ok]})
    for k, v in (
        ("pivot", "../../etc"),
        ("axis", [0, 0, 0]),
        ("angle_deg", float("inf")),
        ("pivot", [True, 0, 0]),
    ):
        try:
            W._parse_model_options({"part_animations": [{**ok, k: v}]})
        except W.WorkspaceError:
            pass
        else:
            raise AssertionError((k, v))

    class P:
        root = tmp_path

    (tmp_path / M.MODEL_OPTIONS_FILE).write_text("[1, 2]")
    assert M.model_options_of(P) == {"normal_maps": False, "part_animations": []}
    (tmp_path / M.MODEL_OPTIONS_FILE).write_text("no es json")
    assert M.model_options_of(P)["normal_maps"] is False
    (tmp_path / M.MODEL_OPTIONS_FILE).write_text(
        json.dumps({"normal_maps": "false", "part_animations": "abc"})
    )
    assert M.model_options_of(P) == {"normal_maps": False, "part_animations": []}

    veh = tmp_path / "veh"
    veh.mkdir()
    (tmp_path / "fuera.dds").write_bytes(b"DDS xxxx")
    BS = chr(92)
    assert km._normal_map({"normalMap": ".." + BS + "fuera.dds"}, veh) == ("", b"")
    (veh / "ok.dds").write_bytes(b"DDS yyyy")
    assert km._normal_map({"normalMap": "art" + BS + "ok.dds"}, veh)[0] == "ok.dds"


def test_caras_traseras_de_piezas_finas():
    import struct

    from src.ac import double_sided as D
    from src.ac.kn5_tree import MeshData

    def vert(x, y, z, ny=1.0):
        return struct.pack("<3f3f2f3f", x, y, z, 0, ny, 0, 0, 0, 1, 0, 0)

    sheet = MeshData(
        b"\x01\x01\x00",
        [vert(0, 0, 0), vert(1, 0, 0), vert(0, 0, 1), vert(1, 0, 1)],
        [0, 1, 2, 1, 3, 2],
        0,
        0,
        0,
        100,
        True,
    )
    assert D.is_open(sheet)
    # tetraedro cerrado: sin bordes
    tet = MeshData(
        b"\x01\x01\x00",
        [vert(0, 0, 0), vert(1, 0, 0), vert(0, 1, 0), vert(0, 0, 1)],
        [0, 2, 1, 0, 1, 3, 0, 3, 2, 1, 2, 3],
        0,
        0,
        0,
        100,
        True,
    )
    assert not D.is_open(tet)
    both = D.with_back_faces(sheet)
    assert len(both.vertices) == 8 and len(both.indices) == 12
    ny = [struct.unpack_from("<3f", v, 12)[1] for v in both.vertices]
    assert ny[:4] == [1.0] * 4 and ny[4:] == [-1.0] * 4  # normales negadas en la copia
    assert both.indices[6:9] == [4, 6, 5]  # giro invertido (a, c, b)
    assert D.AERO_NODE.search("35b84_efixturetype__eft_wing1016")
    assert not D.AERO_NODE.search("35b84_body0")


@pytest.mark.skip(
    reason="la aplicación ya no construye bancos con FMOD (_staged_build retirado de src.audio.pipeline)"
)
def test_fmod_reintenta_con_proyecto_nuevo_y_carpeta_corta(tmp_path, monkeypatch):
    from tools.audio import fmod_build as FB
    from src.audio import pipeline as AP

    work_dirs = []
    calls = {"n": 0}

    def prepare(spec, work, cl):
        work_dirs.append(work)
        (work / "spec.json").write_text("{}")

    def run_build(work, cl, car):
        calls["n"] += 1
        (work / "fmod_output.txt").write_text(f"intento {calls['n']}")
        if calls["n"] < 3:
            raise FB.AudioBuildError("-build terminó con código 1")
        return {"fmod_output": "x"}

    def collect(work, car, events):
        d = work / "prebuilt"
        d.mkdir()
        (d / f"{car}.bank").write_bytes(b"bank")
        return d

    monkeypatch.setattr(FB, "prepare_project", prepare)
    monkeypatch.setattr(FB, "run_build", run_build)
    monkeypatch.setattr(FB, "collect", collect)
    out = tmp_path / "salida_con_un_nombre_largo"
    out.mkdir()
    report: dict = {}
    dst = AP._staged_build(
        {"secondary": [], "engine": {}}, out, "fmodcl", "beam2ac_x", report
    )
    assert dst == out / "prebuilt" and (dst / "beam2ac_x.bank").read_bytes() == b"bank"
    assert (
        report["build_attempts"] == 3 and len(set(work_dirs)) == 3
    )  # proyecto nuevo por intento
    assert all(
        len(str(w)) < 80 and not w.exists() for w in work_dirs
    )  # cortas y borradas
    assert (out / "fmod_output_intento1.txt").is_file() and (
        out / "fmod_output_intento3.txt"
    ).is_file()
    # siempre falla: error explicado tras BUILD_ATTEMPTS
    calls["n"] = -100
    out2 = tmp_path / "otra"
    out2.mkdir()
    with pytest.raises(FB.AudioBuildError, match="tras 3 intentos"):
        AP._staged_build(
            {"secondary": [], "engine": {}}, out2, "fmodcl", "beam2ac_x", {}
        )


def test_animaciones_forma_simple_sin_numeros():
    import glob

    import pytest

    from src.ac import part_anim as P
    from src.app import workspace as W
    from src.ac.steer_anim import read_ksanim
    from src.project.export import default_ac_root

    simple = {"kind": "door_L", "node": "DOOR_L", "hinge": "front", "angle_deg": 60}
    assert W._parse_model_options({"part_animations": [simple]})["part_animations"] == [
        simple
    ]
    for bad in (
        {**simple, "hinge": "arriba"},
        {**simple, "axis_name": "x"},
        {**simple, "angle_deg": 999},
    ):
        with pytest.raises(W.WorkspaceError):
            W._parse_model_options({"part_animations": [bad]})
    root = default_ac_root()
    ae86 = (
        [
            k
            for k in glob.glob(str(root / "content/cars/ks_toyota_ae86/*.kn5"))
            if "collider" not in k
        ]
        if root
        else []
    )
    if not ae86:
        pytest.skip("sin AE86 de Kunos instalado")
    out = P.build_files(
        ae86[0],
        [{"kind": "wiper", "node": "WIPER1", "hinge": "bottom", "angle_deg": 40}],
    )
    ver, bones = read_ksanim(out["car_wiper.ksanim"])
    assert bones[0][0] == "WIPER1" and len(bones[0][1]) == 100
    f0, f50 = bones[0][1][0], bones[0][1][50]
    assert max(abs(a - b) for a, b in zip(f0, f50)) > 0.01
    with pytest.raises(P.PartAnimError):
        P.build_files(
            ae86[0],
            [{"kind": "wiper", "node": "WIPER1", "hinge": "arriba", "angle_deg": 5}],
        )


def test_geometria_de_piezas_alineada_y_referencias(tmp_path):
    import json
    import struct

    from src.ac.part_anim import node_refs

    class N:
        def __init__(self, name):
            self.name = name

    assert node_refs(type("K", (), {"nodes": [N("a"), N("b"), N("a"), N("a")]})) == [
        "a#1",
        "b",
        "a#2",
        "a#3",
    ]
    # el cliente crea Float32Array sobre (4 + longitud del JSON): debe ser múltiplo de 4
    head = json.dumps({"pieces": []}).encode()
    head += b" " * (-len(head) % 4)
    assert (4 + len(struct.pack("<I", len(head)) + head) - 4) % 4 == 0


def test_resolver_piezas_repetidas_por_referencia():
    from src.ac.part_anim import resolve_node

    class N:
        def __init__(self, name):
            self.name = name

    a1, b, a2 = N("a"), N("b"), N("a")
    kn = type("K", (), {"nodes": [a1, b, a2]})
    assert resolve_node(kn, "a#2") == [a2] and resolve_node(kn, "a#1") == [a1]
    assert resolve_node(kn, "b") == [b]
    assert resolve_node(kn, "a") == []  # ambiguo: hay que usar a#1 o a#2
    assert resolve_node(kn, "zz") == [] and resolve_node(kn, None) == []


def test_texturas_de_llamas_generadas():
    import struct

    from src.ac import flames as F

    files = F.flame_texture_files()
    assert len(files) == 3 * F.TEX_FRAMES
    names = [k.split("/")[-1] for k in files]
    # prefijos de AC: f = destello, l = cola, otro = llama principal
    assert sum(n.startswith("f") for n in names) == F.TEX_FRAMES
    assert sum(n.startswith("l") for n in names) == F.TEX_FRAMES
    assert sum(n[0] not in "fl" for n in names) == F.TEX_FRAMES
    for k, d in files.items():
        assert k.startswith("texture/flames/") and d[:4] == b"DDS "
        assert struct.unpack_from("<II", d, 12) == (F.TEX_PX, F.TEX_PX)
        assert len(d) == 128 + F.TEX_PX * F.TEX_PX * 4
        alphas = d[128 + 3 :: 4]
        assert max(alphas) > 200 and alphas[0] == 0  # centro opaco, borde transparente


def test_bc5_se_convierte_a_normal_completa(tmp_path):
    import struct

    from src.ac import kn5_materials as km

    # bloque BC4 de valor constante 255 (r0=r1=255, índices 0) y 0 (r0=r1=0): x=+1, y=-1 → z=0
    blk = bytes([255, 255, 0, 0, 0, 0, 0, 0]) + bytes([0, 0, 0, 0, 0, 0, 0, 0])
    assert km._bc4_block(blk[:8]) == [255] * 16 and km._bc4_block(blk[8:]) == [0] * 16
    flags = 0x1 | 0x2 | 0x4 | 0x1000
    head = struct.pack("<7I", 124, flags, 4, 4, 16, 0, 1) + bytes(44)
    pf = struct.pack("<II4s5I", 32, 4, b"ATI2", 0, 0, 0, 0, 0)
    dds = b"DDS " + head + pf + struct.pack("<5I", 0x1000, 0, 0, 0, 0) + blk
    out = km.bc5_to_rgba_dds(dds)
    assert len(out) == 128 + 4 * 4 * 4 and out[84:88] != b"ATI2"
    b, g, r, a = out[128:132]
    assert (r, g, a) == (255, 0, 255) and b == 128  # z = 0 → 0,5
    # índice 2 de la paleta (r0 > r1): (6·r0 + 1·r1) // 7
    assert (
        km._bc4_block(bytes([200, 100, 0b010, 0, 0, 0, 0, 0]))[0]
        == (6 * 200 + 100) // 7
    )
    # _normal_map convierte BC5 al leerlo
    (tmp_path / "n.dds").write_bytes(dds)
    name, data = km._normal_map({"normalMap": "n.dds"}, tmp_path)
    assert name == "n.dds" and data[84:88] != b"ATI2" and len(data) == len(out)
