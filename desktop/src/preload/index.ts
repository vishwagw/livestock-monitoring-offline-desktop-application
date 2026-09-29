/**
 * The only bridge between the sandboxed renderer and the main process.
 * It exposes a small, typed API; no Node.js or Electron objects leak into
 * the page.
 */

import { contextBridge, ipcRenderer, webUtils } from 'electron'

import { IPC, type LivestockApi, type ProgressEvent } from '@shared/types'

const api: LivestockApi = {
  pathForFile: (file) => webUtils.getPathForFile(file),
  inspectFiles: (paths) => ipcRenderer.invoke(IPC.inspectFiles, paths),
  browseFiles: (role) => ipcRenderer.invoke(IPC.browseFiles, role),
  forgetFile: (id) => ipcRenderer.invoke(IPC.forgetFile, id),
  run: (request) => ipcRenderer.invoke(IPC.run, request),
  cancel: () => ipcRenderer.invoke(IPC.cancel),
  onProgress: (listener) => {
    const wrapped = (_event: Electron.IpcRendererEvent, payload: ProgressEvent): void => listener(payload)
    ipcRenderer.on(IPC.progress, wrapped)
    return () => ipcRenderer.removeListener(IPC.progress, wrapped)
  },
  engineStatus: () => ipcRenderer.invoke(IPC.engineStatus),
  choosePython: () => ipcRenderer.invoke(IPC.choosePython),
  exportResult: (kind) => ipcRenderer.invoke(IPC.exportResult, kind),
  getSettings: () => ipcRenderer.invoke(IPC.getSettings),
  updateSettings: (patch) => ipcRenderer.invoke(IPC.updateSettings, patch),
  chooseTileCache: (clear) => ipcRenderer.invoke(IPC.chooseTileCache, clear === true),
  tileInfo: () => ipcRenderer.invoke(IPC.tileInfo)
}

contextBridge.exposeInMainWorld('livestock', api)
