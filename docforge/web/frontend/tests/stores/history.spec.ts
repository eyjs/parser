import { describe, it, expect, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useHistoryStore } from '@/stores/history'
import type { HistoryEntry } from '@/domain/types'

beforeEach(() => {
  setActivePinia(createPinia())
})

describe('useHistoryStore', () => {
  const sampleItems: HistoryEntry[] = [
    {
      taskId: 't1',
      filename: 'a.pdf',
      status: 'done',
      progress: '100%',
      progressPct: 100,
      createdAt: '2026-01-01',
      completedAt: '2026-01-01',
      error: '',
      totalPages: 5,
    },
    {
      taskId: 't2',
      filename: 'b.pdf',
      status: 'running',
      progress: '50%',
      progressPct: 50,
      createdAt: '2026-01-02',
      completedAt: '',
      error: '',
      totalPages: 10,
    },
    {
      taskId: 't3',
      filename: 'c.pdf',
      status: 'error',
      progress: '0%',
      progressPct: 0,
      createdAt: '2026-01-03',
      completedAt: '',
      error: 'Failed',
      totalPages: 0,
    },
  ]

  describe('setItems / setLoading / setError', () => {
    it('stores items via setItems', () => {
      const store = useHistoryStore()
      store.setItems(sampleItems)

      expect(store.items).toEqual(sampleItems)
    })

    it('tracks loading state', () => {
      const store = useHistoryStore()
      expect(store.isLoading).toBe(false)

      store.setLoading(true)
      expect(store.isLoading).toBe(true)

      store.setLoading(false)
      expect(store.isLoading).toBe(false)
    })

    it('tracks error state', () => {
      const store = useHistoryStore()
      expect(store.error).toBeNull()

      store.setError('Network error')
      expect(store.error).toBe('Network error')

      store.setError(null)
      expect(store.error).toBeNull()
    })
  })

  describe('removeItem', () => {
    it('removes item by taskId', () => {
      const store = useHistoryStore()
      store.setItems(sampleItems)
      store.removeItem('t1')

      expect(store.items).toHaveLength(2)
      expect(store.items.find((i) => i.taskId === 't1')).toBeUndefined()
    })

    it('does nothing for non-existent taskId', () => {
      const store = useHistoryStore()
      store.setItems(sampleItems)
      store.removeItem('non-existent')

      expect(store.items).toHaveLength(3)
    })
  })

  describe('addItem', () => {
    it('prepends item to list', () => {
      const store = useHistoryStore()
      store.setItems(sampleItems)

      const newItem: HistoryEntry = {
        taskId: 't4',
        filename: 'd.pdf',
        status: 'queued',
        progress: '0%',
        progressPct: 0,
        createdAt: '2026-01-04',
        completedAt: '',
        error: '',
        totalPages: 0,
      }
      store.addItem(newItem)

      expect(store.items[0].taskId).toBe('t4')
      expect(store.items).toHaveLength(4)
    })
  })

  describe('updateItemStatus', () => {
    it('updates status and pct of a specific item', () => {
      const store = useHistoryStore()
      store.setItems(sampleItems)
      store.updateItemStatus('t2', 'done', 100)

      const item = store.items.find((i) => i.taskId === 't2')
      expect(item!.status).toBe('done')
      expect(item!.progressPct).toBe(100)
    })

    it('keeps existing pct when not provided', () => {
      const store = useHistoryStore()
      store.setItems(sampleItems)
      store.updateItemStatus('t2', 'done')

      const item = store.items.find((i) => i.taskId === 't2')
      expect(item!.progressPct).toBe(50)
    })
  })

  describe('getters', () => {
    it('doneItems returns only done items', () => {
      const store = useHistoryStore()
      store.setItems(sampleItems)

      expect(store.doneItems).toHaveLength(1)
      expect(store.doneItems[0].taskId).toBe('t1')
    })

    it('pendingItems returns non-done, non-error items', () => {
      const store = useHistoryStore()
      store.setItems(sampleItems)

      expect(store.pendingItems).toHaveLength(1)
      expect(store.pendingItems[0].taskId).toBe('t2')
    })

    it('isEmpty returns true when no items', () => {
      const store = useHistoryStore()
      expect(store.isEmpty).toBe(true)
    })

    it('isEmpty returns false when items exist', () => {
      const store = useHistoryStore()
      store.setItems(sampleItems)

      expect(store.isEmpty).toBe(false)
    })
  })
})
