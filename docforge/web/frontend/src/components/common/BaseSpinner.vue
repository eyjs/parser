<script setup lang="ts">
import { computed } from 'vue'

interface Props {
  size?: 'sm' | 'md' | 'lg'
}

const props = withDefaults(defineProps<Props>(), {
  size: 'md',
})

const classes = computed(() => {
  const cls = ['spinner']
  if (props.size !== 'md') cls.push(`spinner--${props.size}`)
  return cls
})
</script>

<template>
  <span :class="classes" aria-hidden="true"></span>
</template>
