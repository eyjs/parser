import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { useQueueStatus } from '@/composables/useQueueStatus'

vi.mock('@/api/client', () => ({
  getQueueStatus: vi.fn(),
}))

vi.mock('vue', async () => {
  const actual = await vi.importActual('vue')
  return {
    ...actual,
    onMounted: (fn: () => void) => fn(),
    onUnmounted: vi.fn(),
  }
})

import { getQueueStatus } from '@/api/client'

const mockGetQueueStatus = vi.mocked(getQueueStatus)

beforeEach(() => {
  vi.useFakeTimers()
  mockGetQueueStatus.mockResolvedValue({
    running: 2,
    queued: 3,
    max_workers: 4,
  })
})

afterEach(() => {
  vi.useRealTimers()
  vi.clearAllMocks()
})

describe('useQueueStatus', () => {
  it('fetches status immediately on start', async () => {
    const { running, queued, maxWorkers } = useQueueStatus()

    await vi.advanceTimersByTimeAsync(0)

    expect(mockGetQueueStatus).toHaveBeenCalledTimes(1)
    expect(running.value).toBe(2)
    expect(queued.value).toBe(3)
    expect(maxWorkers.value).toBe(4)
  })

  it('polls at the specified interval', async () => {
    const { stopPolling } = useQueueStatus(2000)

    await vi.advanceTimersByTimeAsync(0)
    expect(mockGetQueueStatus).toHaveBeenCalledTimes(1)

    await vi.advanceTimersByTimeAsync(2000)
    expect(mockGetQueueStatus).toHaveBeenCalledTimes(2)

    await vi.advanceTimersByTimeAsync(2000)
    expect(mockGetQueueStatus).toHaveBeenCalledTimes(3)

    stopPolling()
  })

  it('stopPolling stops the interval', async () => {
    const { stopPolling } = useQueueStatus(1000)

    await vi.advanceTimersByTimeAsync(0)
    expect(mockGetQueueStatus).toHaveBeenCalledTimes(1)

    stopPolling()

    await vi.advanceTimersByTimeAsync(5000)
    expect(mockGetQueueStatus).toHaveBeenCalledTimes(1)
  })

  it('handles fetch errors gracefully', async () => {
    mockGetQueueStatus.mockRejectedValueOnce(new Error('Network error'))

    const { error, running, stopPolling } = useQueueStatus()

    await vi.advanceTimersByTimeAsync(0)

    expect(error.value).toBe('Network error')
    expect(running.value).toBe(0)

    stopPolling()
  })

  it('clears error on successful fetch after failure', async () => {
    mockGetQueueStatus.mockRejectedValueOnce(new Error('fail'))

    const { error, stopPolling } = useQueueStatus(1000)

    await vi.advanceTimersByTimeAsync(0)
    expect(error.value).toBe('fail')

    mockGetQueueStatus.mockResolvedValueOnce({
      running: 1,
      queued: 0,
      max_workers: 4,
    })

    await vi.advanceTimersByTimeAsync(1000)
    expect(error.value).toBeNull()

    stopPolling()
  })
})
