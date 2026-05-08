export type { TaskStatus } from '@/api/types'

export interface HistoryEntry {
  taskId: string
  filename: string
  status: string
  progress: string
  progressPct: number
  createdAt: string
  completedAt: string
  error: string
  totalPages: number
}

export interface ParseResultData {
  taskId: string
  filename: string
  markdown: string
  metadata: Record<string, unknown>
  stats: Record<string, unknown>
  completedAt: string
  pdfPath: string
}

export interface QueueInfo {
  running: number
  queued: number
  maxWorkers: number
}

export interface VersionInfo {
  name: string
  path: string
  size: number
}

export interface VersionDiff {
  v1: string
  v2: string
  diff: string
  hasChanges: boolean
}

export interface PageContent {
  pageNum: number
  markdown: string
}
