"""Core spatial processing engine for de-duplicating livestock detections.

Pipeline overview::

    pixel (u, v) + flight metadata
        -> ray cast onto the ground plane         (projection)
        -> metric easting/northing in local UTM   (geo)
        -> DBSCAN clustering, eps = 2.0 m         (dedup)
        -> one centroid record per animal
"""

from .camera import CameraModel, FramePose
from .dedup import AnimalRecord, DedupConfig, DedupResult, deduplicate
from .geo import LocalUTM
from .models import Dataset, Detection, Frame
from .pipeline import PipelineResult, run_pipeline

__version__ = "0.4.0"

__all__ = [
    "AnimalRecord",
    "CameraModel",
    "Dataset",
    "DedupConfig",
    "DedupResult",
    "Detection",
    "Frame",
    "FramePose",
    "LocalUTM",
    "PipelineResult",
    "deduplicate",
    "run_pipeline",
]
