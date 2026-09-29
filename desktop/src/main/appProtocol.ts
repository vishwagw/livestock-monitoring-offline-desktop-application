/**
 * Serves the built renderer from `app://bundle/…`.
 *
 * Loading the UI from `file://` would need Electron's extra file-protocol
 * privileges, which the release fuses turn off (`GrantFileProtocolExtraPrivileges`).
 * A dedicated scheme gives the page a stable, non-file origin and lets the
 * main process decide exactly which files it may load: only files inside the
 * renderer folder, with a small MIME allow-list.
 */

import { readFile } from 'node:fs/promises'
import { extname, join, normalize, sep } from 'node:path'

export const APP_SCHEME = 'app'
export const APP_HOST = 'bundle'
export const APP_ORIGIN = `${APP_SCHEME}://${APP_HOST}`
export const APP_INDEX_URL = `${APP_ORIGIN}/index.html`

const MIME: Record<string, string> = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.webp': 'image/webp',
  '.woff': 'font/woff',
  '.woff2': 'font/woff2',
  '.ico': 'image/x-icon'
}

/**
 * True for URLs of the app's own pages. Note that `URL.origin` is the opaque
 * string "null" for custom schemes, so protocol and host are compared instead.
 */
export function isAppUrl(url: string): boolean {
  try {
    const u = new URL(url)
    return u.protocol === `${APP_SCHEME}:` && u.host === APP_HOST
  } catch {
    return false
  }
}

/** Map an `app://bundle/...` URL to a file inside `root`, or null if not allowed. */
export function resolveAppUrl(root: string, url: string): string | null {
  let parsed: URL
  try {
    parsed = new URL(url)
  } catch {
    return null
  }
  if (parsed.protocol !== `${APP_SCHEME}:` || parsed.hostname !== APP_HOST) return null
  let pathname: string
  try {
    pathname = decodeURIComponent(parsed.pathname)
  } catch {
    return null
  }
  if (pathname.includes('\0')) return null
  if (pathname === '/' || pathname === '') pathname = '/index.html'
  const base = normalize(root)
  const file = normalize(join(base, pathname))
  if (!file.startsWith(base.endsWith(sep) ? base : base + sep)) return null
  if (!(extname(file).toLowerCase() in MIME)) return null
  return file
}

export async function serveApp(root: string, request: Request): Promise<Response> {
  const file = resolveAppUrl(root, request.url)
  if (!file) return new Response('Not found', { status: 404 })
  try {
    const data = await readFile(file)
    return new Response(new Uint8Array(data), {
      headers: { 'content-type': MIME[extname(file).toLowerCase()], 'x-content-type-options': 'nosniff' }
    })
  } catch {
    return new Response('Not found', { status: 404 })
  }
}
