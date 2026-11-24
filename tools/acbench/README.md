# Banco LSD autónomo en Assetto Corsa (G2.22)

Controla el coche de AC sin conducir: CSP Custom AI para los mandos, una app Lua propia
(`beam2ac_lsd_bench`) para la telemetría por rueda (`feedbackTorque`, `shaftVelocity`…) y el CSV,
y un controlador Python con máquina de estados. Informe y resultados:
`docs/research/g222_banco_lsd_autonomo_2026-11-07.md`; evidencia en `output/g222_2026-11-07/`.

## Requisitos

- AC con CSP ≥ 0.2.8 (probado con 0.3.0-preview622) y Steam abierto.
- Autorización del usuario para: crear carpetas nuevas en la instalación de AC (circuito
  `beam2ac_g222_pad`, app `beam2ac_lsd_bench`, coches `beam2ac_g221lsd_*`), tocar
  `Documents/Assetto Corsa/cfg/{race.ini, assists.ini, extension/new_behaviour.ini}` (con copia
  de seguridad) y lanzar/cerrar AC.

## Uso

```bash
# 1. preparar (solo output/): circuito generado, app con las estructuras de bridge.py, copias de los coches
python -m tools.acbench.install stage --out output/<nueva>/preparacion

# 2. comprobaciones en el juego (instalan si falta, configuran, lanzan y cierran AC)
python -m tools.acbench.f0 --stage <prep> --state <estado> --out output/<nueva>/f0   # viabilidad
python -m tools.acbench.f1 --stage <prep> --state <estado> --out output/<nueva>/f1   # mandos + vigilante

# 3. banco completo (7 coches, una sesión de AC por coche; ~1 h)
python -m tools.acbench.run_bench --stage <prep> --state <estado> --out output/<nueva>/run --run-id r01
#    opciones: --cars a1 a2 …  --items N12+ D30- …  --scale 0.5

# 4. análisis (criterios pre-registrados en output/g222_2026-11-07/preregistro.json)
python -m tools.acbench.analysis output/<nueva>/run [más carpetas] --out output/<nueva>/analisis

# 5. retirar todo y restaurar la configuración del usuario
python -m tools.acbench.install uninstall --state <estado> --logs-out output/<nueva>/registros_app
python -m tools.acbench.install restore-configs --state <estado>
```

`<estado>` guarda `instalacion.json` (hashes de todo lo instalado) y `respaldo/` (configuración
original). La retirada solo borra carpetas cuyos archivos coinciden con el registro (tolera
`data/proview_nodes.ini`, que crea AC, y los CSV de `registros/`, que copia antes).

**Lazy (2026-11-21):** el manifest de la app va con `LAZY = FULL`: el script solo corre con su ventana abierta en AC (no llena los logs). Los bancos automáticos (`f0`, `f1`, `run_bench`) no abren la ventana: prepáralos con `stage --always-on` (`LAZY = NONE`). La app ya instalada en `apps/lua` no cambia hasta reinstalarla.

## Hallazgos que el código da por medidos (F0/F1)

| Hecho | Medida |
|---|---|
| Menú de salida de AC | se evita con `__START_IMMEDIATELY=1` y `FORCE_START=1` en `race.ini` (claves de CSP) |
| Custom AI | `Car0.v0` aparece y su `packet_id` crece fuera del menú; mandos aplicados tal cual |
| `steer` | normalizado; 0,2 → 84,6° de volante; positivo ⇒ guiñada negativa |
| Marchas | `ac.StateCar.gear` 0 = N, 1 = 1.ª; Custom AI `gear` 1 = N, 2 = 1.ª |
| Punto muerto | `gear_dn` con embrague abierto (0); `physics.engageGear(0, 0)` en marcha no se mantiene |
| Embrague | `ac.StateCar.clutch` 1 = cerrado; el automático de Custom AI desliza por debajo de ~2200 rpm |
| `physics.*` | todas disponibles para la app con `[_SCRIPTING_PHYSICS]` + `WAV_PITCH=extended-0` |
| `SimState.v1` | **no usar**: `time_scale` 0,25 congeló la física y no volvió con 1,0 |
| Vigilante | sin latido 0,5 s ⇒ `physics.setGentleStop` + gas 0 (escribir `CarControls0` desde Lua no basta) |
| `drivetrainTorque` | = par `T` de el cálculo 4WD de AC (balance de 4 ruedas) |

