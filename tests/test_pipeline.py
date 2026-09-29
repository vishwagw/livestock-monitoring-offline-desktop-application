import csv
import json

import pytest

from livestock_engine.cli import main
from livestock_engine.dedup import DedupConfig
from livestock_engine.io import DatasetError, dataset_from_dict, load_dataset, save_dataset
from livestock_engine.metrics import evaluate
from livestock_engine.pipeline import run_pipeline
from livestock_engine.simulate import profile_config, simulate_survey


@pytest.mark.parametrize("profile", ["ideal", "standard", "oblique", "dense", "harsh"])
def test_simulated_flights_reach_milestone_accuracy(profile):
    for seed in range(2):
        dataset = simulate_survey(profile_config(profile, seed=seed))
        result = run_pipeline(dataset)
        ev = evaluate(result, dataset)
        assert ev.raw_detections > 5 * ev.observed_animals  # heavy overlap
        assert ev.count_accuracy >= 0.99, (profile, seed, ev)
        assert ev.f1 >= 0.99, (profile, seed, ev)


def test_ideal_flight_is_exact_without_registration():
    dataset = simulate_survey(profile_config("ideal", seed=5))
    result = run_pipeline(dataset, registration=None)
    ev = evaluate(result, dataset)
    assert ev.count_accuracy == 1.0 and ev.f1 == 1.0
    assert ev.mean_position_error_m < 1e-6


def test_dataset_json_round_trip(tmp_path):
    dataset = simulate_survey(profile_config("standard", seed=1, n_animals=20))
    path = tmp_path / "flight.json"
    save_dataset(dataset, path)
    loaded = load_dataset(path)
    assert len(loaded.frames) == len(dataset.frames)
    assert loaded.n_detections == dataset.n_detections
    a = run_pipeline(dataset).dedup.n_unique
    b = run_pipeline(loaded).dedup.n_unique
    assert a == b


def test_shared_camera_block_and_defaults():
    ds = dataset_from_dict(
        {
            "camera": {"image_width": 4000, "image_height": 3000, "fov_deg": 84, "fov_type": "diagonal"},
            "frames": [
                {
                    "latitude": 10.0,
                    "longitude": 20.0,
                    "altitude_agl_m": 50,
                    "heading_deg": 0,
                    "detections": [{"x": 2000, "y": 1500}],
                }
            ],
        }
    )
    frame = ds.frames[0]
    assert frame.pose.gimbal_pitch_deg == -90.0
    assert frame.camera.fov_type == "diagonal"
    assert frame.detections[0].label == "animal"
    result = run_pipeline(ds)
    assert result.animals[0].latitude == pytest.approx(10.0, abs=1e-7)
    assert result.animals[0].longitude == pytest.approx(20.0, abs=1e-7)


def test_missing_fields_raise_dataset_error():
    with pytest.raises(DatasetError, match="altitude_agl_m"):
        dataset_from_dict(
            {"frames": [{"latitude": 1, "longitude": 1, "heading_deg": 0, "image_width": 10, "image_height": 10, "fov_deg": 60}]}
        )


def test_csv_input(tmp_path):
    path = tmp_path / "dets.csv"
    header = "frame_id,latitude,longitude,altitude_agl_m,heading_deg,gimbal_pitch_deg,image_width,image_height,fov_deg,x,y,label,confidence\n"
    rows = [
        "F1,-33.87,151.21,60,0,-90,4000,3000,73.7,2000,1500,cattle,0.9\n",
        "F1,-33.87,151.21,60,0,-90,4000,3000,73.7,2400,1500,cattle,0.8\n",
        # Next frame, drone ~10 m north: the same two animals appear lower in the image.
        "F2,-33.86991,151.21,60,0,-90,4000,3000,73.7,2000,1944,cattle,0.95\n",
        "F2,-33.86991,151.21,60,0,-90,4000,3000,73.7,2400,1944,sheep,0.5\n",
    ]
    path.write_text(header + "".join(rows))
    ds = load_dataset(path)
    assert [len(f.detections) for f in ds.frames] == [2, 2]
    result = run_pipeline(ds, DedupConfig(), registration=None)
    assert result.dedup.n_unique == 2
    assert sorted(a.n_observations for a in result.animals) == [2, 2]


def test_cli_simulate_dedup_benchmark(tmp_path, capsys):
    flight = tmp_path / "flight.json"
    assert main(["simulate", "-o", str(flight), "--seed", "3", "--animals", "40"]) == 0
    out = tmp_path / "animals.csv"
    summary = tmp_path / "summary.json"
    rc = main(
        [
            "dedup",
            str(flight),
            "-o",
            str(out),
            "--geojson",
            str(tmp_path / "a.geojson"),
            "--assignments",
            str(tmp_path / "assign.csv"),
            "--frame-corrections",
            str(tmp_path / "corr.csv"),
            "--summary-json",
            str(summary),
            "--evaluate",
        ]
    )
    assert rc == 0
    with out.open() as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 40
    report = json.loads(summary.read_text())
    assert report["evaluation"]["count_accuracy"] == 1.0
    geo = json.loads((tmp_path / "a.geojson").read_text())
    assert len(geo["features"]) == 40

    assert main(["benchmark", "--runs", "1", "--animals", "40"]) == 0
    assert "PASS" in capsys.readouterr().out
    assert main(["benchmark", "--runs", "1", "--animals", "40", "--threshold", "1.01"]) == 1


def test_cli_reports_bad_input(tmp_path, capsys):
    bad = tmp_path / "bad.txt"
    bad.write_text("nope")
    assert main(["dedup", str(bad), "-o", str(tmp_path / "x.csv")]) == 2
    assert "unsupported input format" in capsys.readouterr().err
