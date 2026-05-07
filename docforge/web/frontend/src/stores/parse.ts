import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type { TaskStatus } from '@/api/types'

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

export const useParseStore = defineStore('parse', () => {
  const activeTasks = ref<Map<string, ActiveTask>>(new Map())

  // Getters
  const hasActiveTasks = computed(() => activeTasks.value.size > 0)

  const activeTaskList = computed(() =>
    Array.from(activeTasks.value.values()).sort(
      (a, b) => b.startedAt - a.startedAt,
    ),
  )

  function getTask(taskId: string): ActiveTask | undefined {
    return activeTasks.value.get(taskId)
  }

  // Actions
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
    // Preserve pageMarkdowns reference since Partial<Omit> excludes it
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
    const next = new Map(activeTasks.value)
    next.delete(taskId)
    activeTasks.value = next
  }

  function clearAll() {
    activeTasks.value = new Map()
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
  }
})
