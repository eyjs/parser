<script setup lang="ts">
import { ref, computed } from 'vue'
import { useRouter } from 'vue-router'
import { storeToRefs } from 'pinia'
import { useHistoryStore } from '@/stores/history'
import { useTaskStore } from '@/stores/task'
import { uploadFiles } from '@/api/client'
import SidebarTaskCard from './SidebarTaskCard.vue'
import type { HistoryEntry } from '@/domain/types'

const router = useRouter()
const historyStore = useHistoryStore()
const taskStore = useTaskStore()

const { items, isEmpty, isLoading } = storeToRefs(historyStore)
const { activeTaskList } = storeToRefs(taskStore)

const fileInput = ref<HTMLInputElement | null>(null)

const currentTaskId = computed(() => {
  const route = router.currentRoute.value
  if (route.name === 'viewer') {
    return route.params.taskId as string
  }
  return null
})

// Merge active tasks (running/queued, not yet in history) with history items
const allTasks = computed<HistoryEntry[]>(() => {
  const historyIds = new Set(items.value.map((i) => i.taskId))
  const activeTasks: HistoryEntry[] = activeTaskList.value
    .filter((t) => !historyIds.has(t.taskId))
    .map((t) => ({
      taskId: t.taskId,
      filename: t.filename,
      status: t.status,
      progress: '',
      progressPct: t.pct,
      createdAt: new Date(t.startedAt).toISOString(),
      completedAt: '',
      error: t.error ?? '',
      totalPages: t.totalPages,
    }))
  return [...activeTasks, ...items.value]
})

function onCardClick(task: HistoryEntry) {
  if (task.status === 'done') {
    router.push(`/viewer/${task.taskId}`)
  }
}

function triggerUpload() {
  fileInput.value?.click()
}

async function onFileChange(event: Event) {
  const input = event.target as HTMLInputElement
  const files = input.files
  if (!files || files.length === 0) return

  try {
    const response = await uploadFiles(Array.from(files))
    const taskIds = response.task_ids ?? [response.task_id]
    for (let i = 0; i < taskIds.length; i++) {
      const filename = files[i]?.name ?? `file-${i + 1}.pdf`
      taskStore.addTask(taskIds[i], filename)
    }
    historyStore.fetchHistory()
  } catch {
    // Upload errors are handled in DashboardView
  }

  // Reset input so same file can be re-selected
  input.value = ''
}
</script>

<template>
  <aside class="app-sidebar" role="navigation" aria-label="작업 목록">
    <div class="app-sidebar__header">
      <router-link to="/" class="app-sidebar__logo">
        DocForge
      </router-link>
      <button
        class="btn btn--primary app-sidebar__upload-btn"
        @click="triggerUpload"
      >
        PDF 업로드
      </button>
      <input
        ref="fileInput"
        type="file"
        accept=".pdf"
        multiple
        class="visually-hidden"
        @change="onFileChange"
      />
    </div>

    <div class="app-sidebar__list">
      <div v-if="isLoading && isEmpty" class="app-sidebar__empty">
        불러오는 중...
      </div>
      <div v-else-if="allTasks.length === 0" class="app-sidebar__empty">
        변환 이력이 없습니다.<br />PDF를 업로드하여 시작하세요.
      </div>
      <SidebarTaskCard
        v-for="task in allTasks"
        :key="task.taskId"
        :task="task"
        :active="task.taskId === currentTaskId"
        @click="onCardClick(task)"
      />
    </div>
  </aside>
</template>

<style scoped>
.app-sidebar {
  width: var(--sidebar-width);
  background: var(--sidebar-bg);
  border-right: 1px solid var(--sidebar-border);
  display: flex;
  flex-direction: column;
  height: 100vh;
  position: sticky;
  top: 0;
}

.app-sidebar__header {
  padding: var(--space-4);
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  flex-shrink: 0;
  border-bottom: 1px solid var(--sidebar-border);
}

.app-sidebar__logo {
  font-size: var(--font-size-xl);
  font-weight: var(--font-weight-bold);
  color: var(--color-primary);
  text-decoration: none;
}

.app-sidebar__upload-btn {
  width: 100%;
  justify-content: center;
}

.app-sidebar__list {
  flex: 1;
  overflow-y: auto;
  padding: var(--space-2);
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.app-sidebar__list::-webkit-scrollbar {
  width: 6px;
}

.app-sidebar__list::-webkit-scrollbar-track {
  background: transparent;
}

.app-sidebar__list::-webkit-scrollbar-thumb {
  background: var(--color-border);
  border-radius: var(--radius-full);
}

.app-sidebar__empty {
  padding: var(--space-8) var(--space-4);
  text-align: center;
  color: var(--color-text-muted);
  font-size: var(--font-size-sm);
  line-height: var(--line-height-relaxed);
}

.visually-hidden {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}
</style>
