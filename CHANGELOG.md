# Changelog

## 0.4.0 — Phase 4: validation, performance and handover

### Accuracy
- **Dense-group mode** for sheep yards, feedlots and calves lying together:
  - animal spacing is measured inside single images;
  - frames are pre-aligned by clustering-free pairwise displacement voting (`sync.py`);
  - the cluster radius is tightened to 0.45 × spacing.

  Yards 0.6–0.9 m apart went from 72–96% to 99.4–99.9% count accuracy, with no under-counting.
- Same-image duplicate-box suppression (`frame_nms_m`, default 0.25 m).
- Validation and evaluation matching now gate costs at the match radius; a distant false detection no
  longer drags correct matches apart.

### Performance
- 55k-detection pipeline 7.2 s → 2.6 s. Full raw-log path scales linearly (~10.5k detections/s; 221k in 21 s).
- Fixed quadratic cluster resolution; vectorised resolve; heap-based same-image split
  (near-linear, bounded memory).
- Desktop map: canvas point-cloud layer and virtualised animal table. 221k detections render in 1.1 s
  instead of 17.7 s, with 70% less memory.

### New commands and options
- `livestock-engine validate`: score a report against surveyed positions or manual counts.
- `livestock-engine stress`: timing and memory of the full pipeline at scale.
- `--no-adaptive-density`, `--frame-nms`; desktop setting "Adapt to tightly packed animals".
- Simulator: `layout="pens"`, profiles `sheep_pen` and `stress`. Fixed profile overrides that repeated
  a profile key.

### Handover
- JSON Schemas for every exchanged file (`schemas/`), tested against real output.
- Integration, developer, build & release, performance and pilot guides.
- Handover inventory.
- Pinned build environments (`requirements*.txt`, `.python-version`, `.nvmrc`), `Makefile`.
- Generated third-party license inventory; reproducible source-package builder.

## 0.3.0 — Phase 3: standalone installers
- PyInstaller-frozen engine bundled in NSIS `.exe`, `.dmg`, AppImage and `.deb`, with no Python needed.
- Staged, weighted progress streaming and a processing dashboard.
- Electron fuses; UI served from `app://`; release verification over DevTools.

## 0.2.0 — Phase 2: offline desktop app
- Electron + React app with drop zones, sandboxed IPC and an offline Leaflet map (local tile cache).
- Ingestion of DJI `.SRT`, telemetry CSV and bounding-box logs (CSV/JSON/COCO/YOLO).

## 0.1.0 — Phase 1: spatial engine
- Ray casting from pixels to UTM, DBSCAN de-duplication (ε = 2.0 m), the same-image rule, frame
  alignment, CLI, simulator and accuracy benchmark.
