/**
 * Quick, dependency-free classification of dropped files from their name and
 * first bytes. It only drives the UI; the Python engine does the real parsing
 * and reports precise errors. Column aliases mirror
 * `livestock_engine/ingest/*.py`.
 */

import type { FileKind } from './types'

export const ACCEPTED_EXTENSIONS = ['.srt', '.csv', '.json', '.txt'] as const

const LAT = ['latitude', 'lat', 'gps_latitude']
const LON = ['longitude', 'lon', 'lng', 'long', 'gps_longitude']
const FRAME_KEYS = [
  'image', 'image_name', 'image_id', 'filename', 'file_name', 'file', 'photo', 'frame_id',
  'frame_number', 'frame', 'framecnt', 'frame_idx', 'frame_index', 'time_s', 'time_sec',
  'seconds', 'timestamp_s', 'time'
]
const BOX_SETS: string[][][] = [
  [['xmin', 'x_min', 'x1', 'left_x'], ['ymin', 'y_min', 'y1', 'top_y'], ['xmax', 'x_max', 'x2', 'right_x'], ['ymax', 'y_max', 'y2', 'bottom_y']],
  [['left', 'bbox_x', 'box_x', 'x'], ['top', 'bbox_y', 'box_y', 'y'], ['width', 'bbox_w', 'bbox_width', 'box_w', 'w'], ['height', 'bbox_h', 'bbox_height', 'box_h', 'h']],
  [['cx', 'x_center', 'center_x', 'xc'], ['cy', 'y_center', 'center_y', 'yc'], ['width', 'w', 'bbox_w', 'box_w'], ['height', 'h', 'bbox_h', 'box_h']],
  [['x', 'px', 'u'], ['y', 'py', 'v']]
]

export function extensionOf(name: string): string {
  const dot = name.lastIndexOf('.')
  return dot >= 0 ? name.slice(dot).toLowerCase() : ''
}

/** Same normalisation as `ingest/columns.py`: lower-case, units in brackets dropped. */
export function normaliseHeader(header: string): string {
  let text = header.trim().replace(/^﻿/, '')
  text = text.replace(/\s*[([][^)\]]*[)\]]\s*$/, '')
  return text.trim().toLowerCase().replace(/[\s\-.]+/g, '_').replace(/^_+|_+$/g, '')
}

export function splitCsvLine(line: string): string[] {
  const out: string[] = []
  let cur = ''
  let quoted = false
  for (let i = 0; i < line.length; i++) {
    const c = line[i]
    if (quoted) {
      if (c === '"' && line[i + 1] === '"') {
        cur += '"'
        i++
      } else if (c === '"') quoted = false
      else cur += c
    } else if (c === '"') quoted = true
    else if (c === ',') {
      out.push(cur)
      cur = ''
    } else cur += c
  }
  out.push(cur)
  return out
}

const hasAny = (cols: Set<string>, names: string[]): boolean => names.some((n) => cols.has(n))

export interface Classification {
  kind: FileKind
  detail: string
}

export function classifyCsvHeader(headerLine: string): Classification {
  const cols = new Set(splitCsvLine(headerLine).map(normaliseHeader))
  const hasPosition = hasAny(cols, LAT) && hasAny(cols, LON)
  const box = BOX_SETS.some((set) => set.every((aliases) => hasAny(cols, aliases)))
  const hasFrame = hasAny(cols, FRAME_KEYS)

  if (hasPosition && box && cols.has('image_width') && cols.has('fov_deg')) {
    return { kind: 'dataset', detail: 'Engine dataset CSV (telemetry + detections)' }
  }
  if (hasPosition) return { kind: 'telemetry', detail: 'Telemetry CSV' }
  if (box && hasFrame) return { kind: 'detections', detail: 'Bounding-box CSV' }
  return { kind: 'unknown', detail: 'CSV without position or bounding-box columns' }
}

/**
 * Classify a file from its name and the first few KB of its contents.
 */
export function classifyFile(name: string, head: string): Classification {
  const ext = extensionOf(name)
  const text = head.replace(/^﻿/, '')
  switch (ext) {
    case '.srt':
      if (/-->/.test(text) && /(latitude|GPS\s*\()/i.test(text)) {
        return { kind: 'telemetry', detail: 'DJI SRT subtitle log' }
      }
      return { kind: 'telemetry', detail: 'SRT log (no GPS seen in the first block)' }
    case '.csv': {
      const firstLine = text.split(/\r?\n/, 1)[0] ?? ''
      return classifyCsvHeader(firstLine)
    }
    case '.json':
      if (/"frames"\s*:/.test(text)) return { kind: 'dataset', detail: 'Engine dataset JSON' }
      if (/"annotations"\s*:/.test(text)) return { kind: 'detections', detail: 'COCO detections JSON' }
      if (/"(bbox|box|xmin|x1|cx|detections|predictions)"\s*:/.test(text)) {
        return { kind: 'detections', detail: 'Bounding-box JSON' }
      }
      return { kind: 'unknown', detail: 'JSON without frames or bounding boxes' }
    case '.txt': {
      const line = text.split(/\r?\n/).find((l) => l.trim().length > 0) ?? ''
      if (/^\s*\d+(\s+-?\d*\.?\d+(e-?\d+)?){4,5}\s*$/i.test(line)) {
        return { kind: 'detections', detail: 'YOLO label file' }
      }
      return { kind: 'unknown', detail: 'Text file is not in YOLO format' }
    }
    default:
      return { kind: 'unknown', detail: `Unsupported file type ${ext || '(none)'}` }
  }
}
