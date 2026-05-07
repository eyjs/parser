<script setup lang="ts">
import { ref, computed } from 'vue'
import PageGrid from './PageGrid.vue'
import StagePills from './StagePills.vue'
import BaseModal from '@/components/common/BaseModal.vue'

interface Props {
  totalPages: number
  completedPages: number
  currentStage: string
  pageMarkdowns: Map<number, string>
}

const props = defineProps<Props>()

const tailCollapsed = ref(false)
const viewerOpen = ref(false)
const viewerPage = ref(1)

const recentPages = computed(() => {
  const entries = Array.from(props.pageMarkdowns.entries())
  // Sort by page number descending to show most recent
  return entries.sort((a, b) => b[0] - a[0]).slice(0, 5)
})

const viewerMarkdown = computed(() => {
  return props.pageMarkdowns.get(viewerPage.value) ?? ''
})

function openPageViewer(pageNum: number) {
  viewerPage.value = pageNum
  viewerOpen.value = true
}

function navigatePage(direction: number) {
  const donePages = Array.from(props.pageMarkdowns.keys()).sort((a, b) => a - b)
  const currentIdx = donePages.indexOf(viewerPage.value)
  if (currentIdx < 0) return

  const nextIdx = currentIdx + direction
  if (nextIdx >= 0 && nextIdx < donePages.length) {
    viewerPage.value = donePages[nextIdx]
  }
}

async function copyPageMarkdown() {
  const md = props.pageMarkdowns.get(viewerPage.value) ?? ''
  try {
    await navigator.clipboard.writeText(md)
  } catch {
    // Fallback for insecure contexts
  }
}

function truncate(text: string, maxLength = 600): string {
  if (text.length <= maxLength) return text
  return text.slice(0, maxLength) + '\n\n...(생략)'
}
</script>

<template>
  <section class="card mb-6" aria-labelledby="live-preview-heading">
    <div class="card__header" id="live-preview-heading">
      <span>실시간 파싱 현황</span>
      <span class="badge badge--running" style="margin-left: auto;">
        {{ completedPages }} / {{ totalPages }}
      </span>
    </div>
    <div class="card__body live-preview">
      <PageGrid
        :total-pages="totalPages"
        :completed-pages="pageMarkdowns"
        @page-click="openPageViewer"
      />

      <StagePills :current-stage="currentStage" />

      <div class="live-preview__tail-header">
        <span>최근 처리된 페이지</span>
        <button
          type="button"
          class="btn btn--secondary btn--sm"
          @click="tailCollapsed = !tailCollapsed"
        >
          {{ tailCollapsed ? '펼치기' : '접기' }}
        </button>
      </div>

      <div
        v-show="!tailCollapsed"
        class="live-preview__tail"
        aria-live="polite"
      >
        <template v-if="recentPages.length === 0">
          <p class="text-muted text-sm" style="padding: 1rem; text-align: center;">
            처리가 시작되면 여기에 페이지별 마크다운이 표시됩니다.
          </p>
        </template>
        <details
          v-for="[pageNum, markdown] in recentPages"
          :key="pageNum"
          class="tail-page"
          open
        >
          <summary>
            페이지 {{ pageNum }}
            <span class="text-muted text-sm">({{ markdown.length }}자)</span>
            <button
              type="button"
              class="btn btn--secondary btn--sm tail-page__open"
              @click.stop.prevent="openPageViewer(pageNum)"
            >
              전체 보기
            </button>
          </summary>
          <pre class="tail-page__body">{{ truncate(markdown) }}</pre>
        </details>
      </div>
    </div>
  </section>

  <!-- Page viewer modal -->
  <BaseModal
    :open="viewerOpen"
    :title="`페이지 ${viewerPage} 미리보기`"
    wide
    @close="viewerOpen = false"
  >
    <div style="display: flex; align-items: center; gap: var(--space-2); margin-bottom: var(--space-3);">
      <button
        class="btn btn--secondary btn--sm"
        aria-label="이전 페이지"
        @click="navigatePage(-1)"
      >
        이전
      </button>
      <span class="text-muted text-sm">
        {{ viewerPage }} / {{ totalPages }}
      </span>
      <button
        class="btn btn--secondary btn--sm"
        aria-label="다음 페이지"
        @click="navigatePage(1)"
      >
        다음
      </button>
      <button
        class="btn btn--secondary btn--sm"
        aria-label="복사"
        @click="copyPageMarkdown"
      >
        복사
      </button>
    </div>
    <pre class="page-viewer__body" tabindex="0">{{ viewerMarkdown || '(빈 페이지)' }}</pre>
  </BaseModal>
</template>
