<script setup lang="ts">
import { onMounted } from 'vue'
import { RouterView } from 'vue-router'
import { useHistoryStore } from '@/stores/history'

const historyStore = useHistoryStore()

onMounted(() => {
  historyStore.fetchHistory()
})
</script>

<template>
  <RouterView />
</template>
