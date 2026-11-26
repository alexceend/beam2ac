Mallas, audio, seguridad y pruebas

Para desarrolladores: piezas de apoyo

# Mallas 3D → KN5

Las mallas de BeamNG vienen en .dae y se colocan según las entradas flexbodies del JBeam. src/mesh genera un plan y src/mesh/pure_kn5.py construye el KN5 en Python puro.

- El plan (plan_for_zip o src.mesh.build --plan-only) se calcula antes de construir el KN5.

- El constructor lee los .dae, aplica la transformación de cada pieza, duplica la rueda por cada rueda, nombra los objetos como pide AC (WHEEL_LF, SUSP_LF, COCKPIT_HR…) y escribe el KN5 directamente.

- AC no admite hijos de una malla: la jerarquía usa objetos vacíos (WHEEL_xx → llanta, neumático, disco).

- El origen del KN5 y el desplazamiento gráfico (GRAPHICS_OFFSET) se derivan de la posición de las ruedas, sin promediar: si las cuatro no coinciden, es un error.

- El collider usa la malla raíz de mayor caja; las texturas salen de materials.json o materials.cs.

# Coordenadas

BeamNG y AC usan ejes distintos. La conversión es una rotación pura (sin espejo) y vive en src/coords.py. Es el único sitio donde debe hacerse; el constructor de KN5 aplica solo esa conversión.

# Audio

src/audio convierte los WAV de origen en un banco de sonido de AC. Cada paso tiene un módulo:

| Módulo | Función |
|---|---|

| src/extract/audio_sources.py | Detecta los blends de sonido y sus WAV, las series de carga/retención y la cobertura de revoluciones. |

| wavinfo.py | Lee las cabeceras WAV a mano (el módulo estándar no admite float32). |

| plan.py | Valida y asigna cada WAV a su papel. |

| engine_gen.py, bank_graph.py, bank_patch.py | Generan el banco en Python a partir del esqueleto versionado (src/audio/data), sin FMOD. |

| pipeline.py | Orquesta y calcula la escalera de estados. |

Escalera: detectado → validado → plan generado → proyecto generado → banco construido → instalado en la exportación → validado acústicamente → escuchado → validado en AC. Los peldaños «escuchado» y «validado en AC» nunca los marca el código: requieren una persona.

- python -m src.audio status <vehiculo> muestra el plan; python -m src.audio build … --out output/<nueva> [--no-build] genera el proyecto.

- El esqueleto se regenera con python -m tools.audio.make_skeleton (herramienta de mantenimiento: necesita FMOD Studio y el SDK de AC; la aplicación no la usa).

- Límites: solo motor, un banco no normalizado, sin zips.

# Guardián de escritura (`safe_paths`)

Toda ruta de escritura pasa por ensure_safe_output_path(). Lo que admite: output/, builds/, la carpeta temporal del sistema y las raíces de BEAM2AC_OUTPUT_ROOTS. Lo que rechaza, en este orden:

1. Las carpetas de muestras (sample_beamng, samples_beamng, sample_assetto, sample_assetto_unpacked).

1. Una lista cerrada de carpetas históricas o de producción sin sello.

1. Las instalaciones de los juegos y las carpetas de usuario de Assetto Corsa, BeamNG y Content Manager, y las rutas de BEAM2AC_PROTECTED_ROOTS.

1. Cualquier ruta fuera de las raíces permitidas.

1. Cualquier carpeta que contenga un manifest.json (sellada), salvo las que creó el propio proceso.

Además, el car_id de una exportación debe empezar por beam2ac_, y se rechaza si ya existe en content/cars de la instalación de AC (sin distinguir mayúsculas), salvo --allow-installed-car-id, que lo deja como aviso.

| Aviso de portabilidad: export.py contiene una ruta fija de la instalación de AC de la máquina original (KNOWN_AC_ROOTS). En otra máquina, pasa --ac-root o ajusta esa lista. |
|---|

# Pruebas y calidad

python -m pytest                    # pytest.ini: testpaths = tests

python -m unittest discover

python -m black src tests *.py

python -m flake8 src tests *.py

- Hay unos 140 archivos de prueba (unittest.TestCase y pytest) en tests/, con más de mil pruebas individuales.

- Las pruebas que necesitan herramientas externas están desactivadas por defecto: BEAM2AC_WEBVIEW_TESTS=1 (.venv-ui).

- La suite completa es larga y consume memoria; al tocar un módulo, lanza primero las pruebas dirigidas (por ejemplo tests/test_safe_paths.py).

- Las pruebas de capas (TestLayering) y de huellas de etapa (test_g28_stage_code) protegen las dos invariantes más importantes.

# Documentación del repositorio

- docs/README.md: índice de documentos de investigación; docs/PLAN.md: plan y criterios de aceptación.

- STATUS.md: estado vivo. CLAUDE.md: historial detallado de fases, útil pero largo y con partes superadas: ante dudas, manda el código.

- docs/research/: investigación por subsistema; consúltala antes de tocar uno.

- El proyecto no es un repositorio Git: la evidencia y las bases de análisis son locales y no deben redistribuirse.
