import { FormEvent, useMemo, useState } from 'react'
import { api } from './api'
import { hapticSuccess } from './telegram'
import type { CalendarStatus, HomeEvent } from './types'

type Props = {
  items: HomeEvent[]
  status?: CalendarStatus
  setItems: (value: HomeEvent[]) => void
  setStatus: (value: CalendarStatus) => void
  onError: (value: string) => void
}

const inputDateTime = (value?: string | null) => {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60000)
  return local.toISOString().slice(0, 16)
}

const addMinutes = (value: string, minutes: number) => {
  if (!value) return ''
  return inputDateTime(new Date(new Date(value).getTime() + minutes * 60000).toISOString())
}

const addDays = (value: string, days: number) => {
  const date = new Date(`${value}T12:00:00`)
  date.setDate(date.getDate() + days)
  return date.toISOString().slice(0, 10)
}

const inclusiveEnd = (event: HomeEvent) => event.all_day && event.end_at ? addDays(event.end_at, -1) : event.end_at

const formatRange = (event: HomeEvent) => {
  if (event.all_day) {
    const start = new Date(`${event.start_at}T12:00:00`).toLocaleDateString('he-IL', { day: 'numeric', month: 'short' })
    const endValue = inclusiveEnd(event)
    const end = endValue ? new Date(`${endValue}T12:00:00`).toLocaleDateString('he-IL', { day: 'numeric', month: 'short' }) : start
    return start === end ? `כל היום · ${start}` : `כל היום · ${start}–${end}`
  }
  const start = new Date(event.start_at)
  const end = new Date(event.end_at)
  const sameDay = start.toDateString() === end.toDateString()
  const date = start.toLocaleDateString('he-IL', { weekday: 'short', day: 'numeric', month: 'short' })
  const startTime = start.toLocaleTimeString('he-IL', { hour: '2-digit', minute: '2-digit' })
  const endTime = end.toLocaleTimeString('he-IL', { hour: '2-digit', minute: '2-digit' })
  return sameDay ? `${date} · ${startTime}–${endTime}` : `${start.toLocaleString('he-IL')}–${end.toLocaleString('he-IL')}`
}

const durationText = (event: HomeEvent) => {
  if (event.all_day) return ''
  const minutes = Math.round((new Date(event.end_at).getTime() - new Date(event.start_at).getTime()) / 60000)
  if (!Number.isFinite(minutes) || minutes <= 0) return ''
  if (minutes < 60) return `${minutes} דקות`
  const hours = Math.floor(minutes / 60)
  const rest = minutes % 60
  return rest ? `${hours} ש׳ ${rest} דק׳` : `${hours} שעות`
}

