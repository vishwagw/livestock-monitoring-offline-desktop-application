/**
 * Pure geometry for the point layer (no DOM/Leaflet), so it can be unit-tested.
 */

/** Web Mercator unit coordinates in [0, 1] (Leaflet's EPSG:3857 at zoom 0 / 256). */
export function toMercatorUnit(lat: number, lon: number): [number, number] {
  const clamped = Math.max(-85.05112878, Math.min(85.05112878, lat))
  const s = Math.sin((clamped * Math.PI) / 180)
  return [(lon + 180) / 360, 0.5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI)]
}

/** Grid index over Mercator unit coordinates; cells are zoom-18 tiles. */
export class PointIndex {
  static readonly LEVEL = 2 ** 18
  private readonly cells = new Map<number, number[]>()

  constructor(
    readonly u: Float64Array,
    readonly v: Float64Array
  ) {
    for (let i = 0; i < u.length; i++) {
      const key = this.key(Math.floor(u[i] * PointIndex.LEVEL), Math.floor(v[i] * PointIndex.LEVEL))
      const cell = this.cells.get(key)
      if (cell) cell.push(i)
      else this.cells.set(key, [i])
    }
  }

  private key(cx: number, cy: number): number {
    return cx * PointIndex.LEVEL + cy
  }

  /** Index of the nearest point within `radius` (unit coordinates), or -1. */
  nearest(u: number, v: number, radius: number): number {
    const L_ = PointIndex.LEVEL
    const r = Math.ceil(radius * L_)
    const cx = Math.floor(u * L_)
    const cy = Math.floor(v * L_)
    let best = -1
    let bestD = radius * radius
    for (let x = cx - r; x <= cx + r; x++) {
      for (let y = cy - r; y <= cy + r; y++) {
        const cell = this.cells.get(this.key(x, y))
        if (!cell) continue
        for (const i of cell) {
          const du = this.u[i] - u
          const dv = this.v[i] - v
          const d = du * du + dv * dv
          if (d <= bestD) {
            bestD = d
            best = i
          }
        }
      }
    }
    return best
  }
}
