import { ref, onMounted, onUnmounted } from 'vue'
import { getQueueStatus } from '@/api/client'
import type { QueueStatus } from '@/api/types'

const DEFAULT_INTERVAL_MS = 5000

export function useQueueStatus(intervalMs = DEFAULT_INTERVAL_MS) {
  const running = ref(0)
  const queued = ref(0)
  const maxWorkers = ref(0)
  const isLoading = ref(false)
  const error = ref<string | null>(null)

  let timer: ReturnType<typeof setInterval> | null = null

  async function fetchStatus() {
    try {
      isLoading.value = true
      error.value = null
      const data: QueueStatus = await getQueueStatus()
      running.value = data.running
      queued.value = data.queued
      maxWorkers.value = data.max_workers
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Queue status fetch failed'
    } finally {
      isLoading.value = false
    }
  }

  function startPolling() {
    if (timer) return
    fetchStatus()
    timer = setInterval(fetchStatus, intervalMs)
  }

  function stopPolling() {
    if (timer) {
      clearInterval(timer)
      timer = null
    }
  }

  onMounted(() => {
    startPolling()
  })

  onUnmounted(() => {
    stopPolling()
  })

  return {
    running,
    queued,
    maxWorkers,
    isLoading,
    error,
    startPolling,
    stopPolling,
    fetchStatus,
  }
}