export function EventsPanel({ items, status, setItems, setStatus, onError }: Props) {
  const [editing, setEditing] = useState<HomeEvent | null>(null)
  const [eventFormOpen, setEventFormOpen] = useState(false)
  const [title, setTitle] = useState('')
  const [start, setStart] = useState('')
  const [end, setEnd] = useState('')
  const [allDay, setAllDay] = useState(false)
  const [location, setLocation] = useState('')
  const [description, setDescription] = useState('')
  const [attendees, setAttendees] = useState('')
  const [recurrence, setRecurrence] = useState('')
  const [reminder, setReminder] = useState('30')
  const [syncing, setSyncing] = useState(false)

  const upcoming = useMemo(() => items
    .filter(event => event.status !== 'cancelled')
    .sort((a, b) => String(a.start_at).localeCompare(String(b.start_at))), [items])

  const groups = useMemo(() => upcoming.reduce<Record<string, HomeEvent[]>>((result, event) => {
    const key = event.all_day ? event.start_at : new Date(event.start_at).toISOString().slice(0, 10)
    ;(result[key] ||= []).push(event)
    return result
  }, {}), [upcoming])

  const changeStart = (value: string) => {
    setStart(value)
    if (!end || (!allDay && new Date(end) <= new Date(value))) {
      setEnd(allDay ? value : addMinutes(value, 60))
    }
  }

  const reset = () => {
    setTitle(''); setStart(''); setEnd(''); setAllDay(false); setLocation(''); setDescription(''); setAttendees(''); setRecurrence(''); setReminder('30')
  }

  const create = async (event: FormEvent) => {
    event.preventDefault()
    if (!title.trim() || !start || !end) return
    try {
      const startAt = allDay ? start : new Date(start).toISOString()
      const endAt = allDay ? addDays(end, 1) : new Date(end).toISOString()
      if (endAt <= startAt) throw new Error('שעת הסיום חייבת להיות אחרי שעת ההתחלה')
      const created = await api.addEvent({
        title: title.trim(),
        start_at: startAt,
        end_at: endAt,
        all_day: allDay,
        location: location.trim(),
        description: description.trim(),
        attendees: attendees.split(',').map(value => value.trim()).filter(Boolean),
        recurrence: recurrence ? [`RRULE:${recurrence}`] : [],
        reminders: reminder === 'default' ? { useDefault: true } : { useDefault: false, overrides: [{ method: 'popup', minutes: Number(reminder) }] },
      })
      setItems([...items, created])
      reset()
      setEventFormOpen(false)
      hapticSuccess()
    } catch (error) { onError(String(error)) }
  }

  const sync = async () => {
    setSyncing(true)
    try {
      await api.syncEvents()
      const [events, nextStatus] = await Promise.all([api.events(), api.calendarStatus()])
      setItems(events); setStatus(nextStatus); hapticSuccess()
    } catch (error) { onError(String(error)) }
    finally { setSyncing(false) }
  }

  const remove = async (event: HomeEvent) => {
    if (!confirm(`למחוק את האירוע “${event.title}” גם מ־Google Calendar?`)) return
    try {
      await api.deleteEvent(event.id)
      setItems(items.filter(row => row.id !== event.id))
      setEditing(null)
    } catch (error) { onError(String(error)) }
  }

  return <section className="page">
    <div className="page-title"><div><h2>אירועים</h2><p>Google Calendar הוא מקור האמת</p></div><button className={syncing ? 'icon-button spinning' : 'icon-button'} onClick={sync} aria-label="סנכרון">↻</button></div>
    <div className={status?.last_error ? 'sync-card error' : 'sync-card'}>
      <span>{status?.configured ? 'Google מחובר' : 'Google לא מחובר'}</span>
      <small>{status?.last_error || (status?.last_incremental_sync_at ? `סונכרן ${new Date(status.last_incremental_sync_at * 1000).toLocaleString('he-IL')}` : 'ממתין לסנכרון ראשון')}</small>
    </div>
    <details className="content-section" open={eventFormOpen} onToggle={event => setEventFormOpen(event.currentTarget.open)}>
      <summary style={{ cursor: 'pointer', fontWeight: 800 }}>＋ יצירת אירוע חדש</summary>
      <form className="stack-form" style={{ marginTop: 12, boxShadow: 'none', padding: 0 }} onSubmit={create}>
        <input value={title} onChange={event => setTitle(event.target.value)} placeholder="שם האירוע" />
        <label className="check-label"><input type="checkbox" checked={allDay} onChange={event => { setAllDay(event.target.checked); setStart(''); setEnd('') }} /> אירוע לכל היום</label>
        <div className="field-labels"><span>התחלה</span><span>סיום</span></div>
        <div className="form-row">
          <input type={allDay ? 'date' : 'datetime-local'} value={start} onChange={event => changeStart(event.target.value)} />
          <input type={allDay ? 'date' : 'datetime-local'} value={end} min={start} onChange={event => setEnd(event.target.value)} />
        </div>
        <input value={location} onChange={event => setLocation(event.target.value)} placeholder="מיקום, לא חובה" />
        <textarea value={description} onChange={event => setDescription(event.target.value)} placeholder="תיאור או הערות" />
        <input value={attendees} onChange={event => setAttendees(event.target.value)} placeholder="אימיילים להזמנה, מופרדים בפסיק" />
        <div className="form-row">
          <select value={recurrence} onChange={event => setRecurrence(event.target.value)}><option value="">ללא חזרה</option><option value="FREQ=WEEKLY">כל שבוע</option><option value="FREQ=MONTHLY">כל חודש</option><option value="FREQ=DAILY">כל יום</option></select>
          <select value={reminder} onChange={event => setReminder(event.target.value)}><option value="10">תזכורת 10 דקות לפני</option><option value="30">30 דקות לפני</option><option value="60">שעה לפני</option><option value="1440">יום לפני</option><option value="default">ברירת המחדל של Google</option></select>
        </div>
        <button className="primary-button">הוספה ל־Google Calendar</button>
      </form>
    </details>

    {Object.entries(groups).map(([date, events]) => <section className="content-section" key={date}><div className="section-heading"><h3>{new Date(`${date}T12:00:00`).toLocaleDateString('he-IL', { weekday: 'long', day: 'numeric', month: 'long' })}</h3></div><div className="section-body">
      {events.map(event => <button className="agenda-event" key={event.id} onClick={() => setEditing({ ...event })}>
        <div className="agenda-time">{event.all_day ? 'כל היום' : new Date(event.start_at).toLocaleTimeString('he-IL', { hour: '2-digit', minute: '2-digit' })}</div>
        <div><strong>{event.title}</strong><small>{formatRange(event)}{durationText(event) ? ` · ${durationText(event)}` : ''}</small>{event.location && <small>📍 {event.location}</small>}</div>
        <span className="sync-badge">{event.sync_status === 'synced' ? 'Google ✓' : event.sync_status}</span>
      </button>)}
    </div></section>)}
    {!upcoming.length && <div className="empty-state"><span>◇</span><p>אין אירועים קרובים</p></div>}

    {editing && <EventSheet event={editing} setEvent={setEditing} onClose={() => setEditing(null)} onDelete={() => remove(editing)} onSaved={updated => { setItems(items.map(row => row.id === updated.id ? updated : row)); setEditing(updated) }} onError={onError} />}
  </section>
}

