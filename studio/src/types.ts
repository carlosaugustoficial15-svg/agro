export type Layout = { tilt: number; azimuth: number; spacing: number; rows: number; panelsPerRow: number; height: number; [key: string]: any }
export type Site = { name: string; latitude: number; longitude: number; altitude: number; timezone: string; mode: 'agriculture'|'roof'; enriched?: any }
export type Project = {
  schemaVersion: 1; name: string; site: Site; layout: Layout; module?: any; siteParameters?: Record<string, any>; agriculture: Record<string, any>; structure?: Record<string, any>;
  weights: { energy: number; agriculture: number; access: number; cost: number };
  polygon: [number, number][]; optimizationBounds?: { width: number; height: number }; useDrawnArea?: boolean;
  electricalModel: 'simple'|'cec'; updatedAt: string
}
