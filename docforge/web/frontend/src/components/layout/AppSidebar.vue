<script setup lang="ts">
import { ref, computed } from 'vue'
import { useRouter } from 'vue-router'
import { storeToRefs } from 'pinia'
import { useHistoryStore } from '@/stores/history'
import { useTaskStore } from '@/stores/task'
import { uploadFiles } from '@/api/client'
import SidebarTaskCard from './SidebarTaskCard.vue'
import BaseAlert from '@/components/common/BaseAlert.vue'
import type { HistoryEntry } from '@/domain/types'

const props = defineProps<{
  collapsed: boolean
}>()

const emit = defineEmits<{
  toggle: []
}>()

const router = useRouter()
const historyStore = useHistoryStore()
const taskStore = useTaskStore()

const { items, isEmpty, isLoading } = storeToRefs(historyStore)
const { activeTaskList } = storeToRefs(taskStore)

const fileInput = ref<HTMLInputElement | null>(null)
const uploadError = ref<string | null>(null)

const currentTaskId = computed(() => {
  const route = router.currentRoute.value
  const q = route.query.task
  return typeof q === 'string' && q ? q : null
})

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
    router.push({ query: { task: task.taskId } })
  }
}

function triggerUpload() {
  fileInput.value?.click()
}

async function onFileChange(event: Event) {
  const input = event.target as HTMLInputElement
  const files = input.files
  if (!files || files.length === 0) return

  uploadError.value = null
  try {
    const response = await uploadFiles(Array.from(files))
    const taskIds = response.task_ids ?? [response.task_id]
    for (let i = 0; i < taskIds.length; i++) {
      const filename = files[i]?.name ?? `file-${i + 1}.pdf`
      taskStore.trackTask(taskIds[i], filename)
    }
    historyStore.fetchHistory()
  } catch (e) {
    uploadError.value = e instanceof Error ? e.message : '업로드 중 오류가 발생했습니다.'
  }

  input.value = ''
}
</script>

<template>
  <aside
    class="app-sidebar"
    :class="{ 'app-sidebar--collapsed': collapsed }"
    role="navigation"
    aria-label="작업 목록"
  >
    <div class="app-sidebar__header">
      <router-link v-if="!collapsed" to="/" class="app-sidebar__logo">
        DocForge
      </router-link>
      <button
        class="app-sidebar__toggle"
        :title="collapsed ? '사이드바 펼치기' : '사이드바 접기'"
        @click="emit('toggle')"
      >
        <span class="toggle-icon" :class="{ 'toggle-icon--collapsed': collapsed }">&#x276E;</span>
      </button>
    </div>

    <template v-if="!collapsed">
      <div class="app-sidebar__actions">
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

      <BaseAlert
        v-if="uploadError"
        variant="error"
        dismissible
        style="margin: var(--space-2);"
        @dismiss="uploadError = null"
      >
        {{ uploadError }}
      </BaseAlert>

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
    </template>

    <!-- Collapsed: upload icon only -->
    <template v-else>
      <div class="app-sidebar__actions app-sidebar__actions--collapsed">
        <button
          class="btn btn--primary app-sidebar__upload-icon"
          title="PDF 업로드"
          @click="triggerUpload"
        >
          +
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
    </template>
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
  transition: width var(--transition-normal);
  overflow: hidden;
}

.app-sidebar--collapsed {
  width: var(--sidebar-width-collapsed);
}

.app-sidebar__header {
  padding: var(--space-3);
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-shrink: 0;
  border-bottom: 1px solid var(--sidebar-border);
  min-height: 52px;
}

.app-sidebar--collapsed .app-sidebar__header {
  justify-content: center;
}

.app-sidebar__logo {
  font-size: var(--font-size-xl);
  font-weight: var(--font-weight-bold);
  color: var(--color-primary);
  text-decoration: none;
  white-space: nowrap;
}

.app-sidebar__toggle {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border: none;
  background: transparent;
  cursor: pointer;
  color: var(--color-text-muted);
  border-radius: var(--radius-sm);
  flex-shrink: 0;
  transition: background var(--transition-fast), color var(--transition-fast);
}

.app-sidebar__toggle:hover {
  background: var(--color-surface-alt);
  color: var(--color-text);
}

.toggle-icon {
  display: inline-block;
  font-size: var(--font-size-sm);
  transition: transform var(--transition-normal);
}

.toggle-icon--collapsed {
  transform: rotate(180deg);
}

.app-sidebar__actions {
  padding: var(--space-3);
  flex-shrink: 0;
}

.app-sidebar__actions--collapsed {
  display: flex;
  justify-content: center;
}

.app-sidebar__upload-btn {
  width: 100%;
  justify-content: center;
}

.app-sidebar__upload-icon {
  width: 36px;
  height: 36px;
  padding: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: var(--font-size-xl);
  border-radius: var(--radius-md);
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
