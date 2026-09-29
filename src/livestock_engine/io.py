"""Reading flight datasets and writing de-duplicated results.

Two input formats are supported:

* **JSON** (``.json``) - frames with nested detections, optional shared
  ``camera`` block and optional ``ground_truth`` (see README).
* **CSV** (``.csv``) - one row per detection, with the frame metadata
  repeated on every row.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from .camera import CameraModel, FramePose
from .models import Dataset, Detection, Frame
from .pipeline import PipelineResult

CAMERA_KEYS = ("image_width", "image_height", "fov_deg", "fov_type", "cx", "cy")
REQUIRED_CSV_COLUMNS = (
    "frame_id",
    "latitude",
    "longitude",
    "altitude_agl_m",
    "heading_deg",
    "image_width",
    "image_height",
    "fov_deg",
    "x",
    "y",
)


class DatasetError(ValueError):
    """Raised when an input file is malformed."""


def load_dataset(path: str | Path) -> Dataset:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".json":
        with path.open("r", encoding="utf-8") as fh:
            return dataset_from_dict(json.load(fh))
    if suffix == ".csv":
        return _load_csv(path)
    raise DatasetError(f"unsupported input format {suffix!r} (expected .json or .csv)")


def _camera_from(values: dict[str, Any], context: str) -> CameraModel:
    try:
        return CameraModel(
            image_width=int(values["image_width"]),
            image_height=int(values["image_height"]),
            fov_deg=float(values["fov_deg"]),
            fov_type=str(values.get("fov_type") or "horizontal"),
            cx=_opt_float(values.get("cx")),
            cy=_opt_float(values.get("cy")),
        )
    except KeyError as exc:
        raise DatasetError(f"{context}: missing camera field {exc.args[0]!r}") from None
    except ValueError as exc:
        raise DatasetError(f"{context}: {exc}") from None


def _pose_from(values: dict[str, Any], context: str) -> FramePose:
    try:
        return FramePose(
            latitude=float(values["latitude"]),
            longitude=float(values["longitude"]),
            altitude_agl_m=float(values["altitude_agl_m"]),
            heading_deg=float(values["heading_deg"]),
            gimbal_pitch_deg=float(_default(values.get("gimbal_pitch_deg"), -90.0)),
            gimbal_roll_deg=float(_default(values.get("gimbal_roll_deg"), 0.0)),
        )
    except KeyError as exc:
        raise DatasetError(f"{context}: missing pose field {exc.args[0]!r}") from None
    except ValueError as exc:
        raise DatasetError(f"{context}: {exc}") from None


def _detection_from(values: dict[str, Any], context: str) -> Detection:
    try:
        return Detection(
            x=float(values["x"]),
            y=float(values["y"]),
            label=str(values.get("label") or "animal"),
            confidence=float(_default(values.get("confidence"), 1.0)),
            detection_id=_opt_str(values.get("detection_id")),
            truth_id=_opt_str(values.get("truth_id")),
        )
    except KeyError as exc:
        raise DatasetError(f"{context}: missing detection field {exc.args[0]!r}") from None
    except ValueError as exc:
        raise DatasetError(f"{context}: {exc}") from None


def dataset_from_dict(data: dict[str, Any]) -> Dataset:
    if "frames" not in data:
        raise DatasetError("JSON input must contain a 'frames' list")
    shared_camera = data.get("camera") or {}
    frames = []
    for i, raw in enumerate(data["frames"]):
        frame_id = str(raw.get("frame_id") or f"frame_{i:05d}")
        context = f"frame {frame_id}"
        camera_values = {**shared_camera, **{k: raw[k] for k in CAMERA_KEYS if k in raw}}
        camera_values.update(raw.get("camera") or {})
        frames.append(
            Frame(
                frame_id=frame_id,
                pose=_pose_from(raw, context),
                camera=_camera_from(camera_values, context),
                detections=[
                    _detection_from(d, f"{context} detection {j}")
                    for j, d in enumerate(raw.get("detections") or [])
                ],
                timestamp=raw.get("timestamp"),
            )
        )
    return Dataset(
        frames=frames,
        ground_truth=data.get("ground_truth"),
        metadata=data.get("metadata") or {},
    )


def dataset_to_dict(dataset: Dataset) -> dict[str, Any]:
    frames = []
    for f in dataset.frames:
        frames.append(
            {
                "frame_id": f.frame_id,
                "timestamp": f.timestamp,
                "latitude": f.pose.latitude,
                "longitude": f.pose.longitude,
                "altitude_agl_m": f.pose.altitude_agl_m,
                "heading_deg": f.pose.heading_deg,
                "gimbal_pitch_deg": f.pose.gimbal_pitch_deg,
                "gimbal_roll_deg": f.pose.gimbal_roll_deg,
                "camera": {
                    "image_width": f.camera.image_width,
                    "image_height": f.camera.image_height,
                    "fov_deg": f.camera.fov_deg,
                    "fov_type": f.camera.fov_type,
                },
                "detections": [
                    {
                        k: v
                        for k, v in {
                            "detection_id": d.detection_id,
                            "x": round(d.x, 2),
                            "y": round(d.y, 2),
                            "label": d.label,
                            "confidence": round(d.confidence, 4),
                            "truth_id": d.truth_id,
                        }.items()
                        if v is not None
                    }
                    for d in f.detections
                ],
            }
        )
    out: dict[str, Any] = {"metadata": dataset.metadata, "frames": frames}
    if dataset.ground_truth is not None:
        out["ground_truth"] = dataset.ground_truth
    return out


def save_dataset(dataset: Dataset, path: str | Path) -> None:
    path = Path(path)
    if path.suffix.lower() != ".json":
        raise DatasetError("datasets can only be saved as .json")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(dataset_to_dict(dataset), fh, indent=1)


def _load_csv(path: Path) -> Dataset:
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        missing = [c for c in REQUIRED_CSV_COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise DatasetError(f"CSV input is missing columns: {', '.join(missing)}")
        frames: dict[str, Frame] = {}
        for line_no, row in enumerate(reader, start=2):
            context = f"{path.name}:{line_no}"
            frame_id = row["frame_id"]
            frame = frames.get(frame_id)
            if frame is None:
                frame = Frame(
                    frame_id=frame_id,
                    pose=_pose_from(row, context),
                    camera=_camera_from(row, context),
                    timestamp=row.get("timestamp") or None,
                )
                frames[frame_id] = frame
            frame.detections.append(_detection_from(row, context))
    return Dataset(frames=list(frames.values()))


ANIMAL_COLUMNS = (
    "animal_id",
    "label",
    "confidence",
    "latitude",
    "longitude",
    "easting",
    "northing",
    "n_observations",
    "n_frames",
    "spread_m",
    "class_conflict",
    "label_votes",
    "frame_ids",
    "detection_ids",
)


def write_animals_csv(result: PipelineResult, path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow([*ANIMAL_COLUMNS, "utm_epsg"])
        for a in result.animals:
            writer.writerow(
                [
                    a.animal_id,
                    a.label,
                    f"{a.confidence:.4f}",
                    f"{a.latitude:.8f}",
                    f"{a.longitude:.8f}",
                    f"{a.easting:.3f}",
                    f"{a.northing:.3f}",
                    a.n_observations,
                    len(a.frame_ids),
                    f"{a.spread_m:.3f}",
                    int(a.class_conflict),
                    json.dumps(a.label_votes, sort_keys=True),
                    ";".join(a.frame_ids),
                    ";".join(a.detection_ids),
                    result.utm.epsg,
                ]
            )


def write_geojson(result: PipelineResult, path: str | Path) -> None:
    features = [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [a.longitude, a.latitude]},
            "properties": {
                "animal_id": a.animal_id,
                "label": a.label,
                "confidence": round(a.confidence, 4),
                "n_observations": a.n_observations,
                "spread_m": round(a.spread_m, 3),
                "class_conflict": a.class_conflict,
                "frame_ids": a.frame_ids,
            },
        }
        for a in result.animals
    ]
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump({"type": "FeatureCollection", "features": features}, fh, indent=1)


def write_assignments_csv(result: PipelineResult, path: str | Path) -> None:
    """Per-detection audit trail: where each detection landed and which animal it became."""
    g = result.ground
    raw = result.raw_ground or g
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "detection_id",
                "frame_id",
                "label",
                "confidence",
                "raw_easting",
                "raw_northing",
                "easting",
                "northing",
                "animal_id",
            ]
        )
        for i in range(len(g)):
            k = result.dedup.assignment[i]
            writer.writerow(
                [
                    g.detection_ids[i],
                    g.frame_ids[i],
                    g.labels[i],
                    f"{g.confidences[i]:.4f}",
                    f"{raw.easting[i]:.3f}",
                    f"{raw.northing[i]:.3f}",
                    f"{g.easting[i]:.3f}",
                    f"{g.northing[i]:.3f}",
                    result.animals[k].animal_id if k >= 0 else "",
                ]
            )
        for det_id in result.projection.rejected_detection_ids:
            writer.writerow([det_id, "", "", "", "", "", "", "", "REJECTED_NO_GROUND_INTERSECTION"])


def write_frame_corrections_csv(result: PipelineResult, path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["frame_id", "model", "n_matches", "dx_m", "dy_m", "rotation_deg", "scale"])
        for c in result.frame_corrections.values():
            writer.writerow(
                [
                    c.frame_id,
                    c.model,
                    c.n_matches,
                    f"{c.dx_m:.3f}",
                    f"{c.dy_m:.3f}",
                    f"{c.rotation_deg:.4f}",
                    f"{c.scale:.5f}",
                ]
            )


def _default(value, fallback):
    return fallback if value is None or value == "" else value


def _opt_float(value) -> float | None:
    return None if value is None or value == "" else float(value)


def _opt_str(value) -> str | None:
    return None if value is None or value == "" else str(value)
