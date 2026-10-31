<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import type { NormBox } from '@/types/api'
import { containRect, constrainRect, normToRect, rectToNorm, type Rect } from '@/lib/box'

/**
 * 截图 + 高亮圈。
 * fit="width"：容器按图片宽高比自适应（结果页，无留白）
 * fit="fill" ：容器铺满父元素，图片 object-contain（全屏模式，有 letterbox，必须换算）
 */
const props = withDefaults(
  defineProps<{
    imageUrl: string
    naturalWidth: number
    naturalHeight: number
    box: NormBox | null
    fit?: 'width' | 'fill'
    editable?: boolean
    breathing?: boolean
    dim?: boolean
    label?: string
  }>(),
  { fit: 'width', editable: false, breathing: false, dim: false, label: '目标位置' },
)

const emit = defineEmits<{ 'update:box': [NormBox] }>()

const containerRef = ref<HTMLDivElement | null>(null)
const size = reactive({ width: 0, height: 0 })
let observer: ResizeObserver | null = null

onMounted(() => {
  const el = containerRef.value
  if (!el) return
  size.width = el.clientWidth
  size.height = el.clientHeight
  observer = new ResizeObserver((entries) => {
    const entry = entries[0]
    if (!entry) return
    size.width = entry.contentRect.width
    size.height = entry.contentRect.height
  })
  observer.observe(el)
})

onBeforeUnmount(() => {
  observer?.disconnect()
  observer = null
})

const content = computed(() => containRect(size.width, size.height, props.naturalWidth, props.naturalHeight))
const ready = computed(() => content.value.width > 0 && content.value.height > 0 && props.box !== null)

/** 拖拽过程中用本地像素框，避免父组件回写造成抖动 */
const dragRect = ref<Rect | null>(null)
const displayRect = computed<Rect>(() => {
  if (dragRect.value) return dragRect.value
  return props.box ? normToRect(props.box, content.value) : { x: 0, y: 0, width: 0, height: 0 }
})

const ringStyle = computed(() => ({
  left: `${displayRect.value.x}px`,
  top: `${displayRect.value.y}px`,
  width: `${displayRect.value.width}px`,
  height: `${displayRect.value.height}px`,
  // 压暗圈外区域：不用额外遮罩层，圈的位置就能始终保持可点
  boxShadow: props.dim ? '0 0 0 9999px rgb(0 0 0 / 0.45)' : undefined,
}))

const containerStyle = computed(() =>
  props.fit === 'width'
    ? { aspectRatio: `${props.naturalWidth} / ${props.naturalHeight}` }
    : {},
)

type DragMode = 'move' | 'nw' | 'ne' | 'sw' | 'se'
interface DragState {
  pointerId: number
  mode: DragMode
  startX: number
  startY: number
  origin: Rect
}
let drag: DragState | null = null

function beginDrag(event: PointerEvent, mode: DragMode) {
  if (!props.editable || !ready.value) return
  event.preventDefault()
  event.stopPropagation()
  const el = containerRef.value
  if (!el) return
  drag = {
    pointerId: event.pointerId,
    mode,
    startX: event.clientX,
    startY: event.clientY,
    origin: { ...displayRect.value },
  }
  dragRect.value = { ...displayRect.value }
  el.setPointerCapture(event.pointerId)
}

function onPointerMove(event: PointerEvent) {
  if (!drag || event.pointerId !== drag.pointerId) return
  const dx = event.clientX - drag.startX
  const dy = event.clientY - drag.startY
  const o = drag.origin
  let next: Rect
  switch (drag.mode) {
    case 'move':
      next = { x: o.x + dx, y: o.y + dy, width: o.width, height: o.height }
      break
    case 'nw':
      next = { x: o.x + dx, y: o.y + dy, width: o.width - dx, height: o.height - dy }
      break
    case 'ne':
      next = { x: o.x, y: o.y + dy, width: o.width + dx, height: o.height - dy }
      break
    case 'sw':
      next = { x: o.x + dx, y: o.y, width: o.width - dx, height: o.height + dy }
      break
    default:
      next = { x: o.x, y: o.y, width: o.width + dx, height: o.height + dy }
  }
  dragRect.value = constrainRect(next, content.value)
  emit('update:box', rectToNorm(dragRect.value, content.value))
}

function endDrag(event: PointerEvent) {
  if (!drag || event.pointerId !== drag.pointerId) return
  const el = containerRef.value
  if (el?.hasPointerCapture(event.pointerId)) el.releasePointerCapture(event.pointerId)
  if (dragRect.value) emit('update:box', rectToNorm(dragRect.value, content.value))
  drag = null
  dragRect.value = null
}

// 换一张图/换一个目标时，丢弃未完成的拖拽
watch(
  () => props.imageUrl,
  () => {
    drag = null
    dragRect.value = null
  },
)

const HANDLES: { mode: DragMode; class: string }[] = [
  { mode: 'nw', class: 'left-0 top-0 -translate-x-1/2 -translate-y-1/2 cursor-nwse-resize' },
  { mode: 'ne', class: 'right-0 top-0 translate-x-1/2 -translate-y-1/2 cursor-nesw-resize' },
  { mode: 'sw', class: 'left-0 bottom-0 -translate-x-1/2 translate-y-1/2 cursor-nesw-resize' },
  { mode: 'se', class: 'right-0 bottom-0 translate-x-1/2 translate-y-1/2 cursor-nwse-resize' },
]
</script>

<template>
  <div
    ref="containerRef"
    class="relative w-full select-none overflow-hidden"
    :class="fit === 'fill' ? 'h-full' : 'rounded-xl bg-black/5'"
    :style="containerStyle"
    role="img"
    :aria-label="`屏幕截图，${label}`"
    @pointermove="onPointerMove"
    @pointerup="endDrag"
    @pointercancel="endDrag"
  >
    <img
      :src="imageUrl"
      :width="naturalWidth"
      :height="naturalHeight"
      class="absolute inset-0 h-full w-full object-contain"
      alt="手机屏幕截图"
      draggable="false"
    >

    <template v-if="ready">
      <!-- 呼吸外圈：只在大字模式开，吸引老人视线 -->
      <div
        v-if="breathing"
        class="pointer-events-none absolute rounded-xl border-4 border-target animate-pulse-ring"
        :style="ringStyle"
        aria-hidden="true"
      />
      <div
        data-target-ring
        class="absolute rounded-xl border-4 border-target bg-target/10"
        :class="[breathing ? 'animate-breathe' : '', editable ? 'cursor-move touch-none' : 'pointer-events-none']"
        :style="ringStyle"
        @pointerdown="beginDrag($event, 'move')"
      >
        <template v-if="editable">
          <span
            v-for="handle in HANDLES"
            :key="handle.mode"
            class="absolute h-7 w-7 rounded-full border-2 border-target bg-card shadow touch-none"
            :class="handle.class"
            aria-hidden="true"
            @pointerdown="beginDrag($event, handle.mode)"
          />
        </template>
      </div>
    </template>
  </div>
</template>
