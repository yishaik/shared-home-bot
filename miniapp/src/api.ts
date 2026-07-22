import type {
  Activity,
  CalendarStatus,
  Dashboard,
  HomeEvent,
  Household,
  MemoryControl,
  MemoryItem,
  Project,
  ShoppingItem,
  TaskCalendarBlock,
  TaskRelationship,
  TaskResource,
  Todo,
} from './types'

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

export type EventPayload = {
  title: string
  start_at: string
  end_at: string
  location?: string
  description?: string
  notes?: string
  all_day?: boolean
  attendees?: string[]
  recurrence?: string[]
  reminders?: Record<string, unknown>
}

export type ProjectPayload = {
  name: string
  description?: string
  status?: Project['status']
  owner_id?: number | null
  start_at?: string | null
  due_at?: string | null
  priority?: Project['priority']
  create_drive_folder?: boolean
}

export type TaskPayload = {
  title: string
  description?: string
  project_id?: number | null
  parent_task_id?: number | null
  status?: Todo['status']
  assigned_to?: number | null
  due_at?: string | null
  priority?: Todo['priority']
  recurrence_rule?: string
  estimate_minutes?: number | null
}

export const api = {
  home: () => request<Dashboard>('/api/home'),
  activity: () => request<Activity[]>('/api/activity'),

  shopping: () => request<ShoppingItem[]>('/api/shopping'),
  addShopping: (item: string, qty = '1', category = '') => request<ShoppingItem>('/api/shopping', { method: 'POST', body: JSON.stringify({ item, qty, category }) }),
  updateShopping: (id: number, patch: Partial<ShoppingItem>) => request<ShoppingItem>(`/api/shopping/${id}`, { method: 'PATCH', body: JSON.stringify(patch) }),
  deleteShopping: (id: number) => request<void>(`/api/shopping/${id}`, { method: 'DELETE' }),

  projects: (includeClosed = false) => request<Project[]>(`/api/projects?include_closed=${includeClosed}`),
  project: (id: number) => request<Project>(`/api/projects/${id}`),
  addProject: (payload: ProjectPayload) => request<Project>('/api/projects', { method: 'POST', body: JSON.stringify(payload) }),
  updateProject: (id: number, patch: Partial<ProjectPayload>) => request<Project>(`/api/projects/${id}`, { method: 'PATCH', body: JSON.stringify(patch) }),
  deleteProject: (id: number) => request<void>(`/api/projects/${id}`, { method: 'DELETE' }),
  createProjectFolder: (id: number) => request<{ id: string; url: string }>(`/api/projects/${id}/drive-folder`, { method: 'POST' }),

  tasks: (includeDone = false, projectId?: number) => {
    const params = new URLSearchParams({ include_done: String(includeDone) })
    if (projectId) params.set('project_id', String(projectId))
    return request<Todo[]>(`/api/tasks?${params}`)
  },
  task: (id: number) => request<Todo>(`/api/tasks/${id}`),
  addTask: (payload: TaskPayload) => request<Todo>('/api/tasks', { method: 'POST', body: JSON.stringify(payload) }),
  updateTask: (id: number, patch: Partial<TaskPayload> & { done?: boolean }) => request<Todo>(`/api/tasks/${id}`, { method: 'PATCH', body: JSON.stringify(patch) }),
  deleteTask: (id: number) => request<void>(`/api/tasks/${id}`, { method: 'DELETE' }),
  addTaskRelationship: (taskId: number, payload: Omit<TaskRelationship, 'source_title' | 'target_title'>) => request<TaskRelationship>(`/api/tasks/${taskId}/relationships`, { method: 'POST', body: JSON.stringify(payload) }),
  deleteTaskRelationship: (taskId: number, relationship: TaskRelationship) => request<void>(`/api/tasks/${taskId}/relationships/${relationship.source_task_id}/${relationship.target_task_id}/${relationship.relationship_type}`, { method: 'DELETE' }),
  addCalendarBlock: (taskId: number, payload: { start_at: string; end_at: string; location?: string; block_type?: TaskCalendarBlock['block_type'] }) => request<TaskCalendarBlock>(`/api/tasks/${taskId}/calendar-blocks`, { method: 'POST', body: JSON.stringify(payload) }),
  deleteCalendarBlock: (taskId: number, blockId: number) => request<void>(`/api/tasks/${taskId}/calendar-blocks/${blockId}`, { method: 'DELETE' }),
  createTaskDoc: (taskId: number) => request<TaskResource>(`/api/tasks/${taskId}/resources/doc`, { method: 'POST' }),
  createTaskSheet: (taskId: number, template = 'tracker') => request<TaskResource>(`/api/tasks/${taskId}/resources/sheet`, { method: 'POST', body: JSON.stringify({ template }) }),
  addTaskResourceLink: (taskId: number, payload: { file_name: string; web_url: string; google_file_id?: string; mime_type?: string; relationship?: TaskResource['relationship'] }) => request<TaskResource>(`/api/tasks/${taskId}/resources/link`, { method: 'POST', body: JSON.stringify(payload) }),

  events: (sync = false) => request<HomeEvent[]>(`/api/events${sync ? '?sync=true' : ''}`),
  event: (id: string) => request<HomeEvent>(`/api/events/${encodeURIComponent(id)}`),
  calendarStatus: () => request<CalendarStatus>('/api/events/status'),
  syncEvents: (full = false) => request<{ ok: boolean; mode: string; count: number }>(`/api/events/sync?full=${full}`, { method: 'POST' }),
  addEvent: (event: EventPayload) => request<HomeEvent>('/api/events', { method: 'POST', body: JSON.stringify(event) }),
  updateEvent: (id: string, patch: Partial<EventPayload>) => request<HomeEvent>(`/api/events/${encodeURIComponent(id)}`, { method: 'PATCH', body: JSON.stringify(patch) }),
  deleteEvent: (id: string) => request<void>(`/api/events/${encodeURIComponent(id)}`, { method: 'DELETE' }),

  memoryControl: () => request<MemoryControl>('/api/memory/control'),
  updateMemorySettings: (enabled: boolean) => request<MemoryControl['status']>('/api/memory/settings', { method: 'PATCH', body: JSON.stringify({ auto_memory_enabled: enabled }) }),
  updateMemory: (key: string, value: string, category: string) => request<MemoryItem>(`/api/memory/${encodeURIComponent(key)}`, { method: 'PATCH', body: JSON.stringify({ value, category }) }),
  deleteMemory: (key: string) => request<void>(`/api/memory/${encodeURIComponent(key)}`, { method: 'DELETE' }),
  updateCoreMemory: (value: string) => request<{ core_memory: string }>('/api/memory/core', { method: 'PUT', body: JSON.stringify({ value }) }),
  household: () => request<{ household: Household; members: Dashboard['members'] }>('/api/household'),
  updateHousehold: (patch: Partial<Household>) => request<Household>('/api/household', { method: 'PATCH', body: JSON.stringify(patch) }),
}
