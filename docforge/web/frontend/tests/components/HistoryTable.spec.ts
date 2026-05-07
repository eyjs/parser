import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { setActivePinia, createPinia } from 'pinia'
import HistoryTable from '@/components/dashboard/HistoryTable.vue'
import { useHistoryStore } from '@/stores/history'

vi.mock('@/api/client', () => ({
  getHistory: vi.fn().mockResolvedValue([]),
  deleteHistory: vi.fn().mockResolvedValue(undefined),
  getExportUrl: vi.fn((taskId: string) => `/api/export/${taskId}`),
}))

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

    store.$patch({
      items: [
        {
          task_id: 'abc-123',
          filename: 'report.pdf',
          status: 'done',
          progress: '100%',
          progress_pct: 100,
          created_at: '2026-01-01T00:00:00Z',
          completed_at: '2026-01-01T00:05:00Z',
          error: '',
        },
      ],
    })

    const wrapper = mount(HistoryTable, { global: { plugins: [pinia] } })

    expect(wrapper.text()).toContain('report.pdf')
    expect(wrapper.text()).toContain('완료')
  })

  it('shows correct badge variant for each status', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const store = useHistoryStore()

    store.$patch({
      items: [
        {
          task_id: 't1',
          filename: 'a.pdf',
          status: 'done',
          progress: '',
          progress_pct: 100,
          created_at: '2026-01-01T00:00:00Z',
          completed_at: '2026-01-01T00:05:00Z',
          error: '',
        },
        {
          task_id: 't2',
          filename: 'b.pdf',
          status: 'error',
          progress: '',
          progress_pct: 0,
          created_at: '2026-01-01T00:00:00Z',
          completed_at: '',
          error: 'fail',
        },
      ],
    })

    const wrapper = mount(HistoryTable, { global: { plugins: [pinia] } })

    const rows = wrapper.findAll('tbody tr')
    expect(rows[0].text()).toContain('완료')
    expect(rows[1].text()).toContain('오류')
  })

  it('shows verify and download buttons only for done items', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const store = useHistoryStore()

    store.$patch({
      items: [
        {
          task_id: 't1',
          filename: 'done.pdf',
          status: 'done',
          progress: '',
          progress_pct: 100,
          created_at: '2026-01-01T00:00:00Z',
          completed_at: '2026-01-01T00:05:00Z',
          error: '',
        },
        {
          task_id: 't2',
          filename: 'running.pdf',
          status: 'running',
          progress: '',
          progress_pct: 50,
          created_at: '2026-01-01T00:00:00Z',
          completed_at: '',
          error: '',
        },
      ],
    })

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

    store.$patch({
      items: [
        {
          task_id: 't1',
          filename: 'a.pdf',
          status: 'done',
          progress: '',
          progress_pct: 100,
          created_at: '2026-01-01T00:00:00Z',
          completed_at: '2026-01-01T00:05:00Z',
          error: '',
        },
      ],
    })

    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const deleteSpy = vi.spyOn(store, 'deleteItem')

    const wrapper = mount(HistoryTable, { global: { plugins: [pinia] } })

    const deleteBtn = wrapper.findAll('button').find((b) => b.text() === '삭제')
    await deleteBtn!.trigger('click')

    expect(deleteSpy).toHaveBeenCalledWith('t1')
  })
})
