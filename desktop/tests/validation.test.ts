import { describe, expect, it } from 'vitest'

import { DEFAULT_SETTINGS } from '@shared/types'
import * as validate from '../src/main/validation'

const ID = 'a1b2c3d4e5f60718293a4b5c'
const base = () => ({
  telemetryIds: [ID],
  detectionIds: [ID.replace('a', 'b')],
  datasetId: null,
  camera: { ...DEFAULT_SETTINGS.camera },
  flight: { ...DEFAULT_SETTINGS.flight },
  clustering: { ...DEFAULT_SETTINGS.clustering }
})

describe('runRequest', () => {
  it('accepts a well-formed request and drops unknown fields', () => {
    const req = validate.runRequest({ ...base(), extra: 'ignored', camera: { ...DEFAULT_SETTINGS.camera, evil: 1 } })
    expect(req).not.toHaveProperty('extra')
    expect(req.camera).not.toHaveProperty('evil')
    expect(req.telemetryIds).toEqual([ID])
  })

  it('rejects paths or bad ids in place of file ids', () => {
    expect(() => validate.runRequest({ ...base(), telemetryIds: ['/etc/passwd'] })).toThrow(/invalid/)
    expect(() => validate.runRequest({ ...base(), datasetId: '../x' })).toThrow(/invalid/)
  })

  it('requires telemetry and detections unless a dataset is given', () => {
    expect(() => validate.runRequest({ ...base(), detectionIds: [] })).toThrow(/at least one/)
    expect(validate.runRequest({ ...base(), telemetryIds: [], detectionIds: [], datasetId: ID }).datasetId).toBe(ID)
  })

  it('range-checks numbers and enums', () => {
    expect(() => validate.runRequest({ ...base(), camera: { ...DEFAULT_SETTINGS.camera, fovDeg: 400 } })).toThrow(/field of view/)
    expect(() => validate.runRequest({ ...base(), camera: { ...DEFAULT_SETTINGS.camera, imageWidth: 1200.5 } })).toThrow(/whole/)
    expect(() => validate.runRequest({ ...base(), clustering: { ...DEFAULT_SETTINGS.clustering, epsM: NaN } })).toThrow(/number/)
    expect(() => validate.runRequest({ ...base(), flight: { ...DEFAULT_SETTINGS.flight, bboxAnchor: 'top' } })).toThrow(/anchor/)
    expect(() =>
      validate.runRequest({ ...base(), flight: { ...DEFAULT_SETTINGS.flight, classNames: 'a\n--report /x' } })
    ).toThrow(/single line/)
  })
})

describe('settingsPatch', () => {
  it('only allows UI-editable sections', () => {
    expect(validate.settingsPatch({ clustering: DEFAULT_SETTINGS.clustering })).toEqual({ clustering: DEFAULT_SETTINGS.clustering })
    expect(() => validate.settingsPatch({ pythonPath: '/tmp/evil' })).toThrow(/cannot be changed/)
    expect(() => validate.settingsPatch({ tileCacheDir: '/' })).toThrow(/cannot be changed/)
  })
})

describe('pathList', () => {
  it('rejects non-strings, NUL bytes and oversized lists', () => {
    expect(validate.pathList(['/a/b.csv'])).toEqual(['/a/b.csv'])
    expect(() => validate.pathList([1])).toThrow()
    expect(() => validate.pathList(['/a\0b'])).toThrow()
    expect(() => validate.pathList(new Array(501).fill('/a.csv'))).toThrow(/at most/)
  })
})
