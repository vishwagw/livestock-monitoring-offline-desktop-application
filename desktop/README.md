# Livestock Counter – desktop app (Phase 2)

An offline Electron + React application for drone pilots. Drop in a flight log
and the AI detection log, press **Remove duplicates & count**, and see the true
headcount on a local map:

* **Green** markers are distinct animals (cluster centroids).
* **Red** markers are the duplicate entries that were removed.
* Clicking an animal draws a link to every sighting it absorbed.

No network access is needed or allowed. All processing runs on this machine
through the Python engine in `../src/livestock_engine`.

![Desktop app](../docs/desktop-app.png)

## Running it

Requirements: Node.js ≥ 20, and Python ≥ 3.10 with the engine installed.

```bash
# from the repository root
pip install -e .            # numpy, scipy, scikit-learn, pyproj + livestock_engine

cd desktop
npm install
npm run dev                 # Vite dev server + Electron with hot reload
```

Try it with `../examples/sample-flight/flight.SRT` and
`../examples/sample-flight/detections.csv`. That is a simulated 150-animal
survey; `ground_truth.csv` holds the true positions.

At start-up the app looks for a Python that can `import livestock_engine`, in
this order:
1. the interpreter chosen with **Choose Python…**;
2. `$LIVESTOCK_ENGINE_PYTHON`;
3. `../.venv`;
4. `python3` / `python` (or `py -3` on Windows).

In development the engine is imported straight from `../src`.

| Script | Purpose |
| --- | --- |
| `npm run dev` | Development app with hot reload |
| `npm run build` / `npm start` | Production bundles in `out/` / run them |
| `npm run typecheck` | TypeScript for main, preload and renderer |
| `npm test` | Unit tests (vitest): IPC validation, file detection, engine runner, tiles, routing, tile seeder |
| `npm run test:e2e` | Real-Electron smoke test on the sample flight (run `npm run build` first; on headless Linux use `xvfb-run -a`) |
| `npm run package` | Installers via electron-builder (`release/`) |
| `npm run seed-tiles -- …` | Pre-download map tiles for offline use (see below) |

## Architecture

```
┌──────────── renderer (sandboxed, React) ────────────┐
│ DropZones · SettingsPanel · MapView (Leaflet) · …   │
│            window.livestock  (typed API)            │
└───────────────────────┬─────────────────────────────┘
          contextBridge │ ipcRenderer.invoke / on
┌───────────────────────▼─────────────────────────────┐
│ main process (Node.js)                              │
│  ipc.ts        validates every payload, sender check│
│  files.ts      registry: path → opaque file id      │
│  engine.ts     spawn python -m livestock_engine     │
│                process --progress (JSON lines)      │
│  tiles.ts      tiles://cache/{z}/{x}/{y} from disk  │
│  settings.ts   userData/settings.json               │
└───────────────────────┬─────────────────────────────┘
                        │ child process, argv only (no shell)
               livestock_engine ingest → dedup → report.json
```

1. A dropped file's path comes from `webUtils.getPathForFile` in the preload
   script. The main process checks it (absolute path, regular file, `.srt`,
   `.csv`, `.json` or `.txt`, at most 4 GB), sniffs the first 16 KB to classify
   it, and returns an **opaque id**. From then on the renderer can only refer
   to files by id.
2. **Run** sends ids plus camera, flight and clustering settings. The main
   process validates every field and rebuilds the request, so unknown keys are
   dropped. It then spawns the engine in a per-run folder under `userData/runs`
   (the last 5 are kept).
3. The engine streams `{"event": "progress" | "done" | "error"}` lines, which
   drive the progress bar. `report.json` holds the summary, the animals, every
   detection with `status: kept | duplicate`, frames and flight paths.
4. **Export** copies the run's `animals.csv`, `animals.geojson`, `report.json`
   or `assignments.csv` (the per-detection audit) to a location picked in a
   save dialog.

### Security model

