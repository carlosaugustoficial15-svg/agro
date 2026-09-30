import { describe, expect, it } from 'vitest'
import { layoutGeometry, polygonBounds } from './geometry'

describe('layout geometry', () => {
  it('uses the same dimensions as the PASE layout fields', () => {
    const geometry = layoutGeometry({ rows: 3, panelsPerRow: 40, spacing: 8, tilt: 20, azimuth: 180, height: 2.5, blocksY: 1, panelsX: 1, panelSpacingY: 1.31 })
    expect(geometry.total).toBe(120)
    expect(geometry.spanX).toBeCloseTo(18.384)
    expect(geometry.spanZ).toBeCloseTo(52.393)
  })

  it('shows an empty photovoltaic scene when zero blocks are configured', () => {
    expect(layoutGeometry({ rows: 0, panelsPerRow: 16, spacing: 6, tilt: 0, azimuth: 180, height: 2 }).total).toBe(0)
  })
})

describe('optimization area', () => {
  it('requires an area and calculates metres from geographic coordinates', () => {
    expect(polygonBounds([])).toBeNull()
    const bounds = polygonBounds([[4.7, 50.56], [4.7001, 50.56], [4.7001, 50.5601]])
    expect(bounds?.width).toBeGreaterThan(6)
    expect(bounds?.height).toBeGreaterThan(10)
  })
})
