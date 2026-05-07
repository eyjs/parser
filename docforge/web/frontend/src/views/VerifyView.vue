<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { getParseResult, getExportUrl, getUploadUrl, ApiClientError } from '@/api/client'
import { useParseTask } from '@/composables/useParseTask'
import { stripFrontMatter } from '@/utils/markdown'
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

const pdfPanelRef = ref<InstanceType<typeof PdfPanel> | null>(null)

let liveAttempted = false

const liveTask = useParseTask(taskId, {
  onDone() {
    isLiveMode.value = false
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
    const isNotReady = e instanceof ApiClientError && (e.status === 404 || e.code === 'NOT_READY')
    if (isNotReady && !liveAttempted) {
      enterLiveMode()
    } else if (isNotReady) {
      error.value = '결과가 아직 준비되지 않았습니다. 페이지를 새로고침하세요.'
    } else {
      error.value = e instanceof Error ? e.message : '결과를 불러올 수 없습니다.'
    }
  } finally {
    isLoading.value = false
  }
}

function enterLiveMode() {
  if (liveAttempted) return
  liveAttempted = true
  isLiveMode.value = true
  isLoading.value = false
  liveTask.connect()
}

function onScrollToPage(page: number) {
  pdfPanelRef.value?.goToPage(page)
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
