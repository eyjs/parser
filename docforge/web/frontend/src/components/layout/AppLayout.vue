<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import AppHeader from './AppHeader.vue'

const route = useRoute()

// Verify and compare views use full width; dashboard uses constrained page-content
const isFullWidth = computed(() => {
  return route.path.startsWith('/verify') || route.path.startsWith('/compare')
})
</script>

<template>
  <div class="app-layout">
    <AppHeader />
    <main :class="isFullWidth ? 'page-content page-content--full' : 'page-content'">
      <slot />
    </main>
  </div>
</template>

<style scoped>
.app-layout {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
}

.page-content {
  flex: 1;
  max-width: 1200px;
  margin: 0 auto;
  padding: var(--space-4) var(--space-4);
  width: 100%;
}

.page-content--full {
  max-width: 100%;
  padding: var(--space-3) var(--space-4);
}
</style>
