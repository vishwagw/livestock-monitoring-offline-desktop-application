"""DJI ``.SRT`` subtitle telemetry parser.

DJI drones write one subtitle block per video frame (or per second on older
models). The payload format differs across firmware, so values are pulled out
with tolerant patterns rather than a fixed layout. Supported variants include::

    # Mavic 3 / Air 2S / Mini (one block per frame)
    <font size="28">FrameCnt: 12, DiffTime: 33ms
    2024-03-08 10:12:34.123
    [iso: 100] [shutter: 1/1000.0] [latitude: -33.868800] [longitude: 151.209300]
    [rel_alt: 60.000 abs_alt: 120.500] [gb_yaw: 12.3 gb_pitch: -90.0 gb_roll: 0.0] </font>

    # Phantom 4 (one block per second)
    HOME(149.0251,-35.2345) 2019.01.01 12:00:00
    GPS(149.0253,-35.2346,19) BAROMETER:60.2

    # Mavic Pro
    F/2.8, SS 206.03, ISO 100, EV 0, GPS (149.0253, -35.2346, 19), D 24.18m, H 6.00m
"""

from __future__ import annotations

import re
from pathlib import Path

from .telemetry import TelemetrySample, TelemetryTrack

_TIME_RE = re.compile(
    r"(\d+):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*(\d+):(\d{2}):(\d{2})[,.](\d{1,3})"
)
_TAG_RE = re.compile(r"<[^>]+>")
_KV_RE = re.compile(r"([A-Za-z_][A-Za-z_\.]*)\s*[:=]\s*(-?\d+(?:\.\d+)?)")
_GPS_RE = re.compile(r"GPS\s*\(\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)(?:\s*,\s*(-?\d+(?:\.\d+)?))?\s*\)")
_HEIGHT_RE = re.compile(r"(?:^|[\s,])H\s+(-?\d+(?:\.\d+)?)\s*m\b")
_DATETIME_RE = re.compile(r"\d{4}[-.]\d{2}[-.]\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?")
_FRAMECNT_RE = re.compile(r"(?:FrameCnt|SrtCnt)\s*[:=]\s*(\d+)", re.IGNORECASE)

ALTITUDE_KEYS = ("rel_alt", "relative_alt", "height", "barometer", "h")
HEADING_KEYS = ("gb_yaw", "gimbal_yaw", "gimbal_heading", "heading", "yaw")
PITCH_KEYS = ("gb_pitch", "gimbal_pitch", "camera_pitch")
ROLL_KEYS = ("gb_roll", "gimbal_roll", "camera_roll")


class SrtError(ValueError):
    pass


def _seconds(h: str, m: str, s: str, ms: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000.0


def _first(values: dict[str, float], keys: tuple[str, ...]) -> float | None:
    for k in keys:
        if k in values:
            return values[k]
    return None


def parse_srt_text(text: str, source: str = "telemetry.srt") -> TelemetryTrack:
    blocks = re.split(r"\r?\n\s*\r?\n", text.replace("﻿", "").strip())
    samples: list[TelemetrySample] = []
    skipped_no_fix = 0
    missing_alt = 0

    for block in blocks:
        time_match = _TIME_RE.search(block)
        if not time_match:
            continue
        g = time_match.groups()
        start = _seconds(*g[:4])
        payload = _TAG_RE.sub(" ", block[time_match.end():])

        values: dict[str, float] = {}
        for key, val in _KV_RE.findall(payload):
            values.setdefault(key.lower(), float(val))

        lat = values.get("latitude", values.get("lat"))
        lon = values.get("longitude", values.get("lon", values.get("lng")))
        gps = _GPS_RE.search(payload)
        if (lat is None or lon is None) and gps:
            a, b = float(gps.group(1)), float(gps.group(2))
            # DJI writes GPS(lon, lat, ...); swap only if that is impossible.
            lon, lat = (a, b) if abs(b) <= 90.0 else (b, a)
        if lat is None or lon is None:
            continue
        if abs(lat) < 1e-9 and abs(lon) < 1e-9:
            skipped_no_fix += 1
            continue

        altitude = _first(values, ALTITUDE_KEYS)
        if altitude is None:
            h = _HEIGHT_RE.search(payload)
            altitude = float(h.group(1)) if h else None
        if altitude is None:
            missing_alt += 1

        frame_cnt = _FRAMECNT_RE.search(payload)
        dt = _DATETIME_RE.search(payload)
        samples.append(
            TelemetrySample(
                latitude=lat,
                longitude=lon,
                altitude_agl_m=altitude,
                heading_deg=_normalise_heading(_first(values, HEADING_KEYS)),
                gimbal_pitch_deg=_first(values, PITCH_KEYS),
                gimbal_roll_deg=_first(values, ROLL_KEYS),
                time_s=start,
                frame_number=int(frame_cnt.group(1)) if frame_cnt else None,
                timestamp=dt.group(0) if dt else None,
            )
        )

    if not samples:
        raise SrtError(f"{source}: no GPS telemetry found in SRT file")
    track = TelemetryTrack(source=source, samples=samples)
    if skipped_no_fix:
        track.warnings.append(f"{track.name}: skipped {skipped_no_fix} blocks without a GPS fix")
    if missing_alt:
        track.warnings.append(
            f"{track.name}: {missing_alt} blocks have no relative altitude; "
            "the default flight altitude will be used for them"
        )
    return track


def _normalise_heading(value: float | None) -> float | None:
    return None if value is None else value % 360.0


def load_srt(path: str | Path) -> TelemetryTrack:
    path = Path(path)
    text = path.read_text(encoding="utf-8", errors="replace")
    return parse_srt_text(text, source=str(path))
