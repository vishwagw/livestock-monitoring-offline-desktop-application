"""DBSCAN-based de-duplication of ground-projected detections.

Every detection is a point in the local metric plane. Observations of the
same animal from overlapping frames land within a body envelope of each
other, so DBSCAN with ``eps = 2.0 m`` groups them. Each resulting cluster is
then resolved into a single :class:`AnimalRecord`.

DBSCAN is single-linkage, so tightly packed animals can chain into one
cluster. Two detections from the *same* image are by construction different
animals, so the structural resolution step splits any cluster containing
several detections from one frame (frame-exclusivity constraint).
"""

from __future__ import annotations

import heapq
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from functools import cached_property

import numpy as np
from scipy.spatial import cKDTree
from sklearn.cluster import DBSCAN


@dataclass(frozen=True)
class DedupConfig:
    eps_m: float = 2.0
    min_samples: int = 1
    enforce_frame_exclusivity: bool = True
    # Max distance from a sub-cluster centroid when splitting a cluster by
    # frame exclusivity. Defaults to ``eps_m``.
    split_gate_m: float | None = None
    # With min_samples > 1, DBSCAN labels sparse points as noise. Keep them
    # as single-observation animals unless this is set.
    drop_noise: bool = False
    # Two boxes from the *same* image closer than this on the ground are one
    # animal detected twice (a detector NMS failure); the weaker box is folded
    # into the stronger one. 0 disables.
    frame_nms_m: float = 0.25
    # Dense-group handling (see livestock_engine.density): when animals stand
    # closer together than eps, pre-align frames and tighten eps to
    # ``eps_spacing_fraction`` x the typical in-frame spacing.
    adaptive_density: bool = True
    density_quantile: float = 10.0
    eps_spacing_fraction: float = 0.45
    min_eps_m: float = 0.35

    def __post_init__(self) -> None:
        if self.eps_m <= 0.0:
            raise ValueError("eps_m must be positive")
        if self.min_samples < 1:
            raise ValueError("min_samples must be >= 1")
        if self.frame_nms_m < 0:
            raise ValueError("frame_nms_m must be >= 0")
        if not 0 < self.eps_spacing_fraction < 1:
            raise ValueError("eps_spacing_fraction must be in (0, 1)")

    @property
    def gate_m(self) -> float:
        return self.eps_m if self.split_gate_m is None else self.split_gate_m


@dataclass
class GroundDetections:
    """Column-oriented set of detections projected to metric coordinates."""

    easting: np.ndarray
    northing: np.ndarray
    frame_ids: list[str]
    labels: list[str]
    confidences: np.ndarray
    detection_ids: list[str]
    truth_ids: list[str | None] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.easting)

    @cached_property
    def xy(self) -> np.ndarray:
        # Cached: built once per instance (instances are replaced, not mutated).
        return np.column_stack([self.easting, self.northing])


@dataclass
class AnimalRecord:
    animal_id: str
    easting: float
    northing: float
    label: str
    confidence: float
    n_observations: int
    frame_ids: list[str]
    detection_ids: list[str]
    spread_m: float
    class_conflict: bool
    label_votes: dict[str, int]
    latitude: float | None = None
    longitude: float | None = None


@dataclass
class DedupResult:
    animals: list[AnimalRecord]
    # Index into ``animals`` for every input detection, -1 if dropped as noise.
    assignment: np.ndarray
    n_dbscan_clusters: int
    n_frame_splits: int
    # Same-frame duplicate boxes folded into a stronger box (frame_nms_m).
    n_suppressed: int = 0
    # Number of groups; equals len(animals) unless records were not resolved.
    n_groups: int = -1

    @property
    def n_unique(self) -> int:
        return self.n_groups if self.n_groups >= 0 else len(self.animals)


