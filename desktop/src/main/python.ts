/**
 * Locating the spatial engine.
 *
 * Packaged builds ship a self-contained PyInstaller binary in
 * `resources/engine/`, so field laptops need no Python at all. In
 * development the engine runs from the repository sources with whatever
 * Python is available. Every candidate is verified with
 * `livestock-engine self-check --json`, which imports all native
 * dependencies and exercises PROJ and DBSCAN.
 */

import { execFile } from 'node:child_process'
import { existsSync } from 'node:fs'
import { join } from 'node:path'

import type { EngineStatus } from '@shared/types'
import { bundledEngine, engineEnv, pythonEngine, type EngineCommand } from './engine'

export function bundledEnginePath(dir: string, platform: NodeJS.Platform): string {
  return join(dir, platform === 'win32' ? 'livestock-engine.exe' : 'livestock-engine')
}

export function candidateEngines(options: {
  packaged: boolean
  bundledDir: string | null
  envBinary: string | undefined
  configuredPython: string | null
  envPython: string | undefined
  repoRoot: string | null
  platform: NodeJS.Platform
}): EngineCommand[] {
  const { platform } = options
  const bundled: EngineCommand[] = []
  if (options.envBinary) bundled.push(bundledEngine(options.envBinary))
  if (options.bundledDir) {
    const exe = bundledEnginePath(options.bundledDir, platform)
    if (existsSync(exe)) bundled.push(bundledEngine(exe))
  }

  const pythons: EngineCommand[] = []
  if (options.configuredPython) pythons.push(pythonEngine(options.configuredPython))
  if (options.envPython) pythons.push(pythonEngine(options.envPython))
  if (options.repoRoot) {
    const venv =
      platform === 'win32'
        ? join(options.repoRoot, '.venv', 'Scripts', 'python.exe')
        : join(options.repoRoot, '.venv', 'bin', 'python')
    if (existsSync(venv)) pythons.push(pythonEngine(venv))
  }
  if (platform === 'win32') pythons.push(pythonEngine('py', ['-3']), pythonEngine('python'))
  else pythons.push(pythonEngine('python3'), pythonEngine('python'))

  // Packaged: the bundled binary first; Python is only a fallback. In
  // development the sources win so code edits take effect immediately (set
  // LIVESTOCK_ENGINE_BIN to test a frozen build).
  if (options.envBinary) return [...bundled, ...pythons]
  return options.packaged ? [...bundled, ...pythons] : [...pythons, ...bundled]
}

interface SelfCheck {
  ok: boolean
  version: string
  python: string
  frozen: boolean
}

export function probeEngine(engine: EngineCommand, engineSrc: string | null): Promise<EngineStatus> {
  return new Promise((resolve) => {
    execFile(
      engine.command,
      [...engine.args, 'self-check', '--json'],
      { env: engineEnv(engine, engineSrc), timeout: 60_000, windowsHide: true },
      (error, stdout, stderr) => {
        const base = { kind: engine.kind, command: engine.command }
        let check: SelfCheck | null = null
        try {
          check = JSON.parse(stdout.trim().split(/\r?\n/).pop() ?? '') as SelfCheck
        } catch {
          check = null
        }
        if (!error && check?.ok) {
          resolve({ ...base, ok: true, version: check.version, python: check.python, error: null })
          return
        }
        const missing = /No module named '([^']+)'/.exec(stderr)?.[1]
        const code = (error as NodeJS.ErrnoException | null)?.code
        resolve({
          ...base,
          ok: false,
          version: check?.version ?? null,
          python: check?.python ?? null,
          error: missing
            ? `Python found but the '${missing}' package is missing (pip install -e . in the repository)`
            : code === 'ENOENT'
              ? `'${engine.command}' was not found`
              : /invalid choice: 'self-check'/.test(stderr)
                ? 'this livestock_engine is too old; update it'
                : stderr.trim().split(/\r?\n/).pop() || error?.message || 'engine self-check failed'
        })
      }
    )
  })
}

export async function findEngine(
  candidates: EngineCommand[],
  engineSrc: string | null
): Promise<{ engine: EngineCommand | null; status: EngineStatus }> {
  let best: EngineStatus = {
    ok: false,
    kind: null,
    command: null,
    version: null,
    python: null,
    error: 'no processing engine found'
  }
  for (const candidate of candidates) {
    const status = await probeEngine(candidate, engineSrc)
    if (status.ok) return { engine: candidate, status }
    // Prefer the most informative failure ("missing package" over "not found").
    if (!best.command || !/not found/.test(status.error ?? '')) best = status
  }
  return { engine: null, status: best }
}
