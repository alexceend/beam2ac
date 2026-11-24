"""Enlaces J6 y J7 del eje rígido (G2.44; composed_fox tiene 7). NO volver a ejecutar.

Mismo patrón que ``add_rigid_axle_rules.py`` (importa sus utilidades): 4 atributos, 4 reglas de valor y 4 de aplicabilidad,
más los objetivos de ``catalog/targets/ac_physics.json``."""
import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools" / "catalog"))
import add_rigid_axle_rules as m  # noqa: E402
from src.attrs import schema as S  # noqa: E402
from src.attrs.store import REPO_CATALOG, Catalog, write_rule  # noqa: E402

rules, attrs, targets = [], [], []
anchor = m.load("susp.wbcar_bottom_front_rear_200409@1")
base_attr = json.loads(m.ATTR_FILE.read_text("utf-8"))
tmpl = next(a for a in base_attr if a["id"] == f"{m.SUSP}/AXLE/J0_CAR")
for i in (6, 7):
    for side in ("CAR", "AXLE"):
        aid = f"{m.SUSP}/AXLE/J{i}_{side}"
        a = copy.deepcopy(tmpl)
        a.update(id=aid, description=f"punto {side} del enlace {i}, relativo al centro del eje")
        attrs.append(a)
        targets.append("ac:suspensions.ini/AXLE/" + f"J{i}_{side}")
        d = copy.deepcopy(anchor)
        m.widen(d, m.EXPORTERS)
        m.set_linkage(d, ("eq", "rigid_axle"))
        src_in = f"{m.REAR}.anchors.J{i}_{side}"
        d["conditions"].append({"fact": src_in, "op": "exists", "value": None})
        d.update(target=aid, inputs={"x": src_in})
        d["transform"]["formula"] = f"punto {side} del enlace {i} − centro del eje en reposo (ejes AC)"
        rules.append(m.finish(d, f"susp.axle_j{i}_{side.lower()}", f"[AXLE] J{i}_{side} medido"))
        rules.append(m.app_rule("app.rear.wbtyre_top@1", f"app.axle.j{i}_{side.lower()}", aid, "m",
                                {"fact": f"{m.SUSP}/REAR/TYPE", "op": "eq", "value": "AXLE"}))
if "--dry-run" in sys.argv:
    print("ok", len(attrs), len(rules))
    sys.exit(0)
m.ATTR_FILE.write_bytes((json.dumps(base_attr + attrs, indent=1, ensure_ascii=False) + "\n").replace("\n", "\r\n").encode("utf-8"))
tp = ROOT / "catalog/targets/ac_physics.json"
t = json.loads(tp.read_bytes().decode("utf-8"))
last = max(i for i, x in enumerate(t["targets"]) if x.startswith("ac:suspensions.ini/AXLE/"))
t["targets"][last + 1:last + 1] = targets
tp.write_bytes((json.dumps(t, indent=1, ensure_ascii=False) + "\n").replace("\n", "\r\n").encode("utf-8"))
for r in rules:
    write_rule(REPO_CATALOG, r)
Catalog.load(REPO_CATALOG)
print("escritos", len(attrs), "atributos y", len(rules), "reglas")
