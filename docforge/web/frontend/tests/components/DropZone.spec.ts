import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import DropZone from '@/components/dashboard/DropZone.vue'

function createPdfFile(name: string, sizeMB = 1): File {
  const bytes = new Uint8Array(sizeMB * 1024 * 1024)
  return new File([bytes], name, { type: 'application/pdf' })
}

function createTxtFile(name: string): File {
  return new File(['hello'], name, { type: 'text/plain' })
}

describe('DropZone', () => {
  it('renders with correct aria attributes', () => {
    const wrapper = mount(DropZone)
    const zone = wrapper.find('.drop-zone')
    expect(zone.attributes('role')).toBe('button')
    expect(zone.attributes('tabindex')).toBe('0')
  })

  it('emits files-selected with valid PDF files on drop', async () => {
    const wrapper = mount(DropZone)
    const zone = wrapper.find('.drop-zone')

    const file = createPdfFile('test.pdf')
    const dataTransfer = { files: [file] }

    await zone.trigger('drop', { dataTransfer })

    expect(wrapper.emitted('files-selected')).toHaveLength(1)
    expect(wrapper.emitted('files-selected')![0][0]).toHaveLength(1)
  })

  it('filters out non-PDF files and shows error', async () => {
    const wrapper = mount(DropZone)
    const zone = wrapper.find('.drop-zone')

    const file = createTxtFile('readme.txt')
    const dataTransfer = { files: [file] }

    await zone.trigger('drop', { dataTransfer })

    expect(wrapper.emitted('files-selected')).toBeUndefined()
    expect(wrapper.find('[role="alert"]').text()).toContain('PDF 파일만')
  })

  it('rejects files exceeding 100MB', async () => {
    const wrapper = mount(DropZone)
    const zone = wrapper.find('.drop-zone')

    const file = createPdfFile('huge.pdf', 101)
    const dataTransfer = { files: [file] }

    await zone.trigger('drop', { dataTransfer })

    expect(wrapper.emitted('files-selected')).toBeUndefined()
    expect(wrapper.find('[role="alert"]').text()).toContain('초과')
  })

  it('accepts multiple PDF files', async () => {
    const wrapper = mount(DropZone)
    const zone = wrapper.find('.drop-zone')

    const files = [createPdfFile('a.pdf'), createPdfFile('b.pdf')]
    const dataTransfer = { files }

    await zone.trigger('drop', { dataTransfer })

    const emitted = wrapper.emitted('files-selected')!
    expect(emitted[0][0]).toHaveLength(2)
  })

  it('adds drag-active class on dragover', async () => {
    const wrapper = mount(DropZone)
    const zone = wrapper.find('.drop-zone')

    await zone.trigger('dragover')
    expect(zone.classes()).toContain('drop-zone--active')

    await zone.trigger('dragleave')
    expect(zone.classes()).not.toContain('drop-zone--active')
  })

  it('triggers file input on click', async () => {
    const wrapper = mount(DropZone)
    const zone = wrapper.find('.drop-zone')
    const input = wrapper.find('input[type="file"]')
    const clickSpy = vi.spyOn(input.element as HTMLInputElement, 'click')

    await zone.trigger('click')

    expect(clickSpy).toHaveBeenCalled()
  })

  it('triggers file input on Enter keydown', async () => {
    const wrapper = mount(DropZone)
    const zone = wrapper.find('.drop-zone')
    const input = wrapper.find('input[type="file"]')
    const clickSpy = vi.spyOn(input.element as HTMLInputElement, 'click')

    await zone.trigger('keydown', { key: 'Enter' })

    expect(clickSpy).toHaveBeenCalled()
  })
})