## Seguridad

- El controlador escribe `CarControls0.v0` con el freno a 1 antes de lanzar AC.
- Parada segura al salir por cualquier causa.
- Vigilante en Python (telemetría parada > 0,5 s ⇒ parada segura; > 15 s ⇒ aborta).
- Vigilante en Lua (sin latido > 0,5 s ⇒ parada suave), comprobado en F1.

## G2.25: validación funcional de coches (`validate.py`)

Informe de la auditoría y del protocolo: `output/g225_2026-11-09/audit_r01/README.md`.

```bash
# preparación (solo output/): app beam2ac_g225_bench (G2.22 + bloque Geo), circuito beam2ac_g225_pad, coches
python -m tools.acbench.app_g225 stage --out output/<nueva>/prep [--source-root … --export 004 --prefix beam2ac_g225b_]
# una sesión (instala si falta, configura, lanza y cierra AC); protocolo mínimo por defecto
python -m tools.acbench.validate --stage <prep> --state <estado> --out <runs> --car atlanta --run-id r31 \
    [--car-dir <prep>/content/cars/<id>] [--protocol minimo|extendido] [--abort-rate 0.5] [--reps 3]
# análisis con los criterios pre-registrados (+ adendas 1 y 2)
python -m tools.acbench.validate_analysis <runs> --prereg …/preregistro.json --kn5 …/geometria_kn5.json \
    --specs …/specs_b.json --out <results>
```

| Pieza | Archivo |
|---|---|
| Geometría por fotograma, capturas `ac.GeometryShot` (solo ortográficas), volcado JSON | `bridge_geo.py`, `lua/beam2ac_g225_bench/g225_extension.lua.in` |
| Circuito con dos muros (los muros NO son referencia de colisión válida, ver el informe) | `track_g225.py` |
| Batería y protocolos `minimo` / `extendido` | `validate.py` |
| Resultado de cada ensayo (`valid_trial`, `vehicle_failure`, `track_failure`, `controller_failure`, `telemetry_failure`, `invalid_trial`), reintentos y aborto | `policy.py` |
| Análisis | `validate_analysis.py` |
| Doble sintético (solo pruebas) | `simac_g225.py` |

Hechos medidos en AC que el código da por buenos: `physics.setCarPosition(pos, dir)` deja el coche mirando a
**−dir**; con el embrague automático de Custom AI activo, `clutch = 0` NO abre el embrague (se desactiva el
automático); las vistas en perspectiva de `ac.GeometryShot` no mostraron el coche (se usan ortográficas).

### G2.25 (2026-11-10): arranque, Steam y adenda 3

- `validate.run_car` registra `sesion/arranque.jsonl` (Tel + Geo, solo lectura, desde la primera telemetría hasta
  12 s de física) y lo resume en `resumen.json → arranque`; `validate_analysis` lo evalúa (`A_arranque`, adenda 3).
- `install.launch_ac` exige Steam con sesión iniciada (`HKCU\Software\Valve\Steam\ActiveProcess\ActiveUser` ≠ 0)
  y el lanzador oficial cerrado; `AcSession.wait_ready` aborta y cierra `AssettoCorsa.exe` si Steam lo abre (con
  Steam recién abierto, AC volvió por el lanzador, que reescribió la configuración del usuario).
- Sesiones nocturnas: `output/g225_2026-11-09/noche.sh <coche> <r3N>`.
