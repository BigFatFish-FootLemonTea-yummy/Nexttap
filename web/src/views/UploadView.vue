<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'
import { useRouter } from 'vue-router'
import UiButton from '@/components/UiButton.vue'
import { compressImage, formatBytes, type CompressedImage } from '@/lib/compress'
import { locate, uploadScreenshot } from '@/api/client'
import { ApiError } from '@/types/api'
import { useSessionStore } from '@/stores/session'

const router = useRouter()
const session = useSessionStore()

const cameraInput = ref<HTMLInputElement | null>(null)
const galleryInput = ref<HTMLInputElement | null>(null)
const image = ref<CompressedImage | null>(null)
const goal = ref('')
const preparing = ref(false)
const stage = ref<'idle' | 'uploading' | 'locating'>('idle')
const error = ref<ApiError | null>(null)
const elapsed = ref(0)
let timer: number | undefined

// step=1 的 goal 必须是"用户口语"，不能说成按钮名（评测口径同样的要求）
const GOAL_CHIPS = ['我想看看儿子', '我想挂个号', '我想连 WiFi', '我想付钱', '我想看天气预报']

const busy = computed(() => stage.value !== 'idle' || preparing.value)
const goalLength = computed(() => goal.value.trim().length)
const goalInvalid = computed(() => goalLength.value === 0 || goalLength.value > 100)
const canSubmit = computed(() => Boolean(image.value) && !goalInvalid.value && !busy.value)
const stageText = computed(() => {
  if (preparing.value) return '正在压缩图片…'
  if (stage.value === 'uploading') return '正在上传截图…'
  if (stage.value === 'locating') return `正在识别屏幕内容… 已等待 ${elapsed.value} 秒`
  return ''
})

function pick(source: 'camera' | 'gallery') {
  error.value = null
  const input = source === 'camera' ? cameraInput.value : galleryInput.value
  input?.click()
}

async function onFileChange(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = '' // 允许重复选同一张
  if (!file) return

  error.value = null
  preparing.value = true
  try {
    image.value = await compressImage(file)
  } catch (err) {
    image.value = null
    error.value = new ApiError(0, 'invalid', err instanceof Error ? err.message : '图片读取失败，请换一张')
  } finally {
    preparing.value = false
  }
}

function startTimer() {
  elapsed.value = 0
  window.clearInterval(timer)
  timer = window.setInterval(() => {
    elapsed.value += 1
  }, 1000)
}

function stopTimer() {
  window.clearInterval(timer)
  timer = undefined
}

onBeforeUnmount(stopTimer)

async function submit() {
  if (!image.value || !canSubmit.value) return
  const current = image.value
  const text = goal.value.trim()
  error.value = null
  startTimer()
  try {
    stage.value = 'uploading'
    const uploaded = await uploadScreenshot(current.blob)
    session.startSession({
      imageId: uploaded.image_id,
      imageDataUrl: current.dataUrl,
      width: uploaded.width,
      height: uploaded.height,
      goal: text,
    })

    stage.value = 'locating'
    const result = await locate({ image_id: uploaded.image_id, goal: text })
    session.applyResult(result)
    await router.push({ name: 'result' })
  } catch (err) {
    error.value =
      err instanceof ApiError ? err : new ApiError(0, 'network', '操作失败，请重试')
  } finally {
    stage.value = 'idle'
    stopTimer()
  }
}
</script>

