"""The published JSON Schemas (schemas/) must match what the engine reads and writes."""

import json
from pathlib import Path

import pytest

jsonschema = pytest.importorskip("jsonschema")

from livestock_engine.cli import main  # noqa: E402
from livestock_engine.io import dataset_to_dict  # noqa: E402
from livestock_engine.simulate import export_raw_logs, profile_config, simulate_survey  # noqa: E402

SCHEMAS = Path(__file__).resolve().parent.parent / "schemas"


def validator(name):
    schema = json.loads((SCHEMAS / f"{name}.schema.json").read_text())
    jsonschema.Draft202012Validator.check_schema(schema)
    return jsonschema.Draft202012Validator(schema)


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    out = {}
    for profile in ("standard", "sheep_pen"):
        tmp = tmp_path_factory.mktemp(profile)
        dataset = simulate_survey(profile_config(profile, seed=1, n_animals=40 if profile == "standard" else 200))
        paths = export_raw_logs(dataset, tmp)
        report, events_file, ds_out = tmp / "report.json", tmp / "events.txt", tmp / "ingested.json"
        import contextlib
        import io

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = main(["process", "--progress", "--telemetry", str(paths["srt"]), "--detections",
                       str(paths["detections"]), "--report", str(report), "--dataset-out", str(ds_out)])
        assert rc == 0
        events_file.write_text(buf.getvalue())
        out[profile] = dict(dataset=dataset, paths=paths, report=report, events=events_file, ingested=ds_out)
    return out


def test_all_schemas_are_valid_json_schema():
    for path in SCHEMAS.glob("*.schema.json"):
        validator(path.name.removesuffix(".schema.json"))


def test_datasets_match_schema(runs):
    v = validator("dataset")
    for run in runs.values():
        v.validate(dataset_to_dict(run["dataset"]))  # simulated, with ground truth
        v.validate(json.loads(run["ingested"].read_text()))  # produced by ingestion
    with pytest.raises(jsonschema.ValidationError):
        v.validate({"frames": [{"latitude": 1, "longitude": 2, "heading_deg": 0}]})  # no altitude


@pytest.mark.parametrize("profile", ["standard", "sheep_pen"])
def test_reports_match_schema(runs, profile):
    report = json.loads(runs[profile]["report"].read_text())
    validator("report").validate(report)
    assert report["summary"]["density"]["mode"] == ("dense" if profile == "sheep_pen" else "normal")


def test_progress_events_match_schema(runs):
    v = validator("progress-event")
    for run in runs.values():
        events = [json.loads(line) for line in run["events"].read_text().splitlines()]
        for e in events:
            v.validate(e)
        assert events[-1]["event"] == "done"
    v.validate({"event": "error", "message": "bad input"})
    with pytest.raises(jsonschema.ValidationError):
        v.validate({"event": "progress", "percent": 150})


def test_detection_logs_match_schema(tmp_path):
    v = validator("detections")
    v.validate([{"image": "DJI_0001.JPG", "bbox": [1, 2, 3, 4], "label": "cow", "score": 0.9}])
    v.validate({"detections": [{"frame": 12, "cx": 5, "cy": 6, "w": 2, "h": 2, "video": "DJI_0003"}]})
    v.validate({"images": [{"id": 1, "file_name": "a.jpg"}], "annotations": [{"image_id": 1, "bbox": [0, 0, 5, 5]}]})
    with pytest.raises(jsonschema.ValidationError):
        v.validate([{"bbox": [1, 2, 3, 4]}])  # no frame key


def test_validation_result_matches_schema(runs, tmp_path):
    run = runs["standard"]
    out = tmp_path / "v.json"
    assert main(["validate", "--report", str(run["report"]), "--truth", str(run["paths"]["ground_truth"]),
                 "--json", str(out)]) == 0
    validator("validation-result").validate(json.loads(out.read_text()))
