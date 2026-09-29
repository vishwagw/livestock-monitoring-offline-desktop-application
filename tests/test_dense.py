"""Sprint 4.1: dense groups, duplicate boxes and large surveys."""

import time

import numpy as np
import pytest

from livestock_engine.cli import main
from livestock_engine.dedup import DedupConfig, GroundDetections, _split_by_frame, deduplicate, suppress_frame_duplicates
from livestock_engine.density import in_frame_spacing, plan
from livestock_engine.metrics import evaluate
from livestock_engine.pipeline import run_pipeline
from livestock_engine.simulate import profile_config, simulate_survey
from livestock_engine.sync import SyncConfig, synchronise


def _grid_scene(n_frames=6, spacing=0.9, offsets=None, seed=0):
    """A packed 12x12 pen seen by every frame, each frame shifted rigidly."""
    rng = np.random.default_rng(seed)
    gx, gy = np.meshgrid(np.arange(12) * spacing, np.arange(12) * spacing)
    animals = np.column_stack([gx.ravel(), gy.ravel()]) + rng.normal(0, 0.05, (144, 2))
    offsets = offsets if offsets is not None else rng.normal(0, 0.35, (n_frames, 2))
    xy = np.vstack([animals + off + rng.normal(0, 0.02, animals.shape) for off in offsets])
    frames = np.repeat(np.arange(n_frames), len(animals))
    return xy, frames, offsets, animals


def test_sync_recovers_rigid_frame_offsets_in_a_packed_pen():
    xy, frames, offsets, _ = _grid_scene()
    result = synchronise(xy, frames, SyncConfig(radius_m=1.5))
    # Offsets are recovered up to the common gauge (mean offset).
    est = result.translations - result.translations.mean(axis=0)
    truth = offsets - offsets.mean(axis=0)
    assert np.max(np.linalg.norm(est - truth, axis=1)) < 0.05
    assert result.n_links == result.n_pairs == 15  # every frame pair constrained


def test_sync_without_overlap_is_a_no_op():
    xy = np.array([[0.0, 0.0], [100.0, 100.0]])
    result = synchronise(xy, np.array([0, 1]))
    assert np.allclose(result.translations, 0) and result.n_pairs == 0


def test_density_plan_switches_only_for_packed_animals():
    xy, frames, _, _ = _grid_scene()
    ids = [f"f{f}" for f in frames]
    # 10th percentile of a jittered 0.9 m grid sits a little below 0.9 m.
    assert 0.6 < in_frame_spacing(xy, ids, 10) <= 0.9
    cfg, info = plan(xy, ids, DedupConfig())
    assert info.mode == "dense" and cfg.eps_m == pytest.approx(max(0.35, 0.45 * info.spacing_m))
    assert cfg.frame_nms_m <= cfg.eps_m / 2
    sparse = xy * 5  # animals ~4.5 m apart
    cfg, info = plan(sparse, ids, DedupConfig())
    assert info.mode == "normal" and cfg.eps_m == 2.0
    cfg, info = plan(xy, ids, DedupConfig(adaptive_density=False))
    assert info.mode == "normal"


def test_frame_nms_folds_double_boxes_into_one_animal():
    xy = np.array([[0.0, 0.0], [0.1, 0.0], [5.0, 0.0], [0.05, 0.02]])
    frames = ["a", "a", "a", "b"]
    conf = np.array([0.6, 0.9, 0.8, 0.7])
    keeper = suppress_frame_duplicates(xy, frames, conf, 0.25)
    assert keeper.tolist() == [1, 1, 2, 3]  # frame b's box is never suppressed by frame a's
    g = GroundDetections(xy[:, 0], xy[:, 1], frames, ["cow"] * 4, conf, ["d0", "d1", "d2", "d3"])
    res = deduplicate(g)
    assert res.n_unique == 2 and res.n_suppressed == 1
    assert res.assignment[0] == res.assignment[1] == res.assignment[3]
    assert deduplicate(g, DedupConfig(frame_nms_m=0)).n_unique == 3  # same-image rule splits them


def test_sparse_split_scales_to_a_huge_chained_cluster():
    # 2,500 animals 0.9 m apart, 8 frames each: one DBSCAN cluster of 20,000 points.
    rng = np.random.default_rng(1)
    gx, gy = np.meshgrid(np.arange(50) * 0.9, np.arange(50) * 0.9)
    animals = np.column_stack([gx.ravel(), gy.ravel()])
    xy = np.vstack([animals + rng.normal(0, 0.05, animals.shape) for _ in range(8)])
    frames = [f"f{k}" for k in range(8) for _ in range(len(animals))]
    started = time.perf_counter()
    groups = _split_by_frame(list(range(len(xy))), xy, frames, 0.4)
    assert time.perf_counter() - started < 20
    assert len(groups) == pytest.approx(2500, abs=10)
    assert all(len({frames[i] for i in g}) == len(g) for g in groups)


def test_sheep_yards_are_counted_without_under_counting():
    for seed in (0, 2):
        dataset = simulate_survey(profile_config("sheep_pen", seed=seed))
        result = run_pipeline(dataset)
        assert result.density.mode == "dense"
        ev = evaluate(result, dataset, result.density.eps_effective_m)
        assert ev.count_accuracy >= 0.99, ev
        assert ev.predicted_animals >= ev.observed_animals  # never under-count
        assert ev.over_merged_clusters <= 2
    # With the Phase 1 settings the same yards mix hundreds of animals.
    dataset = simulate_survey(profile_config("sheep_pen", seed=0))
    legacy = run_pipeline(dataset, DedupConfig(adaptive_density=False, frame_nms_m=0))
    assert evaluate(legacy, dataset, 0.4).over_merged_clusters > 50


def test_cattle_surveys_stay_in_normal_mode():
    result = run_pipeline(simulate_survey(profile_config("standard", seed=0, n_animals=60)))
    assert result.density.mode == "normal" and result.density.eps_effective_m == 2.0
    assert result.summary()["density"]["mode"] == "normal"


def test_profile_overrides_can_replace_profile_values():
    cfg = profile_config("sheep_pen", n_animals=100, min_separation_m=0.7)
    assert cfg.n_animals == 100 and cfg.min_separation_m == 0.7 and cfg.layout == "pens"


def test_stress_command_smoke(capsys):
    assert main(["stress", "--animals", "300", "600"]) == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 2 and "det/s" in out[0] and "(100.00%)" in out[1]
