import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useHistoryStore } from '@/stores/history'

vi.mock('@/api/client', () => ({
  getHistory: vi.fn(),
  deleteHistory: vi.fn(),
}))

import { getHistory, deleteHistory } from '@/api/client'

const mockGetHistory = vi.mocked(getHistory)
const mockDeleteHistory = vi.mocked(deleteHistory)

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
})

describe('useHistoryStore', () => {
  const sampleItems = [
    {
      task_id: 't1',
      filename: 'a.pdf',
      status: 'done',
      progress: '100%',
      progress_pct: 100,
      created_at: '2026-01-01',
      completed_at: '2026-01-01',
      error: '',
    },
    {
      task_id: 't2',
      filename: 'b.pdf',
      status: 'running',
      progress: '50%',
      progress_pct: 50,
      created_at: '2026-01-02',
      completed_at: '',
      error: '',
    },
    {
      task_id: 't3',
      filename: 'c.pdf',
      status: 'error',
      progress: '0%',
      progress_pct: 0,
      created_at: '2026-01-03',
      completed_at: '',
      error: 'Failed',
    },
  ]

  describe('fetchHistory', () => {
    it('fetches and stores history items', async () => {
      mockGetHistory.mockResolvedValue(sampleItems)

      const store = useHistoryStore()
      await store.fetchHistory()

      expect(store.items).toEqual(sampleItems)
      expect(store.isLoading).toBe(false)
      expect(store.error).toBeNull()
    })

    it('handles fetch error', async () => {
      mockGetHistory.mockRejectedValue(new Error('Network error'))

      const store = useHistoryStore()
      await store.fetchHistory()

      expect(store.items).toEqual([])
      expect(store.error).toBe('Network error')
    })
  })

  describe('deleteItem', () => {
    it('removes item from store on success', async () => {
      mockGetHistory.mockResolvedValue(sampleItems)
      mockDeleteHistory.mockResolvedValue(undefined)

      const store = useHistoryStore()
      await store.fetchHistory()
      await store.deleteItem('t1')

      expect(store.items).toHaveLength(2)
      expect(store.items.find((i) => i.task_id === 't1')).toBeUndefined()
    })

    it('throws on delete failure', async () => {
      mockGetHistory.mockResolvedValue(sampleItems)
      mockDeleteHistory.mockRejectedValue(new Error('Delete failed'))

      const store = useHistoryStore()
      await store.fetchHistory()

      await expect(store.deleteItem('t1')).rejects.toThrow('Delete failed')
      // Items should remain unchanged
      expect(store.items).toHaveLength(3)
    })
  })

  describe('addItem', () => {
    it('prepends item to list', async () => {
      mockGetHistory.mockResolvedValue(sampleItems)

      const store = useHistoryStore()
      await store.fetchHistory()

      const newItem = {
        task_id: 't4',
        filename: 'd.pdf',
        status: 'queued',
        progress: '0%',
        progress_pct: 0,
        created_at: '2026-01-04',
        completed_at: '',
        error: '',
      }
      store.addItem(newItem)

      expect(store.items[0].task_id).toBe('t4')
      expect(store.items).toHaveLength(4)
    })
  })

  describe('updateItemStatus', () => {
    it('updates status and pct of a specific item', async () => {
      mockGetHistory.mockResolvedValue(sampleItems)

      const store = useHistoryStore()
      await store.fetchHistory()
      store.updateItemStatus('t2', 'done', 100)

      const item = store.items.find((i) => i.task_id === 't2')
      expect(item!.status).toBe('done')
      expect(item!.progress_pct).toBe(100)
    })
  })

  describe('getters', () => {
    it('doneItems returns only done items', async () => {
      mockGetHistory.mockResolvedValue(sampleItems)

      const store = useHistoryStore()
      await store.fetchHistory()

      expect(store.doneItems).toHaveLength(1)
      expect(store.doneItems[0].task_id).toBe('t1')
    })

    it('pendingItems returns non-done, non-error items', async () => {
      mockGetHistory.mockResolvedValue(sampleItems)

      const store = useHistoryStore()
      await store.fetchHistory()

      expect(store.pendingItems).toHaveLength(1)
      expect(store.pendingItems[0].task_id).toBe('t2')
    })

    it('isEmpty returns true when no items', () => {
      const store = useHistoryStore()
      expect(store.isEmpty).toBe(true)
    })

    it('isEmpty returns false when items exist', async () => {
      mockGetHistory.mockResolvedValue(sampleItems)

      const store = useHistoryStore()
      await store.fetchHistory()

      expect(store.isEmpty).toBe(false)
    })
  })
})
