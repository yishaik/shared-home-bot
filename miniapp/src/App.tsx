import { FormEvent, useCallback, useEffect, useMemo, useState } from 'react'
import { api, authenticate } from './api'
import { MemoryPanel } from './MemoryPanel'
import { hapticSelection, hapticSuccess, tg } from './telegram'
import type { Activity, Dashboard, HomeEvent, Household, ShoppingItem, Todo } from './types'

type Tab = 'home' | 'shopping' | 'tasks' | 'events' | 'settings'
type Filter = 'all' | 'urgent' | 'dated'

const tabFromUrl = (): Tab => {
  const value = new URLSearchParams(location.search).get('tab')
  return ['shopping', 'tasks', 'events', 'settings'].includes(value || '') ? value as Tab : 'home'
}

const dateText = (value?: string | null) => {
  if (!value) return 'ללא תאריך'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('he-IL', { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }).format(date)
}

const inputDate = (value?: string | null) => {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60000)
  return local.toISOString().slice(0, 16)
}

function App() {
  const [tab, setTab] = useState<Tab>(tabFromUrl())
  const [dashboard, setDashboard] = useState<Dashboard | null>(null)
  const [shopping, setShopping] = useState<ShoppingItem[]>([])
  const [tasks, setTasks] = useState<Todo[]>([])
  const [events, setEvents] = useState<HomeEvent[]>([])
  const [activity, setActivity] = useState<Activity[]>([])
  const [household, setHousehold] = useState<Household | null>(null)
  const [userName, setUserName] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [lastSync, setLastSync] = useState<Date | null>(null)

  const load = useCallback(async (quiet = false) => {
    if (!quiet) setRefreshing(true)
    try {
      const [home, shop, taskRows, eventRows, activityRows] = await Promise.all([
        api.home(), api.shopping(), api.tasks(), api.events(), api.activity(),
      ])
      setDashboard(home)
      setShopping(shop)
      setTasks(taskRows)
      setEvents(eventRows)
      setActivity(activityRows)
      setHousehold(home.household)
      setLastSync(new Date())
    } finally {
      if (!quiet) setRefreshing(false)
    }
  }, [])

  useEffect(() => {
    ;(async () => {
      try {
        const initData = tg?.initData || ''
        if (!sessionStorage.getItem('home_session')) {
          if (!initData) throw new Error('פתח את האפליקציה מתוך הבוט בטלגרם כדי להתחבר בבטחה.')
          const auth = await authenticate(initData)
          setUserName(auth.user.name)
        }
        await load(true)
      } catch (e) {
        setError(e instanceof Error ? e.message : 'אירעה שגיאה')
      } finally {
        setLoading(false)
      }
    })()
  }, [load])

  useEffect(() => {
    const refresh = () => { if (document.visibilityState === 'visible') load(true).catch(() => undefined) }
    const timer = window.setInterval(refresh, 15000)
    document.addEventListener('visibilitychange', refresh)
    return () => {
      window.clearInterval(timer)
      document.removeEventListener('visibilitychange', refresh)
    }
  }, [load])

  const navigate = (next: Tab) => {
    hapticSelection()
    setTab(next)
    history.replaceState(null, '', next === 'home' ? '/app' : `/app?tab=${next}`)
  }

  const refresh = async () => {
    try { await load() } catch (e) { setError(e instanceof Error ? e.message : String(e)) }
  }

  if (loading) return <Loading />
  if (error && !dashboard) return <FatalError message={error} />

  return (
    <div className="app-shell">
      <header className="topbar">
        <div>
          <span className="eyebrow">המרכז המשפחתי</span>
          <h1>{household?.name || 'הבית שלנו'}</h1>
        </div>
        <div className="top-actions">
          <button className={refreshing ? 'icon-button spinning' : 'icon-button'} onClick={refresh} aria-label="רענון">↻</button>
          <div className="avatar" aria-label={userName || 'משתמש'}>{(userName || 'ב').slice(0, 1)}</div>
        </div>
      </header>
      <div className="sync-line">{lastSync ? `עודכן ${new Intl.DateTimeFormat('he-IL', { hour: '2-digit', minute: '2-digit' }).format(lastSync)}` : 'מתחבר…'}</div>
      {error && <button className="error-banner" onClick={() => setError('')}>{error} ×</button>}
      <main>
        {tab === 'home' && dashboard && <Home dashboard={dashboard} onNavigate={navigate} />}
        {tab === 'shopping' && <Shopping items={shopping} setItems={setShopping} onError={setError} />}
        {tab === 'tasks' && <Tasks items={tasks} members={dashboard?.members || []} setItems={setTasks} onError={setError} />}
        {tab === 'events' && <Events items={events} setItems={setEvents} onError={setError} />}
        {tab === 'settings' && household && <Settings household={household} setHousehold={setHousehold} activity={activity} onError={setError} />}
      </main>
      <nav className="bottom-nav" aria-label="ניווט ראשי">
        <NavButton active={tab === 'home'} icon="⌂" label="בית" onClick={() => navigate('home')} />
        <NavButton active={tab === 'shopping'} icon="🛒" label="קניות" count={shopping.length} onClick={() => navigate('shopping')} />
        <NavButton active={tab === 'tasks'} icon="✓" label="משימות" count={tasks.length} onClick={() => navigate('tasks')} />
        <NavButton active={tab === 'events'} icon="◷" label="אירועים" onClick={() => navigate('events')} />
        <NavButton active={tab === 'settings'} icon="⚙" label="עוד" onClick={() => navigate('settings')} />
      </nav>
    </div>
  )
}

