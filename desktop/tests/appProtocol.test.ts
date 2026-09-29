import { mkdirSync, mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

import { describe, expect, it } from 'vitest'

import { APP_INDEX_URL, isAppUrl, resolveAppUrl, serveApp } from '../src/main/appProtocol'

const root = mkdtempSync(join(tmpdir(), 'renderer-'))
mkdirSync(join(root, 'assets'))
writeFileSync(join(root, 'index.html'), '<html></html>')
writeFileSync(join(root, 'assets', 'app.js'), 'console.log(1)')
writeFileSync(join(root, 'secret.env'), 'KEY=1')

describe('app:// protocol', () => {
  it('serves files inside the renderer folder', async () => {
    expect(resolveAppUrl(root, APP_INDEX_URL)).toBe(join(root, 'index.html'))
    expect(resolveAppUrl(root, 'app://bundle/')).toBe(join(root, 'index.html'))
    const res = await serveApp(root, new Request('app://bundle/assets/app.js'))
    expect(res.status).toBe(200)
    expect(res.headers.get('content-type')).toMatch(/javascript/)
    expect(await res.text()).toBe('console.log(1)')
  })

  it.each([
    'app://bundle/../secret.env',
    'app://bundle/%2e%2e/%2e%2e/etc/passwd',
    'app://bundle/..%2F..%2Fetc%2Fpasswd.html',
    'app://bundle/secret.env',
    'app://other/index.html',
    'file:///etc/passwd',
    'app://bundle/%00.html',
    'app://bundle/%E0%A4%A.html'
  ])('refuses %s', async (url) => {
    expect(resolveAppUrl(root, url)).toBeNull()
  })

  it('returns 404 for missing files', async () => {
    expect((await serveApp(root, new Request('app://bundle/missing.js'))).status).toBe(404)
  })
})

describe('isAppUrl', () => {
  it('recognises only the app origin (URL.origin is "null" for custom schemes)', () => {
    expect(new URL(APP_INDEX_URL).origin).toBe('null')
    expect(isAppUrl(APP_INDEX_URL)).toBe(true)
    expect(isAppUrl('app://bundle/assets/x.js?v=1#h')).toBe(true)
    expect(isAppUrl('app://evil/index.html')).toBe(false)
    expect(isAppUrl('app://bundle.evil/index.html')).toBe(false)
    expect(isAppUrl('file:///index.html')).toBe(false)
    expect(isAppUrl('https://bundle/index.html')).toBe(false)
    expect(isAppUrl('not a url')).toBe(false)
  })
})
