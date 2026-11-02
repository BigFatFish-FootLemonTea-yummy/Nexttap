import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import type { AgentResult, AgentStep, NormBox, PathStep } from '@/types/api'

const STORAGE_KEY = 'nexttap.session.v2'

export interface SessionImage {
  imageId: string
  dataUrl: string
  width: number
  height: number
}

interface Persisted {
  images: SessionImage[]
  goal: string
  agent: AgentResult | null
  stepIndex: number
  box: NormBox | null
  instruction: string
  adjusted: boolean
}

function load(): Partial<Persisted> {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY)
    return raw ? (JSON.parse(raw) as Persisted) : {}
  } catch {
    return {}
  }
}

/**
 * 一次"拍第一张 → 出计划 → 逐步补图 → 给老人看"的会话。
 *
 * 多步之后，会话里要同时保存：
 *   - images：按顺序拍的截图（第 i 张对应第 i 步）
 *   - agent ：后端返回的整条链（含每步的 bbox 与 trace）
 *   - stepIndex：当前在看第几步
 *
 * `box` / `instruction` 始终是"当前步"的可编辑副本，全屏模式直接读它们，
 * 这样人工微调只影响当前步，不会污染 Agent 的原始结果。
 */
export const useSessionStore = defineStore('session', () => {
  const saved = load()

  const images = ref<SessionImage[]>(saved.images ?? [])
  const goal = ref(saved.goal ?? '')
  const agent = ref<AgentResult | null>(saved.agent ?? null)
  const stepIndex = ref(saved.stepIndex ?? 1)
  const box = ref<NormBox | null>(saved.box ?? null)
  const instruction = ref(saved.instruction ?? '')
  const adjusted = ref(saved.adjusted ?? false)

  const steps = computed<AgentStep[]>(() => agent.value?.steps ?? [])
  const totalSteps = computed(() => Math.max(steps.value.length, agent.value?.next_step ? steps.value.length + 1 : 0))
  const currentStep = computed<AgentStep | null>(() => steps.value[stepIndex.value - 1] ?? null)
  const currentImage = computed<SessionImage | null>(
    () => images.value[stepIndex.value - 1] ?? images.value[images.value.length - 1] ?? null,
  )

  const imageId = computed(() => currentImage.value?.imageId ?? '')
  const imageDataUrl = computed(() => currentImage.value?.dataUrl ?? '')
  const width = computed(() => currentImage.value?.width ?? 0)
  const height = computed(() => currentImage.value?.height ?? 0)

  const hasImage = computed(() => images.value.length > 0)
  const hasResult = computed(() => Boolean(currentStep.value && box.value))
  const status = computed(() => agent.value?.status ?? '')
  const taskName = computed(() => agent.value?.task_name ?? '')
  const nextStep = computed<PathStep | null>(() => agent.value?.next_step ?? null)
  const canContinue = computed(() => agent.value?.status === 'in_progress' && Boolean(agent.value?.next_step))

  function persist() {
    try {
      const data: Persisted = {
        images: images.value,
        goal: goal.value,
        agent: agent.value,
        stepIndex: stepIndex.value,
        box: box.value,
        instruction: instruction.value,
        adjusted: adjusted.value,
      }
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify(data))
    } catch {
      // 超配额（多张 base64）时只是失去"刷新不丢"，不影响主流程
    }
  }

  /** 当前步的原始结果同步到可编辑副本 */
  function syncFromStep() {
    const step = steps.value[stepIndex.value - 1]
    if (!step) {
      box.value = null
      instruction.value = ''
      adjusted.value = false
      return
    }
    box.value = step.bbox_norm ? ([...step.bbox_norm] as NormBox) : null
    instruction.value = step.instruction
    adjusted.value = false
  }

  function startSession(input: { goal: string; image: SessionImage }) {
    images.value = [input.image]
    goal.value = input.goal
    agent.value = null
    stepIndex.value = 1
    syncFromStep()
    persist()
  }

  /** 继续拍下一步：追加一张图，不动已有结果 */
  function pushImage(image: SessionImage) {
    images.value = [...images.value, image]
    persist()
  }

  function setAgent(result: AgentResult) {
    agent.value = result
    // 默认停在最后一张图上（也就是刚拍的那一步）
    stepIndex.value = Math.max(1, result.steps.length)
    syncFromStep()
    persist()
  }

  function selectStep(index: number) {
    stepIndex.value = Math.min(Math.max(1, index), Math.max(1, steps.value.length))
    syncFromStep()
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
    syncFromStep()
    persist()
  }

  function reset() {
    images.value = []
    goal.value = ''
    agent.value = null
    stepIndex.value = 1
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
    images,
    goal,
    agent,
    stepIndex,
    box,
    instruction,
    adjusted,
    steps,
    totalSteps,
    currentStep,
    currentImage,
    imageId,
    imageDataUrl,
    width,
    height,
    hasImage,
    hasResult,
    status,
    taskName,
    nextStep,
    canContinue,
    startSession,
    pushImage,
    setAgent,
    selectStep,
    setGoal,
    setBox,
    setInstruction,
    restoreResult,
    reset,
  }
})
