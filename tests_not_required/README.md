# tests_not_required/ — pruebas apartadas de la ejecución habitual

Nada se ha borrado: cada prueba está en UNA sola ubicación (aquí o en `tests/`). Pytest no las
descubre por defecto (`pytest.ini: testpaths = tests`).

| Nivel | Comando | Contenido |
|---|---|---|
| 1 habitual | `python tools/run_tests.py habitual` (o `python -m pytest`) | `tests/` sin `@pytest.mark.ampliada` |
| 2 ampliada | `python tools/run_tests.py ampliada` | `tests/` completo (+ barridos de corpus `ampliada`) |
| 3 completa | `python tools/run_tests.py completa` | `tests/` + `tests_not_required/` (todo) |

Solo las apartadas: `python -m pytest tests_not_required -m ""`.

Clasificación, motivos y cobertura que se conserva o desplaza: `docs/research/g248_niveles_de_tests_2026-11-14.md`.
Procedencia: la cabecera de cada archivo indica de qué `tests/<archivo>.py` y de qué clase/método sale; los
helpers se importan de `tests.<archivo>` (no se duplican).
