#!/usr/bin/env python3
"""Build the self-contained web demo from real engine runs.

    python demo/build_web_demo.py        # -> demo/web/livestock-counter-demo.html

Each scenario is simulated, written out as the raw files a pilot brings home
(DJI .SRT + bounding-box CSV), then processed exactly like the desktop app
does (ingest -> pipeline). The page embeds the engine's output: raw
sightings, unique animals, which sightings merged into which animal, each
image's ground footprint for the flight replay, and the accuracy against the
simulation's ground truth.
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from livestock_engine import __version__  # noqa: E402
from livestock_engine.camera import CameraModel  # noqa: E402
from livestock_engine.ingest import IngestOptions, ingest_files  # noqa: E402
from livestock_engine.pipeline import run_pipeline  # noqa: E402
from livestock_engine.projection import camera_rotation, cast_to_ground  # noqa: E402
from livestock_engine.simulate import export_raw_logs, profile_config, simulate_survey  # noqa: E402
from livestock_engine.validation import validate_points  # noqa: E402

TEMPLATE = ROOT / "demo" / "web" / "template.html"
OUT = ROOT / "demo" / "web" / "livestock-counter-demo.html"

SCENARIOS = [
    {
        "id": "paddock",
        "label": "Cattle paddock",
        "blurb": "150 head across a 300 x 200 m paddock. Mapping flight at 60 m, 70% front / 60% side overlap.",
        "config": profile_config("standard", seed=7),
    },
    {
        "id": "yard",
        "label": "Sheep yards",
        "blurb": "480 sheep packed into four yards, about 0.9 m apart. Flown at 40 m. Dense-group mode switches on automatically.",
        "config": profile_config("sheep_pen", seed=2),
    },
    {
        "id": "station",
        "label": "Station block",
        "blurb": "1,200 head over 85 ha with consumer-grade GPS and compass drift (about twice the paddock's noise).",
        "config": replace(
            profile_config("harsh", seed=3),
            n_animals=1200, n_herds=10, field_width_m=850.0, field_height_m=570.0,
        ),
    },
]


def r(v: float, nd: int = 2) -> float:
    return round(float(v), nd)


def build_scenario(spec: dict) -> dict:
    cfg = spec["config"]
    sim = simulate_survey(cfg)
    with tempfile.TemporaryDirectory() as tmp:
        paths = export_raw_logs(sim, tmp)
        camera = CameraModel(cfg.image_width, cfg.image_height, cfg.fov_deg, cfg.fov_type)
        started = time.perf_counter()
        dataset, _ = ingest_files([paths["srt"]], [paths["detections"]], IngestOptions(camera=camera))
        result = run_pipeline(dataset)
        elapsed = time.perf_counter() - started
        truth = [{"latitude": g["latitude"], "longitude": g["longitude"], "label": g["label"], "row": i}
                 for i, g in enumerate(sim.ground_truth)]

    utm = result.utm
    animals_xy = np.array([[a.easting, a.northing] for a in result.animals])
    origin = animals_xy.mean(axis=0)

    # Each simulated capture in flight order, with its ground footprint (raw telemetry).
    frames = []
    for f in sim.frames:
        e, n = utm.to_utm(f.pose.longitude, f.pose.latitude)
        grid_heading = f.pose.heading_deg + utm.grid_convergence_deg(f.pose.longitude, f.pose.latitude)
        rot = camera_rotation(grid_heading, f.pose.gimbal_pitch_deg, f.pose.gimbal_roll_deg)
        corners, _ = cast_to_ground(
            camera, (e, n, f.pose.altitude_agl_m), rot,
            [0, cfg.image_width, cfg.image_width, 0], [0, 0, cfg.image_height, cfg.image_height],
        )
        frames.append([r(e - origin[0], 1), r(n - origin[1], 1)] +
                      [r(v, 1) for v in (corners - origin).ravel()])

    # Ingested frame ids are "<srt>#<FrameCnt>", FrameCnt = capture index + 1.
    g = result.ground
    raw = result.raw_ground
    best = {}
    for i, k in enumerate(result.dedup.assignment):
        if k >= 0 and (k not in best or g.confidences[i] > g.confidences[best[k]]):
            best[k] = i
    dets = []
    for i in range(len(g)):
        frame_idx = int(re.search(r"#(\d+)$", g.frame_ids[i]).group(1)) - 1
        k = int(result.dedup.assignment[i])
        dets.append([r(raw.easting[i] - origin[0]), r(raw.northing[i] - origin[1]), k, frame_idx,
                     1 if best.get(k) == i else 0])
    dets.sort(key=lambda d: d[3])

    report = {"animals": [{"id": a.animal_id, "lat": a.latitude, "lon": a.longitude, "label": a.label}
                          for a in result.animals],
              "summary": {"density": result.density.as_dict()}}
    val = validate_points(report, truth)
    density = result.density
    return {
        "id": spec["id"],
        "label": spec["label"],
        "blurb": spec["blurb"],
        "species": result.animals[0].label if result.animals else "animal",
        "frames": frames,
        "dets": dets,
        "animals": [[r(a.easting - origin[0]), r(a.northing - origin[1]), a.n_observations,
                     a.label, r(a.confidence, 3)] for a in result.animals],
        "stats": {
            "images": len(sim.frames),
            "raw": len(g),
            "unique": result.dedup.n_unique,
            "truth": len(truth),
            "accuracy": r(val.count_accuracy, 4),
            "precision": r(val.precision, 4),
            "recall": r(val.recall, 4),
            "meanErrorM": r(val.mean_position_error_m, 2),
            "seconds": r(elapsed, 2),
            "mode": density.mode,
            "spacingM": density.spacing_m and r(density.spacing_m, 2),
            "epsM": r(density.eps_effective_m, 2),
            "altitudeM": cfg.altitude_agl_m,
            "alignShiftM": result.summary()["registration"].get("mean_shift_m"),
        },
    }


def main() -> int:
    # Warm up imports and native libraries so the first scenario's time is comparable.
    run_pipeline(simulate_survey(profile_config("standard", seed=0, n_animals=20)))
    scenarios = []
    for spec in SCENARIOS:
        s = build_scenario(spec)
        st = s["stats"]
        print(f"{s['id']:8s} {st['images']:5d} images {st['raw']:6d} sightings -> {st['unique']:5d} animals "
              f"(truth {st['truth']}, accuracy {st['accuracy']:.2%}, {st['seconds']} s, {st['mode']})")
        scenarios.append(s)
    data = {"engineVersion": __version__, "scenarios": scenarios}
    payload = json.dumps(data, separators=(",", ":"))
    html = TEMPLATE.read_text(encoding="utf-8").replace("/*__DEMO_DATA__*/null", payload)
    OUT.write_text(html, encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)} ({OUT.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
