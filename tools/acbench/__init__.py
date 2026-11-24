"""G2.22: banco LSD autónomo en Assetto Corsa (CSP Custom AI + app Lua de registro).

Módulos:

* :mod:`shm` — memoria compartida con nombre de Windows (crear/abrir) y doble en memoria
  para pruebas;
* :mod:`cai` — estructuras de Custom AI de CSP (documentación oficial, tamaños fijados);
* :mod:`bridge` — puente propio con la app Lua ``beam2ac_lsd_bench`` (órdenes y telemetría);
* :mod:`control` — controladores de velocidad, radio y marcha;
* :mod:`fsm` — máquina de estados, vigilante y parada segura;
* :mod:`plan` — plan de pruebas determinista por variante;
* :mod:`analysis` — análisis corregido de los CSV (G2.21 y G2.22);
* :mod:`track` — generador del circuito de pruebas ``beam2ac_g222_pad``;
* :mod:`install` — instalación/retirada con manifiesto y copias de seguridad, lanzamiento de AC;
* :mod:`simac` — doble SINTÉTICO de AC para pruebas del controlador (nunca da datos de AC).

Nada de producción (``src/``) importa este paquete.
"""
