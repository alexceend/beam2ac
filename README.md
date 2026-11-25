<h1 align="center">
  <img src="src/app/static/logo.svg" alt="beam2ac" width="420">
</h1>

<p align="center">
  <b>Convierte coches de Automation – The Car Company Tycoon Game, exportados a BeamNG.drive, en coches de Assetto Corsa: física (motor, transmisión, suspensión, neumáticos, aerodinámica), mallas y KN5, en Windows y Linux.</b>
</p>

<p align="center">
  <a href="https://github.com/alexceend/beam2ac/releases/latest"><img src="https://img.shields.io/github/v/release/alexceend/beam2ac?label=latest&color=cba6f7&labelColor=1e1e2e" alt="Última versión"></a>
  <a href="https://github.com/alexceend/beam2ac/releases"><img src="https://img.shields.io/github/downloads/alexceend/beam2ac/total?label=all%20downloads&color=94e2d5&labelColor=1e1e2e" alt="Descargas"></a>
  <img src="https://img.shields.io/badge/platform-Windows%2010%2F11-0078d6?logo=windows&logoColor=white&labelColor=1e1e2e" alt="Plataforma: Windows 10/11">
  <img src="https://img.shields.io/badge/platform-Linux%20x64%20%7C%20arm64-e95420?logo=linux&logoColor=white&labelColor=1e1e2e" alt="Plataforma: Linux x64 y arm64">
  <a href="https://github.com/alexceend/beam2ac/commits/main"><img src="https://img.shields.io/github/last-commit/alexceend/beam2ac/main?label=last%20commit&color=89b4fa&labelColor=1e1e2e" alt="Último commit"></a>
  <a href="https://github.com/alexceend/beam2ac/issues"><img src="https://img.shields.io/github/issues/alexceend/beam2ac?label=open%20issues&color=f38ba8&labelColor=1e1e2e" alt="Issues abiertos"></a>
</p>

<p align="center">
  <a href="https://github.com/alexceend/beam2ac/releases/latest"><img src="https://img.shields.io/badge/%E2%AC%87_Download-latest_for_Windows-cba6f7?style=for-the-badge&labelColor=1e1e2e" alt="Descargar para Windows"></a>
  <a href="https://github.com/alexceend/beam2ac/releases/latest"><img src="https://img.shields.io/badge/%E2%AC%87_Download-latest_for_Linux_(x64_%2F_arm64)-e95420?style=for-the-badge&labelColor=1e1e2e" alt="Descargar para Linux"></a>
</p>

<p align="center">
  <b><a href="https://github.com/alexceend/beam2ac/releases">Releases</a></b> ·
  <a href="CREDITS.md">Credits</a> ·
  <a href="SECURITY.md">Security</a> ·
  <a href="LICENSE">License</a> ·
  <a href="https://github.com/alexceend/beam2ac/issues">Issues</a>
</p>

