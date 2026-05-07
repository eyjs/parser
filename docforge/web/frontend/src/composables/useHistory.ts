import { getHistory, deleteHistory, getExportUrl } from '@/api/client'
import { toHistoryEntry } from '@/api/mappers'
import { useHistoryStore } from '@/stores/history'

export function useHistory() {
  const store = useHistoryStore()

  async function fetchHistory() {
    if (store.isLoading) return
    store.setLoading(true)
    store.setError(null)

    try {
      const data = await getHistory()
      store.setItems(data.map(toHistoryEntry))
    } catch (e) {
      store.setError(e instanceof Error ? e.message : 'Failed to fetch history')
    } finally {
      store.setLoading(false)
    }
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
    items: store.items,
    isLoading: store.isLoading,
    error: store.error,
    doneItems: store.doneItems,
    pendingItems: store.pendingItems,
    isEmpty: store.isEmpty,
    addItem: store.addItem,
    updateItemStatus: store.updateItemStatus,
    fetchHistory,
    deleteItem,
    exportUrl,
  }
}
