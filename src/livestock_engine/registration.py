"""Frame-to-consensus registration.

Telemetry errors (GPS offset, heading, altitude, gimbal pitch/roll) move all
of a frame's ground projections together, almost as a rigid 2D similarity
transform, while the animals' layout *within* a frame is very precise. After a
first clustering pass, each frame is therefore aligned onto the consensus of
the other frames that observed the same animals:

1. For every detection in a multi-frame cluster, the target is the
   leave-one-out centroid of the other frames' observations of that animal.
2. A robust (trimmed) similarity transform - translation, rotation, scale -
   is fitted per frame, falling back to translation only when the matches
   are too few or too compact to constrain rotation and scale.
3. Implausible corrections are rejected, all frames are updated at once and
   clustering is repeated.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from typing import Callable

import numpy as np

from .dedup import DedupConfig, DedupResult, GroundDetections, deduplicate


@dataclass(frozen=True)
class RegistrationConfig:
    iterations: int = 3
    min_matches_similarity: int = 3
    # RMS radius (m) the matched points must span to fit rotation/scale.
    min_spread_m: float = 5.0
    max_shift_m: float = 5.0
    max_rotation_deg: float = 5.0
    max_scale_error: float = 0.1
    # Matches whose residual exceeds this after the first fit are discarded.
    trim_residual_m: float | None = None


@dataclass
class FrameCorrection:
    frame_id: str
    dx_m: float
    dy_m: float
    rotation_deg: float
    scale: float
    n_matches: int
    model: str  # "similarity", "translation" or "none"


def fit_similarity(src: np.ndarray, dst: np.ndarray) -> tuple[float, np.ndarray, np.ndarray]:
    """Least-squares 2D similarity (Umeyama): dst ~= s * R @ src + t."""
    mu_s, mu_d = src.mean(axis=0), dst.mean(axis=0)
    s_c, d_c = src - mu_s, dst - mu_d
    cov = d_c.T @ s_c / len(src)
    u, sig, vt = np.linalg.svd(cov)
    sign = np.diag([1.0, np.sign(np.linalg.det(u) * np.linalg.det(vt)) or 1.0])
    rot = u @ sign @ vt
    var_s = (s_c**2).sum() / len(src)
    scale = float(np.trace(np.diag(sig) @ sign) / var_s) if var_s > 0 else 1.0
    trans = mu_d - scale * rot @ mu_s
    return scale, rot, trans


def _apply(points: np.ndarray, scale: float, rot: np.ndarray, trans: np.ndarray) -> np.ndarray:
    return scale * points @ rot.T + trans


def _estimate(
    frame_id: str, frame_pts: np.ndarray, src: np.ndarray, dst: np.ndarray, cfg: RegistrationConfig, trim: float
) -> tuple[FrameCorrection, np.ndarray]:
    identity = FrameCorrection(frame_id, 0.0, 0.0, 0.0, 1.0, len(src), "none")
    if len(src) == 0:
        return identity, frame_pts

    use_similarity = (
        len(src) >= cfg.min_matches_similarity
        and math.sqrt(((src - src.mean(axis=0)) ** 2).sum(axis=1).mean()) >= cfg.min_spread_m
    )

    def fit(s, d):
        if use_similarity and len(s) >= cfg.min_matches_similarity:
            return fit_similarity(s, d)
        return 1.0, np.eye(2), (d - s).mean(axis=0)

    scale, rot, trans = fit(src, dst)
    residual = np.linalg.norm(_apply(src, scale, rot, trans) - dst, axis=1)
    keep = residual <= trim
    if 0 < keep.sum() < len(src):
        src, dst = src[keep], dst[keep]
        scale, rot, trans = fit(src, dst)

    moved = _apply(frame_pts, scale, rot, trans)
    rotation = math.degrees(math.atan2(rot[1, 0], rot[0, 0]))
    shift = float(np.max(np.linalg.norm(moved - frame_pts, axis=1)))
    model = "similarity" if use_similarity and len(src) >= cfg.min_matches_similarity else "translation"

    plausible = (
        shift <= cfg.max_shift_m
        and abs(rotation) <= cfg.max_rotation_deg
        and abs(scale - 1.0) <= cfg.max_scale_error
    )
    if not plausible:
        # Fall back to a pure translation, which is always well conditioned.
        trans = (dst - src).mean(axis=0)
        if np.linalg.norm(trans) > cfg.max_shift_m:
            return identity, frame_pts
        scale, rot, rotation, model = 1.0, np.eye(2), 0.0, "translation"
        moved = frame_pts + trans

    centre = frame_pts.mean(axis=0)
    dx, dy = _apply(centre[None, :], scale, rot, trans)[0] - centre
    return FrameCorrection(frame_id, float(dx), float(dy), rotation, float(scale), len(src), model), moved


def register_frames(
    ground: GroundDetections,
    dedup_config: DedupConfig,
    config: RegistrationConfig | None = None,
    on_step: Callable[[int, int, DedupResult], None] | None = None,
) -> tuple[GroundDetections, DedupResult, dict[str, FrameCorrection]]:
    """Iteratively align frames and re-cluster. Returns corrected detections.

    ``on_step(step, iterations, result)`` is called after the initial
    clustering (step 0) and after every alignment pass.
    """
    config = config or RegistrationConfig()
    trim = config.trim_residual_m if config.trim_residual_m is not None else dedup_config.eps_m

    raw_xy = ground.xy
    xy = raw_xy.copy()
    frame_index: dict[str, list[int]] = defaultdict(list)
    for i, f in enumerate(ground.frame_ids):
        frame_index[f].append(i)

    current = ground
    result = deduplicate(current, dedup_config, resolve=config.iterations == 0)
    corrections: dict[str, FrameCorrection] = {}
    if on_step:
        on_step(0, config.iterations, result)

    for step in range(1, config.iterations + 1):
        assign = result.assignment
        n_clusters = result.n_unique
        valid = assign >= 0
        sums = np.zeros((n_clusters, 2))
        counts = np.zeros(n_clusters)
        np.add.at(sums, assign[valid], xy[valid])
        np.add.at(counts, assign[valid], 1)

        new_xy = xy.copy()
        for frame_id, idx in frame_index.items():
            idx = np.asarray(idx)
            k = assign[idx]
            # Leave-one-out targets: each cluster holds at most one detection
            # per frame when frame exclusivity is on, so subtracting the point
            # itself removes this frame's influence.
            usable = (k >= 0) & (counts[np.maximum(k, 0)] >= 2)
            src = xy[idx[usable]]
            kk = k[usable]
            dst = (sums[kk] - src) / (counts[kk] - 1)[:, None]
            corr, moved = _estimate(frame_id, xy[idx], src, dst, config, trim)
            new_xy[idx] = moved
            corrections[frame_id] = corr

        xy = new_xy
        current = GroundDetections(
            easting=xy[:, 0].copy(),
            northing=xy[:, 1].copy(),
            frame_ids=ground.frame_ids,
            labels=ground.labels,
            confidences=ground.confidences,
            detection_ids=ground.detection_ids,
            truth_ids=ground.truth_ids,
        )
        # Only the final pass needs full animal records.
        result = deduplicate(current, dedup_config, resolve=step == config.iterations)
        if on_step:
            on_step(step, config.iterations, result)

    update_cumulative(corrections, raw_xy, xy, frame_index)
    return current, result, corrections


def update_cumulative(
    corrections: dict[str, FrameCorrection],
    raw_xy: np.ndarray,
    xy: np.ndarray,
    frame_index: dict[str, list[int]],
) -> None:
    """Set each correction to the total move from ``raw_xy`` to ``xy``."""
    for frame_id, idx in frame_index.items():
        c = corrections.get(frame_id)
        if c is None:
            continue
        idx = np.asarray(idx)
        if len(idx) >= 2:
            scale, rot, trans = fit_similarity(raw_xy[idx], xy[idx])
        else:
            scale, rot, trans = 1.0, np.eye(2), xy[idx][0] - raw_xy[idx][0]
        centre = raw_xy[idx].mean(axis=0)
        dx, dy = _apply(centre[None, :], scale, rot, trans)[0] - centre
        c.dx_m, c.dy_m = float(dx), float(dy)
        c.rotation_deg = math.degrees(math.atan2(rot[1, 0], rot[0, 0]))
        c.scale = float(scale)
