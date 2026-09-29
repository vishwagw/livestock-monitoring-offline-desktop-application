/**
 * Bridge to the spatial engine (`livestock_engine`).
 *
 * The engine runs as a child process, either the bundled PyInstaller binary
 * (`livestock-engine process ... --progress`) or, in development, Python
 * (`python -m livestock_engine process ...`). It prints one JSON event per
 * line on stdout (progress, done, error), which is relayed to the renderer.
 * Arguments are passed as an array (never through a shell) and only contain
 * validated values and paths from the file registry.
 */

import { spawn, type ChildProcess } from 'node:child_process'
import { delimiter, join } from 'node:path'

import type { IngestedFile, ProgressEvent, RunRequest } from '@shared/types'

export interface EngineCommand {
  kind: 'bundled' | 'python'
  command: string
  /** Arguments placed before the engine sub-command. */
  args: string[]
}

export function bundledEngine(path: string): EngineCommand {
  return { kind: 'bundled', command: path, args: [] }
}

/** `pyArgs` e.g. `['-3']` for the Windows `py` launcher. */
export function pythonEngine(command: string, pyArgs: string[] = []): EngineCommand {
  return { kind: 'python', command, args: [...pyArgs, '-m', 'livestock_engine'] }
}

export interface EngineEvent {
  event: 'progress' | 'done' | 'error'
  stage?: string
  stage_label?: string
  stage_index?: number
  stages?: string[]
  stage_percent?: number
  percent?: number
  message?: string
  counts?: Record<string, number>
  elapsed_s?: number
  report?: string
}

export const ENGINE_OUTPUTS = {
  report: 'report.json',
  csv: 'animals.csv',
  geojson: 'animals.geojson',
  assignments: 'assignments.csv'
} as const

export function buildProcessArgs(
  request: RunRequest,
  resolveFile: (id: string) => IngestedFile,
  runDir: string
): string[] {
  const args = ['process', '--progress']
  if (request.datasetId) {
    args.push('--dataset', resolveFile(request.datasetId).path)
  } else {
    for (const id of request.telemetryIds) args.push('--telemetry', resolveFile(id).path)
    for (const id of request.detectionIds) args.push('--detections', resolveFile(id).path)
    const { camera, flight } = request
    args.push(
      '--image-width', String(camera.imageWidth),
      '--image-height', String(camera.imageHeight),
      '--fov', String(camera.fovDeg),
      '--fov-type', camera.fovType,
      '--default-pitch', String(flight.defaultPitchDeg),
      '--fps', String(flight.videoFps),
      '--bbox-anchor', flight.bboxAnchor,
      '--time-tolerance', String(flight.timeToleranceS)
    )
    if (flight.defaultAltitudeM !== null) args.push('--default-altitude', String(flight.defaultAltitudeM))
    if (!flight.deriveHeading) args.push('--no-derive-heading')
    if (flight.classNames.trim()) args.push('--class-names', flight.classNames.trim())
  }
  const { clustering } = request
  args.push('--eps', String(clustering.epsM))
  if (!clustering.registration) args.push('--no-registration')
  if (!clustering.frameExclusivity) args.push('--no-frame-exclusivity')
  args.push(
    '--report', join(runDir, ENGINE_OUTPUTS.report),
    '-o', join(runDir, ENGINE_OUTPUTS.csv),
    '--geojson', join(runDir, ENGINE_OUTPUTS.geojson),
    '--assignments', join(runDir, ENGINE_OUTPUTS.assignments)
  )
  return args
}

export function parseEngineLine(line: string): EngineEvent | null {
  const text = line.trim()
  if (!text.startsWith('{')) return null
  try {
    const value = JSON.parse(text) as EngineEvent
    if (value && (value.event === 'progress' || value.event === 'done' || value.event === 'error')) return value
  } catch {
    /* not an event line */
  }
  return null
}

