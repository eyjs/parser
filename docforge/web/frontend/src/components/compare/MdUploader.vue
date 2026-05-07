<script setup lang="ts">
import { ref } from 'vue'

const emit = defineEmits<{
  'file-loaded': [content: string, filename: string]
}>()

const isDragOver = ref(false)
const fileInput = ref<HTMLInputElement | null>(null)
const errorMessage = ref<string | null>(null)

function onClick() {
  fileInput.value?.click()
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
  if (files.length > 0) processFile(files[0])
}

function onFileChange() {
  const input = fileInput.value
  if (!input || !input.files || input.files.length === 0) return
  processFile(input.files[0])
  input.value = ''
}

function processFile(file: File) {
  errorMessage.value = null

  if (!file.name.toLowerCase().endsWith('.md')) {
    errorMessage.value = '.md 파일만 업로드할 수 있습니다.'
    return
  }

  const reader = new FileReader()
  reader.onload = () => {
    const content = reader.result as string
    emit('file-loaded', content, file.name)
  }
  reader.onerror = () => {
    errorMessage.value = '파일 읽기 실패'
  }
  reader.readAsText(file)
}
</script>

<template>
  <div
    :class="['drop-zone drop-zone--sm', { 'drop-zone--active': isDragOver }]"
    role="button"
    tabindex="0"
    aria-label="마크다운 파일 드래그 또는 클릭"
    @click="onClick"
    @dragover="onDragOver"
    @dragleave="onDragLeave"
    @drop="onDrop"
    @keydown.enter="onClick"
    @keydown.space.prevent="onClick"
  >
    <p class="drop-zone__label">.md 파일을 드래그하거나 클릭</p>
    <input
      ref="fileInput"
      type="file"
      accept=".md"
      class="sr-only"
      @change="onFileChange"
    />
  </div>
  <p v-if="errorMessage" class="alert alert--error" style="margin-top: var(--space-2);">
    {{ errorMessage }}
  </p>
</template>

<style scoped>
.drop-zone--sm {
  padding: var(--space-3);
  min-height: auto;
}
</style>
