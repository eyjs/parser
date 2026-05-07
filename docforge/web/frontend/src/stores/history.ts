import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type { HistoryEntry } from '@/domain/types'

export const useHistoryStore = defineStore('history', () => {
  const items = ref<HistoryEntry[]>([])
  const isLoading = ref(false)
  const error = ref<string | null>(null)

  const doneItems = computed(() =>
    items.value.filter((item) => item.status === 'done'),
  )

  const pendingItems = computed(() =>
    items.value.filter((item) => item.status !== 'done' && item.status !== 'error'),
  )

  const isEmpty = computed(() => items.value.length === 0)

  function setItems(entries: HistoryEntry[]) {
    items.value = entries
  }

  function setLoading(loading: boolean) {
    isLoading.value = loading
  }

  function setError(err: string | null) {
    error.value = err
  }

  function removeItem(taskId: string) {
    items.value = items.value.filter((item) => item.taskId !== taskId)
  }

  function addItem(item: HistoryEntry) {
    items.value = [item, ...items.value]
  }

  function updateItemStatus(taskId: string, status: string, pct?: number) {
    items.value = items.value.map((item) => {
      if (item.taskId !== taskId) return item
      return {
        ...item,
        status,
        progressPct: pct ?? item.progressPct,
      }
    })
  }

  return {
    items,
    isLoading,
    error,
    doneItems,
    pendingItems,
    isEmpty,
    setItems,
    setLoading,
    setError,
    removeItem,
    addItem,
    updateItemStatus,
  }
})
