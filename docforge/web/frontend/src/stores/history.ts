import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { getHistory, deleteHistory } from '@/api/client'
import type { HistoryItem } from '@/api/types'

export const useHistoryStore = defineStore('history', () => {
  const items = ref<HistoryItem[]>([])
  const isLoading = ref(false)
  const error = ref<string | null>(null)

  // Getters
  const doneItems = computed(() =>
    items.value.filter((item) => item.status === 'done'),
  )

  const pendingItems = computed(() =>
    items.value.filter((item) => item.status !== 'done' && item.status !== 'error'),
  )

  const isEmpty = computed(() => items.value.length === 0)

  // Actions
  async function fetchHistory() {
    if (isLoading.value) return
    isLoading.value = true
    error.value = null

    try {
      const data = await getHistory()
      items.value = data
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to fetch history'
    } finally {
      isLoading.value = false
    }
  }

  async function deleteItem(taskId: string) {
    try {
      await deleteHistory(taskId)
      items.value = items.value.filter((item) => item.task_id !== taskId)
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to delete item'
      throw e
    }
  }

  function addItem(item: HistoryItem) {
    // Prepend to maintain newest-first order
    items.value = [item, ...items.value]
  }

  function updateItemStatus(taskId: string, status: string, pct?: number) {
    items.value = items.value.map((item) => {
      if (item.task_id !== taskId) return item
      return {
        ...item,
        status,
        progress_pct: pct ?? item.progress_pct,
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
    fetchHistory,
    deleteItem,
    addItem,
    updateItemStatus,
  }
})
