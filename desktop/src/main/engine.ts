/**
 * Bridge to the Python spatial engine (`livestock_engine`).
 *
 * The engine runs as a child process: `python -m livestock_engine process
 * ... --progress`. It prints one JSON event per line on stdout (progress,
 * done, error), which is relayed to the renderer. Arguments are passed as an
 * array (never through a shell) and only contain validated values and paths
 * from the file registry.
 */

import { spawn, type ChildProcess } from 'node:child_process'
import { delimiter, join } from 'node:path'

import type { IngestedFile, RunRequest } from '@shared/types'

export interface PythonCommand {
  command: string
  /** Leading arguments, e.g. `['-3']` for the Windows `py` launcher. */
  args: string[]
}

export interface EngineEvent {
  event: 'progress' | 'done' | 'error'
  stage?: string
  percent?: number
  message?: string
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
  const args = ['-m', 'livestock_engine', 'process', '--progress']
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

export function engineEnv(engineSrc: string | null, base: NodeJS.ProcessEnv = process.env): NodeJS.ProcessEnv {
  const env: NodeJS.ProcessEnv = { ...base, PYTHONUNBUFFERED: '1', PYTHONIOENCODING: 'utf-8' }
  if (engineSrc) env.PYTHONPATH = base.PYTHONPATH ? `${engineSrc}${delimiter}${base.PYTHONPATH}` : engineSrc
  return env
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
    python: PythonCommand,
    args: string[],
    options: { cwd: string; env: NodeJS.ProcessEnv; timeoutMs?: number },
    onEvent: (event: EngineEvent) => void
  ): Promise<RunOutcome> {
    if (this.child) return Promise.reject(new Error('the engine is already running'))
    this.cancelled = false
    return new Promise((resolvePromise) => {
      let stderr = ''
      let lastError: string | null = null
      const child = spawn(python.command, [...python.args, ...args], {
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
