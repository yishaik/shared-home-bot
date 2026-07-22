export type Household = { id: string; name: string; timezone: string }
export type Member = { telegram_user_id: number; display_name: string; username: string; role: string }
export type Todo = { id: number; title: string; done: number; done_at?: number | null; assigned_to?: number | null; due_at?: string | null; priority: 'low' | 'normal' | 'high' }
export type ShoppingItem = { id: number; item: string; qty: string; category: string; done: number }
export type HomeEvent = {
  id: string
  google_event_id?: string
  title: string
  description?: string
  notes: string
  when_text: string
  start_at?: string | null
  end_at?: string | null
  location: string
  all_day: boolean
  status?: string
  recurrence?: string[]
  recurring_event_id?: string | null
  attendees?: Array<{ email?: string; displayName?: string; responseStatus?: string }>
  reminders?: Record<string, unknown>
  html_link?: string
  source?: string
  sync_status?: 'synced' | 'syncing' | 'error'
}
export type CalendarStatus = {
  configured: boolean
  calendar_id: string
  cached_events: number
  last_full_sync_at?: number | null
  last_incremental_sync_at?: number | null
  last_error?: string
}
export type Activity = { id: number; actor_id?: number | null; actor_name?: string; kind: string; entity_type: string; summary: string; created_at: number }
export type MemoryItem = { key: string; value: string; category: string; updated_by?: number | null; updated_at: number }
export type MemoryAudit = { id: number; action: string; memory_key: string; old_value: string; new_value: string; source: string; actor_id?: number | null; metadata: Record<string, unknown>; created_at: number }
export type MemoryControl = {
  status: { auto_memory_enabled: boolean; last_status: string; last_at: string; last_error: string }
  core_memory: string
  memories: MemoryItem[]
  audit: MemoryAudit[]
}
export type Dashboard = {
  household: Household
  members: Member[]
  counts: { todos: number; shopping: number; events: number }
  todos: Todo[]
  shopping: ShoppingItem[]
  events: HomeEvent[]
  activity: Activity[]
}
