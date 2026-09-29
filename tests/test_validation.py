import json

import pytest

from livestock_engine.cli import main
from livestock_engine.simulate import export_raw_logs, profile_config, simulate_survey
from livestock_engine.validation import ValidationError, parse_counts, validate_counts, validate_points


@pytest.fixture(scope="module")
def processed(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("val")
    paths = export_raw_logs(simulate_survey(profile_config("standard", seed=4, n_animals=40)), tmp)
    report = tmp / "report.json"
    assert main(["process", "--telemetry", str(paths["srt"]), "--detections", str(paths["detections"]),
                 "--report", str(report)]) == 0
    return report, paths["ground_truth"]


def test_validate_against_surveyed_positions(processed, tmp_path, capsys):
    report, truth = processed
    out = tmp_path / "v.json"
    assert main(["validate", "--report", str(report), "--truth", str(truth), "--json", str(out)]) == 0
    result = json.loads(out.read_text())
    assert result["count_accuracy"] == 1.0 and result["f1"] == 1.0
    assert result["mean_position_error_m"] < 0.5 and result["missed"] == [] and result["extra"] == []
    assert "precision 100.00%" in capsys.readouterr().out


def test_missing_and_extra_animals_are_listed(processed):
    report_path, truth_path = processed
    report = json.loads(report_path.read_text())
    from livestock_engine.validation import load_truth_points

    truth = load_truth_points(truth_path)
    moved = dict(report, animals=report["animals"][:-2] + [dict(report["animals"][0], id="GHOST", lat=0.0, lon=0.0)])
    result = validate_points(moved, truth, radius_m=2.0)
    # Before cost gating, the far-away ghost dragged correct matches apart (36 matched).
    assert result.count_error == -1 and result.matched == 38 and result.recall == pytest.approx(38 / 40)
    assert result.extra == ["GHOST"] and len(result.missed) == 2


def test_manual_counts():
    report = {"animals": [{"label": "cattle"}] * 118 + [{"label": "Sheep"}] * 30}
    total = validate_counts(report, parse_counts("150"))
    assert total.count_error == -2 and total.count_accuracy == pytest.approx(1 - 2 / 150)
    per = validate_counts(report, parse_counts("cattle=120, sheep=30"))
    assert per.per_class["cattle"].predicted == 118 and per.per_class["sheep"].count_accuracy == 1.0
    with pytest.raises(ValidationError):
        parse_counts("cattle=lots")


def test_validate_cli_errors(processed, tmp_path, capsys):
    report, truth = processed
    assert main(["validate", "--report", str(report)]) == 2  # neither --truth nor --count
    bad = tmp_path / "bad.csv"
    bad.write_text("x,y\n1,2\n")
    assert main(["validate", "--report", str(report), "--truth", str(bad)]) == 2
    assert "latitude and longitude" in capsys.readouterr().err
    assert main(["validate", "--report", str(report), "--count", "400"]) == 1  # far below threshold
