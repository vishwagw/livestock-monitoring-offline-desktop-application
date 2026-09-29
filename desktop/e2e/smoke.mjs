#!/usr/bin/env node
/**
 * End-to-end smoke test of the packaged-mode app (out/ build) in real Electron:
 * loads the sample flight through the file dialogs, runs the Python engine,
 * checks the headcount, offline tiles, export and the renderer's security
 * posture. Native dialogs are stubbed; everything else is real.
 *
 *   npm run build && npm run test:e2e        (Linux CI: xvfb-run -a npm run test:e2e)
 */

import assert from 'node:assert/strict'
import { mkdirSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { deflateSync } from 'node:zlib'

import { _electron as electron } from 'playwright'

const here = dirname(fileURLToPath(import.meta.url))
const appDir = resolve(here, '..')
const sample = resolve(appDir, '../examples/sample-flight')
const require = createRequire(import.meta.url)

function crc32(buf) {
  let c = ~0
  for (const b of buf) {
    c ^= b
    for (let k = 0; k < 8; k++) c = (c >>> 1) ^ (0xedb88320 & -(c & 1))
  }
  return ~c >>> 0
}

function solidPng(r, g, b) {
  const chunk = (type, data) => {
    const len = Buffer.alloc(4)
    len.writeUInt32BE(data.length)
    const td = Buffer.concat([Buffer.from(type), data])
    const crc = Buffer.alloc(4)
    crc.writeUInt32BE(crc32(td))
    return Buffer.concat([len, td, crc])
  }
  const ihdr = Buffer.alloc(13)
  ihdr.writeUInt32BE(256, 0)
  ihdr.writeUInt32BE(256, 4)
  ihdr.set([8, 2, 0, 0, 0], 8)
  const row = Buffer.concat([Buffer.from([0]), Buffer.alloc(256 * 3).map((_, i) => [r, g, b][i % 3])])
  const raw = Buffer.concat(Array.from({ length: 256 }, () => row))
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk('IHDR', ihdr),
    chunk('IDAT', deflateSync(raw)),
    chunk('IEND', Buffer.alloc(0))
  ])
}

/** A small synthetic tile cache covering the sample flight. */
function makeTileCache(dir) {
  const truth = readFileSync(join(sample, 'ground_truth.csv'), 'utf8').trim().split('\n').slice(1)
  const pts = truth.map((l) => l.split(',').slice(2, 4).map(Number))
  const lat = pts.reduce((s, p) => s + p[0], 0) / pts.length
  const lon = pts.reduce((s, p) => s + p[1], 0) / pts.length
  const png = solidPng(150, 180, 110)
  for (let z = 15; z <= 19; z++) {
    const n = 2 ** z
    const cx = Math.floor(((lon + 180) / 360) * n)
    const rad = (lat * Math.PI) / 180
    const cy = Math.floor(((1 - Math.asinh(Math.tan(rad)) / Math.PI) / 2) * n)
    const r = Math.max(2, 2 ** (z - 16))
    for (let x = cx - r; x <= cx + r; x++) {
      mkdirSync(join(dir, String(z), String(x)), { recursive: true })
      for (let y = cy - r; y <= cy + r; y++) writeFileSync(join(dir, String(z), String(x), `${y}.png`), png)
    }
  }
}

const userData = mkdtempSync(join(tmpdir(), 'livestock-e2e-'))
const tiles = join(userData, 'tiles')
makeTileCache(tiles)

const app = await electron.launch({
  executablePath: require('electron'),
  args: [join(appDir, 'out/main/index.js'), '--no-sandbox', `--user-data-dir=${userData}`],
  cwd: appDir
})
const step = (msg) => console.log(`  ✓ ${msg}`)

try {
  const page = await app.firstWindow()
  await page.waitForSelector('.engine--ok, .engine--bad', { timeout: 60_000 })
  assert.match(await page.textContent('.engine'), /^Engine /, 'the Python engine must be available')
  step('engine detected')

  const security = await page.evaluate(async () => {
    let network = 'blocked'
    try {
      await fetch('https://example.com/')
      network = 'allowed'
    } catch {
      /* expected */
    }
    const forged = await window.livestock.run({
      telemetryIds: ['/etc/passwd'], detectionIds: ['x'], datasetId: null, camera: {}, flight: {}, clustering: {}
    })
    let setting = 'accepted'
    try {
      await window.livestock.updateSettings({ pythonPath: '/tmp/evil' })
    } catch {
      setting = 'rejected'
    }
    return { require: typeof require, process: typeof process, network, forged: forged.ok, setting }
  })
  assert.deepEqual(security, { require: 'undefined', process: 'undefined', network: 'blocked', forged: false, setting: 'rejected' })
  step('renderer is sandboxed, offline, and IPC rejects forged input')

  const stubOpen = (paths) =>
    app.evaluate(({ dialog }, p) => {
      dialog.showOpenDialog = async () => ({ canceled: false, filePaths: p })
    }, paths)
  await stubOpen([join(sample, 'flight.SRT'), join(sample, 'detections.csv')])
  await page.click('.dropzone[data-role="telemetry"]')
  await page.waitForSelector('.filelist__item >> nth=1')
  assert.equal(await page.locator('ul[aria-label="detections files"] li').count(), 1)
  step('files added and auto-routed to the right drop zones')

  await stubOpen([tiles])
  await page.click('text=Map tiles:')
  await page.waitForSelector('text=cached')
  step('offline tile cache selected')

  await page.getByRole('button', { name: /Remove duplicates/ }).click()
  await page.waitForSelector('.success', { timeout: 120_000 })
  const unique = Number((await page.textContent('[data-testid="unique-count"]')).replace(/,/g, ''))
  const truth = readFileSync(join(sample, 'ground_truth.csv'), 'utf8').trim().split('\n').length - 1
  assert.ok(Math.abs(unique - truth) / truth <= 0.01, `headcount ${unique} vs truth ${truth}`)
  step(`headcount ${unique} (truth ${truth})`)

  await page.waitForTimeout(1000)
  const loadedTiles = await page.evaluate(
    () => [...document.querySelectorAll('.offline-tiles img')].filter((i) => i.complete && i.naturalWidth === 256).length
  )
  assert.ok(loadedTiles > 0, 'offline tiles should render')
  step(`${loadedTiles} offline tiles rendered`)

  await page.locator('table.animals tbody tr').first().click()
  await page.waitForSelector('.leaflet-popup')
  step('selecting an animal opens its map popup')

  const target = join(userData, 'export.csv')
  await app.evaluate(({ dialog }, t) => {
    dialog.showSaveDialog = async () => ({ canceled: false, filePath: t })
  }, target)
  await page.getByRole('button', { name: 'CSV', exact: true }).click()
  await page.waitForSelector('text=Saved')
  assert.equal(readFileSync(target, 'utf8').trim().split('\n').length - 1, unique)
  step('CSV export matches the headcount')

  if (process.env.E2E_SCREENSHOT) await page.screenshot({ path: process.env.E2E_SCREENSHOT })
  console.log('e2e smoke test passed')
} finally {
  await app.close()
}
