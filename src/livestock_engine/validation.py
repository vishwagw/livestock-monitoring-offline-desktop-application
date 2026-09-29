"""Validate a processed survey against real-world ground truth.

For real flights the true answer usually comes from one of two places:

* **surveyed positions** - a CSV of animal locations (GPS-collared animals,
  RTK points, or animals marked by hand on an orthomosaic): per-animal
  matching gives precision, recall, F1 and position error;
* **manual counts** - a yard/gate count, overall or per class: count accuracy
  only.

Both work on the engine's map report (``report.json``), so any past run can
be scored without re-processing.
"""

from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
from scipy.optimize import linear_sum_assignment
from scipy.spatial.distance import cdist

from .geo import LocalUTM
from .ingest.columns import ColumnMap, parse_float

LAT = ("latitude", "lat", "gps_latitude")
LON = ("longitude", "lon", "lng", "long", "gps_longitude")
LABEL = ("label", "class", "species", "class_name")


class ValidationError(ValueError):
    pass


@dataclass
class ClassScore:
    truth: int
    predicted: int
    count_accuracy: float


@dataclass
class ValidationResult:
    predicted: int
    truth: int
    count_accuracy: float
    count_error: int  # predicted - truth (negative = under-count)
    matched: int | None = None
    precision: float | None = None
    recall: float | None = None
    f1: float | None = None
    mean_position_error_m: float | None = None
    p95_position_error_m: float | None = None
    match_radius_m: float | None = None
    per_class: dict[str, ClassScore] = field(default_factory=dict)
    missed: list[dict] = field(default_factory=list)  # truth points with no match
    extra: list[str] = field(default_factory=list)  # predicted animal ids with no match

    def as_dict(self) -> dict:
        d = asdict(self)
        d["per_class"] = {k: asdict(v) for k, v in self.per_class.items()}
        return d


def _accuracy(pred: int, truth: int) -> float:
    return max(0.0, 1.0 - abs(pred - truth) / truth) if truth else (1.0 if pred == 0 else 0.0)


def gated_cost(cost: np.ndarray, radius_m: float) -> np.ndarray:
    """Cap costs beyond the match radius at one constant.

    Without the cap a far-away false detection still has to pair with some
    true animal, and its large, uneven distances dominate the assignment,
    pulling correct nearby matches apart.
    """
    return np.where(cost <= radius_m, cost, radius_m * 1e3)


def load_truth_points(path: str | Path) -> list[dict]:
    path = Path(path)
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        cols = ColumnMap(reader.fieldnames or [])
        lat_c, lon_c, lab_c = cols.find(LAT), cols.find(LON), cols.find(LABEL)
        if not lat_c or not lon_c:
            raise ValidationError(f"{path.name}: ground-truth CSV needs latitude and longitude columns")
        points = []
        for line, row in enumerate(reader, start=2):
            lat, lon = parse_float(row.get(lat_c[0])), parse_float(row.get(lon_c[0]))
            if lat is None or lon is None:
                raise ValidationError(f"{path.name}:{line}: invalid coordinates")
            label = (row.get(lab_c[0]) or "").strip() if lab_c else ""
            points.append({"latitude": lat, "longitude": lon, "label": label or None, "row": line})
    if not points:
        raise ValidationError(f"{path.name}: no ground-truth points")
    return points


def parse_counts(text: str) -> dict[str, int]:
    """``"150"`` -> total; ``"cattle=120,sheep=30"`` -> per class."""
    text = text.strip()
    if text.isdigit():
        return {"*": int(text)}
    counts = {}
    for part in text.split(","):
        if "=" not in part:
            raise ValidationError(f"invalid count {part!r}; use 150 or cattle=120,sheep=30")
        k, v = part.split("=", 1)
        if not v.strip().isdigit():
            raise ValidationError(f"invalid count for {k.strip()!r}: {v!r}")
        counts[k.strip().lower()] = int(v)
    return counts


def _predicted_by_class(animals: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for a in animals:
        out[a["label"].lower()] = out.get(a["label"].lower(), 0) + 1
    return out


def validate_counts(report: dict, counts: dict[str, int]) -> ValidationResult:
    animals = report["animals"]
    if "*" in counts:
        truth = counts["*"]
        return ValidationResult(len(animals), truth, _accuracy(len(animals), truth), len(animals) - truth)
    pred = _predicted_by_class(animals)
    per_class = {k: ClassScore(v, pred.get(k, 0), _accuracy(pred.get(k, 0), v)) for k, v in counts.items()}
    truth = sum(counts.values())
    predicted = sum(pred.get(k, 0) for k in counts)
    return ValidationResult(predicted, truth, _accuracy(predicted, truth), predicted - truth, per_class=per_class)


def validate_points(report: dict, truth: list[dict], radius_m: float | None = None) -> ValidationResult:
    animals = report["animals"]
    if radius_m is None:
        density = (report.get("summary") or {}).get("density") or {}
        radius_m = float(density.get("eps_effective_m") or 2.0)
    ref = truth[0]
    utm = LocalUTM(ref["latitude"], ref["longitude"])
    t_xy = np.column_stack(utm.to_utm(np.array([t["longitude"] for t in truth]), np.array([t["latitude"] for t in truth])))
    p_xy = np.column_stack(
        utm.to_utm(np.array([a["lon"] for a in animals]), np.array([a["lat"] for a in animals]))
    ).reshape(-1, 2)

    matched_pairs: list[tuple[int, int, float]] = []
    if len(p_xy) and len(t_xy):
        cost = cdist(p_xy, t_xy)
        rows, cols = linear_sum_assignment(gated_cost(cost, radius_m))
        matched_pairs = [(r, c, cost[r, c]) for r, c in zip(rows, cols) if cost[r, c] <= radius_m]
    matched = len(matched_pairs)
    errors = np.array([d for _, _, d in matched_pairs]) if matched_pairs else np.empty(0)
    precision = matched / len(animals) if animals else 0.0
    recall = matched / len(truth)
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    hit_truth = {c for _, c, _ in matched_pairs}
    hit_pred = {r for r, _, _ in matched_pairs}

    per_class: dict[str, ClassScore] = {}
    if any(t["label"] for t in truth):
        truth_counts: dict[str, int] = {}
        for t in truth:
            key = (t["label"] or "unlabelled").lower()
            truth_counts[key] = truth_counts.get(key, 0) + 1
        pred = _predicted_by_class(animals)
        per_class = {k: ClassScore(v, pred.get(k, 0), _accuracy(pred.get(k, 0), v)) for k, v in truth_counts.items()}

    return ValidationResult(
        predicted=len(animals),
        truth=len(truth),
        count_accuracy=_accuracy(len(animals), len(truth)),
        count_error=len(animals) - len(truth),
        matched=matched,
        precision=precision,
        recall=recall,
        f1=f1,
        mean_position_error_m=float(errors.mean()) if len(errors) else None,
        p95_position_error_m=float(np.percentile(errors, 95)) if len(errors) else None,
        match_radius_m=radius_m,
        per_class=per_class,
        missed=[{"row": truth[i]["row"], "latitude": truth[i]["latitude"], "longitude": truth[i]["longitude"]}
                for i in range(len(truth)) if i not in hit_truth],
        extra=[animals[i]["id"] for i in range(len(animals)) if i not in hit_pred],
    )


def load_report(path: str | Path) -> dict:
    with Path(path).open("r", encoding="utf-8") as fh:
        report = json.load(fh)
    if not isinstance(report, dict) or "animals" not in report:
        raise ValidationError(f"{path}: not an engine report (no 'animals')")
    return report
