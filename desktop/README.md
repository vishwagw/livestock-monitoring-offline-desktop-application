# Livestock Counter – desktop app (Phases 2 & 3)

An offline Electron + React application for drone pilots. Drop in a flight log
and the AI detection log, press **Remove duplicates & count**, and see the true
headcount on a local map:

* **Green** markers are distinct animals (cluster centroids).
* **Red** markers are the duplicate entries that were removed.
* Clicking an animal draws a link to every sighting it absorbed.

No network access is needed or allowed. All processing runs on this machine.
Installed builds carry a self-contained copy of the spatial engine
(`../src/livestock_engine`, frozen with PyInstaller), so field laptops need
no Python, Node.js or internet.

![Desktop app](../docs/desktop-app.png)

## Running it

Requirements: Node.js 22 (`.nvmrc`), and Python ≥ 3.11 with the engine installed (for development only; installed builds bundle the engine).

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

At start-up the app picks an engine and checks it with
`livestock-engine self-check`, which imports every native dependency and runs
PROJ and DBSCAN once. The top bar shows the engine it chose
(`Engine 0.3.0 · built-in` or `· Python 3.x`).

| Build | Engine order |
| --- | --- |
| Installed / packaged | bundled `resources/engine/livestock-engine[.exe]`, then any Python below as a fallback |
| Development (`npm run dev`) | Python with `../src` on `PYTHONPATH`: **Choose Python…**, then `$LIVESTOCK_ENGINE_PYTHON`, `../.venv`, `python3` / `python` / `py -3`; after that `resources/engine` if you've built it |

`$LIVESTOCK_ENGINE_BIN` forces a specific frozen engine, which is useful for
testing a PyInstaller build in development.

| Script | Purpose |
| --- | --- |
| `npm run dev` | Development app with hot reload |
| `npm run build` / `npm start` | Production bundles in `out/` / run them |
| `npm run typecheck` | TypeScript for main, preload and renderer |
| `npm test` | Unit tests (vitest): IPC validation, file detection, engine runner, tiles, routing, tile seeder |
| `npm run test:e2e` | Real-Electron smoke test on the sample flight (run `npm run build` first; on headless Linux use `xvfb-run -a`). Set `E2E_APP=<packaged executable> E2E_NO_PYTHON=1` to test a test-mode package |
| `npm run build:engine` | Freeze the engine with PyInstaller into `resources/engine` and self-test it |
| `npm run package:win` / `:mac` / `:linux` | Installers in `release/` (`.exe`, `.dmg`, AppImage + `.deb`) |
| `npm run package:dir` | Unpacked app only (quick local check) |
| `npm run test:release` | Launch the hardened packaged app in `release/` offline with no Python on `PATH`, process the sample flight and check the headcount |
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
│  engine.ts     spawn livestock-engine (bundled) or  │
│                python -m livestock_engine           │
│                process --progress (JSON lines)      │
│  appProtocol   app://bundle/ serves the UI          │
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
3. The engine streams `{"event": "progress" | "done" | "error"}` JSON lines.
   Every progress event carries:
   - the stage list;
   - the current stage and its own %;
   - the overall % (weighted by stage, never decreasing);
   - a message and live counters (detections read and matched, frames,
     animals so far).

   Updates come per frame while georeferencing and per pass while aligning,
   throttled to 10 Hz. The main process validates each event and forwards it
   to the **processing dashboard**, which shows the stage checklist, overall
   %, elapsed time and time remaining. **Cancel** kills the engine process.
   `report.json` holds the summary, the animals, every detection with
   `status: kept | duplicate`, frames and flight paths.
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
- **Offline is enforced**: `webRequest` cancels any request that isn't `app:`,
  `tiles:`, `data:`, `blob:` or `devtools:` (plus the Vite dev server in
  development).
- **The UI is served from `app://bundle/`**, a privileged custom scheme, not
  from `file://`. It only serves files inside the built renderer folder, from
  a MIME allow-list, with no path traversal. The page therefore never gets
  file-protocol privileges.
- **Release builds apply Electron fuses**:
  - off: run-as-node, `NODE_OPTIONS`, `--inspect`, extra file-protocol
    privileges;
  - on: ASAR integrity validation, loading the app only from the ASAR
    archive, cookie encryption.

  Nobody can reuse the installed binary as a general Node.js runtime, or
  attach a debugger to it.
- The frozen engine runs with `PYTHONPATH` / `PYTHONHOME` removed, so a
  Python installed on the laptop can't interfere with it.

