<script setup lang="ts">
import { ref, onMounted, onUnmounted, computed } from 'vue'
import { useRoute } from 'vue-router'
import {
  getParseResult,
  getExportUrl,
  getUploadUrl,
  getVersions,
  getDiff,
  saveMarkdown,
  ApiClientError,
} from '@/api/client'
import { toParseResultData, toVersionInfo, toVersionDiff } from '@/api/mappers'
import { useParseTask } from '@/composables/useParseTask'
import { useViewerStore } from '@/stores/viewer'
import { useHistoryStore } from '@/stores/history'
import { stripFrontMatter } from '@/utils/markdown'
import type { VersionInfo, VersionDiff } from '@/domain/types'
import PdfPanel from '@/components/verify/PdfPanel.vue'
import MarkdownPanel from '@/components/verify/MarkdownPanel.vue'
import VersionList from '@/components/verify/VersionList.vue'
import StatsGrid from '@/components/verify/StatsGrid.vue'
import BaseSpinner from '@/components/common/BaseSpinner.vue'
import BaseAlert from '@/components/common/BaseAlert.vue'
import BaseButton from '@/components/common/BaseButton.vue'

const route = useRoute()
const taskId = route.params.taskId as string
const viewerStore = useViewerStore()
const historyStore = useHistoryStore()

const error = ref<string | null>(null)
const resultStats = ref<Record<string, unknown> | null>(null)
const resultMetadata = ref<Record<string, unknown> | null>(null)
const resultFilename = ref('')
const resultCompletedAt = ref('')
const isLiveMode = ref(false)
const isSaving = ref(false)

// Version state
const versions = ref<VersionInfo[]>([])
const versionsLoading = ref(false)
const versionDiff = ref<VersionDiff | null>(null)

const pdfPanelRef = ref<InstanceType<typeof PdfPanel> | null>(null)

let liveAttempted = false

const liveTask = useParseTask(taskId, {
  onDone() {
    isLiveMode.value = false
    loadResult()
    historyStore.fetchHistory()
  },
  onError(message) {
    isLiveMode.value = false
    error.value = message
  },
})

const exportUrl = computed(() => getExportUrl(taskId))

onMounted(() => {
  viewerStore.setLoading(true)
  loadResult()
})

onUnmounted(() => {
  liveTask.disconnect()
})

async function loadResult() {
  error.value = null

  try {
    const data = toParseResultData(await getParseResult(taskId))
    resultStats.value = data.stats
    resultMetadata.value = data.metadata
    resultFilename.value = data.filename
    resultCompletedAt.value = data.completedAt

    let pdfUrl: string | null = null
    if (data.pdfPath) {
      const parts = data.pdfPath.replace(/\\/g, '/').split('/')
      const taskIdx = parts.indexOf(taskId)
      if (taskIdx >= 0) {
        const relative = parts.slice(taskIdx).join('/')
        pdfUrl = getUploadUrl(relative)
      }
    }

    const md = stripFrontMatter(data.markdown ?? '')
    viewerStore.openDocument(taskId, { pdfUrl, markdown: md })
    loadVersions()
  } catch (e) {
    const isNotReady = e instanceof ApiClientError && (e.status === 404 || e.code === 'NOT_READY')
    if (isNotReady && !liveAttempted) {
      enterLiveMode()
    } else if (isNotReady) {
      error.value = '결과가 아직 준비되지 않았습니다. 페이지를 새로고침하세요.'
    } else {
      error.value = e instanceof Error ? e.message : '결과를 불러올 수 없습니다.'
    }
    viewerStore.setLoading(false)
  }
}

async function loadVersions() {
  versionsLoading.value = true
  try {
    versions.value = (await getVersions(taskId)).map(toVersionInfo)
  } catch {
    // Version list is non-critical; leave empty
  } finally {
    versionsLoading.value = false
  }
}

async function onVersionCompare(v1: string, v2: string) {
  try {
    versionDiff.value = toVersionDiff(await getDiff(taskId, v1, v2))
  } catch (e) {
    error.value = e instanceof Error ? e.message : '버전 비교에 실패했습니다.'
  }
}

