import type { Project } from '../types'
import { polygonBounds } from '../geometry'

type Props = { project: Project; onChange(project: Project): void; onOptimize(): void; busy: boolean }
const weightFields: { key: keyof Project['weights']; label: string }[] = [
  { key: 'energy', label: 'Energia' },
  { key: 'agriculture', label: 'Produtividade agrícola' },
  { key: 'access', label: 'Acesso de máquinas' },
  { key: 'cost', label: 'Custo' },
]

export default function OptimizationControls({ project, onChange, onOptimize, busy }: Props) {
  const drawn = polygonBounds(project.polygon)
  const useDrawn = !!drawn && project.useDrawnArea !== false
  const manual = project.optimizationBounds || { width: 40, height: 30 }
  const effective = useDrawn ? drawn : manual
  const changeBounds = (key: 'width' | 'height', value: number) => onChange({ ...project, optimizationBounds: { ...manual, [key]: value } })
  return <section className="optimizer-controls">
    <h3>OTIMIZAÇÃO DO ARRANJO</h3>
    <p className="muted">Busca preliminar; a simulação completa valida o arranjo selecionado.</p>
    {drawn && <label className="toggle"><input type="checkbox" checked={useDrawn} onChange={event => onChange({ ...project, useDrawnArea: event.target.checked })} /> Usar limites desenhados no mapa</label>}
    <div className="twocol">
      <label>Largura da área (m)<input type="number" min="1" step="0.1" disabled={useDrawn} value={useDrawn ? effective.width.toFixed(1) : manual.width} onChange={event => changeBounds('width', Number(event.target.value))} /></label>
      <label>Comprimento da área (m)<input type="number" min="1" step="0.1" disabled={useDrawn} value={useDrawn ? effective.height.toFixed(1) : manual.height} onChange={event => changeBounds('height', Number(event.target.value))} /></label>
    </div>
    {drawn && <small className="muted">O otimizador usa o retângulo envolvente da área; confira as bordas na cena antes da execução.</small>}
    <h3>PRIORIDADES</h3>
    {weightFields.map(({ key, label }) => <label className="field" key={key}><span>{label}<b>{Math.round(project.weights[key] * 100)}%</b></span><input type="range" min="0" max="1" step="0.05" value={project.weights[key]} onChange={event => onChange({ ...project, weights: { ...project.weights, [key]: Number(event.target.value) } })} /></label>)}
    <button className="optimize" onClick={onOptimize} disabled={busy || effective.width <= 0 || effective.height <= 0}>Otimizar configuração</button>
  </section>
}
