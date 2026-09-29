import numpy as np
import pytest

from livestock_engine.dedup import DedupConfig, GroundDetections, deduplicate
from livestock_engine.registration import fit_similarity


def ground(points, frames, labels=None, conf=None):
    points = np.asarray(points, dtype=float)
    n = len(points)
    return GroundDetections(
        easting=points[:, 0],
        northing=points[:, 1],
        frame_ids=list(frames),
        labels=list(labels or ["cattle"] * n),
        confidences=np.asarray(conf if conf is not None else [0.9] * n, dtype=float),
        detection_ids=[f"d{i}" for i in range(n)],
    )


def test_observations_within_eps_collapse_to_centroid():
    g = ground([[0, 0], [1.0, 0], [0.5, 0.8]], ["f1", "f2", "f3"])
    res = deduplicate(g)
    assert res.n_unique == 1
    a = res.animals[0]
    assert (a.easting, a.northing) == pytest.approx((0.5, 0.8 / 3))
    assert a.n_observations == 3 and a.frame_ids == ["f1", "f2", "f3"]


def test_points_beyond_eps_stay_separate():
    g = ground([[0, 0], [2.5, 0]], ["f1", "f2"])
    assert deduplicate(g).n_unique == 2
    assert deduplicate(g, DedupConfig(eps_m=3.0)).n_unique == 1


def test_same_frame_detections_never_merge():
    g = ground([[0, 0], [1.2, 0]], ["f1", "f1"])
    assert deduplicate(g).n_unique == 2
    assert deduplicate(g, DedupConfig(enforce_frame_exclusivity=False)).n_unique == 1


def test_chained_herd_is_split_by_frame_exclusivity():
    # Two animals 1.6 m apart, each seen from three frames with small noise.
    rng = np.random.default_rng(0)
    truth = np.array([[0.0, 0.0], [1.6, 0.0]])
    pts, frames = [], []
    for f in range(3):
        for t in truth:
            pts.append(t + rng.normal(0, 0.1, 2))
            frames.append(f"f{f}")
    res = deduplicate(ground(pts, frames))
    assert res.n_unique == 2
    assert res.n_frame_splits == 1
    for a in res.animals:
        assert a.n_observations == 3
        assert len(set(a.frame_ids)) == 3


def test_highest_confidence_label_and_conflict_flag():
    g = ground(
        [[0, 0], [0.3, 0], [0.1, 0.2]],
        ["f1", "f2", "f3"],
        labels=["sheep", "cattle", "cattle"],
        conf=[0.4, 0.95, 0.8],
    )
    a = deduplicate(g).animals[0]
    assert a.label == "cattle"
    assert a.confidence == pytest.approx(0.95)
    assert a.class_conflict
    assert a.label_votes == {"cattle": 2, "sheep": 1}


def test_min_samples_noise_handling():
    g = ground([[0, 0], [0.5, 0], [50, 50]], ["f1", "f2", "f3"])
    assert deduplicate(g, DedupConfig(min_samples=2)).n_unique == 2
    res = deduplicate(g, DedupConfig(min_samples=2, drop_noise=True))
    assert res.n_unique == 1
    assert res.assignment.tolist() == [0, 0, -1]


def test_empty_input():
    res = deduplicate(ground(np.empty((0, 2)), []))
    assert res.n_unique == 0


def test_fit_similarity_recovers_transform():
    rng = np.random.default_rng(3)
    src = rng.uniform(-40, 40, (20, 2))
    theta = np.radians(1.3)
    rot = np.array([[np.cos(theta), -np.sin(theta)], [np.sin(theta), np.cos(theta)]])
    dst = 1.02 * src @ rot.T + np.array([0.7, -0.4])
    scale, r, t = fit_similarity(src, dst)
    assert scale == pytest.approx(1.02)
    np.testing.assert_allclose(r, rot, atol=1e-9)
    np.testing.assert_allclose(t, [0.7, -0.4], atol=1e-9)
