<script setup lang="ts">
import { ref, computed, watch, shallowRef, onMounted, onUnmounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { uploadFiles, getParseResult, getExportUrl, getTaskExportUrl, getUploadUrl, getVersions, getDiff, saveMarkdown, ApiClientError } from '@/api/client'
import { toParseResultData, toVersionInfo, toVersionDiff } from '@/api/mappers'
import { useParseTask } from '@/composables/useParseTask'
import { useTaskStore } from '@/stores/task'
import { useHistoryStore } from '@/stores/history'
import { useViewerStore } from '@/stores/viewer'
import { stripFrontMatter } from '@/utils/markdown'
import type { VersionInfo, VersionDiff } from '@/domain/types'
import DropZone from '@/components/dashboard/DropZone.vue'
import QueueBanner from '@/components/dashboard/QueueBanner.vue'
import LivePreview from '@/components/dashboard/LivePreview.vue'
import PdfPanel from '@/components/verify/PdfPanel.vue'
import MarkdownPanel from '@/components/verify/MarkdownPanel.vue'
import VersionList from '@/components/verify/VersionList.vue'
import StatsGrid from '@/components/verify/StatsGrid.vue'
import BaseSpinner from '@/components/common/BaseSpinner.vue'
import BaseAlert from '@/components/common/BaseAlert.vue'
// BaseButton available if needed

type MainState = 'idle' | 'uploading' | 'viewing'

const router = useRouter()
const route = useRoute()
const taskStore = useTaskStore()
const historyStore = useHistoryStore()
const viewerStore = useViewerStore()

// --- State machine ---
const state = ref<MainState>('idle')

// --- Viewing state ---
const error = ref<string | null>(null)
const resultStats = ref<Record<string, unknown> | null>(null)
const resultMetadata = ref<Record<string, unknown> | null>(null)
const resultFilename = ref('')
const resultCompletedAt = ref('')
const isLiveMode = ref(false)
const metaSidebarOpen = ref(false)

const versions = ref<VersionInfo[]>([])
const versionsLoading = ref(false)
const versionDiff = ref<VersionDiff | null>(null)

const pdfPanelRef = ref<InstanceType<typeof PdfPanel> | null>(null)

let liveAttempted = false

// --- Export state ---
const exportDropdownOpen = ref(false)

// --- Upload state ---
const uploadError = ref<string | null>(null)
const lastUploadedTaskIds = ref<string[]>([])

// --- Computed ---
const taskIdFromQuery = computed(() => {
  const q = route.query.task
  return typeof q === 'string' && q ? q : null
})

const primaryTaskId = computed(() => {
  const ids = lastUploadedTaskIds.value
  return ids.length > 0 ? ids[ids.length - 1] : null
})

const primaryTask = computed(() => {
  if (!primaryTaskId.value) return null
  return taskStore.getTask(primaryTaskId.value) ?? null
})

const exportUrl = computed(() => {
  const tid = taskIdFromQuery.value
  return tid ? getExportUrl(tid) : ''
})

// --- Live task for viewing ---
function createLiveTask(id: string) {
  return useParseTask(id, {
    onDone() {
      isLiveMode.value = false
      loadResult(id)
      historyStore.fetchHistory()
    },
    onError(message) {
      isLiveMode.value = false
      error.value = message
    },
  })
}

const liveTask = shallowRef(createLiveTask(taskIdFromQuery.value || '__noop__'))

// --- State determination ---
function determineState(): MainState {
  if (taskIdFromQuery.value) return 'viewing'

  const pt = primaryTask.value
  if (pt && pt.status !== 'done' && pt.status !== 'error') return 'uploading'

  return 'idle'
}

// Watch query param changes
watch(taskIdFromQuery, (newId, oldId) => {
  if (newId && newId !== oldId) {
    enterViewing(newId)
  } else if (!newId) {
    state.value = determineState()
    if (state.value === 'idle') {
      resetViewingState()
    }
  }
}, { immediate: false })

// Watch active task completion for auto-transition
watch(
  () => primaryTask.value?.status,
  (newStatus) => {
    if (newStatus === 'done' && state.value === 'uploading' && primaryTaskId.value) {
      const tid = primaryTaskId.value
      setTimeout(() => {
        router.replace({ query: { task: tid } })
      }, 1200)
    }
  },
)

// Watch active task for uploading state
watch(primaryTask, () => {
  if (state.value === 'idle' && primaryTask.value && primaryTask.value.status !== 'done' && primaryTask.value.status !== 'error') {
    state.value = 'uploading'
  }
})

// --- Lifecycle ---
onMounted(() => {
  const tid = taskIdFromQuery.value
  if (tid) {
    enterViewing(tid)
  } else {
    state.value = 'idle'
  }
  document.addEventListener('click', onDocumentClick, true)
})

onUnmounted(() => {
  liveTask.value.disconnect()
  document.removeEventListener('click', onDocumentClick, true)
})

// --- State transitions ---
function resetViewingState() {
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
}

function enterViewing(taskId: string) {
  resetViewingState()
  state.value = 'viewing'
  liveTask.value = createLiveTask(taskId)
  viewerStore.setLoading(true)
  loadResult(taskId)
}

async function loadResult(taskId: string) {
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
    loadVersions(taskId)
  } catch (e) {
    const isNotReady = e instanceof ApiClientError && (e.status === 404 || e.code === 'NOT_READY')
    if (isNotReady && !liveAttempted) {
      enterLiveMode()
    } else if (isNotReady) {
      error.value = '결과가 아직 준비되지 않았습니다. 잠시 후 다시 시도해주세요.'
    } else {
      error.value = e instanceof Error ? e.message : '결과를 불러올 수 없습니다.'
    }
    viewerStore.setLoading(false)
  }
}

