# Performance and accuracy validation (Phase 4)

All figures below come from the engine's own test harness, so anyone can reproduce them with the
commands shown. They were measured on a Linux x86-64 container (Python 3.11, single process).

> **Scope.** These results use *simulated* surveys: realistic lawnmower flights, DJI camera
> geometry, per-frame GPS/heading/altitude/gimbal noise, detector jitter, missed detections and
> label errors. No real flight logs with ground truth were available during development. Validate on
> real flights with `livestock-engine validate` before relying on counts
> (see [INTEGRATION_GUIDE.md §8](INTEGRATION_GUIDE.md#8-validating-against-real-flights)).

## 1. Accuracy by scenario

`livestock-engine benchmark --runs 10 --profile standard --profile oblique --profile dense --profile harsh --profile sheep_pen`

Each profile is 10 complete surveys (seeds 0–9). Count accuracy is `1 − |counted − true| / true`,
scored against animals detected at least once. F1 uses a one-to-one match within the cluster radius
actually used.

| Profile | Scenario | Raw detections → animals | Count accuracy | F1 |
| --- | --- | --- | --- | --- |
| `standard` | cattle, nadir, 60 m, typical telemetry noise | 11,842 → 1,500 | **100.00%** | 100.00% |
| `oblique` | gimbal −75°, diagonal flight lines | 13,622 → 1,500 | **100.00%** | 100.00% |
| `dense` | 250 cattle in tight herds, ≥ 2.5 m apart | 19,649 → 2,500 | **100.00%** | 100.00% |
| `harsh` | ~2× telemetry noise, 92% detector recall, 5% label errors | 11,283 → 1,502 | **99.87%** | 99.93% |
| `sheep_pen` | 480 sheep packed in yards 0.9 m apart, 40 m altitude | 34,834 → 4,805 | **99.90%** | 99.95% |

## 2. Tight animal groups (Sprint 4.1)

### The problem

The configured cluster radius, ε = 2.0 m, is a cattle body envelope. In a sheep yard, animals stand
about 1 m apart, so:
- DBSCAN chains a whole yard into one cluster;
- the frame-alignment step then pulls frames toward the *wrong* animals, because raw telemetry error
  (~0.5 m) is as large as the gap between sheep.

With the Phase 1–3 pipeline, yards were counted at 72–96% accuracy with hundreds of mixed-up clusters.

### The fix: dense-group mode (`livestock_engine/density.py`, `sync.py`)

1. **Measure packing where it's reliable: inside single images.**
   - Detections in one image share one camera pose, so their spacing is accurate even when GPS is not.
   - The 10th-percentile nearest-neighbour distance within images is the "spacing".
2. **If the spacing is below ε, switch to dense mode.** Open-pasture surveys never trigger it, so cattle
   results are unchanged.
3. **Pre-align frames without clustering.**
   - For each overlapping image pair, vote over the displacement vectors between their detections: true
     matches all share the pair's rigid offset, and neighbouring animals scatter.
   - Then solve one robust least-squares system over all pairs (a pose graph) for each image's correction.
4. **Tighten ε** to 0.45 × spacing (never below 0.35 m), then run the usual alignment and clustering.
5. **Suppress same-image double boxes** closer than 0.25 m (a detector NMS failure). Otherwise the
   same-image rule would count them as two animals.

### Results (5 surveys per row)

"Under-counted" / "over-counted" are the net shortfall / excess summed over the surveys. "Mixed
clusters" are clusters holding sightings of two or more animals.

| Yard scenario | Phase 1–3 accuracy | **Phase 4 accuracy** | Under-counted | Over-counted | Mixed clusters (before → after) |
| --- | --- | --- | --- | --- | --- |
| sheep 0.9 m apart | 92.1% | **99.9%** | 0 | 3 | 1,319 → 0 |
| sheep 0.75 m apart | 95.6% | **99.8%** | 0 | 5 | 1,111 → 1 |
| sheep 0.6 m apart (touching) | 95.3% | **99.7%** | 0 | 10 | 1,220 → 3 |
| 0.9 m + harsh telemetry | 75.6% | **99.5%** | 0 | 11 | 2,613 → 8 |
| 0.75 m + harsh telemetry | 72.5% | **99.4%** | 0 | 16 | 3,140 → 11 |

No yard scenario under-counts. The residual errors are a few extra animals, where one animal's
sightings were split in two; these are visible on the map as a pair of green points closer than the
spacing.

## 3. Large surveys (> 50,000 detections)

`livestock-engine stress --animals 7000 14000 28000`: the full production path (DJI SRT +
bounding-box CSV → ingestion → georeferencing → clustering → alignment → map report), with the
station area scaled to keep stocking density constant.

| Animals | Detections | Images | Total time | Throughput | Peak memory | Report size | Accuracy |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 7,000 | 55,357 | 4,838 | 4.7 s | 11.8k det/s | 275 MB | 14.6 MB | 100% |
| 14,000 | 110,322 | 9,462 | 10.3 s | 10.8k det/s | 466 MB | 29.1 MB | 100% |
| 28,000 | 221,125 | 18,603 | 21.0 s | 10.5k det/s | 798 MB | 58.3 MB | 100% |

The time per stage for 221k detections was:

| Stage | Time |
| --- | --- |
| Read files | 4.9 s |
| Match detections to telemetry | 2.0 s |
| Georeference | 0.8 s |
| Cluster | 2.4 s |
| Align frames | 6.8 s |
| Build report | 4.0 s |

Scaling is linear. Peak memory includes the simulator generating the survey in the same process.

### Optimisations made in Phase 4

| Change | Effect (55k detections) |
| --- | --- |
| `GroundDetections.xy` was rebuilt on every access, and cluster resolution read it once per animal (quadratic) | cached; resolution vectorised over all clusters |
| Intermediate alignment passes built full animal records they never used | records built once, on the final pass |
| Same-image split used a dense m×m distance matrix and Python loops (a yard chained into one cluster took seconds; a feedlot would exhaust memory) | KD-tree candidate pairs + priority queue: identical results, near-linear; 20,000-point chained cluster in < 20 s worst case (test) |
| **Pipeline total** | **7.2 s → 2.6 s** (dense yards: 2.4–11.9 s → 0.2 s) |

### Desktop map at scale

Leaflet's per-marker objects took 17.7 s to draw 190k duplicate markers. They were replaced with a
canvas point-cloud layer that uses typed arrays, a pre-rendered sprite, viewport and overlap culling,
and grid-indexed hover/click. The animal table is virtualised (only visible rows are in the page).

| Report | First render | Zoom step | JS heap |
| --- | --- | --- | --- |
| 55k detections, before → after | 4.5 s → **0.5 s** | 0.8 s → **0.14 s** | 179 → **61 MB** |
| 221k detections, before → after | 17.7 s → **1.1 s** | 2.2 s → **0.13 s** | 631 → **190 MB** |

## 4. Reproducing

```bash
pip install -r requirements-dev.txt && pip install --no-deps -e .
livestock-engine benchmark --runs 10 --profile standard --profile oblique --profile dense --profile harsh --profile sheep_pen
livestock-engine stress --animals 7000 14000 28000 --json stress.json
pytest tests/test_dense.py -v
```

## 5. Known limits

* **Flat ground.** Altitude is height above take-off. On slopes, the ground-position error grows with
  the height difference × tan(view angle). Hilly sites need per-frame AGL from a terrain model.
* **Moving animals.** Adjacent flight strips are captured minutes apart. An animal that walks more than
  the cluster radius between passes can be counted twice.
* **Dense mode needs overlap.** Pre-alignment relies on each animal being seen in several images
  (≥ 60% front and side overlap is recommended). Sparse single-pass flights over yards fall back to
  telemetry accuracy.
* **Detector quality dominates.** Missed animals (never detected in any image) cannot be recovered, and
  persistent false positives are counted. Tune the detector's confidence threshold on real imagery.
