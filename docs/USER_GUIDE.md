# Livestock Counter: pilot guide

Livestock Counter turns an overlapping drone survey into a true headcount. Each animal is photographed
several times as the flight lines overlap; the app works out which detections are the same animal and
counts it once. **It works completely offline.**

![Livestock Counter](desktop-app.png)

## Before the flight

- **Overlap:** plan ≥ 60% front and side overlap (70/60 is ideal). The app relies on seeing each animal
  more than once.
- **Camera straight down** (gimbal −90°) unless you set the pitch in the app. Fly at a constant height
  above the paddock.
- **Keep the logs:**
  - *video*: the DJI `.SRT` file recorded next to each video;
  - *photos*: a telemetry CSV (image name, latitude, longitude, height, gimbal yaw and pitch), e.g.
    exported from the photos' metadata.
- **Map background (optional):** while you still have internet, ask your administrator for a map tile
  folder of the area (see *Map tiles* below). Without one, the app shows a plain grid, which works fine.

## Counting a flight

1. **Add the flight data.** Drag files onto the drop zones, or click a zone to browse.
   - **Flight logs**: the `.SRT` file(s) or telemetry CSV.
   - **Detection logs**: the AI model's bounding-box file (CSV, JSON, COCO or YOLO `.txt`).

   Files dropped on the wrong zone are moved automatically, with a note.
2. **Check the settings.** Pick your drone under **Drone camera**. If you ran the detector on video
   frames, set **Video FPS**.
   - For oblique (angled) imagery, set **Box ground point** to *Bottom edge*.
   - Leave **Adapt to tightly packed animals** on: it is what keeps sheep yards and feedlots from being
     under-counted.
3. **Press "Remove duplicates & count".** The panel shows each processing step, the % complete, the
   time left, and live counts. **Cancel** stops the run at any time.
4. **Read the result.**
   - **True headcount** (green): the number of distinct animals, per class.
   - **Duplicates removed** (red): repeat sightings merged into those animals.
   - **Frame alignment**: how much the app corrected GPS/compass drift between photos. It says
     *dense groups* when it switched to tight-packing mode.
   - Open **warnings** (under the Count button) after a new kind of flight: they list detections that
     couldn't be matched to the flight log, and why.

## Reading the map

| Marker | Meaning |
| --- | --- |
| Green dot | one animal (counted once) |
| Red dots | the extra sightings of that animal that were removed; they sit around the green dot |
| Blue dashed line | the flight path |
| Grey dots (off by default) | where each photo was taken |

- **Selecting an animal**: click a green or red dot, or a row in **Animal index**. The map zooms in,
  draws yellow lines to every sighting that was merged into that animal, and shows its class,
  confidence and number of sightings.
- **Class flag**: ⚑ next to a class means the sightings disagreed (e.g. 8× sheep, 1× goat). The most
  confident class is used.
- **Layers**: the legend switches each layer on and off.

## Exporting

**Export** in the top right saves:
- **CSV**: the animal list for spreadsheets;
- **GeoJSON**: points for GIS software;
- **Report**: everything shown on the map;
- **Audit**: every detection and the animal it became.

## Map tiles

Click **Map tiles:** in the top bar and choose a folder of cached map tiles, which must contain
numbered folders such as `15/`, `16/`, …. The app never downloads anything; it only reads that folder.

## Troubleshooting

| Message | What to do |
| --- | --- |
| *Engine unavailable* | Reinstall the app. The processing engine ships inside it and needs no Python. |
| *N detections fall outside the configured image size* | The camera setting doesn't match the images the detector saw (e.g. 4K video vs. 4000×3000 photos). Fix **Width/Height**. |
| *skipped detections: no telemetry for frame* | The detection file's image names or frame numbers don't match the flight log. Check you picked the log from the same flight. |
| *heading missing … derived from the GPS track* | The log has no gimbal yaw, so the app used the flight direction. Fine for straight survey lines; less accurate on manual flights. |
| Counts look too high along one flight line | Animals may have moved between passes. Check the red/green pattern there; consider flying yards when stock is settled. |

## Accuracy you can expect

On realistic simulated surveys the count is within 1% of the truth, for both open-paddock cattle and
sheep packed 0.6–0.9 m apart (see [PERFORMANCE.md](PERFORMANCE.md)). Real-world accuracy also depends
on your detector and flight. Compare a few flights with a gate count before relying on the numbers.
