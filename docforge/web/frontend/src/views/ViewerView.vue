<script setup lang="ts">
import { ref, onMounted, onUnmounted, computed, watch, shallowRef } from 'vue'
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
const taskId = computed(() => route.params.taskId as string)
const viewerStore = useViewerStore()
const historyStore = useHistoryStore()

const error = ref<string | null>(null)
const resultStats = ref<Record<string, unknown> | null>(null)
const resultMetadata = ref<Record<string, unknown> | null>(null)
const resultFilename = ref('')
const resultCompletedAt = ref('')
const isLiveMode = ref(false)
const isSaving = ref(false)
const metaSidebarOpen = ref(false)

const versions = ref<VersionInfo[]>([])
const versionsLoading = ref(false)
const versionDiff = ref<VersionDiff | null>(null)

const pdfPanelRef = ref<InstanceType<typeof PdfPanel> | null>(null)

let liveAttempted = false

function createLiveTask(id: string) {
  return useParseTask(id, {
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
}

const liveTask = shallowRef(createLiveTask(taskId.value))

const exportUrl = computed(() => getExportUrl(taskId.value))

onMounted(() => {
  viewerStore.setLoading(true)
  loadResult()
})

watch(taskId, (newId) => {
  liveTask.value.disconnect()
  liveAttempted = false
  isLiveMode.value = false
  error.value = null
  resultStats.value = null
  resultMetadata.value = null
  resultFilename.value = ''
  resultCompletedAt.value = ''
  versions.value = []
  versionDiff.value = null
  viewerStore.close()

  liveTask.value = createLiveTask(newId)
  viewerStore.setLoading(true)
  loadResult()
})

onUnmounted(() => {
  liveTask.value.disconnect()
})

async function loadResult() {
  error.value = null

  try {
    const data = toParseResultData(await getParseResult(taskId.value))
    resultStats.value = data.stats
    resultMetadata.value = data.metadata
    resultFilename.value = data.filename
    resultCompletedAt.value = data.completedAt

    let pdfUrl: string | null = null
    if (data.pdfPath) {
      const parts = data.pdfPath.replace(/\\/g, '/').split('/')
      const taskIdx = parts.indexOf(taskId.value)
      if (taskIdx >= 0) {
        const relative = parts.slice(taskIdx).join('/')
        pdfUrl = getUploadUrl(relative)
      }
    }

    const md = stripFrontMatter(data.markdown ?? '')
    viewerStore.openDocument(taskId.value, { pdfUrl, markdown: md })
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
    versions.value = (await getVersions(taskId.value)).map(toVersionInfo)
  } catch {
    // Version list is non-critical; leave empty
  } finally {
    versionsLoading.value = false
  }
}

async function onVersionCompare(v1: string, v2: string) {
  try {
    versionDiff.value = toVersionDiff(await getDiff(taskId.value, v1, v2))
  } catch (e) {
    error.value = e instanceof Error ? e.message : '버전 비교에 실패했습니다.'
  }
}

async function onSaveMarkdown(md: string) {
  isSaving.value = true
  viewerStore.setSaving(true)
  try {
    await saveMarkdown(taskId.value, md)
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
  liveTask.value.connect()
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
        <button
          v-if="viewerStore.isOpen && resultStats"
          class="toolbar-btn"
          :class="{ 'toolbar-btn--active': metaSidebarOpen }"
          title="문서 정보"
          @click="metaSidebarOpen = !metaSidebarOpen"
        >
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
            <circle cx="8" cy="8" r="7" stroke="currentColor" stroke-width="1.5"/>
            <path d="M8 7v4M8 5h.01" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
          </svg>
          <span class="toolbar-btn__label">정보</span>
        </button>
        <BaseButton
          v-if="viewerStore.isOpen"
          variant="secondary"
          size="sm"
          :href="exportUrl"
          :download="resultFilename ? resultFilename.replace(/\.pdf$/i, '.md') : 'download.md'"
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

    <!-- Main content area -->
    <div
      v-if="!viewerStore.isLoading && (viewerStore.isOpen || isLiveMode)"
      class="viewer-content"
    >
      <!-- Split panel editor — primary focus -->
      <div class="viewer-layout">
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

      <!-- Collapsible right sidebar for metadata -->
      <transition name="meta-slide">
        <div v-if="metaSidebarOpen" class="meta-sidebar">
          <div class="meta-sidebar__header">
            <h3 class="meta-sidebar__title">문서 정보</h3>
            <button
              class="meta-sidebar__close"
              title="닫기"
              @click="metaSidebarOpen = false"
            >
              &#x2715;
            </button>
          </div>
          <div class="meta-sidebar__body">
            <StatsGrid
              v-if="resultStats"
              :stats="resultStats"
              :metadata="resultMetadata ?? undefined"
              :filename="resultFilename"
              :completed-at="resultCompletedAt"
            />
            <VersionList
              v-if="viewerStore.isOpen"
              :versions="versions"
              :is-loading="versionsLoading"
              :diff-result="versionDiff"
              @compare="onVersionCompare"
            />
          </div>
        </div>
      </transition>
    </div>
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

.toolbar-btn {
  display: flex;
  align-items: center;
  gap: var(--space-1);
  padding: var(--space-1) var(--space-2);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--color-text-muted);
  font-size: var(--font-size-xs);
  cursor: pointer;
  transition: all var(--transition-fast);
}

.toolbar-btn:hover {
  background: var(--color-surface-alt);
  color: var(--color-text);
}

.toolbar-btn--active {
  background: var(--color-primary-bg);
  color: var(--color-primary);
  border-color: var(--color-primary);
}

.toolbar-btn__label {
  line-height: 1;
}

/* Main content: split panels + optional meta sidebar */
.viewer-content {
  display: flex;
  flex: 1;
  min-height: 0;
  gap: 0;
}

.viewer-layout {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-3);
  flex: 1;
  min-width: 0;
}

/* Right metadata sidebar */
.meta-sidebar {
  width: var(--meta-sidebar-width);
  flex-shrink: 0;
  border-left: 1px solid var(--viewer-divider);
  background: var(--color-surface);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.meta-sidebar__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--space-2) var(--space-3);
  border-bottom: 1px solid var(--color-border);
  flex-shrink: 0;
}

.meta-sidebar__title {
  font-size: var(--font-size-sm);
  font-weight: var(--font-weight-semibold);
  color: var(--color-text);
  margin: 0;
}

.meta-sidebar__close {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border: none;
  background: transparent;
  color: var(--color-text-muted);
  cursor: pointer;
  border-radius: var(--radius-sm);
  font-size: var(--font-size-sm);
}

.meta-sidebar__close:hover {
  background: var(--color-surface-alt);
  color: var(--color-text);
}

.meta-sidebar__body {
  flex: 1;
  overflow-y: auto;
  padding: var(--space-3);
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.meta-sidebar__body::-webkit-scrollbar {
  width: 6px;
}

.meta-sidebar__body::-webkit-scrollbar-thumb {
  background: var(--color-border);
  border-radius: var(--radius-full);
}

/* Slide transition for meta sidebar */
.meta-slide-enter-active,
.meta-slide-leave-active {
  transition: width var(--transition-normal), opacity var(--transition-normal);
}

.meta-slide-enter-from,
.meta-slide-leave-to {
  width: 0;
  opacity: 0;
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
