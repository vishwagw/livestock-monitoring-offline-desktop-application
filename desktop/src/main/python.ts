/**
 * Locating a Python interpreter that can import `livestock_engine`.
 */

import { execFile } from 'node:child_process'
import { existsSync } from 'node:fs'
import { join } from 'node:path'

import type { EngineStatus } from '@shared/types'
import { engineEnv, type PythonCommand } from './engine'

export function candidatePythons(options: {
  configured: string | null
  envOverride: string | undefined
  repoRoot: string | null
  platform: NodeJS.Platform
}): PythonCommand[] {
  const out: PythonCommand[] = []
  if (options.configured) out.push({ command: options.configured, args: [] })
  if (options.envOverride) out.push({ command: options.envOverride, args: [] })
  if (options.repoRoot) {
    const venv =
      options.platform === 'win32'
        ? join(options.repoRoot, '.venv', 'Scripts', 'python.exe')
        : join(options.repoRoot, '.venv', 'bin', 'python')
    if (existsSync(venv)) out.push({ command: venv, args: [] })
  }
  if (options.platform === 'win32') {
    out.push({ command: 'py', args: ['-3'] }, { command: 'python', args: [] })
  } else {
    out.push({ command: 'python3', args: [] }, { command: 'python', args: [] })
  }
  return out
}

const PROBE =
  'import sys, livestock_engine, numpy, sklearn, pyproj, scipy; print(livestock_engine.__version__); print(sys.executable)'

export function probePython(python: PythonCommand, engineSrc: string | null): Promise<EngineStatus> {
  return new Promise((resolve) => {
    execFile(
      python.command,
      [...python.args, '-c', PROBE],
      { env: engineEnv(engineSrc), timeout: 30_000, windowsHide: true },
      (error, stdout, stderr) => {
        if (error) {
          const missing = /No module named '([^']+)'/.exec(stderr)?.[1]
          resolve({
            ok: false,
            python: python.command,
            version: null,
            error: missing
              ? `Python found but the '${missing}' package is missing (pip install -e . in the repository)`
              : (error as NodeJS.ErrnoException).code === 'ENOENT'
                ? `'${python.command}' was not found`
                : stderr.trim().split(/\r?\n/).pop() || error.message
          })
          return
        }
        const [version, executable] = stdout.trim().split(/\r?\n/)
        resolve({ ok: true, python: executable || python.command, version: version ?? null, error: null })
      }
    )
  })
}

export async function findPython(
  candidates: PythonCommand[],
  engineSrc: string | null
): Promise<{ command: PythonCommand | null; status: EngineStatus }> {
  let last: EngineStatus = { ok: false, python: null, version: null, error: 'no Python interpreter found' }
  for (const candidate of candidates) {
    const status = await probePython(candidate, engineSrc)
    if (status.ok) return { command: candidate, status }
    // Prefer the most informative failure (a Python with missing packages
    // beats "not found").
    if (!last.python || !/not found/.test(status.error ?? '')) last = status
  }
  return { command: null, status: last }
}