function Loading() { return <div className="loading-screen"><div className="brand-mark">🏠</div><div className="skeleton wide"/><div className="skeleton"/><div className="skeleton"/></div> }
function FatalError({ message }: { message: string }) { return <div className="fatal"><div className="brand-mark">🏠</div><h1>לא הצלחנו לפתוח את הבית</h1><p>{message}</p></div> }
function NavButton({ active, icon, label, count, onClick }: { active: boolean; icon: string; label: string; count?: number; onClick: () => void }) {
  return <button className={active ? 'nav-button active' : 'nav-button'} onClick={onClick}><span>{icon}{count ? <b className="nav-badge">{count}</b> : null}</span><small>{label}</small></button>
}

function Home({ dashboard, onNavigate }: { dashboard: Dashboard; onNavigate: (tab: Tab) => void }) {
  const greeting = new Date().getHours() < 12 ? 'בוקר טוב' : new Date().getHours() < 18 ? 'צהריים טובים' : 'ערב טוב'
  const urgent = dashboard.todos.filter(item => item.priority === 'high').length
  return <section className="page home-page">
    <div className="hero-card">
      <span className="eyebrow">{greeting}</span>
      <h2>מה צריך לקדם היום?</h2>
      <p>{dashboard.counts.todos} משימות, {dashboard.counts.shopping} פריטים לקנייה ו־{dashboard.counts.events} אירועים בלוח.</p>
      <div className="hero-actions">
        <button onClick={() => onNavigate('tasks')}>＋ משימה</button>
        <button onClick={() => onNavigate('shopping')}>＋ לקניות</button>
      </div>
    </div>
    <div className="metric-grid">
      <button className="metric" onClick={() => onNavigate('tasks')}><strong>{dashboard.counts.todos}</strong><span>משימות פתוחות</span></button>
      <button className="metric" onClick={() => onNavigate('shopping')}><strong>{dashboard.counts.shopping}</strong><span>ברשימת הקניות</span></button>
      <button className="metric" onClick={() => onNavigate('events')}><strong>{dashboard.counts.events}</strong><span>אירועים</span></button>
    </div>
    {urgent > 0 && <div className="attention-card"><span>⚡</span><div><strong>{urgent} משימות בעדיפות גבוהה</strong><small>כדאי לטפל בהן לפני שאר המשימות.</small></div><button onClick={() => onNavigate('tasks')}>הצג</button></div>}
    <Section title="בקרוב" action="כל האירועים" onAction={() => onNavigate('events')}>
      {dashboard.events.length ? dashboard.events.slice(0, 3).map(event => <div className="timeline-row" key={event.id}><div className="timeline-dot"/><div><strong>{event.title}</strong><small>{dateText(event.start_at || event.when_text)}</small></div></div>) : <Empty text="אין אירועים קרובים"/>}
    </Section>
    <Section title="שינויים אחרונים">
      {dashboard.activity.length ? dashboard.activity.slice(0, 5).map(item => <ActivityRow key={item.id} item={item}/>) : <Empty text="כאן יופיעו עדכונים מבני הבית"/>}
    </Section>
  </section>
}

