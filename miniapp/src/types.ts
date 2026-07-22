export type Household = { id: string; name: string; timezone: string }
export type Member = { telegram_user_id: number; display_name: string; username: string; role: string; notification_mode?: string; can_receive_dm?: number | boolean; bot_started_at?: number | null; last_seen_at?: number | null; google_email?: string }
export type ProjectStatus = 'planned' | 'active' | 'paused' | 'completed' | 'cancelled'
export type TaskStatus = 'todo' | 'in_progress' | 'waiting' | 'completed' | 'cancelled'
export type Priority = 'low' | 'normal' | 'high'
export type Project = { id: number; name: string; description: string; status: ProjectStatus; owner_id?: number | null; owner_name?: string; start_at?: string | null; due_at?: string | null; priority: Priority; drive_folder_id?: string; drive_folder_url?: string; task_count: number; completed_count: number; progress: number; tasks?: Todo[] }
export type TaskRelationship = { source_task_id: number; target_task_id: number; relationship_type: 'blocks' | 'related' | 'follows' | 'duplicates'; source_title?: string; target_title?: string }
export type TaskCalendarBlock = { id: number; task_id: number; google_event_id: string; block_type: 'work' | 'appointment' | 'review' | 'focus'; start_at: string; end_at: string; location: string; sync_status: string }
export type TaskResource = { id: number; task_id: number; project_id?: number | null; provider: string; google_file_id: string; file_name: string; mime_type: string; web_url: string; relationship: 'attachment' | 'working_doc' | 'source' | 'output'; created_at: number }
export type Todo = { id: number; title: string; description: string; status: TaskStatus; done: number; done_at?: number | null; project_id?: number | null; project_name?: string | null; parent_task_id?: number | null; assigned_to?: number | null; assigned_name?: string | null; due_at?: string | null; priority: Priority; recurrence_rule?: string; estimate_minutes?: number | null; blocked: boolean; blockers: Array<{ id: number; title: string; status: TaskStatus }>; relationships: TaskRelationship[]; calendar_blocks: TaskCalendarBlock[]; resources: TaskResource[] }
export type ShoppingItem = { id: number; item: string; qty: string; category: string; done: number }
export type HomeEvent = { id: string; google_event_id: string; title: string; description: string; notes: string; start_at: string; end_at: string; location: string; all_day: boolean; status: string; recurrence: string[]; recurring_event_id?: string | null; attendees: Array<{ email?: string; displayName?: string; responseStatus?: string }>; reminders: Record<string, unknown>; html_link: string; sync_status: string; source: string }
export type CalendarStatus = { configured: boolean; calendar_id?: string; cached_events?: number; last_full_sync_at?: number | null; last_incremental_sync_at?: number | null; last_error?: string }
export type Activity = { id: number; actor_id?: number | null; actor_name?: string; kind: string; entity_type: string; summary: string; created_at: number }
export type MemoryItem = { key: string; value: string; category: string; updated_by?: number | null; updated_at: number }
export type MemoryAudit = { id: number; action: string; memory_key: string; old_value: string; new_value: string; source: string; actor_id?: number | null; metadata: Record<string, unknown>; created_at: number }
export type MemoryControl = { status: { auto_memory_enabled: boolean; last_status: string; last_at: string; last_error: string }; core_memory: string; memories: MemoryItem[]; audit: MemoryAudit[] }

export type InboxStatus = 'pending' | 'executing' | 'completed' | 'failed' | 'needs_review' | 'editing' | 'cancelled' | 'expired'
export type InboxRisk = 'low' | 'medium' | 'high'
export type InboxStep = {
  proposal_id: string
  position: number
  tool_name: string
  arguments: Record<string, unknown>
  fingerprint: string
  risk_level: InboxRisk
  requires_approval: boolean
  external_side_effect: boolean
  status: 'pending' | 'executing' | 'completed' | 'failed' | 'uncertain'
  result?: Record<string, unknown> | null
  last_error: string
  started_at?: number | null
  completed_at?: number | null
}
export type InboxAudit = {
  id: number
  proposal_id: string
  actor_id?: number | null
  action: string
  from_status: string
  to_status: string
  metadata: Record<string, unknown>
  created_at: number
}
export type InboxProposal = {
  id: string
  household_id: string
  source_kind: string
  source_key: string
  chat_id?: number | null
  thread_id?: number | null
  source_message_id?: number | null
  source_update_id?: number | null
  created_by: number
  agent_id: string
  source_text: string
  summary: string
  risk_level: InboxRisk
  approval_policy: string
  status: InboxStatus
  version: number
  retry_count: number
  last_error: string
  created_at: number
  updated_at: number
  expires_at: number
  executing_at?: number | null
  completed_at?: number | null
  cancelled_at?: number | null
  steps: InboxStep[]
  audit?: InboxAudit[]
  can_approve: boolean
  can_cancel: boolean
  can_retry: boolean
  requires_approval?: boolean
}
export type InboxCounts = Record<InboxStatus, number> & { attention: number }

export type Dashboard = { household: Household; members: Member[]; counts: { todos: number; shopping: number; events: number; projects: number }; todos: Todo[]; shopping: ShoppingItem[]; events: HomeEvent[]; projects: Project[]; activity: Activity[]; calendar_status?: CalendarStatus }