async function onSaveMarkdown(md: string) {
  isSaving.value = true
  viewerStore.setSaving(true)
  try {
    await saveMarkdown(taskId, md)
    viewerStore.markSaved()
  } catch (e) {
    const msg = e instanceof Error ? e.message : '저장에 실패했습니다.'
    viewerStore.setSaveError(msg)
  } finally {
    isSaving.value = false
  }
}

function enterLiveMode() {
  if (liveAttempted) return
  liveAttempted = true
  isLiveMode.value = true
  viewerStore.setLoading(false)
  liveTask.connect()
}

function onScrollToPage(page: number) {
  pdfPanelRef.value?.goToPage(page)
}
</script>

<template>
  <div class="viewer-view">
    <!-- Toolbar -->
    <div class="viewer-toolbar">
      <div class="viewer-toolbar__left">
        <span class="viewer-toolbar__filename">
          {{ resultFilename || '파싱 중...' }}
        </span>
        <span v-if="isLiveMode" class="viewer-toolbar__live">
          <span class="live-dot"></span>
          실시간 수신 중
        </span>
      </div>
      <div class="viewer-toolbar__right">
        <BaseButton
          v-if="viewerStore.isOpen"
          variant="secondary"
          size="sm"
          :href="exportUrl"
          download
        >
          다운로드
        </BaseButton>
      </div>
    </div>

    <!-- Loading state -->
    <div v-if="viewerStore.isLoading" class="loading-state">
      <BaseSpinner size="lg" />
      <p class="text-muted">결과를 불러오는 중...</p>
    </div>

    <!-- Error state -->
    <BaseAlert v-if="error" variant="error">
      {{ error }}
    </BaseAlert>

    <!-- Stats -->
    <StatsGrid
      v-if="resultStats"
      :stats="resultStats"
      :metadata="resultMetadata"
      :filename="resultFilename"
      :completed-at="resultCompletedAt"
    />

    <!-- Split panel editor -->
    <div
      v-if="!viewerStore.isLoading && (viewerStore.isOpen || isLiveMode)"
      class="viewer-layout"
    >
      <PdfPanel
        ref="pdfPanelRef"
        :pdf-url="viewerStore.pdfUrl"
      />
      <MarkdownPanel
        :initial-markdown="viewerStore.markdown"
        :is-live-mode="isLiveMode"
        :live-page-markdowns="liveTask.pageMarkdowns.value"
        :save-fn="onSaveMarkdown"
        @scroll-to-page="onScrollToPage"
      />
    </div>

    <!-- Version list -->
    <VersionList
      v-if="viewerStore.isOpen"
      :versions="versions"
      :is-loading="versionsLoading"
      :diff-result="versionDiff"
      style="margin-top: var(--space-4);"
      @compare="onVersionCompare"
    />
  </div>
</template>

<style scoped>
.viewer-view {
  display: flex;
  flex-direction: column;
  height: calc(100vh - var(--space-8));
}

.viewer-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: var(--viewer-toolbar-height);
  padding: 0 var(--space-3);
  border-bottom: 1px solid var(--viewer-divider);
  background: var(--color-surface);
  border-radius: var(--radius-md) var(--radius-md) 0 0;
  flex-shrink: 0;
}

.viewer-toolbar__left {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

.viewer-toolbar__filename {
  font-weight: var(--font-weight-medium);
  font-size: var(--font-size-sm);
  color: var(--color-text);
}

.viewer-toolbar__live {
  display: flex;
  align-items: center;
  gap: var(--space-1);
  font-size: var(--font-size-xs);
  color: var(--color-primary);
}

.live-dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: var(--radius-full);
  background: var(--color-primary);
  animation: pulse 1.5s ease-in-out infinite;
}

.viewer-toolbar__right {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.viewer-layout {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-3);
  flex: 1;
  min-height: 0;
}

.loading-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--space-3);
  padding: var(--space-8);
  flex: 1;
}

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.4; }
}
</style>
