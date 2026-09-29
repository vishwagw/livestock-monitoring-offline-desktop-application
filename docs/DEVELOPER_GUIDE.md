# Developer guide

For engineers who maintain or extend Livestock Counter. Related guides:
- [INTEGRATION_GUIDE.md](INTEGRATION_GUIDE.md): file formats;
- [BUILD_AND_RELEASE.md](BUILD_AND_RELEASE.md): installers;
- [PERFORMANCE.md](PERFORMANCE.md): validation results.

## 1. Architecture

```
┌────────────────────────── Desktop app (desktop/, Electron) ──────────────────────────┐
│ Renderer: React + Leaflet, sandboxed, served from app://bundle                      │
│   DropZone · SettingsPanel · ProgressPanel · MapView (canvas point layers) · table  │
│                     │ window.livestock (typed preload bridge)                        │
│ Main process (Node.js): validates every IPC call, file registry (opaque ids),       │
│   tiles:// offline map server, settings, engine locator + runner                    │
└──────────────────────────────────────┬───────────────────────────────────────────────┘
                                       │ spawn (argv, no shell) · JSON-lines progress on stdout
┌──────────────────────────────────────▼───────────────────────────────────────────────┐
│ Spatial engine (src/livestock_engine, Python; frozen with PyInstaller in installers) │
│  ingest/   SRT · telemetry CSV · box logs → Dataset (frames + pixel detections)      │
│  pipeline  project → density plan → [sync] → cluster → align×3 → resolve → report    │
└───────────────────────────────────────────────────────────────────────────────────────┘
```

### Engine pipeline (`pipeline.run_pipeline`)

| Step | Module | What happens |
| --- | --- | --- |
| Ingest | `ingest/` | Parse logs, match each box to a telemetry sample (§3 of the integration guide), build a `Dataset` |
| Georeference | `projection.py`, `geo.py` | Pinhole ray per pixel, rotated by grid heading / pitch / roll, intersected with the ground plane in one local UTM zone |
| Density plan | `density.py` | In-image nearest-neighbour spacing decides `normal` or `dense` mode and the effective ε |
| Pre-align (dense only) | `sync.py` | Pairwise displacement voting + robust pose-graph least squares |
| Cluster | `dedup.py` | Same-image NMS → DBSCAN(ε) → same-image split (heap-based cannot-link agglomeration) |
| Align | `registration.py` | 3 passes: fit a trimmed similarity transform per image to the leave-one-out cluster centroids, re-cluster |
| Resolve | `dedup._resolve_all` | One record per cluster: centroid, best-confidence class, votes, spread, provenance |
| Report | `report.py`, `io.py` | Map report, CSV, GeoJSON, audit trail |

Progress is reported by stage through `progress.ProgressTracker`, which the CLI emits as JSON lines.

### Key design decisions

- **Same-image rule (cannot-link).** Two detections in one image are two animals. This is the strongest
  constraint available, and it stops DBSCAN's chaining from under-counting.
- **Align images, don't correct poses.** Telemetry errors move a whole image's projections almost
  rigidly. Estimating a 2D correction per image is simpler and more robust than refining 6-DoF poses.
- **Density is measured inside images.** Relative positions within one image don't depend on GPS
  error, so they are a reliable signal for adapting ε.
- **Opaque file ids across IPC.** The renderer never hands paths to the engine; the main process only
  runs files that were admitted through a drop or a native dialog.
- **The engine is a separate process.** It keeps Python out of Electron, can be killed cleanly on
  cancel, and is the same binary the CLI tests exercise.

## 2. Repository map

```
src/livestock_engine/   engine package
  cli.py                all commands (process, ingest, dedup, validate, benchmark, stress, simulate, self-check)
  camera.py geo.py projection.py      geometry
  dedup.py density.py sync.py registration.py pipeline.py   de-duplication core
  ingest/               raw-log readers + telemetry join
  report.py io.py validation.py metrics.py progress.py simulate.py
tests/                  pytest suite (unit, end-to-end, schema, docs, packaging)
schemas/                JSON Schemas of every exchanged file
examples/sample-flight/ simulated DJI SRT + telemetry CSV + box CSV + truth
packaging/              PyInstaller spec, engine build/self-test, licenses, source package
desktop/                Electron app (see desktop/README.md for its own map)
.github/workflows/      ci.yml (every push), release.yml (installers per OS)
docs/                   this guide, integration guide, build & release, performance, user guide, handover
```

