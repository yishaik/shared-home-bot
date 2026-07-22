import { FormEvent, useMemo, useState } from 'react'
import { api } from './api'
import { hapticSuccess } from './telegram'
import type { Member, Project, TaskCalendarBlock, TaskRelationship, TaskResource, Todo } from './types'

type Props = {
  tasks: Todo[]
  projects: Project[]
  members: Member[]
  setTasks: (value: Todo[]) => void
  setProjects: (value: Project[]) => void
  onError: (value: string) => void
}

type Mode = 'tasks' | 'projects'
type TaskFilter = 'open' | 'mine' | 'blocked' | 'all'

const inputDate = (value?: string | null) => {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60000)
  return local.toISOString().slice(0, 16)
}

const memberLabel = (member: Member) => member.username
  ? `${member.display_name} · @${member.username}`
  : member.display_name

const taskStatusLabel: Record<Todo['status'], string> = {
  todo: 'לביצוע',
  in_progress: 'בתהליך',
  waiting: 'ממתינה',
  completed: 'הושלמה',
  cancelled: 'בוטלה',
}

const projectStatusLabel: Record<Project['status'], string> = {
  planned: 'מתוכנן',
  active: 'פעיל',
  paused: 'מושהה',
  completed: 'הושלם',
  cancelled: 'בוטל',
}

