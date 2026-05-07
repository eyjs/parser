<script setup lang="ts">
import { ref, shallowRef, computed, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { uploadFiles } from '@/api/client'
import { useParseTask } from '@/composables/useParseTask'
import { useParseStore } from '@/stores/parse'
import { useHistoryStore } from '@/stores/history'
import DropZone from '@/components/dashboard/DropZone.vue'
import QueueBanner from '@/components/dashboard/QueueBanner.vue'
import LivePreview from '@/components/dashboard/LivePreview.vue'
import HistoryTable from '@/components/dashboard/HistoryTable.vue'
import BaseAlert from '@/components/common/BaseAlert.vue'

type ParseTaskReturn = ReturnType<typeof useParseTask>

const router = useRouter()
const parseStore = useParseStore()
const historyStore = useHistoryStore()

const uploadError = ref<string | null>(null)
const activeParseTasks = shallowRef<Map<string, ParseTaskReturn>>(new Map())

onUnmounted(() => {
  for (const task of activeParseTasks.value.values()) {
    task.disconnect()
  }
})

// Most recent active task for live preview display
const primaryTask = computed<ParseTaskReturn | null>(() => {
  const tasks = Array.from(activeParseTasks.value.values())
  return tasks.length > 0 ? tasks[tasks.length - 1] : null
})

const showLivePreview = computed(() => {
  const task = primaryTask.value
  if (!task) return false
  return task.status.value !== 'done' && task.status.value !== 'error'
})

async function onFilesSelected(files: File[]) {
  uploadError.value = null

  try {
    const response = await uploadFiles(files)
    const taskIds = response.task_ids ?? [response.task_id]

    for (let i = 0; i < taskIds.length; i++) {
      const taskId = taskIds[i]
      const filename = files[i]?.name ?? `file-${i + 1}.pdf`

      parseStore.addTask(taskId, filename)

      const taskState = useParseTask(taskId, {
        onPageResult(page, markdown) {
          parseStore.setPageMarkdown(taskId, page, markdown)
        },
        onDone() {
          parseStore.updateTask(taskId, { status: 'done', pct: 100 })
          historyStore.fetchHistory()

          // Single file upload: navigate to verify page
          if (taskIds.length === 1) {
            setTimeout(() => {
              router.push(`/verify/${taskId}`)
            }, 1200)
          }
        },
        onError(message) {
          parseStore.updateTask(taskId, { status: 'error', error: message })
          historyStore.fetchHistory()
        },
        onStageChange(stage) {
          parseStore.updateTask(taskId, { currentStage: stage })
        },
      })

      const next = new Map(activeParseTasks.value)
      next.set(taskId, taskState)
      activeParseTasks.value = next
      taskState.connect()
    }
  } catch (e) {
    uploadError.value = e instanceof Error ? e.message : '업로드 중 오류가 발생했습니다.'
  }
}
</script>

<template>
  <div>
    <div class="flex items-center gap-4" style="margin-bottom: var(--space-2);">
      <h1 class="page-title" style="margin-bottom: 0;">PDF → 마크다운 변환</h1>
    </div>
    <p class="page-subtitle">PDF 파일을 업로드하면 자동으로 마크다운으로 변환합니다.</p>

    <!-- Upload section -->
    <section class="card mb-6" aria-labelledby="upload-heading">
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

    <!-- Live preview -->
    <LivePreview
      v-if="showLivePreview && primaryTask"
      :total-pages="primaryTask.totalPages.value"
      :completed-pages="primaryTask.completedPages.value"
      :current-stage="primaryTask.currentStage.value"
      :page-markdowns="primaryTask.pageMarkdowns.value"
    />

    <!-- History -->
    <HistoryTable />
  </div>
</template>
