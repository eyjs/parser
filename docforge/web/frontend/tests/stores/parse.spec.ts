import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useParseStore } from '@/stores/parse'

beforeEach(() => {
  vi.useFakeTimers()
  setActivePinia(createPinia())
})

afterEach(() => {
  vi.useRealTimers()
})

describe('useParseStore', () => {
  describe('addTask', () => {
    it('adds a new task to activeTasks', () => {
      const store = useParseStore()

      store.addTask('task-1', 'test.pdf')

      expect(store.activeTasks.size).toBe(1)
      const task = store.getTask('task-1')
      expect(task).toBeDefined()
      expect(task!.filename).toBe('test.pdf')
      expect(task!.status).toBe('queued')
      expect(task!.pct).toBe(0)
    })

    it('sets startedAt timestamp', () => {
      const store = useParseStore()
      const before = Date.now()
      store.addTask('task-1', 'test.pdf')
      const after = Date.now()

      const task = store.getTask('task-1')
      expect(task!.startedAt).toBeGreaterThanOrEqual(before)
      expect(task!.startedAt).toBeLessThanOrEqual(after)
    })
  })

  describe('updateTask', () => {
    it('updates task fields immutably', () => {
      const store = useParseStore()
      store.addTask('task-1', 'test.pdf')

      store.updateTask('task-1', { status: 'running', pct: 50 })

      const task = store.getTask('task-1')
      expect(task!.status).toBe('running')
      expect(task!.pct).toBe(50)
      expect(task!.filename).toBe('test.pdf')
    })

    it('does nothing for non-existent task', () => {
      const store = useParseStore()
      store.updateTask('nonexistent', { pct: 100 })
      expect(store.activeTasks.size).toBe(0)
    })
  })

  describe('setPageMarkdown', () => {
    it('sets page markdown and updates completedPages', () => {
      const store = useParseStore()
      store.addTask('task-1', 'test.pdf')

      store.setPageMarkdown('task-1', 1, '# Page 1')
      store.setPageMarkdown('task-1', 2, '# Page 2')

      const task = store.getTask('task-1')
      expect(task!.pageMarkdowns.get(1)).toBe('# Page 1')
      expect(task!.pageMarkdowns.get(2)).toBe('# Page 2')
      expect(task!.completedPages).toBe(2)
    })

    it('overwrites existing page markdown', () => {
      const store = useParseStore()
      store.addTask('task-1', 'test.pdf')

      store.setPageMarkdown('task-1', 1, 'old')
      store.setPageMarkdown('task-1', 1, 'new')

      const task = store.getTask('task-1')
      expect(task!.pageMarkdowns.get(1)).toBe('new')
      expect(task!.completedPages).toBe(1)
    })
  })

  describe('removeTask', () => {
    it('removes task from activeTasks', () => {
      const store = useParseStore()
      store.addTask('task-1', 'test.pdf')
      store.addTask('task-2', 'test2.pdf')

      store.removeTask('task-1')

      expect(store.activeTasks.size).toBe(1)
      expect(store.getTask('task-1')).toBeUndefined()
      expect(store.getTask('task-2')).toBeDefined()
    })
  })

  describe('clearAll', () => {
    it('clears all tasks', () => {
      const store = useParseStore()
      store.addTask('task-1', 'a.pdf')
      store.addTask('task-2', 'b.pdf')

      store.clearAll()

      expect(store.activeTasks.size).toBe(0)
    })
  })

  describe('getters', () => {
    it('hasActiveTasks returns true when tasks exist', () => {
      const store = useParseStore()
      expect(store.hasActiveTasks).toBe(false)

      store.addTask('task-1', 'test.pdf')
      expect(store.hasActiveTasks).toBe(true)
    })

    it('activeTaskList returns tasks sorted by startedAt descending', () => {
      const store = useParseStore()
      vi.setSystemTime(new Date('2026-01-01T00:00:00Z'))
      store.addTask('task-1', 'first.pdf')
      vi.setSystemTime(new Date('2026-01-01T00:00:01Z'))
      store.addTask('task-2', 'second.pdf')

      const list = store.activeTaskList
      // Second task should be first (newest)
      expect(list[0].taskId).toBe('task-2')
      expect(list[1].taskId).toBe('task-1')
    })
  })
})
