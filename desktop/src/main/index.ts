import { existsSync } from 'node:fs'
import { join, resolve } from 'node:path'

import { app, BrowserWindow, Menu, protocol, session, shell, type MenuItemConstructorOptions } from 'electron'

import type { TileInfo } from '@shared/types'
import { APP_INDEX_URL, APP_SCHEME, isAppUrl, serveApp } from './appProtocol'
import { registerIpc } from './ipc'
import { SettingsStore } from './settings'
import { parseTileUrl, readTile, TILE_SCHEME } from './tiles'

const isDev = !app.isPackaged && Boolean(process.env.ELECTRON_RENDERER_URL)
const devServerUrl = isDev ? new URL(process.env.ELECTRON_RENDERER_URL!) : null
const rendererDir = join(__dirname, '../renderer')

// In development the engine runs from the repository's src/ folder with the
// local Python. Packaged builds use only the frozen binary below.
const repoRoot = app.isPackaged ? null : resolve(app.getAppPath(), '..')
const engineSrc =
  repoRoot && existsSync(join(repoRoot, 'src', 'livestock_engine')) ? join(repoRoot, 'src') : null

// Packaged builds carry the frozen engine (PyInstaller one-folder bundle).
// In development it may exist after `python packaging/build_engine.py`.
const bundledEngineDir = app.isPackaged
  ? join(process.resourcesPath, 'engine')
  : join(app.getAppPath(), 'resources', 'engine')

protocol.registerSchemesAsPrivileged([
  { scheme: APP_SCHEME, privileges: { standard: true, secure: true, supportFetchAPI: true } },
  { scheme: TILE_SCHEME, privileges: { standard: true, secure: true, supportFetchAPI: true } }
])

if (!app.requestSingleInstanceLock()) {
  app.quit()
}

let mainWindow: BrowserWindow | null = null
let tileCacheDir: string | null = null

function isTrustedUrl(url: string): boolean {
  if (devServerUrl) {
    try {
      return new URL(url).origin === devServerUrl.origin
    } catch {
      return false
    }
  }
  return isAppUrl(url)
}

function isAllowedRequest(url: string): boolean {
  if (/^(app|tiles|data|blob|devtools):/i.test(url)) return true
  if (devServerUrl) {
    try {
      const u = new URL(url)
      return u.hostname === devServerUrl.hostname && u.port === devServerUrl.port
    } catch {
      return false
    }
  }
  return false
}

function createWindow(): void {
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 1024,
    minHeight: 680,
    show: false,
    title: 'Livestock Counter',
    backgroundColor: '#10151c',
    webPreferences: {
      preload: join(__dirname, '../preload/index.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
      spellcheck: false
    }
  })
  mainWindow.once('ready-to-show', () => mainWindow?.show())
  mainWindow.on('closed', () => {
    mainWindow = null
  })
  if (devServerUrl) void mainWindow.loadURL(devServerUrl.href)
  else void mainWindow.loadURL(APP_INDEX_URL)
}

function buildMenu(): void {
  const template: MenuItemConstructorOptions[] = [
    ...(process.platform === 'darwin' ? [{ role: 'appMenu' as const }] : []),
    { role: 'fileMenu' },
    { role: 'editMenu' },
    {
      label: 'View',
      submenu: [
        ...(isDev ? [{ role: 'reload' as const }, { role: 'toggleDevTools' as const }, { type: 'separator' as const }] : []),
        { role: 'resetZoom' },
        { role: 'zoomIn' },
        { role: 'zoomOut' },
        { type: 'separator' },
        { role: 'togglefullscreen' }
      ]
    },
    { role: 'windowMenu' }
  ]
  Menu.setApplicationMenu(Menu.buildFromTemplate(template))
}

app.on('web-contents-created', (_event, contents) => {
  contents.setWindowOpenHandler(({ url }) => {
    // Links to documentation open in the system browser, never in-app.
    if (/^https:\/\//.test(url)) void shell.openExternal(url)
    return { action: 'deny' }
  })
  contents.on('will-navigate', (event, url) => {
    if (!isTrustedUrl(url)) event.preventDefault()
  })
  contents.on('will-attach-webview', (event) => event.preventDefault())
})

app.on('second-instance', () => {
  if (mainWindow) {
    if (mainWindow.isMinimized()) mainWindow.restore()
    mainWindow.focus()
  }
})

app.whenReady().then(async () => {
  const ses = session.defaultSession
  ses.setPermissionRequestHandler((_wc, _permission, callback) => callback(false))
  ses.setPermissionCheckHandler(() => false)
  // Offline by design: nothing may leave the machine.
  ses.webRequest.onBeforeRequest((details, callback) => callback({ cancel: !isAllowedRequest(details.url) }))

  const settings = new SettingsStore(join(app.getPath('userData'), 'settings.json'))
  tileCacheDir = (await settings.load()).tileCacheDir

  protocol.handle(APP_SCHEME, (request) => serveApp(rendererDir, request))
  protocol.handle(TILE_SCHEME, async (request) => {
    const coord = parseTileUrl(request.url)
    if (!coord || !tileCacheDir) return new Response(null, { status: 404 })
    const tile = await readTile(tileCacheDir, coord)
    if (!tile) return new Response(null, { status: 404 })
    return new Response(new Uint8Array(tile.data), {
      headers: { 'content-type': tile.type, 'cache-control': 'max-age=86400' }
    })
  })

  registerIpc({
    settings,
    userData: app.getPath('userData'),
    engineSrc,
    bundledEngineDir,
    packaged: app.isPackaged,
    repoRoot,
    isTrustedUrl,
    onTileCacheChanged: (info: TileInfo) => {
      tileCacheDir = info.enabled ? info.path : null
    }
  })

  buildMenu()
  createWindow()
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit()
})