function Shopping({ items, setItems, onError }: { items: ShoppingItem[]; setItems: (v: ShoppingItem[]) => void; onError: (v: string) => void }) {
  const [value, setValue] = useState('')
  const [qty, setQty] = useState('1')
  const [category, setCategory] = useState('')
  const [query, setQuery] = useState('')
  const filtered = items.filter(item => `${item.item} ${item.category}`.toLowerCase().includes(query.toLowerCase()))
  const grouped = useMemo(() => Object.entries(filtered.reduce<Record<string, ShoppingItem[]>>((acc, item) => { const key = item.category || 'כללי'; (acc[key] ||= []).push(item); return acc }, {})), [filtered])
  const add = async (e: FormEvent) => { e.preventDefault(); if (!value.trim()) return; try { const item = await api.addShopping(value.trim(), qty, category.trim()); setItems([item, ...items]); setValue(''); setQty('1'); setCategory(''); hapticSuccess() } catch (err) { onError(String(err)) } }
  const done = async (item: ShoppingItem) => { const before = items; setItems(items.filter(x => x.id !== item.id)); try { await api.updateShopping(item.id, { done: 1 }); hapticSuccess() } catch (err) { setItems(before); onError(String(err)) } }
  const remove = async (item: ShoppingItem) => { if (!confirm(`למחוק את ${item.item}?`)) return; const before = items; setItems(items.filter(x => x.id !== item.id)); try { await api.deleteShopping(item.id) } catch (err) { setItems(before); onError(String(err)) } }
  return <section className="page"><PageTitle title="רשימת קניות" subtitle={`${items.length} פריטים פתוחים`} />
    <form className="stack-form" onSubmit={add}>
      <input aria-label="פריט חדש" value={value} onChange={e => setValue(e.target.value)} placeholder="מה חסר בבית?"/>
      <div className="form-row"><input aria-label="כמות" value={qty} onChange={e => setQty(e.target.value)} placeholder="כמות"/><input aria-label="קטגוריה" value={category} onChange={e => setCategory(e.target.value)} placeholder="קטגוריה, למשל ירקות"/></div>
      <button className="primary-button">הוספה לרשימה</button>
    </form>
    <input className="search-input" value={query} onChange={e => setQuery(e.target.value)} placeholder="חיפוש ברשימה…"/>
    {grouped.length ? grouped.map(([group, rows]) => <Section key={group} title={group}>{rows.map(item => <div className="manage-row" key={item.id}><button className="row-main" onClick={() => done(item)}><span className="check-circle"/><span><strong>{item.item}</strong><small>כמות: {item.qty}</small></span></button><button className="danger-icon" onClick={() => remove(item)} aria-label={`מחיקת ${item.item}`}>×</button></div>)}</Section>) : <Empty text="לא נמצאו פריטים"/>}
  </section>
}

