"""The integration guide must describe what the code actually does."""

import re
from pathlib import Path

from livestock_engine.cli import build_parser
from livestock_engine.ingest import detections, srt, telemetry_csv

ROOT = Path(__file__).resolve().parent.parent
GUIDE = (ROOT / "docs" / "INTEGRATION_GUIDE.md").read_text()


def _aliases():
    groups = [getattr(telemetry_csv, n) for n in
              ("FRAME_ID", "FRAME_NUMBER", "TIME_S", "DATETIME", "LATITUDE", "LONGITUDE", "ALTITUDE", "HEADING", "PITCH", "ROLL")]
    groups += [getattr(detections, n) for n in
               ("FRAME_ID", "FRAME_NUMBER", "TIME_S", "SOURCE", "LABEL", "CLASS_ID", "CONFIDENCE")]
    groups += [fields for _, layout in detections.BOX_LAYOUTS for fields in layout]
    groups += [srt.ALTITUDE_KEYS, srt.HEADING_KEYS, srt.PITCH_KEYS, srt.ROLL_KEYS]
    return {alias for g in groups for alias in g}


def test_every_column_alias_is_documented():
    documented = set(re.findall(r"`([a-z_0-9]+)`", GUIDE))
    missing = sorted(_aliases() - documented)
    assert not missing, f"document these aliases in docs/INTEGRATION_GUIDE.md: {missing}"


def test_every_cli_command_is_documented():
    sub = next(a for a in build_parser()._actions if a.dest == "command")
    missing = [c for c in sub.choices if f"`{c}`" not in GUIDE]
    assert not missing, missing


def test_python_api_example_runs(monkeypatch):
    block = GUIDE.split("## 7. Python API", 1)[1].split("```python", 1)[1].split("```", 1)[0]
    sample = ROOT / "examples" / "sample-flight"
    code = block.replace('"flight.SRT"', repr(str(sample / "flight.SRT"))).replace(
        '"boxes.csv"', repr(str(sample / "detections.csv")))
    namespace: dict = {}
    monkeypatch.setattr("builtins.print", lambda *a, **k: None)
    exec(compile(code, "INTEGRATION_GUIDE.md", "exec"), namespace)
    assert namespace["result"].dedup.n_unique == 150
    assert namespace["report"]["summary"]["unique_animals"] == 150
