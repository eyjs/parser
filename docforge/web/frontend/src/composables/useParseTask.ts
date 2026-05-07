import { ref, onUnmounted, getCurrentInstance, type Ref } from 'vue'
import { getParseStatusUrl } from '@/api/client'
import type { TaskStatus, SSEEventType } from '@/api/types'

export interface ParseTaskState {
  taskId: string
  filename: Ref<string>
  status: Ref<TaskStatus>
  pct: Ref<number>
  currentStage: Ref<string>
  totalPages: Ref<number>
  completedPages: Ref<number>
  pageMarkdowns: Ref<Map<number, string>>
  error: Ref<string | null>
  isConnected: Ref<boolean>
}

export interface UseParseTaskOptions {
  getStatusUrl?: (taskId: string) => string
  onPageResult?: (page: number, markdown: string) => void
  onStageChange?: (stage: string) => void
  onDone?: () => void
  onError?: (message: string) => void
}

const MAX_RETRIES = 5
const RETRY_DELAY_MS = 3000

export function useParseTask(
  taskId: string,
  options: UseParseTaskOptions = {},
): ParseTaskState & { connect: () => void; disconnect: () => void } {
  const filename = ref('')
  const status = ref<TaskStatus>('queued')
  const pct = ref(0)
  const currentStage = ref('')
  const totalPages = ref(0)
  const completedPages = ref(0)
  const pageMarkdowns = ref<Map<number, string>>(new Map())
  const error = ref<string | null>(null)
  const isConnected = ref(false)

  let eventSource: EventSource | null = null
  let retryCount = 0
  let retryTimer: ReturnType<typeof setTimeout> | null = null

  function handleEvent(eventType: SSEEventType, data: Record<string, unknown>) {
    switch (eventType) {
      case 'catchup': {
        // Restore full state from catchup snapshot
        if (data.filename) filename.value = data.filename as string
        if (data.status) status.value = data.status as TaskStatus
        if (data.pct != null) pct.value = data.pct as number
        if (data.current_stage) currentStage.value = data.current_stage as string
        if (data.total_pages != null) totalPages.value = data.total_pages as number
        if (data.completed_pages != null) completedPages.value = data.completed_pages as number
        if (data.page_markdowns && typeof data.page_markdowns === 'object') {
          const pages = data.page_markdowns as Record<string, string>
          const next = new Map(pageMarkdowns.value)
          for (const [key, value] of Object.entries(pages)) {
            next.set(Number(key), value)
          }
          pageMarkdowns.value = next
        }
        if (data.error_message) error.value = data.error_message as string
        break
      }

      case 'profiling':
      case 'strategy_report': {
        status.value = 'running'
        currentStage.value = eventType
        if (data.pct != null) pct.value = data.pct as number
        options.onStageChange?.(eventType)
        break
      }

      case 'noise_learning': {
        currentStage.value = 'noise_learning'
        if (data.pct != null) pct.value = data.pct as number
        options.onStageChange?.('noise_learning')
        break
      }

      case 'page_progress':
      case 'progress': {
        if (data.total_pages != null) totalPages.value = data.total_pages as number
        if (data.completed_pages != null) completedPages.value = data.completed_pages as number
        if (data.pct != null) pct.value = data.pct as number
        currentStage.value = 'pages'
        break
      }

      case 'page_result': {
        const pageNum = Number(data.page_num)
        const markdown = String(data.markdown ?? '')
        if (!isNaN(pageNum) && markdown) {
          const next = new Map(pageMarkdowns.value)
          next.set(pageNum, markdown)
          pageMarkdowns.value = next
          completedPages.value = next.size
          options.onPageResult?.(pageNum, markdown)
        }
        if (data.pct != null) pct.value = Number(data.pct)
        break
      }

      case 'table_merging': {
        currentStage.value = 'table_merging'
        if (data.pct != null) pct.value = data.pct as number
        options.onStageChange?.('table_merging')
        break
      }

      case 'assembling': {
        currentStage.value = 'assembling'
        if (data.pct != null) pct.value = data.pct as number
        options.onStageChange?.('assembling')
        break
      }

      case 'done': {
        status.value = 'done'
        pct.value = 100
        currentStage.value = 'done'
        if (data.markdown) {
          // Final full markdown might be provided
        }
        options.onDone?.()
        disconnect()
        break
      }

      case 'error': {
        status.value = 'error'
        const message = (data.message as string) || (data.error as string) || 'Unknown error'
        error.value = message
        options.onError?.(message)
        disconnect()
        break
      }

      case 'heartbeat': {
        // Keep-alive, no state change
        break
      }
    }
  }

  function connect() {
    if (eventSource) {
      eventSource.close()
    }

    const url = (options.getStatusUrl ?? getParseStatusUrl)(taskId)
    eventSource = new EventSource(url)
    isConnected.value = true
    retryCount = 0

    const eventTypes: SSEEventType[] = [
      'catchup', 'profiling', 'strategy_report', 'noise_learning',
      'page_progress', 'page_result', 'table_merging', 'assembling',
      'done', 'error', 'heartbeat', 'progress',
    ]

    for (const type of eventTypes) {
      eventSource.addEventListener(type, (event) => {
        try {
          const data = JSON.parse((event as MessageEvent).data) as Record<string, unknown>
          handleEvent(type, data)
        } catch {
          // Ignore malformed events
        }
      })
    }

    // Fallback for unnamed SSE events (default type 'message')
    eventSource.addEventListener('message', (event) => {
      try {
        const raw = JSON.parse((event as MessageEvent).data) as Record<string, unknown>
        const eventType = (raw.event as SSEEventType) || 'progress'
        const payload = (raw.data as Record<string, unknown>) ?? raw
        handleEvent(eventType, payload)
      } catch {
        // Ignore malformed events
      }
    })

    eventSource.onerror = () => {
      isConnected.value = false
      eventSource?.close()
      eventSource = null

      // Don't retry if task is in terminal state
      if (status.value === 'done' || status.value === 'error') {
        return
      }

      if (retryCount < MAX_RETRIES) {
        retryCount++
        retryTimer = setTimeout(() => {
          connect()
        }, RETRY_DELAY_MS)
      } else {
        error.value = 'SSE 연결 실패: 최대 재시도 횟수 초과'
        options.onError?.(error.value)
      }
    }
  }

  function disconnect() {
    if (retryTimer) {
      clearTimeout(retryTimer)
      retryTimer = null
    }
    if (eventSource) {
      eventSource.close()
      eventSource = null
    }
    isConnected.value = false
  }

  if (getCurrentInstance()) {
    onUnmounted(() => {
      disconnect()
    })
  }

  return {
    taskId,
    filename,
    status,
    pct,
    currentStage,
    totalPages,
    completedPages,
    pageMarkdowns,
    error,
    isConnected,
    connect,
    disconnect,
  }
}
