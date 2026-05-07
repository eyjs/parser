import type {
  HistoryItem,
  ParseResult,
  QueueStatus,
  Version,
  DiffResult,
  PageMarkdown,
} from './types'
import type {
  HistoryEntry,
  ParseResultData,
  QueueInfo,
  VersionInfo,
  VersionDiff,
  PageContent,
} from '@/domain/types'

export function toHistoryEntry(dto: HistoryItem): HistoryEntry {
  return {
    taskId: dto.task_id,
    filename: dto.filename,
    status: dto.status,
    progress: dto.progress,
    progressPct: dto.progress_pct,
    createdAt: dto.created_at,
    completedAt: dto.completed_at,
    error: dto.error,
  }
}

export function toParseResultData(dto: ParseResult): ParseResultData {
  return {
    taskId: dto.task_id,
    filename: dto.filename,
    markdown: dto.markdown,
    metadata: dto.metadata,
    stats: dto.stats,
    completedAt: dto.completed_at,
    pdfPath: dto.pdf_path,
  }
}

export function toQueueInfo(dto: QueueStatus): QueueInfo {
  return {
    running: dto.running,
    queued: dto.queued,
    maxWorkers: dto.max_workers,
  }
}

export function toVersionInfo(dto: Version): VersionInfo {
  return { name: dto.name, path: dto.path, size: dto.size }
}

export function toVersionDiff(dto: DiffResult): VersionDiff {
  return {
    v1: dto.v1,
    v2: dto.v2,
    diff: dto.diff,
    hasChanges: dto.has_changes,
  }
}

export function toPageContent(dto: PageMarkdown): PageContent {
  return {
    pageNum: dto.page_num,
    markdown: dto.markdown,
  }
}
