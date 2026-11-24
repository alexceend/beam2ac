"""Genera ``pipelines_contratos.md`` a partir de ``src/pipeline/contracts.py``.

    python tools/analysis/render_pipeline_contracts.py --out output/<nueva>

Escribe ``pipelines_contratos.md`` en la carpeta nueva (vía ``safe_paths``); copiarlo a
``docs/research/`` es un paso manual. Las herramientas externas se detectan solo por su
ruta (``tool_status``); nada se ejecuta.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.pipeline.contracts import (  # noqa: E402
    PIPELINES,
    PipelineSpec,
    available_operations,
    tool_status,
)
from src.safe_paths import ensure_safe_output_path, new_output_dir  # noqa: E402

HEADER = """# Contratos de los pipelines de conversión ({day})

Fase 8. **Generado a partir de `src/pipeline/contracts.py`** (`PIPELINES`)
con `tools/analysis/render_pipeline_contracts.py`: si cambia un contrato, se cambia
allí y se vuelve a generar este documento. Pruebas: `tests/test_pipeline_contracts.py`.

Herramientas externas: se detectan **solo por su ruta** (`tool_status`); nunca se
ejecutan. En esta máquina ({day}):

{tools}

El audio es el único pipeline opcional: sin FMOD 1.08.12 solo deja de estar
disponible la operación `build` (compilar bancos). `none` y `prebuilt` (copiar un
banco ya compilado) no lo necesitan, y ningún otro pipeline depende de FMOD
(`audio_capabilities`, probado con FMOD simulado como ausente).

Adaptadores aislados, probados sin modificar paquetes:
- `src/pipeline/physics_preview.py`: física en memoria;
- `geometry_plan_summary`: plan de escena sin Blender (carpeta o zip);
- `src/extract/engine_combustion.py`: motor `combustionEngine` de solo lectura;
- `detect_fmod`/`audio_capabilities`;
- `src/attrs/candidates_dwb.py`: puntos `DWB` candidatos.
"""


def _join(items: tuple[str, ...] | list[str]) -> str:
    return "; ".join(items) if items else "—"


def render_spec(s: PipelineSpec, tools: dict) -> str:
    deps = "; ".join(
        f"`{op}`: {', '.join(need) if need else 'ninguna'}"
        for op, need in s.tools.items()
    )

    def op_state(v: dict) -> str:
        return "sí" if v["available"] else f"no (falta {', '.join(v['missing'])})"

    ops = "; ".join(
        f"`{op}` {op_state(v)}" for op, v in available_operations(s, tools).items()
    )
    title = f"## {s.title} (`{s.id}`)" + (" — opcional" if s.optional else "")
    lines = [
        title,
        "",
        f"- **Módulos:** {', '.join(f'`{m}`' for m in s.modules)}",
        f"- **Entrada principal:** `{s.entry}`",
        f"- **Entradas:** {_join(s.inputs)}",
        f"- **Salidas:** {_join(s.outputs)}",
        f"- **Dependencias externas por operación:** {deps}",
        f"- **Atributos:** {_join(s.attributes)}",
        f"- **Transformaciones:** {_join(s.transforms)}",
        f"- **Validaciones:** {_join(s.validations)}",
        f"- **Limitaciones:** {_join(s.limitations)}",
        f"- **Independiente:** {'sí' if s.standalone else 'no'} · **repetible:** "
        f"{s.repeatable} · **escribe:** {s.writes}",
        f"- **Operaciones disponibles ahora:** {ops}",
    ]
    return "\n".join(lines)


def render(day: str | None = None) -> str:
    tools = tool_status()
    tool_lines = "\n".join(
        f"- `{name}`: {'disponible' if t.get('available') else 'no disponible'} "
        f"({t.get('path')})"
        for name, t in tools.items()
    )
    head = HEADER.format(day=day or date.today().isoformat(), tools=tool_lines)
    return head + "\n" + "\n\n".join(render_spec(s, tools) for s in PIPELINES) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    out = new_output_dir(args.out)
    target = ensure_safe_output_path(out / "pipelines_contratos.md")
    target.write_text(render(), encoding="utf-8")
    print(target)
    return 0


if __name__ == "__main__":
    sys.exit(main())
