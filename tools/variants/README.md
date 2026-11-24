# Reconstrucción de las variantes aprobadas (fase A)

Scripts que construyeron los paquetes de prueba del Seat Ibiza aprobados en el juego, conservados en el proyecto y
adaptados para reconstruirlos en una carpeta nueva y aislada. **Ningún script de aquí escribe en los paquetes aprobados**:
los `ingame_cycle/pkg_*` están sellados por su `manifest.json` (`src/safe_paths.py`).

## Contenido

| Ruta | Qué es |
|---|---|
| `original/` | Copia byte a byte de cada script tal como estaba, con su procedencia y sha256 en `original/SHA256SUMS.txt`. No se ejecuta. |
| `*.py` (raíz) | Versión adaptada de cada original. Solo cambian las raíces de entrada/salida y la creación de la carpeta nueva (`ADAPTATIONS.diff`). |
| `_common.py` | Argumentos comunes: `--base-root`, `--out-root`, `--repro`. |
| `reproduce_chain.py` | Ejecuta la cadena completa en una carpeta nueva y compara cada paquete con su manifiesto aprobado. |
| `ADAPTATIONS.diff` | Diff unificado original → adaptado de cada script. |

## Inventario de la cadena

Raíz de la cadena (no se reconstruye aquí): `ingame_cycle/pkg_v13_integrated`, copia de
`integration_2026-10-25/run1_v12_equiv` (salida de `python -m src.ac.assemble … --physics-base pkg_v6_drive`; ver
`docs/research/informe_integracion_ensamblador_2026-10-25.md`).

| Paquete | Base | Script original (ubicación anterior) | Transformación |
|---|---|---|---|
| `pkg_v13vw_shiftwindow` | `pkg_v13_integrated` | `make_v13vw.py` (solo existía en la carpeta temporal de la sesión de Claude Code) | `data/drivetrain.ini` + `[GEARBOX] VALID_SHIFT_RPM_WINDOW=800` |
| `pkg_v14as_animstatic`, `pkg_v14hp_hands_pos`, `pkg_v14hn_hands_neg` | `pkg_v13vw` | `ingame_cycle/tools_2026-10-25_v14/build_v14_anim.py` | `animations/steer.ksanim` + `[STEER_ANIMATION]` en `driver3d.ini` |
| `pkg_v14hc_hands_continuous` | `pkg_v14hp` | `tools_2026-10-25_v14/build_v14hc.py` | clip de 600 fotogramas, `LOCK=450` |
| `pkg_v14ha_alternating_grip` | `pkg_v14hc` | `tools_2026-10-25_v14/build_v14ha.py` | clip de agarre alterno, `LOCK=125` |
| `pkg_v15li_lights` | `pkg_v14ha` | `tools_2026-10-25_v15/build_v15li.py` | 19 mallas de luz renombradas en el KN5, `lights.ini`, secciones CSP en `ext_config.ini` |
| `pkg_v16sn_sound` | `pkg_v15li` | `sound_work_2026-10-25/scripts/build_v16sn.py` | `sfx/<id>.bank`, `sfx/GUIDs.txt` recortado, `data/sounds.ini` |
| `pkg_v17cam` | `pkg_v16sn` | `camera_work_2026-10-25/scripts/measure_cameras.py` + `build_v17cam.py` | `data/dash_cam.ini`, `BONNET_CAMERA_POS` |
| `pkg_v18bu` | `pkg_v17cam` | `camera_work_2026-10-25/scripts/measure_bumper.py` + `build_v18bu.py` | `BUMPER_CAMERA_POS` |

**Pasos que esta cadena NO rehace** (entran como artefactos de solo lectura):
- `pkg_v13_integrated`: necesita Blender y el ensamblador.
- El banco FMOD de v16sn (`sound_work_2026-10-25/fmod_project/…/Build/Desktop/<id>.bank`): necesita FMOD Studio
  1.08.12 (`make_spec.py` → `build_ibiza_events.js` → `fmodstudiocl -script` y `-build`). Las copias de esos scripts
  están en `original/v16_sound/` solo como registro.
- Las mediciones de cámara sí se rehacen (`measure_cameras.py`, `measure_bumper.py`) y se comparan con las originales
  de `camera_work_2026-10-25/` sin las rutas absolutas.

## Adaptaciones (todas en `ADAPTATIONS.diff`)

1. `ROOT` se calcula desde `tools/variants` (`parents[2]`) en lugar de la ubicación anterior.
2. `IC` (carpeta de los paquetes base) = `--base-root`, por defecto los aprobados; los paquetes nuevos van a
   `--out-root` (`OUT_IC`). Las mediciones de cámara se leen y escriben en `--out-root`.
3. `OUT.mkdir(parents=True)` → `new_output_dir(OUT)`: falla si la carpeta existe o está sellada.
4. `--repro`: el control «el id ya existe en la instalación de AC» no detiene la reconstrucción (el paquete no se instala).
   Sin `--repro` se comporta como el original.
5. Las mediciones fallan si su JSON de salida ya existe.

La lógica de construcción y todas las comprobaciones de cada script son las originales.

Diferencia conocida con lo que se ejecutó: `camera_work_2026-10-25/scripts/build_v17cam.py` se editó **después** de construir
v17cam. Fue un arreglo de estilo (`flake8` E128) en la comprobación «BONNET_CAMERA_POS = medición»: se quitó un
`.split(";")[0]` innecesario, porque `read_ini` ya quita el comentario. El resultado de la comprobación es el mismo.

## Uso

```bash
python tools/variants/reproduce_chain.py --out-root output/seat_ibiza_acfr/repro_<fecha>
```

## Resultado de la reconstrucción del 2026-10-25

Carpeta: `output/seat_ibiza_acfr/repro_2026-10-25/`. Informe con los sha256: `repro_report.json`; salida de cada paso:
`logs/`. Los 10 pasos terminan con código 0 y sin nada en stderr.

| Paquete | Archivos idénticos al manifiesto aprobado | Distintos | Faltan | Sobran |
|---|---|---|---|---|
| `pkg_v13vw_shiftwindow` | 19/19 | 0 | 0 | 0 |
| `pkg_v14as_animstatic` | 20/20 | 0 | 0 | 0 |
| `pkg_v14hp_hands_pos` | 20/20 | 0 | 0 | 0 |
| `pkg_v14hn_hands_neg` | 20/20 | 0 | 0 | 0 |
| `pkg_v14hc_hands_continuous` | 20/20 | 0 | 0 | 0 |
| `pkg_v14ha_alternating_grip` | 20/20 | 0 | 0 | 0 |
| `pkg_v15li_lights` | 21/21 | 0 | 0 | 0 |
| `pkg_v16sn_sound` | 24/24 | 0 | 0 | 0 |
| `pkg_v17cam` | 25/25 | 0 | 0 | 0 |
| `pkg_v18bu` | 25/25 | 0 | 0 | 0 |

**Mediciones de cámara:** `cameras_measurement.json` y `bumper_measurement.json` son iguales a las originales de
`camera_work_2026-10-25/`, quitando las rutas absolutas del KN5.

**Alcance de «reproducible»:** sí, desde `pkg_v13_integrated` y el banco FMOD ya construido, que entran como artefactos
de solo lectura. No se han reconstruido ni la salida del ensamblador (Blender) ni el banco (FMOD Studio).

**Comparación:** solo se comparan los archivos de la carpeta del coche (`files` del manifiesto). Los `manifest.json`
y `README.md` de nivel de paquete no se comparan: llevan rutas absolutas o se escribieron a mano.
