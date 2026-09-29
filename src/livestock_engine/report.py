"""Map-ready JSON report consumed by the desktop UI.

Every projected detection gets a ``status``:

* ``kept`` - the highest-confidence observation of an animal (one per animal)
* ``duplicate`` - any other observation of the same animal, i.e. the entries
  removed by de-duplication (drawn red on the map)

Detections carry both the aligned position used for clustering (``lat``/``lon``)
and the raw ray-cast position (``raw_lat``/``raw_lon``).

Unique animals (drawn green) are reported at their cluster centroid.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from .models import Dataset
from .pipeline import PipelineResult

REPORT_VERSION = 1


def _r(value: float, digits: int) -> float:
    return round(float(value), digits)


def build_report(dataset: Dataset, result: PipelineResult, warnings: list[str] | None = None) -> dict[str, Any]:
    ground = result.ground
    assignment = result.dedup.assignment
    animals = result.animals

    raw = result.raw_ground if result.raw_ground is not None else ground
    det_lon = det_lat = raw_lon = raw_lat = np.empty(0)
    if len(ground):
        det_lon, det_lat = (np.atleast_1d(v) for v in result.utm.to_geographic(ground.easting, ground.northing))
        raw_lon, raw_lat = (np.atleast_1d(v) for v in result.utm.to_geographic(raw.easting, raw.northing))

    representative: dict[int, int] = {}
    for i, k in enumerate(assignment):
        if k < 0:
            continue
        best = representative.get(int(k))
        if best is None or ground.confidences[i] > ground.confidences[best]:
            representative[int(k)] = i

    detections = []
    for i in range(len(ground)):
        k = int(assignment[i])
        detections.append(
            {
                "id": ground.detection_ids[i],
                "frame_id": ground.frame_ids[i],
                "lat": _r(det_lat[i], 8),
                "lon": _r(det_lon[i], 8),
                # Straight ray-cast position before frame alignment: where the
                # observation would have been counted without de-duplication.
                "raw_lat": _r(raw_lat[i], 8),
                "raw_lon": _r(raw_lon[i], 8),
                "label": ground.labels[i],
                "confidence": _r(ground.confidences[i], 4),
                "animal_id": animals[k].animal_id if k >= 0 else None,
                "status": "kept" if representative.get(k) == i else ("duplicate" if k >= 0 else "noise"),
            }
        )

    frames = []
    for f in dataset.frames:
        frames.append(
            {
                "id": f.frame_id,
                "lat": _r(f.pose.latitude, 8),
                "lon": _r(f.pose.longitude, 8),
                "altitude_agl_m": _r(f.pose.altitude_agl_m, 2),
                "heading_deg": _r(f.pose.heading_deg, 2),
                "gimbal_pitch_deg": _r(f.pose.gimbal_pitch_deg, 2),
                "detections": len(f.detections),
                "timestamp": f.timestamp,
            }
        )

    flight_paths = dataset.metadata.get("flight_paths")
    if not flight_paths:
        flight_paths = [{"name": "flight", "coordinates": [[fr["lat"], fr["lon"]] for fr in frames]}]

    lats = [a.latitude for a in animals] + [fr["lat"] for fr in frames]
    lons = [a.longitude for a in animals] + [fr["lon"] for fr in frames]
    for path in flight_paths:
        for lat, lon in path["coordinates"]:
            lats.append(lat)
            lons.append(lon)
    bounds = [[min(lats), min(lons)], [max(lats), max(lons)]] if lats else None

    summary = result.summary()
    summary["frames"] = len(dataset.frames)
    summary["duplicate_detections"] = sum(1 for d in detections if d["status"] == "duplicate")

    return {
        "version": REPORT_VERSION,
        "summary": summary,
        "bounds": bounds,
        "animals": [
            {
                "id": a.animal_id,
                "lat": _r(a.latitude, 8),
                "lon": _r(a.longitude, 8),
                "easting": _r(a.easting, 3),
                "northing": _r(a.northing, 3),
                "label": a.label,
                "confidence": _r(a.confidence, 4),
                "observations": a.n_observations,
                "frames": len(a.frame_ids),
                "spread_m": _r(a.spread_m, 3),
                "class_conflict": a.class_conflict,
                "label_votes": a.label_votes,
            }
            for a in animals
        ],
        "detections": detections,
        "rejected_detections": result.projection.rejected_detection_ids,
        "frames": frames,
        "flight_paths": flight_paths,
        "ingest": dataset.metadata.get("ingest"),
        "warnings": list(warnings or []),
    }