export function engineEnv(
  engine: EngineCommand,
  engineSrc: string | null,
  base: NodeJS.ProcessEnv = process.env
): NodeJS.ProcessEnv {
  const env: NodeJS.ProcessEnv = { ...base, PYTHONUNBUFFERED: '1', PYTHONIOENCODING: 'utf-8' }
  if (engine.kind === 'bundled') {
    // A frozen engine must not pick up a system Python's modules.
    delete env.PYTHONPATH
    delete env.PYTHONHOME
  } else if (engineSrc) {
    env.PYTHONPATH = base.PYTHONPATH ? `${engineSrc}${delimiter}${base.PYTHONPATH}` : engineSrc
  }
  return env
}

/** Convert an engine progress line into the IPC event sent to the renderer. */
export function toProgressEvent(runId: string, e: EngineEvent): ProgressEvent {
  const stages = Array.isArray(e.stages) ? e.stages.map(String).slice(0, 20) : []
  const counts: Record<string, number> = {}
  for (const [k, v] of Object.entries(e.counts ?? {})) if (typeof v === 'number' && Number.isFinite(v)) counts[k] = v
  const done = e.event === 'done'
  return {
    runId,
    stage: done ? 'done' : String(e.stage ?? ''),
    stageIndex: done ? stages.length : Number(e.stage_index ?? 0),
    stages,
    stagePercent: done ? 100 : Number(e.stage_percent ?? 0),
    percent: Math.min(100, Math.max(0, Number(e.percent ?? 0))),
    message: String(e.message ?? (done ? 'Done' : '')),
    counts,
    elapsedS: Number(e.elapsed_s ?? 0)
  }
}

/** Split a stream into lines, buffering partial lines between chunks. */
export function lineSplitter(onLine: (line: string) => void): (chunk: Buffer | string) => void {
  let buffer = ''
  return (chunk) => {
    buffer += chunk.toString()
    let idx: number
    while ((idx = buffer.indexOf('\n')) >= 0) {
      onLine(buffer.slice(0, idx).replace(/\r$/, ''))
      buffer = buffer.slice(idx + 1)
    }
  }
}

export interface RunOutcome {
  code: number | null
  cancelled: boolean
  error: string | null
  stderr: string
}

export class EngineRunner {
  private child: ChildProcess | null = null
  private cancelled = false

  get busy(): boolean {
    return this.child !== null
  }

  run(
    engine: EngineCommand,
    args: string[],
    options: { cwd: string; env: NodeJS.ProcessEnv; timeoutMs?: number },
    onEvent: (event: EngineEvent) => void
  ): Promise<RunOutcome> {
    if (this.child) return Promise.reject(new Error('the engine is already running'))
    this.cancelled = false
    return new Promise((resolvePromise) => {
      let stderr = ''
      let lastError: string | null = null
      const child = spawn(engine.command, [...engine.args, ...args], {
        cwd: options.cwd,
        env: options.env,
        shell: false,
        windowsHide: true,
        stdio: ['ignore', 'pipe', 'pipe']
      })
      this.child = child
      const timer = options.timeoutMs
        ? setTimeout(() => {
            lastError = 'processing timed out'
            child.kill()
          }, options.timeoutMs)
        : null

      child.stdout?.on(
        'data',
        lineSplitter((line) => {
          const event = parseEngineLine(line)
          if (!event) return
          if (event.event === 'error') lastError = event.message ?? 'engine error'
          onEvent(event)
        })
      )
      child.stderr?.on('data', (chunk: Buffer) => {
        stderr = (stderr + chunk.toString()).slice(-20_000)
      })
      const finish = (code: number | null, spawnError?: Error): void => {
        if (timer) clearTimeout(timer)
        this.child = null
        resolvePromise({
          code,
          cancelled: this.cancelled,
          error: spawnError ? spawnError.message : lastError,
          stderr
        })
      }
      child.on('error', (err) => finish(null, err))
      child.on('close', (code) => finish(code))
    })
  }

  cancel(): boolean {
    if (!this.child) return false
    this.cancelled = true
    this.child.kill()
    return true
  }
}

/** Last meaningful line of a Python traceback / error output. */
export function summariseStderr(stderr: string): string {
  const lines = stderr.split(/\r?\n/).map((l) => l.trim()).filter(Boolean)
  const last = lines[lines.length - 1] ?? ''
  return last.replace(/^error:\s*/i, '')
}