export function WorkPanel({ tasks, projects, members, setTasks, setProjects, onError }: Props) {
  const [mode, setMode] = useState<Mode>('tasks')
  const [filter, setFilter] = useState<TaskFilter>('open')
  const [projectFilter, setProjectFilter] = useState<number | ''>('')
  const [editing, setEditing] = useState<Todo | null>(null)
  const [taskFormOpen, setTaskFormOpen] = useState(false)

  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [priority, setPriority] = useState<Todo['priority']>('normal')
  const [assignedTo, setAssignedTo] = useState('')
  const [dueAt, setDueAt] = useState('')
  const [projectId, setProjectId] = useState('')

  const [projectName, setProjectName] = useState('')
  const [projectDescription, setProjectDescription] = useState('')
  const [projectDue, setProjectDue] = useState('')
  const [projectOwner, setProjectOwner] = useState('')
  const [createFolder, setCreateFolder] = useState(true)

  const visible = useMemo(() => tasks.filter(task => {
    if (projectFilter && task.project_id !== Number(projectFilter)) return false
    if (filter === 'open') return !['completed', 'cancelled'].includes(task.status)
    if (filter === 'blocked') return task.blocked
    if (filter === 'mine') return Boolean(task.assigned_to)
    return true
  }), [tasks, filter, projectFilter])

  const addTask = async (event: FormEvent) => {
    event.preventDefault()
    if (!title.trim()) return
    try {
      const task = await api.addTask({
        title: title.trim(),
        description: description.trim(),
        priority,
        assigned_to: assignedTo ? Number(assignedTo) : null,
        due_at: dueAt ? new Date(dueAt).toISOString() : null,
        project_id: projectId ? Number(projectId) : null,
      })
      setTasks([task, ...tasks])
      setTitle(''); setDescription(''); setAssignedTo(''); setDueAt(''); setProjectId(''); setPriority('normal')
      setTaskFormOpen(false)
      hapticSuccess()
    } catch (error) { onError(String(error)) }
  }

  const addProject = async (event: FormEvent) => {
    event.preventDefault()
    if (!projectName.trim()) return
    try {
      const project = await api.addProject({
        name: projectName.trim(),
        description: projectDescription.trim(),
        owner_id: projectOwner ? Number(projectOwner) : null,
        due_at: projectDue ? new Date(projectDue).toISOString() : null,
        status: 'active',
        create_drive_folder: createFolder,
      })
      setProjects([project, ...projects])
      setProjectName(''); setProjectDescription(''); setProjectDue(''); setProjectOwner('')
      hapticSuccess()
    } catch (error) { onError(String(error)) }
  }

  const complete = async (task: Todo) => {
    try {
      const updated = await api.updateTask(task.id, { status: task.status === 'completed' ? 'todo' : 'completed' })
      setTasks(tasks.map(row => row.id === updated.id ? updated : row))
      hapticSuccess()
    } catch (error) { onError(String(error)) }
  }

  const saveTask = async (event: FormEvent) => {
    event.preventDefault()
    if (!editing) return
    try {
      const updated = await api.updateTask(editing.id, {
        title: editing.title,
        description: editing.description,
        status: editing.status,
        priority: editing.priority,
        project_id: editing.project_id ?? null,
        parent_task_id: editing.parent_task_id ?? null,
        assigned_to: editing.assigned_to ?? null,
        due_at: editing.due_at || null,
        recurrence_rule: editing.recurrence_rule || '',
        estimate_minutes: editing.estimate_minutes ?? null,
      })
      setTasks(tasks.map(row => row.id === updated.id ? updated : row))
      setEditing(updated)
      hapticSuccess()
    } catch (error) { onError(String(error)) }
  }

  const removeTask = async (task: Todo) => {
    if (!confirm(`למחוק את המשימה “${task.title}”?`)) return
    try {
      await api.deleteTask(task.id)
      setTasks(tasks.filter(row => row.id !== task.id))
      setEditing(null)
    } catch (error) { onError(String(error)) }
  }

  const openProject = (project: Project) => {
    setProjectFilter(project.id)
    setMode('tasks')
  }

  return <section className="page">
    <div className="page-title"><div><h2>עבודה משותפת</h2><p>פרויקטים, משימות, זמן וקבצים</p></div></div>
    <div className="segmented work-mode">
      <button className={mode === 'tasks' ? 'active' : ''} onClick={() => setMode('tasks')}>משימות</button>
      <button className={mode === 'projects' ? 'active' : ''} onClick={() => setMode('projects')}>פרויקטים</button>
    </div>

    {mode === 'projects' ? <>
      <form className="stack-form" onSubmit={addProject}>
        <input value={projectName} onChange={event => setProjectName(event.target.value)} placeholder="שם הפרויקט" />
        <textarea value={projectDescription} onChange={event => setProjectDescription(event.target.value)} placeholder="מה מטרת הפרויקט?" />
        <div className="form-row">
          <select value={projectOwner} onChange={event => setProjectOwner(event.target.value)}>
            <option value="">ללא אחראי</option>
            {members.map(member => <option key={member.telegram_user_id} value={member.telegram_user_id}>{memberLabel(member)}</option>)}
          </select>
          <input type="datetime-local" value={projectDue} onChange={event => setProjectDue(event.target.value)} />
        </div>
        <label className="check-label"><input type="checkbox" checked={createFolder} onChange={event => setCreateFolder(event.target.checked)} /> יצירת תיקיית Google Drive לפרויקט</label>
        <button className="primary-button">יצירת פרויקט</button>
      </form>
      <div className="project-grid">
        {projects.map(project => <article className="project-card" key={project.id}>
          <div className="project-card-head"><div><strong>{project.name}</strong><small>{projectStatusLabel[project.status]}{project.owner_name ? ` · ${project.owner_name}` : ''}</small></div><span>{project.progress}%</span></div>
          {project.description && <p>{project.description}</p>}
          <div className="progress-track"><span style={{ width: `${project.progress}%` }} /></div>
          <small>{project.completed_count || 0} מתוך {project.task_count || 0} משימות הושלמו</small>
          <div className="card-actions">
            <button onClick={() => openProject(project)}>פתיחת משימות</button>
            {project.drive_folder_url
              ? <button onClick={() => window.open(project.drive_folder_url, '_blank')}>Drive</button>
              : <button onClick={async () => {
                  try {
                    const folder = await api.createProjectFolder(project.id)
                    setProjects(projects.map(row => row.id === project.id ? { ...row, drive_folder_id: folder.id, drive_folder_url: folder.url } : row))
                  } catch (error) { onError(String(error)) }
                }}>צור תיקייה</button>}
          </div>
        </article>)}
        {!projects.length && <div className="empty-state"><span>◇</span><p>עוד אין פרויקטים</p></div>}
      </div>
    </> : <>
      <details className="content-section" open={taskFormOpen} onToggle={event => setTaskFormOpen(event.currentTarget.open)}>
        <summary style={{ cursor: 'pointer', fontWeight: 800 }}>＋ יצירת משימה חדשה</summary>
        <form className="stack-form" style={{ marginTop: 12, boxShadow: 'none', padding: 0 }} onSubmit={addTask}>
          <input value={title} onChange={event => setTitle(event.target.value)} placeholder="משימה חדשה…" />
          <textarea value={description} onChange={event => setDescription(event.target.value)} placeholder="תיאור או תוצאה רצויה, לא חובה" />
          <div className="form-row">
            <select value={projectId} onChange={event => setProjectId(event.target.value)}>
              <option value="">ללא פרויקט</option>
              {projects.map(project => <option key={project.id} value={project.id}>{project.name}</option>)}
            </select>
            <select value={assignedTo} onChange={event => setAssignedTo(event.target.value)}>
              <option value="">ללא אחראי</option>
              {members.map(member => <option key={member.telegram_user_id} value={member.telegram_user_id}>{memberLabel(member)}</option>)}
            </select>
          </div>
          <div className="form-row">
            <select value={priority} onChange={event => setPriority(event.target.value as Todo['priority'])}>
              <option value="normal">עדיפות רגילה</option><option value="high">דחוף</option><option value="low">נמוכה</option>
            </select>
            <input type="datetime-local" value={dueAt} onChange={event => setDueAt(event.target.value)} />
          </div>
          <button className="primary-button">הוספת משימה</button>
        </form>
      </details>
      <div className="filter-row">
        <select value={projectFilter} onChange={event => setProjectFilter(event.target.value ? Number(event.target.value) : '')}>
          <option value="">כל הפרויקטים</option>
          {projects.map(project => <option key={project.id} value={project.id}>{project.name}</option>)}
        </select>
        <select value={filter} onChange={event => setFilter(event.target.value as TaskFilter)}>
          <option value="open">פתוחות</option><option value="blocked">חסומות</option><option value="mine">משויכות</option><option value="all">הכול</option>
        </select>
      </div>
      <section className="content-section"><div className="section-heading"><h3>משימות</h3><span className="count-chip">{visible.length}</span></div><div className="section-body">
        {visible.map(task => <div className="manage-row" key={task.id}>
          <button className="row-main" onClick={() => complete(task)}>
            <span className={task.status === 'completed' ? 'check-circle checked' : 'check-circle'} />
            <span><strong>{task.title}</strong><small>{task.project_name || 'ללא פרויקט'} · {taskStatusLabel[task.status]}{task.assigned_name ? ` · ${task.assigned_name}` : ''}</small>{task.blocked && <small className="blocked-text">🔒 חסומה על ידי {task.blockers.map(row => row.title).join(', ')}</small>}</span>
            {task.priority === 'high' && <i className="priority">דחוף</i>}
          </button>
          <button className="more-button" onClick={() => setEditing({ ...task })}>•••</button>
        </div>)}
        {!visible.length && <div className="empty-state"><span>◇</span><p>אין משימות בתצוגה הזו</p></div>}
      </div></section>
    </>}

    {editing && <TaskSheet
      task={editing}
      tasks={tasks}
      projects={projects}
      members={members}
      setTask={setEditing}
      onSave={saveTask}
      onDelete={() => removeTask(editing)}
      onUpdate={(updated) => { setEditing(updated); setTasks(tasks.map(row => row.id === updated.id ? updated : row)) }}
      onError={onError}
      onClose={() => setEditing(null)}
    />}
  </section>
}