def deduplicate(
    points: GroundDetections, config: DedupConfig | None = None, resolve: bool = True
) -> DedupResult:
    """Cluster detections into animals.

    ``resolve=False`` skips building :class:`AnimalRecord` objects; alignment
    passes only need the assignment.
    """
    config = config or DedupConfig()
    n = len(points)
    if n == 0:
        return DedupResult([], np.empty(0, dtype=int), 0, 0, n_groups=0)

    xy = points.xy
    keeper = suppress_frame_duplicates(xy, points.frame_ids, points.confidences, config.frame_nms_m)
    active = np.flatnonzero(keeper == np.arange(n))
    labels = np.full(n, -2, dtype=int)
    labels[active] = DBSCAN(eps=config.eps_m, min_samples=config.min_samples).fit_predict(xy[active])

    groups: list[list[int]] = []
    clusters: dict[int, list[int]] = defaultdict(list)
    for idx in active:
        lab = labels[idx]
        if lab == -1:
            if not config.drop_noise:
                groups.append([int(idx)])
        else:
            clusters[int(lab)].append(int(idx))

    n_splits = 0
    for lab in sorted(clusters):
        members = clusters[lab]
        if config.enforce_frame_exclusivity:
            parts = _split_by_frame(members, xy, points.frame_ids, config.gate_m)
            n_splits += len(parts) - 1
            groups.extend(parts)
        else:
            groups.append(members)

    # Suppressed duplicate boxes join the group of the box that absorbed them.
    suppressed = np.flatnonzero(keeper != np.arange(n))
    if len(suppressed):
        group_of = {i: k for k, g in enumerate(groups) for i in g}
        for i in suppressed:
            k = group_of.get(int(keeper[i]))
            if k is not None:
                groups[k].append(int(i))

    # Deterministic ordering: west-to-east, then south-to-north.
    groups.sort(key=lambda g: (float(xy[g, 0].mean()), float(xy[g, 1].mean())))

    assignment = np.full(n, -1, dtype=int)
    for k, members in enumerate(groups):
        assignment[members] = k
    animals = _resolve_all(assignment, len(groups), points) if resolve else []
    return DedupResult(animals, assignment, len(clusters), n_splits, int(len(suppressed)), n_groups=len(groups))


def suppress_frame_duplicates(
    xy: np.ndarray, frame_ids: list[str], confidences: np.ndarray, radius_m: float
) -> np.ndarray:
    """Ground-space non-maximum suppression within each frame.

    Returns ``keeper`` where ``keeper[i] == i`` for kept detections and
    otherwise the index of the stronger same-frame box that absorbs ``i``.
    Boxes are linked when closer than ``radius_m``; each linked group keeps its
    highest-confidence box.
    """
    n = len(xy)
    keeper = np.arange(n)
    if radius_m <= 0 or n < 2:
        return keeper
    pairs = cKDTree(xy).query_pairs(radius_m, output_type="ndarray")
    if len(pairs) == 0:
        return keeper
    codes = np.unique(np.asarray(frame_ids), return_inverse=True)[1]
    pairs = pairs[codes[pairs[:, 0]] == codes[pairs[:, 1]]]
    if len(pairs) == 0:
        return keeper
    parent = np.arange(n)

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for a, b in pairs:
        ra, rb = find(int(a)), find(int(b))
        if ra != rb:
            parent[rb] = ra
    roots = np.array([find(i) for i in range(n)])
    best: dict[int, int] = {}
    for i in np.flatnonzero(np.bincount(roots, minlength=n)[roots] > 1):
        r = int(roots[i])
        if r not in best or confidences[i] > confidences[best[r]]:
            best[r] = int(i)
    for i in range(n):
        r = int(roots[i])
        if r in best:
            keeper[i] = best[r]
    return keeper


