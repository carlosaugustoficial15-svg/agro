import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from './api'
import type { Project } from './types'
import Scene3D from './components/Scene3D'
import MapView from './components/MapView'
import OptimizationControls from './components/OptimizationControls'
import { layoutGeometry, polygonBounds } from './geometry'

const initial: Project = {
  schemaVersion: 1, name: 'Nova simulação',
  site: { name: 'Gembloux', latitude: 50.565114, longitude: 4.702089, altitude: 165, timezone: 'Europe/Brussels', mode: 'agriculture' },
  layout: { tilt: 20, azimuth: 180, spacing: 6, rows: 5, panelsPerRow: 12, height: 2.5 },
  weights: { energy: .35, agriculture: .35, access: .2, cost: .1 }, polygon: [], optimizationBounds: { width: 40, height: 30 }, useDrawnArea: true, electricalModel: 'simple',
  siteParameters: {}, structure: {}, agriculture: { model: 'simple' }, updatedAt: new Date().toISOString()
}
const Slider = ({ label, value, min, max, step = 1, onChange, unit = '' }: any) => <label className="field"><span>{label}<b>{value}{unit}</b></span><input type="range" min={min} max={max} step={step} value={value} onChange={e => onChange(+e.target.value)} /></label>
type Section = 'layout' | 'site' | 'pv' | 'crop' | 'management' | 'advanced'

