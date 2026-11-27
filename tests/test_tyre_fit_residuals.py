"""``tyre_fit._residuals`` (desenrollado) == ``tyre_sctm.solve``, bit a bit."""

import math
import random

from src.ac import tyre_fit as tf
from src.ac.tyre_sctm import SctmParams


def _reference(p, pts):
    out = []
    for pt in pts:
        fy, fx = tf.model(p, pt)
        if pt.kind in ("lat", "comb"):
            out.append((fy - pt.fy) / pt.fz)
        if pt.kind in ("lon", "comb"):
            out.append((fx - pt.fx) / pt.fz)
    return out


def test_fast_residuals_match_model():
    rnd = random.Random(0)
    bounds = list(tf.LATERAL) + list(tf.LONGITUDINAL) + list(tf.COMBINED)
    pts = []
    for _ in range(600):
        kind = rnd.choice(("lat", "lon", "comb"))
        sa = rnd.choice((0.0, rnd.uniform(-0.6, 0.6), math.radians(6)))
        sr = rnd.choice((0.0, -1.0, -1.5, rnd.uniform(-0.99, 1.5)))
        camber = rnd.choice((0.0, rnd.uniform(-0.2, 0.2)))
        fz = rnd.uniform(200.0, 12000.0)
        speed = rnd.uniform(0.0, 60.0)
        pts.append(tf.Point(kind, fz, sa, sr, camber, speed, 100.0, -50.0))
    for trial in range(60):
        kw = {n: rnd.uniform(a, b) for n, a, b in bounds}
        if trial % 3 == 0:
            kw.update(combined_factor=2.0, dx_ref=0.0, brake_dx_mod=0.0)
        p = SctmParams(
            fz0=rnd.uniform(1000.0, 6000.0),
            dcamber_0=0.0 if trial % 2 else 0.2,
            dcamber_1=0.0 if trial % 2 else -0.5,
            **kw,
        )
        assert tf._residuals(p, pts) == _reference(p, pts)
