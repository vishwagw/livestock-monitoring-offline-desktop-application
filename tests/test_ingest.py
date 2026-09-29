import json

import numpy as np
import pytest

from livestock_engine.camera import CameraModel
from livestock_engine.cli import main
from livestock_engine.ingest import (
    DetectionLogError,
    IngestOptions,
    build_dataset,
    ingest_files,
    load_detections,
    load_telemetry_csv,
    parse_srt_text,
)
from livestock_engine.ingest.columns import normalise_header
from livestock_engine.ingest.detections import RawDetection
from livestock_engine.pipeline import run_pipeline
from livestock_engine.simulate import export_raw_logs, profile_config, simulate_survey

CAM = CameraModel(4000, 3000, 84.0, "diagonal")

MAVIC3_SRT = """1
00:00:00,000 --> 00:00:00,033
<font size="28">FrameCnt: 1, DiffTime: 33ms
2024-03-08 10:12:34.123
[iso: 100] [shutter: 1/1000.0] [fnum: 2.8] [ev: 0] [latitude: -33.868800] [longitude: 151.209300] [rel_alt: 60.000 abs_alt: 120.500] [gb_yaw: -90.0 gb_pitch: -90.0 gb_roll: 0.0] </font>

2
00:00:00,033 --> 00:00:00,066
<font size="28">FrameCnt: 2, DiffTime: 33ms
2024-03-08 10:12:34.156
[iso: 100] [latitude: -33.868810] [longitude: 151.209300] [rel_alt: 60.200 abs_alt: 120.700] [gb_yaw: -89.0 gb_pitch: -89.5 gb_roll: 0.1] </font>
"""

PHANTOM_SRT = """1
00:00:00,000 --> 00:00:01,000
HOME(149.0251,-35.2345) 2019.01.01 12:00:00
GPS(149.0253,-35.2346,19) BAROMETER:60.2
ISO:100 Shutter:60 EV:0 Fnum:F2.8

2
00:00:01,000 --> 00:00:02,000
HOME(149.0251,-35.2345) 2019.01.01 12:00:01
GPS(149.0253,-35.2345,19) BAROMETER:60.4
ISO:100 Shutter:60 EV:0 Fnum:F2.8
"""

MAVIC_PRO_SRT = """1
00:00:00,000 --> 00:00:01,000
F/2.8, SS 206.03, ISO 100, EV 0, GPS (149.0253, -35.2346, 19), D 24.18m, H 45.00m, H.S 0.00m/s, V.S 0.00m/s

2
00:00:00,000 --> 00:00:00,000
F/2.8, SS 206.03, ISO 100, EV 0, GPS (0.0, 0.0, 0), D 0m, H 0.00m
"""


def test_parse_modern_dji_srt():
    track = parse_srt_text(MAVIC3_SRT, "DJI_0001.SRT")
    assert len(track.samples) == 2
    s = track.samples[1]
    assert (s.latitude, s.longitude) == pytest.approx((-33.86881, 151.2093))
    assert s.altitude_agl_m == pytest.approx(60.2)
    assert s.heading_deg == pytest.approx(271.0)  # -89 normalised
    assert s.gimbal_pitch_deg == pytest.approx(-89.5)
    assert s.frame_number == 2
    assert s.time_s == pytest.approx(0.033)
    assert s.timestamp == "2024-03-08 10:12:34.156"
    assert track.name == "DJI_0001"


def test_parse_phantom_and_mavic_pro_srt():
    track = parse_srt_text(PHANTOM_SRT)
    assert track.samples[0].latitude == pytest.approx(-35.2346)
    assert track.samples[0].longitude == pytest.approx(149.0253)
    assert track.samples[0].altitude_agl_m == pytest.approx(60.2)
    assert track.samples[0].heading_deg is None
    track = parse_srt_text(MAVIC_PRO_SRT)
    assert len(track.samples) == 1  # the 0,0 block has no GPS fix
    assert track.samples[0].altitude_agl_m == pytest.approx(45.0)
    assert any("without a GPS fix" in w for w in track.warnings)


