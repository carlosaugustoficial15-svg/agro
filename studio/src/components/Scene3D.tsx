import { useEffect, useMemo, useRef, useState } from 'react'
import { Canvas, useThree } from '@react-three/fiber'
import { Grid, OrbitControls, Text } from '@react-three/drei'
import * as THREE from 'three'
import type { Layout } from '../types'
import { layoutGeometry } from '../geometry'

const MAX_PANELS = 2500

function CameraRig({ spanX, spanZ, height, reset }: { spanX: number; spanZ: number; height: number; reset: number }) {
  const { camera, controls, invalidate } = useThree()
  useEffect(() => {
    const distance = Math.max(12, spanX, spanZ) * 1.05 + 9
    const target = new THREE.Vector3(0, height / 2, 0)
    camera.position.set(distance * 0.75, distance * 0.72, distance * 0.83)
    camera.near = Math.max(0.05, distance / 10000)
    camera.far = Math.max(1000, distance * 8)
    camera.lookAt(target)
    camera.updateProjectionMatrix()
    const orbit = controls as { target?: THREE.Vector3; update?: () => void } | null
    orbit?.target?.copy(target)
    orbit?.update?.()
    invalidate()
  }, [camera, controls, height, invalidate, reset, spanX, spanZ])
  return null
}

function useCellTexture() {
  const texture = useMemo(() => {
    const canvas = document.createElement('canvas')
    canvas.width = 256
    canvas.height = 256
    const context = canvas.getContext('2d')!
    context.fillStyle = '#0e1d2b'
    context.fillRect(0, 0, 256, 256)
    for (let row = 0; row < 10; row++) for (let column = 0; column < 6; column++) {
      const x = 5 + column * 41
      const y = 5 + row * 25
      context.fillStyle = (row + column) % 3 === 0 ? '#294a64' : '#24425b'
      context.fillRect(x, y, 37, 21)
      context.fillStyle = '#375e79'
      context.fillRect(x + 2, y + 2, 33, 1)
    }
    const result = new THREE.CanvasTexture(canvas)
    result.colorSpace = THREE.SRGBColorSpace
    result.anisotropy = 4
    return result
  }, [])
  useEffect(() => () => texture.dispose(), [texture])
  return texture
}

function Panels({ layout, module }: { layout: Layout; module?: any }) {
  const frames = useRef<THREE.InstancedMesh>(null)
  const cells = useRef<THREE.InstancedMesh>(null)
  const texture = useCellTexture()
  const g = layoutGeometry(layout, module)
  const count = Math.min(MAX_PANELS, g.total)

  useEffect(() => {
    if (!frames.current || !cells.current) return
    const panel = new THREE.Object3D()
    const surfaceOffset = new THREE.Matrix4().makeTranslation(0, g.thickness / 2 + 0.008, 0)
    const surface = new THREE.Matrix4()
    const tilt = THREE.MathUtils.degToRad(Number(layout.tilt) || 0)
    const azimuth = THREE.MathUtils.degToRad(-(Number(layout.azimuth) || 0))
    for (let sample = 0; sample < count; sample++) {
      let index = count === 1 ? Math.floor((g.total - 1) / 2) : Math.floor(sample * (g.total - 1) / (count - 1))
      const panelY = index % g.panelsY
      index = Math.floor(index / g.panelsY)
      const panelX = index % g.panelsX
      index = Math.floor(index / g.panelsX)
      const blockY = index % g.blocksY
      const blockX = Math.floor(index / g.blocksY)
      panel.position.set((blockX - (g.blocksX - 1) / 2) * g.blockStepX + (panelX - (g.panelsX - 1) / 2) * g.panelStepX,
        Number(layout.height) || 2.5,
        (blockY - (g.blocksY - 1) / 2) * g.blockStepY + (panelY - (g.panelsY - 1) / 2) * g.panelStepY)
      panel.rotation.set(0, azimuth, tilt)
      panel.updateMatrix()
      frames.current.setMatrixAt(sample, panel.matrix)
      surface.multiplyMatrices(panel.matrix, surfaceOffset)
      cells.current.setMatrixAt(sample, surface)
    }
    frames.current.instanceMatrix.needsUpdate = true
    cells.current.instanceMatrix.needsUpdate = true
    frames.current.computeBoundingSphere()
    cells.current.computeBoundingSphere()
  }, [count, g.blocksX, g.blocksY, g.panelsX, g.panelsY, g.blockStepX, g.blockStepY, g.panelStepX, g.panelStepY, g.thickness, g.total, layout.azimuth, layout.height, layout.tilt])

  if (!count) return null
  return <>
    <instancedMesh key={`frames-${count}`} ref={frames} args={[undefined, undefined, count]} castShadow receiveShadow>
      <boxGeometry args={[g.width, g.thickness, g.depth]} />
      <meshStandardMaterial color="#aeb9bd" metalness={0.72} roughness={0.3} />
    </instancedMesh>
    <instancedMesh key={`cells-${count}`} ref={cells} args={[undefined, undefined, count]} castShadow receiveShadow>
      <boxGeometry args={[Math.max(0.1, g.width - 0.09), 0.016, Math.max(0.1, g.depth - 0.09)]} />
      <meshStandardMaterial map={texture} metalness={0.32} roughness={0.3} />
    </instancedMesh>
  </>
}

