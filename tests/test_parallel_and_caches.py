"""Ajuste en procesos paralelos y memorias del catálogo: mismos resultados."""

import json
import sys
from pathlib import Path

import pytest

from src.ac import tyre_fit as tf
from src.attrs.store import REPO_CATALOG, Catalog


def _datasets(limit):
    def walk(o, load=None):
        if isinstance(o, dict):
            load = o.get("reference_load", load)
            if o.get("rows") and "columns" in o and load:
                yield o, float(load), True
            for v in o.values():
                yield from walk(v, load)
        elif isinstance(o, list):
            for v in o:
                yield from walk(v, load)

    out = []
    for p in sorted(Path("measurements").rglob("*.json")):
        out += list(walk(json.loads(p.read_text("utf-8"))))
        if len(out) >= limit:
            return out[:limit]
    return out


@pytest.mark.skipif(sys.platform != "win32", reason="el paralelismo es solo Windows")
def test_prefit_matches_sequential(monkeypatch):
    jobs = _datasets(2)
    if len(jobs) < 2:
        pytest.skip("sin medidas de neumáticos")
    monkeypatch.setattr(tf, "_CACHE", {})
    monkeypatch.setattr(tf, "PARALLEL", True)
    tf.prefit(jobs)
    assert len(tf._CACHE) == 2
    parallel = [repr(tf.fit_dataset(*j)) for j in jobs]
    tf._CACHE.clear()
    assert parallel == [repr(tf.fit_dataset(*j)) for j in jobs]


def test_prefit_is_off_by_default(monkeypatch):
    monkeypatch.setattr(tf, "_CACHE", {})
    tf.prefit([({}, 1.0, False), ({}, 2.0, False)])
    assert tf._CACHE == {}


def test_catalog_copies_are_independent_and_follow_the_files(tmp_path):
    extra = tmp_path / "proj"
    extra.mkdir()
    first = Catalog.load(REPO_CATALOG, extra)
    second = Catalog.load(REPO_CATALOG, extra)
    assert first == second and first is not second
    second.decisions.append("x")
    second.attributes.clear()
    assert Catalog.load(REPO_CATALOG, extra) == first
    (extra / "targets").mkdir()
    (extra / "targets" / "mine.json").write_text(
        json.dumps({"schema": "beam2ac.targets/1", "targets": []}), "utf-8"
    )
    assert "mine" in Catalog.load(REPO_CATALOG, extra).targets
