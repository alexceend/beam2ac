"""SHOW_POPUP_ON_OPEN del .txt de la raíz de beam2ac."""

from src.app import settings


def test_popup_flag_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("BEAM2AC_STARTUP_FILE", str(tmp_path / "beam2ac_config.txt"))
    assert settings.show_popup_on_open() is True  # sin archivo: se muestra
    settings.set_show_popup_on_open(False)
    assert (tmp_path / "beam2ac_config.txt").read_text() == "SHOW_POPUP_ON_OPEN=0\n"
    assert settings.show_popup_on_open() is False
    settings.set_show_popup_on_open(True)
    assert settings.show_popup_on_open() is True
