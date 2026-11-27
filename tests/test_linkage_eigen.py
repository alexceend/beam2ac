"""``linkage._jacobi_eigen`` (con atajo de ciclo) == las 200 rotaciones completas."""

import math
import random

from src.extract import linkage as L


def _full(a):
    n = len(a)
    m = [row[:] for row in a]
    for _ in range(200):
        off, p, q = max(
            (abs(m[i][j]), i, j) for i in range(n) for j in range(n) if i != j
        )
        if off < 1e-30:
            break
        th = 0.5 * math.atan2(2 * m[p][q], m[q][q] - m[p][p])
        c, s = math.cos(th), math.sin(th)
        for r in range(n):
            x, y = m[r][p], m[r][q]
            m[r][p], m[r][q] = c * x - s * y, s * x + c * y
        for r in range(n):
            x, y = m[p][r], m[q][r]
            m[p][r], m[q][r] = c * x - s * y, s * x + c * y
    return sorted(m[i][i] for i in range(n))


def test_cycle_shortcut_matches_full_iteration():
    rnd = random.Random(0)
    for trial in range(300):
        rank = rnd.choice((1, 3, 5, 6))
        j = [[rnd.gauss(0, 1) for _ in range(rank)] for _ in range(6)]
        if trial % 4 == 0:  # ceros y empates de módulo
            j = [[rnd.choice((0.0, 1.0, -1.0)) for _ in range(rank)] for _ in j]
        a = [[sum(x * y for x, y in zip(u, v)) for v in j] for u in j]
        assert repr(L._jacobi_eigen(a)) == repr(_full(a))
