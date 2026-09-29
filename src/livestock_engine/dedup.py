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

from collections import Counter, defaultdict
from dataclasses import dataclass, field

import numpy as np
from scipy.spatial.distance import cdist
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

    def __post_init__(self) -> None:
        if self.eps_m <= 0.0:
            raise ValueError("eps_m must be positive")
        if self.min_samples < 1:
            raise ValueError("min_samples must be >= 1")

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

    @property
    def xy(self) -> np.ndarray:
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

    @property
    def n_unique(self) -> int:
        return len(self.animals)


def deduplicate(points: GroundDetections, config: DedupConfig | None = None) -> DedupResult:
    config = config or DedupConfig()
    n = len(points)
    if n == 0:
        return DedupResult([], np.empty(0, dtype=int), 0, 0)

    xy = points.xy
    labels = DBSCAN(eps=config.eps_m, min_samples=config.min_samples).fit_predict(xy)

    groups: list[list[int]] = []
    clusters: dict[int, list[int]] = defaultdict(list)
    for idx, lab in enumerate(labels):
        if lab == -1:
            if not config.drop_noise:
                groups.append([idx])
        else:
            clusters[int(lab)].append(idx)

    n_splits = 0
    for lab in sorted(clusters):
        members = clusters[lab]
        if config.enforce_frame_exclusivity:
            parts = _split_by_frame(members, xy, points.frame_ids, config.gate_m)
            n_splits += len(parts) - 1
            groups.extend(parts)
        else:
            groups.append(members)

    # Deterministic ordering: west-to-east, then south-to-north.
    groups.sort(key=lambda g: (float(xy[g, 0].mean()), float(xy[g, 1].mean())))

    assignment = np.full(n, -1, dtype=int)
    animals = []
    for k, members in enumerate(groups):
        assignment[members] = k
        animals.append(_resolve(k, members, points))

    return DedupResult(animals, assignment, len(clusters), n_splits)


def _split_by_frame(
    members: list[int], xy: np.ndarray, frame_ids: list[str], gate_m: float
) -> list[list[int]]:
    """Split a cluster so that no sub-cluster holds two detections of one frame.

    Constrained agglomerative clustering: start with one group per
    detection and repeatedly merge the two groups with the closest centroids,
    provided they share no frame (cannot-link) and are within ``gate_m``.
    Closest-first merging makes the result independent of frame order.
    """
    frames_of = [frame_ids[i] for i in members]
    if len(set(frames_of)) == len(frames_of):
        return [members]

    groups: list[list[int] | None] = [[i] for i in members]
    group_frames: list[set[str]] = [{f} for f in frames_of]
    centroids = xy[members].astype(float)
    sizes = np.ones(len(members))

    frame_codes = {f: k for k, f in enumerate(sorted(set(frames_of)))}
    codes = np.array([frame_codes[f] for f in frames_of])
    dist = cdist(centroids, centroids)
    dist[codes[:, None] == codes[None, :]] = np.inf  # also covers the diagonal

    while True:
        flat = int(np.argmin(dist))
        a, b = divmod(flat, dist.shape[1])
        if not dist[a, b] <= gate_m:
            break
        # Merge b into a.
        groups[a].extend(groups[b])
        groups[b] = None
        group_frames[a] |= group_frames[b]
        centroids[a] = (centroids[a] * sizes[a] + centroids[b] * sizes[b]) / (sizes[a] + sizes[b])
        sizes[a] += sizes[b]

        dist[b, :] = np.inf
        dist[:, b] = np.inf
        row = np.linalg.norm(centroids - centroids[a], axis=1)
        for j, g in enumerate(groups):
            if g is None or j == a or group_frames[j] & group_frames[a]:
                row[j] = np.inf
        row[a] = np.inf
        dist[a, :] = row
        dist[:, a] = row

    return [g for g in groups if g is not None]


def _resolve(k: int, members: list[int], points: GroundDetections) -> AnimalRecord:
    """Collapse a cluster into a single centroid record."""
    xy = points.xy[members]
    centroid = xy.mean(axis=0)
    spread = float(np.max(np.linalg.norm(xy - centroid, axis=1)))

    confs = points.confidences[members]
    best = members[int(np.argmax(confs))]
    votes = Counter(points.labels[i] for i in members)

    frames = sorted({points.frame_ids[i] for i in members})
    return AnimalRecord(
        animal_id=f"A{k + 1:05d}",
        easting=float(centroid[0]),
        northing=float(centroid[1]),
        label=points.labels[best],
        confidence=float(points.confidences[best]),
        n_observations=len(members),
        frame_ids=frames,
        detection_ids=[points.detection_ids[i] for i in members],
        spread_m=spread,
        class_conflict=len(votes) > 1,
        label_votes=dict(votes),
    )
