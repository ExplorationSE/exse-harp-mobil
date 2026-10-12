import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.generate_ptu import main as gen_main  # noqa: E402


@pytest.fixture(scope="session")
def t3_file(tmp_path_factory) -> Path:
    p = tmp_path_factory.mktemp("ptu") / "t3.ptu"
    gen_main(["--out", str(p), "--duration", "1.0", "--seed", "7"])
    return p


@pytest.fixture(scope="session")
def t2_file(tmp_path_factory) -> Path:
    p = tmp_path_factory.mktemp("ptu") / "t2.ptu"
    gen_main(["--t2", "--out", str(p), "--duration", "0.2", "--sync", "1e6", "--seed", "3"])
    return p


@pytest.fixture(scope="session")
def hbt_file(tmp_path_factory) -> Path:
    p = tmp_path_factory.mktemp("ptu") / "hbt.ptu"
    gen_main(["--hbt", "--out", str(p), "--duration", "8", "--seed", "5"])
    return p


@pytest.fixture(scope="session")
def heralded_file(tmp_path_factory) -> Path:
    p = tmp_path_factory.mktemp("ptu") / "heralded.ptu"
    gen_main(["--heralded", "--out", str(p), "--duration", "3", "--seed", "11"])
    return p
