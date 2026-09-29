/**
 * Contract shared by the main process, the preload bridge and the renderer.
 * Everything crossing the IPC boundary is plain, structured-clone-safe data.
 */

export const IPC = {
  inspectFiles: 'files:inspect',
  browseFiles: 'files:browse',
  forgetFile: 'files:forget',
  run: 'engine:run',
  cancel: 'engine:cancel',
  progress: 'engine:progress',
  engineStatus: 'engine:status',
  exportResult: 'results:export',
  getSettings: 'settings:get',
  updateSettings: 'settings:update',
  chooseTileCache: 'tiles:choose',
  tileInfo: 'tiles:info',
  choosePython: 'engine:choose-python'
} as const

export type FileKind = 'telemetry' | 'detections' | 'dataset' | 'unknown'
export type FileRole = 'telemetry' | 'detections' | 'dataset'

export interface IngestedFile {
  /** Opaque handle issued by the main process; the renderer never sends raw paths back. */
  id: string
  name: string
  path: string
  sizeBytes: number
  kind: FileKind
  /** Short human-readable reason for the classification, e.g. "DJI SRT subtitle log". */
  detail: string
}

export interface InspectResult {
  files: IngestedFile[]
  rejected: { name: string; reason: string }[]
}

export type FovType = 'diagonal' | 'horizontal' | 'vertical'
export type BboxAnchor = 'center' | 'bottom'

export interface CameraSettings {
  imageWidth: number
  imageHeight: number
  fovDeg: number
  fovType: FovType
}

export interface FlightSettings {
  defaultPitchDeg: number
  defaultAltitudeM: number | null
  videoFps: number
  bboxAnchor: BboxAnchor
  timeToleranceS: number
  deriveHeading: boolean
  classNames: string
}

export interface ClusteringSettings {
  epsM: number
  registration: boolean
  frameExclusivity: boolean
}

export interface RunRequest {
  telemetryIds: string[]
  detectionIds: string[]
  datasetId: string | null
  camera: CameraSettings
  flight: FlightSettings
  clustering: ClusteringSettings
}

export interface ProgressEvent {
  runId: string
  /** Machine name of the current stage, e.g. `align`. */
  stage: string
  stageIndex: number
  /** Human-readable labels of every stage in this job, in order. */
  stages: string[]
  /** Progress within the current stage, 0-100. */
  stagePercent: number
  /** Overall progress, 0-100 (never decreases). */
  percent: number
  message: string
  /** Live counters, e.g. detections matched, frames, animals found so far. */
  counts: Record<string, number>
  elapsedS: number
}

export interface ReportSummary {
  utm_epsg: number
  input_detections: number
  projected_detections: number
  rejected_detections: number
  unique_animals: number
  duplicates_removed: number
  duplicate_detections: number
  frames: number
  label_counts: Record<string, number>
  registration: {
    enabled: boolean
    frames?: number
    mean_shift_m?: number
    max_shift_m?: number
    mean_abs_rotation_deg?: number
  }
}

export interface ReportAnimal {
  id: string
  lat: number
  lon: number
  easting: number
  northing: number
  label: string
  confidence: number
  observations: number
  frames: number
  spread_m: number
  class_conflict: boolean
  label_votes: Record<string, number>
}

export type DetectionStatus = 'kept' | 'duplicate' | 'noise'

export interface ReportDetection {
  id: string
  frame_id: string
  /** Aligned ground position used for clustering. */
  lat: number
  lon: number
  /** Raw ray-cast position before frame alignment. */
  raw_lat: number
  raw_lon: number
  label: string
  confidence: number
  animal_id: string | null
  status: DetectionStatus
}

export interface ReportFrame {
  id: string
  lat: number
  lon: number
  altitude_agl_m: number
  heading_deg: number
  gimbal_pitch_deg: number
  detections: number
  timestamp: string | null
}

export interface FlightPath {
  name: string
  coordinates: [number, number][]
}

export interface Report {
  version: number
  summary: ReportSummary
  bounds: [[number, number], [number, number]] | null
  animals: ReportAnimal[]
  detections: ReportDetection[]
  rejected_detections: string[]
  frames: ReportFrame[]
  flight_paths: FlightPath[]
  warnings: string[]
  evaluation?: Record<string, number>
}

export type RunResult =
  | { ok: true; runId: string; report: Report; durationMs: number }
  | { ok: false; runId: string; error: string; cancelled?: boolean }

export type ExportKind = 'csv' | 'geojson' | 'report' | 'assignments'

export type EngineKind = 'bundled' | 'python'

export interface EngineStatus {
  ok: boolean
  /** `bundled` = the self-contained binary shipped with the app. */
  kind: EngineKind | null
  /** Executable that was probed. */
  command: string | null
  version: string | null
  /** Python version inside the engine (bundled or system). */
  python: string | null
  error: string | null
}

export interface TileInfo {
  enabled: boolean
  path: string | null
  minZoom: number | null
  maxZoom: number | null
  format: string | null
}

export interface AppSettings {
  pythonPath: string | null
  tileCacheDir: string | null
  camera: CameraSettings
  flight: FlightSettings
  clustering: ClusteringSettings
}

export const DEFAULT_SETTINGS: AppSettings = {
  pythonPath: null,
  tileCacheDir: null,
  camera: { imageWidth: 4000, imageHeight: 3000, fovDeg: 84, fovType: 'diagonal' },
  flight: {
    defaultPitchDeg: -90,
    defaultAltitudeM: null,
    videoFps: 30,
    bboxAnchor: 'center',
    timeToleranceS: 0.5,
    deriveHeading: true,
    classNames: ''
  },
  clustering: { epsM: 2, registration: true, frameExclusivity: true }
}

/** The API the preload script exposes on `window.livestock`. */
export interface LivestockApi {
  pathForFile(file: File): string
  inspectFiles(paths: string[]): Promise<InspectResult>
  browseFiles(role: FileRole): Promise<InspectResult>
  forgetFile(id: string): Promise<void>
  run(request: RunRequest): Promise<RunResult>
  cancel(): Promise<boolean>
  onProgress(listener: (event: ProgressEvent) => void): () => void
  engineStatus(): Promise<EngineStatus>
  choosePython(): Promise<EngineStatus>
  exportResult(kind: ExportKind): Promise<{ saved: boolean; path?: string; error?: string }>
  getSettings(): Promise<AppSettings>
  updateSettings(patch: Partial<AppSettings>): Promise<AppSettings>
  chooseTileCache(clear?: boolean): Promise<TileInfo>
  tileInfo(): Promise<TileInfo>
}
