import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { useParseTask } from '@/composables/useParseTask'

class MockEventSource {
  url: string
  listeners: Record<string, ((e: MessageEvent) => void)[]> = {}
  onmessage: ((e: MessageEvent) => void) | null = null
  onerror: (() => void) | null = null
  readyState = 1

  constructor(url: string) {
    this.url = url
    MockEventSource.instances.push(this)
  }

  addEventListener(type: string, handler: (e: MessageEvent) => void) {
    if (!this.listeners[type]) this.listeners[type] = []
    this.listeners[type].push(handler)
  }

  close() {
    this.readyState = 2
  }

  emit(type: string, data: Record<string, unknown>) {
    const event = { data: JSON.stringify(data) } as MessageEvent
    if (this.listeners[type]) {
      for (const handler of this.listeners[type]) {
        handler(event)
      }
    }
  }

  emitMessage(data: Record<string, unknown>) {
    const event = { data: JSON.stringify(data) } as MessageEvent
    if (this.listeners['message']) {
      for (const handler of this.listeners['message']) {
        handler(event)
      }
    }
  }

  triggerError() {
    if (this.onerror) this.onerror()
  }

  static instances: MockEventSource[] = []
  static clear() { MockEventSource.instances = [] }
  static latest(): MockEventSource | undefined {
    return MockEventSource.instances[MockEventSource.instances.length - 1]
  }
}

beforeEach(() => {
  MockEventSource.clear()
  vi.stubGlobal('EventSource', MockEventSource)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('useParseTask', () => {
  it('initializes with default state', () => {
    const task = useParseTask('test-task-1')

    expect(task.taskId).toBe('test-task-1')
    expect(task.status.value).toBe('queued')
    expect(task.pct.value).toBe(0)
    expect(task.currentStage.value).toBe('')
    expect(task.totalPages.value).toBe(0)
    expect(task.completedPages.value).toBe(0)
    expect(task.error.value).toBeNull()
    expect(task.isConnected.value).toBe(false)
  })

  it('connects to EventSource with correct URL', () => {
    const task = useParseTask('task-123')
    task.connect()

    const es = MockEventSource.latest()
    expect(es).toBeDefined()
    expect(es!.url).toContain('/api/parse/task-123/status')
    expect(task.isConnected.value).toBe(true)
  })

  it('uses injected getStatusUrl when provided', () => {
    const customUrl = vi.fn((id: string) => `/custom/${id}/stream`)
    const task = useParseTask('task-456', { getStatusUrl: customUrl })
    task.connect()

    const es = MockEventSource.latest()
    expect(customUrl).toHaveBeenCalledWith('task-456')
    expect(es!.url).toBe('/custom/task-456/stream')
  })

  it('handles catchup event', () => {
    const task = useParseTask('task-123')
    task.connect()

    const es = MockEventSource.latest()!
    es.emit('catchup', {
      filename: 'test.pdf',
      status: 'running',
      pct: 45,
      current_stage: 'pages',
      total_pages: 10,
      completed_pages: 5,
    })

    expect(task.filename.value).toBe('test.pdf')
    expect(task.status.value).toBe('running')
    expect(task.pct.value).toBe(45)
    expect(task.currentStage.value).toBe('pages')
    expect(task.totalPages.value).toBe(10)
    expect(task.completedPages.value).toBe(5)
  })

  it('handles page_result event', () => {
    const onPageResult = vi.fn()
    const task = useParseTask('task-123', { onPageResult })
    task.connect()

    const es = MockEventSource.latest()!
    es.emit('page_result', {
      page_num: 3,
      markdown: '# Page 3 content',
      pct: 30,
    })

    expect(task.pageMarkdowns.value.get(3)).toBe('# Page 3 content')
    expect(task.completedPages.value).toBe(1)
    expect(task.pct.value).toBe(30)
    expect(onPageResult).toHaveBeenCalledWith(3, '# Page 3 content')
  })

  it('handles done event', () => {
    const onDone = vi.fn()
    const task = useParseTask('task-123', { onDone })
    task.connect()

    const es = MockEventSource.latest()!
    es.emit('done', { markdown: 'full content' })

    expect(task.status.value).toBe('done')
    expect(task.pct.value).toBe(100)
    expect(task.currentStage.value).toBe('done')
    expect(onDone).toHaveBeenCalled()
    expect(es.readyState).toBe(2)
  })

  it('handles error event', () => {
    const onError = vi.fn()
    const task = useParseTask('task-123', { onError })
    task.connect()

    const es = MockEventSource.latest()!
    es.emit('error', { message: 'Something failed' })

    expect(task.status.value).toBe('error')
    expect(task.error.value).toBe('Something failed')
    expect(onError).toHaveBeenCalledWith('Something failed')
  })

  it('handles profiling stage event', () => {
    const onStageChange = vi.fn()
    const task = useParseTask('task-123', { onStageChange })
    task.connect()

    const es = MockEventSource.latest()!
    es.emit('profiling', { pct: 5 })

    expect(task.status.value).toBe('running')
    expect(task.currentStage.value).toBe('profiling')
    expect(task.pct.value).toBe(5)
    expect(onStageChange).toHaveBeenCalledWith('profiling')
  })

  it('handles onmessage fallback with event field in data', () => {
    const task = useParseTask('task-123')
    task.connect()

    const es = MockEventSource.latest()!
    es.emitMessage({
      event: 'page_progress',
      total_pages: 20,
      completed_pages: 8,
      pct: 40,
    })

    expect(task.totalPages.value).toBe(20)
    expect(task.completedPages.value).toBe(8)
    expect(task.pct.value).toBe(40)
  })

  it('retries on connection error (non-terminal state)', () => {
    vi.useFakeTimers()
    const task = useParseTask('task-123')
    task.connect()

    const es1 = MockEventSource.latest()!
    es1.triggerError()

    expect(task.isConnected.value).toBe(false)

    vi.advanceTimersByTime(3000)

    expect(MockEventSource.instances.length).toBe(2)
    vi.useRealTimers()
  })

  it('does not retry when task is in terminal state', () => {
    vi.useFakeTimers()
    const task = useParseTask('task-123')
    task.connect()

    const es = MockEventSource.latest()!
    es.emit('done', {})

    const instances = MockEventSource.instances.length

    vi.advanceTimersByTime(3000)
    expect(MockEventSource.instances.length).toBe(instances)
    vi.useRealTimers()
  })

  it('disconnect closes EventSource', () => {
    const task = useParseTask('task-123')
    task.connect()

    const es = MockEventSource.latest()!
    task.disconnect()

    expect(es.readyState).toBe(2)
    expect(task.isConnected.value).toBe(false)
  })
})
