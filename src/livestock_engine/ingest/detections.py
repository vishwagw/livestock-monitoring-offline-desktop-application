"""AI bounding-box log readers.

Supported inputs:

* **CSV** - one row per box. The frame is identified by an image name
  (``image``/``filename``/``frame_id``), a video frame number (``frame``) or a
  time offset (``time_s``). Boxes may be corners (``xmin,ymin,xmax,ymax`` or
  ``x1,y1,x2,y2``), top-left + size (``left,top,width,height``), centre + size
  (``cx,cy,w,h``) or a single point (``x,y``). Coordinates in [0, 1] are
  treated as normalised.
* **JSON** - a list of the same records (or ``{"detections": [...]}``), where
  ``bbox`` may be ``[x1, y1, x2, y2]``; COCO result files (``images`` +
  ``annotations`` with ``[x, y, w, h]`` boxes) are recognised too.
* **YOLO txt** - one file per image, ``class cx cy w h [conf]`` normalised;
  the file stem is the image name.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .columns import ColumnMap, normalise_header, parse_float

FRAME_ID = ("image", "image_name", "image_id", "filename", "file_name", "file", "photo", "frame_id")
FRAME_NUMBER = ("frame_number", "frame", "framecnt", "frame_idx", "frame_index")
TIME_S = ("time_s", "time_sec", "seconds", "timestamp_s", "time")
SOURCE = ("video", "source", "flight", "log", "sequence", "srt")
LABEL = ("label", "class_name", "class", "category", "species", "name")
CLASS_ID = ("class_id", "cls", "category_id")
CONFIDENCE = ("confidence", "conf", "score", "probability", "prob")
BOX_LAYOUTS: tuple[tuple[str, tuple[tuple[str, ...], ...]], ...] = (
    ("xyxy", (("xmin", "x_min", "x1", "left_x"), ("ymin", "y_min", "y1", "top_y"),
              ("xmax", "x_max", "x2", "right_x"), ("ymax", "y_max", "y2", "bottom_y"))),
    # A bare x, y with a size is read as the top-left corner (COCO convention).
    ("xywh", (("left", "bbox_x", "box_x", "x"), ("top", "bbox_y", "box_y", "y"),
              ("width", "bbox_w", "bbox_width", "box_w", "w"), ("height", "bbox_h", "bbox_height", "box_h", "h"))),
    ("cxcywh", (("cx", "x_center", "center_x", "xc"), ("cy", "y_center", "center_y", "yc"),
                ("width", "w", "bbox_w", "box_w"), ("height", "h", "bbox_h", "box_h"))),
    ("point", (("x", "px", "u"), ("y", "py", "v"))),
)


class DetectionLogError(ValueError):
    pass


@dataclass
class RawDetection:
    """A detection before it is tied to a telemetry sample."""

    x: float
    y: float
    label: str
    confidence: float
    frame_id: str | None = None
    frame_number: int | None = None
    time_s: float | None = None
    source: str | None = None
    box: tuple[float, float, float, float] | None = None  # x1, y1, x2, y2 (pixels)
    normalised: bool = False
    detection_id: str | None = None
    origin: str = ""

    def anchor(self, mode: str) -> tuple[float, float]:
        """Ground contact point: box centre, or bottom-centre for oblique views."""
        if mode == "bottom" and self.box is not None:
            x1, _, x2, y2 = self.box
            return (x1 + x2) / 2.0, y2
        return self.x, self.y

    def scaled(self, width: int, height: int) -> "RawDetection":
        if not self.normalised:
            return self
        box = None
        if self.box is not None:
            x1, y1, x2, y2 = self.box
            box = (x1 * width, y1 * height, x2 * width, y2 * height)
        return RawDetection(
            self.x * width, self.y * height, self.label, self.confidence, self.frame_id,
            self.frame_number, self.time_s, self.source, box, False, self.detection_id, self.origin,
        )


def detect_box_layout(headers: list[str]) -> tuple[str, list[str]] | None:
    cols = ColumnMap(headers)
    for layout, fields in BOX_LAYOUTS:
        found = [cols.find(aliases) for aliases in fields]
        if all(found):
            # "w"/"h" alone must not turn a point log into a size log.
            return layout, [f[0] for f in found]
    return None


def is_detection_header(headers: list[str]) -> bool:
    cols = ColumnMap(headers)
    has_frame = cols.has(FRAME_ID) or cols.has(FRAME_NUMBER) or cols.has(TIME_S)
    return has_frame and detect_box_layout(headers) is not None


def _box_from(layout: str, values: list[float]) -> tuple[float, float, tuple | None]:
    if layout == "xyxy":
        x1, y1, x2, y2 = values
    elif layout == "xywh":
        x1, y1, w, h = values
        x2, y2 = x1 + w, y1 + h
    elif layout == "cxcywh":
        cx, cy, w, h = values
        x1, y1, x2, y2 = cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2
    else:
        return values[0], values[1], None
    x1, x2 = min(x1, x2), max(x1, x2)
    y1, y2 = min(y1, y2), max(y1, y2)
    return (x1 + x2) / 2.0, (y1 + y2) / 2.0, (x1, y1, x2, y2)


def _record_to_detection(
    rec: dict[str, Any], cols: ColumnMap, layout: str, box_cols: list[str], origin: str, index: int
) -> RawDetection | None:
    values = [parse_float(rec.get(c)) for c in box_cols]
    if any(v is None for v in values):
        return None
    x, y, box = _box_from(layout, values)

    def get(aliases):
        col = cols.find(aliases)
        return rec.get(col[0]) if col else None

    label = get(LABEL)
    if label is None or str(label).strip() == "":
        cid = get(CLASS_ID)
        label = f"class_{int(float(cid))}" if parse_float(cid) is not None else "animal"
    conf = parse_float(get(CONFIDENCE))
    if conf is not None and conf > 1.0:
        conf = conf / 100.0  # percentages
    number = parse_float(get(FRAME_NUMBER))
    frame_id = get(FRAME_ID)
    return RawDetection(
        x=x,
        y=y,
        label=str(label).strip(),
        confidence=min(max(conf if conf is not None else 1.0, 0.0), 1.0),
        frame_id=_opt_str(frame_id),
        frame_number=int(number) if number is not None else None,
        time_s=parse_float(get(TIME_S)),
        source=_opt_str(get(SOURCE)),
        box=box,
        detection_id=_opt_str(get(("detection_id", "det_id", "id"))),
        origin=f"{origin}:{index}",
    )


def _opt_str(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _mark_normalised(dets: list[RawDetection]) -> list[RawDetection]:
    coords = []
    for d in dets:
        coords.extend([d.x, d.y])
        if d.box:
            coords.extend(d.box)
    if coords and max(coords) <= 1.0 and min(coords) >= 0.0:
        for d in dets:
            d.normalised = True
    return dets


def _from_records(records: list[dict[str, Any]], origin: str) -> list[RawDetection]:
    if not records:
        return []
    headers = sorted({k for r in records for k in r.keys()})
    cols = ColumnMap(headers)
    layout = detect_box_layout(headers)
    if layout is None:
        raise DetectionLogError(f"{origin}: could not find bounding-box columns")
    if not (cols.has(FRAME_ID) or cols.has(FRAME_NUMBER) or cols.has(TIME_S)):
        raise DetectionLogError(f"{origin}: detections need an image name, frame number or time column")
    out = []
    for i, rec in enumerate(records):
        d = _record_to_detection(rec, cols, layout[0], layout[1], origin, i)
        if d is not None:
            out.append(d)
    return _mark_normalised(out)


def load_detection_csv(path: str | Path) -> list[RawDetection]:
    path = Path(path)
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        records = list(csv.DictReader(fh))
    return _from_records(records, path.name)


def _flatten_bbox(rec: dict[str, Any], fmt: str) -> dict[str, Any]:
    bbox = rec.get("bbox") or rec.get("box")
    if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
        rec = dict(rec)
        keys = ("left", "top", "width", "height") if fmt == "xywh" else ("xmin", "ymin", "xmax", "ymax")
        rec.update(dict(zip(keys, bbox)))
        rec.pop("bbox", None)
        rec.pop("box", None)
    elif isinstance(bbox, dict):
        rec = {**{k: v for k, v in rec.items() if k not in ("bbox", "box")}, **bbox}
    return rec


def load_detection_json(path: str | Path) -> list[RawDetection]:
    path = Path(path)
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)

    if isinstance(data, dict) and "annotations" in data:
        # COCO: bbox is [x, y, w, h]; images map ids to file names.
        images = {img["id"]: img.get("file_name", str(img["id"])) for img in data.get("images", [])}
        categories = {c["id"]: c.get("name", str(c["id"])) for c in data.get("categories", [])}
        records = []
        for ann in data["annotations"]:
            rec = _flatten_bbox(ann, "xywh")
            rec["image"] = images.get(ann.get("image_id"), str(ann.get("image_id")))
            rec.pop("image_id", None)
            if "category_id" in ann and ann["category_id"] in categories:
                rec["label"] = categories[ann["category_id"]]
            records.append(rec)
        return _from_records(records, path.name)

    if isinstance(data, dict):
        data = data.get("detections") or data.get("predictions") or data.get("results")
    if not isinstance(data, list):
        raise DetectionLogError(f"{path.name}: expected a list of detections")
    fmt = "xyxy"
    return _from_records([_flatten_bbox(r, fmt) for r in data if isinstance(r, dict)], path.name)


def load_yolo_txt(path: str | Path, class_names: list[str] | None = None) -> list[RawDetection]:
    path = Path(path)
    out = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
        parts = line.split()
        if len(parts) < 5:
            continue
        try:
            cls = int(float(parts[0]))
            cx, cy, w, h = (float(p) for p in parts[1:5])
            conf = float(parts[5]) if len(parts) > 5 else 1.0
        except ValueError as exc:
            raise DetectionLogError(f"{path.name}:{i + 1}: {exc}") from None
        label = class_names[cls] if class_names and 0 <= cls < len(class_names) else f"class_{cls}"
        x, y, box = _box_from("cxcywh", [cx, cy, w, h])
        out.append(
            RawDetection(
                x=x, y=y, label=label, confidence=min(max(conf, 0.0), 1.0),
                frame_id=path.stem, box=box, normalised=True, origin=f"{path.name}:{i + 1}",
            )
        )
    return out


def load_detections(path: str | Path, class_names: list[str] | None = None) -> list[RawDetection]:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return load_detection_csv(path)
    if suffix == ".json":
        return load_detection_json(path)
    if suffix == ".txt":
        return load_yolo_txt(path, class_names)
    raise DetectionLogError(f"{path.name}: unsupported detection log format {suffix!r}")


__all__ = [
    "DetectionLogError",
    "RawDetection",
    "detect_box_layout",
    "is_detection_header",
    "load_detections",
    "normalise_header",
]
