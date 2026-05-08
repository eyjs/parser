import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { setActivePinia, createPinia } from 'pinia'
import HistoryTable from '@/components/dashboard/HistoryTable.vue'
import { useHistoryStore } from '@/stores/history'
import type { HistoryEntry } from '@/domain/types'

vi.mock('@/composables/useHistory', () => {
  return {
    useHistory: () => {
      const store = useHistoryStore()
      return {
        items: store.items,
        isLoading: store.isLoading,
        error: store.error,
        doneItems: store.doneItems,
        pendingItems: store.pendingItems,
        isEmpty: store.isEmpty,
        addItem: store.addItem,
        updateItemStatus: store.updateItemStatus,
        fetchHistory: vi.fn(),
        deleteItem: vi.fn().mockResolvedValue(undefined),
        exportUrl: (taskId: string) => `/api/export/${taskId}`,
      }
    },
  }
})

function makeItem(overrides: Partial<HistoryEntry> = {}): HistoryEntry {
  return {
    taskId: 'default-id',
    filename: 'default.pdf',
    status: 'done',
    progress: '100%',
    progressPct: 100,
    createdAt: '2026-01-01T00:00:00Z',
    completedAt: '2026-01-01T00:05:00Z',
    error: '',
    totalPages: 0,
    ...overrides,
  }
}

beforeEach(() => {
  setActivePinia(createPinia())
})

describe('HistoryTable', () => {
  it('shows empty message when no items', async () => {
    const wrapper = mount(HistoryTable)
    await vi.dynamicImportSettled()

    expect(wrapper.text()).toContain('변환 이력이 없습니다')
  })

  it('renders history items', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const store = useHistoryStore()

    store.setItems([
      makeItem({ taskId: 'abc-123', filename: 'report.pdf' }),
    ])

    const wrapper = mount(HistoryTable, { global: { plugins: [pinia] } })

    expect(wrapper.text()).toContain('report.pdf')
    expect(wrapper.text()).toContain('완료')
  })

  it('shows correct badge variant for each status', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const store = useHistoryStore()

    store.setItems([
      makeItem({ taskId: 't1', filename: 'a.pdf', status: 'done' }),
      makeItem({
        taskId: 't2',
        filename: 'b.pdf',
        status: 'error',
        progressPct: 0,
        completedAt: '',
        error: 'fail',
      }),
    ])

    const wrapper = mount(HistoryTable, { global: { plugins: [pinia] } })

    const rows = wrapper.findAll('tbody tr')
    expect(rows[0].text()).toContain('완료')
    expect(rows[1].text()).toContain('오류')
  })

  it('shows verify and download buttons only for done items', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const store = useHistoryStore()

    store.setItems([
      makeItem({ taskId: 't1', filename: 'done.pdf', status: 'done' }),
      makeItem({
        taskId: 't2',
        filename: 'running.pdf',
        status: 'running',
        progressPct: 50,
        completedAt: '',
      }),
    ])

    const wrapper = mount(HistoryTable, { global: { plugins: [pinia] } })

    const doneRow = wrapper.find('[data-task-id="t1"]')
    expect(doneRow.text()).toContain('검증')
    expect(doneRow.text()).toContain('다운로드')

    const runningRow = wrapper.find('[data-task-id="t2"]')
    expect(runningRow.text()).not.toContain('검증')
    expect(runningRow.text()).not.toContain('다운로드')
  })

  it('calls deleteItem on delete button click', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const store = useHistoryStore()

    store.setItems([
      makeItem({ taskId: 't1', filename: 'a.pdf' }),
    ])

    vi.spyOn(window, 'confirm').mockReturnValue(true)

    const wrapper = mount(HistoryTable, { global: { plugins: [pinia] } })

    const deleteBtn = wrapper.findAll('button').find((b) => b.text() === '삭제')
    await deleteBtn!.trigger('click')

    expect(window.confirm).toHaveBeenCalled()
  })
})
