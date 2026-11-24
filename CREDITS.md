# Créditos y licencias

## Licencia del proyecto
[Creative Commons Attribution-NonCommercial 4.0 (CC BY-NC 4.0)](LICENSE): puedes copiar, modificar y compartir el
proyecto citando al autor, pero no usarlo con fines comerciales.

## Componentes de terceros incluidos
| Componente | Dónde | Licencia |
|---|---|---|
| [three.js](https://threejs.org/) 0.160.0 | `src/app/static/vendor/three-0.160.0/` (con su `LICENSE` y `MANIFEST.json`) | MIT |
| Paleta [Catppuccin](https://github.com/catppuccin/catppuccin) | colores de la interfaz (Mocha en oscuro, Latte en claro) | MIT, «Copyright (c) 2021 Catppuccin»: solo se usan sus valores de color, con esta atribución |
| Iconos y logotipo «b2ac» / «beam2ac» | `src/app/static/icon.svg`, `src/app/static/logo.svg` | propios, bajo la licencia del proyecto |

La interfaz aplica ajustes propios a la paleta para cumplir contraste 4,5:1: `--na` en Mocha y, en Latte, los acentos
usados como texto (más oscuros) y los grises de texto secundario.

## Componentes de terceros que no se incluyen
- **`assetto_corsa_tools`** (Moppius, GPL-3.0): complemento de Blender para exportar KN5. La aplicación lo descarga de su
  origen solo si el usuario lo pide (Ajustes → Instalar complemento KN5; commit fijado, huella verificada). No se versiona.
- **Blender 3.6**, **FMOD Studio** y el SDK de audio de Assetto Corsa: los aporta el usuario.

## Marcas y obras de terceros
Automation – The Car Company Tycoon Game, BeamNG.drive y Assetto Corsa (Kunos Simulazioni) son marcas y obras de sus
titulares; este proyecto no está afiliado a ellos y no incluye ningún archivo suyo. Las exportaciones con piloto o manos
(`--local-driver`) contienen material derivado de Kunos y no se deben distribuir.
