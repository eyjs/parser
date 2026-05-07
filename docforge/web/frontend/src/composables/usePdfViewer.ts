import { ref, onUnmounted, type Ref } from 'vue'
import * as pdfjsLib from 'pdfjs-dist'

// Configure worker
pdfjsLib.GlobalWorkerOptions.workerSrc = new URL(
  'pdfjs-dist/build/pdf.worker.mjs',
  import.meta.url,
).toString()

export interface UsePdfViewerOptions {
  scale?: number
  containerRef: Ref<HTMLElement | null>
}

function clearContainer(container: HTMLElement) {
  while (container.firstChild) {
    container.removeChild(container.firstChild)
  }
}

export function usePdfViewer(options: UsePdfViewerOptions) {
  const { scale = 1.5, containerRef } = options

  const totalPages = ref(0)
  const currentPage = ref(1)
  const isLoading = ref(false)
  const error = ref<string | null>(null)

  let pdfDocument: pdfjsLib.PDFDocumentProxy | null = null
  const canvasElements: HTMLCanvasElement[] = []
  let observer: IntersectionObserver | null = null
  let renderCancelled = false

  async function loadDocument(source: string | ArrayBuffer) {
    cleanup()
    isLoading.value = true
    error.value = null

    try {
      const loadingTask = pdfjsLib.getDocument(source)
      pdfDocument = await loadingTask.promise
      totalPages.value = pdfDocument.numPages
      await renderAllPages()
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'PDF load failed'
    } finally {
      isLoading.value = false
    }
  }

  const RENDER_CHUNK = 3

  async function renderAllPages() {
    if (!pdfDocument || !containerRef.value) return
    renderCancelled = false

    const container = containerRef.value
    clearContainer(container)
    canvasElements.length = 0

    for (let i = 1; i <= pdfDocument.numPages; i++) {
      if (renderCancelled) return

      const page = await pdfDocument.getPage(i)
      const viewport = page.getViewport({ scale })

      const canvas = document.createElement('canvas')
      canvas.width = viewport.width
      canvas.height = viewport.height
      canvas.dataset.pageNum = String(i)
      canvas.style.display = 'block'
      canvas.style.marginBottom = '8px'

      const ctx = canvas.getContext('2d')
      if (ctx) {
        await page.render({ canvasContext: ctx, viewport }).promise
      }

      if (renderCancelled) return

      container.appendChild(canvas)
      canvasElements.push(canvas)

      if (i % RENDER_CHUNK === 0) {
        await new Promise((r) => setTimeout(r, 0))
      }
    }

    if (!renderCancelled) {
      setupIntersectionObserver()
    }
  }

  function setupIntersectionObserver() {
    if (!containerRef.value) return

    observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            const pageNum = Number((entry.target as HTMLCanvasElement).dataset.pageNum)
            if (pageNum && pageNum > 0) {
              currentPage.value = pageNum
            }
          }
        }
      },
      {
        root: containerRef.value,
        threshold: 0.5,
      },
    )

    for (const canvas of canvasElements) {
      observer.observe(canvas)
    }
  }

  function goToPage(pageNum: number) {
    if (pageNum < 1 || pageNum > totalPages.value) return
    const canvas = canvasElements[pageNum - 1]
    if (canvas) {
      canvas.scrollIntoView({ behavior: 'smooth', block: 'start' })
      currentPage.value = pageNum
    }
  }

  function getCurrentPageFromScroll(): number {
    if (!containerRef.value || canvasElements.length === 0) return 1

    const container = containerRef.value
    const scrollTop = container.scrollTop
    const containerTop = container.getBoundingClientRect().top

    for (let i = canvasElements.length - 1; i >= 0; i--) {
      const canvas = canvasElements[i]
      const canvasTop = canvas.getBoundingClientRect().top - containerTop + scrollTop
      if (scrollTop >= canvasTop - 10) {
        return i + 1
      }
    }

    return 1
  }

  function cleanup() {
    renderCancelled = true
    if (observer) {
      observer.disconnect()
      observer = null
    }

    for (const canvas of canvasElements) {
      const ctx = canvas.getContext('2d')
      if (ctx) {
        ctx.clearRect(0, 0, canvas.width, canvas.height)
      }
    }
    canvasElements.length = 0

    if (pdfDocument) {
      pdfDocument.destroy()
      pdfDocument = null
    }

    if (containerRef.value) {
      clearContainer(containerRef.value)
    }

    totalPages.value = 0
    currentPage.value = 1
  }

  onUnmounted(() => {
    cleanup()
  })

  return {
    totalPages,
    currentPage,
    isLoading,
    error,
    loadDocument,
    goToPage,
    getCurrentPageFromScroll,
    cleanup,
  }
}
