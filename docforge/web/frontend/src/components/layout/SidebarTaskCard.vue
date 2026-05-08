<script setup lang="ts">
import { computed } from 'vue'
import type { HistoryEntry } from '@/domain/types'

const props = defineProps<{
  task: HistoryEntry
  active: boolean
}>()

const emit = defineEmits<{
  click: []
}>()

const statusLabel = computed(() => {
  switch (props.task.status) {
    case 'done': return '완료'
    case 'running': return '진행중'
    case 'queued': return '대기'
    case 'error': return '오류'
    case 'cancelled': return '취소'
    default: return props.task.status
  }
})

const statusVariant = computed(() => {
  switch (props.task.status) {
    case 'done': return 'done'
    case 'running': return 'running'
    case 'queued': return 'queued'
    case 'error': return 'error'
    case 'cancelled': return 'error'
    default: return 'queued'
  }
})

const formattedDate = computed(() => {
  if (!props.task.createdAt) return ''
  const date = new Date(props.task.createdAt)
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  const hours = String(date.getHours()).padStart(2, '0')
  const minutes = String(date.getMinutes()).padStart(2, '0')
  return `${month}-${day} ${hours}:${minutes}`
})

const pageLabel = computed(() => {
  if (!props.task.totalPages) return ''
  return `${props.task.totalPages}p`
})

const showProgress = computed(() => {
  return props.task.status === 'running' && props.task.progressPct > 0
})
</script>

<template>
  <div
    :class="[
      'sidebar-card',
      { 'sidebar-card--active': active },
      { 'sidebar-card--error': task.status === 'error' },
    ]"
    role="button"
    tabindex="0"
    :aria-current="active ? 'page' : undefined"
    :aria-label="`${task.filename} - ${statusLabel}`"
    @click="emit('click')"
    @keydown.enter="emit('click')"
    @keydown.space.prevent="emit('click')"
  >
    <div class="sidebar-card__name" :title="task.filename">
      {{ task.filename }}
    </div>
    <div class="sidebar-card__meta">
      <span v-if="formattedDate">{{ formattedDate }}</span>
      <span v-if="pageLabel">{{ pageLabel }}</span>
      <span
        :class="['sidebar-card__badge', `sidebar-card__badge--${statusVariant}`]"
      >
        {{ statusLabel }}
      </span>
    </div>
    <div v-if="showProgress" class="sidebar-card__progress">
      <div
        class="sidebar-card__progress-fill"
        :style="{ width: `${task.progressPct}%` }"
      />
    </div>
  </div>
</template>

<style scoped>
.sidebar-card {
  padding: var(--space-3);
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: background var(--transition-fast);
  border-left: 3px solid transparent;
}

.sidebar-card:hover {
  background: var(--sidebar-card-hover);
}

.sidebar-card--active {
  background: var(--sidebar-card-active);
  border-left-color: var(--color-primary);
}

.sidebar-card--error {
  border-left-color: var(--color-error);
}

.sidebar-card:focus-visible {
  outline: 2px solid var(--color-border-focus);
  outline-offset: -2px;
}

.sidebar-card__name {
  font-size: var(--font-size-sm);
  font-weight: var(--font-weight-medium);
  color: var(--color-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.sidebar-card__meta {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-top: var(--space-1);
  font-size: var(--font-size-xs);
  color: var(--color-text-muted);
}

.sidebar-card__badge {
  display: inline-block;
  padding: 1px var(--space-2);
  border-radius: var(--radius-full);
  font-size: var(--font-size-xs);
  font-weight: var(--font-weight-medium);
  line-height: 1.4;
}

.sidebar-card__badge--done {
  background: var(--color-success-bg);
  color: var(--color-success);
}

.sidebar-card__badge--running {
  background: var(--color-primary-bg);
  color: var(--color-primary);
  animation: pulse 1.5s ease-in-out infinite;
}

.sidebar-card__badge--queued {
  background: var(--color-surface-alt);
  color: var(--color-text-muted);
}

.sidebar-card__badge--error {
  background: var(--color-error-bg);
  color: var(--color-error);
}

.sidebar-card__progress {
  margin-top: var(--space-2);
  height: 4px;
  background: var(--color-progress-track);
  border-radius: var(--radius-full);
  overflow: hidden;
}

.sidebar-card__progress-fill {
  height: 100%;
  background: var(--color-progress-fill);
  border-radius: var(--radius-full);
  transition: width var(--transition-normal);
}

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.6; }
}
</style>
