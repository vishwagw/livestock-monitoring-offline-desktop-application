import { randomBytes } from 'node:crypto'
import { copyFile, mkdir, readdir, readFile, rm } from 'node:fs/promises'
import { join } from 'node:path'

import { BrowserWindow, dialog, ipcMain, type IpcMainInvokeEvent, type OpenDialogOptions } from 'electron'

import {
  IPC,
  type EngineStatus,
  type ExportKind,
  type FileRole,
  type ProgressEvent,
  type Report,
  type RunResult,
  type TileInfo
} from '@shared/types'
import { buildProcessArgs, engineEnv, ENGINE_OUTPUTS, EngineRunner, summariseStderr, type PythonCommand } from './engine'
import { FileRegistry } from './files'
import { candidatePythons, findPython, probePython } from './python'
import { SettingsStore } from './settings'
import { scanTileCache } from './tiles'
import * as validate from './validation'

const KEEP_RUNS = 5
const RUN_TIMEOUT_MS = 60 * 60 * 1000

export interface IpcContext {
  settings: SettingsStore
  userData: string
  engineSrc: string | null
  repoRoot: string | null
  isTrustedUrl: (url: string) => boolean
  onTileCacheChanged: (info: TileInfo) => void
}

const FILTERS: Record<FileRole, Electron.FileFilter[]> = {
  telemetry: [
    { name: 'Flight logs', extensions: ['srt', 'SRT', 'csv'] },
    { name: 'All files', extensions: ['*'] }
  ],
  detections: [
    { name: 'Detection logs', extensions: ['csv', 'json', 'txt'] },
    { name: 'All files', extensions: ['*'] }
  ],
  dataset: [{ name: 'Engine dataset', extensions: ['json', 'csv'] }]
}

const EXPORTS: Record<ExportKind, { file: string; label: string; ext: string }> = {
  csv: { file: ENGINE_OUTPUTS.csv, label: 'CSV', ext: 'csv' },
  geojson: { file: ENGINE_OUTPUTS.geojson, label: 'GeoJSON', ext: 'geojson' },
  report: { file: ENGINE_OUTPUTS.report, label: 'JSON report', ext: 'json' },
  assignments: { file: ENGINE_OUTPUTS.assignments, label: 'Detection audit CSV', ext: 'csv' }
}

