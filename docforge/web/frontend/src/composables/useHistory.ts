import { storeToRefs } from 'pinia'
import { deleteHistory, getExportUrl } from '@/api/client'
import { useHistoryStore } from '@/stores/history'

export function useHistory() {
  const store = useHistoryStore()
  const {
    items,
    isLoading,
    error,
    doneItems,
    pendingItems,
    isEmpty,
  } = storeToRefs(store)

  async function fetchHistory() {
    return store.fetchHistory()
  }

  async function deleteItem(taskId: string) {
    try {
      await deleteHistory(taskId)
      store.removeItem(taskId)
    } catch (e) {
      store.setError(e instanceof Error ? e.message : 'Failed to delete item')
      throw e
    }
  }

  function exportUrl(taskId: string): string {
    return getExportUrl(taskId)
  }

  return {
    items,
    isLoading,
    error,
    doneItems,
    pendingItems,
    isEmpty,
    addItem: store.addItem,
    updateItemStatus: store.updateItemStatus,
    fetchHistory,
    deleteItem,
    exportUrl,
  }
}
