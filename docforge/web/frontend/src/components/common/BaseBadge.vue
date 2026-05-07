<script setup lang="ts">
import { computed } from 'vue'

interface Props {
  variant: 'success' | 'warning' | 'error' | 'pending' | 'running'
}

const props = defineProps<Props>()

const classes = computed(() => ['badge', `badge--${props.variant}`])
</script>

<template>
  <span :class="classes">
    <slot />
  </span>
</template>
