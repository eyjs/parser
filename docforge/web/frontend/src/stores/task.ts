import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type { TaskStatus } from '@/api/types'
import { useParseTask } from '@/composables/useParseTask'
import { useHistoryStore } from '@/stores/history'

export interface ActiveTask {
  taskId: string
  filename: string
  status: TaskStatus
  pct: number
  currentStage: string
  totalPages: number
  completedPages: number
  pageMarkdowns: Map<number, string>
  error: string | null
  startedAt: number
}

const sseConnections = new Map<string, { disconnect: () => void }>()

export const useTaskStore = defineStore('task', () => {
  const activeTasks = ref<Map<string, ActiveTask>>(new Map())

  const hasActiveTasks = computed(() => activeTasks.value.size > 0)

  const activeTaskList = computed(() =>
    Array.from(activeTasks.value.values()).sort(
      (a, b) => b.startedAt - a.startedAt,
    ),
  )

  function getTask(taskId: string): ActiveTask | undefined {
    return activeTasks.value.get(taskId)
  }

  function addTask(taskId: string, filename: string) {
    const task: ActiveTask = {
      taskId,
      filename,
      status: 'queued',
      pct: 0,
      currentStage: '',
      totalPages: 0,
      completedPages: 0,
      pageMarkdowns: new Map(),
      error: null,
      startedAt: Date.now(),
    }

    const next = new Map(activeTasks.value)
    next.set(taskId, task)
    activeTasks.value = next
  }

  function updateTask(taskId: string, updates: Partial<Omit<ActiveTask, 'taskId' | 'pageMarkdowns'>>) {
    const existing = activeTasks.value.get(taskId)
    if (!existing) return

    const updated: ActiveTask = { ...existing, ...updates }
    updated.pageMarkdowns = existing.pageMarkdowns

    const next = new Map(activeTasks.value)
    next.set(taskId, updated)
    activeTasks.value = next
  }

  function setPageMarkdown(taskId: string, pageNum: number, markdown: string) {
    const existing = activeTasks.value.get(taskId)
    if (!existing) return

    const newPageMarkdowns = new Map(existing.pageMarkdowns)
    newPageMarkdowns.set(pageNum, markdown)

    const updated: ActiveTask = {
      ...existing,
      pageMarkdowns: newPageMarkdowns,
      completedPages: newPageMarkdowns.size,
    }

    const next = new Map(activeTasks.value)
    next.set(taskId, updated)
    activeTasks.value = next
  }

  function removeTask(taskId: string) {
    sseConnections.get(taskId)?.disconnect()
    sseConnections.delete(taskId)
    const next = new Map(activeTasks.value)
    next.delete(taskId)
    activeTasks.value = next
  }

  function clearAll() {
    for (const conn of sseConnections.values()) {
      conn.disconnect()
    }
    sseConnections.clear()
    activeTasks.value = new Map()
  }

  function trackTask(taskId: string, filename: string) {
    if (sseConnections.has(taskId)) return

    addTask(taskId, filename)

    const historyStore = useHistoryStore()

    const task = useParseTask(taskId, {
      onProgress(info) {
        updateTask(taskId, {
          status: 'running',
          pct: info.pct,
          totalPages: info.totalPages,
          completedPages: info.completedPages,
        })
      },
      onStageChange(stage) {
        updateTask(taskId, { currentStage: stage, status: 'running' })
      },
      onPageResult(page, markdown) {
        setPageMarkdown(taskId, page, markdown)
      },
      onDone() {
        updateTask(taskId, { status: 'done', pct: 100 })
        sseConnections.delete(taskId)
        historyStore.fetchHistory()
        setTimeout(() => removeTask(taskId), 30_000)
      },
      onError(message) {
        updateTask(taskId, { status: 'error', error: message })
        sseConnections.delete(taskId)
        historyStore.fetchHistory()
      },
    })

    sseConnections.set(taskId, task)
    task.connect()
  }

  return {
    activeTasks,
    hasActiveTasks,
    activeTaskList,
    getTask,
    addTask,
    updateTask,
    setPageMarkdown,
    removeTask,
    clearAll,
    trackTask,
  }
})
