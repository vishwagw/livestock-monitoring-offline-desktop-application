import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import {
  DEFAULT_SETTINGS,
  type AppSettings,
  type EngineStatus,
  type ExportKind,
  type FileRole,
  type ProgressEvent,
  type Report,
  type TileInfo
} from '@shared/types'

import { AnimalTable } from './components/AnimalTable'
import { DropZone } from './components/DropZone'
import { FileList } from './components/FileList'
import { Legend } from './components/Legend'
import { MapView, type LayerVisibility } from './components/MapView'
import { SettingsPanel } from './components/SettingsPanel'
import { StatsBar } from './components/StatsBar'
import { canRun, routeFiles, type RoutedFile } from './lib/routing'

const api = window.livestock

type Status =
  | { state: 'idle' }
  | { state: 'running'; progress: ProgressEvent | null }
  | { state: 'done'; durationMs: number }
  | { state: 'error'; message: string }

const FlightIcon = (
  <svg viewBox="0 0 24 24" width="26" height="26" fill="none" stroke="currentColor" strokeWidth="1.6">
    <path d="M4 17c3-6 6-9 8-9s3 3 8-3" strokeLinecap="round" />
    <circle cx="4" cy="17" r="1.6" />
    <circle cx="20" cy="5" r="1.6" />
  </svg>
)
const BoxIcon = (
  <svg viewBox="0 0 24 24" width="26" height="26" fill="none" stroke="currentColor" strokeWidth="1.6">
    <rect x="3" y="5" width="8" height="6" rx="1" />
    <rect x="13" y="12" width="8" height="7" rx="1" />
    <path d="M3 19h6M15 5h6" strokeLinecap="round" />
  </svg>
)

