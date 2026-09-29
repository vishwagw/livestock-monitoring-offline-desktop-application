# Integration guide: file formats and frame matching

This guide is for developers who feed the engine from their own flight software, AI detector or GIS
pipeline, or who consume its results. It defines every accepted input schema, how detections are
matched to telemetry, and every output format.

Machine-readable JSON Schemas (draft 2020-12) are in [`schemas/`](../schemas). The test suite
checks them against real engine output, so they can't drift from the code.

| Schema | Describes |
| --- | --- |
| [`dataset.schema.json`](../schemas/dataset.schema.json) | Engine dataset: frames, poses and pixel detections (input or intermediate) |
| [`detections.schema.json`](../schemas/detections.schema.json) | Bounding-box logs in JSON form (list, wrapped list or COCO) |
| [`report.schema.json`](../schemas/report.schema.json) | Map report written by `process` (what the desktop map shows) |
| [`progress-event.schema.json`](../schemas/progress-event.schema.json) | The `--progress` JSON-lines stream |
| [`validation-result.schema.json`](../schemas/validation-result.schema.json) | Output of `validate --json` |

Contents:

1. [Conventions](#1-conventions)
2. [Input route A: raw flight logs](#2-input-route-a-raw-flight-logs)
3. [Frame matching rules](#3-frame-matching-rules)
4. [Input route B: engine dataset](#4-input-route-b-engine-dataset)
5. [Outputs](#5-outputs)
6. [Command line, exit codes and progress stream](#6-command-line-exit-codes-and-progress-stream)
7. [Python API](#7-python-api)
8. [Validating against real flights](#8-validating-against-real-flights)
9. [Integration checklist](#9-integration-checklist)

---

## 1. Conventions

| Quantity | Convention |
| --- | --- |
| Positions | WGS84 latitude/longitude in decimal degrees. Internally a single UTM zone is chosen for the whole survey (EPSG 326xx/327xx) and reported as `utm_epsg`. |
| Altitude | Height of the camera **above the ground** in metres (AGL). DJI's `rel_alt` / "height above take-off" is used as AGL, which assumes flat ground at the take-off point. Altitude above sea level (`abs_alt`) is never used. |
| Heading | Camera (gimbal) yaw in degrees **clockwise from true north**; -180 to 180 and 0 to 360 are both accepted. Magnetic headings must be corrected before ingestion. |
| Gimbal pitch | 0 = horizon, -90 = straight down (nadir). Defaults to -90 when not logged. |
| Gimbal roll | Rotation about the optical axis, in degrees. Defaults to 0. |
| Pixels | Origin at the **top-left** of the image, x to the right, y down, in pixels of the full-resolution image. Coordinates that all lie in [0, 1] are treated as normalised and scaled by the configured image size. |
| Confidence | 0 to 1. Values above 1 are read as percentages (e.g. `87` → 0.87). Missing → 1.0. |
| Camera | Pinhole with square pixels: image width, height and one field of view (`horizontal`, `vertical` or `diagonal`, the last being what DJI publishes). Lens distortion isn't modelled, so undistort detections first if the camera isn't rectified. |
| Text files | UTF-8 (a BOM is ignored). CSV uses commas and a header row, and quoting follows RFC 4180. |

### Column-name matching

CSV headers (and JSON keys) are matched to fields through **alias lists**, after normalisation:

1. Trim and lower-case.
2. Split off a trailing unit in `(...)` or `[...]`: `Height Above Takeoff (feet)` becomes `height_above_takeoff` with unit `feet`.
3. Replace spaces, dashes and dots with `_`.

When several aliases are present, the **first alias in the table order wins**. Unknown columns are ignored.

---

## 2. Input route A: raw flight logs

Supply **one or more telemetry files** plus **one or more bounding-box logs**:

```bash
livestock-engine process --telemetry DJI_0001.SRT --detections boxes.csv \
  --image-width 3840 --image-height 2160 --fov 82 --fov-type diagonal \
  --report report.json -o animals.csv
```

### 2.1 Telemetry: DJI `.SRT`

DJI writes one subtitle block per video frame (or per second on older aircraft). The parser reads
`key: value` pairs anywhere in the block, so these firmware styles all work:

```text
# Mavic 3 / Air 2S / Mini (per frame)
1
00:00:00,000 --> 00:00:00,033
<font size="28">FrameCnt: 1, DiffTime: 33ms
2024-03-08 10:12:34.123
[iso: 100] [latitude: -33.868800] [longitude: 151.209300] [rel_alt: 60.000 abs_alt: 120.500] [gb_yaw: -90.0 gb_pitch: -90.0 gb_roll: 0.0] </font>

# Phantom 4 (per second)
HOME(149.0251,-35.2345) 2019.01.01 12:00:00
GPS(149.0253,-35.2346,19) BAROMETER:60.2

# Mavic Pro
F/2.8, SS 206.03, ISO 100, EV 0, GPS (149.0253, -35.2346, 19), D 24.18m, H 6.00m
```

| Field | Keys read (first found wins) |
| --- | --- |
| Time | the block's start time (`HH:MM:SS,mmm -->`) → `time_s` |
| Frame number | `FrameCnt` or `SrtCnt` |
| Latitude / longitude | `latitude`/`lat`, `longitude`/`lon`/`lng`; otherwise `GPS(lon, lat, …)` (swapped automatically if the first value can't be a latitude) |
| Altitude AGL | `rel_alt`, `relative_alt`, `height`, `barometer`, `h`, or `H 6.00m` |
| Heading | `gb_yaw`, `gimbal_yaw`, `gimbal_heading`, `heading`, `yaw` |
| Pitch | `gb_pitch`, `gimbal_pitch`, `camera_pitch` |
| Roll | `gb_roll`, `gimbal_roll`, `camera_roll` |
| Timestamp | first `YYYY-MM-DD HH:MM:SS(.fff)` or `YYYY.MM.DD HH:MM:SS` |

Blocks at `0, 0` (no GPS fix) are skipped with a warning.

### 2.2 Telemetry: CSV

Either one row per photo (image name + pose) or a time series (flight log exports such as AirData).

| Field | Accepted headers (in priority order) | Units |
| --- | --- | --- |
| Image name | `image`, `image_name`, `filename`, `file_name`, `file`, `photo`, `frame_id`, `source_file` | matched by case-insensitive file **stem** |
| Frame number | `frame_number`, `frame`, `framecnt`, `frame_idx`, `frame_index` | integer |
| Time offset | `time_s`, `time_sec`, `seconds`, `offset_s`, `elapsed_s`, `time` | seconds; `(ms)` / `(millisecond)` unit converts |
| Date-time | `datetime`, `timestamp`, `datetime_utc`, `date_time`, `utc_time` | ISO 8601; converted to seconds since the first row when no time offset exists |
| Latitude **(required)** | `latitude`, `lat`, `gps_latitude` | degrees |
| Longitude **(required)** | `longitude`, `lon`, `lng`, `long`, `gps_longitude` | degrees |
| Altitude AGL | `altitude_agl_m`, `altitude_agl`, `agl`, `rel_alt`, `relative_altitude`, `height_above_takeoff`, `height`, `altitude`, `alt` | metres; `(ft)` / `(feet)` converts. A plain `altitude`/`alt` column triggers a warning to confirm it isn't above sea level |
| Heading | `heading_deg`, `gimbal_heading`, `gimbal_yaw`, `gimbal_yaw_degree`, `gb_yaw`, `camera_yaw`, `heading`, `yaw`, `compass_heading`, `flight_yaw_degree` | degrees, true north |
| Pitch | `gimbal_pitch_deg`, `gimbal_pitch`, `gimbal_pitch_degree`, `gb_pitch`, `camera_pitch` | degrees |
| Roll | `gimbal_roll_deg`, `gimbal_roll`, `gimbal_roll_degree`, `gb_roll`, `camera_roll` | degrees |

Example (per-photo metadata, e.g. exported from EXIF/XMP):

```csv
image,latitude,longitude,relative_altitude,gimbal_yaw,gimbal_pitch,gimbal_roll
DJI_0001.JPG,-33.86909300,151.20923300,60.20,1.40,-90.10,0.00
```

**Missing values:**
- **Altitude**: the `--default-altitude` value is used; without it, the frame is skipped.
- **Pitch**: `--default-pitch` (-90) is used.
- **Heading**: derived from the GPS track (course over ground; `--no-derive-heading` disables this). At lawnmower turns, each sample keeps the heading of the strip it belongs to.

### 2.3 Detections: bounding-box CSV

One row per box. Each row needs a **frame key** and a **box**. The frame key is one of:
- an image name;
- a frame number;
- a time.

| Field | Accepted headers (in priority order) |
| --- | --- |
| Image name | `image`, `image_name`, `image_id`, `filename`, `file_name`, `file`, `photo`, `frame_id` |
| Frame number | `frame_number`, `frame`, `framecnt`, `frame_idx`, `frame_index` |
| Time (s) | `time_s`, `time_sec`, `seconds`, `timestamp_s`, `time` |
| Telemetry file | `video`, `source`, `flight`, `log`, `sequence`, `srt` (the telemetry file's stem; required when several telemetry files are keyed by frame number or time) |
| Class label | `label`, `class_name`, `class`, `category`, `species`, `name` |
| Numeric class | `class_id`, `cls`, `category_id` (→ `class_<id>` when there is no label) |
| Confidence | `confidence`, `conf`, `score`, `probability`, `prob` |
| Detection id | `detection_id`, `det_id`, `id` (optional, carried into the audit output) |

**Box layouts** (the first layout whose columns are all present is used):

| Layout | Columns | Meaning |
| --- | --- | --- |
| Corners | `xmin`/`x_min`/`x1`/`left_x`, `ymin`/`y_min`/`y1`/`top_y`, `xmax`/`x_max`/`x2`/`right_x`, `ymax`/`y_max`/`y2`/`bottom_y` | two corners |
| Top-left + size | `left`/`bbox_x`/`box_x`/`x`, `top`/`bbox_y`/`box_y`/`y`, `width`/`bbox_w`/`bbox_width`/`box_w`/`w`, `height`/`bbox_h`/`bbox_height`/`box_h`/`h` | COCO convention |
| Centre + size | `cx`/`x_center`/`center_x`/`xc`, `cy`/`y_center`/`center_y`/`yc`, `width`/`w`/`bbox_w`/`box_w`, `height`/`h`/`bbox_h`/`box_h` | YOLO convention |
| Point | `x`/`px`/`u`, `y`/`py`/`v` | already the ground-contact pixel |

> A bare `x, y` **with** width and height columns is read as the **top-left corner** (COCO), not the centre.
> Use `cx, cy` for centres.

The ground-contact point is the box centre by default. Use `--bbox-anchor bottom` (the desktop app's
"Bottom edge (oblique)" setting) for oblique imagery, where an animal's feet sit at the bottom edge.

```csv
image,frame,xmin,ymin,xmax,ymax,label,confidence
DJI_0002.JPG,2,2847.1,590.2,2948.8,641.0,cattle,0.9657
```

### 2.4 Detections: JSON, COCO and YOLO

* **JSON list**: records with the same keys as the CSV columns. `bbox: [x1, y1, x2, y2]` is also
  accepted. The list may be wrapped as `{"detections": [...]}`, `{"predictions": [...]}` or
  `{"results": [...]}`.
* **COCO results**: `images` (id → `file_name`), `categories` (id → `name`), and `annotations` with
  `image_id`, `category_id`, `bbox: [x, y, width, height]` and `score`.
* **YOLO `.txt`**: one file per image, named after the image, with lines of
  `class cx cy w h [confidence]` (normalised 0–1). Pass `--class-names cattle,sheep,...` to label the
  numeric classes.

---

## 3. Frame matching rules

Every detection must be tied to a telemetry sample, which gives the camera pose for that image. The
rules, applied in order:

1. **Choose the telemetry file.**
   - If the detection has a `video`/`source` value, use the telemetry file with that stem (case-insensitive).
   - Otherwise, with **one** telemetry file, use it.
   - Otherwise, with several, image-name keys are searched in every file.
   - Frame-number and time keys can't be matched across several files, so they're skipped as *ambiguous*.
2. **Image name.** Match the telemetry row whose image name has the same case-insensitive file stem.
   `DJI_0001.JPG`, `dji_0001.jpg` and `DJI_0001` are all the same frame. An image name made only of
   digits that has no telemetry match is retried as a frame number.
3. **Frame number.**
   - If the telemetry has frame numbers (SRT `FrameCnt`, or a CSV `frame` column), match exactly.
   - Otherwise convert to a time: `time = frame / --fps` (default 30), then step 4.
4. **Time.**
   - Interpolate linearly between the two surrounding telemetry samples. Headings interpolate along the shorter arc.
   - The detection must be within `--time-tolerance` (default 0.5 s) of the telemetry's time span.
   - Across gaps longer than 30 s, the nearest sample within the tolerance is used instead of interpolating.
5. **Validation.**
   - A detection is skipped and counted in the warnings if:
     - no sample matches;
     - there is no altitude and no `--default-altitude`;
     - there is no heading and derivation is off;
     - the anchor pixel lies outside the configured image size.
   - Skipped detections are never silently dropped: `report.json → ingest.unmatched` lists them by reason.

Detections that share a frame key become one **frame**. For image names that key is the stem; for
frame numbers and times it is `<telemetry-stem>#<frame>` or `<telemetry-stem>@<t>s`.

---

## 4. Input route B: engine dataset

When your own software already knows each image's pose, write an engine dataset directly
(`schemas/dataset.schema.json`) and skip telemetry matching:

```json
{
  "camera": {"image_width": 4000, "image_height": 3000, "fov_deg": 84, "fov_type": "diagonal"},
  "frames": [
    {
      "frame_id": "DJI_0001", "latitude": -33.8688, "longitude": 151.2093,
      "altitude_agl_m": 60.0, "heading_deg": 12.5, "gimbal_pitch_deg": -90.0,
      "detections": [{"x": 1834.5, "y": 902.0, "label": "cattle", "confidence": 0.94}]
    }
  ]
}
```

A flat CSV is also accepted, with one row per detection and the frame fields repeated. Required columns:
`frame_id, latitude, longitude, altitude_agl_m, heading_deg, image_width, image_height, fov_deg, x, y`.

```bash
livestock-engine process --dataset flight.json --report report.json
```

---

## 5. Outputs

| File (option) | Format |
| --- | --- |
| `report.json` (`--report`) | [`report.schema.json`](../schemas/report.schema.json): summary, animals, every detection with `status` `kept` or `duplicate` (plus raw and aligned positions), frames, flight paths, warnings |
| `animals.csv` (`-o`) | one row per unique animal (below) |
| `animals.geojson` (`--geojson`) | FeatureCollection of points (WGS84) with `animal_id`, `label`, `confidence`, `n_observations`, `spread_m`, `class_conflict`, `frame_ids` |
| `assignments.csv` (`--assignments`) | audit trail: `detection_id, frame_id, label, confidence, raw_easting, raw_northing, easting, northing, animal_id`. Rejected rays show `animal_id = REJECTED_NO_GROUND_INTERSECTION` |
| frame corrections (`dedup --frame-corrections`) | `frame_id, model, n_matches, dx_m, dy_m, rotation_deg, scale` |

`animals.csv` columns:

| Column | Meaning |
| --- | --- |
| `animal_id` | `A00001` …, ordered west to east |
| `label`, `confidence` | class and score of the **highest-confidence** sighting |
| `latitude`, `longitude`, `easting`, `northing`, `utm_epsg` | cluster centroid |
| `n_observations`, `n_frames` | sightings merged, and the distinct images they came from |
| `spread_m` | largest distance of a sighting from the centroid (quality indicator) |
| `class_conflict`, `label_votes` | 1 when sightings disagreed on the class, with the vote counts (JSON) |
| `frame_ids`, `detection_ids` | `;`-separated provenance |

Report `summary.density` says how the run adapted to animal spacing:
- `mode`: `normal` or `dense`;
- `spacing_m`: the 10th-percentile nearest-neighbour distance within images;
- `eps_effective_m`: the cluster radius actually used.

---

## 6. Command line, exit codes and progress stream

`livestock-engine <command> --help` documents every option. The commands are:

| Command | Purpose |
| --- | --- |
| `process` | raw logs or a dataset → report (+ CSV / GeoJSON / audit). Used by the desktop app |
| `ingest` | raw logs → engine dataset JSON (to inspect or edit the matching) |
| `dedup` | dataset → animals CSV (+ outputs, `--evaluate` for simulated data) |
| `validate` | score a report against real ground truth (§8) |
| `simulate` | synthetic surveys, optionally written as raw logs (`--export-raw`) |
| `benchmark` / `stress` | accuracy gate over simulated profiles / timing at scale |
| `self-check` | import every native dependency and run PROJ + DBSCAN once (`--json`) |

**Exit codes:**
- `0`: success.
- `1`: an accuracy gate was not met (`benchmark`, `stress`, `validate --threshold`).
- `2`: invalid input or usage. The message goes to stderr, and with `--progress` it is also an `error` event.

**Progress stream** (`process --progress`): one JSON object per stdout line
([`progress-event.schema.json`](../schemas/progress-event.schema.json)). The stream:
- lists all stages in every event, and reports the overall and per-stage percent;
- keeps `percent` from ever decreasing;
- includes live `counts` (e.g. `raw_detections`, `matched`, `frames`, `detections`, `animals`);
- is throttled to 10 events per second;
- ends with exactly one `done` or `error` event.

```json
{"event": "progress", "stage": "align", "stage_label": "Aligning overlapping frames", "stage_index": 4, "stage_count": 6,
 "stages": ["Reading flight files", "..."], "stage_percent": 33.3, "percent": 60.0,
 "message": "Alignment pass 1 of 3: 150 animals", "counts": {"animals": 150}, "elapsed_s": 1.51}
```

---

## 7. Python API

```python
from livestock_engine import DedupConfig, run_pipeline
from livestock_engine.camera import CameraModel
from livestock_engine.ingest import IngestOptions, ingest_files
from livestock_engine.report import build_report

opts = IngestOptions(camera=CameraModel(4000, 3000, 84.0, "diagonal"))
dataset, ingest_report = ingest_files(["flight.SRT"], ["boxes.csv"], opts)
result = run_pipeline(dataset, DedupConfig(eps_m=2.0))

print(result.dedup.n_unique, result.summary()["density"])
for animal in result.animals:
    print(animal.animal_id, animal.label, animal.latitude, animal.longitude, animal.n_observations)
report = build_report(dataset, result, ingest_report.warnings)   # same dict as report.json
```

`DedupConfig` options:
- `eps_m` (2.0): the cluster radius;
- `frame_nms_m` (0.25): folds same-image double boxes together;
- `adaptive_density` (on): dense-group mode;
- `enforce_frame_exclusivity` (on): the same-image rule.

`run_pipeline(..., registration=None)` disables frame alignment.

---

## 8. Validating against real flights

The engine was tuned on simulated surveys (`docs/PERFORMANCE.md`). Before production use, score a few
**real** flights against independent ground truth:

```bash
# Surveyed positions (GPS collars, RTK points, or animals marked on an orthomosaic)
livestock-engine validate --report report.json --truth truth.csv --json validation.json

# A yard or gate count, overall or per class
livestock-engine validate --report report.json --count 312
livestock-engine validate --report report.json --count cattle=280,sheep=32
```

- `truth.csv` needs `latitude` and `longitude` columns (optionally `label`).
- Matching is one-to-one within the run's effective cluster radius (override with `--radius`).
- The result lists precision, recall, position error, per-class counts, and every missed truth
  point and extra animal, so you can inspect them on the map.

---

## 9. Integration checklist

- [ ] Headings are **true north** (or telemetry carries gimbal yaw); pitch is logged, or the flight was nadir.
- [ ] Altitude is **above ground** (height above take-off on flat terrain), not above sea level.
- [ ] Image width/height/FOV in the app or on the CLI match the images the detector ran on (after any resizing).
- [ ] Detection frame keys use the same image names or frame numbering as the telemetry.
- [ ] With several SRT files keyed by frame number or time, detections carry a `video` column.
- [ ] Check `report.json → ingest.unmatched` and `warnings` after the first run of a new data source.
- [ ] Validate at least one real flight with `livestock-engine validate` before relying on counts.
