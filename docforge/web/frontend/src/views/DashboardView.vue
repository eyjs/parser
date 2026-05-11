<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { useRouter } from 'vue-router'
import { uploadFiles } from '@/api/client'
import { useTaskStore } from '@/stores/task'
import { useHistoryStore } from '@/stores/history'
import DropZone from '@/components/dashboard/DropZone.vue'
import QueueBanner from '@/components/dashboard/QueueBanner.vue'
import LivePreview from '@/components/dashboard/LivePreview.vue'
import BaseAlert from '@/components/common/BaseAlert.vue'

const router = useRouter()
const taskStore = useTaskStore()
const historyStore = useHistoryStore()

const uploadError = ref<string | null>(null)
const lastUploadedTaskIds = ref<string[]>([])

const primaryTaskId = computed(() => {
  const ids = lastUploadedTaskIds.value
  return ids.length > 0 ? ids[ids.length - 1] : null
})

const primaryTask = computed(() => {
  if (!primaryTaskId.value) return null
  return taskStore.getTask(primaryTaskId.value) ?? null
})

const showLivePreview = computed(() => {
  const task = primaryTask.value
  if (!task) return false
  return task.status !== 'done' && task.status !== 'error'
})

watch(
  () => primaryTask.value?.status,
  (newStatus) => {
    if (newStatus === 'done' && lastUploadedTaskIds.value.length === 1) {
      const taskId = lastUploadedTaskIds.value[0]
      setTimeout(() => {
        router.push(`/viewer/${taskId}`)
      }, 1200)
    }
  },
)

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
  } catch (e) {
    uploadError.value = e instanceof Error ? e.message : '업로드 중 오류가 발생했습니다.'
  }
}
</script>

<template>
  <div>
    <div class="flex items-center gap-4" style="margin-bottom: var(--space-2);">
      <h1 class="page-title" style="margin-bottom: 0;">PDF -> 마크다운 변환</h1>
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
      :total-pages="primaryTask.totalPages"
      :completed-pages="primaryTask.completedPages"
      :current-stage="primaryTask.currentStage"
      :page-markdowns="primaryTask.pageMarkdowns"
    />
  </div>
</template>
