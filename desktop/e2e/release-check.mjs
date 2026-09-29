#!/usr/bin/env node
/**
 * Verify a hardened *release* build (Electron fuses on) the way a field laptop
 * would run it: no Python on PATH, no network, fresh profile.
 *
 *   node e2e/release-check.mjs release            (finds the unpacked app in release/)
 *   node e2e/release-check.mjs "release/linux-unpacked/livestock-counter"
 *   xvfb-run -a node e2e/release-check.mjs squashfs-root/livestock-counter   (extracted AppImage)
 *
 * The fuses disable the Node inspector Playwright normally uses, so this
 * attaches to the renderer through Chromium's DevTools protocol and drives
 * the real preload bridge: file admission -> engine run over IPC -> report.
 */

import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { existsSync, mkdtempSync, readdirSync, readFileSync, statSync } from 'node:fs'
import { createServer } from 'node:net'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { chromium } from 'playwright'

const here = dirname(fileURLToPath(import.meta.url))
const sample = resolve(here, '../../examples/sample-flight')
/** Accept an executable, or a release/ folder containing an unpacked app. */
function locateExecutable(target) {
  const path = resolve(target)
  if (!existsSync(path)) throw new Error(`${path} does not exist`)
  if (!statSync(path).isDirectory() || path.endsWith('.app')) {
    if (path.endsWith('.app')) {
      const macos = join(path, 'Contents', 'MacOS')
      return join(macos, readdirSync(macos)[0])
    }
    return path
  }
  for (const entry of readdirSync(path)) {
    const dir = join(path, entry)
    if (!statSync(dir).isDirectory()) continue
    if (entry === 'win-unpacked') {
      const exe = readdirSync(dir).find((f) => f.endsWith('.exe') && !/uninstall/i.test(f))
      if (exe) return join(dir, exe)
    }
    if (entry === 'linux-unpacked' && existsSync(join(dir, 'livestock-counter'))) return join(dir, 'livestock-counter')
    if (entry.startsWith('mac')) {
      const app = readdirSync(dir).find((f) => f.endsWith('.app'))
      if (app) return locateExecutable(join(dir, app))
    }
  }
  throw new Error(`no unpacked app found in ${path}`)
}

if (!process.argv[2]) {
  console.error('usage: release-check.mjs <app executable | release folder>')
  process.exit(2)
}
const executable = locateExecutable(process.argv[2])
console.log(`checking ${executable}`)

const freePort = () =>
  new Promise((res) => {
    const srv = createServer().listen(0, '127.0.0.1', () => {
      const { port } = srv.address()
      srv.close(() => res(port))
    })
  })

const port = await freePort()
const env = { ...process.env }
env.PATH = process.platform === 'win32' ? `${process.env.SystemRoot}\\System32` : '/nonexistent'
for (const k of ['PYTHONPATH', 'PYTHONHOME', 'LIVESTOCK_ENGINE_PYTHON', 'LIVESTOCK_ENGINE_BIN']) delete env[k]

const app = spawn(
  resolve(executable),
  [`--remote-debugging-port=${port}`, `--user-data-dir=${mkdtempSync(join(tmpdir(), 'livestock-release-'))}`, '--no-sandbox'],
  { env, stdio: ['ignore', 'ignore', 'pipe'] }
)
let stderr = ''
app.stderr.on('data', (d) => (stderr = (stderr + d).slice(-4000)))
const step = (msg) => console.log(`  ✓ ${msg}`)

try {
  let browser
  for (let i = 0; i < 60 && !browser; i++) {
    try {
      browser = await chromium.connectOverCDP(`http://127.0.0.1:${port}`)
    } catch {
      await new Promise((r) => setTimeout(r, 500))
    }
  }
  assert.ok(browser, `could not attach to the app\n${stderr}`)
  let page
  for (let i = 0; i < 40 && !page; i++) {
    page = browser.contexts().flatMap((c) => c.pages()).find((p) => p.url().endsWith('index.html'))
    if (!page) await new Promise((r) => setTimeout(r, 250))
  }
  assert.ok(page, 'app window did not open')

  await page.waitForSelector('.engine--ok, .engine--bad', { timeout: 60_000 })
  const label = await page.textContent('.engine')
  assert.match(label, /built-in/, `expected the bundled engine, got "${label}"`)
  step(`bundled engine running without Python: ${label}`)

  const result = await page.evaluate(async (dir) => {
    const api = window.livestock
    let network = 'blocked'
    try {
      await fetch('https://example.com/')
      network = 'allowed'
    } catch {
      /* expected */
    }
    const admitted = await api.inspectFiles([`${dir}/flight.SRT`, `${dir}/detections.csv`])
    const settings = await api.getSettings()
    const events = []
    const off = api.onProgress((e) => events.push(e.percent))
    const run = await api.run({
      telemetryIds: admitted.files.filter((f) => f.kind === 'telemetry').map((f) => f.id),
      detectionIds: admitted.files.filter((f) => f.kind === 'detections').map((f) => f.id),
      datasetId: null,
      camera: settings.camera,
      flight: settings.flight,
      clustering: settings.clustering
    })
    off()
    return {
      network,
      nodeInPage: typeof process !== 'undefined' || typeof require !== 'undefined',
      ok: run.ok,
      error: run.ok ? null : run.error,
      unique: run.ok ? run.report.summary.unique_animals : null,
      events
    }
  }, sample)

  assert.equal(result.network, 'blocked', 'the release build must not reach the network')
  assert.equal(result.nodeInPage, false, 'Node.js must not be exposed to the page')
  step('offline and sandboxed')
  assert.ok(result.ok, `processing failed: ${result.error}`)
  const truth = readFileSync(join(sample, 'ground_truth.csv'), 'utf8').trim().split('\n').length - 1
  assert.equal(result.unique, truth)
  assert.equal(result.events.at(-1), 100)
  step(`sample flight: ${result.unique} animals (truth ${truth}), ${result.events.length} progress events`)
  await browser.close()
  console.log('release check passed')
} finally {
  app.kill()
}