export function registerIpc(ctx: IpcContext): void {
  const registry = new FileRegistry()
  const runner = new EngineRunner()
  let python: PythonCommand | null = null
  let status: EngineStatus | null = null
  let lastRunDir: string | null = null

  /** Every handler checks the calling frame before touching its payload. */
  function handle<A extends unknown[], R>(channel: string, fn: (event: IpcMainInvokeEvent, ...args: A) => Promise<R> | R): void {
    ipcMain.handle(channel, async (event, ...args) => {
      const url = event.senderFrame?.url ?? ''
      if (!ctx.isTrustedUrl(url)) throw new Error('blocked IPC from an untrusted frame')
      return fn(event, ...(args as A))
    })
  }

  async function resolveEngine(force = false): Promise<EngineStatus> {
    if (status && !force) return status
    const settings = ctx.settings.get()
    const found = await findPython(
      candidatePythons({
        configured: settings.pythonPath,
        envOverride: process.env.LIVESTOCK_ENGINE_PYTHON,
        repoRoot: ctx.repoRoot,
        platform: process.platform
      }),
      ctx.engineSrc
    )
    python = found.command
    status = found.status
    return status
  }

  const window = (event: IpcMainInvokeEvent): BrowserWindow | undefined =>
    BrowserWindow.fromWebContents(event.sender) ?? undefined

  handle(IPC.inspectFiles, (_e, paths: unknown) => registry.admit(validate.pathList(paths)))

  handle(IPC.browseFiles, async (event, role: unknown) => {
    const r = validate.fileRole(role)
    const options: OpenDialogOptions = {
      title: r === 'telemetry' ? 'Add flight logs' : r === 'detections' ? 'Add detection logs' : 'Open engine dataset',
      properties: r === 'dataset' ? ['openFile'] : ['openFile', 'multiSelections'],
      filters: FILTERS[r]
    }
    const win = window(event)
    const picked = win ? await dialog.showOpenDialog(win, options) : await dialog.showOpenDialog(options)
    if (picked.canceled) return { files: [], rejected: [] }
    return registry.admit(picked.filePaths)
  })

  handle(IPC.forgetFile, (_e, id: unknown) => registry.forget(validate.fileId(id)))

  handle(IPC.engineStatus, () => resolveEngine(true))

  handle(IPC.choosePython, async (event) => {
    const win = window(event)
    const options: OpenDialogOptions = { title: 'Choose Python interpreter', properties: ['openFile'] }
    const picked = win ? await dialog.showOpenDialog(win, options) : await dialog.showOpenDialog(options)
    if (picked.canceled || !picked.filePaths[0]) return resolveEngine()
    const candidate: PythonCommand = { command: picked.filePaths[0], args: [] }
    const probed = await probePython(candidate, ctx.engineSrc)
    if (probed.ok) {
      await ctx.settings.update({ pythonPath: candidate.command })
      python = candidate
      status = probed
    }
    return probed
  })

  handle(IPC.run, async (event, payload: unknown): Promise<RunResult> => {
    const runId = `${new Date().toISOString().replace(/[:.]/g, '-')}-${randomBytes(3).toString('hex')}`
    let request
    try {
      request = validate.runRequest(payload)
    } catch (err) {
      return { ok: false, runId, error: (err as Error).message }
    }
    if (runner.busy) return { ok: false, runId, error: 'processing is already running' }
    const engine = await resolveEngine(!python)
    if (!engine.ok || !python) {
      return { ok: false, runId, error: `Processing engine unavailable: ${engine.error ?? 'unknown error'}` }
    }

    const runsDir = join(ctx.userData, 'runs')
    const runDir = join(runsDir, runId)
    await mkdir(runDir, { recursive: true })

    let args: string[]
    try {
      args = buildProcessArgs(request, (id) => registry.get(id), runDir)
    } catch (err) {
      return { ok: false, runId, error: (err as Error).message }
    }

    const send = (p: Omit<ProgressEvent, 'runId'>): void => {
      if (!event.sender.isDestroyed()) event.sender.send(IPC.progress, { runId, ...p })
    }
    send({ stage: 'start', percent: 1, message: 'Starting processing engine' })
    const started = Date.now()
    const outcome = await runner.run(
      python,
      args,
      { cwd: runDir, env: engineEnv(ctx.engineSrc), timeoutMs: RUN_TIMEOUT_MS },
      (e) => {
        if (e.event === 'progress' || e.event === 'done') {
          send({ stage: e.stage ?? e.event, percent: e.percent ?? 0, message: e.message ?? '' })
        }
      }
    )

    if (outcome.cancelled) return { ok: false, runId, error: 'Processing cancelled', cancelled: true }
    if (outcome.code !== 0) {
      const detail = outcome.error ?? (summariseStderr(outcome.stderr) || `engine exited with code ${outcome.code}`)
      return { ok: false, runId, error: detail }
    }
    try {
      const report = JSON.parse(await readFile(join(runDir, ENGINE_OUTPUTS.report), 'utf8')) as Report
      lastRunDir = runDir
      void pruneRuns(runsDir, KEEP_RUNS)
      return { ok: true, runId, report, durationMs: Date.now() - started }
    } catch (err) {
      return { ok: false, runId, error: `could not read the engine report: ${(err as Error).message}` }
    }
  })

  handle(IPC.cancel, () => runner.cancel())

  handle(IPC.exportResult, async (event, kind: unknown) => {
    const k = validate.exportKind(kind)
    if (!lastRunDir) return { saved: false, error: 'process a flight first' }
    const spec = EXPORTS[k]
    const stamp = new Date().toISOString().slice(0, 10)
    const options = {
      title: `Export ${spec.label}`,
      defaultPath: `livestock-${k === 'assignments' ? 'detections' : 'animals'}-${stamp}.${spec.ext}`,
      filters: [{ name: spec.label, extensions: [spec.ext] }]
    }
    const win = window(event)
    const target = win ? await dialog.showSaveDialog(win, options) : await dialog.showSaveDialog(options)
    if (target.canceled || !target.filePath) return { saved: false }
    try {
      await copyFile(join(lastRunDir, spec.file), target.filePath)
      return { saved: true, path: target.filePath }
    } catch (err) {
      return { saved: false, error: (err as Error).message }
    }
  })

  handle(IPC.getSettings, () => ctx.settings.get())

  handle(IPC.updateSettings, (_e, patch: unknown) => ctx.settings.update(validate.settingsPatch(patch)))

  handle(IPC.tileInfo, () => scanTileCache(ctx.settings.get().tileCacheDir))

  handle(IPC.chooseTileCache, async (event, clear: unknown) => {
    if (clear === true) {
      await ctx.settings.update({ tileCacheDir: null })
      const info = await scanTileCache(null)
      ctx.onTileCacheChanged(info)
      return info
    }
    const win = window(event)
    const options: OpenDialogOptions = { title: 'Choose offline map tile folder', properties: ['openDirectory'] }
    const picked = win ? await dialog.showOpenDialog(win, options) : await dialog.showOpenDialog(options)
    if (picked.canceled || !picked.filePaths[0]) return scanTileCache(ctx.settings.get().tileCacheDir)
    const info = await scanTileCache(picked.filePaths[0])
    if (info.enabled) {
      await ctx.settings.update({ tileCacheDir: picked.filePaths[0] })
      ctx.onTileCacheChanged(info)
    }
    return info
  })
}

async function pruneRuns(dir: string, keep: number): Promise<void> {
  try {
    const runs = (await readdir(dir)).sort()
    for (const old of runs.slice(0, Math.max(0, runs.length - keep))) {
      await rm(join(dir, old), { recursive: true, force: true })
    }
  } catch {
    /* best effort */
  }
}
