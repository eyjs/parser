<script setup lang="ts">
import { ref } from 'vue'
import AppSidebar from './AppSidebar.vue'

const sidebarCollapsed = ref(false)
</script>

<template>
  <div class="app-layout" :class="{ 'app-layout--collapsed': sidebarCollapsed }">
    <AppSidebar
      :collapsed="sidebarCollapsed"
      @toggle="sidebarCollapsed = !sidebarCollapsed"
    />
    <main class="app-layout__main">
      <slot />
    </main>
  </div>
</template>

<style scoped>
.app-layout {
  display: grid;
  grid-template-columns: var(--sidebar-width) 1fr;
  min-height: 100vh;
  transition: grid-template-columns var(--transition-normal);
}

.app-layout--collapsed {
  grid-template-columns: var(--sidebar-width-collapsed) 1fr;
}

.app-layout__main {
  padding: var(--space-4);
  overflow-y: auto;
  background: var(--color-bg);
}
</style>
