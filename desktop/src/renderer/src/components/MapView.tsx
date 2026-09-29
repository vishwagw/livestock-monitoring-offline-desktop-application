import L from 'leaflet'
import { useEffect, useRef } from 'react'

import type { Report, ReportAnimal, ReportDetection, TileInfo } from '@shared/types'

import { formatCoord, formatPercent } from '../lib/format'
import { PointLayer } from '../lib/pointLayer'

export interface LayerVisibility {
  unique: boolean
  duplicates: boolean
  flightPath: boolean
  frames: boolean
}

interface Props {
  report: Report | null
  tiles: TileInfo | null
  visibility: LayerVisibility
  selectedAnimalId: string | null
  onSelectAnimal: (id: string | null) => void
}

export const MAP_COLORS = {
  unique: '#1fa64a',
  duplicate: '#e0342b',
  path: '#4f8fd6',
  frame: '#8aa0b8',
  link: '#f5b700'
}

const TILE_URL = 'tiles://cache/{z}/{x}/{y}'

/**
 * Background used when no offline tile cache is configured: a neutral canvas
 * grid so the map still gives a sense of scale without any imagery.
 */
const OfflineGrid = L.GridLayer.extend({
  createTile() {
    const tile = document.createElement('canvas')
    const size = this.getTileSize() as L.Point
    tile.width = size.x
    tile.height = size.y
    const ctx = tile.getContext('2d')!
    const dark = window.matchMedia?.('(prefers-color-scheme: dark)').matches
    ctx.fillStyle = dark ? '#18212b' : '#eef1ec'
    ctx.fillRect(0, 0, size.x, size.y)
    ctx.strokeStyle = dark ? 'rgba(255,255,255,0.06)' : 'rgba(0,0,0,0.06)'
    ctx.lineWidth = 1
    for (let i = 0; i <= 4; i++) {
      const p = (i * size.x) / 4 + 0.5
      ctx.beginPath()
      ctx.moveTo(p, 0)
      ctx.lineTo(p, size.y)
      ctx.moveTo(0, p)
      ctx.lineTo(size.x, p)
      ctx.stroke()
    }
    ctx.strokeStyle = dark ? 'rgba(255,255,255,0.14)' : 'rgba(0,0,0,0.14)'
    ctx.strokeRect(0.5, 0.5, size.x, size.y)
    return tile
  }
}) as unknown as new (options?: L.GridLayerOptions) => L.GridLayer

function escapeHtml(text: string): string {
  return text.replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]!)
}

function animalPopup(a: ReportAnimal): string {
  const votes = Object.entries(a.label_votes)
    .map(([k, v]) => `${escapeHtml(k)} ×${v}`)
    .join(', ')
  return `
    <div class="popup">
      <div class="popup__title"><span class="dot dot--unique"></span>${escapeHtml(a.id)} · ${escapeHtml(a.label)}</div>
      <dl>
        <dt>Confidence</dt><dd>${formatPercent(a.confidence)}</dd>
        <dt>Seen</dt><dd>${a.observations}× in ${a.frames} frame${a.frames === 1 ? '' : 's'}</dd>
        <dt>Spread</dt><dd>${a.spread_m.toFixed(2)} m</dd>
        ${a.class_conflict ? `<dt>Class votes</dt><dd class="warn">${votes}</dd>` : ''}
        <dt>Position</dt><dd>${formatCoord(a.lat, a.lon)}</dd>
      </dl>
    </div>`
}

function duplicateTooltip(d: ReportDetection): string {
  return `
    <div class="popup">
      <div class="popup__title"><span class="dot dot--duplicate"></span>Removed duplicate</div>
      <dl>
        <dt>Detection</dt><dd>${escapeHtml(d.id)}</dd>
        <dt>Frame</dt><dd>${escapeHtml(d.frame_id)}</dd>
        <dt>Class</dt><dd>${escapeHtml(d.label)} (${formatPercent(d.confidence)})</dd>
        <dt>Merged into</dt><dd>${escapeHtml(d.animal_id ?? '—')}</dd>
      </dl>
    </div>`
}

interface DataLayers {
  flightPath: L.LayerGroup
  frames: PointLayer | null
  unique: PointLayer | null
  duplicates: PointLayer | null
}

type Hit = { kind: 'animal' | 'duplicate' | 'frame'; index: number } | null

// Draw order (Leaflet panes): path < capture points < duplicates < animals < selection.
// Green animals stay on top so the true headcount reads at any zoom, even in
// dense yards with 7+ re-sightings per animal; red appears around them.
const PANES: [string, number][] = [
  ['lc-frames', 410],
  ['lc-duplicates', 415],
  ['lc-animals', 420],
  ['lc-selection', 440]
]

