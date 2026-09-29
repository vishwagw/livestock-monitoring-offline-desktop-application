"""Ray casting from image pixels onto the ground plane.

World frame is local ENU in metres: x = easting, y = northing, z = up, with
the ground plane at z = 0 and the camera at z = altitude AGL.

Camera frame follows the computer-vision convention: x = image right,
y = image down, z = optical axis (forward).
"""

from __future__ import annotations

import math

import numpy as np

from .camera import CameraModel

# Rays shallower than this (|dz| of the unit direction) are treated as never
# reaching the ground.
_MIN_DOWNWARD_COMPONENT = 1e-6


def camera_rotation(heading_deg: float, pitch_deg: float, roll_deg: float = 0.0) -> np.ndarray:
    """Rotation matrix mapping camera-frame vectors into ENU world vectors.

    ``heading_deg`` is the grid azimuth of the optical axis (clockwise from
    north), ``pitch_deg`` is 0 at the horizon and -90 at nadir, and
    ``roll_deg`` rotates the image about the optical axis. At nadir the top
    of the image points along ``heading_deg``.
    """
    psi = math.radians(heading_deg)
    theta = math.radians(pitch_deg)

    horizontal_forward = np.array([math.sin(psi), math.cos(psi), 0.0])
    right = np.array([math.cos(psi), -math.sin(psi), 0.0])
    up = np.array([0.0, 0.0, 1.0])

    forward = math.cos(theta) * horizontal_forward + math.sin(theta) * up
    down = np.cross(forward, right)
    rotation = np.column_stack([right, down, forward])

    if roll_deg:
        phi = math.radians(roll_deg)
        c, s = math.cos(phi), math.sin(phi)
        roll = np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])
        rotation = rotation @ roll
    return rotation


def pixel_rays(camera: CameraModel, u, v) -> np.ndarray:
    """Unnormalised camera-frame ray directions for pixel coordinates."""
    u = np.atleast_1d(np.asarray(u, dtype=float))
    v = np.atleast_1d(np.asarray(v, dtype=float))
    cx, cy = camera.principal_point
    f = camera.focal_px
    return np.column_stack([(u - cx) / f, (v - cy) / f, np.ones_like(u)])


def cast_to_ground(
    camera: CameraModel,
    camera_position: tuple[float, float, float],
    rotation: np.ndarray,
    u,
    v,
    max_range_m: float = 1000.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Intersect pixel rays with the ground plane z = 0.

    Returns ``(points, valid)`` where ``points`` is an ``(N, 2)`` array of
    ground (x, y) coordinates and ``valid`` flags rays that hit the ground
    within ``max_range_m`` of the camera. Invalid rows are NaN.
    """
    origin = np.asarray(camera_position, dtype=float)
    directions = pixel_rays(camera, u, v) @ rotation.T
    directions /= np.linalg.norm(directions, axis=1, keepdims=True)

    dz = directions[:, 2]
    valid = dz < -_MIN_DOWNWARD_COMPONENT
    t = np.full(dz.shape, np.nan)
    t[valid] = -origin[2] / dz[valid]
    valid &= t <= max_range_m

    points = origin[:2] + directions[:, :2] * t[:, None]
    points[~valid] = np.nan
    return points, valid


def ground_to_pixel(
    camera: CameraModel,
    camera_position: tuple[float, float, float],
    rotation: np.ndarray,
    points_xy,
    ground_z: float = 0.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Project ground points into the image (inverse of :func:`cast_to_ground`).

    Returns ``(pixels, in_front)`` where ``pixels`` is ``(N, 2)`` of (u, v)
    and ``in_front`` flags points in front of the camera. Visibility within
    the image bounds is left to the caller.
    """
    pts = np.atleast_2d(np.asarray(points_xy, dtype=float))
    world = np.column_stack([pts, np.full(len(pts), ground_z)])
    cam = (world - np.asarray(camera_position, dtype=float)) @ rotation
    in_front = cam[:, 2] > 1e-9
    cx, cy = camera.principal_point
    f = camera.focal_px
    with np.errstate(divide="ignore", invalid="ignore"):
        u = f * cam[:, 0] / cam[:, 2] + cx
        v = f * cam[:, 1] / cam[:, 2] + cy
    pixels = np.column_stack([u, v])
    pixels[~in_front] = np.nan
    return pixels, in_front


def in_image(camera: CameraModel, pixels: np.ndarray, margin_px: float = 0.0) -> np.ndarray:
    u, v = pixels[:, 0], pixels[:, 1]
    with np.errstate(invalid="ignore"):
        return (
            (u >= margin_px)
            & (u < camera.image_width - margin_px)
            & (v >= margin_px)
            & (v < camera.image_height - margin_px)
        )
