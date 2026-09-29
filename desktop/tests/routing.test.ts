import { describe, expect, it } from 'vitest'

import type { IngestedFile } from '@shared/types'
import { canRun, routeFiles } from '../src/renderer/src/lib/routing'

const f = (id: string, kind: IngestedFile['kind'], name = `${id}.csv`): IngestedFile => ({
  id, kind, name, path: `/x/${name}`, sizeBytes: 1, detail: ''
})

describe('routeFiles', () => {
  it('routes by detected kind and notes files dropped on the wrong zone', () => {
    const { files, notices } = routeFiles([], [f('a', 'telemetry'), f('b', 'detections')], 'telemetry')
    expect(files.map((x) => [x.id, x.role])).toEqual([['a', 'telemetry'], ['b', 'detections']])
    expect(notices).toHaveLength(1)
    expect(notices[0]).toMatch(/b.csv looks like a detection log/)
  })

  it('keeps unknown files where they were dropped', () => {
    const { files, notices } = routeFiles([], [f('u', 'unknown')], 'detections')
    expect(files[0].role).toBe('detections')
    expect(notices).toEqual([])
  })

  it('keeps a single dataset and de-duplicates re-added files', () => {
    let state = routeFiles([], [f('d1', 'dataset')], 'dataset').files
    const second = routeFiles(state, [f('d2', 'dataset'), f('t', 'telemetry')], 'dataset')
    state = second.files
    expect(state.filter((x) => x.role === 'dataset').map((x) => x.id)).toEqual(['d2'])
    expect(second.notices.some((n) => /replaced/.test(n))).toBe(true)
    expect(routeFiles(state, [f('t', 'telemetry')], 'telemetry').files).toHaveLength(2)
  })
})

describe('canRun', () => {
  it('needs telemetry and detections, or a dataset', () => {
    expect(canRun([]).ok).toBe(false)
    const t = { ...f('t', 'telemetry'), role: 'telemetry' as const }
    const d = { ...f('d', 'detections'), role: 'detections' as const }
    expect(canRun([t]).reason).toMatch(/bounding-box/)
    expect(canRun([d]).reason).toMatch(/flight log/)
    expect(canRun([t, d]).ok).toBe(true)
    expect(canRun([{ ...f('s', 'dataset'), role: 'dataset' }]).ok).toBe(true)
  })
})

describe('progress time helpers', () => {
  it('formats durations and estimates remaining time', async () => {
    const { estimateRemaining, formatDuration } = await import('../src/renderer/src/lib/format')
    expect(formatDuration(12.4)).toBe('12 s')
    expect(formatDuration(125)).toBe('2 min 5 s')
    expect(formatDuration(-1)).toBe('—')
    expect(estimateRemaining(50, 10)).toBe(10)
    expect(estimateRemaining(5, 10)).toBeNull() // too early to be meaningful
    expect(estimateRemaining(100, 10)).toBeNull()
  })
})

describe('point layer geometry', () => {
  it('matches Leaflet Web Mercator and finds nearest points', async () => {
    const { PointIndex, toMercatorUnit } = await import('../src/renderer/src/lib/mercator')
    expect(toMercatorUnit(0, 0)).toEqual([0.5, 0.5])
    const [u, v] = toMercatorUnit(-33.8688, 151.2093)
    // EPSG:3857 reference (pyproj) at 256 px per world: (235.52661, 153.62362).
    expect(u * 256).toBeCloseTo(235.52661, 4)
    expect(v * 256).toBeCloseTo(153.62362, 4)
    const pts = [[-33.8688, 151.2093], [-33.8689, 151.2093], [-33.9, 151.3]].map(([a, b]) => toMercatorUnit(a, b))
    const index = new PointIndex(Float64Array.from(pts.map((p) => p[0])), Float64Array.from(pts.map((p) => p[1])))
    const [qu, qv] = toMercatorUnit(-33.86889, 151.2093)
    expect(index.nearest(qu, qv, 1e-6)).toBe(1)
    expect(index.nearest(qu, qv, 1e-12)).toBe(-1)
  })

  it('renders only the visible slice of a long table', async () => {
    const { visibleWindow, ROW_HEIGHT } = await import('../src/renderer/src/lib/virtual')
    const w = visibleWindow(28000, 1000 * ROW_HEIGHT, 400)
    expect(w.end - w.start).toBeLessThan(60)
    expect(w.before + (w.end - w.start) * ROW_HEIGHT + w.after).toBe(28000 * ROW_HEIGHT)
  })
})
