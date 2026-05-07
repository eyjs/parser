<script setup lang="ts">
import { watch, onUnmounted } from 'vue'

interface Props {
  open: boolean
  title: string
  wide?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  wide: false,
})

const emit = defineEmits<{
  close: []
}>()

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape') {
    emit('close')
  }
}

function onOverlayClick(e: MouseEvent) {
  if ((e.target as HTMLElement).classList.contains('modal-overlay')) {
    emit('close')
  }
}

watch(
  () => props.open,
  (isOpen) => {
    if (isOpen) {
      document.addEventListener('keydown', onKeydown)
      document.body.style.overflow = 'hidden'
    } else {
      document.removeEventListener('keydown', onKeydown)
      document.body.style.overflow = ''
    }
  },
  { immediate: true },
)

onUnmounted(() => {
  document.removeEventListener('keydown', onKeydown)
  document.body.style.overflow = ''
})
</script>

<template>
  <Teleport to="body">
    <div
      v-if="open"
      class="modal-overlay"
      aria-modal="true"
      role="dialog"
      :aria-label="title"
      @click="onOverlayClick"
    >
      <div :class="['modal-content', { 'modal-content--wide': wide }]">
        <div class="modal-header">
          <h2 class="modal-title">{{ title }}</h2>
          <button
            class="modal-close"
            aria-label="닫기"
            @click="emit('close')"
          >
            &times;
          </button>
        </div>
        <div class="modal-body">
          <slot />
        </div>
      </div>
    </div>
  </Teleport>
</template>
