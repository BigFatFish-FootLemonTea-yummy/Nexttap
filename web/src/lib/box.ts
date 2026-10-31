import type { NormBox } from '@/types/api'

/**
 * 叠圈坐标换算。
 *
 * 交接文档第五节点名的坑：图片用 object-fit: contain 时，
 * 不能把 bbox_norm/10 的百分比直接套在容器上——容器比图片实际渲染区域大，
 * 上下（或左右）有 letterbox 留白，圈会整体偏移。
 * 这里统一先求出"图片在容器里的实际渲染盒"，再在渲染盒内做百分比映射。
 */

export interface Rect {
  x: number
  y: number
  width: number
  height: number
}

/** contain 模式下，原图在容器内的实际渲染矩形（相对容器左上角，单位 px） */
export function containRect(containerW: number, containerH: number, naturalW: number, naturalH: number): Rect {
  if (containerW <= 0 || containerH <= 0 || naturalW <= 0 || naturalH <= 0) {
    return { x: 0, y: 0, width: 0, height: 0 }
  }
  const scale = Math.min(containerW / naturalW, containerH / naturalH)
  const width = naturalW * scale
  const height = naturalH * scale
  return { x: (containerW - width) / 2, y: (containerH - height) / 2, width, height }
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value))
}

export function clampNorm(value: number): number {
  return clamp(value, 0, 1000)
}

/** 归一化框 → 容器内像素框（left/top/width/height 可直接用于绝对定位） */
export function normToRect(box: NormBox, content: Rect): Rect {
  return {
    x: content.x + (box[0] / 1000) * content.width,
    y: content.y + (box[1] / 1000) * content.height,
    width: ((box[2] - box[0]) / 1000) * content.width,
    height: ((box[3] - box[1]) / 1000) * content.height,
  }
}

/** 容器内像素框 → 归一化框（拖拽微调后回写，供全屏模式复用） */
export function rectToNorm(rect: Rect, content: Rect): NormBox {
  if (content.width <= 0 || content.height <= 0) return [0, 0, 0, 0]
  const x0 = ((rect.x - content.x) / content.width) * 1000
  const y0 = ((rect.y - content.y) / content.height) * 1000
  const x1 = ((rect.x + rect.width - content.x) / content.width) * 1000
  const y1 = ((rect.y + rect.height - content.y) / content.height) * 1000
  return [clampNorm(x0), clampNorm(y0), clampNorm(x1), clampNorm(y1)]
}

/** 把框限制在图片渲染盒内，并保证最小可点尺寸 */
export function constrainRect(rect: Rect, content: Rect, minSize = 24): Rect {
  const width = clamp(rect.width, minSize, content.width)
  const height = clamp(rect.height, minSize, content.height)
  const x = clamp(rect.x, content.x, content.x + content.width - width)
  const y = clamp(rect.y, content.y, content.y + content.height - height)
  return { x, y, width, height }
}
