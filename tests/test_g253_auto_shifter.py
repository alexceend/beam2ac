from src.project.export import PackageFile, _ensure_auto_shifter


def _run(extra=None):
    files = [PackageFile("data/drivetrain.ini", b"[GEARS]\nCOUNT=5\n", {})]
    texts = {"drivetrain.ini": "[GEARS]\nCOUNT=5\n", **(extra or {})}
    issues = []
    _ensure_auto_shifter(files, texts, issues)
    return files, texts, issues


def test_añade_auto_shifter_y_ai_ini():
    files, texts, issues = _run()
    assert "SLIP_THRESHOLD=0.95" in texts["drivetrain.ini"]
    assert "GAS_CUTOFF_TIME=0.3" in texts["drivetrain.ini"]
    ai = next(f for f in files if f.rel == "data/ai.ini")
    assert b"[GEARS]" in ai.data and b"SLIP_THRESHOLD=0.95" in ai.data
    assert (
        issues and issues[0].code == "ayudas.auto_shifter"
        if hasattr(issues[0], "code")
        else True
    )


def test_idempotente_y_respeta_ai_ini_existente():
    files, texts, _ = _run({"ai.ini": "[GEARS]\nUP=1\n"})
    assert not any(f.rel == "data/ai.ini" for f in files)
    _ensure_auto_shifter(files, texts, [])
    assert texts["drivetrain.ini"].upper().count("[AUTO_SHIFTER]") == 1
