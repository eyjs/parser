<script setup lang="ts">
import { ref, computed, watch, onUnmounted } from 'vue'
import { saveMarkdown } from '@/api/client'
import { marked } from 'marked'
import DOMPurify from 'dompurify'

interface Props {
  taskId: string
  initialMarkdown: string
  isLiveMode?: boolean
  livePageMarkdowns?: Map<number, string>
}

const props = withDefaults(defineProps<Props>(), {
  isLiveMode: false,
  livePageMarkdowns: () => new Map(),
})

const emit = defineEmits<{
  'scroll-to-page': [page: number]
}>()

const PAGE_SEP = '<!-- pagebreak -->'

const markdown = ref(props.initialMarkdown)
const previewMode = ref(false)
const isSaving = ref(false)
const saveSuccess = ref(false)
const charCount = computed(() => markdown.value.length)

let autoSaveTimer: ReturnType<typeof setTimeout> | null = null

// Sync initial markdown when it changes (e.g., result loaded)
watch(
  () => props.initialMarkdown,
  (val) => {
    if (!props.isLiveMode) {
      markdown.value = val
    }
  },
)

// In live mode, rebuild markdown from page map
watch(
  () => props.livePageMarkdowns,
  (pages) => {
    if (!props.isLiveMode || !pages || pages.size === 0) return
    const sorted = Array.from(pages.entries()).sort((a, b) => a[0] - b[0])
    markdown.value = sorted.map(([, md]) => md).join(`\n${PAGE_SEP}\n`)
  },
  { deep: true },
)

const saveError = ref<string | null>(null)

const renderedHtml = computed(() => {
  try {
    return DOMPurify.sanitize(marked(markdown.value) as string)
  } catch {
    return '<p>렌더링 오류</p>'
  }
})

function togglePreview() {
  previewMode.value = !previewMode.value
}

function onInput(e: Event) {
  const target = e.target as HTMLTextAreaElement
  markdown.value = target.value

  // Debounced preview update
  if (previewMode.value) {
    if (autoSaveTimer) clearTimeout(autoSaveTimer)
    autoSaveTimer = setTimeout(() => {
      // preview auto-updates via computed
    }, 300)
  }
}

function onKeydown(e: KeyboardEvent) {
  // Tab key inserts spaces
  if (e.key === 'Tab') {
    e.preventDefault()
    const target = e.target as HTMLTextAreaElement
    const start = target.selectionStart
    const end = target.selectionEnd
    markdown.value = markdown.value.slice(0, start) + '  ' + markdown.value.slice(end)
    // Restore cursor position after Vue re-renders
    requestAnimationFrame(() => {
      target.selectionStart = start + 2
      target.selectionEnd = start + 2
    })
  }

  // Ctrl/Cmd + S saves
  if ((e.ctrlKey || e.metaKey) && e.key === 's') {
    e.preventDefault()
    onSave()
  }
}

async function onSave() {
  if (isSaving.value) return
  isSaving.value = true
  saveSuccess.value = false

  try {
    await saveMarkdown(props.taskId, markdown.value)
    saveSuccess.value = true
    saveError.value = null
    setTimeout(() => { saveSuccess.value = false }, 2000)
  } catch (e) {
    saveError.value = e instanceof Error ? e.message : '저장 중 오류가 발생했습니다.'
  } finally {
    isSaving.value = false
  }
}

function onScroll(e: Event) {
  const target = e.target as HTMLTextAreaElement
  const lineHeight = getLineHeight(target)
  const topLine = Math.floor(target.scrollTop / lineHeight)
  const lines = markdown.value.split('\n')

  let page = 1
  for (let i = 0; i < lines.length && i <= topLine; i++) {
    if (lines[i].trim() === PAGE_SEP) {
      page++
    }
  }

  emit('scroll-to-page', page)
}

function getLineHeight(el: HTMLElement): number {
  const style = window.getComputedStyle(el)
  const lh = parseFloat(style.lineHeight)
  if (isNaN(lh)) {
    return parseFloat(style.fontSize) * 1.5
  }
  return lh
}

onUnmounted(() => {
  if (autoSaveTimer) clearTimeout(autoSaveTimer)
})
</script>

<template>
  <div class="editor-panel">
    <div class="editor-panel__header">
      <span class="editor-panel__title">마크다운 편집기</span>
      <div class="editor-panel__actions">
        <span class="text-muted text-sm">{{ charCount.toLocaleString('ko-KR') }}자</span>
        <button
          class="btn btn--secondary btn--sm"
          @click="togglePreview"
        >
          {{ previewMode ? '편집' : '미리보기' }}
        </button>
        <button
          class="btn btn--primary btn--sm"
          :disabled="isSaving"
          @click="onSave"
        >
          {{ isSaving ? '저장 중...' : saveSuccess ? '저장됨' : '저장' }}
        </button>
      </div>
    </div>

    <p v-if="saveError" class="alert alert--error" role="alert" style="margin: var(--space-2) var(--space-3) 0;">
      {{ saveError }}
    </p>

    <div v-if="isLiveMode" class="live-badge">
      <span class="live-badge__dot"></span>
      <span class="live-badge__text">
        실시간 수신 중... ({{ livePageMarkdowns.size }} 페이지)
      </span>
    </div>

    <!-- Editor mode -->
    <div v-show="!previewMode" class="editor-panel__body">
      <textarea
        class="md-textarea"
        :value="markdown"
        spellcheck="false"
        wrap="off"
        aria-label="마크다운 편집"
        @input="onInput"
        @keydown="onKeydown"
        @scroll="onScroll"
      ></textarea>
    </div>

    <!-- Preview mode -->
    <div
      v-show="previewMode"
      class="editor-panel__body markdown-preview"
      v-html="renderedHtml"
    ></div>
  </div>
</template>

<style scoped>
.editor-panel__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--space-2) var(--space-3);
  border-bottom: 1px solid var(--color-border);
}

.editor-panel__title {
  font-weight: 600;
  font-size: var(--font-size-sm);
}

.editor-panel__actions {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.editor-panel__body {
  flex: 1;
  overflow: auto;
}

.md-textarea {
  width: 100%;
  height: 100%;
  min-height: calc(100vh - 250px);
  border: none;
  resize: none;
  padding: var(--space-3);
  font-family: var(--font-mono);
  font-size: var(--font-size-sm);
  line-height: 1.6;
  outline: none;
}

.markdown-preview {
  padding: var(--space-3);
}
</style>