def test_srt_interpolation_and_derived_heading():
    track = parse_srt_text(PHANTOM_SRT)
    mid = track.at_time(0.5)
    assert mid.latitude == pytest.approx(-35.23455)
    assert mid.altitude_agl_m == pytest.approx(60.3)
    assert track.at_time(5.0) is None
    track.fill_missing_headings(min_baseline_m=5.0)
    assert track.samples[0].heading_deg == pytest.approx(0.0, abs=0.01)  # moving north
    assert track.samples[0].heading_derived


def test_heading_derivation_keeps_strip_heading_at_turns():
    # North leg then an east step then a south leg (lawnmower turn).
    rows = ["image,lat,lon,alt"]
    lat0, lon0, dlat, dlon = -33.0, 151.0, 0.0002, 0.0005
    pts = [(lat0 + i * dlat, lon0) for i in range(4)] + [(lat0 + (3 - i) * dlat, lon0 + dlon) for i in range(4)]
    rows += [f"IMG_{i},{la},{lo},60" for i, (la, lo) in enumerate(pts)]
    track = _csv_track("\n".join(rows))
    track.fill_missing_headings()
    headings = [round(s.heading_deg) % 360 for s in track.samples]
    assert headings[:4] == [0, 0, 0, 0]
    assert headings[4:] == [180, 180, 180, 180]


def _csv_track(text):
    import tempfile
    from pathlib import Path

    d = Path(tempfile.mkdtemp())
    p = d / "log.csv"
    p.write_text(text)
    return load_telemetry_csv(p)


def test_telemetry_csv_units_and_aliases():
    track = _csv_track(
        "Time(millisecond),Latitude,Longitude,Height Above Takeoff (feet),Gimbal Heading,Gimbal Pitch\n"
        "0,-33.1,151.1,196.85,10,-90\n"
        "500,-33.1001,151.1,197.0,11,-90\n"
    )
    s = track.samples[1]
    assert s.time_s == pytest.approx(0.5)
    assert s.altitude_agl_m == pytest.approx(60.0456, abs=1e-3)
    assert s.heading_deg == 11
    assert normalise_header("Height Above Takeoff (feet)") == ("height_above_takeoff", "feet")


@pytest.mark.parametrize(
    "header, row, expect_xy",
    [
        ("image,xmin,ymin,xmax,ymax,class,score", "a.jpg,10,20,30,60,cow,0.9", (20, 40)),
        ("image,x,y,width,height,label,confidence", "a.jpg,10,20,20,40,cow,0.9", (20, 40)),
        ("image,cx,cy,w,h,label,confidence", "a.jpg,20,40,20,40,cow,0.9", (20, 40)),
        ("frame,x,y,label,confidence", "7,20,40,cow,90", (20, 40)),
    ],
)
def test_detection_csv_layouts(tmp_path, header, row, expect_xy):
    p = tmp_path / "d.csv"
    p.write_text(f"{header}\n{row}\n")
    (d,) = load_detections(p)
    assert (d.x, d.y) == pytest.approx(expect_xy)
    assert d.label == "cow"
    assert d.confidence == pytest.approx(0.9)


def test_detection_json_coco_and_yolo(tmp_path):
    coco = {
        "images": [{"id": 1, "file_name": "DJI_0001.JPG"}],
        "categories": [{"id": 3, "name": "cattle"}],
        "annotations": [{"image_id": 1, "category_id": 3, "bbox": [100, 200, 50, 20], "score": 0.8}],
    }
    p = tmp_path / "coco.json"
    p.write_text(json.dumps(coco))
    (d,) = load_detections(p)
    assert d.frame_id == "DJI_0001.JPG" and d.label == "cattle"
    assert (d.x, d.y) == pytest.approx((125, 210))
    assert d.anchor("bottom") == pytest.approx((125, 220))

    y = tmp_path / "DJI_0002.txt"
    y.write_text("0 0.5 0.5 0.1 0.2 0.77\n")
    (d,) = load_detections(y, class_names=["cattle"])
    assert d.normalised and d.frame_id == "DJI_0002" and d.label == "cattle"
    scaled = d.scaled(4000, 3000)
    assert (scaled.x, scaled.y) == pytest.approx((2000, 1500))
    assert scaled.box == pytest.approx((1800, 1200, 2200, 1800))


