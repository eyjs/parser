import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount } from '@vue/test-utils'
import MdDiffPanel from '@/components/compare/MdDiffPanel.vue'

vi.mock('@/utils/diff', () => ({
  computeLineDiff: vi.fn((oldText: string, newText: string) => {
    if (oldText === newText) return [{ type: 'unchanged', content: oldText }]
    const result = []
    if (oldText) result.push({ type: 'removed', content: oldText })
    if (newText) result.push({ type: 'added', content: newText })
    return result
  }),
  renderDiffHtml: vi.fn(
    (_old: string, _new: string, _format: string) => '<div class="d2h-wrapper">diff output</div>',
  ),
}))

import { computeLineDiff, renderDiffHtml } from '@/utils/diff'
const mockComputeLineDiff = vi.mocked(computeLineDiff)
const mockRenderDiffHtml = vi.mocked(renderDiffHtml)

beforeEach(() => {
  vi.useFakeTimers()
})

afterEach(() => {
  vi.useRealTimers()
  vi.clearAllMocks()
})

describe('MdDiffPanel', () => {
  it('renders both textareas', () => {
    const wrapper = mount(MdDiffPanel, {
      props: { baseMarkdown: '# Hello' },
    })

    const textareas = wrapper.findAll('textarea')
    expect(textareas).toHaveLength(2)
    expect(textareas[0].attributes('readonly')).toBeDefined()
    expect(textareas[1].attributes('readonly')).toBeUndefined()
  })

  it('displays base markdown in read-only textarea', () => {
    const wrapper = mount(MdDiffPanel, {
      props: { baseMarkdown: '# Title' },
    })

    const baseTextarea = wrapper.find('.md-textarea--readonly')
    expect((baseTextarea.element as HTMLTextAreaElement).value).toBe('# Title')
  })

  it('computes diff on compare input with debounce', async () => {
    const wrapper = mount(MdDiffPanel, {
      props: { baseMarkdown: '# Old' },
    })

    const editableTextarea = wrapper.findAll('textarea')[1]
    await editableTextarea.setValue('# New')
    await editableTextarea.trigger('input')

    expect(mockComputeLineDiff).not.toHaveBeenCalled()

    await vi.advanceTimersByTimeAsync(300)

    expect(mockComputeLineDiff).toHaveBeenCalledWith('# Old', '# New')
    expect(mockRenderDiffHtml).toHaveBeenCalled()
  })

  it('recomputes diff when baseMarkdown prop changes', async () => {
    const wrapper = mount(MdDiffPanel, {
      props: { baseMarkdown: 'v1' } as any,
    })

    await wrapper.setProps({ baseMarkdown: 'v2' } as any)

    expect(mockComputeLineDiff).toHaveBeenCalledWith('v2', '')
  })

  it('shows diff stats when there are changes', async () => {
    mockComputeLineDiff.mockReturnValueOnce([
      { type: 'removed', content: 'old line' },
      { type: 'added', content: 'new line' },
      { type: 'added', content: 'extra line' },
    ])

    const wrapper = mount(MdDiffPanel, {
      props: { baseMarkdown: 'old' },
    })

    const editableTextarea = wrapper.findAll('textarea')[1]
    await editableTextarea.setValue('new')
    await editableTextarea.trigger('input')
    await vi.advanceTimersByTimeAsync(300)

    expect(wrapper.text()).toContain('+2')
    expect(wrapper.text()).toContain('-1')
  })

  it('renders diff HTML output', async () => {
    const wrapper = mount(MdDiffPanel, {
      props: { baseMarkdown: '# A' },
    })

    const editableTextarea = wrapper.findAll('textarea')[1]
    await editableTextarea.setValue('# B')
    await editableTextarea.trigger('input')
    await vi.advanceTimersByTimeAsync(300)

    expect(wrapper.find('.diff-view').exists()).toBe(true)
    expect(wrapper.find('.diff-view').html()).toContain('diff output')
  })

  it('shows "no changes" message when texts are identical', async () => {
    mockComputeLineDiff.mockReturnValueOnce([
      { type: 'unchanged', content: 'same' },
    ])
    mockRenderDiffHtml.mockReturnValueOnce('')

    const wrapper = mount(MdDiffPanel, {
      props: { baseMarkdown: 'same' },
    })

    const editableTextarea = wrapper.findAll('textarea')[1]
    await editableTextarea.setValue('same')
    await editableTextarea.trigger('input')
    await vi.advanceTimersByTimeAsync(300)

    expect(wrapper.text()).toContain('변경 사항 없음')
  })

  it('exposes setCompareText method', async () => {
    const wrapper = mount(MdDiffPanel, {
      props: { baseMarkdown: '' },
    })

    ;(wrapper.vm as any).setCompareText('injected text')
    await vi.advanceTimersByTimeAsync(0)

    expect(mockComputeLineDiff).toHaveBeenCalledWith('', 'injected text')
  })

  it('supports output format toggle', async () => {
    const wrapper = mount(MdDiffPanel, {
      props: { baseMarkdown: 'a' },
    })

    const editableTextarea = wrapper.findAll('textarea')[1]
    await editableTextarea.setValue('b')
    await editableTextarea.trigger('input')
    await vi.advanceTimersByTimeAsync(300)

    const select = wrapper.find('select')
    await select.setValue('line-by-line')

    expect(mockRenderDiffHtml).toHaveBeenLastCalledWith('a', 'b', 'line-by-line')
  })
})
