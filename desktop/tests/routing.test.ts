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
