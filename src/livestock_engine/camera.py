"""Camera intrinsics and per-frame pose metadata."""

from __future__ import annotations

import math
from dataclasses import dataclass

FOV_TYPES = ("horizontal", "vertical", "diagonal")


@dataclass(frozen=True)
class CameraModel:
    """Ideal pinhole camera with square pixels.

    ``fov_deg`` may be given as the horizontal, vertical or diagonal field of
    view (drone manufacturers usually publish the diagonal one). Pixel
    coordinates use the image convention: origin at the top-left corner,
    ``u`` to the right, ``v`` downwards.
    """

    image_width: int
    image_height: int
    fov_deg: float
    fov_type: str = "horizontal"
    cx: float | None = None
    cy: float | None = None

    def __post_init__(self) -> None:
        if self.image_width <= 0 or self.image_height <= 0:
            raise ValueError("image dimensions must be positive")
        if not 0.0 < self.fov_deg < 180.0:
            raise ValueError(f"fov_deg must be in (0, 180), got {self.fov_deg}")
        if self.fov_type not in FOV_TYPES:
            raise ValueError(f"fov_type must be one of {FOV_TYPES}, got {self.fov_type!r}")

    @property
    def principal_point(self) -> tuple[float, float]:
        cx = self.image_width / 2.0 if self.cx is None else self.cx
        cy = self.image_height / 2.0 if self.cy is None else self.cy
        return cx, cy

    @property
    def focal_px(self) -> float:
        """Focal length expressed in pixels."""
        if self.fov_type == "horizontal":
            half_extent = self.image_width / 2.0
        elif self.fov_type == "vertical":
            half_extent = self.image_height / 2.0
        else:
            half_extent = math.hypot(self.image_width, self.image_height) / 2.0
        return half_extent / math.tan(math.radians(self.fov_deg) / 2.0)

    @property
    def hfov_deg(self) -> float:
        return math.degrees(2.0 * math.atan(self.image_width / 2.0 / self.focal_px))

    @property
    def vfov_deg(self) -> float:
        return math.degrees(2.0 * math.atan(self.image_height / 2.0 / self.focal_px))


@dataclass(frozen=True)
class FramePose:
    """Drone/gimbal state at the moment an image was captured.

    * ``latitude`` / ``longitude``: WGS84 position of the camera, degrees.
    * ``altitude_agl_m``: height of the camera above the (assumed flat) ground.
    * ``heading_deg``: gimbal yaw, clockwise from **true** north.
    * ``gimbal_pitch_deg``: 0 = horizon, -90 = looking straight down (nadir).
    * ``gimbal_roll_deg``: rotation about the optical axis, usually 0 on a
      stabilised gimbal.
    """

    latitude: float
    longitude: float
    altitude_agl_m: float
    heading_deg: float
    gimbal_pitch_deg: float = -90.0
    gimbal_roll_deg: float = 0.0

    def __post_init__(self) -> None:
        if not -90.0 <= self.latitude <= 90.0:
            raise ValueError(f"latitude out of range: {self.latitude}")
        if not -180.0 <= self.longitude <= 180.0:
            raise ValueError(f"longitude out of range: {self.longitude}")
        if self.altitude_agl_m <= 0.0:
            raise ValueError(f"altitude_agl_m must be positive, got {self.altitude_agl_m}")
        if not -180.0 <= self.gimbal_pitch_deg <= 180.0:
            raise ValueError(f"gimbal_pitch_deg out of range: {self.gimbal_pitch_deg}")
