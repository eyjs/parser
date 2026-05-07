<script setup lang="ts">
import { ref, computed, watch, onUnmounted } from 'vue'
import DOMPurify from 'dompurify'
import { computeLineDiff, renderDiffHtml, type DiffLine } from '@/utils/diff'

interface Props {
  baseMarkdown: string
  baseLabel?: string
}

const props = withDefaults(defineProps<Props>(), {
  baseLabel: '기준 MD',
})

const compareMarkdown = ref('')
const outputFormat = ref<'side-by-side' | 'line-by-line'>('side-by-side')
const diffLines = ref<DiffLine[]>([])
const diffHtmlContent = ref('')

let debounceTimer: ReturnType<typeof setTimeout> | null = null

const hasChanges = computed(() => {
  return diffLines.value.some((l) => l.type !== 'unchanged')
})

const addedCount = computed(() => diffLines.value.filter((l) => l.type === 'added').length)
const removedCount = computed(() => diffLines.value.filter((l) => l.type === 'removed').length)

function computeDiff() {
  if (!props.baseMarkdown && !compareMarkdown.value) {
    diffLines.value = []
    diffHtmlContent.value = ''
    return
  }

  diffLines.value = computeLineDiff(props.baseMarkdown, compareMarkdown.value)
  diffHtmlContent.value = DOMPurify.sanitize(renderDiffHtml(
    props.baseMarkdown,
    compareMarkdown.value,
    outputFormat.value,
  ))
}

function onCompareInput(e: Event) {
  const target = e.target as HTMLTextAreaElement
  compareMarkdown.value = target.value

  if (debounceTimer) clearTimeout(debounceTimer)
  debounceTimer = setTimeout(computeDiff, 300)
}

watch(
  () => props.baseMarkdown,
  () => {
    computeDiff()
  },
)

watch(outputFormat, () => {
  computeDiff()
})

function setCompareText(text: string) {
  compareMarkdown.value = text
  computeDiff()
}

onUnmounted(() => {
  if (debounceTimer) clearTimeout(debounceTimer)
})

defineExpose({ setCompareText })
</script>

<template>
  <div class="md-diff-panel">
    <!-- Controls -->
    <div class="md-diff-panel__controls">
      <div class="md-diff-panel__stats" v-if="hasChanges">
        <span class="diff-stat diff-stat--added">+{{ addedCount }}</span>
        <span class="diff-stat diff-stat--removed">-{{ removedCount }}</span>
      </div>
      <select
        v-model="outputFormat"
        class="md-diff-panel__format"
        aria-label="Diff 표시 형식"
      >
        <option value="side-by-side">나란히 보기</option>
        <option value="line-by-line">줄 단위 보기</option>
      </select>
    </div>

    <!-- Editor area -->
    <div class="md-diff-panel__editors">
      <!-- Base (read-only) -->
      <div class="md-diff-panel__side">
        <div class="md-diff-panel__label">{{ baseLabel }} (읽기전용)</div>
        <textarea
          class="md-textarea md-textarea--readonly"
          :value="baseMarkdown"
          readonly
          spellcheck="false"
          wrap="off"
          aria-label="기준 마크다운"
        ></textarea>
      </div>

      <!-- Compare (editable) -->
      <div class="md-diff-panel__side">
        <div class="md-diff-panel__label">비교 MD (편집 가능)</div>
        <textarea
          class="md-textarea"
          :value="compareMarkdown"
          spellcheck="false"
          wrap="off"
          placeholder="비교할 마크다운을 입력하거나 파일을 업로드하세요"
          aria-label="비교 마크다운 편집"
          @input="onCompareInput"
        ></textarea>
      </div>
    </div>

    <!-- Diff output -->
    <div v-if="diffHtmlContent" class="md-diff-panel__output">
      <h4 class="section-title">Diff 결과</h4>
      <div class="diff-view" v-html="diffHtmlContent"></div>
    </div>

    <div v-else-if="compareMarkdown && !hasChanges" class="text-muted text-sm" style="padding: var(--space-3); text-align: center;">
      변경 사항 없음
    </div>
  </div>
</template>

<style scoped>
.md-diff-panel__controls {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--space-2) var(--space-3);
  border-bottom: 1px solid var(--color-border);
}

.md-diff-panel__stats {
  display: flex;
  gap: var(--space-2);
}

.diff-stat {
  font-size: var(--font-size-sm);
  font-weight: 600;
}

.diff-stat--added {
  color: var(--color-success);
}

.diff-stat--removed {
  color: var(--color-error);
}

.md-diff-panel__format {
  padding: var(--space-1) var(--space-2);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  font-size: var(--font-size-sm);
}

.md-diff-panel__editors {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-2);
  padding: var(--space-2);
}

.md-diff-panel__side {
  display: flex;
  flex-direction: column;
}

.md-diff-panel__label {
  font-size: var(--font-size-sm);
  font-weight: 600;
  padding: var(--space-1) var(--space-2);
  background: var(--color-bg-subtle);
  border-radius: var(--radius-sm) var(--radius-sm) 0 0;
}

.md-textarea {
  width: 100%;
  min-height: 300px;
  border: 1px solid var(--color-border);
  border-radius: 0 0 var(--radius-sm) var(--radius-sm);
  padding: var(--space-2);
  font-family: var(--font-mono);
  font-size: var(--font-size-sm);
  line-height: 1.6;
  resize: vertical;
}

.md-textarea--readonly {
  background: var(--color-bg-subtle);
  cursor: default;
}

.md-diff-panel__output {
  padding: var(--space-3);
  border-top: 1px solid var(--color-border);
  max-height: 400px;
  overflow: auto;
}
</style>
