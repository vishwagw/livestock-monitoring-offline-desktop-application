"""Density-aware clustering parameters.

The configured DBSCAN radius (2.0 m) is a cattle body envelope. Where animals
stand closer than that, as in sheep yards, feedlots or calves lying together,
the radius chains neighbouring animals into one cluster. Correct separation
then depends entirely on how well overlapping frames line up.

Relative positions *within one image* are precise (they share one camera
pose), so the nearest-neighbour spacing of detections inside each frame
measures how tightly packed the animals are, regardless of telemetry error.
When that spacing drops below eps, the engine switches to dense-group mode:

* frames are pre-aligned with clustering-free pairwise voting
  (:mod:`livestock_engine.sync`), because first-pass clusters are unreliable
  when animals are closer together than the raw telemetry error;
* eps is tightened to ``eps_spacing_fraction`` x the spacing (floored at
  ``min_eps_m``), so a radius never spans two neighbouring animals.

Open-pasture surveys, where animals are further apart than eps, are left
exactly as configured.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace

import numpy as np
from scipy.spatial import cKDTree

from .dedup import DedupConfig


@dataclass
class DensityInfo:
    mode: str  # "normal" or "dense"
    spacing_m: float | None  # in-frame nearest-neighbour spacing at density_quantile
    eps_m: float  # configured radius
    eps_effective_m: float  # radius actually used
    sync_pairs: int = 0
    sync_links: int = 0
    sync_residual_m: float | None = None

    def as_dict(self) -> dict:
        return {
            "mode": self.mode,
            "spacing_m": None if self.spacing_m is None else round(self.spacing_m, 3),
            "eps_m": self.eps_m,
            "eps_effective_m": round(self.eps_effective_m, 3),
            "sync_pairs": self.sync_pairs,
            "sync_links": self.sync_links,
            "sync_residual_m": None if self.sync_residual_m is None else round(self.sync_residual_m, 3),
        }


def in_frame_spacing(xy: np.ndarray, frame_ids: list[str], quantile: float) -> float | None:
    """Quantile of nearest-neighbour distances between detections of the same frame."""
    by_frame: dict[str, list[int]] = defaultdict(list)
    for i, f in enumerate(frame_ids):
        by_frame[f].append(i)
    dists = []
    for idx in by_frame.values():
        if len(idx) < 2:
            continue
        pts = xy[idx]
        d, _ = cKDTree(pts).query(pts, k=2)
        dists.append(d[:, 1])
    if not dists:
        return None
    return float(np.percentile(np.concatenate(dists), quantile))


def plan(xy: np.ndarray, frame_ids: list[str], config: DedupConfig) -> tuple[DedupConfig, DensityInfo]:
    """Decide normal vs dense mode and the effective clustering config."""
    spacing = in_frame_spacing(xy, frame_ids, config.density_quantile) if config.adaptive_density else None
    if spacing is None or spacing >= config.eps_m:
        return config, DensityInfo("normal", spacing, config.eps_m, config.eps_m)
    eps = float(np.clip(config.eps_spacing_fraction * spacing, config.min_eps_m, config.eps_m))
    # Duplicate boxes of one animal sit much closer than neighbouring animals.
    nms = min(config.frame_nms_m, 0.5 * eps) if config.frame_nms_m else 0.0
    return replace(config, eps_m=eps, split_gate_m=None, frame_nms_m=nms), DensityInfo(
        "dense", spacing, config.eps_m, eps
    )