export default function App() {
  const [project, setProject] = useState<Project>(initial), [view, setView] = useState<'3d' | 'map'>('3d'), [section, setSection] = useState<Section>('layout')
  const [query, setQuery] = useState(''), [results, setResults] = useState<any[]>([]), [sun, setSun] = useState<any>(), [hour, setHour] = useState(12), [previewDate, setPreviewDate] = useState(new Date().toISOString().slice(0, 10))
  const [modules, setModules] = useState<any[]>([]), [moduleQuery, setModuleQuery] = useState(''), [status, setStatus] = useState('Pronto'), [job, setJob] = useState<any>(), [advanced, setAdvanced] = useState(false)
  const [apiConfig, setApiConfig] = useState<any>()
  const openedProject = useRef(false)
  const patchLayout = (changes: Partial<Project['layout']>) => setProject(current => {
    const layout = { ...current.layout, ...changes }
    if (changes.rows !== undefined) layout.blocksX = changes.rows
    if (changes.blocksX !== undefined) layout.rows = changes.blocksX
    if (changes.panelsPerRow !== undefined) layout.panelsY = changes.panelsPerRow
    if (changes.panelsY !== undefined) layout.panelsPerRow = changes.panelsY
    if (changes.spacing !== undefined) layout.blockSpacingX = changes.spacing
    if (changes.blockSpacingX !== undefined) layout.spacing = changes.blockSpacingX
    return { ...current, layout, updatedAt: new Date().toISOString() }
  })
  const patchAg = (group: string, p: any) => setProject(v => ({ ...v, agriculture: { ...(v.agriculture || initial.agriculture), [group]: Array.isArray(p) || p === null || typeof p !== 'object' ? p : { ...((v.agriculture as any)?.[group] || {}), ...p } }, updatedAt: new Date().toISOString() }))
  const timestamp = useMemo(() => `${previewDate}T${String(Math.floor(hour)).padStart(2, '0')}:${String(Math.round((hour % 1) * 60)).padStart(2, '0')}:00`, [hour, previewDate])
  useEffect(() => { api.solar({ latitude: project.site.latitude, longitude: project.site.longitude, altitude: project.site.altitude, timezone: project.site.timezone, timestamp, tilt: project.layout.tilt, azimuth: project.layout.azimuth }).then(setSun).catch(() => {}) }, [project.site, project.layout.tilt, project.layout.azimuth, timestamp])
  useEffect(() => { api.config().then(c => {
    setApiConfig(c)
    if (openedProject.current) return
    setProject(p => ({ ...p,
      siteParameters: c.site,
      layout: { ...p.layout, tilt: c.layout.TiltY, azimuth: c.layout.CentralAzimut, height: c.layout.Height, spacing: c.layout.RepetitionDistanceOfPVBlocksX, rows: c.layout.NumberOfPVBlocksX, panelsPerRow: c.layout.NumberOfPanelsY, panelsX: c.layout.NumberOfPanelsX, panelsY: c.layout.NumberOfPanelsY, panelSpacingX: c.layout.RepetitionDistanceOfPanelsX, panelSpacingY: c.layout.RepetitionDistanceOfPanelsY, blocksX: c.layout.NumberOfPVBlocksX, blocksY: c.layout.NumberOfPVBlocksY, blockSpacingX: c.layout.RepetitionDistanceOfPVBlocksX, blockSpacingY: c.layout.RepetitionDistanceOfPVBlocksY, rotationAxisNumber: c.layout.RotationAxisNumber, hinge: c.layout.Hinge },
      module: { ...c.module, Length: c.module.PanelDimensionX, Width: c.module.PanelDimensionY, STC: c.module.Panel_Peak_Power, Bifacial: c.module.Bifaciality },
      structure: c.structure,
      agriculture: { ...c.agriculture, option2D: 0, model: c.agriculture.simpleConfig.CropModel || 'simple' },
    }))
  }).catch((e: any) => setStatus(`Não foi possível carregar os parâmetros do PASE: ${e.message}`)) }, [])
  useEffect(() => { if (!moduleQuery) return; const t = setTimeout(() => api.modules(moduleQuery).then(r => setModules(r.items)).catch(() => setModules([])), 300); return () => clearTimeout(t) }, [moduleQuery])
  const search = async () => { setStatus('Buscando localização…'); try { setResults((await api.search(query)).items); setStatus('Escolha um resultado para confirmar') } catch (e: any) { setStatus(e.message) } }
  const choose = async (r: any) => { setStatus('Obtendo altitude, clima e fuso…'); try { const enriched = await api.enrich(r.latitude, r.longitude); setProject(p => ({ ...p, site: { ...p.site, name: r.name, latitude: r.latitude, longitude: r.longitude, altitude: enriched.altitude.value, timezone: enriched.timezone.value, enriched }, siteParameters: { ...(p.siteParameters || {}), Latitude: r.latitude, Longitude: r.longitude, Altitude: enriched.altitude.value, TimeZone: enriched.timezone.value } })); setResults([]); setStatus('Local e dados climáticos carregados') } catch (e: any) { setStatus(e.message) } }
  const optimize = async () => {
    if (project.site.mode === 'roof') { setStatus('A otimização de telhados ainda não é suportada pelo motor agrivoltaico.'); return }
    setStatus('Otimizando arranjo…')
    try {
      const bounds = project.useDrawnArea !== false && polygonBounds(project.polygon) || project.optimizationBounds || { width: 40, height: 30 }
      const candidate = await api.optimize({ bounds, panelWidth: project.module?.Length || project.module?.PanelDimensionX || 2.384, panelHeight: project.module?.Width || project.module?.PanelDimensionY || 1.303, weights: project.weights })
      const { tilt, azimuth, spacing, rows, panelsPerRow } = candidate.best
      patchLayout({ tilt, azimuth, spacing, rows, panelsPerRow })
      setStatus('Candidato aplicado; execute a simulação para validação completa')
    } catch (error: any) { setStatus(error.message) }
  }
  const run = async () => {
    if (project.site.mode === 'roof') { setStatus('O motor atual simula culturas em terreno. Telhados ainda não são executáveis.'); return }
    setStatus('Iniciando simulação…'); setJob(null)
    try {
      const started = await api.run(project); setJob(started)
      const timer = setInterval(async () => {
        try {
          const next = await api.job(started.id)
          setJob(next)
          const labels: Record<string, string> = { queued: 'na fila', running: 'em execução', cancelling: 'cancelando', cancelled: 'cancelada', completed: 'concluída' }
          setStatus(next.status === 'failed' ? `Falha: ${next.error || 'consulte os logs'}` : `Simulação ${labels[next.status] || next.status} — ${next.progress}%`)
          if (['completed', 'failed', 'cancelled'].includes(next.status)) clearInterval(timer)
        } catch (error: any) { clearInterval(timer); setStatus(error.message) }
      }, 900)
    } catch (e: any) { setStatus(`Falha ao iniciar: ${e.message}`) }
  }
  const cancel = async () => {
    if (!job?.id) return
    try { setJob(await api.cancel(job.id)); setStatus('Cancelando simulação…') }
    catch (error: any) { setStatus(`Não foi possível cancelar: ${error.message}`) }
  }
  const save = async () => { try { const path = await window.studio.saveProject(project); if (path) setStatus('Projeto salvo com sucesso') } catch (error: any) { setStatus(`Não foi possível salvar: ${error.message}`) } }
  const open = async () => { try { const p = await window.studio.openProject(); if (p) { openedProject.current = true; setProject({ ...initial, ...p, site: { ...initial.site, ...(p.site || {}) }, layout: { ...initial.layout, ...(p.layout || {}) }, weights: { ...initial.weights, ...(p.weights || {}) }, polygon: Array.isArray(p.polygon) ? p.polygon : [], agriculture: { ...initial.agriculture, ...(p.agriculture || {}) }, optimizationBounds: { ...initial.optimizationBounds, ...(p.optimizationBounds || {}) } }); setStatus('Projeto aberto') } } catch (error: any) { setStatus(`Não foi possível abrir: ${error.message}`) } }
  const cropModel = project.agriculture?.model || 'simple'
  const previewModuleCount = layoutGeometry(project.layout, project.module).total
  const editValues = (group: string, fallback: Record<string, any> = {}) => {
    const source = group === 'siteParameters' ? apiConfig?.site : group === 'structure' ? apiConfig?.structure : apiConfig?.agriculture?.[group]
    const specs = group === 'siteParameters' ? apiConfig?.metadata?.site : group === 'structure' ? apiConfig?.metadata?.structure : apiConfig?.metadata?.agriculture?.[group]
    const saved = group === 'siteParameters' ? project.siteParameters : group === 'structure' ? project.structure : (project.agriculture as any)?.[group]
    const values = { ...(source || fallback), ...(saved || {}) }
    const update = (key: string, value: any) => {
      if (group === 'siteParameters') setProject(p => {
        const siteField = ({ Latitude: 'latitude', Longitude: 'longitude', Altitude: 'altitude', TimeZone: 'timezone', LocationName: 'name' } as const)[key as 'Latitude' | 'Longitude' | 'Altitude' | 'TimeZone' | 'LocationName']
        return { ...p, siteParameters: { ...(p.siteParameters || {}), [key]: value }, site: siteField ? { ...p.site, [siteField]: value } : p.site }
      })
      else if (group === 'structure') setProject(p => ({ ...p, structure: { ...(p.structure || {}), [key]: value } }))
      else patchAg(group, { [key]: value })
    }
    return <div className="ag-fields">{Object.entries(values).map(([key, value]) => {
      if (value === undefined || value === null || (typeof value === 'object' && !Array.isArray(value))) return null
      const spec = specs?.[key] || {}, options = spec.options as any[] | undefined
      const description = [spec.definition, spec.unit].filter(Boolean).join(' · ')
      const limits = Array.isArray(spec.limits) ? spec.limits.map(Number) : []
      const numericLimits = limits.length === 2 && limits.every(Number.isFinite) ? limits : []
      const parseOption = (option: any) => spec.type === 'boolean' ? option === true || String(option).toLowerCase() === 'true' : typeof value === 'number' ? Number(option) : String(option)
      return <label className="ag-field" key={key} title={description}><span title={description || key}>{key}{spec.unit ? ` (${spec.unit})` : ''}</span>{typeof value === 'boolean' ? <input type="checkbox" checked={value} onChange={e => update(key, e.target.checked)} /> : options?.length ? <select value={String(value)} onChange={e => update(key, parseOption(options.find(option => String(option) === e.target.value) ?? e.target.value))}>{options.map(option => <option key={String(option)} value={String(option)}>{String(option)}</option>)}</select> : Array.isArray(value) ? <input type="text" value={value.join(', ')} placeholder="Separe valores por vírgula" onChange={e => update(key, e.target.value.split(',').map(item => item.trim()).filter(Boolean))} /> : <input type={typeof value === 'number' ? 'number' : 'text'} min={numericLimits[0]} max={numericLimits[1]} step={typeof value === 'number' && !Number.isInteger(value) ? 'any' : '1'} value={String(value)} onChange={e => update(key, typeof value === 'number' ? Number(e.target.value) : e.target.value)} />}</label>
    })}</div>
  }
  const editRecord = (group: string, title: string) => {
    const records = ((project.agriculture as any)?.[group] || apiConfig?.agriculture?.[group] || []) as Record<string, any>[]
    if (!records.length) return <p className="muted">Tabela indisponível.</p>
    const selected = group === 'simpleParameters'
      ? Math.max(0, records.findIndex(row => Number(row.id) === Number((project.agriculture as any)?.simpleCrop?.CropID ?? 1)))
      : 0
    const row = records[selected] || records[0]
    return <details className="csv-editor"><summary>{title} · {row.Crop || row.Cultivar || row.PFT || `linha ${selected + 1}`}</summary><div className="ag-fields">{Object.entries(row).map(([key, value]) => <label className="ag-field" key={key}><span title={key}>{key}</span><input type={typeof value === 'number' ? 'number' : 'text'} step={typeof value === 'number' && !Number.isInteger(value) ? 'any' : '1'} value={String(value)} onChange={e => { const next = [...records]; next[selected] = { ...row, [key]: typeof value === 'number' ? Number(e.target.value) : e.target.value }; patchAg(group, next as any) }} /></label>)}</div><small className="muted">Os parâmetros da linha selecionada são gravados na cópia desta simulação.</small></details>
  }
  const panelEditor = (fields: [string, number, number, number, string][]) => {
    const aliases: Record<string, string> = { Length: 'PanelDimensionX', Width: 'PanelDimensionY', STC: 'Panel_Peak_Power' }
    return <div className="manual-fields">{fields.map(([label, min, max, step, key]) => <label key={key}>{label}<input type="number" step={step} min={min} max={max} value={(project.module as any)?.[key] ?? apiConfig?.module?.[aliases[key] || key] ?? 0} onChange={e => setProject(p => ({ ...p, module: { ...(p.module || {}), [key]: Number(e.target.value) } }))} /></label>)}</div>
  }
  const selectModule = (item: any) => setProject(current => ({ ...current, module: { ...(current.module || {}), ...item } }))
  return <main className="app-shell"><header className="app-header"><div className="brand"><div className="logo">P</div><div><h1>PASE Studio</h1><small>Ambiente de simulação agrivoltaica</small></div></div><label className="project-control"><span>PROJETO ATUAL</span><input className="project-name" value={project.name} onChange={e => setProject({ ...project, name: e.target.value })} /></label><div className="actions"><button onClick={open}>Abrir</button><button onClick={save}>Salvar</button>{['queued', 'running', 'cancelling'].includes(job?.status) && <button className="cancel-action" onClick={cancel} disabled={job?.status === 'cancelling'}>Cancelar</button>}<button className="primary" onClick={run} disabled={project.site.mode === 'roof' || ['queued', 'running', 'cancelling'].includes(job?.status)} title={project.site.mode === 'roof' ? 'O modo telhado ainda não está disponível para a simulação agrícola.' : undefined}>Executar simulação</button></div></header>
    <section className="workspace"><aside className="left"><div className="panel-heading"><b>Explorador</b><span>CENÁRIO</span></div><h3>LOCALIZAÇÃO</h3><div className="search"><input placeholder="Cidade, fazenda ou endereço" value={query} onChange={e => setQuery(e.target.value)} onKeyDown={e => e.key === 'Enter' && search()} /><button onClick={search} aria-label="Buscar localização">Buscar</button></div>{results.map((r, i) => <button className="result" key={i} onClick={() => choose(r)}>{r.name}<small>{r.source} · {r.cache}</small></button>)}<div className="site-card"><b>{project.site.name}</b><span>{project.site.latitude.toFixed(5)}, {project.site.longitude.toFixed(5)}</span><span>{project.site.altitude} m · {project.site.timezone}</span></div>
      <h3>COMPONENTES</h3>{([['layout', 'Arranjo fotovoltaico'], ['site', 'Local e clima'], ['pv', 'Módulo fotovoltaico'], ['crop', 'Cultura e solo'], ['management', 'Manejo agrícola'], ['advanced', 'Parâmetros avançados']] as [Section, string][]).map(([id, label]) => <button key={id} className={`tree-button ${section === id ? 'active' : ''}`} onClick={() => setSection(id)}>{label}</button>)}
      <h3>TIPO DE LOCAL</h3><div className="seg"><button className={project.site.mode === 'agriculture' ? 'active' : ''} onClick={() => setProject({ ...project, site: { ...project.site, mode: 'agriculture' } })}>Terreno</button><button className={project.site.mode === 'roof' ? 'active' : ''} onClick={() => setProject({ ...project, site: { ...project.site, mode: 'roof' } })}>Telhado</button></div>{project.site.mode === 'roof' && <p className="warning">Prévia de telhado: o motor agrícola ainda não calcula este modo. A execução fica indisponível.</p>}
    </aside>
      <section className={`center ${job ? 'has-job' : ''}`}>
        <nav><button className={view === '3d' ? 'active' : ''} onClick={() => setView('3d')}>Cena 3D</button><button className={view === 'map' ? 'active' : ''} onClick={() => setView('map')}>Mapa e área</button><span className="quality">{previewModuleCount} módulos · prévia em tempo real</span></nav>
        <div className="viewport">
          {view === '3d' ? <Scene3D layout={project.layout} module={project.module} sun={sun} mode={project.site.mode} /> : <MapView center={[project.site.longitude, project.site.latitude]} polygon={project.polygon} onPoint={point => setProject(current => ({ ...current, polygon: [...current.polygon, point] }))} onUndo={() => setProject(current => ({ ...current, polygon: current.polygon.slice(0, -1) }))} onClear={() => setProject(current => ({ ...current, polygon: [] }))} />}
          <div className="sun-card"><b>{sun?.elevation?.toFixed(1) ?? '--'}°</b><span>ELEVAÇÃO SOLAR</span><small>Azimute {sun?.azimuth?.toFixed(1) ?? '--'}° · Incidência {sun?.incidence?.toFixed(1) ?? '--'}°</small></div>
        </div>
        <div className="timeline"><input className="preview-date" type="date" aria-label="Data da prévia solar" value={previewDate} onChange={event => setPreviewDate(event.target.value)} /><span>06:00</span><input type="range" min="6" max="20" step=".25" value={hour} onChange={event => setHour(Number(event.target.value))} /><span>20:00</span><b>{String(Math.floor(hour)).padStart(2, '0')}:{String(Math.round((hour % 1) * 60)).padStart(2, '0')}</b></div>
        <div className="status" aria-live="polite"><span title={status}>{status}</span>{job && <progress max="100" value={job.progress} />}</div>
        {job && <div className={`job-details ${job.status === 'failed' ? 'job-error' : ''}`}>
          <b>{job.status === 'failed' ? 'Falha na execução' : job.status === 'completed' ? 'Resultados da simulação' : job.status === 'cancelled' ? 'Simulação cancelada' : 'Monitor da simulação'}</b>
          {job.error && <p>{job.error}</p>}
          {job.status === 'completed' && job.results && <div className="result-summary">{Object.entries(job.results.pvMWhByYear || {}).map(([year, energy]) => <span key={year}>Energia FV {year}: <strong>{Number(energy).toFixed(2)} MWh</strong></span>)}<span>Modelo agrícola {String(job.results.cropModel).toUpperCase()}: <strong>{job.results.cropCompleted ? 'concluído' : 'não concluído'}</strong></span>{job.results.freshYieldOctoberGm2 !== undefined && <span>Produção fresca média (10/10): <strong>{Number(job.results.freshYieldOctoberGm2).toFixed(1)} g/m²</strong></span>}</div>}
          {job.lastStage && job.status !== 'completed' && <small className="job-stage">{job.lastStage}</small>}
          <details open={job.status === 'failed'}><summary>Detalhes técnicos da execução</summary><pre>{(job.log || []).slice(-80).join('\n')}</pre></details>
        </div>}
      </section>
      <aside className="right"><h2>Inspetor</h2>
        {section === 'layout' && <><p className="muted">Geometria e rastreamento</p><Slider label="Inclinação" value={project.layout.tilt} min={-90} max={90} onChange={(tilt: number) => patchLayout({ tilt })} unit="°" /><Slider label="Azimute central" value={project.layout.azimuth} min={0} max={360} onChange={(azimuth: number) => patchLayout({ azimuth })} unit="°" /><Slider label="Altura" value={project.layout.height} min={.1} max={20} step={.1} onChange={(height: number) => patchLayout({ height })} unit="m" /><Slider label="Distância entre fileiras" value={project.layout.spacing} min={1} max={100} step={.1} onChange={(spacing: number) => patchLayout({ spacing })} unit="m" /><div className="twocol"><label>Fileiras/blocos<input type="number" min="0" max="1000" value={project.layout.rows} onChange={e => patchLayout({ rows: +e.target.value })} /></label><label>Painéis por bloco<input type="number" min="1" max="1000" value={project.layout.panelsPerRow} onChange={e => patchLayout({ panelsPerRow: +e.target.value })} /></label></div><label className="toggle"><input type="checkbox" checked={advanced} onChange={e => setAdvanced(e.target.checked)} /> Mostrar mais controles geométricos</label>{advanced && <><div className="twocol"><label>Painéis eixo X<input type="number" value={project.layout.panelsX ?? 1} onChange={e => patchLayout({ panelsX: +e.target.value })} /></label><label>Painéis eixo Y<input type="number" value={project.layout.panelsY ?? project.layout.panelsPerRow} onChange={e => patchLayout({ panelsY: +e.target.value })} /></label><label>Blocos eixo X<input type="number" value={project.layout.blocksX ?? project.layout.rows} onChange={e => patchLayout({ blocksX: +e.target.value })} /></label><label>Blocos eixo Y<input type="number" value={project.layout.blocksY ?? 1} onChange={e => patchLayout({ blocksY: +e.target.value })} /></label><label>Passo painel X (m)<input type="number" step=".01" value={project.layout.panelSpacingX ?? 2.45} onChange={e => patchLayout({ panelSpacingX: +e.target.value })} /></label><label>Passo painel Y (m)<input type="number" step=".01" value={project.layout.panelSpacingY ?? 1.31} onChange={e => patchLayout({ panelSpacingY: +e.target.value })} /></label><label>Passo bloco X (m)<input type="number" step=".01" value={project.layout.blockSpacingX ?? project.layout.spacing} onChange={e => patchLayout({ blockSpacingX: +e.target.value })} /></label><label>Passo bloco Y (m)<input type="number" step=".01" value={project.layout.blockSpacingY ?? 20} onChange={e => patchLayout({ blockSpacingY: +e.target.value })} /></label></div><label className="select-field">Eixos de rastreamento<select value={project.layout.rotationAxisNumber ?? 0} onChange={e => patchLayout({ rotationAxisNumber: +e.target.value })}><option value={0}>Fixo</option><option value={1}>Um eixo</option></select></label><label className="select-field">Dobradiça<select value={project.layout.hinge ?? 'Top'} onChange={e => patchLayout({ hinge: e.target.value })}><option>Top</option><option>Center</option></select></label></>}</>}
        {section === 'site' && <><p className="muted">Todos os campos do cenário e do clima entram no arquivo da execução.</p>{editValues('siteParameters')}</>}
        {section === 'pv' && <><p className="muted">Catálogo CEC, propriedades do módulo e modelo elétrico DC.</p><label className="select-field">Modelo elétrico<select value={project.electricalModel} onChange={e => setProject({ ...project, electricalModel: e.target.value as any })}><option value="simple">PASE simplificado</option><option value="cec">CEC — diodo único (pvlib)</option></select></label>{project.electricalModel === 'cec' && <p className="hint">O modelo CEC usa temperatura e irradiância calculadas pelo PASE e os sete parâmetros da ficha técnica. O resultado é potência DC por módulo, sem modelo de inversor.</p>}<input placeholder="Buscar fabricante ou modelo no catálogo CEC" value={moduleQuery} onChange={e => setModuleQuery(e.target.value)} /><div className="catalog">{modules.slice(0, 8).map(m => <button key={m.id} onClick={() => selectModule(m)}><b>{m.name}</b><small>{m.STC || '?'} W · {m.Technology || 'CEC'}</small></button>)}</div>{project.module && <div className="selected"><b>{project.module.name || 'Módulo manual'}</b><span>{project.module.STC || apiConfig?.module?.Panel_Peak_Power} W · {project.module.A_c || 'área não informada'} m²</span></div>}<h3>ESPECIFICAÇÃO MANUAL</h3>{panelEditor([['Comprimento X (m)', .1, 5, .01, 'Length'], ['Largura Y (m)', .1, 5, .01, 'Width'], ['Potência STC (W)', 1, 2000, 1, 'STC'], ['Área ativa (m²)', .01, 30, .01, 'A_c'], ['Isc de referência (A)', .01, 50, .001, 'I_sc_ref'], ['Voc de referência (V)', .01, 200, .01, 'V_oc_ref'], ['Imp de referência (A)', .01, 50, .001, 'I_mp_ref'], ['Vmp de referência (V)', .01, 200, .01, 'V_mp_ref'], ['alpha_sc (A/°C)', -.2, .2, .00001, 'alpha_sc'], ['a_ref (V)', .01, 20, .001, 'a_ref'], ['I_L_ref (A)', .001, 100, .001, 'I_L_ref'], ['I_o_ref (A)', .0000000001, 1, .000000001, 'I_o_ref'], ['R_sh_ref (Ω)', .01, 5000, .01, 'R_sh_ref'], ['R_s (Ω)', 0, 20, .001, 'R_s'], ['Adjust', -50, 50, .001, 'Adjust'], ['Coeficiente bifacial', 0, 1, .01, 'Bifaciality_factor']])}<label className="toggle"><input type="checkbox" checked={!!project.module?.Bifacial} onChange={e => setProject({ ...project, module: { ...(project.module || {}), Bifacial: e.target.checked } })} /> Módulo bifacial</label></>}
        {section === 'crop' && <><p className="muted">Parâmetros reais dos modelos presentes no PASE; edite livremente antes da execução.</p><label className="select-field">Modelo agrícola<select value={cropModel} onChange={e => setProject(p => ({ ...p, agriculture: { ...p.agriculture, model: e.target.value } }))}><option value="simple">SIMPLE</option><option value="grassim">GRASSIM</option><option value="stics">STICS (requer JavaStics)</option><option value="pystics">pySTICS (requer dependências)</option></select></label>{cropModel === 'simple' && <><h3>RESOLUÇÃO AGRÍCOLA</h3><label className="select-field">Cálculo espacial<select value={project.agriculture.option2D ?? 0} onChange={e => patchAg('option2D', Number(e.target.value))}><option value={0}>Integrado (rápido)</option><option value={1}>Mapa 2D detalhado (mais lento)</option></select></label><p className="hint">O mapa 2D pode exigir bastante memória e tempo em terrenos grandes. Você pode começar no modo integrado e ativar a resolução detalhada quando precisar de mapas espaciais.</p><h3>CONFIGURAÇÃO SIMPLE</h3>{editValues('simpleConfig')}<h3>ESTADO INICIAL DA CULTURA</h3>{editValues('simpleCrop')}<h3>PARÂMETROS DA CULTIVAR</h3>{editRecord('simpleParameters', 'Tabela completa de cultivares')}<h3>SOLO E ÁGUA</h3>{editValues('simpleSoil')}</>}{cropModel === 'grassim' && <><h3>CONFIGURAÇÃO GRASSIM</h3>{editValues('grassimConfig')}<h3>CULTURA E FENOLOGIA</h3>{editValues('grassimCrop')}<h3>SOLO</h3>{editValues('grassimSoil')}<h3>PROPRIEDADES HIDRÁULICAS</h3>{editValues('grassimSoilParameters')}<h3>COMPOSIÇÃO DE ESPÉCIES (PFT)</h3>{editValues('grassimPftComposition')}<h3>PARÂMETROS DAS ESPÉCIES</h3>{editRecord('grassimPftParameters', 'Tabela de parâmetros por espécie')}<h3>COEFICIENTE DE CULTURA</h3>{editValues('grassimKcValues')}</>}{cropModel === 'stics' && <>{editValues('sticsConfig')}{editValues('stics')}<p className="warning">STICS necessita do JavaSticsCmd instalado para executar.</p></>}{cropModel === 'pystics' && <>{editValues('pysticsConfig')}{editValues('pystics')}<p className="warning">pySTICS necessita das dependências e entradas compatíveis instaladas.</p></>}</>}
        {section === 'management' && <><p className="muted">Calendário e decisões de corte, pastejo, rotação e fertilização.</p>{cropModel === 'grassim' ? <>{editValues('grassimManagement')}<h3>FREQUÊNCIA DO MANEJO</h3>{editValues('grassimManagementFrequency')}</> : <><p className="warning">Os arquivos de manejo detalhado pertencem ao GRASSIM. Troque o modelo agrícola para editar e usar esses controles.</p>{cropModel === 'simple' && <>{editValues('simpleCrop')}</>}</>}</>}
        {section === 'advanced' && <><p className="muted">Entradas que controlam a simulação PASE e sua geometria.</p><h3>ESTRUTURA E SUPORTES</h3>{editValues('structure')}<h3>MODELO AGRÍCOLA — OUTRAS ENTRADAS</h3>{cropModel === 'grassim' ? <>{editValues('grassimConfig')}{editValues('grassimCrop')}{editValues('grassimSoil')}{editValues('grassimSoilParameters')}{editValues('grassimPftComposition')}{editRecord('grassimPftParameters', 'Tabela de parâmetros por espécie')}{editValues('grassimKcValues')}{editValues('grassimManagementFrequency')}</> : cropModel === 'simple' ? <>{editValues('simpleConfig')}{editValues('simpleCrop')}{editRecord('simpleParameters', 'Tabela completa de cultivares')}{editValues('simpleSoil')}</> : cropModel === 'stics' ? <>{editValues('sticsConfig')}{editValues('stics')}</> : <>{editValues('pysticsConfig')}{editValues('pystics')}</>}<h3>INTEGRAÇÕES</h3><p className="warning">As fontes abertas estão ativas. O conector Google ainda não está integrado a esta versão.</p></>}
        <OptimizationControls project={project} onChange={setProject} onOptimize={optimize} busy={['queued', 'running', 'cancelling'].includes(job?.status)} />
      </aside></section></main>
}
