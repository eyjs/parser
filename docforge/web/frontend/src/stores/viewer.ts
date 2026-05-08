import { defineStore } from 'pinia'
import { ref, computed } from 'vue'

export const useViewerStore = defineStore('viewer', () => {
  const currentTaskId = ref<string | null>(null)
  const pdfUrl = ref<string | null>(null)
  const markdown = ref('')
  const originalMarkdown = ref('')
  const isLoading = ref(false)
  const loadError = ref<string | null>(null)
  const isSaving = ref(false)
  const saveError = ref<string | null>(null)

  const isDirty = computed(() => markdown.value !== originalMarkdown.value)

  const isOpen = computed(() => currentTaskId.value !== null)

  function openDocument(taskId: string, data: {
    pdfUrl: string | null
    markdown: string
  }) {
    currentTaskId.value = taskId
    pdfUrl.value = data.pdfUrl
    markdown.value = data.markdown
    originalMarkdown.value = data.markdown
    isLoading.value = false
    loadError.value = null
    saveError.value = null
  }

  function updateMarkdown(md: string) {
    markdown.value = md
  }

  function markSaved() {
    originalMarkdown.value = markdown.value
    isSaving.value = false
    saveError.value = null
  }

  function setSaving(saving: boolean) {
    isSaving.value = saving
  }

  function setSaveError(err: string | null) {
    saveError.value = err
    isSaving.value = false
  }

  function setLoading(loading: boolean) {
    isLoading.value = loading
  }

  function setLoadError(err: string | null) {
    loadError.value = err
    isLoading.value = false
  }

  function close() {
    currentTaskId.value = null
    pdfUrl.value = null
    markdown.value = ''
    originalMarkdown.value = ''
    isLoading.value = false
    loadError.value = null
    isSaving.value = false
    saveError.value = null
  }

  return {
    currentTaskId,
    pdfUrl,
    markdown,
    originalMarkdown,
    isLoading,
    loadError,
    isSaving,
    saveError,
    isDirty,
    isOpen,
    openDocument,
    updateMarkdown,
    markSaved,
    setSaving,
    setSaveError,
    setLoading,
    setLoadError,
    close,
  }
})