def _split_by_frame(
    members: list[int], xy: np.ndarray, frame_ids: list[str], gate_m: float
) -> list[list[int]]:
    """Split a cluster so that no sub-cluster holds two detections of one frame.

    Constrained agglomerative clustering: start with one group per detection
    and repeatedly merge the two groups with the closest centroids, provided
    they share no frame (cannot-link) and are within ``gate_m``. Closest-first
    merging makes the result independent of frame order.

    Candidate merges come from a KD-tree (pairs within the gate) and are kept
    in a priority queue, so the cost grows with the number of nearby pairs
    rather than the square of the cluster size. A dense sheep yard or feedlot
    that DBSCAN chains into one cluster of tens of thousands of detections is
    handled in near-linear time and memory.
    """
    frames_of = [frame_ids[i] for i in members]
    if len(set(frames_of)) == len(frames_of):
        return [members]

    m = len(members)
    pts = xy[members].astype(float)
    centroids = pts.copy()
    sizes = np.ones(m)
    group_frames: list[set[str]] = [{f} for f in frames_of]
    groups: list[list[int] | None] = [[i] for i in members]
    version = [0] * m
    neighbours: list[set[int]] = [set() for _ in range(m)]

    heap: list[tuple[float, int, int, int, int]] = []
    pairs = cKDTree(pts).query_pairs(gate_m, output_type="ndarray")
    for a, b in pairs:
        a, b = int(a), int(b)
        if frames_of[a] == frames_of[b]:
            continue
        neighbours[a].add(b)
        neighbours[b].add(a)
        d = float(np.hypot(*(pts[a] - pts[b])))
        heap.append((d, a, b, 0, 0))
    heapq.heapify(heap)

    while heap:
        d, a, b, va, vb = heapq.heappop(heap)
        if groups[a] is None or groups[b] is None or version[a] != va or version[b] != vb:
            continue  # stale entry: one side merged or moved since it was queued
        if group_frames[a] & group_frames[b]:
            continue  # frame sets only grow, so this pair can never merge
        # Merge b into a.
        groups[a].extend(groups[b])
        groups[b] = None
        group_frames[a] |= group_frames[b]
        centroids[a] = (centroids[a] * sizes[a] + centroids[b] * sizes[b]) / (sizes[a] + sizes[b])
        sizes[a] += sizes[b]
        version[a] += 1
        neighbours[a] |= neighbours[b]
        neighbours[a].discard(a)
        neighbours[a].discard(b)
        neighbours[b] = set()
        dead = []
        for k in neighbours[a]:
            if groups[k] is None:
                dead.append(k)
                continue
            neighbours[k].discard(b)
            neighbours[k].add(a)
            if group_frames[a] & group_frames[k]:
                continue
            dk = float(np.hypot(*(centroids[a] - centroids[k])))
            if dk <= gate_m:
                heapq.heappush(heap, (dk, a, k, version[a], version[k]))
        neighbours[a].difference_update(dead)

    return [g for g in groups if g is not None]


def _resolve_all(assignment: np.ndarray, k: int, points: GroundDetections) -> list[AnimalRecord]:
    """Collapse every cluster into a centroid record (vectorised over clusters)."""
    if k == 0:
        return []
    valid = np.flatnonzero(assignment >= 0)
    g = assignment[valid]
    xy = points.xy[valid]
    counts = np.bincount(g, minlength=k)
    centroids = np.column_stack(
        [np.bincount(g, weights=xy[:, 0], minlength=k), np.bincount(g, weights=xy[:, 1], minlength=k)]
    ) / counts[:, None]
    dist = np.linalg.norm(xy - centroids[g], axis=1)
    spread = np.zeros(k)
    np.maximum.at(spread, g, dist)
    # Highest-confidence member per cluster (ties: first detection).
    conf = points.confidences[valid]
    order = np.lexsort((np.arange(len(g)), -conf, g))
    first = order[np.r_[True, g[order][1:] != g[order][:-1]]]
    best = valid[first]
    # Members grouped per cluster in input order.
    by_cluster = np.split(valid[np.argsort(g, kind="stable")], np.cumsum(counts)[:-1])

    labels, frame_ids, det_ids = points.labels, points.frame_ids, points.detection_ids
    animals = []
    for c in range(k):
        members = by_cluster[c]
        votes = Counter(labels[i] for i in members)
        animals.append(
            AnimalRecord(
                animal_id=f"A{c + 1:05d}",
                easting=float(centroids[c, 0]),
                northing=float(centroids[c, 1]),
                label=labels[best[c]],
                confidence=float(points.confidences[best[c]]),
                n_observations=int(counts[c]),
                frame_ids=sorted({frame_ids[i] for i in members}),
                detection_ids=[det_ids[i] for i in members],
                spread_m=float(spread[c]),
                class_conflict=len(votes) > 1,
                label_votes=dict(votes),
            )
        )
    return animals
