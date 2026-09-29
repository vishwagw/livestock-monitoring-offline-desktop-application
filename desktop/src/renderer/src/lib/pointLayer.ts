/**
 * High-volume point layer for Leaflet.
 *
 * Leaflet's `circleMarker` creates one object (plus event plumbing) per
 * point, which takes seconds and hundreds of MB for the 100k+ detections of a
 * large survey. This layer keeps coordinates in typed arrays, draws them onto
 * one canvas with a pre-rendered sprite, skips points outside the view or
 * hidden under an already-drawn point, and answers hover/click queries from a
 * grid index, so rendering 200k points takes tens of milliseconds.
 */

import L from 'leaflet'

import { PointIndex, toMercatorUnit } from './mercator'

export interface PointStyle {
  radius: number
  fill: string
  stroke?: string
  strokeWidth?: number
  opacity?: number
}

function makeSprite(style: PointStyle, dpr: number): HTMLCanvasElement {
  const stroke = style.stroke ? (style.strokeWidth ?? 1) : 0
  const size = Math.ceil((style.radius + stroke) * 2 * dpr) + 2
  const canvas = document.createElement('canvas')
  canvas.width = canvas.height = size
  const ctx = canvas.getContext('2d')!
  ctx.scale(dpr, dpr)
  const c = size / dpr / 2
  ctx.beginPath()
  ctx.arc(c, c, style.radius, 0, Math.PI * 2)
  ctx.globalAlpha = style.opacity ?? 1
  ctx.fillStyle = style.fill
  ctx.fill()
  if (style.stroke) {
    ctx.globalAlpha = 1
    ctx.lineWidth = stroke
    ctx.strokeStyle = style.stroke
    ctx.stroke()
  }
  return canvas
}

export class PointLayer extends L.Layer {
  readonly index: PointIndex
  private canvas: HTMLCanvasElement | null = null
  private sprite: HTMLCanvasElement | null = null
  private frame = 0
  private readonly paneName: string

  constructor(
    lat: ArrayLike<number>,
    lon: ArrayLike<number>,
    private readonly style: PointStyle,
    pane: string
  ) {
    super()
    const n = lat.length
    const u = new Float64Array(n)
    const v = new Float64Array(n)
    for (let i = 0; i < n; i++) {
      const [x, y] = toMercatorUnit(lat[i], lon[i])
      u[i] = x
      v[i] = y
    }
    this.index = new PointIndex(u, v)
    this.paneName = pane
  }

  get size(): number {
    return this.index.u.length
  }

  onAdd(map: L.Map): this {
    this.canvas = L.DomUtil.create('canvas', 'point-layer leaflet-zoom-hide') as HTMLCanvasElement
    map.getPane(this.paneName)!.appendChild(this.canvas)
    map.on('moveend zoomend resize viewreset', this.schedule, this)
    this.draw()
    return this
  }

  onRemove(map: L.Map): this {
    map.off('moveend zoomend resize viewreset', this.schedule, this)
    cancelAnimationFrame(this.frame)
    this.canvas?.remove()
    this.canvas = null
    return this
  }

  private schedule(): void {
    cancelAnimationFrame(this.frame)
    this.frame = requestAnimationFrame(() => this.draw())
  }

  /** Redraw every visible point. Returns the number drawn (for tests/metrics). */
  draw(): number {
    const map = this._map as L.Map | undefined
    const canvas = this.canvas
    if (!map || !canvas) return 0
    const dpr = window.devicePixelRatio || 1
    const size = map.getSize()
    const topLeft = map.containerPointToLayerPoint([0, 0])
    L.DomUtil.setPosition(canvas, topLeft)
    canvas.width = Math.round(size.x * dpr)
    canvas.height = Math.round(size.y * dpr)
    canvas.style.width = `${size.x}px`
    canvas.style.height = `${size.y}px`
    if (!this.sprite) this.sprite = makeSprite(this.style, dpr)
    const ctx = canvas.getContext('2d')!
    ctx.setTransform(1, 0, 0, 1, 0, 0)

    const worldPx = 256 * 2 ** map.getZoom()
    const origin = map.getPixelOrigin()
    const ox = origin.x + topLeft.x
    const oy = origin.y + topLeft.y
    const sprite = this.sprite
    const half = sprite.width / 2
    const margin = this.style.radius + 2
    // One point per 2x2 CSS-pixel cell: overlapping markers look identical,
    // so a zoomed-out view of 200k points draws only what is visible.
    const cw = Math.ceil(size.x / 2) + 2
    const occupied = new Uint8Array(cw * (Math.ceil(size.y / 2) + 2))
    const { u, v } = this.index
    let drawn = 0
    for (let i = 0; i < u.length; i++) {
      const x = u[i] * worldPx - ox
      const y = v[i] * worldPx - oy
      if (x < -margin || y < -margin || x > size.x + margin || y > size.y + margin) continue
      const cell = ((y + margin) >> 1) * cw + ((x + margin) >> 1)
      if (cell >= 0 && cell < occupied.length) {
        if (occupied[cell]) continue
        occupied[cell] = 1
      }
      ctx.drawImage(sprite, x * dpr - half, y * dpr - half)
      drawn++
    }
    return drawn
  }

  /** Nearest point to a container pixel within `tolerancePx`, or -1. */
  hit(map: L.Map, containerPoint: L.Point, tolerancePx: number): number {
    const worldPx = 256 * 2 ** map.getZoom()
    const layer = map.containerPointToLayerPoint(containerPoint)
    const origin = map.getPixelOrigin()
    return this.index.nearest((layer.x + origin.x) / worldPx, (layer.y + origin.y) / worldPx, tolerancePx / worldPx)
  }
}
