<script setup lang="ts">

interface Props {
  stats: Record<string, unknown>
  filename: string
  completedAt?: string
}

const props = withDefaults(defineProps<Props>(), {
  completedAt: '',
})

function formatValue(value: unknown): string {
  if (value == null) return '-'
  if (typeof value === 'number') return value.toLocaleString('ko-KR')
  return String(value)
}

function formatDate(dateStr: string): string {
  if (!dateStr) return '-'
  try {
    return new Date(dateStr).toLocaleString('ko-KR')
  } catch {
    return dateStr
  }
}
</script>

<template>
  <div class="stats-grid">
    <div class="stats-grid__item">
      <span class="stats-grid__label">파일명</span>
      <span class="stats-grid__value">{{ filename }}</span>
    </div>
    <div v-if="completedAt" class="stats-grid__item">
      <span class="stats-grid__label">완료 시각</span>
      <span class="stats-grid__value">{{ formatDate(completedAt) }}</span>
    </div>
    <div v-for="(value, key) in stats" :key="key" class="stats-grid__item">
      <span class="stats-grid__label">{{ key }}</span>
      <span class="stats-grid__value">{{ formatValue(value) }}</span>
    </div>
  </div>
</template>
