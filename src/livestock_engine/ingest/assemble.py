"""Join bounding-box logs to flight telemetry and build an engine Dataset."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from ..camera import CameraModel, FramePose
from ..models import Dataset, Detection, Frame
from .detections import RawDetection, load_detections
from .srt import load_srt
from .telemetry import TelemetrySample, TelemetryTrack, frame_key
from .telemetry_csv import load_telemetry_csv

BBOX_ANCHORS = ("center", "bottom")
MAX_PATH_POINTS = 4000


@dataclass(frozen=True)
class IngestOptions:
    camera: CameraModel
    default_pitch_deg: float = -90.0
    default_altitude_m: float | None = None
    video_fps: float = 30.0
    bbox_anchor: str = "center"
    time_tolerance_s: float = 0.5
    derive_heading: bool = True

    def __post_init__(self) -> None:
        if self.bbox_anchor not in BBOX_ANCHORS:
            raise ValueError(f"bbox_anchor must be one of {BBOX_ANCHORS}")
        if self.video_fps <= 0:
            raise ValueError("video_fps must be positive")


@dataclass
class IngestReport:
    detections_in: int = 0
    detections_matched: int = 0
    frames: int = 0
    unmatched: Counter = field(default_factory=Counter)
    outside_image: int = 0
    warnings: list[str] = field(default_factory=list)
    tracks: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "detections_in": self.detections_in,
            "detections_matched": self.detections_matched,
            "frames": self.frames,
            "unmatched": dict(self.unmatched),
            "outside_image": self.outside_image,
            "warnings": self.warnings,
            "tracks": self.tracks,
        }


class IngestError(ValueError):
    pass


def load_telemetry(path: str | Path) -> TelemetryTrack:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".srt":
        return load_srt(path)
    if suffix == ".csv":
        return load_telemetry_csv(path)
    raise IngestError(f"{path.name}: unsupported telemetry format {suffix!r} (expected .srt or .csv)")


def _resolve_tracks(det: RawDetection, tracks: list[TelemetryTrack]) -> list[TelemetryTrack] | str:
    if det.source:
        key = frame_key(det.source)
        matches = [t for t in tracks if frame_key(t.name) == key]
        return matches or "unknown telemetry source"
    if len(tracks) == 1 or det.frame_id is not None:
        return tracks
    return "ambiguous telemetry (add a 'video' column naming the log file)"


def _lookup(
    det: RawDetection, track: TelemetryTrack, opts: IngestOptions
) -> tuple[TelemetrySample | None, str | None]:
    """Return the telemetry sample for a detection and a stable frame name."""
    if det.frame_id is not None:
        sample = track.by_frame_id(det.frame_id)
        if sample is not None:
            return sample, frame_key(det.frame_id)
        if det.frame_id.isdigit() and det.frame_number is None:
            det = RawDetection(**{**det.__dict__, "frame_number": int(det.frame_id)})
        elif det.frame_number is None and det.time_s is None:
            return None, None
    if det.frame_number is not None:
        if track.has_frame_numbers:
            sample = track.by_frame_number(det.frame_number)
        else:
            sample = track.at_time(det.frame_number / opts.video_fps, opts.time_tolerance_s)
        return sample, f"{track.name}#{det.frame_number:06d}"
    if det.time_s is not None:
        return track.at_time(det.time_s, opts.time_tolerance_s), f"{track.name}@{det.time_s:.3f}s"
    return None, None


def _decimate(samples: list[TelemetrySample]) -> list[list[float]]:
    step = max(1, len(samples) // MAX_PATH_POINTS)
    return [[round(s.latitude, 7), round(s.longitude, 7)] for s in samples[::step]]


def build_dataset(
    tracks: list[TelemetryTrack], detections: list[RawDetection], opts: IngestOptions
) -> tuple[Dataset, IngestReport]:
    report = IngestReport(detections_in=len(detections))
    if not tracks:
        raise IngestError("at least one telemetry file (.SRT or telemetry CSV) is required")

    for track in tracks:
        missing = sum(1 for s in track.samples if s.heading_deg is None)
        if missing and opts.derive_heading:
            track.fill_missing_headings()
        report.warnings.extend(track.warnings)
        report.tracks.append(
            {
                "name": track.name,
                "samples": len(track.samples),
                "derived_headings": sum(1 for s in track.samples if s.heading_derived),
            }
        )

    cam = opts.camera
    frames: dict[str, Frame] = {}
    for det in detections:
        candidates = _resolve_tracks(det, tracks)
        if isinstance(candidates, str):
            report.unmatched[candidates] += 1
            continue
        sample, name = None, None
        for track in candidates:
            sample, name = _lookup(det, track, opts)
            if sample is not None:
                break
        if sample is None:
            report.unmatched["no telemetry for frame"] += 1
            continue

        altitude = sample.altitude_agl_m if sample.altitude_agl_m is not None else opts.default_altitude_m
        if altitude is None or altitude <= 0:
            report.unmatched["no altitude above ground"] += 1
            continue
        if sample.heading_deg is None:
            report.unmatched["no heading"] += 1
            continue

        det = det.scaled(cam.image_width, cam.image_height)
        u, v = det.anchor(opts.bbox_anchor)
        if not (0 <= u <= cam.image_width and 0 <= v <= cam.image_height):
            report.outside_image += 1
            continue

        frame = frames.get(name)
        if frame is None:
            pose = FramePose(
                latitude=sample.latitude,
                longitude=sample.longitude,
                altitude_agl_m=altitude,
                heading_deg=sample.heading_deg,
                gimbal_pitch_deg=(
                    sample.gimbal_pitch_deg if sample.gimbal_pitch_deg is not None else opts.default_pitch_deg
                ),
                gimbal_roll_deg=sample.gimbal_roll_deg or 0.0,
            )
            frame = Frame(frame_id=name, pose=pose, camera=cam, timestamp=sample.timestamp)
            frames[name] = frame
        frame.detections.append(
            Detection(
                x=u,
                y=v,
                label=det.label,
                confidence=det.confidence,
                detection_id=det.detection_id or det.origin,
            )
        )
        report.detections_matched += 1

    if report.outside_image:
        report.warnings.append(
            f"{report.outside_image} detections fall outside the configured image size "
            f"({cam.image_width}x{cam.image_height}); check the camera settings"
        )
    if report.unmatched:
        details = ", ".join(f"{n} {reason}" for reason, n in report.unmatched.items())
        report.warnings.append(f"skipped detections: {details}")

    report.frames = len(frames)
    metadata = {
        "flight_paths": [{"name": t.name, "coordinates": _decimate(t.ordered_samples)} for t in tracks],
        "ingest": report.as_dict(),
    }
    return Dataset(frames=list(frames.values()), metadata=metadata), report


def ingest_files(
    telemetry_paths: list[str | Path],
    detection_paths: list[str | Path],
    opts: IngestOptions,
    class_names: list[str] | None = None,
) -> tuple[Dataset, IngestReport]:
    tracks = [load_telemetry(p) for p in telemetry_paths]
    detections: list[RawDetection] = []
    for p in detection_paths:
        detections.extend(load_detections(p, class_names))
    if not detections:
        raise IngestError("no detections found in the bounding-box logs")
    return build_dataset(tracks, detections, opts)
