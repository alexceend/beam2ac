"""Masa del coche en conflicto con la ficha de Automation: la decide el usuario.

La masa que mide el conversor en los nodos puede no coincidir con el ``Weight`` de ``info_<config>.json``
(validación ``<id>.<config>.mass`` fallida). El motor marca entonces como ``ambiguous`` toda clave que depende de
ella (``TOTALMASS``, CdG, ``BASEY``, inercia) y la exportación se bloquea. En vez de aflojar la validación, la
interfaz avisa y el usuario elige (``mass_choice``):

* ``measured``: continuar con la masa calculada de los nodos;
* ``declared``: usar el ``Weight`` que declara Automation;
* un número (kg, coche sin piloto): una masa a mano.

La elección se guarda como DECISIONES del proyecto (con su justificación y procedencia), no como excepción
oculta: cada clave afectada acepta su valor actual (``select_reference``) y ``TOTALMASS`` se desplaza por la
diferencia entre la masa elegida y la calculada (``manual_value``). Límite: el CdG y la inercia no cambian (la
diferencia se trata como masa situada en el CdG); es razonable para unos kg, no para decenas.
"""

from __future__ import annotations

import datetime as _dt
import re
from typing import Any

from src.attrs import schema as S

from .workspace import WorkspaceError

TOTALMASS = "ac:car.ini/BASIC/TOTALMASS"
AUTHOR = "usuario (interfaz: conflicto de masa)"


def parse_choice(raw: Any) -> dict[str, Any] | None:
    """``measured`` | ``declared`` | kg (número) → ``{"mode": …[, "kg": …]}``; vacío → ``None``."""
    if raw is None or str(raw).strip() in ("", "none"):
        return None
    text = str(raw).strip().lower()
    if text in ("measured", "declared"):
        return {"mode": text}
    try:
        kg = float(text.replace(",", "."))
    except ValueError:
        raise WorkspaceError(
            "elección de masa: «measured», «declared» o una masa en kg"
        ) from None
    if not 0 < kg < 100_000:
        raise WorkspaceError(
            "elección de masa: debe ser un número de kg entre 0 y 100000"
        )
    return {"mode": "manual", "kg": kg}


def conflict(session: Any, results: list[Any]) -> dict[str, Any] | None:
    """El conflicto de masa que hoy deja claves ``ambiguous`` (``None`` si no hay).

    Lee las validaciones de la ficha con ``info_validations`` y las cruza con las claves que el motor ha
    rebajado por ELLAS (``confidence_criteria``): si una ``in_game_value`` superada ya las cubre, no hay
    conflicto que decidir."""
    from src.attrs.validations_bng import info_validations

    ir = session.ir
    vid, variant = ir.identity.vehicle_id.value, ir.identity.variant.value
    bad = [
        v
        for v in info_validations(vid, variant, session.facts())
        if v.id.endswith(".mass") and v.result == "fail"
    ]
    if not bad:
        return None
    vid_mass = bad[0].id
    keys = [
        r
        for r in results
        if r.status == "ambiguous"
        and r.value is not None
        and any(
            "validación fallida" in c and vid_mass in c for c in r.confidence_criteria
        )
    ]
    if not keys:
        return None
    declared, measured = float(bad[0].expected), float(bad[0].observed)
    return {
        "validation": vid_mass,
        "declared_kg": declared,
        "measured_kg": measured,
        "difference_kg": round(measured - declared, 2),
        "keys": [r.target for r in keys],
        "_results": keys,
    }


