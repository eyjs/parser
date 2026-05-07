<script setup lang="ts">
import { ref, watch } from 'vue'
import { usePdfViewer } from '@/composables/usePdfViewer'

interface Props {
  pdfUrl: string | null
}

const props = defineProps<Props>()

const emit = defineEmits<{
  'page-change': [page: number]
}>()

const containerRef = ref<HTMLElement | null>(null)

const { totalPages, currentPage, isLoading, error, loadDocument, goToPage } =
  usePdfViewer({
    scale: 1.5,
    containerRef,
  })

watch(
  () => props.pdfUrl,
  (url) => {
    if (url) {
      loadDocument(url)
    }
  },
  { immediate: true },
)

watch(currentPage, (page) => {
  emit('page-change', page)
})

defineExpose({ goToPage })

function onPageInput(e: Event) {
  const target = e.target as HTMLInputElement
  const num = parseInt(target.value, 10)
  if (num >= 1 && num <= totalPages.value) {
    goToPage(num)
  }
}

function prevPage() {
  if (currentPage.value > 1) goToPage(currentPage.value - 1)
}

function nextPage() {
  if (currentPage.value < totalPages.value) goToPage(currentPage.value + 1)
}
</script>

<template>
  <div class="editor-panel">
    <div class="editor-panel__header">
      <span class="editor-panel__title">PDF 원본</span>
      <div class="editor-panel__nav" v-if="totalPages > 0">
        <button
          class="btn btn--secondary btn--sm"
          :disabled="currentPage <= 1"
          aria-label="이전 페이지"
          @click="prevPage"
        >
          이전
        </button>
        <input
          type="number"
          class="page-num-input"
          :value="currentPage"
          :min="1"
          :max="totalPages"
          aria-label="페이지 번호"
          @change="onPageInput"
        />
        <span class="text-muted text-sm">/ {{ totalPages }}</span>
        <button
          class="btn btn--secondary btn--sm"
          :disabled="currentPage >= totalPages"
          aria-label="다음 페이지"
          @click="nextPage"
        >
          다음
        </button>
      </div>
    </div>

    <div v-if="isLoading" class="editor-panel__loading">
      <span class="spinner" aria-hidden="true"></span>
      <span>PDF 로딩 중...</span>
    </div>

    <div v-if="error" class="alert alert--error">{{ error }}</div>

    <div
      ref="containerRef"
      class="editor-panel__body pdf-scroll-container"
      style="overflow-y: auto; max-height: calc(100vh - 200px);"
    ></div>
  </div>
</template>

<style scoped>
.editor-panel__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--space-2) var(--space-3);
  border-bottom: 1px solid var(--color-border);
}

.editor-panel__title {
  font-weight: 600;
  font-size: var(--font-size-sm);
}

.editor-panel__nav {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.editor-panel__loading {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-4);
  justify-content: center;
}

.page-num-input {
  width: 3rem;
  text-align: center;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  padding: var(--space-1);
  font-size: var(--font-size-sm);
}
</style>
