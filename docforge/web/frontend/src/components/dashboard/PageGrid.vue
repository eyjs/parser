<script setup lang="ts">

interface Props {
  totalPages: number
  completedPages: Map<number, string>
  activePage?: number
}

const props = withDefaults(defineProps<Props>(), {
  activePage: 0,
})

const emit = defineEmits<{
  'page-click': [pageNum: number]
}>()

function cellClass(pageNum: number): string {
  if (props.completedPages.has(pageNum)) return 'page-cell page-cell--done'
  if (pageNum === props.activePage) return 'page-cell page-cell--active'
  return 'page-cell page-cell--pending'
}

function cellTitle(pageNum: number): string {
  if (props.completedPages.has(pageNum)) {
    const md = props.completedPages.get(pageNum) ?? ''
    return `${pageNum}페이지 (클릭하여 보기 / ${md.length}자)`
  }
  if (pageNum === props.activePage) return `${pageNum}페이지 (처리 중)`
  return `${pageNum}페이지 (대기)`
}

function onCellClick(pageNum: number) {
  if (props.completedPages.has(pageNum)) {
    emit('page-click', pageNum)
  }
}
</script>

<template>
  <div class="live-preview__grid" aria-label="페이지별 처리 상태">
    <button
      v-for="pageNum in totalPages"
      :key="pageNum"
      type="button"
      :class="cellClass(pageNum)"
      :title="cellTitle(pageNum)"
      :disabled="!completedPages.has(pageNum)"
      @click="onCellClick(pageNum)"
    >
      {{ pageNum }}
    </button>
  </div>
</template>
