import { mkdir, readFile, rename, writeFile } from 'node:fs/promises'
import { dirname } from 'node:path'

import { DEFAULT_SETTINGS, type AppSettings } from '@shared/types'
import * as validate from './validation'

/** Persisted user preferences (JSON in the app's userData folder). */
export class SettingsStore {
  private current: AppSettings = structuredClone(DEFAULT_SETTINGS)

  constructor(private readonly file: string) {}

  async load(): Promise<AppSettings> {
    try {
      const raw = JSON.parse(await readFile(this.file, 'utf8')) as Partial<AppSettings>
      const next = structuredClone(DEFAULT_SETTINGS)
      // Each section is validated independently so one bad value does not
      // throw away the rest of the user's preferences.
      const sections = [
        ['camera', validate.camera],
        ['flight', validate.flight],
        ['clustering', validate.clustering]
      ] as const
      for (const [key, check] of sections) {
        try {
          if (raw[key]) (next as unknown as Record<string, unknown>)[key] = check({ ...DEFAULT_SETTINGS[key], ...raw[key] })
        } catch {
          /* keep default */
        }
      }
      next.pythonPath = typeof raw.pythonPath === 'string' ? raw.pythonPath : null
      next.tileCacheDir = typeof raw.tileCacheDir === 'string' ? raw.tileCacheDir : null
      this.current = next
    } catch {
      this.current = structuredClone(DEFAULT_SETTINGS)
    }
    return this.get()
  }

  get(): AppSettings {
    return structuredClone(this.current)
  }

  async update(patch: Partial<AppSettings>): Promise<AppSettings> {
    this.current = { ...this.current, ...structuredClone(patch) }
    await mkdir(dirname(this.file), { recursive: true })
    const tmp = `${this.file}.tmp`
    await writeFile(tmp, JSON.stringify(this.current, null, 2), 'utf8')
    await rename(tmp, this.file)
    return this.get()
  }
}
