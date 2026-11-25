"""G2.50: un ExporterVersion desconocido solo se trata como uno conocido si TODO su código coincide."""

from types import SimpleNamespace

from src.importers.automation import (
    ENGINE_LUA_VERSION,
    KNOWN_LUA,
    equivalent_known_version,
    is_automation,
)

E38, E40 = list(ENGINE_LUA_VERSION)


def src(version, lua, typ="Automation", author="Camshaft Software LTD"):
    info = {"Type": typ, "Author": author, "ExporterVersion": version}
    return SimpleNamespace(
        model_info=info, lua_sha256={f"lua/{k}.lua": v for k, v in lua.items()}
    )


def lua(engine, **extra):
    base = {
        n: next(iter(s))
        for n, s in KNOWN_LUA.items()
        if n in ("camsoEngineThermals", "cefaero")
    }
    return {"camsoEngine": engine, **base, **extra}


def test_known_version_is_itself():
    assert equivalent_known_version(src(40340, lua(E40)))[0] == "40340"


def test_unknown_version_with_identical_code_maps_to_that_version():
    assert equivalent_known_version(src(38597, lua(E40)))[0] == "40340"
    assert equivalent_known_version(src(38597, lua(E38)))[0] == "38022"
    assert is_automation(src(38597, lua(E40)))


def test_unknown_engine_lua_blocks_with_reason():
    v, why = equivalent_known_version(src(38597, lua("0" * 64)))
    assert v is None and "camsoEngine.lua ausente o desconocido" in why
    assert not is_automation(src(38597, lua("0" * 64)))


def test_changed_secondary_lua_blocks():
    v, why = equivalent_known_version(src(38597, lua(E40, cefaero="1" * 64)))
    assert v is None and "cefaero" in why


def test_not_automation_never_maps():
    assert equivalent_known_version(src(38597, lua(E40), author="otro"))[0] is None


def test_no_lua_needs_combustion_marker():
    s = src(38597, {})
    assert equivalent_known_version(s)[0] is None
    assert equivalent_known_version(s, combustion=False)[0] is None
    assert equivalent_known_version(s, combustion=True)[0] == "200409"


# --- resumen de ABS (G2.50) --------------------------------------------------------
from src.importers.beamng import _import_abs_summary  # noqa: E402
from src.ir.param import Param  # noqa: E402
from src.ir.vehicle import BrakeAxle, VehicleIR  # noqa: E402


def abs_ir(front, rear, share=0.7):
    ir = VehicleIR()
    for pos, (state, tgt, hz) in (("front", front), ("rear", rear)):
        ax = BrakeAxle()
        ax.abs = (
            Param.of(state, "enum", "extracted") if state else Param.missing("enum")
        )
        ax.abs_slip_target = Param.of(tgt, "1", "extracted")
        ax.abs_frequency = Param.of(hz, "Hz", "extracted")
        ir.brakes.axles[pos] = ax
    ir.brakes.front_share_full_pedal = Param.of(share, "1", "computed")
    return ir


def run_abs(ir):
    def put(path, value, unit, kind, *refs, note="", key=""):
        ir.set(path, Param.of(value, unit, kind, list(refs), note, key))

    _import_abs_summary(ir, put)
    return ir.brakes


def test_abs_both_axles_weighted_limit_and_min_rate():
    b = run_abs(abs_ir(("enabled", 0.22, 50.0), ("enabled", 0.18, 100.0)))
    assert b.abs_present.value == 1
    assert abs(b.abs_slip_limit.value - (0.7 * 0.22 + 0.3 * 0.18)) < 1e-9
    assert b.abs_rate_hz.value == 50.0


def test_abs_none_is_present_zero_and_rest_not_applicable():
    b = run_abs(abs_ir(("disabled", None, None), ("disabled", None, None)))
    assert b.abs_present.value == 0
    assert b.abs_slip_limit.status == b.abs_rate_hz.status == "not_applicable"


def test_abs_single_axle_stays_missing():
    b = run_abs(abs_ir(("enabled", 0.22, 50.0), ("disabled", None, None)))
    assert not b.abs_present.present and not b.abs_slip_limit.present


def test_same_name_in_two_folders_both_count():
    s = src(99999, lua(E38))
    s.lua_sha256["lua/controller/camsoEngine.lua"] = (
        "0" * 64
    )  # el modificado no puede quedar tapado
    assert equivalent_known_version(s)[0] is None


def test_abs_missing_reason_is_written():
    from src.importers.beamng import _explain_missing

    ir = VehicleIR()
    _explain_missing(ir, SimpleNamespace(stages={"chassis": {"ok": True}}), True)
    assert ir.brakes.abs_present.reason and "ABS" in ir.brakes.abs_present.reason


# --- coherencia de detectores (pairing solo ve info.json; el importador ve también el Lua) ---
from src.catalog.pairing import declares_automation  # noqa: E402
from src.importers.automation import is_automation_info  # noqa: E402


def test_pairing_equals_info_detector_always():
    for v in (38022, 40340, 200409, 38597, "x"):
        for t, a in (
            ("Automation", "Camshaft Software LTD"),
            ("Automation", "otro"),
            ("Otro", "Camshaft Software LTD"),
        ):
            info = {"Type": t, "Author": a, "ExporterVersion": v}
            assert declares_automation(info) == is_automation_info(info)


def test_known_version_all_detectors_agree():
    s = src(40340, lua(E40))
    assert declares_automation(s.model_info) and is_automation(s)


def test_unknown_version_with_known_lua_detectors_differ_on_purpose():
    s = src(38597, lua(E40))
    assert is_automation(s) and not declares_automation(s.model_info)


def test_unknown_version_unknown_lua_nobody_accepts():
    s = src(38597, lua("0" * 64))
    assert not is_automation(s) and not declares_automation(s.model_info)


def test_incomplete_info_json():
    for info in (
        {},
        {"Type": "Automation"},
        {"Type": "Automation", "Author": "Camshaft Software LTD"},
    ):
        s = SimpleNamespace(model_info=info, lua_sha256={"lua/camsoEngine.lua": E40})
        assert not declares_automation(info) and not is_automation(s)
        assert equivalent_known_version(s)[0] is None


def test_ambiguous_no_lua_without_combustion_blocks_and_with_it_accepts():
    s = src(38597, {})
    assert not is_automation(s, None) and not is_automation(s, False)
    assert is_automation(s, True) and equivalent_known_version(s, True)[0] == "200409"
