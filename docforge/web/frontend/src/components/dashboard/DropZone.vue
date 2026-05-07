<script setup lang="ts">
import { ref } from 'vue'

const emit = defineEmits<{
  'files-selected': [files: File[]]
}>()

const isDragOver = ref(false)
const fileInput = ref<HTMLInputElement | null>(null)

const MAX_SIZE_MB = 100
const MAX_SIZE_BYTES = MAX_SIZE_MB * 1024 * 1024
const errorMessage = ref<string | null>(null)

function onClick() {
  fileInput.value?.click()
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter' || e.key === ' ') {
    e.preventDefault()
    fileInput.value?.click()
  }
}

function onDragOver(e: DragEvent) {
  e.preventDefault()
  isDragOver.value = true
}

function onDragLeave() {
  isDragOver.value = false
}

function onDrop(e: DragEvent) {
  e.preventDefault()
  isDragOver.value = false

  const files = Array.from(e.dataTransfer?.files ?? [])
  processFiles(files)
}

function onFileChange() {
  const input = fileInput.value
  if (!input || !input.files) return

  const files = Array.from(input.files)
  processFiles(files)
  input.value = ''
}

function processFiles(files: File[]) {
  errorMessage.value = null

  const pdfFiles = files.filter((f) => f.name.toLowerCase().endsWith('.pdf'))
  if (pdfFiles.length === 0) {
    errorMessage.value = 'PDF 파일만 업로드할 수 있습니다.'
    return
  }

  const oversized = pdfFiles.filter((f) => f.size > MAX_SIZE_BYTES)
  if (oversized.length > 0) {
    errorMessage.value = `${oversized.length}개 파일이 ${MAX_SIZE_MB}MB를 초과합니다.`
    return
  }

  emit('files-selected', pdfFiles)
}
</script>

<template>
  <div
    :class="['drop-zone', { 'drop-zone--active': isDragOver }]"
    role="button"
    aria-label="PDF 파일을 클릭하거나 드래그하여 업로드"
    tabindex="0"
    @click="onClick"
    @keydown="onKeydown"
    @dragover="onDragOver"
    @dragleave="onDragLeave"
    @drop="onDrop"
  >
    <span class="drop-zone__icon" aria-hidden="true">PDF</span>
    <p class="drop-zone__label">PDF 파일을 여기에 드래그하거나 클릭하세요</p>
    <p class="drop-zone__hint">최대 {{ MAX_SIZE_MB }}MB / PDF 파일만 허용 / 여러 파일 선택 가능</p>
    <input
      ref="fileInput"
      type="file"
      accept=".pdf"
      multiple
      class="sr-only"
      aria-label="PDF 파일 선택"
      @change="onFileChange"
    />
  </div>

  <p v-if="errorMessage" class="alert alert--error" role="alert">
    {{ errorMessage }}
  </p>
</template>
