import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import type { LocateOut, NormBox } from '@/types/api'

const STORAGE_KEY = 'nexttap.session.v1'

interface PersistedSession {
  imageId: string
  imageDataUrl: string
  width: number
  height: number
  goal: string
  result: LocateOut | null
  box: NormBox | null
  instruction: string
  adjusted: boolean
}

function load(): Partial<PersistedSession> {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY)
    return raw ? (JSON.parse(raw) as PersistedSession) : {}
  } catch {
    return {}
  }
}

/**
 * 一次"拍照 → 定位 → 微调 → 给老人看"的会话。
 * 存 sessionStorage 是为了刷新/全屏来回切换不丢卡片；
 * 只存当前一张图，不做历史卡片（定稿 F8 属 P1，本赛期不做）。
 */
export const useSessionStore = defineStore('session', () => {
  const saved = load()

  const imageId = ref(saved.imageId ?? '')
  const imageDataUrl = ref(saved.imageDataUrl ?? '')
  const width = ref(saved.width ?? 0)
  const height = ref(saved.height ?? 0)
  const goal = ref(saved.goal ?? '')
  const result = ref<LocateOut | null>(saved.result ?? null)
  const box = ref<NormBox | null>(saved.box ?? null)
  const instruction = ref(saved.instruction ?? '')
  const adjusted = ref(saved.adjusted ?? false)

  const hasImage = computed(() => Boolean(imageId.value && imageDataUrl.value))
  const hasResult = computed(() => Boolean(result.value && box.value))

  function persist() {
    try {
      const data: PersistedSession = {
        imageId: imageId.value,
        imageDataUrl: imageDataUrl.value,
        width: width.value,
        height: height.value,
        goal: goal.value,
        result: result.value,
        box: box.value,
        instruction: instruction.value,
        adjusted: adjusted.value,
      }
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify(data))
    } catch {
      // 隐私模式/超配额时只是失去"刷新不丢"，不影响主流程
    }
  }

  function startSession(input: { imageId: string; imageDataUrl: string; width: number; height: number; goal: string }) {
    imageId.value = input.imageId
    imageDataUrl.value = input.imageDataUrl
    width.value = input.width
    height.value = input.height
    goal.value = input.goal
    result.value = null
    box.value = null
    instruction.value = ''
    adjusted.value = false
    persist()
  }

  function applyResult(value: LocateOut) {
    result.value = value
    box.value = [...value.bbox_norm] as NormBox
    instruction.value = value.instruction
    adjusted.value = false
    persist()
  }

  function setGoal(value: string) {
    goal.value = value
    persist()
  }

  /** 人工微调后的框（归一化坐标），全屏模式与结果页共用 */
  function setBox(value: NormBox) {
    box.value = value
    adjusted.value = true
    persist()
  }

  function setInstruction(value: string) {
    instruction.value = value
    adjusted.value = true
    persist()
  }

  /** 一键退回 AI 原结果，人工微调的标记一并清掉 */
  function restoreResult() {
    if (!result.value) return
    box.value = [...result.value.bbox_norm] as NormBox
    instruction.value = result.value.instruction
    adjusted.value = false
    persist()
  }

  function reset() {
    imageId.value = ''
    imageDataUrl.value = ''
    width.value = 0
    height.value = 0
    goal.value = ''
    result.value = null
    box.value = null
    instruction.value = ''
    adjusted.value = false
    try {
      sessionStorage.removeItem(STORAGE_KEY)
    } catch {
      /* 忽略 */
    }
  }

  return {
    imageId,
    imageDataUrl,
    width,
    height,
    goal,
    result,
    box,
    instruction,
    adjusted,
    hasImage,
    hasResult,
    startSession,
    applyResult,
    setGoal,
    setBox,
    setInstruction,
    restoreResult,
    reset,
  }
})
