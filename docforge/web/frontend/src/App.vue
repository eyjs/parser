<script setup lang="ts">
import { onMounted } from 'vue'
import { RouterView } from 'vue-router'
import AppLayout from '@/components/layout/AppLayout.vue'
import { useHistoryStore } from '@/stores/history'

const historyStore = useHistoryStore()

onMounted(() => {
  historyStore.fetchHistory()
})
</script>

<template>
  <AppLayout>
    <RouterView />
  </AppLayout>
</template>
