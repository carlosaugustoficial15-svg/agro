const BASE = 'http://127.0.0.1:8765'
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, { headers: { 'Content-Type': 'application/json' }, ...init })
  const data = await response.json()
  if (!response.ok) throw new Error(data.error || `Erro ${response.status}`)
  return data
}
export const api = {
  config: () => request<any>('/studio/config'),
  search: (query: string) => request<{items: any[]}>('/locations/search', { method: 'POST', body: JSON.stringify({ query }) }),
  enrich: (latitude: number, longitude: number) => request<any>('/locations/enrich', { method: 'POST', body: JSON.stringify({ latitude, longitude }) }),
  modules: (q: string) => request<{items: any[]}>(`/catalog/modules?q=${encodeURIComponent(q)}&limit=50`),
  optimize: (payload: any) => request<any>('/layout/optimize', { method: 'POST', body: JSON.stringify(payload) }),
  solar: (payload: any) => request<any>('/preview/solar', { method: 'POST', body: JSON.stringify(payload) }),
  run: (project: any) => request<any>('/simulation/start', { method: 'POST', body: JSON.stringify({ project }) }),
  cancel: (id: string) => request<any>('/simulation/cancel', { method: 'POST', body: JSON.stringify({ id }) }),
  job: (id: string) => request<any>(`/jobs/${id}`),
}
