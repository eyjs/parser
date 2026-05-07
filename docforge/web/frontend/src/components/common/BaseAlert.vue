<script setup lang="ts">
import { computed } from 'vue'

interface Props {
  variant: 'error' | 'success'
  dismissible?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  dismissible: false,
})

const emit = defineEmits<{
  dismiss: []
}>()

const classes = computed(() => ['alert', `alert--${props.variant}`])
</script>

<template>
  <div :class="classes" role="alert" aria-live="assertive">
    <slot />
    <button
      v-if="dismissible"
      class="modal-close"
      aria-label="닫기"
      @click="emit('dismiss')"
    >
      &times;
    </button>
  </div>
</template>

<style scoped>
.alert {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
</style>