function Supports({ layout, module }: { layout: Layout; module?: any }) {
  const posts = useRef<THREE.InstancedMesh>(null)
  const rails = useRef<THREE.InstancedMesh>(null)
  const g = layoutGeometry(layout, module)
  const blocks = Math.min(400, g.blocksX * g.blocksY)
  const height = Math.max(0.2, Number(layout.height) || 2.5)
  const rowLength = (g.panelsY - 1) * g.panelStepY + g.depth
  useEffect(() => {
    if (!posts.current || !rails.current) return
    const object = new THREE.Object3D()
    for (let index = 0; index < blocks; index++) {
      const blockX = Math.floor(index / g.blocksY)
      const blockY = index % g.blocksY
      const x = (blockX - (g.blocksX - 1) / 2) * g.blockStepX
      const z = (blockY - (g.blocksY - 1) / 2) * g.blockStepY
      for (let end = 0; end < 2; end++) {
        object.position.set(x, height / 2, z + (end ? 0.35 : -0.35) * rowLength)
        object.updateMatrix()
        posts.current.setMatrixAt(index * 2 + end, object.matrix)
      }
      object.position.set(x, height - 0.1, z)
      object.updateMatrix()
      rails.current.setMatrixAt(index, object.matrix)
    }
    posts.current.instanceMatrix.needsUpdate = true
    rails.current.instanceMatrix.needsUpdate = true
  }, [blocks, g.blocksX, g.blocksY, g.blockStepX, g.blockStepY, height, rowLength])
  if (!blocks) return null
  return <>
    <instancedMesh key={`posts-${blocks}`} ref={posts} args={[undefined, undefined, blocks * 2]} castShadow>
      <cylinderGeometry args={[0.045, 0.06, height, 8]} />
      <meshStandardMaterial color="#88969b" metalness={0.65} roughness={0.42} />
    </instancedMesh>
    <instancedMesh key={`rails-${blocks}`} ref={rails} args={[undefined, undefined, blocks]} castShadow>
      <boxGeometry args={[0.09, 0.075, rowLength]} />
      <meshStandardMaterial color="#67767b" metalness={0.58} roughness={0.5} />
    </instancedMesh>
  </>
}

