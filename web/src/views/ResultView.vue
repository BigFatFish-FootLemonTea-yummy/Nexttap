<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import UiButton from '@/components/UiButton.vue'
import TargetOverlay from '@/components/TargetOverlay.vue'
import type { NormBox } from '@/types/api'
import { useSessionStore } from '@/stores/session'

const router = useRouter()
const session = useSessionStore()

const editingText = ref(false)
const draftText = ref('')

const statusText = computed(() => {
  switch (session.status) {
    case 'done':
      return '全部完成'
    case 'in_progress':
      return '进行中'
    case 'needs_retake':
      return '需要重拍'
    case 'escalated':
      return '需转人工'
    default:
      return ''
  }
})

const statusClass = computed(() => {
  if (session.status === 'done') return 'bg-emerald-100 text-emerald-800'
  if (session.status === 'in_progress') return 'bg-primary/10 text-primary'
  return 'bg-amber-100 text-amber-900'
})

const confidencePct = computed(() => Math.round((session.currentStep?.confidence ?? 0) * 100))

function onBoxChange(box: NormBox) {
  session.setBox(box)
}

function beginEditText() {
  draftText.value = session.instruction
  editingText.value = true
}

function saveText() {
  const text = draftText.value.trim()
  if (!text) return
  session.setInstruction(text.slice(0, 20))
  editingText.value = false
}

function retake() {
  session.reset()
  void router.push({ name: 'upload' })
}

function nextShot() {
  void router.push({ name: 'upload', query: { mode: 'next' } })
}
</script>

<template>
  <div v-if="session.currentStep" class="flex flex-1 flex-col gap-4 pb-4">
    <!-- 任务头 + 步骤链 -->
    <section class="rounded-lg border border-border bg-card p-4">
      <div class="flex items-start justify-between gap-3">
        <div class="min-w-0">
          <p class="text-xs text-muted-foreground">老人想做的事</p>
          <p class="mt-1 truncate text-lg font-semibold">{{ session.taskName || session.goal }}</p>
        </div>
        <span class="shrink-0 rounded-full px-3 py-1 text-xs font-medium" :class="statusClass">
          {{ statusText }}
        </span>
      </div>

      <ol class="mt-3 space-y-1">
        <li v-for="step in session.steps" :key="step.step">
          <button
            type="button"
            class="flex w-full items-start gap-2 rounded-lg px-2 py-2 text-left text-sm transition-colors"
            :class="step.step === session.stepIndex ? 'bg-primary/10' : 'hover:bg-secondary/60'"
            @click="session.selectStep(step.step)"
          >
            <span
              class="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-xs font-semibold"
              :class="step.step === session.stepIndex ? 'bg-primary text-primary-foreground' : 'bg-secondary text-secondary-foreground'"
            >
              {{ step.step }}
            </span>
            <span class="min-w-0">
              <span class="font-medium">点「{{ step.text }}」</span>
              <span class="block truncate text-xs text-muted-foreground">{{ step.instruction }}</span>
            </span>
          </button>
        </li>
        <li v-if="session.nextStep" class="flex items-start gap-2 px-2 py-2 text-sm text-muted-foreground">
          <span class="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full border border-dashed border-input text-xs">
            {{ session.nextStep.step }}
          </span>
          <span class="min-w-0">
            待拍摄：{{ session.nextStep.target }}
            <span class="block text-xs">把老人点完上一步后的界面拍下来</span>
          </span>
        </li>
      </ol>
    </section>

    <!-- 当前步骤的截图与高亮 -->
    <section class="rounded-lg border border-border bg-card p-3">
      <TargetOverlay
        :image-url="session.imageDataUrl"
        :natural-width="session.width"
        :natural-height="session.height"
        :box="session.box"
        :editable="true"
        :label="`请点 ${session.currentStep.text}`"
        @update:box="onBoxChange"
      />
      <p class="px-1 pt-2 text-xs text-muted-foreground">
        圈不准？拖动红圈移动位置，拖四个角调整大小。
      </p>
    </section>

    <!-- 指引文案 -->
    <section class="rounded-lg border border-border bg-card p-4">
      <div class="flex items-start justify-between gap-3">
        <p class="text-xl font-semibold leading-snug" aria-live="polite">{{ session.instruction }}</p>
        <UiButton size="sm" variant="ghost" @click="beginEditText">改文案</UiButton>
      </div>
      <div v-if="editingText" class="mt-3">
        <input
          v-model="draftText"
          maxlength="20"
          class="w-full rounded-xl border border-input bg-background px-3 py-3 text-lg outline-none focus-visible:ring-2 focus-visible:ring-ring"
          placeholder="不超过 20 字，含方位和按钮名"
        >
        <div class="mt-2 flex items-center justify-between">
          <span class="text-xs text-muted-foreground">{{ draftText.trim().length }}/20</span>
          <div class="flex gap-2">
            <UiButton size="sm" variant="ghost" @click="editingText = false">取消</UiButton>
            <UiButton size="sm" :disabled="!draftText.trim()" @click="saveText">保存</UiButton>
          </div>
        </div>
      </div>
      <p class="mt-3 text-xs text-muted-foreground">
        第 {{ session.stepIndex }} 步 · 置信度 {{ confidencePct }}%
        <span v-if="session.adjusted" class="text-primary"> · 已人工微调</span>
      </p>
      <button v-if="session.adjusted" type="button" class="mt-1 text-xs text-primary underline" @click="session.restoreResult()">
        恢复 AI 原结果
      </button>
    </section>

    <!-- 多步：继续拍下一步 -->
    <section v-if="session.canContinue" class="rounded-lg border border-primary/30 bg-primary/5 p-4">
      <p class="text-sm font-medium text-primary">还有 {{ session.nextStep?.step }} 步</p>
      <p class="mt-1 text-xs text-muted-foreground">
        下一步：{{ session.nextStep?.target }}。等老人点完这一步，把新界面拍下来即可。
      </p>
    </section>

    <div class="mt-auto flex flex-col gap-2">
      <UiButton size="lg" block @click="router.push({ name: 'show' })">给老人看</UiButton>
      <UiButton v-if="session.canContinue" variant="outline" block @click="nextShot">拍下一步</UiButton>
      <div class="grid grid-cols-1 gap-2">
        <UiButton variant="ghost" @click="retake">重新开始（换一个目标）</UiButton>
      </div>
    </div>
  </div>
</template>