## 3. Setting up

Prerequisites:
- Python 3.11+ (3.12 recommended, see `.python-version`);
- Node.js 22 (`desktop/.nvmrc`);
- Git.

**Linux / macOS**

```bash
python3 -m venv .venv && source .venv/bin/activate
make install                 # pinned deps + editable engine
make desktop-install         # npm ci in desktop/
make test desktop-test       # all unit tests
cd desktop && npm run dev    # app with hot reload; uses ../src via ../.venv
```

**Windows (PowerShell)**

```powershell
py -3.12 -m venv .venv; .venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt; pip install --no-deps -e .
pytest
cd desktop; npm ci; npm test; npm run dev
```

The app looks for an engine in this order:
1. **Choose Python…**;
2. `$LIVESTOCK_ENGINE_PYTHON`;
3. `../.venv`;
4. `python3` / `py -3`.

Set `LIVESTOCK_ENGINE_BIN` to test a frozen engine in development.

## 4. Tests

| Suite | Command | Covers |
| --- | --- | --- |
| Engine | `pytest` | geometry, clustering, dense mode, ingestion formats, progress, validation, schemas, docs sync, packaging |
| Accuracy gate | `make benchmark` | ≥ 99% on 5 simulated profiles (CI runs it on every push) |
| Scale | `make stress` | 55k–221k detections through the full raw-log path |
| Desktop unit | `cd desktop && npm test` | IPC validation, file detection, engine runner (Python **and** frozen engine), app/tile protocols, routing, geometry |
| Desktop E2E | `npm run build && xvfb-run -a npm run test:e2e` | real Electron: drop → engine → map → export, sandbox/offline checks |
| Release check | `npm run test:release` | the hardened installed app, offline, without Python |

`tests/test_docs.py` fails if a column alias or CLI command is missing from the integration guide, or if
its Python example stops working. `tests/test_packaging.py` fails if `requirements.lock` or the license
inventory drifts from the real dependency set.

## 5. Common changes

### Support a new telemetry or detection format

1. **Parser.** Add it under `src/livestock_engine/ingest/`, returning a `TelemetryTrack` or a list of
   `RawDetection`. Reuse `ColumnMap` for header aliases and units.
2. **Dispatch.** Route it by extension in `ingest/assemble.load_telemetry` or `ingest/detections.load_detections`.
3. **Desktop.** If it's a new extension, add it to `ACCEPTED_EXTENSIONS` and `classifyFile` in
   `desktop/src/shared/fileKinds.ts`.
4. **Docs.** Document the columns in `docs/INTEGRATION_GUIDE.md`; the docs test enforces this. Update
   `schemas/detections.schema.json` for JSON forms.
5. **Tests.** Add a test in `tests/test_ingest.py` that uses real sample lines from the source system.

### Tune clustering

Defaults live in `DedupConfig` (`dedup.py`):

| Setting | Default | Use |
| --- | --- | --- |
| `eps_m` | 2.0 | body envelope; lower for small stock in open fields |
| `frame_nms_m` | 0.25 | raise if the detector often double-boxes large animals |
| `eps_spacing_fraction` | 0.45 | dense-mode radius as a fraction of the in-image spacing |
| `min_eps_m` | 0.35 | dense-mode floor |

Always re-run `make benchmark`, and validate on real flights.

### Add a desktop setting

Update all of these together:
1. `ClusteringSettings`/`FlightSettings` in `desktop/src/shared/types.ts` (+ `DEFAULT_SETTINGS`);
2. the validator in `src/main/validation.ts` (give old saved settings a default);
3. the CLI flag in `src/main/engine.ts → buildProcessArgs`;
4. the control in `SettingsPanel.tsx`;
5. tests in `tests/validation.test.ts` and `tests/engine.test.ts`.

## 6. Coding conventions

- **Python**:
  - type hints;
  - dataclasses for records;
  - numpy vectorisation on any per-detection path (see PERFORMANCE.md for what the loops cost);
  - `pyflakes` clean.
- **TypeScript**:
  - strict mode;
  - no Node APIs in the renderer;
  - every IPC payload validated in `validation.ts`;
  - pure logic in `lib/` or `shared/`, so it can be unit-tested in Node.
- **Commits**: one logical change per commit; CI must be green.
