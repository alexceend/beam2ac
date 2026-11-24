"""Instalación del complemento KN5 de Blender (src/app/blender_addon.py): sin red, con zip sintético."""

import io
import zipfile

import pytest

from src.app import blender_addon as ba

INIT = 'bl_info = {"name": "x"}\n'


def _zip(extra=None, root="repo-abc/"):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(root + "__init__.py", INIT)
        z.writestr(root + "exporter/__init__.py", "")
        for name, data in (extra or {}).items():
            z.writestr(name, data)
    return buf.getvalue()


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("BEAM2AC_BLENDER_ADDONS", str(tmp_path / "addons"))
    # la huella fijada real es del repositorio de Moppius: aquí la del zip sintético
    src = tmp_path / "ref"
    (src / "exporter").mkdir(parents=True)
    (src / "__init__.py").write_bytes(INIT.encode())
    (src / "exporter" / "__init__.py").write_bytes(b"")
    monkeypatch.setattr(ba, "TREE_SHA256", ba.tree_hash(src))
    return tmp_path / "addons" / ba.ADDON_NAME


def test_install_ok_and_status(env):
    assert ba.status()["installed"] is False
    st = ba.install(lambda url: _zip())
    assert st["installed"] and st["matches_pinned"] is True
    assert (env / "exporter" / "__init__.py").is_file()


def test_refuses_existing_install(env):
    env.mkdir(parents=True)
    with pytest.raises(ba.AddonError, match="no se sobrescribe"):
        ba.install(lambda url: _zip())


def test_refuses_hash_mismatch(env):
    bad = _zip({"repo-abc/exporter/evil.py": "import os"})
    with pytest.raises(ba.AddonError, match="huella"):
        ba.install(lambda url: bad)
    assert not env.exists()


def test_refuses_zip_slip(env):
    with pytest.raises(ba.AddonError, match="insegura"):
        ba.install(lambda url: _zip({"../../evil.py": "x"}))
    assert not env.exists()


def test_refuses_non_zip_and_foreign_host(env):
    with pytest.raises(ba.AddonError, match="zip"):
        ba.install(lambda url: b"no soy un zip")
    with pytest.raises(ba.AddonError, match="origen"):
        ba._download("https://example.com/x.zip")


def test_only_hashed_py_and_inert_text_are_installed(env):
    z = _zip(
        {
            "repo-abc/__pycache__/__init__.cpython-310.pyc": b"evil",
            "repo-abc/evil.pyd": b"evil",
            "repo-abc/x.pth": b"import os",
            "repo-abc/LICENSE.txt": b"GPL",
        }
    )
    ba.install(lambda url: z)
    names = sorted(p.name for p in env.rglob("*") if p.is_file())
    assert names == ["LICENSE.txt", "__init__.py", "__init__.py"]
    assert ba.status()["matches_pinned"] is True


def test_refuses_zip_bomb_and_too_many_entries(env):
    with pytest.raises(ba.AddonError, match="demasiado grande"):
        ba.install(
            lambda url: _zip({"repo-abc/big.bin": b"0" * (ba.MAX_UNZIPPED_BYTES + 1)})
        )
    many = {f"repo-abc/f{i}.txt": b"" for i in range(ba.MAX_ENTRIES + 1)}
    with pytest.raises(ba.AddonError, match="demasiado grande"):
        ba.install(lambda url: _zip(many))
    assert not env.exists()


def test_refuses_symlink_entry(env):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("repo-abc/__init__.py", INIT)
        info = zipfile.ZipInfo("repo-abc/link")
        info.external_attr = 0o120777 << 16
        z.writestr(info, "../../outside")
    with pytest.raises(ba.AddonError, match="simbólico"):
        ba.install(lambda url: buf.getvalue())
    assert not env.exists()


def test_redirect_handler_blocks_http_and_foreign_hosts():
    h = ba._SafeRedirect()
    req = ba.urllib.request.Request(ba.ZIP_URL)
    for bad in ("http://codeload.github.com/x", "https://evil.example/x.zip"):
        with pytest.raises(ba.AddonError, match="redirección"):
            h.redirect_request(req, None, 302, "Found", {}, bad)


def _post(port, body):
    import json
    import urllib.error
    import urllib.request

    r = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/blender_addon",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(r) as resp:
            return resp.status, json.load(resp)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


def test_post_route_needs_confirm_and_maps_errors(env, tmp_path, monkeypatch):
    import threading

    from src.app.desktop import make_desktop_server
    from src.app.workspace import Workspace

    orig = (
        ba.install
    )  # la ruta llama a blender_addon.install(): se le inyecta el zip falso
    monkeypatch.setattr(ba, "install", lambda: orig(lambda url: _zip()))
    server = make_desktop_server(Workspace(tmp_path / "proj"), 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    try:
        assert _post(port, {})[0] == 400  # sin confirm
        code, data = _post(port, {"confirm": True})
        assert code == 200 and data["installed"] is True
        code, data = _post(port, {"confirm": True})  # ya existe: 400, no 500
        assert code == 400 and "no se sobrescribe" in data["error"]
    finally:
        server.shutdown()
