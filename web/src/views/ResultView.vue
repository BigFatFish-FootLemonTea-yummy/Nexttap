<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import UiButton from '@/components/UiButton.vue'
import TargetOverlay from '@/components/TargetOverlay.vue'
import { locate } from '@/api/client'
import { ApiError, type NormBox } from '@/types/api'
import { useSessionStore } from '@/stores/session'

const router = useRouter()
const session = useSessionStore()

const editingText = ref(false)
const draftText = ref('')
const showRetry = ref(false)
const retryGoal = ref(session.goal)
const locating = ref(false)
const error = ref<ApiError | null>(null)

const result = computed(() => session.result)
const confidencePct = computed(() => Math.round((result.value?.confidence ?? 0) * 100))
const elapsedSec = computed(() => ((result.value?.elapsed_ms ?? 0) / 1000).toFixed(1))
const retryInvalid = computed(() => {
  const len = retryGoal.value.trim().length
  return len === 0 || len > 100
})

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

/** 恢复 AI 原始结果（人工微调改错了能一键退回） */
function restoreAuto() {
  session.restoreResult()
}

async function retry() {
  if (retryInvalid.value || locating.value) return
  const goal = retryGoal.value.trim()
  locating.value = true
  error.value = null
  try {
    // 复用 image_id，不重新上传（图片已经在后端）
    const next = await locate({ image_id: session.imageId, goal })
    session.setGoal(goal)
    session.applyResult(next)
    showRetry.value = false
  } catch (err) {
    error.value = err instanceof ApiError ? err : new ApiError(0, 'network', '定位失败，请重试')
  } finally {
    locating.value = false
  }
}

function retake() {
  session.reset()
  void router.push({ name: 'upload' })
}
</script>

<template>
  <div v-if="result && session.box" class="flex flex-1 flex-col gap-4 pb-4">
    <section class="rounded-lg border border-border bg-card p-3">
      <p class="px-1 pb-2 text-sm text-muted-foreground">
        需求：<span class="font-medium text-foreground">{{ session.goal }}</span>
      </p>
      <TargetOverlay
        :image-url="session.imageDataUrl"
        :natural-width="session.width"
        :natural-height="session.height"
        :box="session.box"
        :editable="true"
        :label="`请点 ${result.text}`"
        @update:box="onBoxChange"
      />
      <p class="px-1 pt-2 text-xs text-muted-foreground">
        圈不准？拖动红圈移动位置，拖四个角调整大小。
      </p>
    </section>

    <div
      v-if="result.low_confidence"
      class="rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900"
      role="alert"
    >
      AI 把握不大（置信度 {{ confidencePct }}%）。请核对圈的位置，必要时手动调整或重新拍照。
    </div>

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
        命中「{{ result.text }}」 · 置信度 {{ confidencePct }}% · 候选 {{ result.candidate_count }} 个 · 耗时 {{ elapsedSec }}s
        <span v-if="session.adjusted" class="text-primary"> · 已人工微调</span>
      </p>
      <button v-if="session.adjusted" type="button" class="mt-1 text-xs text-primary underline" @click="restoreAuto">
        恢复 AI 原结果
      </button>
    </section>

    <section v-if="showRetry" class="rounded-lg border border-border bg-card p-4">
      <label for="retry-goal" class="text-sm font-medium">换一个目标（不用重新拍照）</label>
      <input
        id="retry-goal"
        v-model="retryGoal"
        maxlength="120"
        :disabled="locating"
        class="mt-2 w-full rounded-xl border border-input bg-background px-3 py-3 text-base outline-none focus-visible:ring-2 focus-visible:ring-ring"
        placeholder="例如：我想给儿子打视频电话"
      >
      <p v-if="error" class="mt-2 text-sm text-destructive" role="alert">{{ error.message }}</p>
      <div class="mt-3 flex gap-2">
        <UiButton variant="outline" :disabled="locating" @click="showRetry = false">取消</UiButton>
        <UiButton class="flex-1" :loading="locating" :disabled="retryInvalid" @click="retry">重新定位</UiButton>
      </div>
    </section>

    <div class="mt-auto flex flex-col gap-2">
      <UiButton size="lg" block @click="router.push({ name: 'show' })">给老人看</UiButton>
      <div class="grid grid-cols-2 gap-2">
        <UiButton variant="outline" :disabled="locating" @click="showRetry = !showRetry; retryGoal = session.goal; error = null">
          换一个目标
        </UiButton>
        <UiButton variant="ghost" @click="retake">重新拍照</UiButton>
      </div>
    </div>
  </div>
</template>
