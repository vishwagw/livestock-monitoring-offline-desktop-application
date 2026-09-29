# Build environments and release procedure

How to compile the standalone installers, and what each build environment needs. Every installer
bundles Electron, the UI and a PyInstaller-frozen engine; target machines need nothing else.

## 1. Toolchain (verified versions)

| Tool | Version | Pinned in |
| --- | --- | --- |
| Python | 3.12 (3.11+ works) | `.python-version`, CI |
| numpy / scipy / scikit-learn / pyproj (+ closure) | 2.4.6 / 1.17.1 / 1.9.1 / 3.7.2 | `requirements.lock` |
| PyInstaller (+ hooks-contrib) | 6.22.3 (2026.7) | `requirements-build.txt` |
| Node.js | 22 | `desktop/.nvmrc`, CI |
| Electron | 44.4.5 | `desktop/package-lock.json` |
| electron-builder / electron-vite / Vite | 26.15.3 / 5.0.0 / 7.3.6 | `desktop/package-lock.json` |

Install exactly these with `pip install -r requirements-build.txt` and `npm ci` (never `npm install`
for a release).

## 2. Per-OS build environment

PyInstaller does not cross-compile, so **each installer is built on its own OS and CPU architecture**.

| Target | Build machine | Extra requirements | Output |
| --- | --- | --- | --- |
| Windows x64 `.exe` (NSIS) | Windows 10/11 x64 | none (wheels are prebuilt); optional code-signing certificate (`.pfx`) | `LivestockCounter-<ver>-win-x64.exe` |
| macOS Apple Silicon `.dmg` | macOS 13+ on arm64 | Xcode Command Line Tools; optional Developer ID certificate + Apple notarisation account | `LivestockCounter-<ver>-mac-arm64.dmg` |
| macOS Intel `.dmg` | macOS on x64 | as above | `LivestockCounter-<ver>-mac-x64.dmg` |
| Linux x64 AppImage + `.deb` | Ubuntu 22.04+ x64 | `libfuse2` to *run* AppImages; `xvfb` for headless tests | `LivestockCounter-<ver>-linux-x86_64.AppImage`, `…-amd64.deb` |

The GitHub Actions workflow `.github/workflows/release.yml` implements exactly this matrix, so it is
the reference build environment. The runner labels (`windows-latest`, `macos-14`, `macos-15-intel`,
`ubuntu-latest`) may need updating as GitHub retires images.

## 3. Building an installer locally

```bash
# repository root
pip install -r requirements-build.txt && pip install --no-deps -e .
python packaging/build_engine.py        # → desktop/resources/engine, then self-tested with Python off PATH

cd desktop
npm ci
npm run package:win                     # or package:mac / package:linux
npm run test:release                    # launch the packaged app offline without Python; checks the count
```

`build_engine.py` fails the build unless the frozen engine:
1. passes `self-check` (PROJ database, DBSCAN, BLAS) with **no Python on PATH**;
2. counts the sample flight exactly.

`scripts/verify-engine.cjs` also refuses to package without a frozen engine built for the target OS.

## 4. Code signing (optional but recommended)

| Platform | Secrets / environment | Without them |
| --- | --- | --- |
| Windows | `CSC_LINK` (base64 `.pfx` or path), `CSC_KEY_PASSWORD` | SmartScreen shows "unknown publisher"; users click *More info → Run anyway* |
| macOS | `CSC_LINK`, `CSC_KEY_PASSWORD` (Developer ID Application), `APPLE_ID`, `APPLE_APP_SPECIFIC_PASSWORD`, `APPLE_TEAM_ID` | the app is ad-hoc signed so it runs on Apple Silicon; Gatekeeper asks once (right-click → Open) |

In CI, add these as repository secrets with the same names; `release.yml` passes them through.
`desktop/build/entitlements.mac.plist` grants the hardened-runtime entitlements the frozen engine needs:
- JIT (for Electron);
- disabled library validation (for the bundled Python extensions).

## 5. Release procedure

1. **Versions.** Bump `version` in `pyproject.toml`, `src/livestock_engine/__init__.py` and
   `desktop/package.json`, and add a `CHANGELOG.md` entry.
2. **Checks.** Run `make test benchmark` and `cd desktop && npm test`. CI must be green on the release commit.
3. **Licenses.** If dependencies changed, run `make licenses` and commit `docs/THIRD_PARTY_LICENSES.md`.
   The packaging test enforces this.
4. **Tag.** Tag and push: `git tag v0.4.0 && git push origin v0.4.0`.
5. **Build.** `release.yml` builds every installer, runs the release check on each OS, and attaches the
   installers to a **draft** GitHub release.
6. **Smoke test.** Install on one machine per OS with networking disabled, open
   `examples/sample-flight`, and confirm the count is 150.
7. **Source package.** Run `make source-package` for the hand-off archive (§6), then publish the draft release.

## 6. Source-code package

```bash
python packaging/make_source_package.py --ref v0.4.0
# dist/livestock-counter-src-0.4.0.zip  (+ .sha256)
```

The archive contains every tracked file at the ref, plus `MANIFEST.sha256` with a per-file checksum.
It never includes build output, `node_modules` or local settings. It is reproducible: the same commit
always gives the same bytes, so a recipient can rebuild it from the repository and compare.

## 7. Troubleshooting builds

| Symptom | Cause / fix |
| --- | --- |
| `self-check failed … No module named …` from `build_engine.py` | a module was excluded or missed by PyInstaller; add it to `hiddenimports` in `packaging/livestock-engine.spec` (never exclude `unittest`, because SciPy imports `numpy.testing`) |
| `Frozen engine not found` during packaging | run `python packaging/build_engine.py` first, on the same OS |
| Packaged app shows **Engine unavailable** | the bundled engine failed its self-check at start-up; run `resources/engine/livestock-engine self-check` from the install folder to see why |
| Blank window in a release build | the UI must load from `app://bundle` (fuses disable file-protocol privileges); check `appProtocol.ts` |
| macOS: "app is damaged" | quarantine flag on an unsigned download: sign/notarise, or `xattr -dr com.apple.quarantine "/Applications/Livestock Counter.app"` |
