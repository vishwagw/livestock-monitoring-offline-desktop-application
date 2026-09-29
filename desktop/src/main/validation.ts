/**
 * Runtime validation of everything the renderer sends over IPC. The renderer
 * is treated as untrusted: payloads are checked field by field and rebuilt,
 * so unexpected properties never reach the file system or the engine.
 */

import type {
  AppSettings,
  CameraSettings,
  ClusteringSettings,
  ExportKind,
  FileRole,
  FlightSettings,
  RunRequest
} from '@shared/types'

export class ValidationError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'ValidationError'
  }
}

type Obj = Record<string, unknown>

function obj(value: unknown, name: string): Obj {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    throw new ValidationError(`${name} must be an object`)
  }
  return value as Obj
}

function num(value: unknown, name: string, min: number, max: number, integer = false): number {
  if (typeof value !== 'number' || !Number.isFinite(value)) throw new ValidationError(`${name} must be a number`)
  if (value < min || value > max) throw new ValidationError(`${name} must be between ${min} and ${max}`)
  if (integer && !Number.isInteger(value)) throw new ValidationError(`${name} must be a whole number`)
  return value
}

function bool(value: unknown, name: string): boolean {
  if (typeof value !== 'boolean') throw new ValidationError(`${name} must be true or false`)
  return value
}

function oneOf<T extends string>(value: unknown, name: string, options: readonly T[]): T {
  if (typeof value !== 'string' || !options.includes(value as T)) {
    throw new ValidationError(`${name} must be one of ${options.join(', ')}`)
  }
  return value as T
}

const ID_RE = /^[a-f0-9]{16,64}$/

export function fileId(value: unknown, name = 'file id'): string {
  if (typeof value !== 'string' || !ID_RE.test(value)) throw new ValidationError(`invalid ${name}`)
  return value
}

function idList(value: unknown, name: string, max = 500): string[] {
  if (!Array.isArray(value)) throw new ValidationError(`${name} must be a list`)
  if (value.length > max) throw new ValidationError(`${name} has too many entries`)
  return [...new Set(value.map((v) => fileId(v, name)))]
}

export function pathList(value: unknown, max = 500): string[] {
  if (!Array.isArray(value)) throw new ValidationError('paths must be a list')
  if (value.length > max) throw new ValidationError(`at most ${max} files can be added at once`)
  return value.map((p) => {
    if (typeof p !== 'string' || p.length === 0 || p.length > 4096 || p.includes('\0')) {
      throw new ValidationError('invalid file path')
    }
    return p
  })
}

export function camera(value: unknown): CameraSettings {
  const o = obj(value, 'camera')
  return {
    imageWidth: num(o.imageWidth, 'image width', 16, 50_000, true),
    imageHeight: num(o.imageHeight, 'image height', 16, 50_000, true),
    fovDeg: num(o.fovDeg, 'field of view', 1, 179),
    fovType: oneOf(o.fovType, 'FOV type', ['diagonal', 'horizontal', 'vertical'] as const)
  }
}

export function flight(value: unknown): FlightSettings {
  const o = obj(value, 'flight settings')
  const classNames = o.classNames ?? ''
  if (typeof classNames !== 'string' || classNames.length > 2000 || /[\0\n\r]/.test(classNames)) {
    throw new ValidationError('class names must be a single line of text')
  }
  return {
    defaultPitchDeg: num(o.defaultPitchDeg, 'default gimbal pitch', -90, 0),
    defaultAltitudeM:
      o.defaultAltitudeM === null || o.defaultAltitudeM === undefined
        ? null
        : num(o.defaultAltitudeM, 'default altitude', 1, 2000),
    videoFps: num(o.videoFps, 'video frame rate', 1, 240),
    bboxAnchor: oneOf(o.bboxAnchor, 'box anchor', ['center', 'bottom'] as const),
    timeToleranceS: num(o.timeToleranceS, 'time tolerance', 0, 60),
    deriveHeading: bool(o.deriveHeading, 'derive heading'),
    classNames
  }
}

export function clustering(value: unknown): ClusteringSettings {
  const o = obj(value, 'clustering settings')
  return {
    epsM: num(o.epsM, 'cluster radius', 0.1, 50),
    registration: bool(o.registration, 'frame registration'),
    frameExclusivity: bool(o.frameExclusivity, 'frame exclusivity')
  }
}

export function runRequest(value: unknown): RunRequest {
  const o = obj(value, 'run request')
  const datasetId = o.datasetId === null || o.datasetId === undefined ? null : fileId(o.datasetId, 'dataset id')
  const request: RunRequest = {
    telemetryIds: idList(o.telemetryIds, 'telemetry files'),
    detectionIds: idList(o.detectionIds, 'detection files'),
    datasetId,
    camera: camera(o.camera),
    flight: flight(o.flight),
    clustering: clustering(o.clustering)
  }
  if (datasetId === null && (request.telemetryIds.length === 0 || request.detectionIds.length === 0)) {
    throw new ValidationError('add at least one flight log and one detection log (or an engine dataset)')
  }
  return request
}

export function settingsPatch(value: unknown): Partial<AppSettings> {
  const o = obj(value, 'settings')
  const patch: Partial<AppSettings> = {}
  for (const key of Object.keys(o)) {
    switch (key) {
      case 'camera':
        patch.camera = camera(o.camera)
        break
      case 'flight':
        patch.flight = flight(o.flight)
        break
      case 'clustering':
        patch.clustering = clustering(o.clustering)
        break
      default:
        // Paths (python, tile cache) are only ever set through native dialogs.
        throw new ValidationError(`setting '${key}' cannot be changed from the UI`)
    }
  }
  return patch
}

export function fileRole(value: unknown): FileRole {
  return oneOf(value, 'file role', ['telemetry', 'detections', 'dataset'] as const)
}

export function exportKind(value: unknown): ExportKind {
  return oneOf(value, 'export kind', ['csv', 'geojson', 'report', 'assignments'] as const)
}
