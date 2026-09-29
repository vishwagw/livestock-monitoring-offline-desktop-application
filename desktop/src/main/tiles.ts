/**
 * Offline map tiles served from a local cache directory.
 *
 * The cache uses the standard XYZ ("slippy map") layout that tile downloaders
 * produce: `<dir>/<z>/<x>/<y>.png|jpg|jpeg|webp`. The renderer requests
 * `tiles://cache/{z}/{x}/{y}` and the main process answers from disk; only
 * integer tile coordinates are accepted, so a request can never escape the
 * cache directory.
 */

import { readdir, readFile } from 'node:fs/promises'
import { join } from 'node:path'

import type { TileInfo } from '@shared/types'

export const TILE_SCHEME = 'tiles'
export const TILE_URL_TEMPLATE = `${TILE_SCHEME}://cache/{z}/{x}/{y}`
const EXTENSIONS: Record<string, string> = {
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.webp': 'image/webp'
}

export interface TileCoord {
  z: number
  x: number
  y: number
}

export function parseTileUrl(url: string): TileCoord | null {
  let parsed: URL
  try {
    parsed = new URL(url)
  } catch {
    return null
  }
  if (parsed.protocol !== `${TILE_SCHEME}:` || parsed.hostname !== 'cache') return null
  const m = /^\/(\d{1,2})\/(\d{1,8})\/(\d{1,8})(?:\.(?:png|jpe?g|webp))?$/.exec(parsed.pathname)
  if (!m) return null
  const [z, x, y] = [Number(m[1]), Number(m[2]), Number(m[3])]
  if (z > 24) return null
  const n = 2 ** z
  if (x >= n || y >= n) return null
  return { z, x, y }
}

export async function readTile(dir: string, coord: TileCoord): Promise<{ data: Buffer; type: string } | null> {
  for (const [ext, type] of Object.entries(EXTENSIONS)) {
    try {
      const data = await readFile(join(dir, String(coord.z), String(coord.x), `${coord.y}${ext}`))
      return { data, type }
    } catch {
      /* try the next extension */
    }
  }
  return null
}

export async function scanTileCache(dir: string | null): Promise<TileInfo> {
  const empty: TileInfo = { enabled: false, path: dir, minZoom: null, maxZoom: null, format: null }
  if (!dir) return empty
  let entries: string[]
  try {
    entries = await readdir(dir)
  } catch {
    return empty
  }
  const zooms = entries.filter((e) => /^\d{1,2}$/.test(e)).map(Number).filter((z) => z <= 24).sort((a, b) => a - b)
  if (zooms.length === 0) return empty

  let format: string | null = null
  try {
    const z = String(zooms[zooms.length - 1])
    const xs = await readdir(join(dir, z))
    for (const x of xs.slice(0, 5)) {
      const ys = await readdir(join(dir, z, x))
      const tile = ys.find((y) => Object.keys(EXTENSIONS).some((ext) => y.toLowerCase().endsWith(ext)))
      if (tile) {
        format = tile.slice(tile.lastIndexOf('.') + 1).toLowerCase()
        break
      }
    }
  } catch {
    /* format stays unknown */
  }
  return { enabled: true, path: dir, minZoom: zooms[0], maxZoom: zooms[zooms.length - 1], format }
}
