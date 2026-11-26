beam2ac: índice y glosario

Punto de entrada de la base de conocimiento

Esta carpeta explica beam2ac a dos tipos de lector: quien quiere usar la aplicación para convertir un coche, y quien quiere entender cómo está hecha por dentro para mantenerla o ampliarla. No hace falta saber de mecánica: se cuenta qué hace cada pieza y por qué, sin entrar en las fórmulas físicas.

# Qué artículo leer

| Artículo | Para quién | Contenido |
|---|---|---|

| 01 · Qué es beam2ac | Todos | Qué problema resuelve, qué entra y qué sale, diagrama general. |

| 02 · Instalación y ejecución | Todos | Requisitos y comandos para arrancar la app y la línea de comandos. |

| 03 · Guía de la aplicación de escritorio | Usuario | Pantalla por pantalla: analizar, convertir, revisar, editar la cabina. |

| 04 · Qué hace en cada caso | Usuario | Versiones de exportación, veredictos, bloqueos y qué hacer ante cada uno. |

| 05 · Arquitectura por capas | Desarrollador | Paquetes, responsabilidades y reglas para no mezclar capas. |

| 06 · Proyectos, etapas y exportación | Desarrollador | Etapas, caché por huella, carpetas de salida y builds/. |

| 07 · Catálogo y motor de reglas | Desarrollador | Cómo se decide cada valor del coche de Assetto Corsa. |

| 08 · Mallas, audio, seguridad y pruebas | Desarrollador | KN5 en Python, banco de sonido, guardián de escritura y tests. |

# Glosario mínimo

| Término | Significado |
|---|---|

| Automation | Videojuego (Automation – The Car Company Tycoon Game) donde se diseñan los coches de origen. |

| BeamNG.drive | Simulador al que Automation exporta el coche. Aquí es solo el formato intermedio: lo que se lee. |

| Assetto Corsa (AC) | Simulador de destino. El resultado es una carpeta de coche para AC. |

| JBeam | Archivos de texto (casi JSON) de BeamNG que describen las piezas del coche. |

| Exportador / versión del exportador | Programa de Automation que genera el coche para BeamNG. Hay varias versiones (38022, 40340, 200409) y cada una escribe cosas ligeramente distintas. |

| IR (representación intermedia) | Modelo neutral interno: lista de parámetros del coche, cada uno con unidad, estado y de dónde salió. |

| Catálogo | Carpeta catalog/ con las reglas que dicen qué valor de AC corresponde a cada dato de origen. |

| Regla / decisión / propuesta | Una regla se aplica sola; una decisión es una elección humana registrada; una propuesta es una regla de otro tipo de coche que se calcula pero no se aplica sin aprobación. |

| Bloqueo | Dato de AC que no se pudo resolver con seguridad. Nunca se rellena con 0 en silencio. |

| KN5 | Formato de malla 3D de Assetto Corsa. |

| INI / LUT | Archivos de configuración (INI) y tablas de curvas (LUT) que AC lee de la carpeta data/. |

| car_id | Nombre de carpeta del coche en AC. Siempre empieza por beam2ac_. |

| Intento de exportación | Cada ejecución de exportar crea exports/NNN/; nunca se sobrescribe una anterior. |

| Huella | Resumen criptográfico (hash) de las entradas; sirve para saber si algo cambió y reutilizar resultados. |

| Lectura honesta del estado: el proyecto genera paquetes de coche coherentes y trazables. Varios coches de prueba se han conducido en Assetto Corsa con un banco automático y han funcionado, pero la mayoría de las exportaciones nunca se han probado en el juego. «Exportación sin errores» no significa «coche que funciona». |
|---|
