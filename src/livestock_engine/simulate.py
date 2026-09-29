"""Synthetic overlapping drone surveys with known ground truth.

A herd is scattered over a field, a lawnmower flight plan with configurable
front/side overlap is flown over it, and every animal visible in a frame
becomes a detection. Telemetry, pixel and detector noise are injected so
the same animal projects to slightly different ground positions from
different frames - exactly the over-counting problem the engine solves.
"""

from __future__ import annotations

import csv
import math
from dataclasses import asdict, dataclass, replace
from pathlib import Path

import numpy as np

from .camera import CameraModel, FramePose
from .geo import LocalUTM
from .models import Dataset, Detection, Frame
from .projection import camera_rotation, ground_to_pixel, in_image


@dataclass(frozen=True)
class SimulationConfig:
    # Site
    origin_latitude: float = -33.8688
    origin_longitude: float = 151.2093
    field_width_m: float = 300.0
    field_height_m: float = 200.0
    # Herd
    n_animals: int = 150
    n_herds: int = 4
    herd_spread_m: float = 18.0
    min_separation_m: float = 3.0
    species: tuple[str, ...] = ("cattle",)
    # "herds": Gaussian herds in open pasture; "pens": animals packed
    # uniformly into rectangular pens (e.g. sheep yards before shearing).
    layout: str = "herds"
    n_pens: int = 4
    pen_width_m: float = 16.0
    pen_height_m: float = 12.0
    # Flight
    altitude_agl_m: float = 60.0
    front_overlap: float = 0.70
    side_overlap: float = 0.60
    flight_direction_deg: float = 0.0
    gimbal_pitch_deg: float = -90.0
    # Camera (DJI-style 4:3 sensor, 84 deg diagonal FOV)
    image_width: int = 4000
    image_height: int = 3000
    fov_deg: float = 84.0
    fov_type: str = "diagonal"
    edge_margin_px: float = 20.0
    # Per-frame telemetry noise (1 sigma)
    gps_sigma_m: float = 0.25
    altitude_sigma_m: float = 0.3
    heading_sigma_deg: float = 0.5
    pitch_sigma_deg: float = 0.3
    roll_sigma_deg: float = 0.2
    # Detector behaviour
    pixel_sigma_px: float = 3.0
    detection_probability: float = 0.97
    label_flip_probability: float = 0.02
    false_positives_per_frame: float = 0.0
    seed: int = 0


PROFILES: dict[str, dict] = {
    "ideal": dict(
        gps_sigma_m=0.0,
        altitude_sigma_m=0.0,
        heading_sigma_deg=0.0,
        pitch_sigma_deg=0.0,
        roll_sigma_deg=0.0,
        pixel_sigma_px=0.0,
        detection_probability=1.0,
        label_flip_probability=0.0,
    ),
    "standard": {},
    "oblique": dict(gimbal_pitch_deg=-75.0, flight_direction_deg=35.0),
    "dense": dict(n_animals=250, n_herds=3, herd_spread_m=14.0, min_separation_m=2.5),
    # Sheep packed into yards: ~1 m apart, well inside the 2 m cattle envelope.
    "sheep_pen": dict(
        layout="pens",
        n_animals=480,
        n_pens=4,
        pen_width_m=16.0,
        pen_height_m=12.0,
        min_separation_m=0.9,
        species=("sheep",),
        altitude_agl_m=40.0,
        field_width_m=120.0,
        field_height_m=90.0,
    ),
    # >50,000 detections: a 2 km x 1.5 km station with 7,000 head.
    "stress": dict(n_animals=7000, n_herds=24, herd_spread_m=22.0, field_width_m=2000.0, field_height_m=1500.0),
    "harsh": dict(
        gps_sigma_m=0.4,
        altitude_sigma_m=0.5,
        heading_sigma_deg=0.8,
        pitch_sigma_deg=0.5,
        roll_sigma_deg=0.3,
        pixel_sigma_px=6.0,
        detection_probability=0.92,
        label_flip_probability=0.05,
    ),
}


def profile_config(name: str, **overrides) -> SimulationConfig:
    if name not in PROFILES:
        raise ValueError(f"unknown profile {name!r}; choose from {', '.join(PROFILES)}")
    return replace(SimulationConfig(), **{**PROFILES[name], **overrides})