def test_detection_log_without_boxes_is_rejected(tmp_path):
    p = tmp_path / "d.csv"
    p.write_text("image,label\na.jpg,cow\n")
    with pytest.raises(DetectionLogError):
        load_detections(p)


def test_unmatched_and_out_of_frame_detections_are_reported():
    track = parse_srt_text(MAVIC3_SRT)
    dets = [
        RawDetection(2000, 1500, "cow", 0.9, frame_number=1),
        RawDetection(2000, 1500, "cow", 0.9, frame_number=99),
        RawDetection(9000, 1500, "cow", 0.9, frame_number=2),
    ]
    dataset, report = build_dataset([track], dets, IngestOptions(camera=CAM))
    assert report.detections_matched == 1
    assert report.unmatched["no telemetry for frame"] == 1
    assert report.outside_image == 1
    assert len(dataset.frames) == 1


def test_multiple_tracks_need_a_source_column():
    a = parse_srt_text(MAVIC3_SRT, "A.SRT")
    b = parse_srt_text(MAVIC3_SRT, "B.SRT")
    dets = [
        RawDetection(2000, 1500, "cow", 0.9, frame_number=1),
        RawDetection(2000, 1500, "cow", 0.9, frame_number=1, source="b"),
    ]
    dataset, report = build_dataset([a, b], dets, IngestOptions(camera=CAM))
    assert report.detections_matched == 1
    assert dataset.frames[0].frame_id == "B#000001"


@pytest.mark.parametrize("telemetry, yaw", [("flight.SRT", True), ("telemetry.csv", True), ("flight.SRT", False)])
def test_raw_logs_round_trip_through_ingestion(tmp_path, telemetry, yaw):
    sim = simulate_survey(profile_config("standard", seed=11, n_animals=60))
    paths = export_raw_logs(sim, tmp_path, include_gimbal_yaw=yaw)
    dataset, report = ingest_files([tmp_path / telemetry], [paths["detections"]], IngestOptions(camera=CAM))
    assert report.detections_matched == sim.n_detections
    result = run_pipeline(dataset)
    assert result.dedup.n_unique == 60
    # Matches the truth positions within the 2 m body envelope.
    truth = np.array([result.utm.to_utm(g["longitude"], g["latitude"]) for g in sim.ground_truth])
    for a in result.animals:
        assert np.min(np.hypot(*(truth - [a.easting, a.northing]).T)) < 1.0


def test_cli_process_report(tmp_path, capsys):
    sim = simulate_survey(profile_config("standard", seed=2, n_animals=30))
    paths = export_raw_logs(sim, tmp_path)
    report_path = tmp_path / "report.json"
    rc = main(
        [
            "process",
            "--telemetry", str(paths["srt"]),
            "--detections", str(paths["detections"]),
            "--report", str(report_path),
            "-o", str(tmp_path / "animals.csv"),
            "--progress",
        ]
    )
    assert rc == 0
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert events[-1]["event"] == "done"
    assert [e["percent"] for e in events] == sorted(e["percent"] for e in events)
    report = json.loads(report_path.read_text())
    assert len(report["animals"]) == 30
    statuses = [d["status"] for d in report["detections"]]
    assert statuses.count("kept") == 30
    assert statuses.count("duplicate") == len(statuses) - 30 == report["summary"]["duplicate_detections"]
    assert report["flight_paths"][0]["coordinates"]
    assert report["bounds"][0][0] < report["bounds"][1][0]


def test_cli_process_errors_are_json_events(tmp_path, capsys):
    bad = tmp_path / "boxes.csv"
    bad.write_text("image,label\na,cow\n")
    srt = tmp_path / "f.SRT"
    srt.write_text(MAVIC3_SRT)
    rc = main(["process", "--telemetry", str(srt), "--detections", str(bad), "--report", str(tmp_path / "r.json"), "--progress"])
    assert rc == 2
    last = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert last["event"] == "error" and "bounding-box" in last["message"]
