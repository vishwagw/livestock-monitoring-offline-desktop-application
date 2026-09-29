"""WGS84 <-> local UTM conversions built on pyproj."""

from __future__ import annotations

import math
from typing import Iterable

import numpy as np
from pyproj import CRS, Transformer

WGS84 = "EPSG:4326"


def utm_zone(latitude: float, longitude: float) -> int:
    """Return the UTM zone number, honouring the Norway/Svalbard exceptions."""
    lon = ((longitude + 180.0) % 360.0) - 180.0
    zone = int((lon + 180.0) // 6.0) + 1
    zone = min(zone, 60)
    if 56.0 <= latitude < 64.0 and 3.0 <= lon < 12.0:
        return 32
    if 72.0 <= latitude <= 84.0 and lon >= 0.0:
        if lon < 9.0:
            return 31
        if lon < 21.0:
            return 33
        if lon < 33.0:
            return 35
        if lon < 42.0:
            return 37
    return zone


def utm_epsg(latitude: float, longitude: float) -> int:
    """EPSG code of the WGS84 / UTM zone containing the point."""
    base = 32600 if latitude >= 0.0 else 32700
    return base + utm_zone(latitude, longitude)


class LocalUTM:
    """A single UTM projection shared by every frame of a survey.

    Using one zone for the whole dataset keeps all coordinates in the same
    metric frame even if the flight straddles a zone boundary.
    """

    def __init__(self, ref_latitude: float, ref_longitude: float) -> None:
        self.epsg = utm_epsg(ref_latitude, ref_longitude)
        self.crs = CRS.from_epsg(self.epsg)
        self._forward = Transformer.from_crs(WGS84, self.crs, always_xy=True)
        self._inverse = Transformer.from_crs(self.crs, WGS84, always_xy=True)

    @classmethod
    def for_points(cls, latitudes: Iterable[float], longitudes: Iterable[float]) -> "LocalUTM":
        lats = np.asarray(list(latitudes), dtype=float)
        lons = np.asarray(list(longitudes), dtype=float)
        if lats.size == 0:
            raise ValueError("cannot choose a UTM zone without any positions")
        return cls(float(np.median(lats)), float(np.median(lons)))

    def to_utm(self, longitude, latitude):
        """(lon, lat) degrees -> (easting, northing) metres. Accepts scalars or arrays."""
        return self._forward.transform(longitude, latitude)

    def to_geographic(self, easting, northing):
        """(easting, northing) metres -> (lon, lat) degrees. Accepts scalars or arrays."""
        return self._inverse.transform(easting, northing)

    def grid_convergence_deg(self, longitude: float, latitude: float) -> float:
        """Grid azimuth of true north at the given point, in degrees.

        A true bearing ``b`` corresponds to the grid bearing ``b + convergence``.
        Computed numerically so the sign convention cannot be misread.
        """
        e0, n0 = self._forward.transform(longitude, latitude)
        e1, n1 = self._forward.transform(longitude, latitude + 1e-4)
        return math.degrees(math.atan2(e1 - e0, n1 - n0))

    def __repr__(self) -> str:
        return f"LocalUTM(epsg={self.epsg})"