The renderer is treated as untrusted:
- `contextIsolation`, `sandbox` and no `nodeIntegration`.
- A strict CSP: no inline scripts, `connect-src 'self'`, and images only from
  `self`, `data:`, `blob:` and `tiles:`.
- Every IPC handler checks that the calling frame is the app's own page.
- Paths such as the Python interpreter or the tile folder can only be set
  through native dialogs, never from renderer data.
- Opening new windows, navigating, attaching webviews and permission requests
  are all denied.
- **Offline is enforced**: `webRequest` cancels any request that isn't `file:`,
  `tiles:`, `data:`, `blob:` or `devtools:` (plus the Vite dev server in
  development).

`npm run test:e2e` checks these in a real Electron window.

## Drop zones and file formats

| Zone | Accepts | Detected as |
| --- | --- | --- |
| Flight logs | DJI `.SRT` (Mavic 3 / Air / Mini, Phantom 4, Mavic Pro styles), telemetry CSV (per-photo metadata or time-series logs such as AirData) | SRT with GPS, CSV with latitude + longitude columns |
| Detection logs | Bounding-box CSV/JSON (`xmin…ymax`, `x,y,width,height`, `cx,cy,w,h`, `x,y`), COCO results JSON, YOLO `.txt` | box columns plus an image, frame or time column |
| Engine dataset (link) | Phase 1 dataset `.json` / `.csv` | processed on its own |

Files dropped on the "wrong" zone are moved to the right one, with a notice.
Files that aren't recognised stay where they were dropped, and the engine
reports exactly what it could not read. Column aliases and unit handling are
listed in the root README.

Camera presets cover common DJI models, or use **Custom**. Other settings:
- **Box ground point**: box centre for top-down images, bottom edge for oblique.
- **Video FPS**: for detection logs keyed by frame number.
- **YOLO class names**: labels for numeric class ids.
- **Heading from GPS track**: used when the log has no yaw.
- De-duplication: cluster radius ε (2.0 m by default), frame alignment, and
  the same-image rule.

## Offline maps

The map works with no basemap at all: a neutral grid with a metric scale bar
is drawn instead. To show imagery in the field, prepare a **tile cache** while
online. It is a folder in the standard XYZ layout, `<dir>/<z>/<x>/<y>.png|jpg|webp`,
as produced by most tile downloaders. Select it with **Map tiles:** in the
top bar. The app serves tiles from disk through the `tiles://` protocol and
only draws them within the cached zoom range, scaling up beyond the highest
level.

`npm run seed-tiles` builds such a cache from a tile server you are licensed
to cache:

```bash
npm run seed-tiles -- --bbox 151.205,-33.872,151.214,-33.865 --zoom 14-19 \
  --url "https://tiles.example.com/{z}/{x}/{y}.png?key=YOUR_KEY" --out ~/LivestockTiles
```

It resumes (existing tiles are skipped) and caps the download size
(`--max-tiles`, default 20,000). It refuses `tile.openstreetmap.org`, whose
usage policy forbids bulk downloads.

## Packaging

`npm run package` builds installers with electron-builder: NSIS for Windows,
DMG for macOS, AppImage/deb for Linux. The Python engine source is bundled
under `resources/engine`. The target machine still needs a Python with
`numpy`, `scipy`, `scikit-learn` and `pyproj`; bundling a self-contained
interpreter is planned for a later phase. No application icon is configured
yet, so electron-builder uses its default.

## Project layout

```
desktop/
  electron.vite.config.ts   main / preload / renderer builds (+ dev-only CSP relaxations)
  src/shared/               IPC contract (types.ts) and file-type sniffing (fileKinds.ts)
  src/main/                 app lifecycle, IPC, engine runner, file registry, tiles, settings
  src/preload/              contextBridge API
  src/renderer/             React UI (App, DropZone, FileList, SettingsPanel, MapView, …)
  scripts/seed-tiles.mjs    offline tile cache builder
  tests/                    vitest unit tests
  e2e/smoke.mjs             Playwright-driven Electron smoke test
```
