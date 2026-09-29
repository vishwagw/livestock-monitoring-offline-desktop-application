"""Header normalisation shared by the CSV readers.

Flight logs come from many tools (DJI SRT exports, AirData, Litchi, custom
scripts), so columns are matched against alias lists after normalising the
header: lower-case, spaces/dashes to underscores, and a trailing unit in
parentheses or brackets split off, e.g. ``"Height Above Takeoff (feet)"`` ->
``("height_above_takeoff", "feet")``.
"""

from __future__ import annotations

import re

_UNIT_RE = re.compile(r"^(?P<name>.*?)\s*[\(\[](?P<unit>[^\)\]]*)[\)\]]\s*$")

FEET_UNITS = {"ft", "feet", "foot"}
MILLISECOND_UNITS = {"ms", "millisecond", "milliseconds"}


def normalise_header(header: str) -> tuple[str, str | None]:
    text = header.strip().lstrip("﻿")
    unit = None
    m = _UNIT_RE.match(text)
    if m:
        text, unit = m.group("name"), m.group("unit").strip().lower() or None
    name = re.sub(r"[\s\-\.]+", "_", text.strip().lower()).strip("_")
    return name, unit


class ColumnMap:
    """Resolves logical fields to the actual CSV headers of one file."""

    def __init__(self, headers: list[str]) -> None:
        self.headers = headers
        self._by_name: dict[str, tuple[str, str | None]] = {}
        for h in headers:
            name, unit = normalise_header(h)
            self._by_name.setdefault(name, (h, unit))

    def find(self, aliases: tuple[str, ...]) -> tuple[str, str | None] | None:
        """First header matching an alias, as ``(original_header, unit)``."""
        for alias in aliases:
            if alias in self._by_name:
                return self._by_name[alias]
        return None

    def has(self, aliases: tuple[str, ...]) -> bool:
        return self.find(aliases) is not None


def parse_float(value) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"nan", "null", "none", "n/a", "-"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None
