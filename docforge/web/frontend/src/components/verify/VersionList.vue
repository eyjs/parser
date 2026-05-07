<script setup lang="ts">
import { ref, watch } from 'vue'
import type { VersionInfo, VersionDiff } from '@/domain/types'
import { formatFileSize } from '@/utils/format'
import BaseButton from '@/components/common/BaseButton.vue'

interface Props {
  versions: VersionInfo[]
  isLoading?: boolean
  diffResult?: VersionDiff | null
}

const props = withDefaults(defineProps<Props>(), {
  isLoading: false,
  diffResult: null,
})

const emit = defineEmits<{
  compare: [v1: string, v2: string]
}>()

const selectedV1 = ref('')
const selectedV2 = ref('')
const showDiff = ref(false)

watch(
  () => props.versions,
  (vs) => {
    if (vs.length >= 2) {
      selectedV1.value = vs[0].name
      selectedV2.value = vs[1].name
    }
  },
  { immediate: true },
)

watch(() => props.diffResult, (result) => {
  if (result) showDiff.value = true
})

function onCompare() {
  if (!selectedV1.value || !selectedV2.value) return
  emit('compare', selectedV1.value, selectedV2.value)
}
</script>

<template>
  <div class="version-list">
    <h3 class="section-title">버전 이력</h3>

    <div v-if="isLoading" class="text-muted text-sm">로딩 중...</div>

    <div v-else-if="versions.length === 0" class="text-muted text-sm">
      저장된 버전이 없습니다.
    </div>

    <template v-else>
      <ul class="version-list__items">
        <li v-for="v in versions" :key="v.name" class="version-list__item">
          <span>{{ v.name }}</span>
          <span class="text-muted text-sm">{{ formatFileSize(v.size) }}</span>
        </li>
      </ul>

      <div v-if="versions.length >= 2" class="version-list__compare">
        <select v-model="selectedV1" aria-label="기준 버전">
          <option v-for="v in versions" :key="v.name" :value="v.name">
            {{ v.name }}
          </option>
        </select>
        <span>vs</span>
        <select v-model="selectedV2" aria-label="비교 버전">
          <option v-for="v in versions" :key="v.name" :value="v.name">
            {{ v.name }}
          </option>
        </select>
        <BaseButton variant="secondary" size="sm" @click="onCompare">
          비교
        </BaseButton>
      </div>

      <div v-if="showDiff && diffResult" class="version-list__diff">
        <p v-if="!diffResult.hasChanges" class="text-muted text-sm">변경 사항 없음</p>
        <pre v-else class="diff-view">{{ diffResult.diff }}</pre>
      </div>
    </template>
  </div>
</template>

<style scoped>
.version-list__items {
  list-style: none;
  padding: 0;
  margin: var(--space-2) 0;
}

.version-list__item {
  display: flex;
  justify-content: space-between;
  padding: var(--space-1) var(--space-2);
  border-bottom: 1px solid var(--color-border);
}

.version-list__compare {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-top: var(--space-2);
}

.version-list__compare select {
  padding: var(--space-1) var(--space-2);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  font-size: var(--font-size-sm);
}

.version-list__diff {
  margin-top: var(--space-3);
  max-height: 300px;
  overflow: auto;
}
</style>
