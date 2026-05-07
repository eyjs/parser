<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { getParseResult, getExportUrl, getUploadUrl } from '@/api/client'
import { useParseTask } from '@/composables/useParseTask'
import type { ParseResult } from '@/api/types'
import PdfPanel from '@/components/verify/PdfPanel.vue'
import MarkdownPanel from '@/components/verify/MarkdownPanel.vue'
import VersionList from '@/components/verify/VersionList.vue'
import StatsGrid from '@/components/verify/StatsGrid.vue'
import BaseSpinner from '@/components/common/BaseSpinner.vue'
import BaseAlert from '@/components/common/BaseAlert.vue'
import BaseButton from '@/components/common/BaseButton.vue'

const route = useRoute()
const taskId = route.params.taskId as string

const isLoading = ref(true)
const error = ref<string | null>(null)
const result = ref<ParseResult | null>(null)
const pdfUrl = ref<string | null>(null)
const markdown = ref('')
const isLiveMode = ref(false)

// PDF panel reference for scroll sync
const pdfPanelRef = ref<InstanceType<typeof PdfPanel> | null>(null)

// Live mode SSE composable
const liveTask = useParseTask(taskId, {
  onDone() {
    isLiveMode.value = false
    // Reload full result
    loadResult()
  },
  onError(message) {
    isLiveMode.value = false
    error.value = message
  },
})

onMounted(() => {
  loadResult()
})

async function loadResult() {
  isLoading.value = true
  error.value = null

  try {
    const data = await getParseResult(taskId)
    result.value = data
    markdown.value = stripFrontMatter(data.markdown ?? '')

    if (data.pdf_path) {
      const parts = data.pdf_path.replace(/\\/g, '/').split('/')
      const taskIdx = parts.indexOf(taskId)
      if (taskIdx >= 0) {
        const relative = parts.slice(taskIdx).join('/')
        pdfUrl.value = getUploadUrl(relative)
      }
    }
  } catch (e) {
    // Check if it's a NOT_READY error — enter live mode
    const errMsg = e instanceof Error ? e.message : ''
    if (errMsg.includes('NOT_READY') || errMsg.includes('404') || errMsg.includes('not found')) {
      enterLiveMode()
    } else {
      error.value = errMsg || '결과를 불러올 수 없습니다.'
    }
  } finally {
    isLoading.value = false
  }
}

function enterLiveMode() {
  isLiveMode.value = true
  isLoading.value = false
  liveTask.connect()
}

function stripFrontMatter(md: string): string {
  if (!md.startsWith('---')) return md
  const endIdx = md.indexOf('\n---', 3)
  if (endIdx < 0) return md
  let cleaned = md.slice(endIdx + 4).replace(/^\n+/, '')
  cleaned = cleaned.replace(/\n---\n/g, '\n<!-- pagebreak -->\n')
  return cleaned
}

function onScrollToPage(page: number) {
  pdfPanelRef.value
  // Direct PDF scroll via goToPage is handled by the PdfPanel through events
  // In this simple version, we rely on the user scrolling manually in PDF
  void page
}
</script>

<template>
  <div>
    <!-- Loading state -->
    <div v-if="isLoading" class="loading-state">
      <BaseSpinner size="lg" />
      <p class="text-muted">결과를 불러오는 중...</p>
    </div>

    <!-- Error state -->
    <BaseAlert v-if="error" variant="error">
      {{ error }}
    </BaseAlert>

    <!-- Live mode indicator -->
    <div v-if="isLiveMode" class="live-badge" style="margin-bottom: var(--space-3);">
      <span class="live-badge__dot"></span>
      <span class="live-badge__text">
        실시간 수신 중... ({{ liveTask.completedPages.value }} 페이지)
      </span>
    </div>

    <!-- Header -->
    <div
      v-if="!isLoading"
      class="flex items-center gap-4"
      style="margin-bottom: var(--space-3);"
    >
      <h1 class="page-title" style="margin-bottom: 0;">
        {{ result?.filename ?? '파싱 중...' }} — 원본 비교
      </h1>
      <BaseButton
        v-if="result"
        variant="secondary"
        size="sm"
        :href="getExportUrl(taskId)"
      >
        다운로드
      </BaseButton>
    </div>

    <!-- Stats -->
    <StatsGrid
      v-if="result && result.stats"
      :stats="result.stats"
      :filename="result.filename"
      :completed-at="result.completed_at"
    />

    <!-- Compare editor layout -->
    <div
      v-if="!isLoading && (result || isLiveMode)"
      class="compare-editor"
    >
      <PdfPanel
        ref="pdfPanelRef"
        :pdf-url="pdfUrl"
      />
      <MarkdownPanel
        :task-id="taskId"
        :initial-markdown="markdown"
        :is-live-mode="isLiveMode"
        :live-page-markdowns="liveTask.pageMarkdowns.value"
        @scroll-to-page="onScrollToPage"
      />
    </div>

    <!-- Version list -->
    <VersionList
      v-if="result"
      :task-id="taskId"
      style="margin-top: var(--space-4);"
    />
  </div>
</template>

<style scoped>
.loading-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--space-3);
  padding: var(--space-8);
}

.compare-editor {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-3);
  min-height: calc(100vh - 300px);
}
</style>
