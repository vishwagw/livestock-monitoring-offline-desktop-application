#!/usr/bin/env node
/**
 * Record a captioned product video of the real desktop app.
 *
 *   python demo/video/make_survey.py demo/video/data     # 55k-detection station survey (raw logs)
 *   cd desktop && LIVESTOCK_TEST_BUILD=1 npx electron-builder --config electron-builder.config.cjs --dir \
 *        -c.directories.output=release-demo
 *   xvfb-run -a -s "-screen 0 1600x1000x24" node ../demo/video/record.mjs release-demo/linux-unpacked/livestock-counter \
 *        ../demo/video/data ../demo/video/out
 *
 * Drives the packaged app with its bundled engine (Python is removed from
 * PATH): adds the flight log and detections, runs the count with live
 * progress, explores the map, opens an animal and exports a CSV. Native file
 * dialogs are answered automatically; everything on screen is the real app.
 * Captions and the cursor are drawn into the window because screen
 * recordings show neither. Writes <out>/livestock-counter-demo.webm.
 */

import { mkdirSync, mkdtempSync, readdirSync, renameSync, writeFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'

const require = createRequire(resolve('desktop/package.json'))
let electron
try {
  ;({ _electron: electron } = require('playwright'))
} catch {
  ;({ _electron: electron } = await import('playwright'))
}

const [appPath, dataDir, outDir] = process.argv.slice(2).map((p) => p && resolve(p))
if (!appPath || !dataDir || !outDir) {
  console.error('usage: record.mjs <packaged app executable> <survey data dir> <output dir>')
  process.exit(2)
}
mkdirSync(outDir, { recursive: true })
const rawDir = mkdtempSync(join(tmpdir(), 'lc-video-'))
const userData = mkdtempSync(join(tmpdir(), 'lc-video-profile-'))
const W = 1440
const H = 900

const launchedAt = Date.now()
const app = await electron.launch({
  executablePath: appPath,
  args: ['--no-sandbox', `--user-data-dir=${userData}`],
  env: { ...process.env, PATH: '/nonexistent' },
  recordVideo: { dir: rawDir, size: { width: W, height: H } }
})
const page = await app.firstWindow()
await app.evaluate(({ BrowserWindow }, [w, h]) => BrowserWindow.getAllWindows()[0].setContentSize(w, h), [W, H])
await page.waitForSelector('.engine--ok', { timeout: 60_000 })

// ---------- Overlay helpers (drawn into the page, recorded with it) ----------
await page.evaluate(() => {
  const style = document.createElement('style')
  style.textContent = `
    #demo-cursor { position: fixed; z-index: 100000; left: 0; top: 0; width: 22px; height: 22px; pointer-events: none;
      transition: transform 0.7s cubic-bezier(.4,0,.2,1); filter: drop-shadow(0 2px 3px rgba(0,0,0,.35)); }
    #demo-cursor.click::after { content: ''; position: absolute; left: -9px; top: -9px; width: 20px; height: 20px; border-radius: 50%;
      border: 2px solid #e8893f; animation: ring .45s ease-out forwards; }
    @keyframes ring { from { transform: scale(.4); opacity: 1 } to { transform: scale(1.8); opacity: 0 } }
    #demo-caption { position: fixed; z-index: 99999; left: 50%; bottom: 34px; transform: translateX(-50%); max-width: 900px;
      background: rgba(16, 22, 18, .9); color: #f2f6f3; padding: 14px 22px; border-radius: 12px; pointer-events: none;
      font: 600 21px/1.35 system-ui, sans-serif; text-align: center; transition: opacity .35s; box-shadow: 0 10px 30px rgba(0,0,0,.25); }
    #demo-caption small { display: block; font-weight: 400; font-size: 15px; opacity: .8; margin-top: 4px; }
    #demo-card { position: fixed; inset: 0; z-index: 100001; display: grid; place-content: center; gap: 14px; text-align: center;
      background: #101612; color: #f2f6f3; font: 400 22px/1.4 system-ui, sans-serif; transition: opacity .6s; }
    #demo-card h1 { font: 700 54px/1.05 system-ui, sans-serif; margin: 0; letter-spacing: -.01em; }
    #demo-card .g { color: #48c47c } #demo-card .r { color: #f0695e } #demo-card p { margin: 0; opacity: .85 }`
  document.head.appendChild(style)
  const cursor = document.createElement('div')
  cursor.id = 'demo-cursor'
  cursor.innerHTML =
    '<svg viewBox="0 0 22 22" width="22" height="22"><path d="M3 2 L3 18 L7.5 13.8 L10.6 20.5 L13.4 19.2 L10.4 12.6 L16.5 12.6 Z" fill="#fff" stroke="#111" stroke-width="1.4" stroke-linejoin="round"/></svg>'
  cursor.style.transform = 'translate(720px, 520px)'
  document.body.appendChild(cursor)
  const caption = document.createElement('div')
  caption.id = 'demo-caption'
  caption.style.opacity = '0'
  document.body.appendChild(caption)
})

const wait = (ms) => page.waitForTimeout(ms)
let cursorAt = { x: 720, y: 520 }

async function caption(text, sub = '') {
  await page.evaluate(
    ([t, s]) => {
      const c = document.getElementById('demo-caption')
      c.innerHTML = s ? `${t}<small>${s}</small>` : t
      c.style.opacity = t ? '1' : '0'
    },
    [text, sub]
  )
}

async function moveTo(x, y) {
  await page.evaluate(([px, py]) => {
    document.getElementById('demo-cursor').style.transform = `translate(${px}px, ${py}px)`
  }, [x, y])
  await page.mouse.move(x, y, { steps: 12 })
  cursorAt = { x, y }
  await wait(750)
}

async function clickAt(x, y) {
  await moveTo(x, y)
  await page.evaluate(() => {
    const c = document.getElementById('demo-cursor')
    c.classList.remove('click')
    void c.offsetWidth
    c.classList.add('click')
  })
  await page.mouse.click(x, y)
}

async function clickOn(selector) {
  const target = page.locator(selector).first()
  // Scroll it into view smoothly, the way a person would, before clicking.
  await target.evaluate((el) => el.scrollIntoView({ behavior: 'smooth', block: 'center' }))
  await wait(700)
  const box = await target.boundingBox()
  await clickAt(box.x + box.width / 2, box.y + box.height / 2)
}

async function card(html, ms) {
  await page.evaluate((h) => {
    const c = document.createElement('div')
    c.id = 'demo-card'
    c.innerHTML = h
    document.body.appendChild(c)
  }, html)
  await wait(ms)
  await page.evaluate(() => {
    const c = document.getElementById('demo-card')
    c.style.opacity = '0'
    setTimeout(() => c.remove(), 650)
  })
  await wait(700)
}

// ---------- Script ----------
// Seconds of recording before the title card (trimmed when converting to MP4).
const introAt = (Date.now() - launchedAt) / 1000
const onFailure = async (err) => {
  console.error(err)
  try {
    await page.screenshot({ path: join(outDir, 'failure.png') })
    console.error(`state at failure: ${join(outDir, 'failure.png')}`)
  } catch {
    /* window already gone */
  }
  process.exit(1)
}
process.on('unhandledRejection', onFailure)
process.on('uncaughtException', onFailure)
await card(
  '<h1>Livestock Counter</h1><p>Offline headcounts from overlapping drone surveys</p>',
  2600
)

await caption('A station survey: 4,838 drone images', 'Overlapping flight lines photograph each animal several times')
await wait(2400)

await app.evaluate(({ dialog }, files) => {
  dialog.showOpenDialog = async () => ({ canceled: false, filePaths: files })
}, [join(dataDir, 'flight.SRT'), join(dataDir, 'detections.csv')])
await caption('Add the DJI flight log and the detector’s bounding boxes', 'SRT or telemetry CSV · YOLO, COCO, CSV or JSON boxes')
await clickOn('.dropzone[data-role="telemetry"]')
await page.waitForSelector('.filelist__item >> nth=1')
await wait(2200)

await caption('Pick the drone camera, then count', 'Camera presets for common DJI models; everything else is read from the logs')
const cameraBox = await page.locator('.settings select').first().boundingBox()
await moveTo(cameraBox.x + cameraBox.width * 0.7, cameraBox.y + cameraBox.height / 2)
await wait(1500)

await caption('Every stage streams live progress', 'Reading → matching → georeferencing → clustering → frame alignment → map report')
await clickOn('button.button--primary')
await page.waitForSelector('.progress-panel .stages li', { timeout: 60_000 })
await page.waitForSelector('.success', { timeout: 180_000 })
await wait(400)

const raw = (await page.textContent('.stat:nth-child(3) .stat__value')).trim()
const unique = (await page.textContent('[data-testid="unique-count"]')).trim()
const dupes = (await page.textContent('[data-testid="duplicate-count"]')).trim()
await caption(`${raw} detections → ${unique} animals`, `${dupes} repeat sightings removed · processed offline, no internet`)
await wait(3600)

// Zoom into the densest part of the map with the scroll wheel.
const target = await page.evaluate(() => {
  const c = [...document.querySelectorAll('canvas.point-layer')].find((el) => el.parentElement.className.includes('lc-animals'))
  const ctx = c.getContext('2d')
  const { width, height } = c
  const data = ctx.getImageData(0, 0, width, height).data
  const r = c.getBoundingClientRect()
  const dpr = width / r.width
  const cell = 40
  let best = null
  let bestN = 0
  for (let y = 0; y < height; y += cell) {
    for (let x = 0; x < width; x += cell) {
      let n = 0
      for (let yy = y; yy < Math.min(height, y + cell); yy += 3) {
        for (let xx = x; xx < Math.min(width, x + cell); xx += 3) {
          const i = (yy * width + xx) * 4
          if (data[i + 1] > 130 && data[i] < 90 && data[i + 3] > 200) n++
        }
      }
      if (n > bestN && x > width * 0.15 && x < width * 0.8 && y > height * 0.1 && y < height * 0.85) {
        bestN = n
        best = { x: r.left + (x + cell / 2) / dpr, y: r.top + (y + cell / 2) / dpr }
      }
    }
  }
  return best
})
await caption('Each green point is one animal', 'Zoom in: the red points around it are the repeat sightings that were merged')
await moveTo(target.x, target.y)
// Three zoom levels: from the whole station down to a few herds.
for (let i = 0; i < 3; i++) {
  await page.mouse.wheel(0, -60)
  await wait(420)
}
await wait(1400)

// Click an animal near the centre of the view.
const animal = await page.evaluate(() => {
  const c = [...document.querySelectorAll('canvas.point-layer')].find((el) => el.parentElement.className.includes('lc-animals'))
  const ctx = c.getContext('2d')
  const { width, height } = c
  const data = ctx.getImageData(0, 0, width, height).data
  const r = c.getBoundingClientRect()
  const dpr = width / r.width
  const cx = width / 2
  const cy = height / 2
  let best = null
  let bestD = Infinity
  for (let y = 0; y < height; y += 2) {
    for (let x = 0; x < width; x += 2) {
      const i = (y * width + x) * 4
      if (data[i + 1] > 130 && data[i] < 90 && data[i + 3] > 220) {
        const d = (x - cx) ** 2 + (y - cy) ** 2
        if (d < bestD) {
          bestD = d
          best = { x: r.left + x / dpr, y: r.top + y / dpr }
        }
      }
    }
  }
  return best
})
await caption('Every count is traceable', 'Select an animal to see each detection that was merged into it, and the image it came from')
await clickAt(animal.x, animal.y)
await page.waitForSelector('.leaflet-popup')
await wait(3800)

const exportPath = join(rawDir, 'animals.csv')
await app.evaluate(({ dialog }, target) => {
  dialog.showSaveDialog = async () => ({ canceled: false, filePath: target })
}, exportPath)
await caption('Export for the client', 'CSV and GeoJSON points, a full map report, and a per-detection audit trail')
await clickOn('.exports button:has-text("GeoJSON")')
await wait(900)
await clickOn('.exports button:has-text("CSV")')
await page.waitForSelector('text=Saved')
await wait(2400)
await caption('')

await card(
  `<h1><span class="r">${raw}</span> detections → <span class="g">${unique}</span> animals</h1>
   <p>Windows · macOS · Linux — runs fully offline, no Python or internet needed</p>`,
  3600
)

const video = page.video()
await app.close()
const recorded = await video.path()
const final = join(outDir, 'livestock-counter-demo.webm')
renameSync(recorded, final)
writeFileSync(join(outDir, 'recording.json'), JSON.stringify({ raw, unique, duplicates: dupes, introAtS: introAt, app: appPath }, null, 2))
console.log(`recorded ${final} (${raw} → ${unique})`)
for (const f of readdirSync(rawDir)) if (f.endsWith('.webm')) console.log('leftover', f)
