<script setup lang="ts">
import { onMounted } from 'vue'
import { useHistoryStore } from '@/stores/history'
import BaseBadge from '@/components/common/BaseBadge.vue'
import BaseButton from '@/components/common/BaseButton.vue'
import { getExportUrl } from '@/api/client'

const historyStore = useHistoryStore()

onMounted(() => {
  historyStore.fetchHistory()
})

function badgeVariant(status: string): 'success' | 'error' | 'running' | 'pending' {
  const map: Record<string, 'success' | 'error' | 'running' | 'pending'> = {
    done: 'success',
    error: 'error',
    cancelled: 'error',
    running: 'running',
    queued: 'pending',
  }
  return map[status] ?? 'pending'
}

function statusLabel(status: string): string {
  const map: Record<string, string> = {
    done: '완료',
    error: '오류',
    cancelled: '취소됨',
    running: '처리 중',
    queued: '대기',
  }
  return map[status] ?? status
}

function formatDate(dateStr: string): string {
  if (!dateStr) return '-'
  try {
    const d = new Date(dateStr)
    return d.toLocaleString('ko-KR', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    })
  } catch {
    return dateStr
  }
}

async function onDelete(taskId: string) {
  if (!confirm('이 항목을 삭제하시겠습니까?')) return
  try {
    await historyStore.deleteItem(taskId)
  } catch (e) {
    alert(e instanceof Error ? e.message : '삭제 중 오류가 발생했습니다.')
  }
}
</script>

<template>
  <section class="card" aria-labelledby="history-heading">
    <div class="card__header" id="history-heading">변환 이력</div>
    <div class="card__body" style="padding: 0; overflow-x: auto;">
      <table class="history-table" aria-label="변환 이력 목록">
        <thead>
          <tr>
            <th scope="col">파일명</th>
            <th scope="col">상태</th>
            <th scope="col">생성 일시</th>
            <th scope="col">완료 일시</th>
            <th scope="col">작업</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="historyStore.isEmpty && !historyStore.isLoading">
            <td colspan="5" class="text-muted text-sm" style="text-align: center; padding: 2rem;">
              변환 이력이 없습니다.
            </td>
          </tr>
          <tr v-for="item in historyStore.items" :key="item.task_id" :data-task-id="item.task_id">
            <td :title="item.task_id">{{ item.filename }}</td>
            <td>
              <BaseBadge :variant="badgeVariant(item.status)">
                {{ statusLabel(item.status) }}
              </BaseBadge>
            </td>
            <td>{{ formatDate(item.created_at) }}</td>
            <td>{{ item.completed_at ? formatDate(item.completed_at) : '-' }}</td>
            <td>
              <div class="actions">
                <BaseButton
                  v-if="item.status === 'done'"
                  variant="secondary"
                  size="sm"
                  :href="`/verify/${item.task_id}`"
                >
                  검증
                </BaseButton>
                <BaseButton
                  v-if="item.status === 'done'"
                  variant="secondary"
                  size="sm"
                  :href="getExportUrl(item.task_id)"
                >
                  다운로드
                </BaseButton>
                <BaseButton
                  variant="secondary"
                  size="sm"
                  @click="onDelete(item.task_id)"
                >
                  삭제
                </BaseButton>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>
