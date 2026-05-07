<script setup lang="ts">

type CompareMode = 'pdf-vs-md' | 'md-vs-md'

interface Props {
  mode: CompareMode
}

defineProps<Props>()

const emit = defineEmits<{
  'update:mode': [mode: CompareMode]
}>()
</script>

<template>
  <div class="compare-toolbar">
    <div class="compare-toolbar__modes">
      <button
        :class="['btn', mode === 'pdf-vs-md' ? 'btn--primary' : 'btn--secondary', 'btn--sm']"
        @click="emit('update:mode', 'pdf-vs-md')"
      >
        PDF vs MD
      </button>
      <button
        :class="['btn', mode === 'md-vs-md' ? 'btn--primary' : 'btn--secondary', 'btn--sm']"
        @click="emit('update:mode', 'md-vs-md')"
      >
        MD vs MD
      </button>
    </div>
    <slot />
  </div>
</template>

<style scoped>
.compare-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--space-2) var(--space-3);
  border-bottom: 1px solid var(--color-border);
  background: var(--color-surface);
  border-radius: var(--radius-md) var(--radius-md) 0 0;
}

.compare-toolbar__modes {
  display: flex;
  gap: var(--space-2);
}
</style>
