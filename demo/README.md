# Client demos

Two demos for prospective clients (drone survey operators). Both are built from **real engine runs** on
simulated surveys and are labelled as simulated data.

| Demo | What it shows | Output |
| --- | --- | --- |
| Web demo | Interactive page: replay a survey, watch detections pile up, de-duplicate to the true headcount (cattle paddock, sheep yards, station block), plus workflow fit, speed and accuracy evidence | `web/livestock-counter-demo.html` (self-contained, ~0.5 MB) |
| App video | Captioned recording of the real packaged desktop app counting a 55,357-detection station survey offline: add files → live progress → map → traceable animal → export | `video/out/livestock-counter-demo.mp4` (~57 s, 1440×900) |

## Rebuilding the web demo

```bash
python demo/build_web_demo.py      # runs the engine on each scenario, embeds results into web/template.html
```

Edit copy or styling in `web/template.html`; scenario definitions live in `build_web_demo.py`.

## Re-recording the app video (Linux, Xvfb)

```bash
python demo/video/make_survey.py demo/video/data                      # 4,838 images, 55,357 detections
python packaging/build_engine.py                                      # frozen engine, if not built
cd desktop && npm run build
LIVESTOCK_TEST_BUILD=1 npx electron-builder --config electron-builder.config.cjs --dir -c.directories.output=release-demo
cd .. && xvfb-run -a -s "-screen 0 1600x1000x24" node demo/video/record.mjs \
  desktop/release-demo/linux-unpacked/livestock-counter demo/video/data demo/video/out
pip install imageio-ffmpeg && python demo/video/to_mp4.py demo/video/out
```

- The recorder uses a *test-mode* package, because release fuses block the automation hook.
- Python is removed from `PATH`, so the bundled engine is what runs.
- Captions and the cursor are drawn into the window.
- Processing time on screen is longer than normal, because recording shares the CPU. Quote speed
  figures from `docs/PERFORMANCE.md` instead.
