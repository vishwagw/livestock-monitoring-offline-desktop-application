import { execFileSync } from 'node:child_process'
import { mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { delimiter, join, resolve } from 'node:path'

import { describe, expect, it } from 'vitest'

import { DEFAULT_SETTINGS, type IngestedFile, type RunRequest } from '@shared/types'
import { buildProcessArgs, engineEnv, EngineRunner, lineSplitter, parseEngineLine, summariseStderr } from '../src/main/engine'

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

describe('buildProcessArgs', () => {
  it('builds raw-log arguments without shell quoting', () => {
    const args = buildProcessArgs(request(), (id) => files[id], '/runs/1')
    expect(args.slice(0, 4)).toEqual(['-m', 'livestock_engine', 'process', '--progress'])
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
        clustering: { epsM: 1.5, registration: false, frameExclusivity: false }
      }),
      (id) => files[id],
      '/r'
    )
    expect(raw[raw.indexOf('--default-altitude') + 1]).toBe('55')
    expect(raw[raw.indexOf('--class-names') + 1]).toBe('cattle,sheep')
    expect(raw).toEqual(expect.arrayContaining(['--no-derive-heading', '--no-registration', '--no-frame-exclusivity']))

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

  it('prepends the engine source to PYTHONPATH', () => {
    expect(engineEnv('/engine', { PYTHONPATH: '/other' }).PYTHONPATH).toBe(`/engine${delimiter}/other`)
    expect(engineEnv(null, {}).PYTHONPATH).toBeUndefined()
    expect(summariseStderr('Traceback...\n  File x\nerror: bad input\n')).toBe('bad input')
  })
})

const python = (() => {
  try {
    execFileSync('python3', ['-c', 'import livestock_engine'], {
      env: engineEnv(resolve(__dirname, '../../src'))
    })
    return 'python3'
  } catch {
    return null
  }
})()

describe.skipIf(!python)('EngineRunner with the real Python engine', () => {
  it('streams progress and writes a report', async () => {
    const dir = mkdtempSync(join(tmpdir(), 'engine-'))
    execFileSync('python3', ['-m', 'livestock_engine', 'simulate', '-o', join(dir, 'sim.json'), '--animals', '25', '--export-raw', dir], {
      env: engineEnv(resolve(__dirname, '../../src'))
    })
    const reg: Record<string, IngestedFile> = {
      t: file('t', join(dir, 'flight.SRT'), 'telemetry'),
      d: file('d', join(dir, 'detections.csv'), 'detections')
    }
    const args = buildProcessArgs(request({ telemetryIds: ['t'], detectionIds: ['d'] }), (id) => reg[id], dir)
    const events: unknown[] = []
    const outcome = await new EngineRunner().run(
      { command: 'python3', args: [] },
      args,
      { cwd: dir, env: engineEnv(resolve(__dirname, '../../src')) },
      (e) => events.push(e)
    )
    expect(outcome.code).toBe(0)
    expect(events.at(-1)).toMatchObject({ event: 'done', percent: 100 })
  }, 60_000)

  it('reports engine errors and supports cancellation', async () => {
    const dir = mkdtempSync(join(tmpdir(), 'engine-'))
    writeFileSync(join(dir, 'bad.csv'), 'image,label\na,cow\n')
    writeFileSync(join(dir, 'f.SRT'), '1\n00:00:00,000 --> 00:00:01,000\n[latitude: 1.0] [longitude: 2.0] [rel_alt: 50]\n')
    const reg: Record<string, IngestedFile> = {
      t: file('t', join(dir, 'f.SRT'), 'telemetry'),
      d: file('d', join(dir, 'bad.csv'), 'detections')
    }
    const env = engineEnv(resolve(__dirname, '../../src'))
    const runner = new EngineRunner()
    const outcome = await runner.run({ command: 'python3', args: [] }, buildProcessArgs(request({ telemetryIds: ['t'], detectionIds: ['d'] }), (id) => reg[id], dir), { cwd: dir, env }, () => {})
    expect(outcome.code).toBe(2)
    expect(outcome.error).toMatch(/bounding-box/)

    const slow = runner.run({ command: 'python3', args: [] }, ['-c', 'import time; time.sleep(30)'], { cwd: dir, env }, () => {})
    expect(runner.busy).toBe(true)
    await expect(runner.run({ command: 'python3', args: [] }, ['-c', 'pass'], { cwd: dir, env }, () => {})).rejects.toThrow(/already running/)
    expect(runner.cancel()).toBe(true)
    expect((await slow).cancelled).toBe(true)
    expect(runner.busy).toBe(false)
  }, 60_000)
})
