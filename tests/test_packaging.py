"""Hand-off packaging stays consistent with the code."""

import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "packaging"))

from third_party_licenses import python_closure  # noqa: E402


def _closure_names():
    names = {d.metadata["Name"].lower() for d in python_closure("livestock-engine")}
    if not names:
        pytest.skip("livestock-engine is not installed as a distribution")
    return names


def test_lock_file_pins_the_whole_runtime_closure():
    locked = {
        line.split("==")[0].strip().lower()
        for line in (ROOT / "requirements.lock").read_text().splitlines()
        if "==" in line and not line.startswith("#")
    }
    assert locked == _closure_names()


def test_license_inventory_lists_every_shipped_python_package():
    text = (ROOT / "docs" / "THIRD_PARTY_LICENSES.md").read_text().lower()
    missing = [n for n in _closure_names() if f"| {n} |" not in text]
    assert not missing, f"regenerate with `make licenses`; missing: {missing}"


def test_source_package_is_reproducible_and_complete(tmp_path):
    if subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True).returncode:
        pytest.skip("not a git checkout")
    script = ROOT / "packaging" / "make_source_package.py"
    for out in ("a", "b"):
        subprocess.run([sys.executable, str(script), "--allow-dirty", "--out", str(tmp_path / out)], check=True,
                       capture_output=True)
    (a,) = (tmp_path / "a").glob("*.zip")
    (b,) = (tmp_path / "b").glob("*.zip")
    assert a.read_bytes() == b.read_bytes()
    names = zipfile.ZipFile(a).namelist()
    root = names[0].split("/")[0]
    for required in ("pyproject.toml", "README.md", "src/livestock_engine/cli.py", "desktop/package.json",
                     "desktop/package-lock.json", "packaging/livestock-engine.spec", "MANIFEST.sha256"):
        assert f"{root}/{required}" in names
    assert not any("node_modules" in n or "/out/" in n or "__pycache__" in n for n in names)
    assert (tmp_path / "a" / f"{a.name}.sha256").read_text().split()[1] == a.name
