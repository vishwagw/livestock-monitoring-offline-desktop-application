"""End-to-end pipeline: dataset -> ground coordinates -> unique animals."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .dedup import DedupConfig, DedupResult, GroundDetections, deduplicate
from .geo import LocalUTM
from .models import Dataset, Frame
from .projection import camera_rotation, cast_to_ground
from .registration import FrameCorrection, RegistrationConfig, register_frames


@dataclass
class ProjectionStats:
    n_detections: int = 0
    n_projected: int = 0
    rejected_detection_ids: list[str] = field(default_factory=list)


@dataclass
class PipelineResult:
    utm: LocalUTM
    ground: GroundDetections
    dedup: DedupResult
    projection: ProjectionStats
    # Unregistered projections, kept for auditing.
    raw_ground: GroundDetections | None = None
    frame_corrections: dict[str, FrameCorrection] = field(default_factory=dict)

    @property
    def animals(self):
        return self.dedup.animals

    def summary(self) -> dict:
        n_in = self.projection.n_detections
        n_unique = self.dedup.n_unique
        return {
            "utm_epsg": self.utm.epsg,
            "input_detections": n_in,
            "projected_detections": self.projection.n_projected,
            "rejected_detections": len(self.projection.rejected_detection_ids),
            "unique_animals": n_unique,
            "duplicates_removed": self.projection.n_projected - n_unique,
            "dbscan_clusters": self.dedup.n_dbscan_clusters,
            "frame_exclusivity_splits": self.dedup.n_frame_splits,
            "label_counts": _label_counts(self.dedup),
            "registration": _registration_summary(self.frame_corrections),
        }


def _registration_summary(corrections: dict[str, FrameCorrection]) -> dict:
    if not corrections:
        return {"enabled": False}
    shifts = np.array([np.hypot(c.dx_m, c.dy_m) for c in corrections.values()])
    rotations = np.array([abs(c.rotation_deg) for c in corrections.values()])
    models: dict[str, int] = {}
    for c in corrections.values():
        models[c.model] = models.get(c.model, 0) + 1
    return {
        "enabled": True,
        "frames": len(corrections),
        "models": models,
        "mean_shift_m": round(float(shifts.mean()), 3),
        "max_shift_m": round(float(shifts.max()), 3),
        "mean_abs_rotation_deg": round(float(rotations.mean()), 3),
    }


def _label_counts(dedup: DedupResult) -> dict[str, int]:
    counts: dict[str, int] = {}
    for a in dedup.animals:
        counts[a.label] = counts.get(a.label, 0) + 1
    return dict(sorted(counts.items()))


def project_frame(frame: Frame, utm: LocalUTM, max_range_m: float = 1000.0):
    """Ray-cast every detection of ``frame`` into UTM (easting, northing)."""
    pose = frame.pose
    easting, northing = utm.to_utm(pose.longitude, pose.latitude)
    grid_heading = pose.heading_deg + utm.grid_convergence_deg(pose.longitude, pose.latitude)
    rotation = camera_rotation(grid_heading, pose.gimbal_pitch_deg, pose.gimbal_roll_deg)
    u = [d.x for d in frame.detections]
    v = [d.y for d in frame.detections]
    return cast_to_ground(
        frame.camera,
        (easting, northing, pose.altitude_agl_m),
        rotation,
        u,
        v,
        max_range_m=max_range_m,
    )


def run_pipeline(
    dataset: Dataset,
    config: DedupConfig | None = None,
    max_range_m: float = 1000.0,
    registration: RegistrationConfig | None = RegistrationConfig(),
) -> PipelineResult:
    """Project, (optionally) register and de-duplicate a dataset.

    Pass ``registration=None`` to cluster the raw projections directly.
    """
    config = config or DedupConfig()
    if not dataset.frames:
        raise ValueError("dataset contains no frames")

    utm = LocalUTM.for_points(
        (f.pose.latitude for f in dataset.frames),
        (f.pose.longitude for f in dataset.frames),
    )

    eastings, northings, confidences = [], [], []
    frame_ids, labels, det_ids, truth_ids = [], [], [], []
    stats = ProjectionStats()

    for frame in dataset.frames:
        if not frame.detections:
            continue
        points, valid = project_frame(frame, utm, max_range_m=max_range_m)
        for j, det in enumerate(frame.detections):
            stats.n_detections += 1
            det_id = det.detection_id or f"{frame.frame_id}#{j}"
            if not valid[j]:
                stats.rejected_detection_ids.append(det_id)
                continue
            eastings.append(points[j, 0])
            northings.append(points[j, 1])
            confidences.append(det.confidence)
            frame_ids.append(frame.frame_id)
            labels.append(det.label)
            det_ids.append(det_id)
            truth_ids.append(det.truth_id)
    stats.n_projected = len(eastings)

    ground = GroundDetections(
        easting=np.asarray(eastings, dtype=float),
        northing=np.asarray(northings, dtype=float),
        frame_ids=frame_ids,
        labels=labels,
        confidences=np.asarray(confidences, dtype=float),
        detection_ids=det_ids,
        truth_ids=truth_ids,
    )
    raw_ground = ground
    corrections: dict[str, FrameCorrection] = {}
    if registration is not None and registration.iterations > 0 and len(ground):
        ground, dedup, corrections = register_frames(ground, config, registration)
    else:
        dedup = deduplicate(ground, config)

    if dedup.animals:
        lon, lat = utm.to_geographic(
            np.array([a.easting for a in dedup.animals]),
            np.array([a.northing for a in dedup.animals]),
        )
        for animal, la, lo in zip(dedup.animals, np.atleast_1d(lat), np.atleast_1d(lon)):
            animal.latitude = float(la)
            animal.longitude = float(lo)

    return PipelineResult(
        utm=utm,
        ground=ground,
        dedup=dedup,
        projection=stats,
        raw_ground=raw_ground,
        frame_corrections=corrections,
    )
