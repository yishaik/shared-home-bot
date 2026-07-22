import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from './api'
import type { InboxCounts, InboxProposal, InboxStatus, InboxStep } from './types'
import './inbox.css'

type Props = {
  onError: (message: string) => void
  onCountsChange: (counts: InboxCounts) => void
}

type Filter = 'attention' | 'history' | 'all'

const attentionStatuses = 'pending,failed,needs_review,editing,executing'
const historyStatuses = 'completed,cancelled,expired'

const statusLabel: Record<InboxStatus, string> = {
  pending: 'ממתין לאישור',
  executing: 'מתבצע',
  completed: 'הושלם',
  failed: 'נכשל',
  needs_review: 'דורש בדיקה',
  editing: 'בעריכה',
  cancelled: 'בוטל',
  expired: 'פג תוקף',
}

const riskLabel = { low: 'נמוך', medium: 'בינוני', high: 'גבוה' }

const formatTime = (value?: number | null) => {
  if (!value) return ''
  return new Intl.DateTimeFormat('he-IL', {
    day: 'numeric',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(value * 1000))
}

const toolLabel = (name: string) => {
  const labels: Record<string, string> = {
    shop_add: 'הוספה לקניות',
    todo_add: 'יצירת משימה',
    todo_update: 'עדכון משימה',
    project_add: 'יצירת פרויקט',
    event_add: 'יצירת אירוע',
    event_update: 'עדכון אירוע',
    event_delete: 'מחיקת אירוע',
    remind_add: 'יצירת תזכורת',
    todo_schedule: 'שריון זמן',
    site_publish: 'פרסום אתר',
  }
  return labels[name] || name.replaceAll('_', ' ')
}

function StepRow({ step }: { step: InboxStep }) {
  const args = Object.entries(step.arguments || {}).filter(([, value]) => value !== '' && value !== null && value !== undefined)
  return <div className={`inbox-step step-${step.status}`}>
    <div className="inbox-step-head">
      <strong>{toolLabel(step.tool_name)}</strong>
      <span>{step.status === 'completed' ? '✓' : step.status === 'failed' ? '!' : step.status === 'uncertain' ? '?' : step.position + 1}</span>
    </div>
    {args.length > 0 && <dl>{args.map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{Array.isArray(value) ? value.join(', ') : typeof value === 'object' ? JSON.stringify(value) : String(value)}</dd></div>)}</dl>}
    {step.last_error && <p className="step-error">{step.last_error}</p>}
  </div>
}

