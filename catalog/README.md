# Catálogo de atributos de beam2ac (fuente de verdad)

Formato y semántica: `docs/research/modelo_atributos.md`. Validar con `python -m src.attrs check`.

| Carpeta / archivo | Contenido | Cómo se cambia |
|---|---|---|
| `attributes/assettocorsa.json` | Atributos de AC con unidad documentada (o `undocumented`) | Editar con evidencia en `doc` |
| `families.json` | Familias con criterios de pertenencia comprobables | Editar con evidencia |
| `rules/<id>@<versión>.json` | Una regla por archivo. **Inmutable** | Nueva versión con `supersedes` (`src.attrs.store.write_rule`); nunca editar una publicada |
| `validations/<vehículo>.json` | Validaciones registradas | Añadir entradas |
| `decisions/<vehículo>.jsonl` | Decisiones manuales. **Solo se añade** | `append_decision`; revertir = otra decisión con `supersedes` |
| `targets/ac_physics.json` | Atributos que resuelve el motor | Editar |

Procedencia:
- **Sembrado:** `tools/catalog/seed_catalog.py`.
- **Decisiones del Ibiza:** importadas de la base física congelada por `tools/catalog/import_physics_base.py`.
- **Fase 5:** `tools/catalog/add_phase5_rules.py`.
- **Fase 6:** `tools/catalog/add_phase6_reference_evidence.py`.

Los scripts se niegan a sobrescribir lo que ya existe.

Las decisiones que se toman en la interfaz van a la carpeta del proyecto (`output/projects/<nombre>/decisions/`), no
aquí.
