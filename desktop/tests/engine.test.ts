import { execFileSync } from 'node:child_process'
import { existsSync, mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { delimiter, join, resolve } from 'node:path'

import { describe, expect, it } from 'vitest'

import { DEFAULT_SETTINGS, type IngestedFile, type RunRequest } from '@shared/types'
import {
  buildProcessArgs,
  bundledEngine,
  engineEnv,
  EngineRunner,
  lineSplitter,
  parseEngineLine,
  pythonEngine,
  summariseStderr,
  toProgressEvent,
  type EngineCommand
} from '../src/main/engine'
import { bundledEnginePath, candidateEngines, findEngine, probeEngine } from '../src/main/python'

const ENGINE_SRC = resolve(__dirname, '../../src')
const SAMPLE = resolve(__dirname, '../../examples/sample-flight')
const BUNDLED = bundledEnginePath(resolve(__dirname, '../resources/engine'), process.platform)

const file = (id: string, path: string, kind: IngestedFile['kind']): IngestedFile => ({
  id, path, kind, name: path.split('/').pop()!, sizeBytes: 1, detail: ''
})
const files: Record<string, IngestedFile> = {
  t1: file('t1', '/data/flight one.SRT', 'telemetry'),
  d1: file('d1', '/data/boxes.csv', 'detections'),
  ds: file('ds', '/data/flight.json', 'dataset')
}
const request = (patch: Partial<RunRequest> = {}): RunRequest => ({
  telemetryIds: ['t1'],
  detectionIds: ['d1'],
  datasetId: null,
  camera: { ...DEFAULT_SETTINGS.camera },
  flight: { ...DEFAULT_SETTINGS.flight },
  clustering: { ...DEFAULT_SETTINGS.clustering },
  ...patch
})

describe('engine commands', () => {
  it('prefixes python with -m livestock_engine but runs the bundle directly', () => {
    expect(pythonEngine('py', ['-3'])).toEqual({ kind: 'python', command: 'py', args: ['-3', '-m', 'livestock_engine'] })
    expect(bundledEngine('/opt/engine/livestock-engine')).toEqual({ kind: 'bundled', command: '/opt/engine/livestock-engine', args: [] })
    expect(bundledEnginePath('/r/engine', 'win32')).toMatch(/livestock-engine\.exe$/)
  })

  it('isolates a bundled engine from the system Python environment', () => {
    const base = { PYTHONPATH: '/other', PYTHONHOME: '/py', PATH: '/bin' }
    expect(engineEnv(pythonEngine('python3'), '/engine', base).PYTHONPATH).toBe(`/engine${delimiter}/other`)
    const frozen = engineEnv(bundledEngine('/e'), '/engine', base)
    expect(frozen.PYTHONPATH).toBeUndefined()
    expect(frozen.PYTHONHOME).toBeUndefined()
    expect(frozen.PYTHONUNBUFFERED).toBe('1')
  })

  it('orders candidates: bundled first when packaged, sources first in development', () => {
    const common = {
      bundledDir: null,
      envBinary: undefined,
      configuredPython: '/usr/local/bin/python3.12',
      envPython: undefined,
      repoRoot: null,
      platform: 'linux' as const
    }
    const packaged = candidateEngines({ ...common, packaged: true, envBinary: '/opt/le' })
    expect(packaged[0]).toEqual(bundledEngine('/opt/le'))
    expect(packaged[1]).toEqual(pythonEngine('/usr/local/bin/python3.12'))
    const dev = candidateEngines({ ...common, packaged: false })
    expect(dev.map((c) => c.command)).toEqual(['/usr/local/bin/python3.12', 'python3', 'python'])
    const win = candidateEngines({ ...common, configuredPython: null, packaged: false, platform: 'win32' })
    expect(win[0]).toEqual(pythonEngine('py', ['-3']))
  })
})

describe('buildProcessArgs', () => {
  it('builds raw-log arguments without shell quoting', () => {
    const args = buildProcessArgs(request(), (id) => files[id], '/runs/1')
    expect(args.slice(0, 2)).toEqual(['process', '--progress'])
    expect(args).toContain('/data/flight one.SRT')
    expect(args[args.indexOf('--fov-type') + 1]).toBe('diagonal')
    expect(args[args.indexOf('--eps') + 1]).toBe('2')
    expect(args[args.indexOf('--report') + 1]).toBe(join('/runs/1', 'report.json'))
    expect(args).not.toContain('--default-altitude')
    expect(args).not.toContain('--no-registration')
  })

  it('passes optional flags and prefers a dataset over raw logs', () => {
    const raw = buildProcessArgs(
      request({
        flight: { ...DEFAULT_SETTINGS.flight, defaultAltitudeM: 55, deriveHeading: false, classNames: ' cattle,sheep ' },
        clustering: { epsM: 1.5, registration: false, frameExclusivity: false, adaptiveDensity: false }
      }),
      (id) => files[id],
      '/r'
    )
    expect(raw[raw.indexOf('--default-altitude') + 1]).toBe('55')
    expect(raw[raw.indexOf('--class-names') + 1]).toBe('cattle,sheep')
    expect(raw).toEqual(expect.arrayContaining(['--no-derive-heading', '--no-registration', '--no-frame-exclusivity', '--no-adaptive-density']))

    const ds = buildProcessArgs(request({ datasetId: 'ds' }), (id) => files[id], '/r')
    expect(ds[ds.indexOf('--dataset') + 1]).toBe('/data/flight.json')
    expect(ds).not.toContain('--telemetry')
    expect(ds).not.toContain('--image-width')
  })

  it('fails for files that are not in the registry', () => {
    expect(() =>
      buildProcessArgs(request({ telemetryIds: ['nope'] }), (id) => {
        if (!files[id]) throw new Error('file is no longer available')
        return files[id]
      }, '/r')
    ).toThrow(/no longer available/)
  })
})

describe('engine output parsing', () => {
  it('parses only event lines', () => {
    expect(parseEngineLine('{"event": "progress", "percent": 40, "message": "x"}')).toMatchObject({ percent: 40 })
    expect(parseEngineLine('warning: something')).toBeNull()
    expect(parseEngineLine('{"not": "an event"}')).toBeNull()
    expect(parseEngineLine('{broken')).toBeNull()
  })

  it('reassembles lines split across chunks', () => {
    const lines: string[] = []
    const push = lineSplitter((l) => lines.push(l))
    push('{"a":')
    push('1}\r\n{"b"')
    push(':2}\n')
    expect(lines).toEqual(['{"a":1}', '{"b":2}'])
  })

  it('maps engine events to sanitised IPC progress events', () => {
    const e = toProgressEvent('run1', {
      event: 'progress',
      stage: 'align',
      stage_index: 4,
      stages: ['Read', 'Match', 'Geo', 'Cluster', 'Align', 'Report'],
      stage_percent: 33.3,
      percent: 60,
      message: 'Alignment pass 1 of 3',
      counts: { animals: 150, bogus: 'x' as unknown as number },
      elapsed_s: 1.5
    })
    expect(e).toEqual({
      runId: 'run1',
      stage: 'align',
      stageIndex: 4,
      stages: ['Read', 'Match', 'Geo', 'Cluster', 'Align', 'Report'],
      stagePercent: 33.3,
      percent: 60,
      message: 'Alignment pass 1 of 3',
      counts: { animals: 150 },
      elapsedS: 1.5
    })
    const done = toProgressEvent('run1', { event: 'done', percent: 100, stages: ['A', 'B'] })
    expect(done).toMatchObject({ stage: 'done', stageIndex: 2, percent: 100, stagePercent: 100 })
    expect(toProgressEvent('r', { event: 'progress', percent: 250 }).percent).toBe(100)
    expect(summariseStderr('Traceback...\n  File x\nerror: bad input\n')).toBe('bad input')
  })
})

const python = (() => {
  try {
    execFileSync('python3', ['-m', 'livestock_engine', 'self-check'], { env: engineEnv(pythonEngine('python3'), ENGINE_SRC) })
    return pythonEngine('python3')
  } catch {
    return null
  }
})()

const engines: [string, EngineCommand | null][] = [
  ['python sources', python],
  ['frozen bundle', existsSync(BUNDLED) ? bundledEngine(BUNDLED) : null]
]

for (const [name, engine] of engines) {
  describe.skipIf(!engine)(`EngineRunner with the ${name}`, () => {
    it('passes self-check', async () => {
      const status = await probeEngine(engine!, ENGINE_SRC)
      expect(status).toMatchObject({ ok: true, kind: engine!.kind, error: null })
      expect(status.version).toMatch(/^\d+\.\d+\.\d+$/)
    }, 60_000)

    it('streams staged progress and writes a report', async () => {
      const dir = mkdtempSync(join(tmpdir(), 'engine-'))
      const reg: Record<string, IngestedFile> = {
        t: file('t', join(SAMPLE, 'flight.SRT'), 'telemetry'),
        d: file('d', join(SAMPLE, 'detections.csv'), 'detections')
      }
      const args = buildProcessArgs(request({ telemetryIds: ['t'], detectionIds: ['d'] }), (id) => reg[id], dir)
      const events = [] as ReturnType<typeof toProgressEvent>[]
      const outcome = await new EngineRunner().run(engine!, args, { cwd: dir, env: engineEnv(engine!, ENGINE_SRC) }, (e) =>
        events.push(toProgressEvent('r', e))
      )
      expect(outcome.code).toBe(0)
      const last = events.at(-1)!
      expect(last).toMatchObject({ stage: 'done', percent: 100 })
      expect(events[0].stages).toEqual([
        'Reading flight files',
        'Matching detections to telemetry',
        'Georeferencing detections',
        'Clustering sightings',
        'Aligning overlapping frames',
        'Building map report'
      ])
      const percents = events.map((e) => e.percent)
      expect(percents).toEqual([...percents].sort((a, b) => a - b))
      expect(new Set(events.map((e) => e.stage))).toEqual(
        new Set(['read', 'match', 'georeference', 'cluster', 'align', 'report', 'done'])
      )
      expect(events.at(-2)!.counts.animals).toBe(150)
    }, 60_000)

    it('reports engine errors and supports cancellation', async () => {
      const dir = mkdtempSync(join(tmpdir(), 'engine-'))
      writeFileSync(join(dir, 'bad.csv'), 'image,label\na,cow\n')
      writeFileSync(join(dir, 'f.SRT'), '1\n00:00:00,000 --> 00:00:01,000\n[latitude: 1.0] [longitude: 2.0] [rel_alt: 50]\n')
      const reg: Record<string, IngestedFile> = {
        t: file('t', join(dir, 'f.SRT'), 'telemetry'),
        d: file('d', join(dir, 'bad.csv'), 'detections')
      }
      const env = engineEnv(engine!, ENGINE_SRC)
      const runner = new EngineRunner()
      const args = buildProcessArgs(request({ telemetryIds: ['t'], detectionIds: ['d'] }), (id) => reg[id], dir)
      const outcome = await runner.run(engine!, args, { cwd: dir, env }, () => {})
      expect(outcome.code).toBe(2)
      expect(outcome.error).toMatch(/bounding-box/)

      // `benchmark` runs long enough to cancel mid-flight.
      const slow = runner.run(engine!, ['benchmark', '--runs', '50'], { cwd: dir, env }, () => {})
      expect(runner.busy).toBe(true)
      await expect(runner.run(engine!, ['self-check'], { cwd: dir, env }, () => {})).rejects.toThrow(/already running/)
      await new Promise((r) => setTimeout(r, 300))
      expect(runner.cancel()).toBe(true)
      expect((await slow).cancelled).toBe(true)
      expect(runner.busy).toBe(false)
    }, 60_000)
  })
}

describe('findEngine', () => {
  it('reports the most useful failure when nothing works', async () => {
    const result = await findEngine([bundledEngine('/definitely/missing/livestock-engine')], null)
    expect(result.engine).toBeNull()
    expect(result.status).toMatchObject({ ok: false, kind: 'bundled' })
    expect(result.status.error).toMatch(/not found/)
  })
})
