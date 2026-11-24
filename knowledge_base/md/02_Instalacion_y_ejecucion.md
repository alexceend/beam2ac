Instalación y ejecución

Requisitos y comandos para arrancar beam2ac

Los comandos se ejecutan desde la raíz del proyecto (en este documento, <RAIZ_DEL_PROYECTO>). El sistema de referencia es Windows.

# Requisitos

| Pieza | Para qué | Notas |
|---|---|---|

| Python 3.11 o superior | Todo el programa | El código principal usa solo la biblioteca estándar: requirements.txt no tiene dependencias de ejecución. |

| Blender (en el PATH o variable BEAM2AC_BLENDER) | Generar la malla .kn5 | Solo para la etapa de modelo 3D. El proyecto se ha usado con Blender 3.6; el código no comprueba la versión. Necesita además el complemento de exportación KN5 (assetto_corsa_tools). |

| Entorno .venv-ui | Ventana nativa de la aplicación | Opcional: sin él, la aplicación se abre en el navegador. |

| FMOD Studio 1.08.12 | Construir bancos de sonido | Opcional. Sin FMOD el programa prepara el proyecto de audio pero no construye el banco. |

| Assetto Corsa instalado (solo lectura) | Referencias, piloto 3D local | Opcional. Nunca se escribe en su carpeta. |

# Preparar el entorno

pip install -r requirements-dev.txt        # pytest, black, flake8

# Solo si quieres la ventana nativa:

python -m venv .venv-ui

.venv-ui\Scripts\python.exe -m pip install --require-hashes -r requirements-ui.txt

# Ejecutar la aplicación de escritorio

python -m src.app desktop                  # servidor local en http://127.0.0.1:8766

python -m src.app desktop --window         # abre la ventana nativa (requiere .venv-ui)

python -m src.app desktop --projects <CARPETA_DE_PROYECTOS>

- Sin --window el comando imprime la dirección; ábrela tú en el navegador. El servidor solo escucha en 127.0.0.1.

- Con --window se reutiliza el entorno .venv-ui; si no existe, el comando falla con un mensaje.

- Por defecto los proyectos viven en output/projects/.

| Limitación conocida: con --window, la opción --port se descarta sin aviso (solo se reenvía --projects). |
|---|

# Ejecutar desde la línea de comandos

| Quiero… | Comando |
|---|---|

| Analizar un coche sin escribir nada de física | python -m src.convert inspect <carpeta\|zip> --out output/<nueva> |

| Crear un proyecto | python -m src.project new --out output/projects/<nombre> --vehicle-dir <carpeta> --pc <archivo.pc>  (o --vehicle-zip <zip> [--common-zip <zip>]) |

| Listar las configuraciones de un zip | python -m src.project configs <zip> |

| Ejecutar las etapas | python -m src.project run <proyecto> [--with-model] |

| Ver el estado de las etapas | python -m src.project status <proyecto> |

| Exportar un paquete de AC | python -m src.project export <proyecto> [--car-id beam2ac_xxx] [--no-model] |

| Comprobar la integridad de una exportación | python -m src.project inspect <proyecto>/exports/001 |

| Ver el sonido de origen | python -m src.audio status <carpeta_vehiculo> |

| Pasar las pruebas | python -m pytest |

## Códigos de salida útiles

- export: 0 sin errores; 2 si el paquete tiene errores (veredicto «bloqueado»); 1 si falló el proyecto o el car_id (ProjectError/ValueError); un error de sintaxis de argumentos sale con 2.

- run: 0 si todas las etapas terminan o están en caché; 2 si alguna no.

- inspect: 0 si los archivos coinciden con el manifiesto; 3 si no.

# Dónde se escribe

Las salidas van a output/, a builds/, a la carpeta temporal o a las carpetas que se declaren en la variable BEAM2AC_OUTPUT_ROOTS. Cualquier otra ruta se rechaza (artículo 08). Las carpetas sample_* y las de los juegos nunca se escriben.