function Crops({ layout, module }: { layout: Layout; module?: any }) {
  const plants = useRef<THREE.InstancedMesh>(null)
  const g = layoutGeometry(layout, module)
  const lanes = g.blocksX > 1 ? g.blocksX - 1 : 2
  const steps = Math.min(80, Math.max(8, Math.floor(g.spanZ / 0.85)))
  const count = Math.min(2600, lanes * steps * 2)
  useEffect(() => {
    if (!plants.current) return
    const object = new THREE.Object3D()
    const color = new THREE.Color()
    for (let index = 0; index < count; index++) {
      const lane = Math.floor(index / (steps * 2))
      const within = index % (steps * 2)
      const step = Math.floor(within / 2)
      const side = within % 2
      const x = g.blocksX > 1 ? (lane - (g.blocksX - 2) / 2) * g.blockStepX + (side ? 0.38 : -0.38) : (side ? 1 : -1) * (g.width / 2 + 0.65)
      const z = (step - (steps - 1) / 2) * (g.spanZ / Math.max(1, steps - 1))
      const scale = 0.8 + (index % 5) * 0.06
      object.position.set(x, 0.28 * scale, z)
      object.scale.set(scale, scale, scale)
      object.rotation.y = (index % 7) * 0.26
      object.updateMatrix()
      plants.current.setMatrixAt(index, object.matrix)
      color.set(index % 4 ? '#657d49' : '#879663')
      plants.current.setColorAt(index, color)
    }
    plants.current.instanceMatrix.needsUpdate = true
    if (plants.current.instanceColor) plants.current.instanceColor.needsUpdate = true
  }, [count, g.blocksX, g.blockStepX, g.spanZ, g.width, steps])
  return <instancedMesh key={`crops-${count}`} ref={plants} args={[undefined, undefined, count]} castShadow>
    <coneGeometry args={[0.18, 0.56, 4]} />
    <meshStandardMaterial color="#c4d8a2" roughness={0.92} side={THREE.DoubleSide} />
  </instancedMesh>
}

function CropBeds({ layout, module }: { layout: Layout; module?: any }) {
  const beds = useRef<THREE.InstancedMesh>(null)
  const g = layoutGeometry(layout, module)
  const lanes = Math.min(400, g.blocksX > 1 ? g.blocksX - 1 : 2)
  useEffect(() => {
    if (!beds.current) return
    const object = new THREE.Object3D()
    for (let lane = 0; lane < lanes; lane++) {
      const x = g.blocksX > 1 ? (lane - (g.blocksX - 2) / 2) * g.blockStepX : (lane ? 1 : -1) * (g.width / 2 + 0.65)
      object.position.set(x, 0.038, 0)
      object.updateMatrix()
      beds.current.setMatrixAt(lane, object.matrix)
    }
    beds.current.instanceMatrix.needsUpdate = true
  }, [g.blocksX, g.blockStepX, g.width, lanes])
  return <instancedMesh key={`beds-${lanes}`} ref={beds} args={[undefined, undefined, lanes]} receiveShadow>
    <boxGeometry args={[1.05, 0.025, g.spanZ + 1]} />
    <meshStandardMaterial color="#45563a" roughness={1} />
  </instancedMesh>
}