function EventSheet({ event, setEvent, onClose, onDelete, onSaved, onError }: {
  event: HomeEvent
  setEvent: (value: HomeEvent) => void
  onClose: () => void
  onDelete: () => void
  onSaved: (value: HomeEvent) => void
  onError: (value: string) => void
}) {
  const [start, setStart] = useState(event.all_day ? event.start_at : inputDateTime(event.start_at))
  const [end, setEnd] = useState(event.all_day ? inclusiveEnd(event) : inputDateTime(event.end_at))
  const [attendees, setAttendees] = useState(event.attendees.map(item => item.email).filter(Boolean).join(', '))
  const [recurrence, setRecurrence] = useState((event.recurrence[0] || '').replace(/^RRULE:/, ''))

  const save = async (submit: FormEvent) => {
    submit.preventDefault()
    if (!start || !end) return
    try {
      const startAt = event.all_day ? start : new Date(start).toISOString()
      const endAt = event.all_day ? addDays(end, 1) : new Date(end).toISOString()
      if (endAt <= startAt) throw new Error('שעת הסיום חייבת להיות אחרי שעת ההתחלה')
      const updated = await api.updateEvent(event.id, {
        title: event.title,
        description: event.description,
        location: event.location,
        all_day: event.all_day,
        start_at: startAt,
        end_at: endAt,
        attendees: attendees.split(',').map(value => value.trim()).filter(Boolean),
        recurrence: recurrence ? [`RRULE:${recurrence}`] : [],
      })
      onSaved(updated)
      hapticSuccess()
    } catch (error) { onError(String(error)) }
  }

  return <div className="sheet-backdrop" onClick={onClose}><form className="edit-sheet" onSubmit={save} onClick={click => click.stopPropagation()}>
    <h3>עריכת אירוע</h3>
    <input value={event.title} onChange={change => setEvent({ ...event, title: change.target.value })} />
    <label className="check-label"><input type="checkbox" checked={event.all_day} onChange={change => { setEvent({ ...event, all_day: change.target.checked }); setStart(''); setEnd('') }} /> אירוע לכל היום</label>
    <div className="field-labels"><span>התחלה</span><span>סיום</span></div>
    <div className="form-row"><input type={event.all_day ? 'date' : 'datetime-local'} value={start} onChange={change => { setStart(change.target.value); if (!end) setEnd(event.all_day ? change.target.value : addMinutes(change.target.value, 60)) }} /><input type={event.all_day ? 'date' : 'datetime-local'} value={end} min={start} onChange={change => setEnd(change.target.value)} /></div>
    <input value={event.location || ''} onChange={change => setEvent({ ...event, location: change.target.value })} placeholder="מיקום" />
    <textarea value={event.description || ''} onChange={change => setEvent({ ...event, description: change.target.value })} placeholder="תיאור" />
    <input value={attendees} onChange={change => setAttendees(change.target.value)} placeholder="אימיילים להזמנה" />
    <select value={recurrence} onChange={change => setRecurrence(change.target.value)}><option value="">ללא חזרה</option><option value="FREQ=WEEKLY">כל שבוע</option><option value="FREQ=MONTHLY">כל חודש</option><option value="FREQ=DAILY">כל יום</option></select>
    <button className="primary-button">שמירה וסנכרון</button>
    {event.html_link && <button type="button" className="ghost-button" onClick={() => window.open(event.html_link, '_blank')}>פתיחה ב־Google Calendar</button>}
    <button type="button" className="danger-button" onClick={onDelete}>מחיקת האירוע</button>
    <button type="button" className="ghost-button" onClick={onClose}>סגירה</button>
  </form></div>
}