export function App() {
  const [files, setFiles] = useState<RoutedFile[]>([])
  const [notices, setNotices] = useState<string[]>([])
  const [settings, setSettings] = useState<AppSettings>(DEFAULT_SETTINGS)
  const [engine, setEngine] = useState<EngineStatus | null>(null)
  const [tiles, setTiles] = useState<TileInfo | null>(null)
  const [status, setStatus] = useState<Status>({ state: 'idle' })
  const [report, setReport] = useState<Report | null>(null)
  const [selected, setSelected] = useState<string | null>(null)
  const [tableOpen, setTableOpen] = useState(true)
  const [visibility, setVisibility] = useState<LayerVisibility>({
    unique: true,
    duplicates: true,
    flightPath: true,
    frames: false
  })
  const saveTimer = useRef<ReturnType<typeof setTimeout>>(undefined)

  const running = status.state === 'running'

  useEffect(() => {
    void api.getSettings().then(setSettings)
    void api.tileInfo().then(setTiles)
    void api.engineStatus().then(setEngine)
    const off = api.onProgress((p) => setStatus((s) => (s.state === 'running' ? { state: 'running', progress: p } : s)))
    // Dropping a file outside a drop zone must never navigate the window.
    const block = (e: DragEvent): void => e.preventDefault()
    window.addEventListener('dragover', block)
    window.addEventListener('drop', block)
    return () => {
      off()
      window.removeEventListener('dragover', block)
      window.removeEventListener('drop', block)
    }
  }, [])

  const addInspected = useCallback((result: Awaited<ReturnType<typeof api.inspectFiles>>, role: FileRole) => {
    setFiles((current) => {
      const routed = routeFiles(current, result.files, role)
      setNotices([...routed.notices, ...result.rejected.map((r) => `${r.name} was not added: ${r.reason}.`)])
      return routed.files
    })
  }, [])

  const onPaths = useCallback(
    async (paths: string[], role: FileRole) => addInspected(await api.inspectFiles(paths), role),
    [addInspected]
  )
  const onBrowse = useCallback(async (role: FileRole) => addInspected(await api.browseFiles(role), role), [addInspected])

  const removeFile = (id: string): void => {
    setFiles((f) => f.filter((x) => x.id !== id))
    void api.forgetFile(id)
  }

  const updateSettings = (patch: Partial<Pick<AppSettings, 'camera' | 'flight' | 'clustering'>>): void => {
    setSettings((s) => ({ ...s, ...patch }))
    clearTimeout(saveTimer.current)
    saveTimer.current = setTimeout(() => void api.updateSettings(patch).catch(() => undefined), 400)
  }

  const datasetFile = files.find((f) => f.role === 'dataset') ?? null
  const runnable = canRun(files)

  const run = async (): Promise<void> => {
    setStatus({ state: 'running', progress: null })
    setSelected(null)
    const result = await api.run({
      telemetryIds: files.filter((f) => f.role === 'telemetry').map((f) => f.id),
      detectionIds: files.filter((f) => f.role === 'detections').map((f) => f.id),
      datasetId: datasetFile?.id ?? null,
      camera: settings.camera,
      flight: settings.flight,
      clustering: settings.clustering
    })
    if (result.ok) {
      setReport(result.report)
      setStatus({ state: 'done', durationMs: result.durationMs })
    } else if (result.cancelled) {
      setStatus({ state: 'idle' })
    } else {
      setStatus({ state: 'error', message: result.error })
    }
  }

  const exportResult = async (kind: ExportKind): Promise<void> => {
    const r = await api.exportResult(kind)
    if (r.saved && r.path) setNotices([`Saved ${r.path}`])
    else if (r.error) setNotices([`Export failed: ${r.error}`])
  }

  const chooseTiles = async (clear = false): Promise<void> => {
    const info = await api.chooseTileCache(clear)
    setTiles(info)
    if (!clear && !info.enabled && info.path) {
      setNotices([`No map tiles found in ${info.path}. Expected folders like 15/29000/19000.png.`])
    }
  }

  const counts = useMemo(
    () =>
      report
        ? {
            unique: report.animals.length,
            duplicates: report.summary.duplicate_detections,
            frames: report.frames.length
          }
        : null,
    [report]
  )

  const progress = status.state === 'running' ? status.progress : null

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="brand__mark" aria-hidden>
            ◎
          </span>
          <span>Livestock Counter</span>
          <span className="pill pill--offline" title="No data leaves this computer">
            Offline
          </span>
        </div>
        <div className="topbar__status">
          <button type="button" className="link" onClick={() => void chooseTiles()} title={tiles?.path ?? undefined}>
            Map tiles:{' '}
            {tiles?.enabled ? `z${tiles.minZoom}–${tiles.maxZoom} cached` : 'none (grid only)'}
          </button>
          {tiles?.enabled && (
            <button type="button" className="link" onClick={() => void chooseTiles(true)}>
              Clear
            </button>
          )}
          <span className={`engine engine--${engine ? (engine.ok ? 'ok' : 'bad') : 'wait'}`} title={engine?.python ?? undefined}>
            <span className="engine__dot" aria-hidden />
            {engine ? (engine.ok ? `Engine ${engine.version}` : 'Engine unavailable') : 'Checking engine…'}
          </span>
          {engine && !engine.ok && (
            <button type="button" className="link" onClick={async () => setEngine(await api.choosePython())}>
              Choose Python…
            </button>
          )}
        </div>
      </header>

      <aside className="sidebar">
        <section className="panel">
          <h2>
            <span className="step">1</span> Flight data
          </h2>
          <DropZone
            role="telemetry"
            title="Flight logs"
            hint="Drop DJI .SRT or telemetry .CSV — or click to browse"
            icon={FlightIcon}
            disabled={running}
            onPaths={onPaths}
            onBrowse={onBrowse}
          />
          <FileList files={files} role="telemetry" disabled={running} onRemove={removeFile} empty="No flight logs yet." />
          <DropZone
            role="detections"
            title="Detection logs"
            hint="Drop AI bounding boxes: CSV, JSON, COCO or YOLO .txt"
            icon={BoxIcon}
            disabled={running}
            onPaths={onPaths}
            onBrowse={onBrowse}
          />
          <FileList files={files} role="detections" disabled={running} onRemove={removeFile} empty="No detection logs yet." />
          {datasetFile && (
            <>
              <h3 className="subhead">Engine dataset</h3>
              <FileList files={files} role="dataset" disabled={running} onRemove={removeFile} empty="" />
              <p className="muted small">A prepared dataset is processed on its own; flight and detection logs are ignored.</p>
            </>
          )}
          <button type="button" className="link small" disabled={running} onClick={() => void onBrowse('dataset')}>
            Open an engine dataset (.json) instead…
          </button>
          {notices.length > 0 && (
            <ul className="notices" aria-live="polite">
              {notices.map((n, i) => (
                <li key={i}>{n}</li>
              ))}
            </ul>
          )}
        </section>

        <section className="panel">
          <h2>
            <span className="step">2</span> Settings
          </h2>
          <SettingsPanel settings={settings} disabled={running} rawMode={!datasetFile} onChange={updateSettings} />
        </section>

        <section className="panel panel--run">
          <h2>
            <span className="step">3</span> Count
          </h2>
          {running ? (
            <>
              <div className="progress" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={progress?.percent ?? 0}>
                <div className="progress__bar" style={{ width: `${Math.max(3, progress?.percent ?? 3)}%` }} />
              </div>
              <p className="muted small">{progress?.message || 'Starting…'}</p>
              <button type="button" className="button button--ghost" onClick={() => void api.cancel()}>
                Cancel
              </button>
            </>
          ) : (
            <>
              <button
                type="button"
                className="button button--primary"
                disabled={!runnable.ok || engine?.ok === false}
                onClick={() => void run()}
              >
                Remove duplicates &amp; count
              </button>
              {!runnable.ok && <p className="muted small">{runnable.reason}</p>}
              {engine && !engine.ok && <p className="error small">{engine.error}</p>}
            </>
          )}
          {status.state === 'error' && (
            <p className="error" role="alert">
              {status.message}
            </p>
          )}
          {status.state === 'done' && report && (
            <p className="success small">
              Processed in {(status.durationMs / 1000).toFixed(1)} s · UTM EPSG:{report.summary.utm_epsg}
            </p>
          )}
          {report && report.warnings.length > 0 && (
            <details className="warnings">
              <summary>{report.warnings.length} warning{report.warnings.length === 1 ? '' : 's'}</summary>
              <ul>
                {report.warnings.map((w, i) => (
                  <li key={i}>{w}</li>
                ))}
              </ul>
            </details>
          )}
        </section>
      </aside>

      <main className="content">
        <div className="content__head">
          <StatsBar report={report} />
          <div className="exports" aria-label="Export results">
            <span className="muted small">Export</span>
            {(['csv', 'geojson', 'report', 'assignments'] as ExportKind[]).map((k) => (
              <button key={k} type="button" className="button button--small" disabled={!report || running} onClick={() => void exportResult(k)}>
                {k === 'csv' ? 'CSV' : k === 'geojson' ? 'GeoJSON' : k === 'report' ? 'Report' : 'Audit'}
              </button>
            ))}
          </div>
        </div>
        <div className="map-wrap">
          <MapView report={report} tiles={tiles} visibility={visibility} selectedAnimalId={selected} onSelectAnimal={setSelected} />
          <Legend visibility={visibility} counts={counts} onToggle={(k) => setVisibility((v) => ({ ...v, [k]: !v[k] }))} />
          {!report && (
            <div className="map-empty">
              <strong>No flight processed yet</strong>
              <span>Add a flight log and its detection log, then press “Remove duplicates &amp; count”.</span>
            </div>
          )}
        </div>
        {report && (
          <div className={`drawer${tableOpen ? ' drawer--open' : ''}`}>
            <button type="button" className="drawer__toggle" onClick={() => setTableOpen((o) => !o)} aria-expanded={tableOpen}>
              {tableOpen ? '▾' : '▴'} Animal index
            </button>
            {tableOpen && <AnimalTable animals={report.animals} selectedId={selected} onSelect={setSelected} />}
          </div>
        )}
      </main>
    </div>
  )
}
