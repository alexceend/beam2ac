# Política de seguridad

beam2ac es una herramienta local: su servidor escucha solo en `127.0.0.1` y no envía datos a ningún sitio.

**Cómo informar de una vulnerabilidad:** usa los avisos privados de GitHub (pestaña *Security* → *Report a vulnerability*)
en lugar de abrir una incidencia pública. Incluye pasos para reproducirla y la versión afectada.

Ámbito que interesa especialmente: lectura o escritura de archivos fuera de `output/`, peticiones entre sitios a la
interfaz local y el tratamiento de zips no confiables.