function TaskSheet({ task, tasks, projects, members, setTask, onSave, onDelete, onUpdate, onError, onClose }: {
  task: Todo
  tasks: Todo[]
  projects: Project[]
  members: Member[]
  setTask: (task: Todo) => void
  onSave: (event: FormEvent) => void
  onDelete: () => void
  onUpdate: (task: Todo) => void
  onError: (value: string) => void
  onClose: () => void
}) {
  const [targetTask, setTargetTask] = useState('')
  const [relationshipType, setRelationshipType] = useState<TaskRelationship['relationship_type']>('blocks')
  const [blockStart, setBlockStart] = useState('')
  const [blockEnd, setBlockEnd] = useState('')
  const [blockLocation, setBlockLocation] = useState('')
  const [linkName, setLinkName] = useState('')
  const [linkUrl, setLinkUrl] = useState('')

  const reload = async () => onUpdate(await api.task(task.id))

  const addRelationship = async () => {
    if (!targetTask) return
    try {
      await api.addTaskRelationship(task.id, {
        source_task_id: task.id,
        target_task_id: Number(targetTask),
        relationship_type: relationshipType,
      })
      setTargetTask('')
      await reload()
    } catch (error) { onError(String(error)) }
  }

  const addBlock = async () => {
    if (!blockStart || !blockEnd) return
    try {
      await api.addCalendarBlock(task.id, {
        start_at: new Date(blockStart).toISOString(),
        end_at: new Date(blockEnd).toISOString(),
        location: blockLocation,
      })
      setBlockStart(''); setBlockEnd(''); setBlockLocation('')
      await reload()
    } catch (error) { onError(String(error)) }
  }

  const createResource = async (type: 'doc' | 'sheet') => {
    try {
      if (type === 'doc') await api.createTaskDoc(task.id)
      else await api.createTaskSheet(task.id)
      await reload()
    } catch (error) { onError(String(error)) }
  }

  const addLink = async () => {
    if (!linkName.trim() || !linkUrl.trim()) return
    try {
      await api.addTaskResourceLink(task.id, { file_name: linkName.trim(), web_url: linkUrl.trim() })
      setLinkName(''); setLinkUrl('')
      await reload()
    } catch (error) { onError(String(error)) }
  }

  return <div className="sheet-backdrop" onClick={onClose}><div className="edit-sheet task-sheet" onClick={event => event.stopPropagation()}>
    <form onSubmit={onSave} className="sheet-section">
      <h3>עריכת משימה</h3>
      <input value={task.title} onChange={event => setTask({ ...task, title: event.target.value })} />
      <textarea value={task.description || ''} onChange={event => setTask({ ...task, description: event.target.value })} placeholder="תיאור" />
      <div className="form-row">
        <select value={task.status} onChange={event => setTask({ ...task, status: event.target.value as Todo['status'] })}>
          {Object.entries(taskStatusLabel).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
        <select value={task.priority} onChange={event => setTask({ ...task, priority: event.target.value as Todo['priority'] })}>
          <option value="normal">רגילה</option><option value="high">דחופה</option><option value="low">נמוכה</option>
        </select>
      </div>
      <div className="form-row">
        <select value={task.project_id || ''} onChange={event => setTask({ ...task, project_id: event.target.value ? Number(event.target.value) : null })}>
          <option value="">ללא פרויקט</option>{projects.map(project => <option key={project.id} value={project.id}>{project.name}</option>)}
        </select>
        <select value={task.assigned_to || ''} onChange={event => setTask({ ...task, assigned_to: event.target.value ? Number(event.target.value) : null })}>
          <option value="">ללא אחראי</option>{members.map(member => <option key={member.telegram_user_id} value={member.telegram_user_id}>{memberLabel(member)}</option>)}
        </select>
      </div>
      <input type="datetime-local" value={inputDate(task.due_at)} onChange={event => setTask({ ...task, due_at: event.target.value ? new Date(event.target.value).toISOString() : null })} />
      <div className="form-row"><input type="number" min="1" value={task.estimate_minutes || ''} onChange={event => setTask({ ...task, estimate_minutes: event.target.value ? Number(event.target.value) : null })} placeholder="הערכת דקות" /><input value={task.recurrence_rule || ''} onChange={event => setTask({ ...task, recurrence_rule: event.target.value })} placeholder="כלל חזרה, למשל שבועי" /></div>
      <button className="primary-button">שמירת פרטים</button>
    </form>

    <section className="sheet-section"><h3>קשרים ותלויות</h3>
      {task.relationships.map(relationship => <div className="compact-row" key={`${relationship.source_task_id}-${relationship.target_task_id}-${relationship.relationship_type}`}><span>{relationship.relationship_type} · {relationship.source_task_id === task.id ? relationship.target_title : relationship.source_title}</span><button onClick={async () => { try { await api.deleteTaskRelationship(task.id, relationship); await reload() } catch (error) { onError(String(error)) } }}>×</button></div>)}
      <div className="form-row"><select value={targetTask} onChange={event => setTargetTask(event.target.value)}><option value="">בחר משימה</option>{tasks.filter(row => row.id !== task.id).map(row => <option key={row.id} value={row.id}>{row.title}</option>)}</select><select value={relationshipType} onChange={event => setRelationshipType(event.target.value as TaskRelationship['relationship_type'])}><option value="blocks">חוסמת</option><option value="follows">אחריה</option><option value="related">קשורה</option><option value="duplicates">כפילות</option></select></div>
      <button className="ghost-button" onClick={addRelationship}>הוספת קשר</button>
    </section>

    <section className="sheet-section"><h3>זמן משוריין ב־Google Calendar</h3>
      {task.calendar_blocks.map((block: TaskCalendarBlock) => <div className="compact-row" key={block.id}><span>{new Date(block.start_at).toLocaleString('he-IL')}–{new Date(block.end_at).toLocaleTimeString('he-IL', { hour: '2-digit', minute: '2-digit' })}</span><button onClick={async () => { try { await api.deleteCalendarBlock(task.id, block.id); await reload() } catch (error) { onError(String(error)) } }}>×</button></div>)}
      <div className="form-row"><input type="datetime-local" value={blockStart} onChange={event => { setBlockStart(event.target.value); if (!blockEnd && event.target.value) setBlockEnd(inputDate(new Date(new Date(event.target.value).getTime() + 60 * 60 * 1000).toISOString())) }} /><input type="datetime-local" value={blockEnd} onChange={event => setBlockEnd(event.target.value)} /></div>
      <input value={blockLocation} onChange={event => setBlockLocation(event.target.value)} placeholder="מיקום, לא חובה" />
      <button className="ghost-button" onClick={addBlock}>שריין זמן ביומן</button>
    </section>

    <section className="sheet-section"><h3>קבצים ומסמכי עבודה</h3>
      {task.resources.map((resource: TaskResource) => <button className="resource-row" key={resource.id} onClick={() => window.open(resource.web_url, '_blank')}><span>{resource.mime_type.includes('spreadsheet') ? '📊' : resource.mime_type.includes('document') ? '📄' : '📎'}</span><span>{resource.file_name}<small>{resource.relationship}</small></span></button>)}
      <div className="card-actions"><button onClick={() => createResource('doc')}>צור Google Doc</button><button onClick={() => createResource('sheet')}>צור Google Sheet</button></div>
      <input value={linkName} onChange={event => setLinkName(event.target.value)} placeholder="שם קישור או קובץ" />
      <input value={linkUrl} onChange={event => setLinkUrl(event.target.value)} placeholder="כתובת Drive או קישור" />
      <button className="ghost-button" onClick={addLink}>צרף קישור</button>
    </section>

    <button className="danger-button" onClick={onDelete}>מחיקת משימה</button>
    <button className="ghost-button" onClick={onClose}>סגירה</button>
  </div></div>
}
