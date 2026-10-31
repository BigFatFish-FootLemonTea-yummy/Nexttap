<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import TargetOverlay from '@/components/TargetOverlay.vue'
import { useSessionStore } from '@/stores/session'
import { useSpeech } from '@/lib/speech'
import { useWakeLock } from '@/lib/wakelock'

const router = useRouter()
const session = useSessionStore()
const speech = useSpeech()
const wakeLock = useWakeLock()

const hintVisible = ref(true)
let hintTimer: number | undefined

function replay() {
  speech.speak(session.instruction)
}

function exit() {
  speech.stop()
  void router.push({ name: 'result' })
}

/**
 * 连点两下才退出。
 * 现场是帮助者举着手机给老人看，单击即退会被误触打断演示；
 * 双击仍是单手可完成的动作，且 touch-action: manipulation 已禁用双击缩放。
 */
let lastTap = 0
function onSurfaceTap() {
  const now = Date.now()
  if (now - lastTap < 800) {
    exit()
    return
  }
  lastTap = now
  hintVisible.value = true
  window.clearTimeout(hintTimer)
  hintTimer = window.setTimeout(() => {
    hintVisible.value = false
  }, 2000)
}

onMounted(() => {
  // 进入全屏本身来自一次点击，属于用户手势，多数浏览器允许直接播报
  window.setTimeout(replay, 300)
  hintTimer = window.setTimeout(() => {
    hintVisible.value = false
  }, 4000)
})

onBeforeUnmount(() => {
  window.clearTimeout(hintTimer)
  speech.stop()
})
</script>

<template>
  <!-- 用 fixed 铺满视口而非 requestFullscreen：iOS 上后者对普通元素无效 -->
  <div
    class="fixed inset-0 z-50 flex flex-col bg-white text-slate-900"
    style="touch-action: manipulation"
    @click="onSurfaceTap"
  >
    <div class="flex items-center justify-between px-3 pt-safe">
      <span v-if="hintVisible" class="text-sm text-slate-500">连点两下退出</span>
      <span v-else class="text-sm text-slate-400">给老人看</span>
      <button
        type="button"
        class="touch-target rounded-lg px-3 text-base text-slate-600 active:bg-slate-100"
        aria-label="退出大字模式"
        @click.stop="exit"
      >
        退出 ✕
      </button>
    </div>

    <p class="hidden text-center text-sm text-slate-500 landscape:block">竖屏拿着，看得更清楚</p>

    <div class="min-h-0 flex-1 px-2 py-2">
      <TargetOverlay
        v-if="session.box"
        :image-url="session.imageDataUrl"
        :natural-width="session.width"
        :natural-height="session.height"
        :box="session.box"
        fit="fill"
        :breathing="true"
        :dim="true"
        :label="`请点 ${session.result?.text ?? '红圈处'}`"
      />
    </div>

    <div class="px-4 pb-safe">
      <p
        class="text-center font-bold leading-snug"
        style="font-size: clamp(40px, 7vw, 56px)"
        aria-live="polite"
      >
        {{ session.instruction }}
      </p>
      <button
        v-if="speech.supported"
        type="button"
        class="mt-4 flex h-16 w-full items-center justify-center gap-2 rounded-2xl bg-primary text-2xl font-semibold text-primary-foreground active:bg-primary/85"
        aria-label="再听一遍语音播报"
        @click.stop="replay"
      >
        🔊 再听一遍
      </button>
      <p class="mt-2 text-center text-xs text-slate-400">
        <span v-if="wakeLock.active">屏幕已保持常亮</span>
        <span v-else>请让老人点自己手机上的红圈位置</span>
      </p>
    </div>
  </div>
</template>
