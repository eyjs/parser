<script setup lang="ts">
import { ref, computed } from 'vue'
import { getParseResult } from '@/api/client'
import BaseAlert from '@/components/common/BaseAlert.vue'
import { useHistoryStore } from '@/stores/history'
import CompareToolbar from '@/components/compare/CompareToolbar.vue'
import PdfComparePanel from '@/components/compare/PdfComparePanel.vue'
import MdDiffPanel from '@/components/compare/MdDiffPanel.vue'
import MdUploader from '@/components/compare/MdUploader.vue'
import type { HistoryItem } from '@/api/types'

type CompareMode = 'pdf-vs-md' | 'md-vs-md'

const historyStore = useHistoryStore()
const mode = ref<CompareMode>('md-vs-md')

// PDF vs MD state
const pdfSource = ref<string | ArrayBuffer | null>(null)
const pdfMdContent = ref('')

// MD vs MD state
const baseMd = ref('')
const baseMdLabel = ref('기준 MD')
const mdDiffPanel = ref<InstanceType<typeof MdDiffPanel> | null>(null)

// History task selection
const selectedTaskId = ref('')
const isLoadingTask = ref(false)
const loadError = ref<string | null>(null)

const doneItems = computed((): HistoryItem[] => {
  return historyStore.items.filter((i) => i.status === 'done')
})

// Load history on mount
historyStore.fetchHistory()

// PDF file upload handler
function onPdfFileSelected(e: Event) {
  const input = e.target as HTMLInputElement
  if (!input.files || input.files.length === 0) return

  const file = input.files[0]
  const reader = new FileReader()
  reader.onload = () => {
    pdfSource.value = reader.result as ArrayBuffer
  }
  reader.readAsArrayBuffer(file)
  input.value = ''
}

// MD file loaded (for PDF vs MD mode)
function onPdfMdLoaded(content: string) {
  pdfMdContent.value = content
}

// Base MD file loaded (for MD vs MD mode)
function onBaseMdLoaded(content: string, filename: string) {
  baseMd.value = content
  baseMdLabel.value = filename
}

// Compare MD file loaded (for MD vs MD mode)
function onCompareMdLoaded(content: string) {
  mdDiffPanel.value?.setCompareText(content)
}

// Load from history
async function onHistorySelect() {
  if (!selectedTaskId.value) return
  isLoadingTask.value = true
  loadError.value = null

  try {
    const result = await getParseResult(selectedTaskId.value)
    baseMd.value = result.markdown ?? ''
    baseMdLabel.value = result.filename ?? '이력에서 로드'
  } catch (e) {
    loadError.value = e instanceof Error ? e.message : '이력을 불러올 수 없습니다.'
  } finally {
    isLoadingTask.value = false
  }
}
</script>

<template>
  <div>
    <h1 class="page-title">비교 도구</h1>
    <p class="page-subtitle">PDF와 마크다운, 또는 마크다운끼리 비교합니다.</p>

    <BaseAlert v-if="loadError" variant="error" dismissible @dismiss="loadError = null">
      {{ loadError }}
    </BaseAlert>

    <div class="card">
      <CompareToolbar :mode="mode" @update:mode="mode = $event">
        <!-- History select for MD vs MD -->
        <div v-if="mode === 'md-vs-md'" class="compare-toolbar__history">
          <select
            v-model="selectedTaskId"
            aria-label="이력에서 기준 MD 선택"
            @change="onHistorySelect"
          >
            <option value="">이력에서 선택...</option>
            <option v-for="item in doneItems" :key="item.task_id" :value="item.task_id">
              {{ item.filename }}
            </option>
          </select>
        </div>
      </CompareToolbar>

      <!-- PDF vs MD mode -->
      <div v-if="mode === 'pdf-vs-md'" class="compare-layout">
        <div class="compare-layout__left">
          <div v-if="!pdfSource" class="compare-upload-area">
            <p class="text-muted text-sm">PDF 파일을 선택하세요</p>
            <input
              type="file"
              accept=".pdf"
              aria-label="PDF 파일 선택"
              @change="onPdfFileSelected"
            />
          </div>
          <PdfComparePanel v-else :pdf-source="pdfSource" />
        </div>
        <div class="compare-layout__right">
          <div v-if="!pdfMdContent" class="compare-upload-area">
            <MdUploader @file-loaded="onPdfMdLoaded" />
          </div>
          <div v-else class="editor-panel">
            <div class="editor-panel__header">
              <span class="editor-panel__title">마크다운</span>
            </div>
            <div class="editor-panel__body">
              <textarea
                class="md-textarea"
                :value="pdfMdContent"
                readonly
                spellcheck="false"
                wrap="off"
                aria-label="마크다운 내용"
              ></textarea>
            </div>
          </div>
        </div>
      </div>

      <!-- MD vs MD diff mode -->
      <div v-if="mode === 'md-vs-md'">
        <div v-if="!baseMd" class="compare-upload-area" style="padding: var(--space-4);">
          <p class="text-muted text-sm" style="margin-bottom: var(--space-2);">
            기준 마크다운을 이력에서 선택하거나 파일을 업로드하세요
          </p>
          <MdUploader @file-loaded="onBaseMdLoaded" />
        </div>

        <template v-else>
          <div style="padding: var(--space-2) var(--space-3);">
            <span class="text-muted text-sm">
              비교 MD를 파일로 업로드하거나 우측에 직접 입력하세요:
            </span>
            <MdUploader @file-loaded="(c) => onCompareMdLoaded(c)" />
          </div>
          <MdDiffPanel
            ref="mdDiffPanel"
            :base-markdown="baseMd"
            :base-label="baseMdLabel"
          />
        </template>
      </div>
    </div>
  </div>
</template>

<style scoped>
.compare-layout {
  display: grid;
  grid-template-columns: 1fr 1fr;
  min-height: calc(100vh - 300px);
}

.compare-layout__left,
.compare-layout__right {
  display: flex;
  flex-direction: column;
}

.compare-layout__left {
  border-right: 1px solid var(--color-border);
}

.compare-upload-area {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: var(--space-6);
  gap: var(--space-3);
}

.compare-toolbar__history select {
  padding: var(--space-1) var(--space-2);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  font-size: var(--font-size-sm);
}

.md-textarea {
  width: 100%;
  min-height: calc(100vh - 300px);
  border: none;
  padding: var(--space-3);
  font-family: var(--font-mono);
  font-size: var(--font-size-sm);
  line-height: 1.6;
  resize: none;
}
</style>
