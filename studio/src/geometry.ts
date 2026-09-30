import type { Layout } from './types'

function positive(value: unknown, fallback: number): number {
  const number = Number(value)
  return Number.isFinite(number) && number > 0 ? number : fallback
}

function count(value: unknown, fallback: number, minimum: number): number {
  const number = Number(value)
  return Number.isFinite(number) ? Math.max(minimum, Math.floor(number)) : fallback
}

export function layoutGeometry(layout: Layout, module?: any) {
  const blocksX = count(layout.rows, 1, 0)
  const blocksY = count(layout.blocksY, 1, 1)
  const panelsX = count(layout.panelsX, 1, 1)
  const panelsY = count(layout.panelsPerRow, 1, 1)
  const width = positive(module?.Length ?? module?.PanelDimensionX, 2.384)
  const depth = positive(module?.Width ?? module?.PanelDimensionY, 1.303)
  const thickness = positive(module?.PanelDimensionZ, 0.07)
  const panelStepX = positive(layout.panelSpacingX, width + 0.05)
  const panelStepY = positive(layout.panelSpacingY, depth + 0.05)
  const blockStepX = positive(layout.spacing, 6)
  const blockStepY = positive(layout.blockSpacingY, 25)
  const spanX = Math.max(width, (blocksX - 1) * blockStepX + (panelsX - 1) * panelStepX + width)
  const spanZ = (blocksY - 1) * blockStepY + (panelsY - 1) * panelStepY + depth
  const total = blocksX * blocksY * panelsX * panelsY
  return { blocksX, blocksY, panelsX, panelsY, width, depth, thickness, panelStepX, panelStepY, blockStepX, blockStepY, spanX, spanZ, total }
}

export function polygonBounds(points: [number, number][]): { width: number; height: number } | null {
  if (points.length < 3) return null
  const latitudes = points.map(point => point[1])
  const longitudes = points.map(point => point[0])
  const meanLatitude = latitudes.reduce((sum, value) => sum + value, 0) / latitudes.length
  const width = (Math.max(...longitudes) - Math.min(...longitudes)) * 111_320 * Math.cos(meanLatitude * Math.PI / 180)
  const height = (Math.max(...latitudes) - Math.min(...latitudes)) * 111_320
  return { width: Math.max(1, width), height: Math.max(1, height) }
}
