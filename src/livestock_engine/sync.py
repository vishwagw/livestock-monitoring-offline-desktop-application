"""Clustering-free frame alignment by pairwise displacement voting.

Telemetry errors move a frame's projected detections almost rigidly, so for
two overlapping frames *a* and *b* every correctly paired observation of the
same animal has (nearly) the same displacement ``e_b - e_a``, while pairings
with neighbouring animals scatter. For each overlapping frame pair the engine
therefore:

1. collects the displacement vectors between detections of *a* and *b* that
   lie within ``radius_m`` of each other (a KD-tree query over all points),
2. votes them into a 2-D histogram and takes the smoothed peak as the pair's
   relative offset, refined by averaging the votes around the peak.

The per-pair offsets are then reconciled into one translation per frame by a
robust (Huber-weighted, iteratively re-weighted) sparse least-squares solve
over the frame graph: ``t_b - t_a = d_ab`` for every pair, plus a weak prior
``t = 0`` that fixes the gauge so the survey stays on its GPS average.

Unlike cluster-based alignment this does not need correct clusters first,
which is what makes it work in dense groups (sheep yards, feedlots), where
animals stand closer together than the raw telemetry error.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.sparse import coo_matrix, vstack
from scipy.sparse.linalg import lsqr
from scipy.spatial import cKDTree


@dataclass(frozen=True)
class SyncConfig:
    radius_m: float = 1.5
    bin_m: float = 0.08
    min_votes: int = 3
    huber_m: float = 0.15
    prior_weight: float = 0.05
    irls_iterations: int = 4


@dataclass
class SyncResult:
    translations: np.ndarray  # (n_frames, 2) estimated error to subtract
    n_pairs: int
    n_links: int  # pairs with enough votes to constrain the solve
    residual_m: float  # median |t_b - t_a - d_ab| after the solve


def pairwise_offsets(
    xy: np.ndarray, frame_code: np.ndarray, config: SyncConfig
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Relative offset ``d_ab`` and vote support for every overlapping frame pair.

    Returns ``(frame_a, frame_b, offset (k, 2), support (k,))`` with a < b.
    """
    empty = (np.empty(0, int), np.empty(0, int), np.empty((0, 2)), np.empty(0, int))
    if len(xy) < 2:
        return empty
    pairs = cKDTree(xy).query_pairs(config.radius_m, output_type="ndarray")
    if len(pairs) == 0:
        return empty
    fi, fj = frame_code[pairs[:, 0]], frame_code[pairs[:, 1]]
    cross = fi != fj
    pairs, fi, fj = pairs[cross], fi[cross], fj[cross]
    if len(pairs) == 0:
        return empty
    # Orient every pair from the lower frame code to the higher one.
    swap = fi > fj
    i = np.where(swap, pairs[:, 1], pairs[:, 0])
    j = np.where(swap, pairs[:, 0], pairs[:, 1])
    fa, fb = frame_code[i], frame_code[j]
    d = xy[j] - xy[i]

    # Histogram votes, smoothed by also voting into the 8 neighbouring bins.
    half = int(np.ceil(config.radius_m / config.bin_m)) + 1
    width = 2 * half + 1
    bx = np.floor(d[:, 0] / config.bin_m).astype(np.int64) + half
    by = np.floor(d[:, 1] / config.bin_m).astype(np.int64) + half
    n_frames = int(frame_code.max()) + 1
    pair_key = fa.astype(np.int64) * n_frames + fb
    offsets = np.array([(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 0), (0, 1), (1, -1), (1, 0), (1, 1)])
    codes = (
        pair_key[:, None] * (width * width)
        + (bx[:, None] + offsets[:, 0]) * width
        + (by[:, None] + offsets[:, 1])
    ).ravel()
    uniq, counts = np.unique(codes, return_counts=True)
    upair = uniq // (width * width)
    order = np.lexsort((-counts, upair))
    first = order[np.r_[True, upair[order][1:] != upair[order][:-1]]]
    peak_pair = upair[first]
    peak_bin = uniq[first] % (width * width)
    peak_center = np.column_stack(
        [(peak_bin // width - half + 0.5) * config.bin_m, (peak_bin % width - half + 0.5) * config.bin_m]
    )

    # Refine: average the raw displacements within 1.5 bins of the peak.
    idx = np.searchsorted(peak_pair, pair_key)
    near = np.linalg.norm(d - peak_center[idx], axis=1) <= 1.5 * config.bin_m
    k = len(peak_pair)
    support = np.bincount(idx[near], minlength=k)
    sums = np.zeros((k, 2))
    np.add.at(sums, idx[near], d[near])
    ok = support >= config.min_votes
    offset = sums[ok] / support[ok, None]
    return (peak_pair[ok] // n_frames).astype(int), (peak_pair[ok] % n_frames).astype(int), offset, support[ok]


def synchronise(xy: np.ndarray, frame_code: np.ndarray, config: SyncConfig | None = None) -> SyncResult:
    """Estimate a translation per frame from pairwise offsets (robust least squares)."""
    config = config or SyncConfig()
    n = int(frame_code.max()) + 1 if len(frame_code) else 0
    fa, fb, d, support = pairwise_offsets(xy, frame_code, config)
    if len(fa) == 0:
        return SyncResult(np.zeros((n, 2)), 0, 0, 0.0)

    m = len(fa)
    rows = np.repeat(np.arange(m), 2)
    cols = np.column_stack([fb, fa]).ravel()
    vals = np.tile([1.0, -1.0], m)
    A_pairs = coo_matrix((vals, (rows, cols)), shape=(m, n)).tocsr()
    prior = coo_matrix((np.full(n, config.prior_weight), (np.arange(n), np.arange(n))), shape=(n, n)).tocsr()
    base_w = np.sqrt(support.astype(float))

    t = np.zeros((n, 2))
    w = base_w.copy()
    for _ in range(max(1, config.irls_iterations)):
        A = vstack([A_pairs.multiply(w[:, None]), prior]).tocsr()
        for axis in range(2):
            b = np.concatenate([d[:, axis] * w, np.zeros(n)])
            t[:, axis] = lsqr(A, b, atol=1e-10, btol=1e-10, x0=t[:, axis])[0]
        resid = np.linalg.norm(t[fb] - t[fa] - d, axis=1)
        huber = np.where(resid <= config.huber_m, 1.0, config.huber_m / np.maximum(resid, 1e-12))
        w = base_w * np.sqrt(huber)
    resid = np.linalg.norm(t[fb] - t[fa] - d, axis=1)
    return SyncResult(t, int(m), int((resid <= config.huber_m).sum()), float(np.median(resid)))
