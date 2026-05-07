<script setup lang="ts">
import { ref, watch } from 'vue'
import { usePdfViewer } from '@/composables/usePdfViewer'

interface Props {
  pdfSource: string | ArrayBuffer | null
}

const props = defineProps<Props>()

const containerRef = ref<HTMLElement | null>(null)

const { totalPages, currentPage, isLoading, error, loadDocument } = usePdfViewer({
  scale: 1.5,
  containerRef,
})

watch(
  () => props.pdfSource,
  (source) => {
    if (source) {
      loadDocument(source)
    }
  },
  { immediate: true },
)
</script>

<template>
  <div class="editor-panel">
    <div class="editor-panel__header">
      <span class="editor-panel__title">PDF 원본 (읽기전용)</span>
      <span v-if="totalPages > 0" class="text-muted text-sm">
        {{ currentPage }} / {{ totalPages }}
      </span>
    </div>

    <div v-if="isLoading" class="editor-panel__loading">
      <span class="spinner" aria-hidden="true"></span>
      <span>PDF 로딩 중...</span>
    </div>

    <div v-if="error" class="alert alert--error">{{ error }}</div>

    <div
      ref="containerRef"
      class="editor-panel__body"
      style="overflow-y: auto; max-height: calc(100vh - 250px);"
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

.editor-panel__loading {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-4);
  justify-content: center;
}
</style>
