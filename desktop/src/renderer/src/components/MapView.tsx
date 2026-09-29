import L from 'leaflet'
import { useEffect, useRef } from 'react'

import type { Report, ReportAnimal, ReportDetection, TileInfo } from '@shared/types'

import { formatCoord, formatPercent } from '../lib/format'

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

interface Layers {
  unique: L.LayerGroup
  duplicates: L.LayerGroup
  flightPath: L.LayerGroup
  frames: L.LayerGroup
  selection: L.LayerGroup
}

export function MapView({ report, tiles, visibility, selectedAnimalId, onSelectAnimal }: Props) {
  const container = useRef<HTMLDivElement>(null)
  const mapRef = useRef<L.Map | null>(null)
  const baseRef = useRef<L.Layer | null>(null)
  const gridRef = useRef<L.GridLayer | null>(null)
  const layersRef = useRef<Layers | null>(null)
  const rendererRef = useRef<L.Renderer | null>(null)
  const markersRef = useRef<Map<string, L.CircleMarker>>(new Map())
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
    const renderer = L.canvas({ padding: 0.5 })
    const layers: Layers = {
      flightPath: L.layerGroup(),
      frames: L.layerGroup(),
      duplicates: L.layerGroup(),
      unique: L.layerGroup(),
      selection: L.layerGroup()
    }
    Object.values(layers).forEach((l) => l.addTo(map))
    map.on('click', () => selectRef.current(null))
    mapRef.current = map
    layersRef.current = layers
    rendererRef.current = renderer
    const observer = new ResizeObserver(() => map.invalidateSize())
    observer.observe(container.current)
    return () => {
      observer.disconnect()
      map.remove()
      mapRef.current = null
      layersRef.current = null
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
    const layers = layersRef.current
    if (!map || !layers) return
    Object.values(layers).forEach((l) => l.clearLayers())
    markersRef.current.clear()
    if (!report) return
    const renderer = rendererRef.current ?? undefined

    for (const path of report.flight_paths) {
      if (path.coordinates.length > 1) {
        L.polyline(path.coordinates, {
          color: MAP_COLORS.path,
          weight: 2,
          opacity: 0.7,
          dashArray: '6 6',
          interactive: false,
          renderer
        }).addTo(layers.flightPath)
      }
    }
    for (const f of report.frames) {
      L.circleMarker([f.lat, f.lon], {
        radius: 2.5,
        color: MAP_COLORS.frame,
        weight: 1,
        fillOpacity: 0.8,
        renderer
      })
        .bindTooltip(`${escapeHtml(f.id)} · ${f.altitude_agl_m.toFixed(1)} m · ${f.detections} detections`)
        .addTo(layers.frames)
    }
    for (const a of report.animals) {
      const marker = L.circleMarker([a.lat, a.lon], {
        radius: 6,
        color: '#ffffff',
        weight: 1.5,
        fillColor: MAP_COLORS.unique,
        fillOpacity: 0.95,
        renderer
      })
        .bindPopup(animalPopup(a))
        .on('click', (e) => {
          L.DomEvent.stopPropagation(e)
          selectRef.current(a.id)
        })
        .addTo(layers.unique)
      markersRef.current.set(a.id, marker)
    }
    // Duplicates are drawn at their raw ray-cast positions, above the
    // animals: zoomed out every green animal shows a red core for the
    // re-sightings it absorbed; zoomed in they separate around it.
    for (const d of report.detections) {
      if (d.status !== 'duplicate') continue
      L.circleMarker([d.raw_lat, d.raw_lon], {
        radius: 3,
        color: MAP_COLORS.duplicate,
        weight: 1,
        fillColor: MAP_COLORS.duplicate,
        fillOpacity: 0.75,
        renderer
      })
        .bindTooltip(duplicateTooltip(d), { direction: 'top', offset: [0, -4] })
        .on('click', (e) => {
          L.DomEvent.stopPropagation(e)
          if (d.animal_id) selectRef.current(d.animal_id)
        })
        .addTo(layers.duplicates)
    }
    if (report.bounds) {
      map.fitBounds(report.bounds, { padding: [40, 40], maxZoom: 19 })
    }
  }, [report])

  // Toggle layer visibility.
  useEffect(() => {
    const map = mapRef.current
    const layers = layersRef.current
    if (!map || !layers) return
    ;(Object.keys(visibility) as (keyof LayerVisibility)[]).forEach((key) => {
      const layer = layers[key]
      if (visibility[key] && !map.hasLayer(layer)) layer.addTo(map)
      if (!visibility[key] && map.hasLayer(layer)) map.removeLayer(layer)
    })
    // Keep the drawing order stable: path < frames < unique < duplicates.
    ;(['flightPath', 'frames', 'unique', 'duplicates', 'selection'] as const).forEach((key) => {
      if (map.hasLayer(layers[key])) layers[key].eachLayer((l) => (l as L.Path).bringToFront?.())
    })
  }, [visibility, report])

  // Highlight the selected animal and link it to the observations it absorbed.
  useEffect(() => {
    const map = mapRef.current
    const layers = layersRef.current
    if (!map || !layers) return
    layers.selection.clearLayers()
    if (!report || !selectedAnimalId) return
    const animal = report.animals.find((a) => a.id === selectedAnimalId)
    if (!animal) return
    const members = report.detections.filter((d) => d.animal_id === animal.id)
    for (const d of members) {
      L.polyline(
        [
          [animal.lat, animal.lon],
          [d.raw_lat, d.raw_lon]
        ],
        { color: MAP_COLORS.link, weight: 1.5, opacity: 0.9, interactive: false }
      ).addTo(layers.selection)
    }
    L.circleMarker([animal.lat, animal.lon], {
      radius: 11,
      color: MAP_COLORS.link,
      weight: 2.5,
      fill: false,
      interactive: false
    }).addTo(layers.selection)
    const zoom = Math.max(map.getZoom(), 21)
    map.flyTo([animal.lat, animal.lon], zoom, { duration: 0.5 })
    markersRef.current.get(animal.id)?.openPopup()
  }, [selectedAnimalId, report])

  return <div ref={container} className="map" role="region" aria-label="Flight map" />
}
