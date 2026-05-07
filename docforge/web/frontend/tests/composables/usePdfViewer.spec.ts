import { describe, it, expect, vi, beforeEach } from 'vitest'
import { ref } from 'vue'

// Mock pdfjs-dist
vi.mock('pdfjs-dist', () => {
  const mockPage = {
    getViewport: vi.fn(() => ({ width: 800, height: 1000 })),
    render: vi.fn(() => ({ promise: Promise.resolve() })),
  }

  const mockDocument = {
    numPages: 3,
    getPage: vi.fn(() => Promise.resolve(mockPage)),
    destroy: vi.fn(),
  }

  return {
    getDocument: vi.fn(() => ({
      promise: Promise.resolve(mockDocument),
    })),
    GlobalWorkerOptions: { workerSrc: '' },
  }
})

// Mock onUnmounted
vi.mock('vue', async () => {
  const actual = await vi.importActual('vue')
  return {
    ...actual,
    onUnmounted: vi.fn(),
  }
})

import { usePdfViewer } from '@/composables/usePdfViewer'
import * as pdfjsLib from 'pdfjs-dist'

beforeEach(() => {
  vi.clearAllMocks()
})

describe('usePdfViewer', () => {
  function createMockContainer() {
    const container = document.createElement('div')
    // Mock methods
    Object.defineProperty(container, 'scrollTop', { value: 0, writable: true })
    Object.defineProperty(container, 'getBoundingClientRect', {
      value: () => ({ top: 0, left: 0, width: 800, height: 600 }),
    })
    return container
  }

  it('initializes with default state', () => {
    const containerRef = ref(createMockContainer())
    const { totalPages, currentPage, isLoading, error } = usePdfViewer({
      containerRef,
    })

    expect(totalPages.value).toBe(0)
    expect(currentPage.value).toBe(1)
    expect(isLoading.value).toBe(false)
    expect(error.value).toBeNull()
  })

  it('loads a PDF document', async () => {
    const containerRef = ref(createMockContainer())
    const { loadDocument, totalPages, isLoading } = usePdfViewer({
      containerRef,
    })

    await loadDocument('http://example.com/test.pdf')

    expect(pdfjsLib.getDocument).toHaveBeenCalledWith('http://example.com/test.pdf')
    expect(totalPages.value).toBe(3)
    expect(isLoading.value).toBe(false)
  })

  it('handles PDF load error', async () => {
    vi.mocked(pdfjsLib.getDocument).mockReturnValueOnce({
      promise: Promise.reject(new Error('Failed to load PDF')),
    } as ReturnType<typeof pdfjsLib.getDocument>)

    const containerRef = ref(createMockContainer())
    const { loadDocument, error } = usePdfViewer({ containerRef })

    await loadDocument('bad-url')

    expect(error.value).toBe('Failed to load PDF')
  })

  it('goToPage calls scrollIntoView on correct canvas', async () => {
    const containerRef = ref(createMockContainer())
    const { loadDocument, goToPage } = usePdfViewer({ containerRef })

    await loadDocument('test.pdf')

    // After loading, canvas elements should be appended to container
    const canvases = containerRef.value.querySelectorAll('canvas')
    expect(canvases.length).toBe(3)

    // Mock scrollIntoView
    const scrollIntoViewMock = vi.fn()
    canvases[1].scrollIntoView = scrollIntoViewMock

    goToPage(2)
    expect(scrollIntoViewMock).toHaveBeenCalledWith({
      behavior: 'smooth',
      block: 'start',
    })
  })

  it('cleanup destroys PDF document', async () => {
    const containerRef = ref(createMockContainer())
    const { loadDocument, cleanup } = usePdfViewer({ containerRef })

    await loadDocument('test.pdf')
    cleanup()

    // After cleanup, container should be empty
    expect(containerRef.value.children.length).toBe(0)
  })

  it('goToPage is no-op for invalid page numbers', async () => {
    const containerRef = ref(createMockContainer())
    const { loadDocument, goToPage, currentPage } = usePdfViewer({
      containerRef,
    })

    await loadDocument('test.pdf')

    goToPage(0)
    expect(currentPage.value).toBe(1)

    goToPage(99)
    expect(currentPage.value).toBe(1)
  })
})
