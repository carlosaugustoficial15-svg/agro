import { useEffect, useRef } from 'react'
import * as maplibregl from 'maplibre-gl'

type Point = [number, number]

export default function MapView({ center, polygon, onPoint, onUndo, onClear }: { center: Point; polygon: Point[]; onPoint(point: Point): void; onUndo(): void; onClear(): void }) {
  const node = useRef<HTMLDivElement>(null)
  const map = useRef<maplibregl.Map | null>(null)
  const onPointRef = useRef(onPoint)
  onPointRef.current = onPoint

  useEffect(() => {
    if (!node.current) return
    const instance = new maplibregl.Map({
      container: node.current,
      center,
      zoom: 17,
      style: {
        version: 8,
        sources: {
          osm: {
            type: 'raster',
            tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],
            tileSize: 256,
            attribution: '© OpenStreetMap contributors',
          },
        },
        layers: [{ id: 'osm', type: 'raster', source: 'osm' }],
      },
    })
    map.current = instance
    instance.addControl(new maplibregl.NavigationControl(), 'top-right')
    instance.on('click', event => onPointRef.current([event.lngLat.lng, event.lngLat.lat]))
    const observer = new ResizeObserver(() => instance.resize())
    observer.observe(node.current)
    return () => {
      observer.disconnect()
      instance.remove()
      map.current = null
    }
  }, [])

  useEffect(() => {
    map.current?.flyTo({ center, zoom: 17 })
  }, [center[0], center[1]])

  useEffect(() => {
    const instance = map.current
    if (!instance) return
    const update = () => {
      const geometry = polygon.length >= 3
        ? { type: 'Polygon' as const, coordinates: [[...polygon, polygon[0]]] }
        : { type: 'LineString' as const, coordinates: polygon }
      const data: GeoJSON.Feature = { type: 'Feature', properties: {}, geometry }
      const source = instance.getSource('area') as maplibregl.GeoJSONSource | undefined
      if (source) {
        source.setData(data)
        return
      }
      instance.addSource('area', { type: 'geojson', data })
      instance.addLayer({ id: 'area-fill', type: 'fill', source: 'area', paint: { 'fill-color': '#4e90bd', 'fill-opacity': 0.24 } })
      instance.addLayer({ id: 'area-line', type: 'line', source: 'area', paint: { 'line-color': '#6fb4e0', 'line-width': 2 } })
    }
    if (instance.isStyleLoaded()) update()
    else instance.once('load', update)
  }, [polygon])

  return <div className="map-editor"><div ref={node} className="map" /><div className="map-tools"><b>ÁREA DO PROJETO</b><span>Clique no mapa para adicionar vértices · {polygon.length} pontos</span><div><button onClick={onUndo} disabled={!polygon.length}>Desfazer ponto</button><button onClick={onClear} disabled={!polygon.length}>Limpar área</button></div></div></div>
}
