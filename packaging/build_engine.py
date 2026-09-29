#!/usr/bin/env python3
"""Freeze the spatial engine with PyInstaller and verify the result.

    python packaging/build_engine.py            # -> desktop/resources/engine/

Steps:
1. run PyInstaller with ``packaging/livestock-engine.spec``;
2. copy the one-folder bundle to ``desktop/resources/engine`` where
   electron-builder picks it up (``extraResources``);
3. smoke-test the frozen binary *with no Python on PATH*: ``self-check`` must
   pass and processing the sample flight must recover the true headcount.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "packaging" / "livestock-engine.spec"
DEFAULT_OUT = ROOT / "desktop" / "resources" / "engine"
SAMPLE = ROOT / "examples" / "sample-flight"
EXE_NAME = "livestock-engine.exe" if os.name == "nt" else "livestock-engine"


def build(work: Path) -> Path:
    cmd = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--distpath", str(work / "dist"), "--workpath", str(work / "build"), str(SPEC),
    ]
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True, cwd=ROOT)
    return work / "dist" / "livestock-engine"


def isolated_env() -> dict[str, str]:
    """Environment without Python: proves the bundle is self-contained."""
    keep = {"SYSTEMROOT", "WINDIR", "TEMP", "TMP", "HOME", "USERPROFILE", "LANG", "LC_ALL"}
    env = {k: v for k, v in os.environ.items() if k.upper() in keep}
    env["PATH"] = os.environ.get("SYSTEMROOT", "") + "\\System32" if os.name == "nt" else "/usr/bin/nonexistent"
    return env


def smoke_test(bundle: Path) -> dict:
    exe = bundle / EXE_NAME
    env = isolated_env()
    started = time.perf_counter()
    out = subprocess.run([str(exe), "self-check", "--json"], env=env, capture_output=True, text=True, timeout=120)
    if out.returncode != 0:
        raise SystemExit(f"self-check failed:\n{out.stdout}\n{out.stderr}")
    info = json.loads(out.stdout)
    info["startup_s"] = round(time.perf_counter() - started, 2)
    if not info.get("frozen"):
        raise SystemExit("self-check did not run from the frozen bundle")

    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "report.json"
        out = subprocess.run(
            [
                str(exe), "process", "--progress",
                "--telemetry", str(SAMPLE / "flight.SRT"),
                "--detections", str(SAMPLE / "detections.csv"),
                "--report", str(report),
            ],
            env=env, capture_output=True, text=True, timeout=300,
        )
        if out.returncode != 0:
            raise SystemExit(f"processing the sample flight failed:\n{out.stdout}\n{out.stderr}")
        events = [json.loads(line) for line in out.stdout.splitlines() if line.startswith("{")]
        unique = json.loads(report.read_text())["summary"]["unique_animals"]
    with (SAMPLE / "ground_truth.csv").open() as fh:
        truth = sum(1 for _ in csv.DictReader(fh))
    if abs(unique - truth) > 0.01 * truth:
        raise SystemExit(f"frozen engine counted {unique} animals, expected {truth}")
    info.update(sample_unique=unique, sample_truth=truth, progress_events=len(events))
    return info


def dir_size_mb(path: Path) -> float:
    """On-disk size; symlinks (used for shared libraries) are not followed."""
    return sum(f.lstat().st_size for f in path.rglob("*") if f.is_file() and not f.is_symlink()) / 1024**2


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help=f"output folder (default: {DEFAULT_OUT})")
    parser.add_argument("--skip-build", action="store_true", help="only smoke-test an existing bundle in --out")
    args = parser.parse_args()

    if not args.skip_build:
        with tempfile.TemporaryDirectory() as tmp:
            bundle = build(Path(tmp))
            if args.out.exists():
                shutil.rmtree(args.out)
            args.out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(bundle, args.out, symlinks=True)

    info = smoke_test(args.out)
    info["bundle_mb"] = round(dir_size_mb(args.out), 1)
    print(json.dumps(info, indent=2))
    (args.out / "engine-info.json").write_text(json.dumps(info, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
