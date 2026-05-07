<script setup lang="ts">
import { computed } from 'vue'

interface Props {
  variant?: 'primary' | 'secondary' | 'danger'
  size?: 'sm' | 'md' | 'lg'
  disabled?: boolean
  loading?: boolean
  href?: string
  download?: string
}

const props = withDefaults(defineProps<Props>(), {
  variant: 'secondary',
  size: 'md',
  disabled: false,
  loading: false,
})

const classes = computed(() => {
  const cls = ['btn']
  if (props.variant) cls.push(`btn--${props.variant}`)
  if (props.size !== 'md') cls.push(`btn--${props.size}`)
  return cls
})

const isDisabled = computed(() => props.disabled || props.loading)
const tag = computed(() => (props.href ? 'a' : 'button'))
</script>

<template>
  <component
    :is="tag"
    :class="classes"
    :disabled="tag === 'button' ? isDisabled : undefined"
    :href="href"
    :download="download"
    :aria-disabled="isDisabled || undefined"
  >
    <span v-if="loading" class="spinner" aria-hidden="true"></span>
    <slot />
  </component>
</template>