class _SeparationGrid:
    """Spatial hash for O(1) minimum-separation checks while placing animals."""

    def __init__(self, cell: float) -> None:
        self.cell = max(cell, 1e-6)
        self.cells: dict[tuple[int, int], list[np.ndarray]] = {}

    def _key(self, p: np.ndarray) -> tuple[int, int]:
        return int(p[0] // self.cell), int(p[1] // self.cell)

    def fits(self, p: np.ndarray, min_sep: float) -> bool:
        kx, ky = self._key(p)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for q in self.cells.get((kx + dx, ky + dy), ()):
                    if (p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2 < min_sep * min_sep:
                        return False
        return True

    def add(self, p: np.ndarray) -> None:
        self.cells.setdefault(self._key(p), []).append(p)


def _pen_boxes(cfg: SimulationConfig, rng: np.random.Generator) -> list[tuple[float, float, float, float]]:
    """Non-overlapping pens laid out on a jittered grid with 4 m alleys."""
    cols = max(1, int(np.ceil(np.sqrt(cfg.n_pens))))
    rows = int(np.ceil(cfg.n_pens / cols))
    alley = 4.0
    block_w, block_h = cfg.pen_width_m + alley, cfg.pen_height_m + alley
    x0 = (cfg.field_width_m - cols * block_w) / 2
    y0 = (cfg.field_height_m - rows * block_h) / 2
    if x0 < 0 or y0 < 0:
        raise ValueError("pens do not fit in the field; enlarge the field or use fewer/smaller pens")
    boxes = []
    for k in range(cfg.n_pens):
        r, c = divmod(k, cols)
        x = x0 + c * block_w + rng.uniform(0, alley / 2)
        y = y0 + r * block_h + rng.uniform(0, alley / 2)
        boxes.append((x, y, x + cfg.pen_width_m, y + cfg.pen_height_m))
    return boxes


def _place_animals(cfg: SimulationConfig, rng: np.random.Generator) -> np.ndarray:
    """Animal positions in field-local metres, honouring the minimum separation."""
    grid = _SeparationGrid(cfg.min_separation_m)
    if cfg.layout == "pens":
        boxes = _pen_boxes(cfg, rng)

        def propose() -> np.ndarray:
            x1, y1, x2, y2 = boxes[rng.integers(len(boxes))]
            return np.array([rng.uniform(x1, x2), rng.uniform(y1, y2)])

    elif cfg.layout == "herds":
        margin = cfg.herd_spread_m
        centres = np.column_stack(
            [
                rng.uniform(margin, cfg.field_width_m - margin, cfg.n_herds),
                rng.uniform(margin, cfg.field_height_m - margin, cfg.n_herds),
            ]
        )

        def propose() -> np.ndarray:
            return centres[rng.integers(cfg.n_herds)] + rng.normal(0.0, cfg.herd_spread_m, 2)

    else:
        raise ValueError(f"unknown layout {cfg.layout!r}")

    placed: list[np.ndarray] = []
    attempts = 0
    max_attempts = cfg.n_animals * 500
    while len(placed) < cfg.n_animals:
        attempts += 1
        if attempts > max_attempts:
            raise RuntimeError(
                "could not place animals with the requested separation; "
                "reduce n_animals or min_separation_m"
            )
        p = propose()
        if not (0 <= p[0] <= cfg.field_width_m and 0 <= p[1] <= cfg.field_height_m):
            continue
        if not grid.fits(p, cfg.min_separation_m):
            continue
        grid.add(p)
        placed.append(p)
    return np.asarray(placed)


def _flight_plan(cfg: SimulationConfig, camera: CameraModel) -> list[tuple[np.ndarray, float]]:
    """Boustrophedon waypoints (field-local xy, heading) covering the field."""
    h = cfg.altitude_agl_m
    footprint_across = 2 * h * math.tan(math.radians(camera.hfov_deg) / 2)
    footprint_along = 2 * h * math.tan(math.radians(camera.vfov_deg) / 2)
    strip_spacing = footprint_across * (1 - cfg.side_overlap)
    shot_spacing = footprint_along * (1 - cfg.front_overlap)

    psi = math.radians(cfg.flight_direction_deg)
    along = np.array([math.sin(psi), math.cos(psi)])
    across = np.array([math.cos(psi), -math.sin(psi)])
    centre = np.array([cfg.field_width_m / 2, cfg.field_height_m / 2])

    # Extent of the field along both flight axes, padded by half a footprint.
    corners = np.array(
        [[0, 0], [cfg.field_width_m, 0], [0, cfg.field_height_m], [cfg.field_width_m, cfg.field_height_m]]
    ) - centre
    a_proj, c_proj = corners @ along, corners @ across
    a_min, a_max = a_proj.min() - footprint_along / 2, a_proj.max() + footprint_along / 2
    c_min, c_max = c_proj.min(), c_proj.max()

    n_strips = max(1, math.ceil((c_max - c_min) / strip_spacing) + 1)
    n_shots = max(1, math.ceil((a_max - a_min) / shot_spacing) + 1)
    c_values = c_min + strip_spacing * np.arange(n_strips) - (strip_spacing * (n_strips - 1) - (c_max - c_min)) / 2
    a_values = a_min + shot_spacing * np.arange(n_shots)

    # For oblique imagery the ground footprint centre is ahead of the drone.
    lead = h / math.tan(math.radians(-cfg.gimbal_pitch_deg)) if cfg.gimbal_pitch_deg > -90 else 0.0

    waypoints = []
    for s, c in enumerate(c_values):
        forward = s % 2 == 0
        heading = cfg.flight_direction_deg if forward else cfg.flight_direction_deg + 180.0
        direction = along if forward else -along
        for a in a_values if forward else a_values[::-1]:
            pos = centre + a * along + c * across - lead * direction
            waypoints.append((pos, heading % 360.0))
    return waypoints


def simulate_survey(cfg: SimulationConfig) -> Dataset:
    rng = np.random.default_rng(cfg.seed)
    camera = CameraModel(cfg.image_width, cfg.image_height, cfg.fov_deg, cfg.fov_type)
    utm = LocalUTM(cfg.origin_latitude, cfg.origin_longitude)
    origin_e, origin_n = utm.to_utm(cfg.origin_longitude, cfg.origin_latitude)
    origin = np.array([origin_e, origin_n])

    animals_local = _place_animals(cfg, rng)
    animals = animals_local + origin
    species = [cfg.species[rng.integers(len(cfg.species))] for _ in range(len(animals))]
    other_labels = ["sheep", "goat", "horse", "cattle"]

    frames: list[Frame] = []
    det_counter = 0
    for fi, (pos_local, heading) in enumerate(_flight_plan(cfg, camera)):
        true_e, true_n = pos_local + origin
        true_lon, true_lat = utm.to_geographic(true_e, true_n)
        # The simulator works in grid coordinates; convert to a true heading as
        # a real autopilot would report it.
        convergence = utm.grid_convergence_deg(true_lon, true_lat)
        true_pitch = cfg.gimbal_pitch_deg + rng.normal(0, cfg.pitch_sigma_deg)
        true_roll = rng.normal(0, cfg.roll_sigma_deg)
        rotation = camera_rotation(heading, true_pitch, true_roll)

        pixels, in_front = ground_to_pixel(camera, (true_e, true_n, cfg.altitude_agl_m), rotation, animals)
        visible = np.flatnonzero(in_front & in_image(camera, pixels, cfg.edge_margin_px))

        detections = []
        for idx in visible:
            if rng.random() > cfg.detection_probability:
                continue
            u, v = pixels[idx] + rng.normal(0, cfg.pixel_sigma_px, 2)
            label = species[idx]
            conf = float(np.clip(rng.beta(12, 2), 0.05, 0.999))
            if rng.random() < cfg.label_flip_probability:
                label = rng.choice([s for s in other_labels if s != label])
                conf = float(rng.uniform(0.3, 0.6))
            det_counter += 1
            detections.append(
                Detection(
                    x=float(u),
                    y=float(v),
                    label=str(label),
                    confidence=conf,
                    detection_id=f"D{det_counter:06d}",
                    truth_id=f"T{idx + 1:05d}",
                )
            )
        for _ in range(rng.poisson(cfg.false_positives_per_frame)):
            det_counter += 1
            detections.append(
                Detection(
                    x=float(rng.uniform(0, cfg.image_width)),
                    y=float(rng.uniform(0, cfg.image_height)),
                    label=str(rng.choice(other_labels)),
                    confidence=float(rng.uniform(0.2, 0.5)),
                    detection_id=f"D{det_counter:06d}",
                    truth_id="FP",
                )
            )

        # Recorded telemetry = truth + sensor noise.
        rec_e = true_e + rng.normal(0, cfg.gps_sigma_m)
        rec_n = true_n + rng.normal(0, cfg.gps_sigma_m)
        rec_lon, rec_lat = utm.to_geographic(rec_e, rec_n)
        pose = FramePose(
            latitude=float(rec_lat),
            longitude=float(rec_lon),
            altitude_agl_m=float(cfg.altitude_agl_m + rng.normal(0, cfg.altitude_sigma_m)),
            heading_deg=float((heading - convergence + rng.normal(0, cfg.heading_sigma_deg)) % 360.0),
            gimbal_pitch_deg=float(true_pitch + rng.normal(0, cfg.pitch_sigma_deg)),
            gimbal_roll_deg=0.0,
        )
        frames.append(Frame(f"IMG_{fi + 1:05d}", pose, camera, detections))

    lons, lats = utm.to_geographic(animals[:, 0], animals[:, 1])
    ground_truth = [
        {
            "truth_id": f"T{i + 1:05d}",
            "label": species[i],
            "latitude": float(lats[i]),
            "longitude": float(lons[i]),
        }
        for i in range(len(animals))
    ]
    config_dict = asdict(cfg)
    config_dict["species"] = list(cfg.species)
    return Dataset(frames=frames, ground_truth=ground_truth, metadata={"simulation": config_dict})


def _srt_time(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def export_raw_logs(
    dataset: Dataset,
    out_dir: str | Path,
    include_gimbal_yaw: bool = True,
    frame_interval_s: float = 2.0,
    body_size_m: tuple[float, float] = (2.2, 1.1),
) -> dict[str, Path]:
    """Write a simulated survey as the raw files a pilot would bring home.

    * ``flight.SRT`` - DJI-style subtitle telemetry, one block per capture
    * ``telemetry.csv`` - per-photo metadata (image name, position, gimbal)
    * ``detections.csv`` - AI bounding boxes keyed by image name *and* frame number
    * ``ground_truth.csv`` - true animal positions, for checking results
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = {
        "srt": out / "flight.SRT",
        "telemetry": out / "telemetry.csv",
        "detections": out / "detections.csv",
        "ground_truth": out / "ground_truth.csv",
    }

    blocks = []
    with paths["telemetry"].open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        header = ["image", "latitude", "longitude", "relative_altitude"]
        if include_gimbal_yaw:
            header.append("gimbal_yaw")
        header += ["gimbal_pitch", "gimbal_roll"]
        writer.writerow(header)
        for i, f in enumerate(dataset.frames):
            p = f.pose
            yaw = ((p.heading_deg + 180.0) % 360.0) - 180.0  # DJI reports -180..180
            row = [f"{f.frame_id}.JPG", f"{p.latitude:.8f}", f"{p.longitude:.8f}", f"{p.altitude_agl_m:.2f}"]
            if include_gimbal_yaw:
                row.append(f"{yaw:.2f}")
            row += [f"{p.gimbal_pitch_deg:.2f}", f"{p.gimbal_roll_deg:.2f}"]
            writer.writerow(row)

            t0, t1 = i * frame_interval_s, (i + 1) * frame_interval_s
            gimbal = f" [gb_yaw: {yaw:.1f} gb_pitch: {p.gimbal_pitch_deg:.1f} gb_roll: {p.gimbal_roll_deg:.1f}]"
            blocks.append(
                f"{i + 1}\n{_srt_time(t0)} --> {_srt_time(t1)}\n"
                f'<font size="28">FrameCnt: {i + 1}, DiffTime: {int(frame_interval_s * 1000)}ms\n'
                f"2024-06-01 09:{(i * 2) // 60 % 60:02d}:{(i * 2) % 60:02d}.000\n"
                f"[iso: 100] [shutter: 1/1000.0] [fnum: 2.8] [ev: 0] [focal_len: 24.00] "
                f"[latitude: {p.latitude:.6f}] [longitude: {p.longitude:.6f}] "
                f"[rel_alt: {p.altitude_agl_m:.3f} abs_alt: {p.altitude_agl_m + 112.4:.3f}]"
                f"{gimbal if include_gimbal_yaw else ''} </font>\n"
            )
    paths["srt"].write_text("\n".join(blocks), encoding="utf-8")

    with paths["detections"].open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["image", "frame", "xmin", "ymin", "xmax", "ymax", "label", "confidence"])
        for i, f in enumerate(dataset.frames):
            gsd = f.pose.altitude_agl_m / f.camera.focal_px
            half_w, half_h = body_size_m[0] / gsd / 2, body_size_m[1] / gsd / 2
            for d in f.detections:
                writer.writerow(
                    [
                        f"{f.frame_id}.JPG",
                        i + 1,
                        f"{d.x - half_w:.1f}",
                        f"{d.y - half_h:.1f}",
                        f"{d.x + half_w:.1f}",
                        f"{d.y + half_h:.1f}",
                        d.label,
                        f"{d.confidence:.4f}",
                    ]
                )

    with paths["ground_truth"].open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["truth_id", "label", "latitude", "longitude"])
        for g in dataset.ground_truth or []:
            writer.writerow([g["truth_id"], g["label"], f"{g['latitude']:.8f}", f"{g['longitude']:.8f}"])
    return paths