export default function Scene3D({ layout, module, sun, mode = 'agriculture' }: { layout: Layout; module?: any; sun: any; mode?: 'agriculture' | 'roof' }) {
  const [reset, setReset] = useState(0)
  const [showGrid, setShowGrid] = useState(true)
  const webglAvailable = useMemo(() => {
    try {
      const canvas = document.createElement('canvas')
      return !!(canvas.getContext('webgl2') || canvas.getContext('webgl'))
    } catch { return false }
  }, [])
  const g = layoutGeometry(layout, module)
  const azimuth = THREE.MathUtils.degToRad(sun?.azimuth ?? 180)
  const elevation = THREE.MathUtils.degToRad(sun?.elevation ?? 35)
  const sunPosition: [number, number, number] = [80 * Math.sin(azimuth) * Math.cos(elevation), 80 * Math.sin(elevation), 80 * Math.cos(azimuth) * Math.cos(elevation)]
  const groundSize = Math.max(100, g.spanX * 1.3, g.spanZ * 1.3)
  const shadowExtent = Math.max(25, g.spanX, g.spanZ) * 0.65
  if (!webglAvailable) return <div className="scene-fallback"><b>Visualização 3D indisponível</b><span>Seu dispositivo não oferece WebGL. Os controles e a simulação continuam disponíveis; use “Mapa e área” para visualizar o local.</span></div>
  return <div className="scene-frame">
    <Canvas shadows dpr={[1, 1.5]} camera={{ position: [22, 20, 28], fov: 48 }} gl={{ antialias: true, powerPreference: 'high-performance' }}>
      <color attach="background" args={['#1b252c']} />
      <fog attach="fog" args={['#1b252c', groundSize * 0.7, groundSize * 2.2]} />
      <hemisphereLight args={['#d6e9f4', '#40553e', 1.15]} />
      <ambientLight intensity={0.5} />
      <directionalLight position={sunPosition} intensity={sun?.elevation < 0 ? 0.15 : 1.7} color="#f7e8c8" castShadow shadow-mapSize={[2048, 2048]} shadow-camera-left={-shadowExtent} shadow-camera-right={shadowExtent} shadow-camera-top={shadowExtent} shadow-camera-bottom={-shadowExtent} shadow-camera-near={0.1} shadow-camera-far={220} shadow-bias={-0.0003} />
      {sun?.elevation > 0 && <mesh position={[sunPosition[0] * 0.55, sunPosition[1] * 0.55, sunPosition[2] * 0.55]}><sphereGeometry args={[1.1, 16, 12]} /><meshBasicMaterial color="#ffdf96" /></mesh>}
      <mesh rotation={[-Math.PI / 2, 0, 0]} receiveShadow><planeGeometry args={[groundSize, groundSize]} /><meshStandardMaterial color={mode === 'roof' ? '#323b3f' : '#2b382d'} roughness={1} /></mesh>
      <mesh position={[0, 0.014, 0]} receiveShadow><boxGeometry args={[g.spanX + 7, mode === 'roof' ? 0.13 : 0.025, g.spanZ + 7]} /><meshStandardMaterial color={mode === 'roof' ? '#626b6d' : '#344339'} roughness={1} /></mesh>
      {showGrid && <Grid args={[groundSize, groundSize]} cellSize={1} sectionSize={10} fadeDistance={groundSize * 0.9} position={[0, 0.03, 0]} cellColor="#42544d" sectionColor="#70867c" />}
      {mode === 'agriculture' && <CropBeds layout={layout} module={module} />}
      {mode === 'agriculture' && <Crops layout={layout} module={module} />}
      <Supports layout={layout} module={module} />
      <Panels layout={layout} module={module} />
      <Text position={[0, 0.25, -Math.max(9, g.spanZ / 2 + 4)]} color="#dbe4df" fontSize={0.8}>N</Text>
      <OrbitControls makeDefault maxPolarAngle={Math.PI / 2.02} target={[0, Number(layout.height) / 2 || 1, 0]} />
      <CameraRig spanX={g.spanX} spanZ={g.spanZ} height={Number(layout.height) || 2.5} reset={reset} />
    </Canvas>
    <div className="scene-controls"><button onClick={() => setReset(value => value + 1)}>Reenquadrar</button><button onClick={() => setShowGrid(value => !value)}>{showGrid ? 'Ocultar grade' : 'Mostrar grade'}</button></div>
    <div className="scene-legend"><b>{mode === 'roof' ? 'PRÉVIA DE TELHADO — SEM CÁLCULO' : 'PRÉVIA GEOMÉTRICA'}</b><span>Arraste para orbitar · roda para aproximar</span>{mode === 'agriculture' && <small>Vegetação ilustrativa; resultados somente após executar</small>}{g.total > MAX_PANELS && <small>{MAX_PANELS} de {g.total} módulos exibidos</small>}</div>
  </div>
}
