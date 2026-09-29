import json

import pytest

from livestock_engine.cli import main
from livestock_engine.pipeline import run_pipeline
from livestock_engine.progress import PROCESS_DATASET_STAGES, NullTracker, ProgressTracker, Stage
from livestock_engine.simulate import profile_config, simulate_survey


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_weighted_overall_percent_and_throttling():
    clock = Clock()
    events = []
    tracker = ProgressTracker(
        [Stage("a", "A", 1), Stage("b", "B", 3)], sink=events.append, min_interval_s=0.1, clock=clock
    )
    a = tracker.stage("a")
    assert events[-1]["percent"] == 0.0 and events[-1]["stages"] == ["A", "B"]
    clock.t = 0.05
    a.update(0.5)  # throttled
    assert len(events) == 1
    clock.t = 0.2
    a.update(0.5, "half", items=3)
    assert events[-1]["percent"] == 12.5 and events[-1]["counts"] == {"items": 3}
    b = tracker.stage("b")  # stage changes are never throttled
    assert events[-1]["percent"] == 25.0 and events[-1]["stage_index"] == 1
    clock.t = 0.21
    b.done()
    assert events[-1]["percent"] == 100.0 and events[-1]["stage_percent"] == 100.0


def test_percent_never_decreases_and_unknown_stage_rejected():
    events = []
    tracker = ProgressTracker([Stage("a", "A", 1), Stage("b", "B", 1)], sink=events.append, min_interval_s=0)
    tracker.stage("b").update(0.5)
    tracker.stage("a").update(0.1)  # re-entering an earlier stage
    assert [e["percent"] for e in events] == sorted(e["percent"] for e in events)
    with pytest.raises(KeyError):
        tracker.stage("nope")
    with pytest.raises(ValueError):
        ProgressTracker([])


def test_pipeline_reports_every_stage():
    events = []
    tracker = ProgressTracker(PROCESS_DATASET_STAGES, sink=events.append, min_interval_s=0)
    dataset = simulate_survey(profile_config("standard", seed=1, n_animals=30))
    run_pipeline(dataset, progress=tracker)
    stages = [e["stage"] for e in events]
    assert {"georeference", "cluster", "align"} <= set(stages)
    assert stages.index("georeference") < stages.index("cluster") < stages.index("align")
    assert events[-1]["counts"]["animals"] == 30
    assert any("Alignment pass 3 of 3" in e["message"] for e in events)
    # Without registration the align stage is still completed (skipped).
    events.clear()
    run_pipeline(dataset, registration=None, progress=ProgressTracker(PROCESS_DATASET_STAGES, sink=events.append))
    assert events[-1]["stage"] == "align" and events[-1]["message"] == "Frame alignment skipped"
    run_pipeline(dataset, progress=NullTracker())  # library use: silent


def test_self_check_command(capsys):
    assert main(["self-check", "--json"]) == 0
    info = json.loads(capsys.readouterr().out)
    assert info["ok"] is True and info["frozen"] is False
    assert {"numpy", "scipy", "sklearn", "pyproj", "proj"} <= set(info)
