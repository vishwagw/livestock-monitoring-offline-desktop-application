#!/usr/bin/env node
/**
 * Pre-download map tiles for a survey area so the desktop app can show a
 * basemap with no internet connection in the field.
 *
 *   npm run seed-tiles -- --bbox 151.205,-33.872,151.214,-33.865 --zoom 14-19 \
 *     --url "https://tiles.example.com/{z}/{x}/{y}.png?key=YOUR_KEY" --out ~/LivestockTiles
 *
 * Writes the standard XYZ layout (<out>/<z>/<x>/<y>.<ext>) that the app's
 * "Map tiles" button accepts. Existing tiles are skipped, so re-running resumes.
 *
 * Only use a tile provider whose terms allow offline caching (your own tile
 * server, or a commercial plan that permits it). The public OpenStreetMap
 * servers forbid bulk downloading and are refused here.
 */

import { mkdir, stat, writeFile } from 'node:fs/promises'
import { dirname, join, resolve } from 'node:path'

const USAGE = `Usage: seed-tiles --bbox minLon,minLat,maxLon,maxLat --zoom MIN-MAX --url TEMPLATE --out DIR
  [--concurrency 4] [--max-tiles 20000] [--user-agent TEXT] [--dry-run]`

export function parseArgs(argv) {
  const opts = { concurrency: 4, maxTiles: 20000, userAgent: 'LivestockCounter-TileSeeder/0.2', dryRun: false }
  for (let i = 0; i < argv.length; i++) {
    const key = argv[i]
    const next = () => {
      const v = argv[++i]
      if (v === undefined) throw new Error(`${key} needs a value`)
      return v
    }
    switch (key) {
      case '--bbox': {
        const parts = next().split(',').map(Number)
        if (parts.length !== 4 || parts.some((n) => !Number.isFinite(n))) throw new Error('--bbox needs 4 numbers')
        const [minLon, minLat, maxLon, maxLat] = parts
        if (minLon >= maxLon || minLat >= maxLat) throw new Error('--bbox must be minLon,minLat,maxLon,maxLat')
        opts.bbox = { minLon, minLat, maxLon, maxLat }
        break
      }
      case '--zoom': {
        const m = /^(\d{1,2})(?:-(\d{1,2}))?$/.exec(next())
        if (!m) throw new Error('--zoom must look like 15 or 14-19')
        opts.minZoom = Number(m[1])
        opts.maxZoom = Number(m[2] ?? m[1])
        if (opts.minZoom > opts.maxZoom || opts.maxZoom > 22) throw new Error('invalid --zoom range')
        break
      }
      case '--url':
        opts.url = next()
        break
      case '--out':
        opts.out = resolve(next())
        break
      case '--concurrency':
        opts.concurrency = Math.max(1, Math.min(8, Number(next()) || 1))
        break
      case '--max-tiles':
        opts.maxTiles = Number(next())
        break
      case '--user-agent':
        opts.userAgent = next()
        break
      case '--dry-run':
        opts.dryRun = true
        break
      case '-h':
      case '--help':
        opts.help = true
        break
      default:
        throw new Error(`unknown option ${key}`)
    }
  }
  if (opts.help) return opts
  for (const k of ['bbox', 'url', 'out']) if (!opts[k]) throw new Error(`--${k} is required`)
  if (opts.minZoom === undefined) throw new Error('--zoom is required')
  if (!/\{z\}/.test(opts.url) || !/\{x\}/.test(opts.url) || !/\{y\}/.test(opts.url)) {
    throw new Error('--url must contain {z}, {x} and {y}')
  }
  if (/(^|\.)tile\.openstreetmap\.org/i.test(new URL(opts.url.replace(/\{[a-z]\}/g, '0')).hostname)) {
    throw new Error('tile.openstreetmap.org forbids bulk downloads; use a provider that permits offline caching')
  }
  return opts
}

export function lonLatToTile(lon, lat, z) {
  const n = 2 ** z
  const clampedLat = Math.max(-85.05112878, Math.min(85.05112878, lat))
  const x = Math.floor(((lon + 180) / 360) * n)
  const rad = (clampedLat * Math.PI) / 180
  const y = Math.floor(((1 - Math.log(Math.tan(rad) + 1 / Math.cos(rad)) / Math.PI) / 2) * n)
  return { x: Math.min(n - 1, Math.max(0, x)), y: Math.min(n - 1, Math.max(0, y)) }
}