`npm run test:e2e` checks these in a real Electron window.
`npm run test:release` repeats the key checks against the hardened installer
build.

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
- De-duplication: cluster radius ε (2.0 m by default), frame alignment, the
  same-image rule, and **Adapt to tightly packed animals** (dense-group mode
  for sheep yards and feedlots; see `../docs/PERFORMANCE.md`).

The map draws animals, duplicates and capture points with a canvas
point-cloud layer (`src/renderer/src/lib/pointLayer.ts`), and the animal table
is virtualised. A 221k-detection survey renders in about a second.

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

## Building the standalone installers (Phase 3)

Every installer bundles Electron, the UI and a frozen copy of the engine, and
needs nothing else on the target machine:

| Platform | Output | Built on |
| --- | --- | --- |
| Windows x64 | `LivestockCounter-<ver>-win-x64.exe` (NSIS; choose the install folder, per-user) | Windows |
| macOS Apple Silicon / Intel | `LivestockCounter-<ver>-mac-arm64.dmg` / `-x64.dmg` | macOS (arm64 / x64) |
| Linux x64 | `LivestockCounter-<ver>-linux-x86_64.AppImage`, `…-amd64.deb` | Linux |

PyInstaller does not cross-compile, so each installer is built on its own OS.
The `Release installers` workflow does this on a runner matrix (for a `v*`
tag or a manual run) and attaches the results to a draft GitHub release.
Locally:

```bash
# 1. Freeze the engine (repository root). PyInstaller one-folder bundle ->
#    desktop/resources/engine; the script then runs it with Python removed
#    from PATH: self-check + processing the sample flight.
pip install -e . pyinstaller
python packaging/build_engine.py

# 2. Package (desktop/)
npm ci
npm run package:win        # or package:mac / package:linux

# 3. Verify the installed-style build offline, without Python
npm run test:release       # Linux headless: xvfb-run -a npm run test:release
```

How it fits together:

* `packaging/livestock-engine.spec`:
  - one-folder build, so no unpack-to-temp on every run (startup ~1.5 s);
  - collects PROJ's `proj.db` and the compiled scikit-learn / SciPy modules;
  - drops tkinter, matplotlib, pandas, test suites and other things the field
    app never uses.

  On Linux the result is about 180 MB (70 MB compressed), almost all of it
  numpy / SciPy / scikit-learn native libraries.
* `electron-builder.config.cjs`:
  - copies `resources/engine` into the app's `resources/engine`, with maximum
    compression;
  - sets `publish: null` (no auto-update feed, which suits offline laptops);
  - applies the Electron fuses.

  `scripts/verify-engine.cjs` refuses to package without an engine built for
  the target OS, and `scripts/after-pack.cjs` strips Electron's default app
  and update metadata. The Linux installers come to about 157 MB each.
* **Code signing** is optional:
  - Windows and macOS sign with `CSC_LINK` / `CSC_KEY_PASSWORD`;
  - macOS also notarises with `APPLE_ID` / `APPLE_APP_SPECIFIC_PASSWORD` /
    `APPLE_TEAM_ID`, via CI secrets of the same names.

  Without them:
  - macOS builds are ad-hoc signed, so they still run on Apple Silicon, but
    Gatekeeper asks the user to allow the app once (right-click → Open);
  - Windows SmartScreen shows "unknown publisher".

  `build/entitlements.mac.plist` grants the hardened-runtime entitlements the
  frozen engine needs.
* No application icon is set yet (electron-builder falls back to Electron's).
  Add `build/icon.png` (1024×1024) to brand the installers.

## Project layout

```
desktop/
  electron.vite.config.ts   main / preload / renderer builds (+ dev-only CSP relaxations)
  src/shared/               IPC contract (types.ts) and file-type sniffing (fileKinds.ts)
  electron-builder.config.cjs  installers: targets, fuses, signing, bundled engine
  build/                    macOS entitlements (installer resources)
  resources/engine/         frozen engine (generated by ../packaging/build_engine.py, git-ignored)
  src/main/                 app lifecycle, IPC, engine runner/locator, app:// + tiles:// protocols, file registry, settings
  src/preload/              contextBridge API
  src/renderer/             React UI (App, DropZone, ProgressPanel, SettingsPanel, MapView, …)
  scripts/                  seed-tiles.mjs (offline tile cache), verify-engine / after-pack build hooks
  tests/                    vitest unit tests
  e2e/smoke.mjs             Playwright-driven Electron smoke test (dev build or test-mode package)
  e2e/release-check.mjs     verifies a hardened release build over DevTools, offline and without Python
```
