import { ChangeEvent, FormEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { filesApi, type DriveItem, type DriveStatus } from './filesApi'
import { hapticSelection, hapticSuccess } from './telegram'

type Breadcrumb = { id: string; name: string }

const formatSize = (size: number | null) => {
  if (size == null) return ''
  if (size < 1024) return `${size} B`
  if (size < 1024 ** 2) return `${Math.round(size / 1024)} KB`
  return `${(size / 1024 ** 2).toFixed(size < 10 * 1024 ** 2 ? 1 : 0)} MB`
}

const formatDate = (value?: string | null) => {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  return new Intl.DateTimeFormat('he-IL', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }).format(date)
}

const fileIcon = (item: DriveItem) => {
  if (item.is_folder) return '📁'
  if (item.mime_type.includes('pdf')) return '📕'
  if (item.mime_type.includes('image')) return '🖼️'
  if (item.mime_type.includes('spreadsheet') || item.mime_type.includes('excel')) return '📊'
  if (item.mime_type.includes('document') || item.mime_type.includes('word') || item.mime_type.includes('text')) return '📄'
  if (item.mime_type.includes('video')) return '🎬'
  if (item.mime_type.includes('audio')) return '🎵'
  return '📎'
}

export function FilesPage({ onError }: { onError: (message: string) => void }) {
  const [status, setStatus] = useState<DriveStatus | null>(null)
  const [items, setItems] = useState<DriveItem[]>([])
  const [folder, setFolder] = useState<DriveItem | null>(null)
  const [breadcrumbs, setBreadcrumbs] = useState<Breadcrumb[]>([])
  const [query, setQuery] = useState('')
  const [folderName, setFolderName] = useState('')
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState('')
  const fileInput = useRef<HTMLInputElement>(null)

  const loadFolder = useCallback(async (folderId?: string, nextBreadcrumbs?: Breadcrumb[]) => {
    const listing = await filesApi.list(folderId)
    setItems(listing.items)
    setFolder(listing.folder)
    setBreadcrumbs(nextBreadcrumbs || [{ id: listing.root.id, name: listing.root.name }])
  }, [])

  const initialize = useCallback(async () => {
    setLoading(true)
    try {
      const nextStatus = await filesApi.status()
      setStatus(nextStatus)
      if (nextStatus.connected) await loadFolder()
    } catch (error) {
      onError(error instanceof Error ? error.message : String(error))
    } finally {
      setLoading(false)
    }
  }, [loadFolder, onError])

  useEffect(() => { initialize() }, [initialize])

  const visibleItems = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase('he')
    return items
      .filter(item => !needle || item.name.toLocaleLowerCase('he').includes(needle))
      .sort((a, b) => Number(b.is_folder) - Number(a.is_folder) || a.name.localeCompare(b.name, 'he'))
  }, [items, query])

  const refresh = async () => {
    try {
      setBusy('refresh')
      await loadFolder(folder?.id, breadcrumbs)
    } catch (error) {
      onError(error instanceof Error ? error.message : String(error))
    } finally {
      setBusy('')
    }
  }

  const enterFolder = async (item: DriveItem) => {
    try {
      hapticSelection()
      setBusy(item.id)
      await loadFolder(item.id, [...breadcrumbs, { id: item.id, name: item.name }])
      setQuery('')
    } catch (error) {
      onError(error instanceof Error ? error.message : String(error))
    } finally {
      setBusy('')
    }
  }

  const navigateBreadcrumb = async (crumb: Breadcrumb, index: number) => {
    try {
      setBusy(crumb.id)
      await loadFolder(crumb.id, breadcrumbs.slice(0, index + 1))
      setQuery('')
    } catch (error) {
      onError(error instanceof Error ? error.message : String(error))
    } finally {
      setBusy('')
    }
  }

  const createFolder = async (event: FormEvent) => {
    event.preventDefault()
    if (!folderName.trim() || !folder) return
    try {
      setBusy('create-folder')
      const created = await filesApi.createFolder(folderName.trim(), folder.id)
      setItems(current => [created, ...current])
      setFolderName('')
      hapticSuccess()
    } catch (error) {
      onError(error instanceof Error ? error.message : String(error))
    } finally {
      setBusy('')
    }
  }

  const uploadFiles = async (event: ChangeEvent<HTMLInputElement>) => {
    const selected = Array.from(event.target.files || [])
    if (!selected.length || !folder) return
    try {
      setBusy('upload')
      const uploaded: DriveItem[] = []
      for (const file of selected) uploaded.push(await filesApi.upload(file, folder.id))
      setItems(current => [...uploaded, ...current])
      hapticSuccess()
    } catch (error) {
      onError(error instanceof Error ? error.message : String(error))
    } finally {
      event.target.value = ''
      setBusy('')
    }
  }

  const remove = async (item: DriveItem) => {
    const label = item.is_folder ? 'התיקייה וכל התוכן שלה' : `הקובץ “${item.name}”`
    if (!confirm(`למחוק את ${label}?`)) return
    const before = items
    setItems(current => current.filter(row => row.id !== item.id))
    try {
      setBusy(item.id)
      await filesApi.delete(item.id)
      hapticSuccess()
    } catch (error) {
      setItems(before)
      onError(error instanceof Error ? error.message : String(error))
    } finally {
      setBusy('')
    }
  }

  if (loading) return <section className="page files-page"><div className="files-loading"><span>☁️</span><strong>מתחבר ל־Google Drive…</strong></div></section>

  if (!status?.connected) {
    return <section className="page files-page">
      <div className="page-title"><div><h2>קבצים</h2><p>המסמכים המשותפים של הבית</p></div></div>
      <div className="drive-setup-card">
        <span>☁️</span>
        <h3>Google Drive עדיין לא מחובר</h3>
        <p>{status?.detail || 'יש להשלים OAuth לחשבון Google המיועד של הבוט.'}</p>
        <button className="primary-button" onClick={initialize}>בדיקה מחדש</button>
      </div>
    </section>
  }

  return <section className="page files-page">
    <div className="page-title files-title">
      <div><h2>קבצים</h2><p>{items.length} פריטים בתיקייה</p></div>
      <button className={busy === 'refresh' ? 'icon-button spinning' : 'icon-button'} onClick={refresh} aria-label="רענון קבצים">↻</button>
    </div>

    <div className="drive-summary">
      <div><span>☁️</span><div><strong>Google Drive מחובר</strong><small>{status.shared_emails.length ? `משותף עם ${status.shared_emails.length} חשבונות` : 'הגישה למשתמשים מנוהלת בתיקיית Drive'}</small></div></div>
      {status.root?.web_view_link && <button onClick={() => window.open(status.root!.web_view_link, '_blank', 'noopener,noreferrer')}>פתיחה ב־Drive ↗</button>}
    </div>

    <div className="file-toolbar">
      <button className="primary-button" disabled={Boolean(busy)} onClick={() => fileInput.current?.click()}>＋ העלאת קובץ</button>
      <input ref={fileInput} hidden multiple type="file" onChange={uploadFiles}/>
      <form onSubmit={createFolder}>
        <input value={folderName} onChange={event => setFolderName(event.target.value)} placeholder="שם תיקייה חדשה"/>
        <button disabled={!folderName.trim() || Boolean(busy)} aria-label="יצירת תיקייה">📁＋</button>
      </form>
    </div>

    <div className="breadcrumbs" aria-label="מיקום בתיקיות">
      {breadcrumbs.map((crumb, index) => <span key={crumb.id}><button disabled={index === breadcrumbs.length - 1 || Boolean(busy)} onClick={() => navigateBreadcrumb(crumb, index)}>{crumb.name}</button>{index < breadcrumbs.length - 1 && <i>‹</i>}</span>)}
    </div>

    <input className="search-input" value={query} onChange={event => setQuery(event.target.value)} placeholder="חיפוש בתיקייה…"/>

    <div className="content-section file-list">
      {visibleItems.length ? visibleItems.map(item => <div className="file-row" key={item.id}>
        <button className="file-main" disabled={busy === item.id} onClick={() => item.is_folder ? enterFolder(item) : window.open(item.web_view_link, '_blank', 'noopener,noreferrer')}>
          <span className="file-icon">{fileIcon(item)}</span>
          <span><strong>{item.name}</strong><small>{item.is_folder ? 'תיקייה' : [formatSize(item.size), formatDate(item.modified_time)].filter(Boolean).join(' · ')}</small></span>
          <i>{item.is_folder ? '‹' : '↗'}</i>
        </button>
        <button className="danger-icon" disabled={busy === item.id} onClick={() => remove(item)} aria-label={`מחיקת ${item.name}`}>×</button>
      </div>) : <div className="empty-state"><span>📂</span><p>{query ? 'לא נמצאו קבצים מתאימים' : 'התיקייה עדיין ריקה'}</p></div>}
    </div>

    <div className="files-note">קבצים שמועלים כאן נשמרים בחשבון Google של הבוט ויורשים את הרשאות השיתוף של תיקיית הבית.</div>
  </section>
}
