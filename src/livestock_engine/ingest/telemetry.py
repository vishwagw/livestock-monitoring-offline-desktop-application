"""Time/frame indexed flight telemetry with interpolation."""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class TelemetrySample:
    latitude: float
    longitude: float
    altitude_agl_m: float | None = None
    heading_deg: float | None = None
    gimbal_pitch_deg: float | None = None
    gimbal_roll_deg: float | None = None
    time_s: float | None = None
    frame_number: int | None = None
    frame_id: str | None = None
    timestamp: str | None = None
    heading_derived: bool = False


def frame_key(name: str) -> str:
    """Case-insensitive file stem, so ``DJI_0001.JPG`` matches ``dji_0001``."""
    return Path(name.strip()).stem.lower()


def _bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dlon = math.radians(lon2 - lon1)
    x = math.sin(dlon) * math.cos(phi2)
    y = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlon)
    return math.degrees(math.atan2(x, y)) % 360.0


def _distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6_371_008.8
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    a = (
        math.sin((phi2 - phi1) / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    )
    return 2 * r * math.asin(math.sqrt(a))


def _lerp_angle(a: float, b: float, t: float) -> float:
    diff = ((b - a + 180.0) % 360.0) - 180.0
    return (a + diff * t) % 360.0


def _lerp(a: float | None, b: float | None, t: float) -> float | None:
    if a is None or b is None:
        return a if b is None else b if a is None else None
    return a + (b - a) * t


@dataclass
class TelemetryTrack:
    """Telemetry samples from one log file (one flight or one video)."""

    source: str
    samples: list[TelemetrySample]
    warnings: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._index()

    def _index(self) -> None:
        self._by_id = {frame_key(s.frame_id): s for s in self.samples if s.frame_id}
        self._by_number = {s.frame_number: s for s in self.samples if s.frame_number is not None}
        timed = sorted((s for s in self.samples if s.time_s is not None), key=lambda s: s.time_s)
        self._timed = timed
        self._times = [s.time_s for s in timed]

    @property
    def name(self) -> str:
        return Path(self.source).stem

    @property
    def ordered_samples(self) -> list[TelemetrySample]:
        """Samples in time order when every sample is timed, else file order."""
        return self._timed if len(self._timed) == len(self.samples) else self.samples

    @property
    def has_frame_numbers(self) -> bool:
        return bool(self._by_number)

    def by_frame_id(self, frame_id: str) -> TelemetrySample | None:
        return self._by_id.get(frame_key(frame_id))

    def by_frame_number(self, number: int) -> TelemetrySample | None:
        return self._by_number.get(number)

    def at_time(self, t: float, tolerance_s: float = 1.0) -> TelemetrySample | None:
        """Linearly interpolated sample at ``t`` seconds (headings on the circle)."""
        times = self._times
        if not times:
            return None
        i = bisect.bisect_left(times, t)
        if i < len(times) and times[i] == t:
            return self._timed[i]
        if i == 0:
            return self._timed[0] if times[0] - t <= tolerance_s else None
        if i == len(times):
            return self._timed[-1] if t - times[-1] <= tolerance_s else None
        a, b = self._timed[i - 1], self._timed[i]
        span = b.time_s - a.time_s
        if span > 30.0:  # telemetry gap: don't invent positions
            nearest = a if t - a.time_s <= b.time_s - t else b
            return nearest if abs(nearest.time_s - t) <= tolerance_s else None
        f = (t - a.time_s) / span
        heading = None
        if a.heading_deg is not None and b.heading_deg is not None:
            heading = _lerp_angle(a.heading_deg, b.heading_deg, f)
        return TelemetrySample(
            latitude=a.latitude + (b.latitude - a.latitude) * f,
            longitude=a.longitude + (b.longitude - a.longitude) * f,
            altitude_agl_m=_lerp(a.altitude_agl_m, b.altitude_agl_m, f),
            heading_deg=heading,
            gimbal_pitch_deg=_lerp(a.gimbal_pitch_deg, b.gimbal_pitch_deg, f),
            gimbal_roll_deg=_lerp(a.gimbal_roll_deg, b.gimbal_roll_deg, f),
            time_s=t,
            heading_derived=a.heading_derived or b.heading_derived,
        )

    def fill_missing_headings(self, min_baseline_m: float = 2.0, turn_deg: float = 30.0) -> int:
        """Derive heading from the course over ground where the log has none.

        Consumer DJI SRT files often omit gimbal yaw. For survey flights the
        camera faces the direction of travel, so the bearing to a neighbouring
        sample at least ``min_baseline_m`` away is used. At a turn (forward
        and backward bearings disagree) the sample belongs to the leg it is
        consistent with: the last photo of a strip keeps the strip's bearing,
        the first photo of the next strip takes the new one.
        Returns the number of samples filled.
        """
        ordered = self.ordered_samples
        n = len(ordered)

        def neighbour(i: int, step: int) -> int | None:
            s = ordered[i]
            j = i + step
            while 0 <= j < n:
                o = ordered[j]
                if _distance_m(s.latitude, s.longitude, o.latitude, o.longitude) >= min_baseline_m:
                    return j
                j += step
            return None

        def bearing(i: int, j: int) -> float:
            a, b = ordered[i], ordered[j]
            return _bearing_deg(a.latitude, a.longitude, b.latitude, b.longitude)

        def differs(a: float, b: float) -> bool:
            return abs(((a - b + 180.0) % 360.0) - 180.0) > turn_deg

        filled = 0
        for i, s in enumerate(ordered):
            if s.heading_deg is not None:
                continue
            fwd, back = neighbour(i, 1), neighbour(i, -1)
            if fwd is None and back is None:
                continue
            if fwd is None:
                heading = bearing(back, i)
            elif back is None:
                heading = bearing(i, fwd)
            else:
                heading = bearing(i, fwd)
                incoming = bearing(back, i)
                if differs(heading, incoming):
                    before = neighbour(back, -1)
                    if before is not None and not differs(bearing(before, back), incoming):
                        heading = incoming
            s.heading_deg = heading
            s.heading_derived = True
            filled += 1
        if filled:
            self.warnings.append(
                f"{self.name}: heading missing for {filled} samples; derived from the GPS "
                "track (course over ground). Logs with gimbal yaw give better accuracy."
            )
        self._index()
        return filled