function Tasks({ items, members, setItems, onError }: { items: Todo[]; members: Dashboard['members']; setItems: (v: Todo[]) => void; onError: (v: string) => void }) {
  const [title, setTitle] = useState('')
  const [priority, setPriority] = useState<Todo['priority']>('normal')
  const [dueAt, setDueAt] = useState('')
  const [assignedTo, setAssignedTo] = useState('')
  const [filter, setFilter] = useState<Filter>('all')
  const [editing, setEditing] = useState<Todo | null>(null)
  const visible = items.filter(item => filter === 'urgent' ? item.priority === 'high' : filter === 'dated' ? Boolean(item.due_at) : true)
  const add = async (e: FormEvent) => { e.preventDefault(); if (!title.trim()) return; try { const item = await api.addTask(title.trim(), priority, assignedTo ? Number(assignedTo) : undefined, dueAt ? new Date(dueAt).toISOString() : undefined); setItems([item, ...items]); setTitle(''); setDueAt(''); setAssignedTo(''); setPriority('normal'); hapticSuccess() } catch (err) { onError(String(err)) } }
  const done = async (item: Todo) => { const before = items; setItems(items.filter(x => x.id !== item.id)); try { await api.updateTask(item.id, { done: 1 }); hapticSuccess() } catch (err) { setItems(before); onError(String(err)) } }
  const save = async (e: FormEvent) => { e.preventDefault(); if (!editing) return; try { const updated = await api.updateTask(editing.id, { title: editing.title, priority: editing.priority, due_at: editing.due_at || null, assigned_to: editing.assigned_to ?? null }); setItems(items.map(x => x.id === updated.id ? updated : x)); setEditing(null); hapticSuccess() } catch (err) { onError(String(err)) } }
  const remove = async (item: Todo) => { if (!confirm(`למחוק את המשימה “${item.title}”?`)) return; const before = items; setItems(items.filter(x => x.id !== item.id)); try { await api.deleteTask(item.id) } catch (err) { setItems(before); onError(String(err)) } }
  return <section className="page"><PageTitle title="משימות" subtitle={`${items.length} פתוחות`} />
    <form className="stack-form" onSubmit={add}>
      <input value={title} onChange={e => setTitle(e.target.value)} placeholder="משימה חדשה…"/>
      <div className="form-row"><select value={priority} onChange={e => setPriority(e.target.value as Todo['priority'])}><option value="normal">עדיפות רגילה</option><option value="high">דחוף</option><option value="low">עדיפות נמוכה</option></select><select value={assignedTo} onChange={e => setAssignedTo(e.target.value)}><option value="">ללא אחראי</option>{members.map(member => <option key={member.telegram_user_id} value={member.telegram_user_id}>{member.display_name || member.username || member.telegram_user_id}</option>)}</select></div>
      <input type="datetime-local" value={dueAt} onChange={e => setDueAt(e.target.value)}/>
      <button className="primary-button">הוספת משימה</button>
    </form>
    <div className="segmented"><button className={filter === 'all' ? 'active' : ''} onClick={() => setFilter('all')}>הכול</button><button className={filter === 'urgent' ? 'active' : ''} onClick={() => setFilter('urgent')}>דחוף</button><button className={filter === 'dated' ? 'active' : ''} onClick={() => setFilter('dated')}>עם מועד</button></div>
    <Section title="לביצוע">{visible.length ? visible.map(item => <div className="manage-row" key={item.id}><button className="row-main" onClick={() => done(item)}><span className="check-circle"/><span><strong>{item.title}</strong><small>{item.due_at ? dateText(item.due_at) : item.priority === 'high' ? 'עדיפות גבוהה' : 'ללא מועד'}</small></span>{item.priority === 'high' && <i className="priority">דחוף</i>}</button><button className="more-button" onClick={() => setEditing({ ...item })}>•••</button></div>) : <Empty text="אין משימות בפילטר הזה"/>}</Section>
    {editing && <div className="sheet-backdrop" onClick={() => setEditing(null)}><form className="edit-sheet" onSubmit={save} onClick={e => e.stopPropagation()}><h3>עריכת משימה</h3><input value={editing.title} onChange={e => setEditing({ ...editing, title: e.target.value })}/><select value={editing.priority} onChange={e => setEditing({ ...editing, priority: e.target.value as Todo['priority'] })}><option value="normal">רגילה</option><option value="high">דחופה</option><option value="low">נמוכה</option></select><select value={editing.assigned_to || ''} onChange={e => setEditing({ ...editing, assigned_to: e.target.value ? Number(e.target.value) : null })}><option value="">ללא אחראי</option>{members.map(member => <option key={member.telegram_user_id} value={member.telegram_user_id}>{member.display_name || member.username || member.telegram_user_id}</option>)}</select><input type="datetime-local" value={inputDate(editing.due_at)} onChange={e => setEditing({ ...editing, due_at: e.target.value ? new Date(e.target.value).toISOString() : null })}/><button className="primary-button">שמירה</button><button type="button" className="danger-button" onClick={() => remove(editing)}>מחיקת משימה</button><button type="button" className="ghost-button" onClick={() => setEditing(null)}>ביטול</button></form></div>}
  </section>
}

