"""Weighted, throttled progress reporting for long-running engine jobs.

A job is split into named stages with relative weights. Each stage reports
its own 0-1 fraction; the tracker converts that into an overall percentage
and forwards structured events to a sink (the CLI prints them as JSON lines,
which the desktop app streams into its dashboard).

Events are throttled to ``min_interval_s`` so that per-frame updates on a
large survey do not flood the pipe, but stage transitions and completion are
always delivered.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

Sink = Callable[[dict[str, Any]], None]


@dataclass(frozen=True)
class Stage:
    name: str
    label: str
    weight: float


PROCESS_RAW_STAGES = (
    Stage("read", "Reading flight files", 12),
    Stage("match", "Matching detections to telemetry", 8),
    Stage("georeference", "Georeferencing detections", 15),
    Stage("cluster", "Clustering sightings", 10),
    Stage("align", "Aligning overlapping frames", 45),
    Stage("report", "Building map report", 10),
)

PROCESS_DATASET_STAGES = (
    Stage("read", "Reading dataset", 10),
    Stage("georeference", "Georeferencing detections", 20),
    Stage("cluster", "Clustering sightings", 10),
    Stage("align", "Aligning overlapping frames", 50),
    Stage("report", "Building map report", 10),
)


class StageProgress:
    """Handle for reporting progress within one stage."""

    def __init__(self, tracker: "ProgressTracker", index: int) -> None:
        self._tracker = tracker
        self._index = index

    def update(self, fraction: float, message: str | None = None, **counts: Any) -> None:
        self._tracker._update(self._index, fraction, message, counts)

    def done(self, message: str | None = None, **counts: Any) -> None:
        self._tracker._update(self._index, 1.0, message, counts, force=True)


class ProgressTracker:
    def __init__(
        self,
        stages: tuple[Stage, ...] | list[Stage],
        sink: Sink | None = None,
        min_interval_s: float = 0.1,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not stages:
            raise ValueError("at least one stage is required")
        self.stages = list(stages)
        self._sink = sink
        self._min_interval = min_interval_s
        self._clock = clock
        total = sum(s.weight for s in self.stages)
        self._starts = []
        acc = 0.0
        for s in self.stages:
            self._starts.append(acc / total)
            acc += s.weight
        self._spans = [s.weight / total for s in self.stages]
        self._current = -1
        self._last_emit = float("-inf")
        self._last_percent = 0.0
        self._counts: dict[str, Any] = {}
        self._started = clock()

    @property
    def labels(self) -> list[str]:
        return [s.label for s in self.stages]

    @property
    def counts(self) -> dict[str, Any]:
        return dict(self._counts)

    @property
    def enabled(self) -> bool:
        return self._sink is not None

    def stage(self, name: str, message: str | None = None) -> StageProgress:
        index = next((i for i, s in enumerate(self.stages) if s.name == name), None)
        if index is None:
            raise KeyError(f"unknown stage {name!r}")
        self._current = index
        self._update(index, 0.0, message, {}, force=True)
        return StageProgress(self, index)

    def overall(self, index: int, fraction: float) -> float:
        fraction = min(max(fraction, 0.0), 1.0)
        return 100.0 * (self._starts[index] + self._spans[index] * fraction)

    def _update(self, index: int, fraction: float, message: str | None, counts: dict, force: bool = False) -> None:
        self._counts.update({k: v for k, v in counts.items() if v is not None})
        if self._sink is None:
            return
        now = self._clock()
        if not force and now - self._last_emit < self._min_interval:
            return
        # Never let the bar move backwards, even if a stage is re-entered.
        percent = max(self._last_percent, self.overall(index, fraction))
        stage = self.stages[index]
        self._last_emit = now
        self._last_percent = percent
        self._sink(
            {
                "event": "progress",
                "stage": stage.name,
                "stage_label": stage.label,
                "stage_index": index,
                "stage_count": len(self.stages),
                "stages": self.labels,
                "stage_percent": round(100.0 * min(max(fraction, 0.0), 1.0), 1),
                "percent": round(percent, 1),
                "message": message or stage.label,
                "counts": dict(self._counts),
                "elapsed_s": round(now - self._started, 3),
            }
        )


class NullStage:
    def update(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    def done(self, *_args: Any, **_kwargs: Any) -> None:
        pass


class NullTracker:
    """Drop-in tracker that reports nothing (library use, tests)."""

    enabled = False

    def stage(self, _name: str, _message: str | None = None) -> NullStage:
        return NullStage()

