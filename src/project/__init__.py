"""Proyectos de conversión y orquestador de etapas (fase 3 del plan de la aplicación).

Un proyecto es una carpeta bajo ``output/`` con:

* ``project.json``: vehículo de origen (carpeta, o zip con ``common.zip``),
  configuración ``.pc`` y opciones; esquema ``beam2ac.project/1``;
* ``decisions/<vehículo>.jsonl``: decisiones de atributos, la MISMA disposición que usa
  la interfaz de atributos (``python -m src.app serve --project``);
* ``stages/<etapa>/<clave>/``: resultados inmutables de cada etapa, identificados por la
  huella de sus entradas (``stage.json`` + archivos);
* ``runs.jsonl``: diario de ejecuciones (añadir, nunca reescribir).

Las etapas envuelven código existente (vista previa de física, motor de atributos, plan
de mallas, validador) y no cambian su comportamiento. Ver ``orchestrator.py``.
"""
