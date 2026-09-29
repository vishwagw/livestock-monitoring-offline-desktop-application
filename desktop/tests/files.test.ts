import { mkdirSync, mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

import { describe, expect, it } from 'vitest'

import { FileRegistry } from '../src/main/files'
import { SettingsStore } from '../src/main/settings'
import { DEFAULT_SETTINGS } from '@shared/types'

describe('FileRegistry', () => {
  it('admits supported files and rejects everything else', async () => {
    const dir = mkdtempSync(join(tmpdir(), 'files-'))
    writeFileSync(join(dir, 'flight.SRT'), '1\n00:00:00,000 --> 00:00:01,000\n[latitude: 1] [longitude: 2]\n')
    writeFileSync(join(dir, 'boxes.csv'), 'image,xmin,ymin,xmax,ymax\na,1,2,3,4\n')
    writeFileSync(join(dir, 'empty.csv'), '')
    writeFileSync(join(dir, 'photo.jpg'), 'x')
    mkdirSync(join(dir, 'folder.csv'))

    const registry = new FileRegistry()
    const result = await registry.admit([
      join(dir, 'flight.SRT'),
      join(dir, 'boxes.csv'),
      join(dir, 'empty.csv'),
      join(dir, 'photo.jpg'),
      join(dir, 'folder.csv'),
      join(dir, 'missing.csv'),
      'relative/boxes.csv'
    ])
    expect(result.files.map((f) => [f.name, f.kind])).toEqual([
      ['flight.SRT', 'telemetry'],
      ['boxes.csv', 'detections']
    ])
    expect(result.rejected.map((r) => r.name)).toEqual(['empty.csv', 'photo.jpg', 'folder.csv', 'missing.csv', 'boxes.csv'])
    expect(result.rejected[0].reason).toMatch(/empty/)

    // Re-adding the same file keeps its id; ids resolve back to paths.
    const again = await registry.admit([join(dir, 'boxes.csv')])
    expect(again.files[0].id).toBe(result.files[1].id)
    expect(registry.get(result.files[0].id).path).toBe(join(dir, 'flight.SRT'))
    registry.forget(result.files[0].id)
    expect(() => registry.get(result.files[0].id)).toThrow(/no longer available/)
  })
})

describe('SettingsStore', () => {
  it('persists updates and recovers from corrupt values', async () => {
    const dir = mkdtempSync(join(tmpdir(), 'settings-'))
    const path = join(dir, 'nested', 'settings.json')
    const store = new SettingsStore(path)
    expect(await store.load()).toEqual(DEFAULT_SETTINGS)
    await store.update({ clustering: { epsM: 1.5, registration: false, frameExclusivity: true } })
    expect((await new SettingsStore(path).load()).clustering.epsM).toBe(1.5)

    writeFileSync(path, JSON.stringify({ camera: { fovDeg: 999 }, clustering: { epsM: 3 }, pythonPath: 42 }))
    const loaded = await new SettingsStore(path).load()
    expect(loaded.camera).toEqual(DEFAULT_SETTINGS.camera)
    expect(loaded.clustering.epsM).toBe(3)
    expect(loaded.pythonPath).toBeNull()
  })
})
