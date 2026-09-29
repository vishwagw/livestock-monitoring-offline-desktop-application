"""Accuracy evaluation of de-duplication results against ground truth."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass

import numpy as np
from scipy.optimize import linear_sum_assignment
from scipy.spatial.distance import cdist

from .models import Dataset
from .pipeline import PipelineResult


@dataclass
class Evaluation:
    true_animals: int
    observed_animals: int
    raw_detections: int
    predicted_animals: int
    matched: int
    precision: float
    recall: float
    f1: float
    count_accuracy: float
    mean_position_error_m: float
    over_merged_clusters: int
    split_animals: int

    def as_dict(self) -> dict:
        return asdict(self)


def evaluate(result: PipelineResult, dataset: Dataset, match_radius_m: float = 2.0) -> Evaluation:
    """Compare the unique-animal index with the simulated ground truth.

    ``count_accuracy`` is ``1 - |predicted - observed| / observed`` where
    *observed* animals are those detected in at least one frame: an animal
    never seen by the detector cannot be recovered by de-duplication.
    Precision/recall use a one-to-one (Hungarian) spatial match within
    ``match_radius_m``.
    """
    if not dataset.ground_truth:
        raise ValueError("dataset has no ground truth to evaluate against")

    gt = dataset.ground_truth
    gt_ids = [g["truth_id"] for g in gt]
    gt_e, gt_n = result.utm.to_utm(
        np.array([g["longitude"] for g in gt]), np.array([g["latitude"] for g in gt])
    )
    gt_xy = np.column_stack([gt_e, gt_n])

    observed_ids = {t for t in result.ground.truth_ids if t is not None and t != "FP"}
    observed_mask = np.array([t in observed_ids for t in gt_ids])
    observed_xy = gt_xy[observed_mask]

    pred_xy = np.array([[a.easting, a.northing] for a in result.animals]).reshape(-1, 2)
    matched, errors = 0, []
    if len(pred_xy) and len(observed_xy):
        cost = cdist(pred_xy, observed_xy)
        rows, cols = linear_sum_assignment(cost)
        ok = cost[rows, cols] <= match_radius_m
        matched = int(ok.sum())
        errors = cost[rows, cols][ok].tolist()

    n_pred, n_obs = len(pred_xy), len(observed_xy)
    precision = matched / n_pred if n_pred else 0.0
    recall = matched / n_obs if n_obs else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    count_accuracy = 1.0 - abs(n_pred - n_obs) / n_obs if n_obs else 0.0

    # Identity diagnostics using the truth labels carried by each detection.
    over_merged = 0
    clusters_per_truth: dict[str, set[int]] = defaultdict(set)
    members: dict[int, list[str]] = defaultdict(list)
    for i, k in enumerate(result.dedup.assignment):
        t = result.ground.truth_ids[i]
        if k < 0 or t is None:
            continue
        members[int(k)].append(t)
        if t != "FP":
            clusters_per_truth[t].add(int(k))
    for truths in members.values():
        real = Counter(t for t in truths if t != "FP")
        if len(real) > 1:
            over_merged += 1
    split = sum(1 for ks in clusters_per_truth.values() if len(ks) > 1)

    return Evaluation(
        true_animals=len(gt),
        observed_animals=n_obs,
        raw_detections=result.projection.n_detections,
        predicted_animals=n_pred,
        matched=matched,
        precision=precision,
        recall=recall,
        f1=f1,
        count_accuracy=max(0.0, count_accuracy),
        mean_position_error_m=float(np.mean(errors)) if errors else float("nan"),
        over_merged_clusters=over_merged,
        split_animals=split,
    )
