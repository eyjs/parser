<script setup lang="ts">
import { useQueueStatus } from '@/composables/useQueueStatus'

const { running, queued, maxWorkers } = useQueueStatus()
</script>

<template>
  <div
    v-if="running > 0 || queued > 0"
    class="queue-banner"
    aria-live="polite"
  >
    <span>
      처리 중 {{ running }}개 / 대기 {{ queued }}개
      <template v-if="maxWorkers > 0">
        (최대 {{ maxWorkers }} 워커)
      </template>
    </span>
  </div>
</template>
