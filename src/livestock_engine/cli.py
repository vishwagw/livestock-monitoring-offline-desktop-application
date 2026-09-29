"""Command-line interface.

    livestock-engine dedup INPUT -o animals.csv [--geojson ...] [--evaluate]
    livestock-engine simulate -o flight.json [--profile standard] [--seed 0]
    livestock-engine benchmark [--runs 20] [--threshold 0.99]
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from dataclasses import replace

import numpy as np

from . import __version__
from .dedup import DedupConfig
from .io import (
    DatasetError,
    load_dataset,
    save_dataset,
    write_animals_csv,
    write_assignments_csv,
    write_frame_corrections_csv,
    write_geojson,
)
from .metrics import evaluate
from .pipeline import run_pipeline
from .registration import RegistrationConfig
from .simulate import PROFILES, profile_config, simulate_survey


def _add_dedup_options(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("clustering")
    g.add_argument("--eps", type=float, default=2.0, help="DBSCAN radius in metres (default: 2.0)")
    g.add_argument("--min-samples", type=int, default=1, help="DBSCAN min_samples (default: 1)")
    g.add_argument(
        "--no-frame-exclusivity",
        action="store_true",
        help="allow two detections from the same image to merge into one animal",
    )
    g.add_argument(
        "--drop-noise",
        action="store_true",
        help="with --min-samples > 1, discard DBSCAN noise points instead of keeping them",
    )
    g.add_argument(
        "--max-range",
        type=float,
        default=1000.0,
        help="reject rays hitting the ground further than this from the camera (m)",
    )
    g = p.add_argument_group("frame registration")
    g.add_argument(
        "--no-registration",
        action="store_true",
        help="cluster raw projections without aligning frames to each other",
    )
    g.add_argument(
        "--registration-iterations",
        type=int,
        default=3,
        help="frame alignment passes before the final clustering (default: 3)",
    )


def _registration_config(args) -> RegistrationConfig | None:
    if args.no_registration or args.registration_iterations <= 0:
        return None
    return RegistrationConfig(iterations=args.registration_iterations)


def _dedup_config(args) -> DedupConfig:
    return DedupConfig(
        eps_m=args.eps,
        min_samples=args.min_samples,
        enforce_frame_exclusivity=not args.no_frame_exclusivity,
        drop_noise=args.drop_noise,
    )


def cmd_dedup(args) -> int:
    dataset = load_dataset(args.input)
    started = time.perf_counter()
    result = run_pipeline(
        dataset,
        _dedup_config(args),
        max_range_m=args.max_range,
        registration=_registration_config(args),
    )
    elapsed = time.perf_counter() - started

    write_animals_csv(result, args.output)
    if args.geojson:
        write_geojson(result, args.geojson)
    if args.assignments:
        write_assignments_csv(result, args.assignments)
    if args.frame_corrections:
        write_frame_corrections_csv(result, args.frame_corrections)

    summary = {"input": str(args.input), "frames": len(dataset.frames), **result.summary()}
    summary["runtime_s"] = round(elapsed, 3)
    if args.evaluate:
        if not dataset.ground_truth:
            print("error: --evaluate requires a dataset with ground_truth", file=sys.stderr)
            return 2
        summary["evaluation"] = _rounded(evaluate(result, dataset, args.eps).as_dict())

    if args.summary_json:
        with open(args.summary_json, "w", encoding="utf-8") as fh:
            json.dump(summary, fh, indent=2)
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        _print_summary(summary, args.output)
    return 0


def cmd_simulate(args) -> int:
    overrides = {
        k: v
        for k, v in {
            "seed": args.seed,
            "n_animals": args.animals,
            "altitude_agl_m": args.altitude,
            "gimbal_pitch_deg": args.pitch,
            "front_overlap": args.front_overlap,
            "side_overlap": args.side_overlap,
            "origin_latitude": args.lat,
            "origin_longitude": args.lon,
        }.items()
        if v is not None
    }
    cfg = profile_config(args.profile, **overrides)
    dataset = simulate_survey(cfg)
    save_dataset(dataset, args.output)
    observed = {d.truth_id for f in dataset.frames for d in f.detections if d.truth_id != "FP"}
    print(
        f"wrote {args.output}: {len(dataset.frames)} frames, {dataset.n_detections} detections "
        f"of {len(dataset.ground_truth)} animals ({len(observed)} observed), profile={args.profile}"
    )
    return 0


def cmd_benchmark(args) -> int:
    config = _dedup_config(args)
    registration = _registration_config(args)
    profiles = args.profile or ["standard"]
    rows = []
    for profile in profiles:
        for run in range(args.runs):
            cfg = profile_config(profile, seed=args.seed + run)
            if args.animals:
                cfg = replace(cfg, n_animals=args.animals)
            dataset = simulate_survey(cfg)
            result = run_pipeline(dataset, config, max_range_m=args.max_range, registration=registration)
            ev = evaluate(result, dataset, config.eps_m)
            rows.append((profile, cfg.seed, ev))

    header = (
        f"{'profile':<9} {'seed':>4} {'raw':>6} {'observed':>8} {'unique':>6} "
        f"{'count_acc':>9} {'precision':>9} {'recall':>7} {'f1':>7} {'err_m':>6} {'merged':>6} {'split':>5}"
    )
    print(header)
    print("-" * len(header))
    for profile, seed, ev in rows:
        print(
            f"{profile:<9} {seed:>4} {ev.raw_detections:>6} {ev.observed_animals:>8} "
            f"{ev.predicted_animals:>6} {ev.count_accuracy:>9.2%} {ev.precision:>9.2%} "
            f"{ev.recall:>7.2%} {ev.f1:>7.2%} {ev.mean_position_error_m:>6.2f} "
            f"{ev.over_merged_clusters:>6} {ev.split_animals:>5}"
        )
    print("-" * len(header))

    ok = True
    for profile in profiles:
        evs = [ev for p, _, ev in rows if p == profile]
        count_acc = float(np.mean([e.count_accuracy for e in evs]))
        f1 = float(np.mean([e.f1 for e in evs]))
        raw = sum(e.raw_detections for e in evs)
        unique = sum(e.predicted_animals for e in evs)
        passed = count_acc >= args.threshold and f1 >= args.threshold
        ok &= passed
        print(
            f"{profile:<9} mean count accuracy {count_acc:.2%} | mean F1 {f1:.2%} | "
            f"{raw} raw detections -> {unique} animals "
            f"({1 - unique / raw:.1%} duplicates removed) | "
            f"{'PASS' if passed else 'FAIL'} (threshold {args.threshold:.0%})"
        )
    return 0 if ok else 1


def _rounded(d: dict) -> dict:
    return {k: (round(v, 4) if isinstance(v, float) and math.isfinite(v) else v) for k, v in d.items()}


def _print_summary(summary: dict, output) -> None:
    print(f"Input            : {summary['input']} ({summary['frames']} frames)")
    print(f"Projection       : UTM EPSG:{summary['utm_epsg']}")
    print(
        f"Detections       : {summary['input_detections']} in, "
        f"{summary['projected_detections']} projected, {summary['rejected_detections']} rejected"
    )
    print(
        f"Unique animals   : {summary['unique_animals']} "
        f"({summary['duplicates_removed']} duplicates removed)"
    )
    print(f"Labels           : {summary['label_counts']}")
    print(f"Frame splits     : {summary['frame_exclusivity_splits']}")
    reg = summary["registration"]
    if reg["enabled"]:
        print(
            f"Registration     : {reg['frames']} frames aligned, mean shift {reg['mean_shift_m']} m "
            f"(max {reg['max_shift_m']} m), mean |rotation| {reg['mean_abs_rotation_deg']} deg"
        )
    else:
        print("Registration     : disabled")
    print(f"Runtime          : {summary['runtime_s']} s")
    if "evaluation" in summary:
        ev = summary["evaluation"]
        print(
            f"Evaluation       : count accuracy {ev['count_accuracy']:.2%}, precision "
            f"{ev['precision']:.2%}, recall {ev['recall']:.2%}, F1 {ev['f1']:.2%} "
            f"(observed {ev['observed_animals']} / true {ev['true_animals']})"
        )
    print(f"Written          : {output}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="livestock-engine",
        description="De-duplicate livestock detections from overlapping drone imagery.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("dedup", help="project detections to the ground and remove duplicates")
    p.add_argument("input", help="flight dataset (.json or .csv)")
    p.add_argument("-o", "--output", required=True, help="unique-animal CSV to write")
    p.add_argument("--geojson", help="also write unique animals as GeoJSON points")
    p.add_argument("--assignments", help="also write a per-detection audit CSV")
    p.add_argument("--frame-corrections", help="also write the per-frame registration CSV")
    p.add_argument("--summary-json", help="write the run summary to this JSON file")
    p.add_argument("--json", action="store_true", help="print the summary as JSON")
    p.add_argument("--evaluate", action="store_true", help="score against the dataset's ground_truth")
    _add_dedup_options(p)
    p.set_defaults(func=cmd_dedup)

    p = sub.add_parser("simulate", help="generate a synthetic overlapping survey with ground truth")
    p.add_argument("-o", "--output", required=True, help="dataset JSON to write")
    p.add_argument("--profile", default="standard", choices=sorted(PROFILES))
    p.add_argument("--seed", type=int)
    p.add_argument("--animals", type=int)
    p.add_argument("--altitude", type=float, help="flight altitude AGL (m)")
    p.add_argument("--pitch", type=float, help="gimbal pitch (deg, -90 = nadir)")
    p.add_argument("--front-overlap", type=float)
    p.add_argument("--side-overlap", type=float)
    p.add_argument("--lat", type=float, help="field origin latitude")
    p.add_argument("--lon", type=float, help="field origin longitude")
    p.set_defaults(func=cmd_simulate)

    p = sub.add_parser("benchmark", help="run simulated flights and verify de-duplication accuracy")
    p.add_argument("--runs", type=int, default=10, help="simulated flights per profile (default: 10)")
    p.add_argument("--seed", type=int, default=0, help="first seed (default: 0)")
    p.add_argument("--animals", type=int, help="override herd size")
    p.add_argument(
        "--profile",
        action="append",
        choices=sorted(PROFILES),
        help="simulation profile, repeatable (default: standard)",
    )
    p.add_argument("--threshold", type=float, default=0.99, help="required mean accuracy (default: 0.99)")
    _add_dedup_options(p)
    p.set_defaults(func=cmd_benchmark)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (DatasetError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
