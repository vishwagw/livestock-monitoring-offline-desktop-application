import { mkdirSync, mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

import { describe, expect, it } from 'vitest'

import { parseTileUrl, readTile, scanTileCache } from '../src/main/tiles'

describe('parseTileUrl', () => {
  it('accepts valid XYZ tile urls', () => {
    expect(parseTileUrl('tiles://cache/15/29000/19000')).toEqual({ z: 15, x: 29000, y: 19000 })
    expect(parseTileUrl('tiles://cache/3/7/7.png')).toEqual({ z: 3, x: 7, y: 7 })
  })

  it.each([
    'tiles://cache/../../etc/passwd',
    'tiles://cache/15/..%2F..%2Fetc/1',
    'tiles://cache/3/8/0', // x out of range for z=3
    'tiles://cache/25/0/0',
    'tiles://other/1/0/0',
    'file:///etc/passwd',
    'tiles://cache/1/0/0.exe',
    'not a url'
  ])('rejects %s', (url) => {
    expect(parseTileUrl(url)).toBeNull()
  })
})

describe('tile cache', () => {
  it('scans zoom levels and reads tiles of any supported format', async () => {
    const dir = mkdtempSync(join(tmpdir(), 'tiles-'))
    mkdirSync(join(dir, '14', '100'), { recursive: true })
    mkdirSync(join(dir, '16', '400'), { recursive: true })
    writeFileSync(join(dir, '16', '400', '300.jpg'), 'jpegdata')
    mkdirSync(join(dir, 'notes'))
    const info = await scanTileCache(dir)
    expect(info).toMatchObject({ enabled: true, minZoom: 14, maxZoom: 16, format: 'jpg' })
    const tile = await readTile(dir, { z: 16, x: 400, y: 300 })
    expect(tile?.type).toBe('image/jpeg')
    expect(tile?.data.toString()).toBe('jpegdata')
    expect(await readTile(dir, { z: 16, x: 400, y: 301 })).toBeNull()
  })

  it('is disabled for missing or empty folders', async () => {
    expect((await scanTileCache(null)).enabled).toBe(false)
    expect((await scanTileCache('/definitely/not/here')).enabled).toBe(false)
    expect((await scanTileCache(mkdtempSync(join(tmpdir(), 'empty-')))).enabled).toBe(false)
  })
})