def decisions(
    session: Any, found: dict[str, Any], choice: dict[str, Any]
) -> list[S.Decision]:
    """Decisiones que materializan ``choice`` sobre las claves en conflicto de ``found``."""
    ir = session.ir
    vid, variant = ir.identity.vehicle_id.value, ir.identity.variant.value
    declared, measured = found["declared_kg"], found["measured_kg"]
    mode = choice["mode"]
    chosen = {"measured": measured, "declared": declared}.get(mode, choice.get("kg"))
    label = {
        "measured": f"continuar con la masa calculada ({measured:g} kg)",
        "declared": f"usar la masa declarada por Automation ({declared:g} kg)",
        "manual": f"masa introducida a mano ({chosen:g} kg)",
    }[mode]
    why = (
        f"Conflicto de masa: calculada {measured:g} kg frente a {declared:g} kg declarados por Automation "
        f"(Δ {measured - declared:+.2f} kg). Elección del usuario: {label}."
    )
    today = _dt.date.today().isoformat()
    out = []
    for r in found["_results"]:
        slug = re.sub(r"[^a-z0-9]+", "_", r.target.lower()).strip("_")
        ref = {"rule": r.rule, "value": r.value, "unit": r.unit}
        kw: dict[str, Any] = {
            "action": "select_reference",
            "value": r.value,
            "unit": r.unit,
            "reference": ref,
        }
        if r.target == TOTALMASS and mode != "measured":
            kw = {
                "action": "manual_value",
                "unit": r.unit,
                "value": round(r.value + (chosen - measured), 6),
            }
        out.append(
            S.Decision(
                id=f"dec.{vid}.{variant}.masa_{slug}",
                target=r.target,
                vehicle=vid,
                variant=variant,
                justification=why,
                author=AUTHOR,
                date=today,
                scope=S.Scope(level="variant", vehicle=vid, variant=variant),
                status="provisional",
                evidence=[
                    S.EvidenceRef(
                        type="report", path="interfaz", locator="conflicto de masa"
                    )
                ],
                history=[
                    S.HistoryEntry(
                        date=today,
                        author=AUTHOR,
                        action=kw["action"],
                        reason=why,
                        status_to="provisional",
                    )
                ],
                **kw,
            )
        )
    return out


def public(found: dict[str, Any], choice: dict[str, Any] | None) -> dict[str, Any]:
    """Lo que ve el cliente: el conflicto sin objetos internos y la elección aplicada."""
    return {
        **{k: v for k, v in found.items() if not k.startswith("_")},
        "choice": choice,
    }


def choice_from_justification(text: str) -> dict[str, Any] | None:
    """La elección (``parse_choice``) que escribió ``decisions()`` en su justificación; ``None`` si no es de masa."""
    if "usar la masa declarada" in text:
        return {"mode": "declared"}
    if "continuar con la masa calculada" in text:
        return {"mode": "measured"}
    m = re.search(r"masa introducida a mano \(([\d.]+) kg\)", text)
    return {"mode": "manual", "kg": float(m[1])} if m else None


def rederive(project_root: Any, source: Any, driver_mass: float) -> list[S.Decision]:
    """Decisiones de masa del proyecto recalculadas para otra masa del piloto: misma elección del usuario, valores
    nuevos, cada una sustituye (``supersedes``) a la vigente de su clave. Lista vacía si el proyecto no tiene."""
    from src.attrs.store import REPO_CATALOG, Catalog

    from . import diagnostics

    cat = Catalog.load(REPO_CATALOG, project_root)
    mine = [d for d in cat.decisions if d.author == AUTHOR]
    old = {}
    for d in mine:
        if not any(x.supersedes == d.id for x in mine):
            old[d.target] = d  # la vigente de cada clave (nadie la sustituye)
    if not old:
        return []
    choice = choice_from_justification(next(iter(old.values())).justification)
    if choice is None:
        raise WorkspaceError("no se pudo leer la elección de masa guardada en el proyecto")
    r = diagnostics.inspect_vehicle(
        source.path,
        source.pc,
        driver_mass,
        source.common_zip,
        mass_choice=str(choice.get("kg")) if choice["mode"] == "manual" else choice["mode"],
    )
    out = []
    for d in map(lambda x: S.from_dict(x), r["mass_decisions"]):
        prev = old.get(d.target)
        if prev is None:
            continue
        n = sum(1 for x in cat.decisions if x.id.startswith(prev.id.split(".v")[0]))
        d.id, d.supersedes = f"{prev.id.split('.v')[0]}.v{n}", prev.id
        out.append(d)
    return out