export function* tilesFor(bbox, minZoom, maxZoom) {
  for (let z = minZoom; z <= maxZoom; z++) {
    const a = lonLatToTile(bbox.minLon, bbox.maxLat, z)
    const b = lonLatToTile(bbox.maxLon, bbox.minLat, z)
    for (let x = a.x; x <= b.x; x++) for (let y = a.y; y <= b.y; y++) yield { z, x, y }
  }
}

export function countTiles(bbox, minZoom, maxZoom) {
  let n = 0
  for (let z = minZoom; z <= maxZoom; z++) {
    const a = lonLatToTile(bbox.minLon, bbox.maxLat, z)
    const b = lonLatToTile(bbox.maxLon, bbox.minLat, z)
    n += (b.x - a.x + 1) * (b.y - a.y + 1)
  }
  return n
}

const EXT_BY_TYPE = { 'image/png': 'png', 'image/jpeg': 'jpg', 'image/webp': 'webp' }

async function exists(path) {
  try {
    await stat(path)
    return true
  } catch {
    return false
  }
}

async function fetchTile(opts, { z, x, y }) {
  const url = opts.url.replace('{z}', z).replace('{x}', x).replace('{y}', y)
  for (let attempt = 1; attempt <= 3; attempt++) {
    try {
      const res = await fetch(url, { headers: { 'user-agent': opts.userAgent } })
      if (res.status === 404) return 'missing'
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const type = (res.headers.get('content-type') || '').split(';')[0].trim()
      const ext = EXT_BY_TYPE[type] || (url.match(/\.(png|jpe?g|webp)(\?|$)/i)?.[1]?.toLowerCase() ?? 'png')
      const file = join(opts.out, String(z), String(x), `${y}.${ext === 'jpeg' ? 'jpg' : ext}`)
      await mkdir(dirname(file), { recursive: true })
      await writeFile(file, Buffer.from(await res.arrayBuffer()))
      return 'saved'
    } catch (err) {
      if (attempt === 3) throw err
      await new Promise((r) => setTimeout(r, 500 * attempt))
    }
  }
}

async function main() {
  let opts
  try {
    opts = parseArgs(process.argv.slice(2))
  } catch (err) {
    console.error(`error: ${err.message}\n${USAGE}`)
    process.exit(2)
  }
  if (opts.help) {
    console.log(USAGE)
    return
  }
  const total = countTiles(opts.bbox, opts.minZoom, opts.maxZoom)
  console.log(`${total} tiles for zoom ${opts.minZoom}-${opts.maxZoom} → ${opts.out}`)
  if (total > opts.maxTiles) {
    console.error(`error: ${total} tiles exceeds --max-tiles ${opts.maxTiles}; shrink the area or zoom range`)
    process.exit(2)
  }
  if (opts.dryRun) return

  const queue = tilesFor(opts.bbox, opts.minZoom, opts.maxZoom)
  const counts = { saved: 0, skipped: 0, missing: 0, failed: 0 }
  let done = 0
  const worker = async () => {
    for (let next = queue.next(); !next.done; next = queue.next()) {
      const t = next.value
      const already = ['png', 'jpg', 'webp'].map((e) => join(opts.out, String(t.z), String(t.x), `${t.y}.${e}`))
      if ((await Promise.all(already.map(exists))).some(Boolean)) counts.skipped++
      else {
        try {
          counts[await fetchTile(opts, t)]++
        } catch (err) {
          counts.failed++
          console.error(`  ${t.z}/${t.x}/${t.y}: ${err.message}`)
        }
      }
      if (++done % 100 === 0 || done === total) process.stdout.write(`\r  ${done}/${total}`)
    }
  }
  await Promise.all(Array.from({ length: opts.concurrency }, worker))
  console.log(`\nsaved ${counts.saved}, skipped ${counts.skipped} existing, ${counts.missing} not available, ${counts.failed} failed`)
  if (counts.failed) process.exit(1)
}

if (import.meta.url === `file://${process.argv[1]}`) {
  await main()
}
