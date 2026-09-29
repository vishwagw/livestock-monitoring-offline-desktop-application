import { describe, expect, it } from 'vitest'

import { countTiles, lonLatToTile, parseArgs, tilesFor } from '../scripts/seed-tiles.mjs'

describe('seed-tiles', () => {
  it('converts coordinates to XYZ tiles', () => {
    expect(lonLatToTile(0, 0, 1)).toEqual({ x: 1, y: 1 })
    expect(lonLatToTile(-180, 85, 2)).toEqual({ x: 0, y: 0 })
    expect(lonLatToTile(151.2093, -33.8688, 15)).toEqual({ x: 30147, y: 19663 })
  })

  it('enumerates exactly the counted tiles', () => {
    const bbox = { minLon: 151.205, minLat: -33.872, maxLon: 151.214, maxLat: -33.865 }
    const tiles = [...tilesFor(bbox, 14, 17)]
    expect(tiles.length).toBe(countTiles(bbox, 14, 17))
    expect(new Set(tiles.map((t) => t.z))).toEqual(new Set([14, 15, 16, 17]))
  })

  it('validates arguments and refuses the public OSM servers', () => {
    const base = ['--bbox', '1,2,3,4', '--zoom', '10-12', '--out', '/tmp/t']
    expect(parseArgs([...base, '--url', 'https://t.example.com/{z}/{x}/{y}.png'])).toMatchObject({ minZoom: 10, maxZoom: 12 })
    expect(() => parseArgs([...base, '--url', 'https://tile.openstreetmap.org/{z}/{x}/{y}.png'])).toThrow(/forbids/)
    expect(() => parseArgs([...base, '--url', 'https://a.tile.openstreetmap.org/{z}/{x}/{y}.png'])).toThrow(/forbids/)
    expect(() => parseArgs([...base, '--url', 'https://t.example.com/tiles.png'])).toThrow(/\{z\}/)
    expect(() => parseArgs(['--bbox', '3,4,1,2', '--zoom', '1', '--url', 'x/{z}/{x}/{y}', '--out', 'o'])).toThrow(/minLon/)
  })
})
