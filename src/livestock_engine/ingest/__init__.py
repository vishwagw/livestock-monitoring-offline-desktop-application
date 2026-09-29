"""Raw flight-log ingestion: DJI SRT / telemetry CSV + AI bounding-box logs."""

from .assemble import IngestError, IngestOptions, IngestReport, build_dataset, ingest_files, load_telemetry
from .detections import DetectionLogError, RawDetection, load_detections
from .srt import SrtError, load_srt, parse_srt_text
from .telemetry import TelemetrySample, TelemetryTrack
from .telemetry_csv import TelemetryCsvError, load_telemetry_csv

INGEST_ERRORS = (IngestError, DetectionLogError, SrtError, TelemetryCsvError)

__all__ = [
    "INGEST_ERRORS",
    "DetectionLogError",
    "IngestError",
    "IngestOptions",
    "IngestReport",
    "RawDetection",
    "SrtError",
    "TelemetryCsvError",
    "TelemetrySample",
    "TelemetryTrack",
    "build_dataset",
    "ingest_files",
    "load_detections",
    "load_srt",
    "load_telemetry",
    "load_telemetry_csv",
    "parse_srt_text",
]
