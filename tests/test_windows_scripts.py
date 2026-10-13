"""Statische Pruefung von install_windows.bat (laeuft ohne Windows)."""
from pathlib import Path

BAT = (Path(__file__).resolve().parent.parent / "install_windows.bat").read_bytes()
TXT = BAT.decode("ascii")  # muss reines ASCII sein


def test_crlf():
    assert b"\n" not in BAT.replace(b"\r\n", b"")


def test_python_kandidaten_in_reihenfolge():
    order = ['"py -3.13"', '"py -3.12"', '"py -3.11"', '"py -3"', '"python"']
    pos = [TXT.index(c) for c in order]
    assert pos == sorted(pos)


def test_kandidat_wird_ueber_ausgabe_verifiziert():
    assert "HARP_PY_OK" in TXT and "sys.version_info >= (3, 10)" in TXT
    code = [l for l in TXT.splitlines() if not l.lstrip().lower().startswith("rem")]
    assert not any("if errorlevel 1" in l for l in code)  # erkennt negative Fehlercodes des py-Managers nicht


def test_abbruch_vor_venv_und_pip():
    assert TXT.index("if not defined PYEXE goto no_python") < TXT.index("-m venv .venv")
    assert 'if not exist ".venv\\Scripts\\python.exe" goto venv_failed' in TXT
    assert TXT.index("goto venv_failed") < TXT.index("-m pip install")
    assert "py install 3.13" in TXT
