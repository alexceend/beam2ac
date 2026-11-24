"""Formato de los INI generados para Assetto Corsa.

Observado en el juego (fase 7.1, docs/research/ensamblado.md §9.3): el lector INI
de AC trata un texto ``[SECCIÓN]`` dentro de una línea de comentario como una
cabecera de sección, y las claves anteriores de esa sección se pierden
(``TOTALMASS``, ``FUEL``, ``MAX_FUEL`` y ``[REAR] TRACK`` dieron KEY_NOT_FOUND).

Por eso los comentarios generados no deben contener corchetes. Solo se toca el
texto que va detrás del primer ``;`` de cada línea: claves, valores y cabeceras
de sección quedan intactos.
"""

from __future__ import annotations


def sanitize_ini_comments(text: str) -> str:
    """Quita ``[`` y ``]`` de la parte de comentario de cada línea."""
    out = []
    for line in text.split("\n"):
        head, sep, comment = line.partition(";")
        if sep:
            comment = comment.replace("[", "").replace("]", "")
        out.append(head + sep + comment)
    return "\n".join(out)
