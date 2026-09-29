"""Telemetry CSV reader (per-photo metadata or time-series flight logs)."""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from .columns import FEET_UNITS, MILLISECOND_UNITS, ColumnMap, normalise_header, parse_float
from .telemetry import TelemetrySample, TelemetryTrack

FRAME_ID = ("image", "image_name", "filename", "file_name", "file", "photo", "frame_id", "source_file")
FRAME_NUMBER = ("frame_number", "frame", "framecnt", "frame_idx", "frame_index")
TIME_S = ("time_s", "time_sec", "seconds", "offset_s", "elapsed_s", "time")
DATETIME = ("datetime", "timestamp", "datetime_utc", "date_time", "utc_time")
LATITUDE = ("latitude", "lat", "gps_latitude")
LONGITUDE = ("longitude", "lon", "lng", "long", "gps_longitude")
# Height above take-off is the best available AGL proxy on flat ground.
ALTITUDE = (
    "altitude_agl_m",
    "altitude_agl",
    "agl",
    "rel_alt",
    "relative_altitude",
    "height_above_takeoff",
    "height",
    "altitude",
    "alt",
)
HEADING = (
    "heading_deg",
    "gimbal_heading",
    "gimbal_yaw",
    "gimbal_yaw_degree",
    "gb_yaw",
    "camera_yaw",
    "heading",
    "yaw",
    "compass_heading",
    "flight_yaw_degree",
)
PITCH = ("gimbal_pitch_deg", "gimbal_pitch", "gimbal_pitch_degree", "gb_pitch", "camera_pitch")
ROLL = ("gimbal_roll_deg", "gimbal_roll", "gimbal_roll_degree", "gb_roll", "camera_roll")


class TelemetryCsvError(ValueError):
    pass


def is_telemetry_header(headers: list[str]) -> bool:
    cols = ColumnMap(headers)
    return cols.has(LATITUDE) and cols.has(LONGITUDE)


def _parse_datetime(text: str) -> datetime | None:
    text = text.strip().replace("Z", "+00:00")
    for candidate in (text, text.replace(" ", "T")):
        try:
            return datetime.fromisoformat(candidate)
        except ValueError:
            continue
    return None


def load_telemetry_csv(path: str | Path) -> TelemetryTrack:
    path = Path(path)
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        headers = reader.fieldnames or []
        cols = ColumnMap(headers)
        lat_col, lon_col = cols.find(LATITUDE), cols.find(LONGITUDE)
        if not lat_col or not lon_col:
            raise TelemetryCsvError(f"{path.name}: telemetry CSV needs latitude and longitude columns")
        alt_col = cols.find(ALTITUDE)
        head_col, pitch_col, roll_col = cols.find(HEADING), cols.find(PITCH), cols.find(ROLL)
        id_col, num_col = cols.find(FRAME_ID), cols.find(FRAME_NUMBER)
        time_col, dt_col = cols.find(TIME_S), cols.find(DATETIME)

        alt_scale = 0.3048 if alt_col and alt_col[1] in FEET_UNITS else 1.0
        time_scale = 0.001 if time_col and time_col[1] in MILLISECOND_UNITS else 1.0

        samples: list[TelemetrySample] = []
        first_dt: datetime | None = None
        skipped = 0
        for row in reader:
            lat, lon = parse_float(row.get(lat_col[0])), parse_float(row.get(lon_col[0]))
            if lat is None or lon is None or (abs(lat) < 1e-9 and abs(lon) < 1e-9):
                skipped += 1
                continue
            alt = parse_float(row.get(alt_col[0])) if alt_col else None
            time_s = parse_float(row.get(time_col[0])) if time_col else None
            timestamp = (row.get(dt_col[0]) or "").strip() or None if dt_col else None
            if time_s is not None:
                time_s *= time_scale
            elif timestamp:
                dt = _parse_datetime(timestamp)
                if dt is not None:
                    first_dt = first_dt or dt
                    time_s = (dt - first_dt).total_seconds()
            number = parse_float(row.get(num_col[0])) if num_col else None
            heading = parse_float(row.get(head_col[0])) if head_col else None
            samples.append(
                TelemetrySample(
                    latitude=lat,
                    longitude=lon,
                    altitude_agl_m=alt * alt_scale if alt is not None else None,
                    heading_deg=heading % 360.0 if heading is not None else None,
                    gimbal_pitch_deg=parse_float(row.get(pitch_col[0])) if pitch_col else None,
                    gimbal_roll_deg=parse_float(row.get(roll_col[0])) if roll_col else None,
                    time_s=time_s,
                    frame_number=int(number) if number is not None else None,
                    frame_id=(row.get(id_col[0]) or "").strip() or None if id_col else None,
                    timestamp=timestamp,
                )
            )

    if not samples:
        raise TelemetryCsvError(f"{path.name}: no rows with a valid position")
    track = TelemetryTrack(source=str(path), samples=samples)
    if skipped:
        track.warnings.append(f"{track.name}: skipped {skipped} rows without a valid position")
    if alt_col is None:
        track.warnings.append(
            f"{track.name}: no altitude column; the default flight altitude will be used"
        )
    elif normalise_header(alt_col[0])[0] in ("altitude", "alt"):
        track.warnings.append(
            f"{track.name}: using column '{alt_col[0]}' as height above ground; "
            "make sure it is relative to take-off, not above sea level"
        )
    return track