async function loadVersions(taskId: string) {
  versionsLoading.value = true
  try {
    versions.value = (await getVersions(taskId)).map(toVersionInfo)
  } catch {
    // Non-critical
  } finally {
    versionsLoading.value = false
  }
}

async function onVersionCompare(v1: string, v2: string) {
  const tid = taskIdFromQuery.value
  if (!tid) return
  try {
    versionDiff.value = toVersionDiff(await getDiff(tid, v1, v2))
  } catch (e) {
    error.value = e instanceof Error ? e.message : '버전 비교에 실패했습니다.'
  }
}

async function onSaveMarkdown(md: string) {
  const tid = taskIdFromQuery.value
  if (!tid) return
  viewerStore.setSaving(true)
  try {
    await saveMarkdown(tid, md)
    viewerStore.markSaved()
  } catch (e) {
    const msg = e instanceof Error ? e.message : '저장에 실패했습니다.'
    viewerStore.setSaveError(msg)
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

// --- Export handling ---
function doExport(format: 'inline' | 'zip') {
  const tid = taskIdFromQuery.value
  if (!tid) return
  exportDropdownOpen.value = false
  window.open(getTaskExportUrl(tid, format), '_blank')
}

function onDocumentClick(e: MouseEvent) {
  if (!exportDropdownOpen.value) return
  const wrapper = (e.target as HTMLElement).closest('.export-dropdown-wrapper')
  if (!wrapper) {
    exportDropdownOpen.value = false
  }
}

// --- Upload handling ---
async function onFilesSelected(files: File[]) {
  uploadError.value = null
  try {
    const response = await uploadFiles(files)
    const taskIds = response.task_ids ?? [response.task_id]
    lastUploadedTaskIds.value = taskIds

    for (let i = 0; i < taskIds.length; i++) {
      const filename = files[i]?.name ?? `file-${i + 1}.pdf`
      taskStore.trackTask(taskIds[i], filename)
    }

    historyStore.fetchHistory()
    state.value = 'uploading'
  } catch (e) {
    uploadError.value = e instanceof Error ? e.message : '업로드 중 오류가 발생했습니다.'
  }
}
</script>

<template>
  <div class="main-area" role="main" aria-label="메인 콘텐츠">
    <!-- IDLE state -->
    <div v-if="state === 'idle'" class="main-area__idle">
      <div class="main-area__welcome">
        <h1 class="page-title">PDF -> 마크다운 변환</h1>
        <p class="page-subtitle">PDF 파일을 업로드하면 자동으로 마크다운으로 변환합니다.</p>
      </div>

      <section class="card main-area__upload-card" aria-labelledby="upload-heading">
        <div class="card__body">
          <h2 id="upload-heading" class="section-title">파일 업로드</h2>
          <DropZone @files-selected="onFilesSelected" />
          <QueueBanner />

          <BaseAlert
            v-if="uploadError"
            variant="error"
            dismissible
            @dismiss="uploadError = null"
          >
            {{ uploadError }}
          </BaseAlert>
        </div>
      </section>
    </div>

    <!-- UPLOADING state -->
    <div v-else-if="state === 'uploading'" class="main-area__uploading">
      <h2 class="main-area__uploading-title">변환 중...</h2>

      <LivePreview
        v-if="primaryTask"
        :total-pages="primaryTask.totalPages"
        :completed-pages="primaryTask.completedPages"
        :current-stage="primaryTask.currentStage"
        :page-markdowns="primaryTask.pageMarkdowns"
      />

      <BaseAlert
        v-if="uploadError"
        variant="error"
        dismissible
        @dismiss="uploadError = null"
      >
        {{ uploadError }}
      </BaseAlert>
    </div>

    <!-- VIEWING state -->
    <div v-else-if="state === 'viewing'" class="main-area__viewing">
      <!-- Toolbar -->
      <div class="main-area__toolbar">
        <div class="main-area__toolbar-left">
          <span class="main-area__filename">
            {{ resultFilename || '파싱 중...' }}
          </span>
          <span v-if="isLiveMode" class="main-area__live">
            <span class="live-dot"></span>
            실시간 수신 중
          </span>
        </div>
        <div class="main-area__toolbar-right">
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
          <div v-if="viewerStore.isOpen" class="export-dropdown-wrapper">
            <button
              class="btn btn--secondary btn--sm"
              @click="exportDropdownOpen = !exportDropdownOpen"
            >
              내보내기 &#x25BE;
            </button>
            <div v-if="exportDropdownOpen" class="export-dropdown">
              <button class="export-dropdown__item" @click="doExport('inline')">
                마크다운 (.md)
              </button>
              <button class="export-dropdown__item" @click="doExport('zip')">
                ZIP 묶음 (.zip)
              </button>
            </div>
          </div>
        </div>
      </div>

      <!-- Loading -->
      <div v-if="viewerStore.isLoading" class="main-area__loading">
        <BaseSpinner size="lg" />
        <p class="text-muted">결과를 불러오는 중...</p>
      </div>

      <!-- Error -->
      <BaseAlert v-if="error" variant="error">
        {{ error }}
      </BaseAlert>

      <!-- Panels -->
      <div
        v-if="!viewerStore.isLoading && (viewerStore.isOpen || isLiveMode)"
        class="main-area__content"
      >
        <div class="main-area__panels">
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

        <!-- Meta sidebar -->
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
  </div>
</template>

<style scoped>
.main-area {
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow: hidden;
}

/* ---- idle state ---- */
.main-area__idle {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  flex: 1;
  padding: var(--space-6);
  gap: var(--space-6);
}

.main-area__welcome {
  text-align: center;
}

.main-area__upload-card {
  width: 100%;
  max-width: 640px;
}

/* ---- uploading state ---- */
.main-area__uploading {
  flex: 1;
  padding: var(--space-6);
  overflow-y: auto;
}

.main-area__uploading-title {
  font-size: var(--font-size-xl);
  font-weight: var(--font-weight-bold);
  color: var(--color-text);
  margin-bottom: var(--space-4);
}

/* ---- viewing state ---- */
.main-area__viewing {
  display: flex;
  flex-direction: column;
  flex: 1;
  overflow: hidden;
}

.main-area__toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: var(--viewer-toolbar-height);
  padding: 0 var(--space-3);
  border-bottom: 1px solid var(--viewer-divider);
  background: var(--color-surface);
  flex-shrink: 0;
}

.main-area__toolbar-left {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

.main-area__filename {
  font-weight: var(--font-weight-medium);
  font-size: var(--font-size-sm);
  color: var(--color-text);
}

.main-area__live {
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

.main-area__toolbar-right {
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

.main-area__loading {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--space-3);
  padding: var(--space-8);
  flex: 1;
}

.main-area__content {
  display: flex;
  flex: 1;
  min-height: 0;
  gap: 0;
}

.main-area__panels {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-3);
  flex: 1;
  min-width: 0;
}

/* ---- meta sidebar ---- */
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

/* Slide transition */
.meta-slide-enter-active,
.meta-slide-leave-active {
  transition: width var(--transition-normal), opacity var(--transition-normal);
}

.meta-slide-enter-from,
.meta-slide-leave-to {
  width: 0;
  opacity: 0;
}

/* ---- export dropdown ---- */
.export-dropdown-wrapper {
  position: relative;
}

.export-dropdown {
  position: absolute;
  top: 100%;
  right: 0;
  margin-top: var(--space-1);
  min-width: 160px;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-md);
  z-index: 10;
  overflow: hidden;
}

.export-dropdown__item {
  display: block;
  width: 100%;
  padding: var(--space-2) var(--space-3);
  border: none;
  background: transparent;
  text-align: left;
  font-size: var(--font-size-sm);
  color: var(--color-text);
  cursor: pointer;
  transition: background var(--transition-fast);
}

.export-dropdown__item:hover {
  background: var(--color-surface-alt);
}

.export-dropdown__item + .export-dropdown__item {
  border-top: 1px solid var(--color-border);
}

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.4; }
}
</style>
