<script setup lang="ts">
import { computed } from 'vue'
import { formatDate } from '@/utils/format'

interface Props {
  stats: Record<string, unknown>
  metadata?: Record<string, unknown>
  filename: string
  completedAt?: string
}

const props = withDefaults(defineProps<Props>(), {
  completedAt: '',
  metadata: () => ({}),
})

const STAT_LABELS: Record<string, string> = {
  total_pages: '전체 페이지',
  parsed_pages: '파싱 페이지',
  tables_found: '테이블 수',
  tables_need_review: '검토 필요 테이블',
  text_blocks: '텍스트 블록',
  heading_count: '제목 수',
  empty_line_ratio: '빈 줄 비율',
  avg_line_length: '평균 줄 길이',
  parse_time_ms: '파싱 소요 시간',
  blocks_retried: '재시도 블록',
  blocks_fallback_ocr: 'OCR 폴백',
  blocks_fallback_vlm: 'VLM 폴백',
  avg_block_quality: '평균 블록 품질',
}

const META_LABELS: Record<string, string> = {
  source: '원본 파일',
  source_type: '파일 유형',
  pages: '페이지 수',
  parsed_at: '파싱 일시',
  parser_version: '파서 버전',
  ocr_used: 'OCR 사용',
  tables_extracted: '추출 테이블',
  tables_need_review: '검토 필요',
}

const HIDDEN_KEYS = new Set(['noise_removed'])

function label(key: string, map: Record<string, string>): string {
  return map[key] ?? key
}

function formatStatValue(key: string, value: unknown): string {
  if (value == null) return '-'
  if (typeof value === 'boolean') return value ? '예' : '아니오'
  if (typeof value === 'object') return '-'
  if (key === 'parse_time_ms' && typeof value === 'number') {
    return value >= 1000
      ? `${(value / 1000).toFixed(1)}초`
      : `${Math.round(value)}ms`
  }
  if (key === 'avg_block_quality' && typeof value === 'number') {
    return `${(value * 100).toFixed(1)}%`
  }
  if (key === 'empty_line_ratio' && typeof value === 'number') {
    return `${(value * 100).toFixed(1)}%`
  }
  if (key === 'avg_line_length' && typeof value === 'number') {
    return value.toFixed(1)
  }
  if (typeof value === 'number') return value.toLocaleString('ko-KR')
  return String(value)
}

const noiseStats = computed(() => {
  const raw = props.stats?.noise_removed as Record<string, number> | undefined
  if (!raw || typeof raw !== 'object') return null
  const entries = Object.entries(raw).filter(([, v]) => v > 0)
  return entries.length > 0 ? entries : null
})

const NOISE_LABELS: Record<string, string> = {
  headers: '헤더',
  footers: '푸터',
  page_numbers: '페이지 번호',
  toc_pages: '목차 페이지',
  toc_entries: '목차 항목',
  watermarks: '워터마크',
}

const visibleStats = computed(() =>
  Object.entries(props.stats ?? {}).filter(([key]) => !HIDDEN_KEYS.has(key)),
)

const visibleMeta = computed(() =>
  Object.entries(props.metadata ?? {}).filter(([key]) => !HIDDEN_KEYS.has(key)),
)
</script>

<template>
  <div class="stats-panel">
    <!-- File info row -->
    <div class="stats-panel__header">
      <div class="stats-panel__file">
        <span class="stats-panel__filename">{{ filename }}</span>
        <span v-if="completedAt" class="stats-panel__date">{{ formatDate(completedAt) }}</span>
      </div>
    </div>

    <div class="stats-panel__body">
      <!-- Metadata section -->
      <div v-if="visibleMeta.length > 0" class="stats-section">
        <h4 class="stats-section__title">문서 정보</h4>
        <div class="stats-grid">
          <div v-for="[key, value] in visibleMeta" :key="key" class="stats-grid__item">
            <span class="stats-grid__label">{{ label(key, META_LABELS) }}</span>
            <span class="stats-grid__value">{{ formatStatValue(key, value) }}</span>
          </div>
        </div>
      </div>

      <!-- Stats section -->
      <div v-if="visibleStats.length > 0" class="stats-section">
        <h4 class="stats-section__title">파싱 통계</h4>
        <div class="stats-grid">
          <div v-for="[key, value] in visibleStats" :key="key" class="stats-grid__item">
            <span class="stats-grid__label">{{ label(key, STAT_LABELS) }}</span>
            <span class="stats-grid__value">{{ formatStatValue(key, value) }}</span>
          </div>
        </div>
      </div>

      <!-- Noise removal section -->
      <div v-if="noiseStats" class="stats-section">
        <h4 class="stats-section__title">노이즈 제거</h4>
        <div class="stats-grid">
          <div v-for="[key, value] in noiseStats" :key="key" class="stats-grid__item">
            <span class="stats-grid__label">{{ NOISE_LABELS[key] ?? key }}</span>
            <span class="stats-grid__value">{{ value }}건</span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.stats-panel {
  background: var(--color-surface, #fff);
  border: 1px solid var(--color-border, #e2e8f0);
  border-radius: var(--radius-lg, 8px);
  overflow: hidden;
  margin-bottom: var(--space-3, 12px);
}

.stats-panel__header {
  padding: var(--space-3, 12px) var(--space-4, 16px);
  border-bottom: 1px solid var(--color-border, #e2e8f0);
  background: var(--color-surface-alt, #f8fafc);
}

.stats-panel__file {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3, 12px);
}

.stats-panel__filename {
  font-weight: 600;
  font-size: var(--text-base, 1rem);
  color: var(--color-text, #1a202c);
}

.stats-panel__date {
  font-size: var(--text-sm, 0.875rem);
  color: var(--color-text-muted, #718096);
}

.stats-panel__body {
  padding: var(--space-3, 12px) var(--space-4, 16px);
  display: flex;
  flex-direction: column;
  gap: var(--space-4, 16px);
}

.stats-section__title {
  font-size: var(--text-sm, 0.875rem);
  font-weight: 600;
  color: var(--color-text-muted, #718096);
  text-transform: uppercase;
  letter-spacing: 0.025em;
  margin: 0 0 var(--space-2, 8px) 0;
}

.stats-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: var(--space-2, 8px) var(--space-4, 16px);
}

.stats-grid__item {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.stats-grid__label {
  font-size: var(--text-xs, 0.75rem);
  color: var(--color-text-muted, #718096);
}

.stats-grid__value {
  font-size: var(--text-sm, 0.875rem);
  font-weight: 500;
  color: var(--color-text, #1a202c);
}
</style>
