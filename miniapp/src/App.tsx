import { FormEvent, useCallback, useEffect, useMemo, useState } from 'react'
import { api, authenticate } from './api'
import { hapticSelection, hapticSuccess, tg } from './telegram'
import type { Activity, Dashboard, HomeEvent, Household, ShoppingItem, Todo } from './types'

type Tab = 'home' | 'shopping' | 'tasks' | 'events' | 'settings'

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

  const load = useCallback(async () => {
    const [home, shop, taskRows, eventRows, activityRows] = await Promise.all([api.home(), api.shopping(), api.tasks(), api.events(), api.activity()])
    setDashboard(home); setShopping(shop); setTasks(taskRows); setEvents(eventRows); setActivity(activityRows); setHousehold(home.household)
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
        await load()
      } catch (e) { setError(e instanceof Error ? e.message : 'אירעה שגיאה') }
      finally { setLoading(false) }
    })()
  }, [load])

  const navigate = (next: Tab) => { hapticSelection(); setTab(next); history.replaceState(null, '', next === 'home' ? '/app' : `/app?tab=${next}`) }

  if (loading) return <Loading />
  if (error && !dashboard) return <FatalError message={error} />

  return (
    <div className="app-shell">
      <header className="topbar">
        <div><span className="eyebrow">המרכז המשפחתי</span><h1>{household?.name || 'הבית שלנו'}</h1></div>
        <div className="avatar" aria-label={userName || 'משתמש'}>{(userName || 'ב').slice(0, 1)}</div>
      </header>
      {error && <button className="error-banner" onClick={() => setError('')}>{error} ×</button>}
      <main>
        {tab === 'home' && dashboard && <Home dashboard={dashboard} onNavigate={navigate} />}
        {tab === 'shopping' && <Shopping items={shopping} setItems={setShopping} onError={setError} />}
        {tab === 'tasks' && <Tasks items={tasks} setItems={setTasks} onError={setError} />}
        {tab === 'events' && <Events items={events} setItems={setEvents} onError={setError} />}
        {tab === 'settings' && household && <Settings household={household} setHousehold={setHousehold} activity={activity} onError={setError} />}
      </main>
      <nav className="bottom-nav" aria-label="ניווט ראשי">
        <NavButton active={tab === 'home'} icon="⌂" label="בית" onClick={() => navigate('home')} />
        <NavButton active={tab === 'shopping'} icon="🛒" label="קניות" onClick={() => navigate('shopping')} />
        <NavButton active={tab === 'tasks'} icon="✓" label="משימות" onClick={() => navigate('tasks')} />
        <NavButton active={tab === 'events'} icon="◷" label="אירועים" onClick={() => navigate('events')} />
        <NavButton active={tab === 'settings'} icon="⚙" label="עוד" onClick={() => navigate('settings')} />
      </nav>
    </div>
  )
}

function Loading() { return <div className="loading-screen"><div className="brand-mark">H</div><div className="skeleton wide"/><div className="skeleton"/><div className="skeleton"/></div> }
function FatalError({ message }: { message: string }) { return <div className="fatal"><div className="brand-mark">H</div><h1>לא הצלחנו לפתוח את הבית</h1><p>{message}</p></div> }
function NavButton({ active, icon, label, onClick }: { active: boolean; icon: string; label: string; onClick: () => void }) { return <button className={active ? 'nav-button active' : 'nav-button'} onClick={onClick}><span>{icon}</span><small>{label}</small></button> }

function Home({ dashboard, onNavigate }: { dashboard: Dashboard; onNavigate: (tab: Tab) => void }) {
  const greeting = new Date().getHours() < 12 ? 'בוקר טוב' : new Date().getHours() < 18 ? 'צהריים טובים' : 'ערב טוב'
  return <section className="page home-page">
    <div className="hero-card"><span className="eyebrow">{greeting}</span><h2>הכול מתואם. הבית בידיים שלכם.</h2><p>{dashboard.counts.todos} משימות ו־{dashboard.counts.shopping} פריטים לקנייה מחכים לטיפול.</p></div>
    <div className="metric-grid">
      <button className="metric" onClick={() => onNavigate('tasks')}><strong>{dashboard.counts.todos}</strong><span>משימות פתוחות</span></button>
      <button className="metric" onClick={() => onNavigate('shopping')}><strong>{dashboard.counts.shopping}</strong><span>ברשימת הקניות</span></button>
      <button className="metric" onClick={() => onNavigate('events')}><strong>{dashboard.counts.events}</strong><span>אירועים שמורים</span></button>
    </div>
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
  const grouped = useMemo(() => Object.entries(items.reduce<Record<string, ShoppingItem[]>>((acc, item) => { const key = item.category || 'כללי'; (acc[key] ||= []).push(item); return acc }, {})), [items])
  const add = async (e: FormEvent) => { e.preventDefault(); if (!value.trim()) return; try { const item = await api.addShopping(value.trim(), qty); setItems([item, ...items]); setValue(''); setQty('1'); hapticSuccess() } catch (err) { onError(String(err)) } }
  const toggle = async (item: ShoppingItem) => { const before = items; setItems(items.filter(x => x.id !== item.id)); try { await api.updateShopping(item.id, { done: 1 }); hapticSuccess() } catch (err) { setItems(before); onError(String(err)) } }
  return <section className="page"><PageTitle title="רשימת קניות" subtitle={`${items.length} פריטים פתוחים`} />
    <form className="quick-form" onSubmit={add}><input aria-label="פריט חדש" value={value} onChange={e => setValue(e.target.value)} placeholder="מה חסר בבית?"/><input className="qty-input" aria-label="כמות" value={qty} onChange={e => setQty(e.target.value)}/><button>＋</button></form>
    {grouped.length ? grouped.map(([category, rows]) => <Section key={category} title={category}>{rows.map(item => <button className="check-row" key={item.id} onClick={() => toggle(item)}><span className="check-circle"/><span><strong>{item.item}</strong><small>כמות: {item.qty}</small></span></button>)}</Section>) : <Empty text="הרשימה ריקה. אפשר להוסיף משהו למעלה."/>}
  </section>
}

