<script setup lang="ts">
import { computed } from 'vue'

interface Props {
  currentStage: string
}

const props = defineProps<Props>()

interface Stage {
  key: string
  label: string
}

const stages: Stage[] = [
  { key: 'profiling', label: '프로파일링' },
  { key: 'noise_learning', label: '노이즈 학습' },
  { key: 'pages', label: '페이지 처리' },
  { key: 'table_merging', label: '테이블 병합' },
  { key: 'assembling', label: 'MD 조립' },
]

const stageOrder = stages.map((s) => s.key)

const currentIdx = computed(() => stageOrder.indexOf(props.currentStage))

function pillClass(stage: Stage, idx: number): string {
  if (props.currentStage === 'done') return 'stage-pill stage-pill--done'
  if (idx < currentIdx.value) return 'stage-pill stage-pill--done'
  if (idx === currentIdx.value) return 'stage-pill stage-pill--active'
  return 'stage-pill stage-pill--idle'
}
</script>

<template>
  <div class="live-preview__stages">
    <div
      v-for="(stage, idx) in stages"
      :key="stage.key"
      :class="pillClass(stage, idx)"
      :data-stage="stage.key"
    >
      {{ stage.label }}
    </div>
  </div>
</template>
