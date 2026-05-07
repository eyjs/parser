// API type definitions for DocForge backend

// Task status
export type TaskStatus = 'queued' | 'running' | 'done' | 'error' | 'cancelled'

// SSE event types
export type SSEEventType =
  | 'profiling'
  | 'strategy_report'
  | 'noise_learning'
  | 'page_progress'
  | 'page_result'
  | 'table_merging'
  | 'assembling'
  | 'done'
  | 'error'
  | 'heartbeat'
  | 'catchup'
  | 'progress'

export interface SSEEvent {
  event: SSEEventType
  data: Record<string, unknown>
}

// Parse
export interface ParseResponse {
  task_id: string
  task_ids: string[]
}

export interface ParseResult {
  task_id: string
  filename: string
  markdown: string
  metadata: Record<string, unknown>
  stats: Record<string, unknown>
  completed_at: string
  pdf_path: string
}

export interface ParseState {
  task_id: string
  filename: string
  status: TaskStatus
  pct: number
  completed_pages: number
  total_pages: number
  current_stage: string
  started_at: string | null
  finished_at: string | null
  error_message: string | null
  completed_page_numbers: number[]
  last_event: SSEEvent | null
}

// History
export interface HistoryItem {
  task_id: string
  filename: string
  status: string
  progress: string
  progress_pct: number
  created_at: string
  completed_at: string
  error: string
}

// Queue
export interface QueueStatus {
  running: number
  queued: number
  workers: number
}

// Version
export interface Version {
  name: string
  path: string
  size: number
}

export interface DiffResult {
  v1: string
  v2: string
  diff: string
  has_changes: boolean
}

// Page markdown
export interface PageMarkdown {
  page_num: number
  markdown: string
}

export interface CompletedPagesResponse {
  task_id: string
  pages: PageMarkdown[]
}

// API error
export interface ApiError {
  code: string
  message: string
}

export interface ApiResponse<T> {
  success: boolean
  data?: T
  error?: ApiError
}