function Tasks({ items, setItems, onError }: { items: Todo[]; setItems: (v: Todo[]) => void; onError: (v: string) => void }) {
  const [value, setValue] = useState('')
  const add = async (e: FormEvent) => { e.preventDefault(); if (!value.trim()) return; try { const item = await api.addTask(value.trim()); setItems([item, ...items]); setValue(''); hapticSuccess() } catch (err) { onError(String(err)) } }
  const toggle = async (item: Todo) => { const before = items; setItems(items.filter(x => x.id !== item.id)); try { await api.updateTask(item.id, { done: 1 }); hapticSuccess() } catch (err) { setItems(before); onError(String(err)) } }
  return <section className="page"><PageTitle title="משימות" subtitle={`${items.length} פתוחות`} />
    <form className="quick-form" onSubmit={add}><input value={value} onChange={e => setValue(e.target.value)} placeholder="משימה חדשה…"/><button>＋</button></form>
    <Section title="לביצוע">{items.length ? items.map(item => <button className="check-row" key={item.id} onClick={() => toggle(item)}><span className="check-circle"/><span><strong>{item.title}</strong><small>{item.due_at ? dateText(item.due_at) : item.priority === 'high' ? 'עדיפות גבוהה' : 'ללא מועד'}</small></span>{item.priority === 'high' && <i className="priority">דחוף</i>}</button>) : <Empty text="כל המשימות הושלמו"/>}</Section>
  </section>
}

function Events({ items, setItems, onError }: { items: HomeEvent[]; setItems: (v: HomeEvent[]) => void; onError: (v: string) => void }) {
  const [title, setTitle] = useState('')
  const [start, setStart] = useState('')
  const add = async (e: FormEvent) => { e.preventDefault(); if (!title.trim() || !start) return; try { const item = await api.addEvent({ title: title.trim(), start_at: new Date(start).toISOString() }); setItems([...items, item]); setTitle(''); setStart(''); hapticSuccess() } catch (err) { onError(String(err)) } }
  return <section className="page"><PageTitle title="אירועים" subtitle="הלו״ז המשותף" />
    <form className="event-form" onSubmit={add}><input value={title} onChange={e => setTitle(e.target.value)} placeholder="שם האירוע"/><input type="datetime-local" value={start} onChange={e => setStart(e.target.value)}/><button className="primary-button">הוספת אירוע</button></form>
    <Section title="בלוח הבית">{items.length ? items.map(item => <div className="event-card" key={item.id}><div className="date-tile"><strong>{new Date(item.start_at || item.when_text).getDate() || '•'}</strong><small>{dateText(item.start_at || item.when_text).split(' ')[1] || ''}</small></div><div><strong>{item.title}</strong><small>{dateText(item.start_at || item.when_text)}</small>{item.location && <small>{item.location}</small>}</div></div>) : <Empty text="עוד אין אירועים בלוח"/>}</Section>
  </section>
}

function Settings({ household, setHousehold, activity, onError }: { household: Household; setHousehold: (v: Household) => void; activity: Activity[]; onError: (v: string) => void }) {
  const [name, setName] = useState(household.name)
  const save = async (e: FormEvent) => { e.preventDefault(); try { const updated = await api.updateHousehold({ name }); setHousehold(updated); hapticSuccess() } catch (err) { onError(String(err)) } }
  return <section className="page"><PageTitle title="הבית" subtitle="הגדרות ופעילות" />
    <Section title="זהות הבית"><form className="settings-form" onSubmit={save}><label>שם הבית<input value={name} onChange={e => setName(e.target.value)}/></label><label>אזור זמן<input value={household.timezone} disabled/></label><button className="primary-button">שמירת שינויים</button></form></Section>
    <Section title="פעילות אחרונה">{activity.slice(0, 12).map(item => <ActivityRow key={item.id} item={item}/>)}</Section>
    <div className="privacy-note"><strong>פרטי כברירת מחדל</strong><p>הגישה מאומתת מול Telegram והמידע משותף רק לחברי הבית שהוגדרו.</p></div>
  </section>
}

function PageTitle({ title, subtitle }: { title: string; subtitle: string }) { return <div className="page-title"><div><h2>{title}</h2><p>{subtitle}</p></div></div> }
function Section({ title, action, onAction, children }: { title: string; action?: string; onAction?: () => void; children: React.ReactNode }) { return <section className="content-section"><div className="section-heading"><h3>{title}</h3>{action && <button onClick={onAction}>{action}</button>}</div><div className="section-body">{children}</div></section> }
function Empty({ text }: { text: string }) { return <div className="empty-state"><span>◇</span><p>{text}</p></div> }
function ActivityRow({ item }: { item: Activity }) { return <div className="activity-row"><span className="activity-icon">{item.entity_type === 'shopping' ? '🛒' : item.entity_type === 'todo' ? '✓' : item.entity_type === 'event' ? '◷' : '•'}</span><div><strong>{item.summary}</strong><small>{item.actor_name || 'הבית'} · {new Intl.DateTimeFormat('he-IL', { hour: '2-digit', minute: '2-digit', day: 'numeric', month: 'short' }).format(new Date(item.created_at * 1000))}</small></div></div> }

export default App