export function InboxPanel({ onError, onCountsChange }: Props) {
  const [filter, setFilter] = useState<Filter>('attention')
  const [items, setItems] = useState<InboxProposal[]>([])
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState<Record<string, boolean>>({})

  const reload = useCallback(async (quiet = false) => {
    if (!quiet) setLoading(true)
    try {
      const status = filter === 'attention' ? attentionStatuses : filter === 'history' ? historyStatuses : undefined
      const [proposals, counts] = await Promise.all([api.inbox(status), api.inboxCounts()])
      setItems(proposals)
      onCountsChange(counts)
    } catch (caught) {
      onError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      if (!quiet) setLoading(false)
    }
  }, [filter, onCountsChange, onError])

  useEffect(() => { reload() }, [reload])
  useEffect(() => {
    const refresh = () => { if (document.visibilityState === 'visible') reload(true) }
    const timer = window.setInterval(refresh, 15000)
    document.addEventListener('visibilitychange', refresh)
    return () => {
      window.clearInterval(timer)
      document.removeEventListener('visibilitychange', refresh)
    }
  }, [reload])

  const replace = (proposal: InboxProposal) => {
    setItems(current => {
      const next = current.map(item => item.id === proposal.id ? proposal : item)
      const shouldRemain =
        filter === 'all'
        || (filter === 'attention' && ['pending', 'failed', 'needs_review', 'editing', 'executing'].includes(proposal.status))
        || (filter === 'history' && ['completed', 'cancelled', 'expired'].includes(proposal.status))
      return shouldRemain ? next : next.filter(item => item.id !== proposal.id)
    })
  }

  const loadDetails = async (proposal: InboxProposal) => {
    if (proposal.audit) return
    try {
      replace(await api.inboxProposal(proposal.id))
    } catch (caught) {
      onError(caught instanceof Error ? caught.message : String(caught))
    }
  }

  const mutate = async (proposal: InboxProposal, action: 'approve' | 'retry' | 'cancel' | 'edit') => {
    setBusy(current => ({ ...current, [proposal.id]: true }))
    try {
      const updated =
        action === 'approve' ? await api.approveInbox(proposal.id, proposal.version)
        : action === 'retry' ? await api.retryInbox(proposal.id, proposal.version)
        : action === 'cancel' ? await api.cancelInbox(proposal.id, proposal.version)
        : await api.editInbox(proposal.id, proposal.version)
      replace(updated)
      onCountsChange(await api.inboxCounts())
    } catch (caught) {
      onError(caught instanceof Error ? caught.message : String(caught))
      await reload(true)
    } finally {
      setBusy(current => ({ ...current, [proposal.id]: false }))
    }
  }

  const pending = useMemo(() => items.filter(item => item.status === 'pending').length, [items])

  return <section className="page inbox-page">
    <div className="page-title inbox-title">
      <div>
        <span className="eyebrow">Action Inbox</span>
        <h2>אישורים ופעולות</h2>
        <p>{pending ? `${pending} פעולות ממתינות להחלטה` : 'אין כרגע פעולות ממתינות'}</p>
      </div>
      <button className="icon-button" onClick={() => reload()} aria-label="רענון Inbox">↻</button>
    </div>

    <div className="segmented inbox-filters">
      <button className={filter === 'attention' ? 'active' : ''} onClick={() => setFilter('attention')}>דורש תשומת לב</button>
      <button className={filter === 'history' ? 'active' : ''} onClick={() => setFilter('history')}>היסטוריה</button>
      <button className={filter === 'all' ? 'active' : ''} onClick={() => setFilter('all')}>הכול</button>
    </div>

    {loading ? <div className="inbox-loading"><div className="skeleton wide"/><div className="skeleton wide"/></div>
      : items.length === 0 ? <div className="empty-card"><span>📥</span><strong>ה־Inbox נקי</strong><p>פעולות מורכבות שיגיעו מהבוט יופיעו כאן לאישור.</p></div>
      : <div className="inbox-list">{items.map(proposal => {
        const isBusy = Boolean(busy[proposal.id])
        return <article className={`inbox-card status-${proposal.status} risk-${proposal.risk_level}`} key={proposal.id}>
          <div className="inbox-card-head">
            <div>
              <div className="inbox-meta">
                <span className={`status-pill status-${proposal.status}`}>{statusLabel[proposal.status]}</span>
                <span className={`risk-pill risk-${proposal.risk_level}`}>סיכון {riskLabel[proposal.risk_level]}</span>
              </div>
              <h3>{proposal.source_text || proposal.summary.split('\n').find(line => line.startsWith('•'))?.replace(/^•\s*/, '') || 'פעולה חדשה'}</h3>
              <small>{formatTime(proposal.created_at)} · {proposal.steps.length} {proposal.steps.length === 1 ? 'צעד' : 'צעדים'}</small>
            </div>
          </div>

          <p className="inbox-summary">{proposal.summary.replace(/^📥 ממתין לאישור\s*/u, '').replace(/^⚙️ מבצע פעולה\s*/u, '').replace(/\n\nהפעולות טרם בוצעו\.$/u, '')}</p>
          {proposal.last_error && <div className="inbox-warning">{proposal.status === 'needs_review' ? '🛑' : '⚠️'} {proposal.last_error}</div>}

          <details className="inbox-details" onToggle={event => { if (event.currentTarget.open) loadDetails(proposal) }}>
            <summary>פרטי הפעולה ובקרת Audit</summary>
            <div className="inbox-steps">{proposal.steps.map(step => <StepRow key={`${proposal.id}-${step.position}`} step={step}/>)}</div>
            {proposal.audit?.length ? <div className="inbox-audit">
              <h4>Audit trail</h4>
              {proposal.audit.map(entry => <div key={entry.id}><span>{formatTime(entry.created_at)}</span><strong>{entry.action}</strong><small>{entry.from_status}{entry.to_status ? ` → ${entry.to_status}` : ''}</small></div>)}
            </div> : null}
          </details>

          <div className="inbox-actions">
            {proposal.can_approve && proposal.status === 'pending' && <button className="primary-button" disabled={isBusy} onClick={() => mutate(proposal, 'approve')}>{isBusy ? 'מבצע…' : 'אישור וביצוע'}</button>}
            {proposal.can_retry && <button className="primary-button" disabled={isBusy} onClick={() => mutate(proposal, 'retry')}>{isBusy ? 'מנסה…' : 'ניסיון חוזר בטוח'}</button>}
            {proposal.status === 'pending' && <button className="secondary-button" disabled={isBusy} onClick={() => mutate(proposal, 'edit')}>עריכה דרך הבוט</button>}
            {proposal.can_cancel && <button className="danger-text-button" disabled={isBusy} onClick={() => mutate(proposal, 'cancel')}>ביטול</button>}
          </div>
        </article>
      })}</div>}
  </section>
}
