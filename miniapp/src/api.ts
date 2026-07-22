import type { Activity, Dashboard, HomeEvent, Household, MemoryControl, MemoryItem, ShoppingItem, Todo } from './types'

let token = sessionStorage.getItem('home_session') || ''

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(options.headers || {}),
    },
  })
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}))
    throw new Error(payload.detail || `Request failed (${response.status})`)
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export async function authenticate(initData: string) {
  const result = await request<{ token: string; user: { id: number; name: string }; household: Household }>('/api/auth/telegram', {
    method: 'POST', body: JSON.stringify({ init_data: initData }),
  })
  token = result.token
  sessionStorage.setItem('home_session', token)
  return result
}

export const api = {
  home: () => request<Dashboard>('/api/home'),
  activity: () => request<Activity[]>('/api/activity'),
  shopping: () => request<ShoppingItem[]>('/api/shopping'),
  addShopping: (item: string, qty = '1', category = '') => request<ShoppingItem>('/api/shopping', { method: 'POST', body: JSON.stringify({ item, qty, category }) }),
  updateShopping: (id: number, patch: Partial<ShoppingItem>) => request<ShoppingItem>(`/api/shopping/${id}`, { method: 'PATCH', body: JSON.stringify(patch) }),
  deleteShopping: (id: number) => request<void>(`/api/shopping/${id}`, { method: 'DELETE' }),
  tasks: () => request<Todo[]>('/api/tasks'),
  addTask: (title: string, priority: Todo['priority'] = 'normal') => request<Todo>('/api/tasks', { method: 'POST', body: JSON.stringify({ title, priority }) }),
  updateTask: (id: number, patch: Partial<Todo>) => request<Todo>(`/api/tasks/${id}`, { method: 'PATCH', body: JSON.stringify(patch) }),
  deleteTask: (id: number) => request<void>(`/api/tasks/${id}`, { method: 'DELETE' }),
  events: () => request<HomeEvent[]>('/api/events'),
  addEvent: (event: { title: string; start_at: string; end_at?: string; location?: string; notes?: string; all_day?: boolean }) => request<HomeEvent>('/api/events', { method: 'POST', body: JSON.stringify(event) }),
  deleteEvent: (id: number) => request<void>(`/api/events/${id}`, { method: 'DELETE' }),
  memoryControl: () => request<MemoryControl>('/api/memory/control'),
  updateMemorySettings: (enabled: boolean) => request<MemoryControl['status']>('/api/memory/settings', { method: 'PATCH', body: JSON.stringify({ auto_memory_enabled: enabled }) }),
  updateMemory: (key: string, value: string, category: string) => request<MemoryItem>(`/api/memory/${encodeURIComponent(key)}`, { method: 'PATCH', body: JSON.stringify({ value, category }) }),
  deleteMemory: (key: string) => request<void>(`/api/memory/${encodeURIComponent(key)}`, { method: 'DELETE' }),
  updateCoreMemory: (value: string) => request<{ core_memory: string }>('/api/memory/core', { method: 'PUT', body: JSON.stringify({ value }) }),
  household: () => request<{ household: Household; members: Dashboard['members'] }>('/api/household'),
  updateHousehold: (patch: Partial<Household>) => request<Household>('/api/household', { method: 'PATCH', body: JSON.stringify(patch) }),
}
