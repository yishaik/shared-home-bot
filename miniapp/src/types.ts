export type Household = { id: string; name: string; timezone: string }
export type Member = { telegram_user_id: number; display_name: string; username: string; role: string }
export type Todo = { id: number; title: string; done: number; assigned_to?: number | null; due_at?: string | null; priority: 'low' | 'normal' | 'high' }
export type ShoppingItem = { id: number; item: string; qty: string; category: string; done: number }
export type HomeEvent = { id: number; title: string; when_text: string; start_at?: string | null; end_at?: string | null; location: string; all_day: number; notes: string }
export type Activity = { id: number; actor_id?: number | null; actor_name?: string; kind: string; entity_type: string; summary: string; created_at: number }
export type Dashboard = {
  household: Household
  members: Member[]
  counts: { todos: number; shopping: number; events: number }
  todos: Todo[]
  shopping: ShoppingItem[]
  events: HomeEvent[]
  activity: Activity[]
}