> [!IMPORTANT]
> **No se incluye ningún archivo de los juegos.** Necesitas tus propias copias de Automation, BeamNG.drive y Assetto Corsa;
> el programa solo lee los archivos que ya tienes. Ver [Uso legal](#uso-legal).

> [!NOTE]
> **Comentario del autor**
> Siento que es importante este comentario antes de explicar el proyecto así que lo pongo arrriba en el readme :)
>
> Hola, soy alex, no sé si debería considerarme AUTOR del proyecto ya que, en gran medida, esto ha sido posible gracias a la ayuda de herramientas de IA. Mi labor en este proyecto ha sido mayoritariamente de diseño y de ingeniería inversa para poder obtener la forma que tiene cada motor (BeamNG y Kunos) de representar las físicas para así relacionarlas y transformarlas con beam2ac. Mis conocimientos en física y mecánica son básicos, por lo que la labor de TRANSFORMACIÓN de físicas y archivos no debería atribuirse a mi autoría.
> Sin embargo en el proyecto no he estado solo. Agradezco enormemente la ayuda de amigos con conocimientos en mecánica y a Salva por haberme ayudado a detectar fallos y elementos a refinar.
>
> Dicho esto, si teneis interés en más proyectos míos podeis visitar [mi GitHub](https://github.com/alexceend) y si alguien quiere apoyarlos os dejo el link a mi Ko-Fi.
> Muchas gracias por leer este tostón!
>
> [![Ko-fi](https://img.shields.io/badge/Ko--fi-FF5E5B?style=for-the-badge&logo=ko-fi&logoColor=white)](https://ko-fi.com/alexceend)

## Características

### Del coche de Automation al de Assetto Corsa
Lee la exportación de Automation (carpeta o `.zip` de BeamNG, sin extraerla), resuelve la configuración completa del coche y
genera los archivos de AC. Cubre las variantes de las versiones del exportador de Automation (38022, 40340, 200409).

### Física medida, no inventada
Masa, centro de gravedad y puntos de suspensión se **miden en la geometría** del coche. Motor, caja, diferencial,
neumáticos, frenos y aerodinámica se traducen con reglas de un catálogo, cada una con su procedencia. Lo que no se puede
resolver queda como bloqueo explícito, nunca como un 0 silencioso.

### Mallas y KN5
Con Blender 3.6 en segundo plano, importa las mallas del coche, aplica las transformaciones, duplica las ruedas, nombra los
objetos como espera AC (`WHEEL_LF`, `SUSP_LF`…) y exporta el `.kn5`.

### Aplicación de escritorio
Varios proyectos, etapas con caché, decisiones que tú confirmas, exportación con confirmación, resultados y vista 3D
esquemática. El servidor es local (`127.0.0.1`).

### Proyectos y exportaciones inmutables
Cada etapa guarda su resultado por huella y cada exportación va en una carpeta nueva, sellada con un `manifest.json`.
Nunca se sobrescribe un paquete ni se instala en Assetto Corsa sin que lo pidas.

## Inicio rápido

### Qué necesitas en tu equipo
Se configuran en la pestaña **Ajustes** de la aplicación (o con variables `BEAM2AC_*`):

| Ajuste | Para qué | Variable |
|---|---|---|
| Carpeta de Assetto Corsa | pose del piloto, comprobaciones y, **para construir el banco de sonido, la plantilla `sdk/audio/ac_fmod_sdk_1_9`** que trae el juego | `BEAM2AC_AC_ROOT` |
| BeamNG.drive / carpeta de mods | leer las exportaciones de Automation (zip) y los bancos de medida | `BEAM2AC_BEAMNG_ROOT`, `BEAM2AC_BEAMNG_MODS` |
| Blender 3.6 | mallas y KN5. El importador de Collada (`.dae`) ya viene con Blender | `BEAM2AC_BLENDER` |
| Complemento de Blender `assetto_corsa_tools` | **obligatorio, además de Blender**: exporta el `.kn5`. De terceros (Moppius, GPL-3.0): en **Ajustes → Instalar complemento KN5** la app lo descarga del origen (commit fijado, huella verificada). No se incluye | `BEAM2AC_BLENDER_ADDONS` (carpeta) |
| FMOD Studio 1.08.12 (opcional) | generar el banco de sonido | `BEAM2AC_FMOD_STUDIO` |

### Windows
1. Descarga `beam2ac-windows-x64.zip` de [Releases](https://github.com/alexceend/beam2ac/releases/latest).
2. Descomprime **toda** la carpeta en un sitio con permiso de escritura. No hay instalador.
3. Abre `beam2ac.exe`.
4. En **Ajustes** indica las rutas de Assetto Corsa, BeamNG y Blender.
5. Crea un proyecto desde el `.zip` de Automation y ejecútalo.

Requiere el *Microsoft Edge WebView2 Runtime*, que ya viene en Windows 11 y en Windows 10 actualizado.

### Linux (x64 y arm64)
```
tar xzf beam2ac-linux-x64.tar.gz     # o beam2ac-linux-arm64.tar.gz
./beam2ac/beam2ac
```
Arranca el servidor local y abre tu navegador en `http://127.0.0.1:8766/`. Si no se abre solo, entra a esa dirección a mano.
No necesita GTK, Qt ni otras dependencias. Assetto Corsa y Blender se usan normalmente en Windows o con Proton: indica las
rutas en Ajustes.

### Desde el código fuente
```
pip install -r requirements-dev.txt
python -m src.app desktop            # aplicación (--open abre el navegador)
python -m src.project new|run|export|status|builds
```
La ventana nativa de Windows usa un entorno aparte (`requirements-ui.txt`, pywebview). Pruebas: `python tools/run_tests.py habitual`.

## Actualizar
Descarga la versión nueva y descomprímela en otra carpeta o encima de la anterior. Tus proyectos y ajustes viven fuera de la
carpeta del programa (ver [Dónde se guardan](#dónde-se-guardan-tus-proyectos-y-ajustes)), así que no se pierden.

## Problemas habituales
- **Windows: la ventana no abre.** Falta el WebView2 Runtime; instálalo desde Microsoft.
- **Windows: aviso de SmartScreen.** El ejecutable no está firmado.
- **Linux: no se abre el navegador.** Abre `http://127.0.0.1:8766/` a mano.
- **La exportación sale «bloqueada».** Falta una decisión tuya o un dato que el coche no aporta; el informe
  (`export_report.md`) dice cuál.
- **Sin Blender o sin su complemento KN5, no hay mallas.** Indica la ruta de Blender 3.6 en Ajustes y pulsa **Instalar complemento KN5**.

## Dónde se guardan tus proyectos y ajustes
| | Windows | Linux |
|---|---|---|
| Proyectos y exportaciones | `%LOCALAPPDATA%\beam2ac` | `~/beam2ac` |
| Ajustes | `%APPDATA%\beam2ac` | `~/.config/beam2ac` |

`BEAM2AC_DATA_DIR` cambia la carpeta de datos. La carpeta del programa es de solo lectura.

## Qué no está en este repositorio
Carpetas de muestras (`sample_*`, `samples_beamng/`), `output/`, `builds/`, el material de referencia de Kunos
(`docs/references/`), las notas de investigación y las medidas de coches de terceros. Parte de las pruebas leen esos
archivos y se omiten o fallan sin ellos. **No se incluyen complementos ni programas de terceros** (Blender, FMOD Studio,
el complemento `assetto_corsa_tools`, Assetto Corsa, BeamNG.drive): los aporta el usuario, o la aplicación descarga el
complemento de su origen solo si el usuario lo pide. El `.gitignore` es una lista blanca: nada de terceros se versiona.

## Seguridad y privacidad
- El servidor de la aplicación escucha **solo en `127.0.0.1`** y rechaza peticiones POST de otros orígenes (`src/app/post_guard.py`).
- No hay telemetría, analítica ni recursos de terceros en la interfaz (three.js va incluido, sin CDN).
- La única conexión de red es opcional y a petición: descargar el complemento KN5 de Blender (commit fijado, huella verificada).
- Todo se lee de los archivos del usuario y se escribe en la carpeta de datos; nunca en las instalaciones de AC o BeamNG.
- Vulnerabilidades: ver [SECURITY.md](SECURITY.md).

## Informar de problemas
Abre un [issue](https://github.com/alexceend/beam2ac/issues) indicando el sistema operativo, la versión de beam2ac y, si
puedes, el `export_report.md` de la exportación. **No subas archivos de los juegos ni coches de terceros.**

## Uso legal
Necesitas tus propias copias de Automation, BeamNG.drive y Assetto Corsa. Un coche exportado con piloto o manos
(`--local-driver`) contiene material derivado de Kunos y no se debe distribuir; el modo por defecto no lo incluye.
Este repositorio no incluye modelos, sonidos ni datos de esos juegos.

## Créditos y licencia
Proyecto bajo [CC BY-NC 4.0](LICENSE): puedes copiarlo, modificarlo y compartirlo citando al autor, pero no usarlo con
fines comerciales. Los componentes de terceros (three.js, la paleta Catppuccin, el complemento de Blender que se descarga
a petición) y sus licencias están en **[CREDITS.md](CREDITS.md)**. Automation, BeamNG.drive y Assetto Corsa son marcas y
obras de sus titulares; este proyecto no está afiliado a ellos.
