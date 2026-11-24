# beam2ac

Conversor de coches de **Automation – The Car Company Tycoon Game**, exportados a **BeamNG.drive**, a coches de
**Assetto Corsa** (Python 3.11+, Blender 3.6 para las mallas). El programa lee los archivos que el usuario ya tiene;
este repositorio no incluye ningún archivo de Automation, BeamNG, Assetto Corsa ni Kunos.

## Qué necesitas en tu equipo
Se configuran en la pestaña **Ajustes** de la aplicación (o con variables `BEAM2AC_*`):

| Ajuste | Para qué | Variable |
|---|---|---|
| Carpeta de Assetto Corsa | pose del piloto, comprobaciones y, **para construir el banco de sonido, la plantilla `sdk/audio/ac_fmod_sdk_1_9`** que trae el juego | `BEAM2AC_AC_ROOT` |
| BeamNG.drive / carpeta de mods | leer las exportaciones de Automation (zip) y los bancos de medida | `BEAM2AC_BEAMNG_ROOT`, `BEAM2AC_BEAMNG_MODS` |
| Blender 3.6 | mallas y KN5. Complemento `assetto_corsa_tools` (Moppius, GPL-3.0): en **Ajustes → Instalar complemento KN5** la app lo descarga del origen (commit fijado, huella verificada) y no lo incluye | `BEAM2AC_BLENDER` |
| FMOD Studio 1.08.12 (opcional) | generar el banco de sonido | `BEAM2AC_FMOD_STUDIO` |

## Uso
```
pip install -r requirements-dev.txt
python -m src.app desktop
python -m src.project new|run|export|status|builds
```

## Qué no está en este repositorio
Carpetas de muestras (`sample_*`, `samples_beamng/`), `output/`, `builds/`, el material de referencia de Kunos
(`docs/references/`) y las notas de investigación. Parte de las pruebas leen esos archivos y se omiten o fallan sin ellos.
Tampoco se incluye el complemento de Blender `assetto_corsa_tools` (de terceros, GPL-3.0): la aplicación lo descarga de su
origen solo si el usuario lo pide (Ajustes → Instalar complemento KN5). El `.gitignore` es una lista blanca: nada de
terceros se versiona.

## Seguridad y privacidad
- El servidor de la aplicación escucha **solo en `127.0.0.1`** y rechaza peticiones POST de otros orígenes (`src/app/post_guard.py`).
- No hay telemetría, analítica ni recursos de terceros en la interfaz (three.js va incluido, sin CDN).
- La única conexión de red es opcional y a petición: descargar el complemento KN5 de Blender (commit fijado, huella verificada).
- Todo se lee de los archivos del usuario y se escribe bajo `output/`; nunca en las instalaciones de AC o BeamNG.
- Vulnerabilidades: ver [SECURITY.md](SECURITY.md).

## Uso legal
Necesitas tus propias copias de Automation, BeamNG.drive y Assetto Corsa. Un coche exportado con piloto o manos
(`--local-driver`) contiene material derivado de Kunos y no se debe distribuir; el modo por defecto no lo incluye.
Este repositorio no incluye modelos, sonidos ni datos de esos juegos.

## Interfaz y créditos
La interfaz usa la paleta [Catppuccin](https://github.com/catppuccin/catppuccin) (Mocha en oscuro, Latte en claro; con ajustes
propios para cumplir contraste 4,5:1: `--na` en Mocha y, en Latte, los acentos usados como texto (más oscuros) y los
grises de texto secundario). Catppuccin se distribuye con licencia MIT,
«Copyright (c) 2021 Catppuccin»: aquí solo se usan sus valores de color, con esta atribución. three.js (MIT) se incluye en
`src/app/static/vendor/three-0.160.0/` con su licencia. El icono «b2ac» es propio.

## Licencia
[Creative Commons Attribution-NonCommercial 4.0 (CC BY-NC 4.0)](LICENSE): puedes copiar, modificar y compartir el
proyecto citando al autor, pero no usarlo con fines comerciales. `src/app/static/vendor/three-0.160.0/` conserva su
licencia MIT. Automation, BeamNG.drive y Assetto Corsa son marcas y obras de sus titulares; este proyecto no está
afiliado a ellos.
