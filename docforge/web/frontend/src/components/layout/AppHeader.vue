<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'

const route = useRoute()

interface NavItem {
  to: string
  label: string
  match: (path: string) => boolean
}

const navItems: NavItem[] = [
  { to: '/', label: '대시보드', match: (p) => p === '/' },
  { to: '/compare', label: '비교 도구', match: (p) => p.startsWith('/compare') },
]

const currentPath = computed(() => route.path)
</script>

<template>
  <header class="app-header">
    <div class="app-header__inner">
      <router-link to="/" class="app-header__logo">
        DocForge
      </router-link>

      <nav class="app-header__nav" aria-label="메인 내비게이션">
        <router-link
          v-for="item in navItems"
          :key="item.to"
          :to="item.to"
          :class="['app-header__link', { 'app-header__link--active': item.match(currentPath) }]"
        >
          {{ item.label }}
        </router-link>
      </nav>
    </div>
  </header>
</template>

<style scoped>
.app-header {
  background: var(--color-surface);
  border-bottom: 1px solid var(--color-border);
  padding: 0 var(--space-4);
}

.app-header__inner {
  display: flex;
  align-items: center;
  gap: var(--space-6);
  max-width: 1440px;
  margin: 0 auto;
  height: 56px;
}

.app-header__logo {
  font-size: var(--font-size-lg);
  font-weight: 700;
  color: var(--color-primary);
  text-decoration: none;
}

.app-header__nav {
  display: flex;
  gap: var(--space-4);
}

.app-header__link {
  font-size: var(--font-size-sm);
  font-weight: 500;
  color: var(--color-text-muted);
  text-decoration: none;
  padding: var(--space-1) 0;
  border-bottom: 2px solid transparent;
  transition: color 0.15s, border-color 0.15s;
}

.app-header__link:hover {
  color: var(--color-text);
}

.app-header__link--active {
  color: var(--color-primary);
  border-bottom-color: var(--color-primary);
}
</style>