export function MapView({ report, tiles, visibility, selectedAnimalId, onSelectAnimal }: Props) {
  const container = useRef<HTMLDivElement>(null)
  const mapRef = useRef<L.Map | null>(null)
  const baseRef = useRef<L.Layer | null>(null)
  const gridRef = useRef<L.GridLayer | null>(null)
  const dataRef = useRef<DataLayers | null>(null)
  const selectionRef = useRef<L.LayerGroup | null>(null)
  // Index into report.detections for each point of the duplicates layer.
  const dupIndexRef = useRef<Int32Array>(new Int32Array(0))
  const reportRef = useRef<Report | null>(report)
  reportRef.current = report
  const visibilityRef = useRef(visibility)
  visibilityRef.current = visibility
  const selectRef = useRef(onSelectAnimal)
  selectRef.current = onSelectAnimal

  // Create the map once.
  useEffect(() => {
    if (!container.current) return
    const map = L.map(container.current, {
      preferCanvas: true,
      zoomControl: true,
      attributionControl: false,
      minZoom: 2,
      maxZoom: 22,
      worldCopyJump: true
    }).setView([0, 0], 3)
    L.control.scale({ imperial: false, position: 'bottomleft' }).addTo(map)
    for (const [name, z] of PANES) map.createPane(name).style.zIndex = String(z)
    const selection = L.layerGroup().addTo(map)
    dataRef.current = { flightPath: L.layerGroup().addTo(map), frames: null, unique: null, duplicates: null }
    selectionRef.current = selection
    mapRef.current = map

    const hitAt = (point: L.Point): Hit => {
      const data = dataRef.current
      const vis = visibilityRef.current
      if (!data) return null
      const a = vis.unique && data.unique ? data.unique.hit(map, point, 9) : -1
      if (a >= 0) return { kind: 'animal', index: a }
      const d = vis.duplicates && data.duplicates ? data.duplicates.hit(map, point, 6) : -1
      if (d >= 0) return { kind: 'duplicate', index: dupIndexRef.current[d] }
      const f = vis.frames && data.frames ? data.frames.hit(map, point, 5) : -1
      if (f >= 0) return { kind: 'frame', index: f }
      return null
    }

    map.on('click', (e: L.LeafletMouseEvent) => {
      const hit = hitAt(e.containerPoint)
      const r = reportRef.current
      if (hit?.kind === 'animal' && r) selectRef.current(r.animals[hit.index].id)
      else if (hit?.kind === 'duplicate' && r) selectRef.current(r.detections[hit.index].animal_id)
      else selectRef.current(null)
    })

    const tooltip = L.tooltip({ direction: 'top', offset: [0, -6], opacity: 0.95 })
    let pending = 0
    map.on('mousemove', (e: L.LeafletMouseEvent) => {
      cancelAnimationFrame(pending)
      pending = requestAnimationFrame(() => {
        const hit = hitAt(e.containerPoint)
        const r = reportRef.current
        map.getContainer().style.cursor = hit && hit.kind !== 'frame' ? 'pointer' : ''
        if (!hit || !r) {
          map.closeTooltip(tooltip)
          return
        }
        let latlng: L.LatLngExpression
        let html: string
        if (hit.kind === 'animal') {
          const a = r.animals[hit.index]
          latlng = [a.lat, a.lon]
          html = `${escapeHtml(a.id)} · ${escapeHtml(a.label)} · ${a.observations} sightings`
        } else if (hit.kind === 'duplicate') {
          const d = r.detections[hit.index]
          latlng = [d.raw_lat, d.raw_lon]
          html = duplicateTooltip(d)
        } else {
          const f = r.frames[hit.index]
          latlng = [f.lat, f.lon]
          html = `${escapeHtml(f.id)} · ${f.altitude_agl_m.toFixed(1)} m · ${f.detections} detections`
        }
        tooltip.setLatLng(latlng).setContent(html)
        if (!map.hasLayer(tooltip)) map.openTooltip(tooltip)
      })
    })
    map.on('mouseout', () => map.closeTooltip(tooltip))

    const observer = new ResizeObserver(() => map.invalidateSize())
    observer.observe(container.current)
    return () => {
      observer.disconnect()
      cancelAnimationFrame(pending)
      map.remove()
      mapRef.current = null
      dataRef.current = null
    }
  }, [])

  // Base layer: offline tile cache, or the neutral grid.
  useEffect(() => {
    const map = mapRef.current
    if (!map) return
    if (baseRef.current) map.removeLayer(baseRef.current)
    if (gridRef.current) map.removeLayer(gridRef.current)
    gridRef.current = null
    // Tiles are only drawn inside the cached zoom range (and scaled up beyond
    // it). Scaling *down* high-zoom tiles would request thousands of them.
    const base =
      tiles?.enabled
        ? L.tileLayer(TILE_URL, {
            minZoom: tiles.minZoom ?? 0,
            maxZoom: 22,
            maxNativeZoom: tiles.maxZoom ?? 19,
            tileSize: 256,
            className: 'offline-tiles'
          })
        : new OfflineGrid({ tileSize: 256 })
    // Keep the neutral grid underneath so zoom levels outside the cache
    // still show a background.
    if (tiles?.enabled) {
      const grid = new OfflineGrid({ tileSize: 256 })
      grid.addTo(map)
      grid.bringToBack()
      gridRef.current = grid
    }
    base.addTo(map)
    if (!tiles?.enabled) (base as L.GridLayer).bringToBack()
    baseRef.current = base
  }, [tiles?.enabled, tiles?.path, tiles?.minZoom, tiles?.maxZoom])

  // Rebuild data layers whenever a new report arrives.
  useEffect(() => {
    const map = mapRef.current
    const data = dataRef.current
    if (!map || !data) return
    data.flightPath.clearLayers()
    for (const key of ['frames', 'unique', 'duplicates'] as const) {
      if (data[key]) map.removeLayer(data[key]!)
      data[key] = null
    }
    selectionRef.current?.clearLayers()
    if (!report) return

    for (const path of report.flight_paths) {
      if (path.coordinates.length > 1) {
        L.polyline(path.coordinates, {
          color: MAP_COLORS.path,
          weight: 2,
          opacity: 0.7,
          dashArray: '6 6',
          interactive: false
        }).addTo(data.flightPath)
      }
    }
    data.frames = new PointLayer(
      report.frames.map((f) => f.lat),
      report.frames.map((f) => f.lon),
      { radius: 2.5, fill: MAP_COLORS.frame, opacity: 0.85 },
      'lc-frames'
    )
    data.unique = new PointLayer(
      report.animals.map((a) => a.lat),
      report.animals.map((a) => a.lon),
      { radius: 6, fill: MAP_COLORS.unique, stroke: '#ffffff', strokeWidth: 1.5, opacity: 0.95 },
      'lc-animals'
    )
    // Duplicates are drawn at their raw ray-cast positions, beneath the
    // animals: each green animal carries a red halo of the re-sightings it
    // absorbed, which separate around it as you zoom in.
    const dupIdx: number[] = []
    report.detections.forEach((d, i) => d.status === 'duplicate' && dupIdx.push(i))
    dupIndexRef.current = Int32Array.from(dupIdx)
    data.duplicates = new PointLayer(
      dupIdx.map((i) => report.detections[i].raw_lat),
      dupIdx.map((i) => report.detections[i].raw_lon),
      { radius: 3, fill: MAP_COLORS.duplicate, opacity: 0.8 },
      'lc-duplicates'
    )
    if (report.bounds) map.fitBounds(report.bounds, { padding: [40, 40], maxZoom: 19, animate: false })
  }, [report])

  // Toggle layer visibility (also runs after a report rebuilds the layers).
  useEffect(() => {
    const map = mapRef.current
    const data = dataRef.current
    if (!map || !data) return
    ;(Object.keys(visibility) as (keyof LayerVisibility)[]).forEach((key) => {
      const layer = data[key]
      if (!layer) return
      if (visibility[key] && !map.hasLayer(layer)) layer.addTo(map)
      if (!visibility[key] && map.hasLayer(layer)) map.removeLayer(layer)
    })
  }, [visibility, report])

  // Highlight the selected animal and link it to the observations it absorbed.
  useEffect(() => {
    const map = mapRef.current
    const selection = selectionRef.current
    if (!map || !selection) return
    selection.clearLayers()
    map.closePopup()
    if (!report || !selectedAnimalId) return
    const animal = report.animals.find((a) => a.id === selectedAnimalId)
    if (!animal) return
    for (const d of report.detections) {
      if (d.animal_id !== animal.id) continue
      L.polyline(
        [
          [animal.lat, animal.lon],
          [d.raw_lat, d.raw_lon]
        ],
        { color: MAP_COLORS.link, weight: 1.5, opacity: 0.9, interactive: false, pane: 'lc-selection' }
      ).addTo(selection)
    }
    L.circleMarker([animal.lat, animal.lon], {
      radius: 11,
      color: MAP_COLORS.link,
      weight: 2.5,
      fill: false,
      interactive: false,
      pane: 'lc-selection'
    }).addTo(selection)
    const zoom = Math.max(map.getZoom(), 21)
    map.flyTo([animal.lat, animal.lon], zoom, { duration: 0.5 })
    L.popup({ offset: [0, -6], autoPan: false }).setLatLng([animal.lat, animal.lon]).setContent(animalPopup(animal)).openOn(map)
  }, [selectedAnimalId, report])

  return <div ref={container} className="map" role="region" aria-label="Flight map" />
}