<template>
  <div class="flex flex-1 flex-col gap-4 pb-4">
    <!-- 拍照 / 相册：capture="environment" 调后置摄像头 -->
    <input ref="cameraInput" type="file" accept="image/*" capture="environment" class="hidden" @change="onFileChange">
    <input ref="galleryInput" type="file" accept="image/*" class="hidden" @change="onFileChange">

    <section v-if="!image" class="rounded-lg border border-dashed border-input bg-card p-5 text-center">
      <p class="text-base font-medium">第一步：拍下老人的手机屏幕</p>
      <p class="mt-1 text-sm text-muted-foreground">对准屏幕、避免反光，也可以直接上传已有的截图</p>
      <div class="mt-4 grid grid-cols-2 gap-3">
        <UiButton size="lg" :disabled="busy" @click="pick('camera')">📷 拍照</UiButton>
        <UiButton size="lg" variant="outline" :disabled="busy" @click="pick('gallery')">🖼 相册</UiButton>
      </div>
    </section>

    <section v-else class="overflow-hidden rounded-lg border border-border bg-card">
      <img :src="image.dataUrl" alt="待识别的屏幕截图" class="max-h-80 w-full bg-black/5 object-contain">
      <div class="flex items-center justify-between gap-2 px-4 py-3 text-sm">
        <span class="text-muted-foreground">
          {{ image.width }}×{{ image.height }} · {{ formatBytes(image.bytes) }}
          <span v-if="image.originalBytes > image.bytes">（已压缩）</span>
        </span>
        <div class="flex gap-2">
          <UiButton size="sm" variant="ghost" :disabled="busy" @click="pick('camera')">重拍</UiButton>
          <UiButton size="sm" variant="ghost" :disabled="busy" @click="pick('gallery')">换一张</UiButton>
        </div>
      </div>
    </section>

    <section class="rounded-lg border border-border bg-card p-4">
      <label for="goal" class="text-base font-medium">第二步：老人想做什么？</label>
      <p class="mt-1 text-sm text-muted-foreground">用大白话说，不用照着按钮念</p>
      <div class="mt-3 flex flex-wrap gap-2">
        <button
          v-for="chip in GOAL_CHIPS"
          :key="chip"
          type="button"
          class="rounded-full border px-3 py-2 text-sm transition-colors"
          :class="goal === chip ? 'border-primary bg-primary/10 text-primary' : 'border-input bg-card text-foreground hover:bg-secondary/60'"
          :disabled="busy"
          @click="goal = chip"
        >
          {{ chip }}
        </button>
      </div>
      <textarea
        id="goal"
        v-model="goal"
        rows="2"
        maxlength="120"
        placeholder="例如：我想看看儿子"
        :disabled="busy"
        class="mt-3 w-full resize-none rounded-xl border border-input bg-background px-3 py-3 text-base outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-60"
      />
      <p class="mt-1 text-right text-xs" :class="goalLength > 100 ? 'text-destructive' : 'text-muted-foreground'">
        {{ goalLength }}/100
      </p>
    </section>

    <div
      v-if="error"
      role="alert"
      class="rounded-lg border border-destructive/40 bg-destructive/10 p-4 text-sm"
    >
      <p class="font-medium text-destructive">{{ error.message }}</p>
      <p v-if="error.kind === 'network'" class="mt-2 text-muted-foreground">
        后端没起来时，在仓库根目录执行：<br>
        <code class="mt-1 block rounded bg-black/5 p-2 text-xs">cd server &amp;&amp; uvicorn app.main:app --port 8000</code>
      </p>
      <p v-else-if="error.kind === 'refused'" class="mt-2 text-muted-foreground">
        这是正常的拒答：模型没把握。换一张更清晰、不反光的图再试。
      </p>
    </div>

    <div class="mt-auto">
      <p v-if="busy" class="mb-2 text-center text-sm text-muted-foreground" aria-live="polite">
        {{ stageText }}<span v-if="stage === 'locating'">（通常 3–5 秒）</span>
      </p>
      <UiButton size="lg" block :disabled="!canSubmit" :loading="busy" @click="submit">
        {{ image ? '生成指路卡片' : '先拍一张屏幕' }}
      </UiButton>
      <p v-if="goalInvalid && goalLength > 100" class="mt-2 text-center text-xs text-destructive">
        需求描述最多 100 字
      </p>
    </div>
  </div>
</template>
