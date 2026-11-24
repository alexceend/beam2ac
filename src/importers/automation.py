"""Perfil de importación de vehículos exportados por Automation (sobre BeamNG).

Procedencia (2026-11-02, decisiones D-P1 y D-P2 de
``docs/research/procedencia_automation_2026-11-01.md``): un vehículo es una exportación
de Automation si y solo si su ``info.json`` declara **los tres** campos:

* ``Type == "Automation"``;
* ``Author == "Camshaft Software LTD"``;
* ``ExporterVersion`` reconocido: uno de ``KNOWN_EXPORTER_VERSIONS``. Es un
  identificador OPACO (texto, sin conversiones ni rangos): el corpus no establece su
  orden (informe §1.6).

Es el MISMO criterio que la pertenencia de la familia ``automation_export``
(``catalog/families.json``; lo comprueba ``tests/test_provenance_automation.py``) y el
que usa ``src.catalog.pairing``. No depende del motor ni de la caja: una exportación con
``combustionEngine`` o ``dctGearbox`` sigue siendo de Automation y su conversión se
bloquea en el adaptador que falta, con su motivo. Las señales estructurales
(``license.txt``, ``*.car``, piezas ``Camso_``, ``art/sound/engine/<hex>``) solo
corroboran (D-P2): no se exigen y no se leen aquí.

Lo propio del exportador que se importa: ``formats.exporter_version``
(``info.json`` → ``ExporterVersion``; cambia el redondeo de los valores declarados,
``validations_bng.TORQUE_DECLARED_BY_EXPORTER``). Se registra siempre que ``info.json``
lo declare (es un dato del origen, también si la versión no es reconocida). La versión
de ``camsoEngine.lua`` queda en ``formats.code_versions`` y su efecto (fila de gas
cerrado) en ``engine.coast_basis``. Lo genérico de BeamNG (pinturas, recursos) está en
``src.importers.resources``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from src.attrs import schema as S
from src.ir.param import Param
from src.ir.vehicle import VehicleIR

TYPE = "Automation"
AUTHOR = "Camshaft Software LTD"
# Versiones del exportador presentes en el corpus de referencia
# (alcance_automation_2026-11-01.md §3). Una versión nueva se añade aquí y en
# catalog/families.json a la vez, con evidencia; nunca por comparación numérica.
KNOWN_EXPORTER_VERSIONS: tuple[str, ...] = ("38022", "40340", "200409")


# Firmas de código del corpus: una versión DESCONOCIDA del exportador solo se trata
# como una conocida si TODO su código coincide con el de esa versión (G2.50). Nunca
# por orden numérico: 200409 es de otra compilación y no está entre 38022 y 40340.
ENGINE_LUA_VERSION: dict[str, str] = {
    "47437d5c0ef36243e4f9edf9409a23dc280d798d9cbb2543f62648ee234b14ed": "38022",
    "91392d703ac3d54a9c45dfa2fb6ba7ef1f87aa6df6c1e4e95f61a3763f8de8bb": "40340",
}
KNOWN_LUA: dict[str, frozenset[str]] = {
    "camsoEngineThermals": frozenset({"d429fe2e656be631becb16b30fd778c47ba6b13b5adf87a0e1e26d990dd13b1b"}),
    "camsoSupercharger": frozenset({"e87db29e228fd33cb4b270b125e4198ee7209c8097bb6d797155883b35d6c196"}),
    "camsoTurbocharger": frozenset({"1d2180f20745b7e8d4ad282711e84c7d478fd5b8f911b4af11a2f8a3d55adc76"}),
    "camso_activeDampers": frozenset({"20434a1912c026a600fa08dfabb22f00f4c0d4cf8fd814ad8fdbb688f327687a"}),
    "camso_activeSwaybars": frozenset({"a7b6fd3ad2730aa89926ffc828c92aa89bb6e27cff8a106060072e7a80c509e9"}),
    # controlador del reparto del diferencial central (±maxBiasOffset alrededor de diffTorqueSplitA);
    # sin equivalente en AC: el conversor usa el reparto base del JBeam
    "camso_advawd": frozenset({"9d20181f2ff32d8dc5f2f3544f5b318f8a0fa435df4367005cf714ffdeefa9a5"}),
    "cefaero": frozenset({"ffcfeed1d5fbdf008a2b1640f5f452ff7bbd7699bb072216b0ce2793886343eb"}),
}


def equivalent_known_version(
    source: Any, combustion: bool | None = None
) -> tuple[str | None, str]:
    """(versión conocida equivalente | None, motivo). Solo para un ``ExporterVersion``
    desconocido de un origen con Type y Author de Automation."""
    info = source.model_info
    if info.get("Type") != TYPE or info.get("Author") != AUTHOR:
        return None, "Type/Author no son los de Automation"
    if "ExporterVersion" not in info:
        return None, "sin ExporterVersion"
    declared = exporter_version_id(info["ExporterVersion"])
    if declared in KNOWN_EXPORTER_VERSIONS:
        return declared, "declarada y conocida"
    # por RUTA, no por nombre: dos Lua con el mismo nombre en carpetas distintas cuentan los dos
    lua = [(k.rsplit("/", 1)[-1].removesuffix(".lua"), v) for k, v in source.lua_sha256.items()]
    if not lua:  # sin Lua propio: familia 200409, solo con motor combustionEngine (marcador positivo)
        if combustion is True:
            return "200409", "sin Lua de vehículo y motor combustionEngine (como 200409)"
        return None, "sin Lua de vehículo y sin motor combustionEngine confirmado"
    engines = [d for n, d in lua if n == "camsoEngine"]
    if not engines or any(d not in ENGINE_LUA_VERSION for d in engines):
        bad = next((d for d in engines if d not in ENGINE_LUA_VERSION), "")
        return None, f"camsoEngine.lua ausente o desconocido (sha256 {bad[:12]})"
    versions = {ENGINE_LUA_VERSION[d] for d in engines}
    if len(versions) != 1:
        return None, "varios camsoEngine.lua de versiones distintas"
    for name, digest in lua:
        if name != "camsoEngine" and digest not in KNOWN_LUA.get(name, frozenset()):
            return None, f"{name}.lua desconocido o modificado (sha256 {digest[:12]})"
    return versions.pop(), "todo el Lua coincide con esa versión"


def exporter_version_id(value: Any) -> str:
    """Identificador de la versión, igual que se guarda en el IR (``str``)."""
    return str(value)


@dataclass(frozen=True)
class Provenance:
    """Resultado del criterio de procedencia sobre ``info.json``."""

    is_automation: bool
    exporter_version: str | None  # declarada (reconocida o no)
    failed: tuple[str, ...]  # criterios que no se cumplen, en texto


def provenance_of_info(model_info: Mapping[str, Any]) -> Provenance:
    failed = []
    if model_info.get("Type") != TYPE:
        failed.append(f"Type {model_info.get('Type')!r} != {TYPE!r}")
    if model_info.get("Author") != AUTHOR:
        failed.append(f"Author {model_info.get('Author')!r} != {AUTHOR!r}")
    version = None
    if "ExporterVersion" not in model_info:
        failed.append("sin ExporterVersion")
    else:
        version = exporter_version_id(model_info["ExporterVersion"])
        if version not in KNOWN_EXPORTER_VERSIONS:
            failed.append(
                f"ExporterVersion {version!r} no reconocido "
                f"(conocidos: {', '.join(KNOWN_EXPORTER_VERSIONS)})"
            )
    return Provenance(not failed, version, tuple(failed))


def is_automation_info(model_info: Mapping[str, Any]) -> bool:
    """¿``info.json`` declara una exportación de Automation? (D-P1).

    Solo mira ``info.json``: un ``ExporterVersion`` desconocido da False aquí aunque
    :func:`is_automation` (con el código del origen, G2.50) lo acepte por equivalencia."""
    return provenance_of_info(model_info).is_automation


def is_automation(source: Any, combustion: bool | None = None) -> bool:
    return (
        is_automation_info(source.model_info)
        or equivalent_known_version(source, combustion)[0] is not None
    )


def enrich_metadata(ir: VehicleIR, source: Any, combustion: bool | None = None) -> None:
    """Metadatos propios del exportador de Automation (sin leer archivos)."""
    if "ExporterVersion" in source.model_info:
        info_ref = S.EvidenceRef(
            type="file", path=source.description, locator="info.json"
        )
        declared = exporter_version_id(source.model_info["ExporterVersion"])
        ir.formats.exporter_version_declared = Param.of(
            declared, "text", "extracted", [info_ref], source_key="ExporterVersion"
        )
        effective, why = equivalent_known_version(source, combustion)
        if effective == declared or effective is None:
            ir.formats.exporter_version = Param.of(
                declared,
                "text",
                "extracted",
                [info_ref],
                note=""
                if effective == declared
                else f"versión desconocida, no equivalente a ninguna conocida: {why}",
                source_key="ExporterVersion",
            )
        else:  # versión desconocida pero con el código de una conocida (G2.50)
            ir.formats.exporter_version = Param.of(
                effective,
                "text",
                "computed",
                [info_ref],
                note=f"ExporterVersion declarada {declared} no está en la lista; "
                f"se trata como {effective}: {why}",
                source_key="ExporterVersion",
            )
