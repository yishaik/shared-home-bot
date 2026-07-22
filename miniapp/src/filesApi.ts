export type DriveItem = {
  id: string
  name: string
  mime_type: string
  is_folder: boolean
  size: number | null
  created_time?: string | null
  modified_time?: string | null
  web_view_link: string
  web_content_link?: string | null
  thumbnail_link?: string | null
  parents: string[]
}

export type DriveStatus = {
  connected: boolean
  detail: string
  root: DriveItem | null
  shared_emails: string[]
}

export type DriveListing = {
  root: DriveItem
  folder: DriveItem
  items: DriveItem[]
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = sessionStorage.getItem('home_session') || ''
  const bodyIsForm = options.body instanceof FormData
  const response = await fetch(path, {
    ...options,
    headers: {
      ...(bodyIsForm ? {} : { 'Content-Type': 'application/json' }),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(options.headers || {}),
    },
  })
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}))
    throw new Error(payload.detail || `Request failed (${response.status})`)
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export const filesApi = {
  status: () => request<DriveStatus>('/api/files/status'),
  list: (folderId?: string) => request<DriveListing>(`/api/files${folderId ? `?folder_id=${encodeURIComponent(folderId)}` : ''}`),
  createFolder: (name: string, parentId?: string) => request<DriveItem>('/api/files/folders', {
    method: 'POST',
    body: JSON.stringify({ name, parent_id: parentId || null }),
  }),
  upload: (file: File, folderId?: string) => {
    const body = new FormData()
    body.append('upload', file)
    if (folderId) body.append('folder_id', folderId)
    return request<DriveItem>('/api/files/upload', { method: 'POST', body })
  },
  delete: (fileId: string) => request<void>(`/api/files/${encodeURIComponent(fileId)}`, { method: 'DELETE' }),
}
