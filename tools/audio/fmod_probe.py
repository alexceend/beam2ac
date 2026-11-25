"""Prueba de reproducción de un banco con el runtime FMOD de la instalación de AC (ctypes).

Carga ``fmodstudio64.dll`` de AC (solo lectura, tal como la usa el juego), renderiza un evento a
un WAV con la salida ``WAVWRITER_NRT`` (sin tarjeta de sonido) y mide el pico. Detecta bancos que
cargan pero no suenan (p. ej. ``SNDH`` con un desplazamiento viejo).

Uso: ``python -m tools.audio.fmod_probe <banco> <GUIDs.txt> <evento> <rpm> [--out carpeta]``.
"""

import ctypes as C, struct, sys, uuid, array, os
from pathlib import Path
from src.project.export import default_ac_root

AC = default_ac_root() or Path("assettocorsa")
os.add_dll_directory(str(AC))
fm = C.CDLL(str(AC / "fmod64.dll"))
st = C.CDLL(str(AC / "fmodstudio64.dll"))
HEADER = 0x00010812  # FMOD 1.08.18 (AC: 'version 67602')


class GUID(C.Structure):
    _fields_ = [
        ("d1", C.c_uint32),
        ("d2", C.c_uint16),
        ("d3", C.c_uint16),
        ("d4", C.c_ubyte * 8),
    ]


def guid(s):
    b = uuid.UUID(s).bytes_le
    g = GUID()
    C.memmove(C.byref(g), b, 16)
    return g


def chk(r, what):
    if r != 0:
        raise RuntimeError(f"{what}: FMOD_RESULT {r}")


def run(banks, event_guid, params, out_wav, seconds=4.0, version=HEADER):
    sysp = C.c_void_p()
    chk(st.FMOD_Studio_System_Create(C.byref(sysp), C.c_uint(version)), "Create")
    low = C.c_void_p()
    chk(st.FMOD_Studio_System_GetLowLevelSystem(sysp, C.byref(low)), "LowLevel")
    chk(fm.FMOD_System_SetOutput(low, C.c_int(5)), "SetOutput WAVWRITER_NRT")
    chk(
        st.FMOD_Studio_System_Initialize(
            sysp, 128, 0, 0, C.c_char_p(str(out_wav).encode())
        ),
        "Initialize",
    )
    loaded = []
    for b in banks:
        bk = C.c_void_p()
        r = st.FMOD_Studio_System_LoadBankFile(
            sysp, C.c_char_p(str(b).encode()), 0, C.byref(bk)
        )
        chk(r, f"LoadBank {Path(b).name}")
        loaded.append(bk)
    desc = C.c_void_p()
    g = guid(event_guid)
    chk(
        st.FMOD_Studio_System_GetEventByID(sysp, C.byref(g), C.byref(desc)),
        "GetEventByID",
    )
    inst = C.c_void_p()
    chk(
        st.FMOD_Studio_EventDescription_CreateInstance(desc, C.byref(inst)),
        "CreateInstance",
    )
    st.FMOD_Studio_EventDescription_LoadSampleData(desc)
    for _ in range(50):
        st.FMOD_Studio_System_Update(sysp)
    chk(st.FMOD_Studio_System_FlushSampleLoading(sysp), "FlushSampleLoading")
    for k, v in params.items():
        chk(
            st.FMOD_Studio_EventInstance_SetParameterValue(
                inst, C.c_char_p(k.encode()), C.c_float(v)
            ),
            f"param {k}",
        )
    chk(st.FMOD_Studio_EventInstance_Start(inst), "Start")
    n = int(seconds * 48000 / 1024)
    for i in range(n):
        chk(st.FMOD_Studio_System_Update(sysp), "Update")
    ps = C.c_int()
    st.FMOD_Studio_EventInstance_GetPlaybackState(inst, C.byref(ps))
    st.FMOD_Studio_EventInstance_Stop(inst, 0)
    chk(st.FMOD_Studio_System_Update(sysp), "Update")
    st.FMOD_Studio_System_Release(sysp)
    return ps.value


def peak(path):
    raw = Path(path).read_bytes()
    i = raw.index(b"data")
    d = raw[i + 8 :]
    fmt = struct.unpack_from("<HHIIHH", raw, raw.index(b"fmt ") + 8)
    if fmt[0] == 3:
        a = array.array("f")
        a.frombytes(d[: len(d) // 4 * 4])
        return max((abs(x) for x in a), default=0), len(a)
    a = array.array("h")
    a.frombytes(d[: len(d) // 2 * 2])
    return max((abs(x) for x in a), default=0) / 32768, len(a)


if __name__ == "__main__":
    bank, gtxt, ev, rpm = sys.argv[1], sys.argv[2], sys.argv[3], float(sys.argv[4])
    car = Path(bank).stem
    gid = None
    for l in Path(gtxt).read_text().splitlines():
        g, n = l.split(None, 1)
        if n.strip() == f"event:/cars/{car}/{ev}":
            gid = g.strip("{}")
    from src.safe_paths import ensure_safe_output_path

    out = ensure_safe_output_path(
        Path(sys.argv[5] if len(sys.argv) > 5 else "output/fmod_probe")
        / f"{car}_{ev}_{int(rpm)}.wav"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    banks = [AC / "content/sfx/common.bank", bank]
    ps = run(banks, gid, {"rpms": rpm, "throttle": 1.0}, out)
    print(car, ev, rpm, "estado", ps, "pico/muestras", peak(out))
