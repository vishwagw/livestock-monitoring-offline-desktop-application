"""Input data model: frames, their metadata and the raw detections."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .camera import CameraModel, FramePose


@dataclass
class Detection:
    """A single animal detection in image space (typically a bbox centre)."""

    x: float
    y: float
    label: str = "animal"
    confidence: float = 1.0
    detection_id: str | None = None
    # Only present in simulated data; never used by the engine itself.
    truth_id: str | None = None

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be in [0, 1], got {self.confidence}")


@dataclass
class Frame:
    frame_id: str
    pose: FramePose
    camera: CameraModel
    detections: list[Detection] = field(default_factory=list)
    timestamp: str | None = None


@dataclass
class Dataset:
    frames: list[Frame]
    ground_truth: list[dict[str, Any]] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def n_detections(self) -> int:
        return sum(len(f.detections) for f in self.frames)

    @property
    def has_truth_labels(self) -> bool:
        return any(d.truth_id is not None for f in self.frames for d in f.detections)