function Events({ items, setItems, onError }: { items: HomeEvent[]; setItems: (v: HomeEvent[]) => void; onError: (v: string) => void }) {
  const [title, setTitle] = useState('')
  const [start, setStart] = useState('')
  const [location, setLocation] = useState('')
  const add = async (e: FormEvent) => { e.preventDefault(); if (!title.trim() || !start) return; try { const item = await api.addEvent({ title: title.trim(), start_at: new Date(start).toISOString(), location: location.trim() }); setItems([...items, item].sort((a, b) => String(a.start_at).localeCompare(String(b.start_at)))); setTitle(''); setStart(''); setLocation(''); hapticSuccess() } catch (err) { onError(String(err)) } }
  const remove = async (item: HomeEvent) => { if (!confirm(`למחוק את האירוע “${item.title}”?`)) return; const before = items; setItems(items.filter(x => x.id !== item.id)); try { await api.deleteEvent(item.id) } catch (err) { setItems(before); onError(String(err)) } }
  return <section className="page"><PageTitle title="אירועים" subtitle="הלו״ז המשותף" />
    <form className="stack-form" onSubmit={add}><input value={title} onChange={e => setTitle(e.target.value)} placeholder="שם האירוע"/><input type="datetime-local" value={start} onChange={e => setStart(e.target.value)}/><input value={location} onChange={e => setLocation(e.target.value)} placeholder="מיקום, לא חובה"/><button className="primary-button">הוספת אירוע</button></form>
    <Section title="בלוח הבית">{items.length ? items.map(item => <div className="event-card" key={item.id}><div className="date-tile"><strong>{new Date(item.start_at || item.when_text).getDate() || '•'}</strong><small>{dateText(item.start_at || item.when_text).split(' ')[1] || ''}</small></div><div className="event-info"><strong>{item.title}</strong><small>{dateText(item.start_at || item.when_text)}</small>{item.location && <small>📍 {item.location}</small>}</div><button className="danger-icon" onClick={() => remove(item)} aria-label={`מחיקת ${item.title}`}>×</button></div>) : <Empty text="עוד אין אירועים בלוח"/>}</Section>
  </section>
}

function Settings({ household, setHousehold, activity, onError }: { household: Household; setHousehold: (v: Household) => void; activity: Activity[]; onError: (v: string) => void }) {
  const [name, setName] = useState(household.name)
  const save = async (e: FormEvent) => { e.preventDefault(); try { const updated = await api.updateHousehold({ name }); setHousehold(updated); hapticSuccess() } catch (err) { onError(String(err)) } }
  return <section className="page"><PageTitle title="הבית" subtitle="הגדרות, פרטיות ופעילות" />
    <Section title="זהות הבית"><form className="settings-form" onSubmit={save}><label>שם הבית<input value={name} onChange={e => setName(e.target.value)}/></label><label>אזור זמן<input value={household.timezone} disabled/></label><button className="primary-button">שמירת שינויים</button></form></Section>
    <Section title="זיכרון ופרטיות"><MemoryPanel onError={onError} /></Section>
    <Section title="פעילות אחרונה">{activity.slice(0, 12).map(item => <ActivityRow key={item.id} item={item}/>)}</Section>
    <div className="privacy-note"><strong>פרטי כברירת מחדל</strong><p>הגישה מאומתת מול Telegram. זיכרונות אוטומטיים ניתנים לכיבוי, עריכה ומחיקה, וכל שינוי נרשם ביומן.</p></div>
  </section>
}

function PageTitle({ title, subtitle }: { title: string; subtitle: string }) { return <div className="page-title"><div><h2>{title}</h2><p>{subtitle}</p></div></div> }
function Section({ title, action, onAction, children }: { title: string; action?: string; onAction?: () => void; children: React.ReactNode }) { return <section className="content-section"><div className="section-heading"><h3>{title}</h3>{action && <button onClick={onAction}>{action}</button>}</div><div className="section-body">{children}</div></section> }
function Empty({ text }: { text: string }) { return <div className="empty-state"><span>◇</span><p>{text}</p></div> }
function ActivityRow({ item }: { item: Activity }) { return <div className="activity-row"><span className="activity-icon">{item.entity_type === 'shopping' ? '🛒' : item.entity_type === 'todo' ? '✓' : item.entity_type === 'event' ? '◷' : item.entity_type === 'memory' ? '🧠' : '•'}</span><div><strong>{item.summary}</strong><small>{item.actor_name || 'הבית'} · {new Intl.DateTimeFormat('he-IL', { hour: '2-digit', minute: '2-digit', day: 'numeric', month: 'short' }).format(new Date(item.created_at * 1000))}</small></div></div> }

export default App
