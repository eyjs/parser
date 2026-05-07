import type {
  ParseResponse,
  ParseResult,
  ParseState,
  HistoryItem,
  QueueStatus,
  Version,
  DiffResult,
  CompletedPagesResponse,
  PageMarkdown,
} from './types'

const API_BASE = import.meta.env.VITE_API_BASE_URL || ''

class ApiClientError extends Error {
  constructor(
    message: string,
    public status: number,
    public code?: string,
  ) {
    super(message)
    this.name = 'ApiClientError'
  }
}

async function request<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const url = `${API_BASE}${path}`
  const response = await fetch(url, {
    ...options,
    headers: {
      ...options.headers,
    },
  })

  if (!response.ok) {
    let errorMessage = `HTTP ${response.status}`
    let errorCode = 'HTTP_ERROR'

    try {
      const body = await response.json()
      if (body.error) {
        errorMessage = body.error.message || body.error
        errorCode = body.error.code || errorCode
      }
    } catch {
      // ignore JSON parse failure
    }

    throw new ApiClientError(errorMessage, response.status, errorCode)
  }

  // Handle no-content responses
  if (response.status === 204) {
    return undefined as unknown as T
  }

  const body = await response.json()
  if (body && typeof body === 'object' && 'data' in body) {
    return body.data as T
  }
  return body as T
}

// Parse endpoints

export async function uploadFiles(files: File[]): Promise<ParseResponse> {
  const formData = new FormData()
  for (const file of files) {
    formData.append('files', file)
  }

  return request<ParseResponse>('/api/parse', {
    method: 'POST',
    body: formData,
  })
}

export function getParseStatusUrl(taskId: string): string {
  return `${API_BASE}/api/parse/${taskId}/status`
}

export async function getParseResult(taskId: string): Promise<ParseResult> {
  return request<ParseResult>(`/api/parse/${taskId}/result`)
}

export async function getParseState(taskId: string): Promise<ParseState> {
  return request<ParseState>(`/api/parse/${taskId}/state`)
}

export async function getActiveTasks(): Promise<ParseState[]> {
  return request<ParseState[]>('/api/parse/active')
}

export async function getCompletedPages(taskId: string): Promise<CompletedPagesResponse> {
  return request<CompletedPagesResponse>(`/api/parse/${taskId}/pages`)
}

export async function getPageMarkdown(taskId: string, pageNum: number): Promise<PageMarkdown> {
  return request<PageMarkdown>(`/api/parse/${taskId}/pages/${pageNum}`)
}

// History endpoints

export async function getHistory(): Promise<HistoryItem[]> {
  return request<HistoryItem[]>('/api/history')
}

export async function deleteHistory(taskId: string): Promise<void> {
  return request<void>(`/api/history/${taskId}`, {
    method: 'DELETE',
  })
}

// Save / Export endpoints

export async function saveMarkdown(taskId: string, markdown: string): Promise<void> {
  return request<void>(`/api/save/${taskId}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ markdown }),
  })
}

export function getExportUrl(taskId: string): string {
  return `${API_BASE}/api/export/${taskId}`
}

// Queue endpoint

export async function getQueueStatus(): Promise<QueueStatus> {
  return request<QueueStatus>('/api/queue/status')
}

// Cancel endpoint

export async function cancelTask(taskId: string): Promise<void> {
  return request<void>(`/api/parse/${taskId}/cancel`, {
    method: 'POST',
  })
}

// Version endpoints

export async function getVersions(taskId: string): Promise<Version[]> {
  return request<Version[]>(`/api/versions/${taskId}`)
}

export async function getDiff(
  taskId: string,
  v1: string,
  v2: string,
): Promise<DiffResult> {
  const params = new URLSearchParams({ v1, v2 })
  return request<DiffResult>(`/api/diff/${taskId}?${params}`)
}

// Upload URL helper

export function getUploadUrl(path: string): string {
  return `${API_BASE}/uploads/${path}`
}

export { ApiClientError }
