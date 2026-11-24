"""Extractor ANTIGUO de la curva de torque (fase 1 original).

Se mantiene como referencia de regresión hasta que la fase 3 de docs/PLAN.md lo
sustituya. Errores conocidos (docs/baseline/engine_translation_baseline.md):
par bruto sin restar frictionTorque, fila throttle 100 en vez de 99, parseo con regex.
"""

import argparse
import os
import re

from src.safe_paths import DEFAULT_OUTPUT_DIR, ensure_safe_output_path

DEFAULT_JBEAM = (
    "./sample_beamng/vehicles/seat_ibiza_acfr/eng_83f4c/camso_engine_83f4c.jbeam"
)
DEFAULT_LUT_OUTPUT = DEFAULT_OUTPUT_DIR / "legacy" / "power_gross_thr100.lut"


def extract_real_torque_curve(jbeam_filepath, output_lut_path):
    # Falla antes de leer nada si la salida apunta a una muestra de referencia.
    output_lut_path = ensure_safe_output_path(output_lut_path)

    if not os.path.exists(jbeam_filepath):
        print(f"Error: El archivo {jbeam_filepath} no existe.")
        return

    with open(jbeam_filepath, "r", encoding="utf-8") as f:
        content = f.read()

    # Eliminar comentarios //...
    content = re.sub(r"//.*", "", content)

    # ---------------------------------------------------------
    # Buscar la tabla principal "torque"
    #
    # ["throttle", "rpm", "torque", "FuelUsed", "Pressure"]
    # ---------------------------------------------------------

    torque_start = re.search(
        r'"torque"\s*:\s*\[\s*' r'\[\s*"throttle"\s*,\s*"rpm"\s*,\s*"torque"', content
    )

    if not torque_start:
        print("No se encontró la tabla principal 'torque'.")
        return

    start = torque_start.end()

    # Encontramos el final de la tabla buscando "frictionTorque"
    friction_start = re.search(r'"frictionTorque"\s*:', content[start:])

    if not friction_start:
        print("No se encontró el final de la tabla 'torque'.")
        return

    end = start + friction_start.start()

    torque_table = content[start:end]

    # ---------------------------------------------------------
    # Buscar únicamente throttle = 100
    #
    # [100, RPM, TORQUE, FuelUsed, Pressure]
    # ---------------------------------------------------------

    row_regex = re.compile(
        r"\[\s*"
        r"100\s*,\s*"
        r"([0-9]+(?:\.[0-9]+)?)\s*,\s*"
        r"(-?[0-9]+(?:\.[0-9]+)?)\s*,"
    )

    extracted_points = []

    for match in row_regex.finditer(torque_table):
        rpm = float(match.group(1))
        torque = float(match.group(2))

        # Ignorar puntos absurdos / idle
        if rpm >= 500:
            extracted_points.append((int(rpm), round(torque, 2)))

    if not extracted_points:
        print("No se encontraron puntos para throttle = 100.")
        return

    # Ordenar por RPM
    extracted_points.sort(key=lambda x: x[0])

    # ---------------------------------------------------------
    # Crear directorio de salida
    # ---------------------------------------------------------

    output_dir = os.path.dirname(output_lut_path)

    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    # ---------------------------------------------------------
    # Escribir LUT
    # ---------------------------------------------------------

    with open(output_lut_path, "w", encoding="utf-8") as f:
        for rpm, torque in extracted_points:
            f.write(f"{rpm}|{torque}\n")

    print("¡Curva WOT encontrada correctamente!")

    print(f"Puntos exportados: {len(extracted_points)}")

    print(f"Rango: {extracted_points[0][0]} RPM " f"a {extracted_points[-1][0]} RPM")

    max_point = max(extracted_points, key=lambda x: x[1])

    print(f"Torque máximo: {max_point[1]} Nm " f"a {max_point[0]} RPM")

    return extracted_points


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Extractor antiguo de la curva WOT (par bruto, throttle 100)."
    )
    parser.add_argument("--jbeam", default=DEFAULT_JBEAM)
    parser.add_argument("--out", default=str(DEFAULT_LUT_OUTPUT))
    args = parser.parse_args(argv)
    return extract_real_torque_curve(args.jbeam, args.out)


if __name__ == "__main__":
    main()
