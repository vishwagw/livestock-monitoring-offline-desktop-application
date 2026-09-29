"""Synthetic overlapping drone surveys with known ground truth.

A herd is scattered over a field, a lawnmower flight plan with configurable
front/side overlap is flown over it, and every animal visible in a frame
becomes a detection. Telemetry, pixel and detector noise are injected so
the same animal projects to slightly different ground positions from
different frames - exactly the over-counting problem the engine solves.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, replace

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
    return replace(SimulationConfig(), **PROFILES[name], **overrides)


def _place_animals(cfg: SimulationConfig, rng: np.random.Generator) -> np.ndarray:
    """Herd-clustered positions in field-local metres, honouring min separation."""
    margin = cfg.herd_spread_m
    centres = np.column_stack(
        [
            rng.uniform(margin, cfg.field_width_m - margin, cfg.n_herds),
            rng.uniform(margin, cfg.field_height_m - margin, cfg.n_herds),
        ]
    )
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
        c = centres[rng.integers(cfg.n_herds)]
        p = c + rng.normal(0.0, cfg.herd_spread_m, 2)
        if not (0 <= p[0] <= cfg.field_width_m and 0 <= p[1] <= cfg.field_height_m):
            continue
        if placed and np.min(np.linalg.norm(np.asarray(placed) - p, axis=1)) < cfg.min_separation_m:
            continue
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
