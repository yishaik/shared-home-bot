import { useEffect, useState } from 'react'
import { api } from './api'
import { hapticSuccess } from './telegram'
import type { MemoryControl, MemoryItem } from './types'

export function MemoryPanel({ onError }: { onError: (value: string) => void }) {
  const [data, setData] = useState<MemoryControl | null>(null)
  const [busy, setBusy] = useState(false)
  const [core, setCore] = useState('')

  const load = async () => {
    try {
      const next = await api.memoryControl()
      setData(next)
      setCore(next.core_memory)
    } catch (error) {
      onError(error instanceof Error ? error.message : String(error))
    }
  }

  useEffect(() => { void load() }, [])

  const toggleAutomatic = async () => {
    if (!data || busy) return
    setBusy(true)
    try {
      const status = await api.updateMemorySettings(!data.status.auto_memory_enabled)
      setData({ ...data, status })
      hapticSuccess()
    } catch (error) {
      onError(error instanceof Error ? error.message : String(error))
    } finally { setBusy(false) }
  }

  const saveCore = async () => {
    if (!data || busy) return
    setBusy(true)
    try {
      const result = await api.updateCoreMemory(core)
      setData({ ...data, core_memory: result.core_memory })
      hapticSuccess()
    } catch (error) {
      onError(error instanceof Error ? error.message : String(error))
    } finally { setBusy(false) }
  }

  const editMemory = async (item: MemoryItem) => {
    const value = window.prompt('עריכת הזיכרון', item.value)
    if (value === null || !value.trim()) return
    try {
      const updated = await api.updateMemory(item.key, value.trim(), item.category)
      setData(current => current ? { ...current, memories: current.memories.map(row => row.key === item.key ? updated : row) } : current)
      hapticSuccess()
    } catch (error) { onError(error instanceof Error ? error.message : String(error)) }
  }

  const removeMemory = async (item: MemoryItem) => {
    if (!window.confirm(`למחוק את הזיכרון “${item.key}”?`)) return
    try {
      await api.deleteMemory(item.key)
      setData(current => current ? { ...current, memories: current.memories.filter(row => row.key !== item.key) } : current)
      hapticSuccess()
    } catch (error) { onError(error instanceof Error ? error.message : String(error)) }
  }

  if (!data) return <div className="memory-loading">טוען את זיכרון הבית…</div>

  const lastAt = data.status.last_at ? new Date(Number(data.status.last_at) * 1000).toLocaleString('he-IL') : 'עדיין לא רץ'

  return <div className="memory-controls">
    <div className="memory-toggle-row">
      <div>
        <strong>זיכרון אוטומטי</strong>
        <small>{data.status.auto_memory_enabled ? 'הבוט יכול לחלץ עובדות עמידות מהשיחה' : 'נשמר רק מידע שמבקשים במפורש'}</small>
      </div>
      <button className={data.status.auto_memory_enabled ? 'toggle-button on' : 'toggle-button'} onClick={toggleAutomatic} disabled={busy} aria-pressed={data.status.auto_memory_enabled}>
        {data.status.auto_memory_enabled ? 'פעיל' : 'כבוי'}
      </button>
    </div>
    <div className="reflection-status">
      <span>מצב תחזוקת זיכרון: <strong>{data.status.last_status}</strong></span>
      <small>הרצה אחרונה: {lastAt}</small>
      {data.status.last_error && <small className="danger-text">שגיאה: {data.status.last_error}</small>}
    </div>
    <label className="core-editor">זיכרון ליבה
      <textarea value={core} onChange={event => setCore(event.target.value)} placeholder="עובדות בסיס שחייבות להיות זמינות תמיד" />
    </label>
    <button className="primary-button" onClick={saveCore} disabled={busy}>שמירת זיכרון הליבה</button>
    <div className="memory-list">
      {data.memories.length ? data.memories.map(item => <div className="memory-row" key={item.key}>
        <div><strong>{item.key}</strong><p>{item.value}</p><small>{item.category} · {new Date(item.updated_at * 1000).toLocaleDateString('he-IL')}</small></div>
        <div className="memory-actions"><button onClick={() => editMemory(item)}>עריכה</button><button className="danger-button" onClick={() => removeMemory(item)}>מחיקה</button></div>
      </div>) : <p className="memory-empty">אין עדיין עובדות שמורות.</p>}
    </div>
    <details className="audit-list">
      <summary>יומן שינויים ({data.audit.length})</summary>
      {data.audit.map(item => <div className="audit-row" key={item.id}>
        <strong>{item.action}{item.memory_key ? ` · ${item.memory_key}` : ''}</strong>
        <small>{item.source} · {new Date(item.created_at * 1000).toLocaleString('he-IL')}</small>
      </div>)}
    </details>
  </div>
}
